# Game Leaderboards

The **Ranks** tab on game detail (`/games/<np_communication_id>/`): every hunter who owns a game, ranked
by completion, with the viewer's own standing surfaced.

**Status**: shipped. A family of boards per game -- overall + per-trophy-group standings, a "Fastest"
(speed) board per group, and a whole-game playtime board -- served by one engine over one denorm. See
[Board family](#board-family).

---

## The ranking

```
progress DESC, most_recent_trophy_date ASC (NULLS LAST), profile_id ASC
```

Completers sit at the top **ordered by when they finished**, then everyone else by how close they are. A
game's board reads as a race rather than a snapshot, and the shape falls out of the ordering rather than
being special-cased: for a fully-completed game the player's most recent trophy *is* the platinum.

| Key | Why |
|-----|-----|
| `progress DESC` | Furthest along leads. |
| `most_recent_trophy_date ASC` | Earliest finisher wins a tie. This is the whole point: at 100% the date is the entire ranking. |
| `profile_id ASC` | **Load-bearing.** Makes the order *total*. |

### Why `profile_id` is not decoration

Ties on the first two keys are the **normal** case, not an edge case - everyone at 100% shares
`progress=100`, and identical timestamps happen. Without a unique final key, Postgres may return tied
rows in a different order between calls, which means:

- adjacent virtual windows **skip or duplicate** players at their shared boundary, and
- a displayed rank **flickers** between refreshes.

Both are silent. `tests/engine/test_game_leaderboard_service.py` tiles boards made entirely of tied rows
with adjacent ranges and asserts they reconstruct the board exactly, to catch this.

### Null dates

Owners with zero trophies have `most_recent_trophy_date = NULL` and sort last within their progress band.
Postgres' default for `ASC` is `NULLS LAST`, so this needs no special handling - but it does mean the
index and the query must both rely on that default (they do).

---

## Board family

Each game exposes several boards, all served by **one engine**. A `Board` (in `game_leaderboard_service.py`)
is a filtered population + a total ordering; the windowing / rank / suggest / row-preview logic is written
once against that interface, and each board subclass supplies only its queryset and its `SortKey` list. The
generic "who ranks strictly ahead of this row" Q is built from the keys, so adding a board is a few lines,
not a re-implementation of the rank math.

| Board | Model | Ranked by | Notes |
|-------|-------|-----------|-------|
| **Everything** (`progress:all`) | `ProfileGame` | overall completion across all trophies | the original board; for a single-group game it IS the base board |
| **Group standings** (`progress:<gid>`) | `ProfileTrophyGroup` | completion within one trophy group | base game or a DLC; fixed denominator, so a day-one platter never slides |
| **Group speed** (`speed:<gid>`) | `ProfileTrophyGroup` | fastest first→last completion of the group | the "Fastest Platinum" race for the default group; only fully-completed, ≥2-trophy groups qualify |
| **Playtime** (`playtime`) | `ProfileGame` | most PSN-reported play time (whole game) | only rows with a reported duration; excludes the ~24% without |

**Why per-group standings exist.** The Everything board ranks by `ProfileGame.progress`, one % across ALL
trophies, whose denominator MOVES when DLC lands: everyone's percentage falls and a player who platted on
day one slides down because of content that did not exist when they finished. A group board's denominator
is fixed, so the base-game race stays a race.

**The denorm.** Per-group standings live in `ProfileTrophyGroup` (one row per profile per group with ≥1
earned trophy): `progress` (floored, so 100 iff every group trophy is earned), per-tier `earned_trophies`,
`first/last_trophy_at`, and `completion_seconds` (set only when the group is fully earned AND has ≥2
trophies -- the speed metric). Maintained on every sync by `PsnApiService.update_trophy_group_stats` (one
profile-scoped grouped aggregate; the denominator is `COUNT(Trophy)`, not `defined_trophies`, so
`earned <= total` is guaranteed) and seeded by `backfill_profile_trophy_groups`. ~1.46M rows on beta
(1.73× ProfileGame). It has no direct `Concept` relation (FK grain is game-level `TrophyGroup`), so it needs
**no `Concept.absorb()` branch**.

**Hidden games** are filtered at read time by the group boards via a subquery on `ProfileGame` (rare, tiny
anti-join) rather than denormed onto the standings row -- no staleness to propagate.

**The switcher.** `board_menu(game)` builds the selector (a couple of small aggregates; the panel is
lazy-loaded): a segmented mode row (Standings / Fastest / Most Played), each present only when it has a
board (Fastest needs a ≥2-trophy group; Most Played needs play-time data), plus a group row on DLC games.
Single-group games (~95%) show just the mode row, or nothing when there's a single board. Each chip carries
its full `?board=` param; clicking re-fetches the whole panel exactly like a filter toggle, and the active
board rides on the `.gd-lb` root so every continuation fetch hits the same board.

---

## Performance

Every board is backed by an index that serves its `ORDER BY` directly, so windowed reads never sort in
memory. Each ends in `profile` (the unique total-order key):

| Index | Model | Fields | Migration |
|-------|-------|--------|-----------|
| `pg_game_leaderboard_idx` | ProfileGame | `game, -progress, most_recent_trophy_date, profile` | `0260` (CONCURRENTLY) |
| `ptg_progress_idx` | ProfileTrophyGroup | `trophy_group, -progress, last_trophy_at, profile` | `0261` (empty table) |
| `ptg_speed_idx` | ProfileTrophyGroup | `trophy_group, completion_seconds, last_trophy_at, profile` **partial** `WHERE completion_seconds IS NOT NULL` | `0261` |
| `pg_playtime_idx` | ProfileGame | `game, -play_duration, profile` **partial** `WHERE play_duration IS NOT NULL` | `0262` (CONCURRENTLY) |

The `ptg_*` indexes ride with the empty-table `CreateModel` (instant, no CONCURRENTLY needed); the two
ProfileGame indexes are on a populated table so they build `CONCURRENTLY`. The partial indexes carry only
the rows that can appear on their board (completers / players with reported time), keeping them small.

Measured on beta (844K `ProfileGame` rows, biggest board 1,421 players):

| Operation | Before the index | After |
|-----------|------------------|-------|
| Top-20 page | 289 ms, 31,988 buffers | **0.6 ms, 23 buffers** |
| Rank lookup ~1,400 deep | n/a | **1.4 ms** |

Without the index the planner walked `profilegame_progress_idx` **backward** to satisfy the `ORDER BY`
and discarded 458,561 rows to return 20. If a plan ever shows `Incremental Sort` or a large
`Rows Removed by Filter` here, the index is not being used.

**No Redis.** The badge leaderboards live in Redis sorted sets because their score is an expensive
aggregation over `EarnedTrophy`. This board's score is two stored columns on one indexed table, so a
cache layer would add a second source of truth to accelerate something already faster than the network
hop. Re-check with `python manage.py measure_leaderboard --explain` before revisiting. (The one exception
is a single mega-board of millions; see [Scaling to huge boards](#scaling-to-huge-boards-when-the-time-comes).)

### Virtualized, so scroll position == rank

The client renders the board **virtualized**: `.gd-lb__list` is a full-height spacer (`board_size ×
--lb-row-h`), so the **page scrollbar spans the whole board** (1..N), but only the visible ~30 rows are in
the DOM, absolutely positioned by rank. This is the architecture that makes scrolling and jumping feel
native, because:

- **Scrolling never inserts rows above the viewport** -- it swaps which of the fixed-position rows are
  rendered. There's nothing to scroll-compensate, so it can't lurch. (The earlier keyset + prepend approach
  fought the page scroll and never stopped feeling janky.)
- **A jump is just a scroll position:** `scrollTop = rank × row_height`. It lands where the rank actually
  is, deep in the board, and the scrollbar thumb moves there too. It can't feel backwards.

The one requirement virtualization carries is a **fixed row height** (`--lb-row-h`, 44px desktop / 58px the
two-line mobile row); the JS keys the spacer height, row offsets, and jump targets off it, and re-lays-out
on the breakpoint resize. Rows are fetched by rank RANGE (`page_range`) as the window moves, page-aligned
and cached, so a range is fetched at most once. `game-detail.js` `lbVirtualize` is the engine.

Plain `OFFSET` is fine for the range reads -- a single game's board is small (biggest on beta ~1,400), so
even the deepest window is single-digit ms on the composite index.

---

## Scaling to huge boards (when the time comes)

Today the biggest board is ~1,400 and everything here is comfortable. This section records where the current
design has ceilings and what to change when a board actually approaches one, so the work is not rediscovered
under pressure. For context, our reference competitor tracks ~2M players on a single game (GTA V).

**The load-bearing rule: never store a dense per-row `rank`.** Rank is volatile -- one new syncer landing
mid-board shifts every rank below it, so a materialized integer rank would fire an **O(n) write cascade on
sync**, the hottest path we have. We deliberately compute rank on demand (`rank_for`), only ever for the
viewer plus a few jump targets, never for every row. That is why there is no cascade today. The scale plan
**keeps rank a read** and makes that read cheap on the few boards that need it; it does not materialize rank.

Everything below is **additive and local to the board that needs it** (frontend or query only). None of it
changes how rank is stored, so there is no migration risk in deferring: when a board crosses a threshold,
attach the matching fix to that board. The long tail of small boards stays exactly as it is.

| Ceiling | What breaks | Roughly when | Fix |
|---------|-------------|--------------|-----|
| **The full-height spacer** | Browsers cap element height (~17.9M px Firefox, ~33.5M px Chrome). At 44px/row that is a wall near **~400K rows** where the board's bottom becomes unreachable | board > ~400K | Frontend only: a **scaled / segmented scrollbar** (non-linear pixel mapping), or reframe to **Top-N + Around-You** windows |
| **`board_size` COUNT** | A filtered COUNT per panel load / control change; ~100-500ms at millions | board > ~500K | Cache the count (short TTL); it need not be exact |
| **`rank_for`, viewer's deep rank** | O(rank) count; ~1.4ms at rank 1,400, hundreds of ms at rank 1M+ | deep viewer on board > ~500K | A **progress-bucket histogram**: rank ~= prefix-sum of higher buckets + a bounded in-bucket count. O(1) to maintain (bump one counter on a progress change), no cascade |
| **Deep `OFFSET` reads** | `OFFSET` scans and discards the skipped rows; single-digit ms shallow, ~100-300ms at offset ~1M | jump/scroll deeper than ~100K on a huge board | **Keyset/seek** the range (seek the sort tuple) instead of OFFSET; O(log n) at any depth |

**The mega-board accelerator (Redis).** For a GTA-V-class board (millions), the cleanest single fix is a
**per-hot-board Redis sorted set**: `ZADD` on sync (O(log n), no cascade), `ZREVRANK` for rank, `ZRANGE` for
a window. This is how game leaderboards do millions, and we already run live Redis sorted sets for the badge
leaderboards. The catch is memory (a 2M-member ZSET is ~150MB), so it attaches to the **handful of huge
boards only**, not every game -- the long tail stays on the DB-compute path. The progress-bucket histogram
is the pure-DB alternative when we would rather not hold a board resident in Redis.

**Rejected: denormalizing rank into a column** (the cascade above). The Phase 2 `ProfileTrophyGroup` denorm
stores per-group **standings** (progress + dates), from which rank is still *computed* -- it does not store
rank itself, and must not.

---

## Endpoint

`GET /games/<np_communication_id>/leaderboard/` - **HTML**, not JSON, and public.

Shapes from one URL (all honour `?board=` and the view options below):

| Query | Returns | Used by |
|-------|---------|---------|
| *(none)* | Full panel: board switcher, controls, header, the viewer's standing, `data-lb-total` (spacer size), and the first window | First activation of the tab / a control or board change |
| `?range=<display-pos>&from=<canonical-rank>&count=<n>` | Rows only, positioned by the client | A virtual window as the list scrolls |
| `?suggest=<q>` | **JSON** `{players: [{display, username, avatar, rank, progress, url}]}` | Search typeahead (by name) |
| `?at=<rank>` | **JSON**, same shape, the single hunter at that canonical rank (or `[]` past the board) | Number typeahead (rank preview) |

`?board=` selects which board: `progress:all` (Everything, the default), `progress:<gid>`, `speed:<gid>`, or
`playtime`. Unrecognized / missing → the Everything board (`resolve_board` degrades gracefully). The client
carries the active board on every fetch (it rides on the `.gd-lb` root), so range/suggest/at always hit the
board the viewer is looking at.

`range` is a 1-indexed display position; `from` is the canonical rank of the window's first row, which the
client derives from the position + the `total` it already holds -- so a range fetch costs no COUNT. Ranks
stay canonical (from the top); an inverted board just counts down.

The toolbar's search field is one input for both jump kinds. A bare **number previews the hunter at that
rank** (`?at=` -> `row_at_rank`, one bounded read) so you see who you'd land on before committing; text runs
the `?suggest=` typeahead over the hunters on this board (scoped to the active filters, so a hidden/filtered
player never appears). Selecting either result, or pressing Enter on a number, **jumps** (a client-side
scroll to that rank's offset). The rank preview is canonical and skips the fetch when the number is past the
board, since the client already holds `total`. It reuses the shared `[data-search-wrap]` chrome
(`PlatPursuit.wireSearchField`) and `debounce`, mirroring the navbar/browse search.

**The minibar** (the sticky bar that surfaces on scroll) carries the SAME search field while the Ranks tab
is active (`data-mb-only="leaderboard"`), a Filters button that reaches the toolbar toggles, and a
**"You #N"** rank widget. One `lbWireSearch(input, drop, form, panel)` drives both the toolbar and minibar
fields; the minibar field is wired once to the persistent panel element and jumps the board below.

**Your standing while scrolled** lives in the minibar's rank widget: the header shows "You're #N" at the top
of the board, the minibar shows it once scrolled. The chevron points toward your rank -- down if it's below
where you're scrolled, up if above, a lit "here" when your row is on screen -- and springs the flip as you
cross your own row; a click jumps to it. The rank rides on the `.gd-lb` root (`data-lb-viewer-rank`);
`lbSyncMbRank` fills the widget, and the virtualizer sets the chevron direction each render by comparing the
viewer's rank offset to the visible range -- no observer, just scroll math.

### View options (BoardOptions)

Parsed from the query string, carried by the JS on every fetch so the view stays consistent:

| Param | Default | Effect | Cost |
|-------|---------|--------|------|
| `earners` | `1` (on) | `earners=0` includes 0%/zero-trophy owners | Free/faster - those rows sit at the index's bottom, so keeping them out just ends the scan sooner |
| `registered` | off | `registered=1` shows only profiles with a site account (`Profile.user` set) | A post-join filter, not index-served, but negligible at board scale |
| `invert` | off | `invert=1` shows the board bottom-first | Free - the same index scanned **backward** |

**Filters change the population**, so `rank_for` / `board_size` / windows all apply them - a rank is always
"position within the currently-viewed board." **Invert is display-only**: rank NUMBERS stay canonical (from
the top), so an inverted board simply counts down.

A jump (typed rank, searched hunter, or the "You #N" widget) resolves to a canonical rank and is a
**client-side scroll** to that rank's offset in the virtual spacer -- no server round-trip; the row's data
is fetched by the normal range read as the window lands there.

---

## The panel is deliberately not server-rendered

Every other panel on game detail ships in the initial HTML for SEO. This one does **not**: it is the only
panel whose cost scales with a game's popularity, and most visitors arrive from search wanting trophy
info and never open it. It is fetched on first tab activation and cached in the DOM thereafter.

`test_detail_page_offers_the_tab_but_does_not_render_the_board` asserts this, because it is exactly the
kind of thing a later "just include it" refactor would quietly undo.

---

## Gotchas and Pitfalls

- **`Game.played_count` is NOT the board size.** It counts hidden rows (`hidden_flag` / `user_hidden`),
  so the header would disagree with the list. Use `board_size()`. On beta the gap is small (2 of 1,421)
  but it is not zero.
- **The URL sits under the Cloudflare origin guard.** `/games/<x>/<y>/` is the shape
  `CloudflareOriginGuardMiddleware` bounces when a request lacks a `CF-Ray` header. Real browser fetches
  carry it (the page itself came through the proxy), so this is intended protection - but tests must send
  the header, and any future direct server-to-server consumer would be redirected.
- **The URL must be declared before `game_detail_with_profile`** in `urls.py`, or `leaderboard` is
  captured as a `psn_username`.
- **JS init order.** The lazy-load flag is declared *above* the view-switcher IIFE. That IIFE runs
  immediately and honors an initial `?view=` by calling `showView()` during setup, so a `let` declared
  after it would still be in its temporal dead zone and throw - which previously aborted the whole file.
- **Row height is fixed and load-bearing.** The virtualizer keys the spacer height, every row's `top`, and
  jump targets off `--lb-row-h` (44px desktop / 58px the two-line mobile row). If a row's content ever
  exceeds that height it clips (`overflow: hidden` on the card); if the value and the real row disagree,
  positions drift. Changing the row layout means re-checking the height. The JS re-lays-out on the md
  breakpoint resize.
- **A control change re-fetches the WHOLE panel** (new `board_size` -> new spacer, re-ranked viewer). The
  JS reads the toggle `aria-pressed` states to rebuild the query, so the returned HTML re-renders the
  toggles in the state it was asked for, and `lbVirtualize` re-initialises (teardown removes the old scroll
  listeners first).
- **First paint isn't a FOUC** even though rows are `position: absolute` with no server-set `top`: the panel
  is lazy-loaded, so `innerHTML = html` and `lbVirtualize` (which sets every `top`) run in the same
  synchronous step before the browser paints.

---

## Roadmap

Overall, group standings, group speed, and playtime boards are **shipped** (see [Board family](#board-family)).
Remaining / deferred:

- **Anti-cheat.** Time is the most spoofable thing we rank on (offline clock manipulation), and the speed
  board is the obvious target. Shipped guards are **data hygiene only** -- `completion_seconds` is null for
  non-positive / untimestamped elapsed, so a broken pair can't sort a garbage row to the top. Real outlier
  detection (vs. the game's typical completion time) is parked until it's worth the effort.
- **Per-board control affordances.** `only_earners` is a no-op on speed/playtime boards (all complete / not a
  completion metric); the toggle still renders. Hiding irrelevant toggles per board is a polish follow-up.

---

## Files

| File | Role |
|------|------|
| `trophies/services/game_leaderboard_service.py` | The `Board` engine (ordering, windowed reads, rank, suggest), the board types, `resolve_board`, `board_menu` |
| `trophies/views/game_leaderboard_views.py` | The response shapes; board resolution; row display-field stamping |
| `trophies/models.py` `ProfileTrophyGroup` | Per-group standings denorm |
| `trophies/services/psn_api_service.py` `update_trophy_group_stats` | Maintains the denorm on sync |
| `trophies/management/commands/backfill_profile_trophy_groups.py` | One-time seed of the denorm |
| `templates/trophies/partials/game_detail/_leaderboard_panel.html` | Board switcher + controls + header + list |
| `templates/trophies/partials/game_detail/_leaderboard_rows.html` | A window of rows (standings / speed / playtime variants) |
| `static/js/game-detail.js` | `loadLeaderboard`, `lbVirtualize`, board/control wiring |
| `static/css/components/game-detail.css` | `.gd-lb*` |
| `core/management/commands/measure_leaderboard.py` | Read-only feasibility/perf probe |

## Related

- [Management Commands](../guides/management-commands.md) - `measure_leaderboard`
- [Data Model](../architecture/data-model.md)
