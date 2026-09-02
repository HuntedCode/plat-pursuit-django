# Game Leaderboards — Phase 2 Plan (group boards, speed boards, playtime)

> Status: **PLAN — for review, not yet built.** Extends the shipped Phase 1 overall board
> (`docs/features/game-leaderboards.md`). When built, fold the relevant parts back into that feature doc.

## Goal

Turn the single overall board into a small family of boards per game, all sharing one denorm and all
reusing the shipped virtualization / windowing / rank / typeahead machinery. Performance is a first-class
requirement: **every board must be single-digit-ms at any game's scale**, the same bar Phase 1 hit, driven
by purpose-built indexes rather than live aggregation.

## Board taxonomy (built dynamically per game)

| Board | Population | Ranking | Exists when |
|-------|-----------|---------|-------------|
| **Progress — default group** | everyone who owns the game | `progress DESC, last_trophy_at ASC, profile` | always |
| **Progress — a DLC group** | owners with that group | same, scoped to the group | game has DLC groups |
| **Progress — Everything (100%)** | everyone | today's overall board (`ProfileGame.progress`) | game has >1 group |
| **Speed — default group** ("Fastest Platinum" when a plat exists) | players who 100%'d the group | `completion_seconds ASC, last_trophy_at ASC, profile` | default group has **≥2** trophies |
| **Speed — a DLC group** | players who 100%'d that DLC | same | that DLC group has **≥2** trophies |
| **Playtime** | owners with PSN-reported time | `play_duration DESC, profile` | any owner has `play_duration` |

**Rules that fall out of this:**
- **No Everything speed board.** Elapsed across groups measures "when you got around to DLC that shipped
  years later," not speed. Speed is only meaningful within a fixed trophy set.
- **Single-trophy group → progress board only.** With one trophy `first == last`, so the speed board would
  duplicate the progress board's first-earners race. Gate the speed board on the group's defined-trophy
  count ≥ 2.
- **Playtime is whole-game**, not per-group (PSN reports cumulative time per title). Hidden entirely when a
  game has no reported time for anyone.

## Data model

### New: `ProfileTrophyGroup` (the per-group denorm)

Per-group standings are stored nowhere today, so a group board would be a live aggregate over
`EarnedTrophy` on every render — the thing Phase 1 was built to avoid. We denorm one row per
`(profile, trophy_group)`.

| Field | Type | Purpose |
|-------|------|---------|
| `profile` | FK Profile, CASCADE | |
| `trophy_group` | FK TrophyGroup, CASCADE | **game-level grain** (group implies its game); keeps it out of `Concept.absorb()` |
| `progress` | `PositiveSmallInteger` (0–100) | per-group completion %; the progress board's sort key AND the speed board's 100% qualifier |
| `earned_trophies` | JSON `{platinum,gold,silver,bronze}` | per-group tier haul, for the row dots (parity with `ProfileGame.earned_trophies`) |
| `first_trophy_at` | datetime, null | MIN earned time in the group |
| `last_trophy_at` | datetime, null | MAX earned time in the group; **doubles as** the progress-board recency tiebreak and the speed completion timestamp |
| `completion_seconds` | `PositiveInteger`, null | `last − first` in seconds, **only** when `progress == 100` AND the group has ≥2 defined trophies; else null (excluded from the speed board) |

`unique_together = (profile, trophy_group)`.

**Grain decision (locked): FK to game-level `TrophyGroup`, not `ConceptTrophyGroup`.** It matches
`ProfileGame`'s per-game grain, the board is per-game anyway, and it means TrophyGroups (and their
ProfileTrophyGroup rows) travel with their Game during concept reassignment — so **no `Concept.absorb()`
branch is needed** (verify at build: confirm no direct Concept relation is added).

Sizing (from the earlier probe): **~1.46M rows, 1.73× ProfileGame** — small; eager row creation is fine.

### `ProfileGame` additions (for the playtime board)

No new field strictly required — `play_duration` (DurationField, nullable) already exists. We only add an
index (below). Decision point: denorm `play_seconds:int` for uniform ordering vs. index `play_duration`
directly. **Lean: index the existing `play_duration`** (one less field to maintain; Postgres orders
`interval` fine).

## Performance — the index plan (the core of this work)

Every board read is a **windowed slice on an index that serves the ORDER BY directly**, exactly like
`pg_game_leaderboard_idx`. No board sorts in Python; no board aggregates on the request path. All three new
indexes are built `CONCURRENTLY` (the `AddIndexConcurrently` + `Migration.atomic = False` pattern from
migration 0260).

| Index | On | Fields | Notes |
|-------|-----|--------|-------|
| `ptg_progress_idx` | ProfileTrophyGroup | `(trophy_group, -progress, last_trophy_at, profile)` | mirrors the Phase 1 index field-for-field; serves every group **progress** board + `rank_for` + `board_size` |
| `ptg_speed_idx` | ProfileTrophyGroup | `(trophy_group, completion_seconds, last_trophy_at, profile)` **PARTIAL** `WHERE completion_seconds IS NOT NULL` | serves every group **speed** board; partial keeps it to completers only (far smaller than the table) |
| `pg_playtime_idx` | ProfileGame | `(game, -play_duration, profile)` **PARTIAL** `WHERE play_duration IS NOT NULL` | serves the **playtime** board; partial excludes the ~24% with no reported time |

Why this stays fast at any scale (same argument as Phase 1, now per board):
- **Ordering is index-served** — the planner walks the index, it never sorts or filters a large row set.
- **`profile` is the load-bearing final key** on the progress/speed indexes — makes each order *total*, so
  adjacent virtual windows never skip/duplicate and a rank never flickers (the exact reason Phase 1 needs
  it; group boards have the same tie clusters — everyone at 100%, identical `completion_seconds`).
- **Partial indexes** on speed and playtime mean those indexes only carry the rows that can appear on the
  board, so they're smaller and hotter than the base table.
- **Windowed reads** (`OFFSET` at board scale) stay single-digit ms; `rank_for` is a bounded count on the
  same index; `board_size` is an indexed count. (The millions-of-players ceilings and their additive fixes
  are already documented in the feature doc's "Scaling to huge boards" section and are unchanged here.)

## Population (maintained on sync, backfilled once)

### Incremental — extend `PSNApiService.update_profilegame_stats`

That method already runs **one** grouped aggregate over `EarnedTrophy` per sync batch and bulk-updates
ProfileGame. We add a **parallel grouped aggregate that also groups by `trophy__trophy_group_id`**:

```
EarnedTrophy.objects
  .filter(profile_id__in=…, trophy__game_id__in=…)
  .values('profile_id', 'trophy__game_id', 'trophy__trophy_group_id')
  .annotate(
      earned=Count('id', filter=Q(earned=True)),
      plat=Count('id',   filter=Q(earned=True, trophy__trophy_type='platinum')),
      gold=…, silver=…, bronze=…,
      first_at=Min('earned_date_time', filter=Q(earned=True)),
      last_at =Max('earned_date_time', filter=Q(earned=True)),
  )
```

Then per `(profile, group)`: look up the group's `defined_trophies` total → `progress`; set
`first/last_trophy_at`; compute `completion_seconds` only when `progress == 100` and the group has ≥2
defined trophies; `bulk_create(update_conflicts=…)` (upsert) the ProfileTrophyGroup rows. This is pure DB
aggregation (whale-safe — no per-row Python iteration), one extra query per batch, same shape as the code
already there.

### One-time backfill — new `backfill_profile_trophy_groups` command

Batched over profiles (mirrors `populate_profilegame_stats`), running the same grouped aggregate per
profile and upserting. Whale-safe, resumable, `--username` for spot-checks. Run at deploy before the boards
go live. No nightly job needed afterward — sync maintains it incrementally, like the other ProfileGame
stats.

## Service layer (`game_leaderboard_service.py`)

Generalize "the board" from an implicit ProfileGame-overall query into a small **board descriptor**:
- `ProgressBoard(trophy_group)` and `ProgressBoard.everything(game)` → over ProfileTrophyGroup / ProfileGame
- `SpeedBoard(trophy_group)` → ProfileTrophyGroup, `WHERE completion_seconds IS NOT NULL`
- `PlaytimeBoard(game)` → ProfileGame, `WHERE play_duration IS NOT NULL`

Each descriptor supplies its `_base_qs`, `ORDER_BY`/`INVERTED_ORDER`, and the display fields. Everything
downstream — `board_queryset`, `board_size`, `page_range`, `rank_for`, `row_at_rank`, `suggest`,
`BoardOptions` — is written **once against the descriptor** and reused unchanged. This is the big win: one
engine, N boards.

## Endpoint / view

`GameLeaderboardView` gains a `?board=` selector (e.g. `progress:default`, `progress:001`,
`progress:all`, `speed:default`, `speed:001`, `playtime`) parsed into the descriptor, carried on every
range/suggest/at fetch alongside the existing `BoardOptions`. Invalid/unavailable board → fall back to
`progress:default`. All existing response shapes (panel / `?range=` / `?suggest=` / `?at=`) are unchanged
apart from routing through the descriptor.

## UI (template + `game-detail.js`)

- **Dynamically built board chips**, not a rigid group×mode matrix (Speed skips Everything; Playtime
  ignores groups). Single-group game (the ~95% case) collapses to a simple `[Standings] [Fastest] [Most
  Played]` — chips absent when their board doesn't exist (single-trophy group hides Fastest; no reported
  time hides Most Played). DLC games additionally surface the group dimension.
- Selecting a chip re-fetches the panel (like the filter toggles today), swapping the descriptor. The
  virtualizer, minibar, jump/typeahead, and the persistent-highlight all work per board with no change.
- **Row rendering per board type:** progress rows unchanged; speed rows show elapsed formatted `5d 6h` +
  the completion date; playtime rows show the duration. Tooltips carry the honest caveats (playtime is
  PSN-reported wall time incl. idle, ~76% coverage; speed is first→last wall-clock, not active play).

## Migration & deploy (main vs rebuild split)

Prod sync must maintain the denorm from day one, so the data layer ships to **main**:
- **→ main:** `ProfileTrophyGroup` model + migration, the three indexes (CONCURRENTLY), the
  `update_profilegame_stats` extension, the `backfill_profile_trophy_groups` command. Deploy, run backfill.
- **→ rebuild:** service descriptors, view `?board=`, template chips, JS, row variants — the display layer,
  which lands when the game-detail rebuild ships.

This mirrors the Phase 1 index split and the community-stats split.

## Testing

- **Service:** per-group progress/rank/window tiling (reuse the Phase 1 tie-cluster tiling test per board);
  speed ordering + the 100%/≥2-trophy gate (completion_seconds null when not qualified); playtime ordering +
  null exclusion; single-trophy group offers no speed board; Everything offers no speed board; each board's
  `rank_for`/`board_size`/`suggest`/`row_at_rank` respects its population.
- **Population:** the extended aggregate produces correct `(progress, first, last, completion_seconds)` for
  a hand-built game with a base group + a DLC group; upsert idempotent across re-sync; data-hygiene guard
  (negative/null elapsed → completion_seconds null, never a garbage top row).
- **View:** `?board=` routes to the right descriptor; unavailable board falls back; CF-Ray header (the
  Cloudflare guard) as in the existing suite.
- **Backfill command:** populates a small fixture correctly; `--username` path.

## Open decisions (my leans as defaults)

1. **Playtime ordering field** — index existing `play_duration` (lean) vs. denorm `play_seconds`.
2. **Data-hygiene floor for speed** — exclude only `completion_seconds ≤ 0` / null (lean), no artificial
   minimum (trivial-but-legit fast completions stay).
3. **Default board on load** — `progress:default` (lean; the platinum race is what most want).
4. **Chip labels** — default speed board reads "Fastest Platinum" when the default group defines a plat,
   else "Fastest Completion" (lean; small dynamic label).
5. Anti-cheat — **parked** per decision; only the data-hygiene guard above ships now.

## Explicitly out of scope

Cross-game speed leaderboards, active-playtime (vs wall-clock) speed, outlier/anti-cheat detection, any
per-group data on pages other than the Ranks tab.
