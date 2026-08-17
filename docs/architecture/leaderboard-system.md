# Leaderboard System

The leaderboard system ranks hunters by badge progress and Badge Points, per series and globally.

> **Migration COMPLETE (2026-08).** There is one backend: Postgres. The Redis sorted-set leaderboards and
> `redis_leaderboard_service` / `leaderboard_service` / `xp_service` were deleted in badge cutover step
> 5b.4. History: [leaderboards-rebuild.md](../design/rebuild/leaderboards-rebuild.md),
> [badge-backend-rebuild.md](../design/rebuild/badge-backend-rebuild.md).

## Architecture Overview

### One backend

| Board | Store | Notes |
|---|---|---|
| Trophies | `Profile`'s own counters | All games, not badge-scoped. Was "Global Progress" -> "Badge Trophies" -> this |
| Badge Points (global + country) | `ProfileBadgeStanding` | Also carries `badges_held`, the secondary stat |
| Badge Points, per edition | `ProfileEditionStanding` | The edition FILTER. Same columns, same names, pre-sliced |
| Per-series board | `SeriesBadgeStanding` | Earners + chasers MERGED into one board |
| Career XP | `ProfileCareerStanding` | No Redis equivalent ever existed |

All of it is `trophies/services/badge_leaderboards.py` ("Lane B"): indexed reads over denormalized
standing columns, written by the recompute the sync path already runs. No cron, no sorted sets, and
identity is read live at render so a renamed hunter cannot show a stale name.

**What the Redis backend cost, and why it is worth remembering.** It needed a rebuild cron
(`update_leaderboards`), incremental writers on four separate paths, a link-time backfill in
`verification_service` because those writers gated on `is_linked`, and a reconciliation pass because the
increments drifted anyway. All of that existed to keep a second copy of numbers Postgres already had.
The standings are recomputed from scratch on every evaluation, so there is nothing to drift and nothing
to reconcile. If a future board feels like it needs a sorted set, re-read this paragraph first.

### The surfaces (steps 4-8, complete)

Boards live on the thing they RANK; the hub is a discovery layer over them. There is exactly one
canonical location per board, so no two pages can drift.

| Surface | URL | What it is |
|---|---|---|
| Global Boards | `/leaderboards/` | Trophies / Badge Points / Career XP, `.pp-switch` tabs, country + edition FILTERS |
| Game Boards | `/leaderboards/games/` | Directory -> game detail's Ranks panel |
| Badge Boards | `/leaderboards/badges/` | Directory -> badge detail's Ranks section |
| Job Boards | `/leaderboards/jobs/` | Directory -> job detail's Ranks tab |
| Jobs catalogue | `/jobs/`, `/jobs/<slug>/` | In the BROWSE hub; Contracts + Ranks tabs, public |

The three directories share one `BoardDirectoryView` base and one template. Each is THIN by rule: search
plus exactly two sorts (alphabetical default, most entrants), no filter panel, no country facet. Without
that rule they converge into second copies of `/games/`, `/badges/` and `/jobs/`.

Directory previews use `ROW_NUMBER() OVER (PARTITION BY ...)` -- one query for the whole page's top
slices, because a per-card board read compounds under infinite scroll.

Country is a FILTER on the full boards only, never a board and never a directory facet. On the boards it
is one WHERE served by a `(..., country_code, ...board order)` composite; on the directories it would
multiply the cache surface ~200x for a view few would use.

### The two filters on Global Boards

Both change what the board READS rather than post-filtering it, which is what keeps a slice the same cost
as the whole thing.

| Filter | Applies to | Mechanism |
|---|---|---|
| Country | all three boards | a WHERE on `country_code`, served by `(country, ...board order)` |
| Edition | **Badge Points only** | a different STORE: `ProfileEditionStanding`, indexed `(edition, [country,] ...board order)` |

Edition exists because Legacy HD and Ultra HD are genuinely different games -- XP accrues per GROUP BADGE,
not per series -- so "who leads Legacy HD" is a question the all-editions board cannot answer. It applies to
Badge Points ALONE: an edition is a PlatformGroup, i.e. a badge concept, and neither Trophies (every game)
nor Career XP (the jobs economy) has editions to slice. A control that renders but changes nothing promises
a slice that does not exist.

### The Trophies board is not badge-scoped, deliberately

It reads `Profile.total_plats` / `total_trophies`, maintained incrementally by the EarnedTrophy signals and
reconciled nightly by `recalc_profile_counters`. **Nothing is denormalized for it.**

It replaced a "Badge Trophies" board that counted trophies across badge-stage games. Keeping that figure
current meant a full-library `EarnedTrophy` aggregate per profile inside `badge_xp.recompute_standing` --
affordable while that seam ran only from `evaluate_badges`, and a per-sync cost the moment the engine was
wired into `sync_complete` (badge cutover 5a). That is the same inline-aggregate pattern `recalc_earn_rates`
exists to undo after the May 2026 incident.

It is also a better board. "Trophies earned in games that happen to have badges" largely measures how many
badge-covered games somebody has played; platinums earned is the figure every hunter already knows about
themselves. The three boards now read one per domain: overall hunting, badges, career.

`ProfileEditionStanding` names its columns identically to `ProfileBadgeStanding`, so `badge_store(edition)`
picks a manager and every board query stays as written. An unrecognised key returns an EMPTY store rather
than falling back to all editions: silently widening would show the global board under an edition heading.
The view validates the key first, so that path only runs on a bug.

**Editions do NOT overlap** for the figures that remain: a group badge belongs to exactly one platform
group, so per-edition XP and badges-held sum to the all-editions totals. (Per-edition TROPHY counts DID
overlap -- a cross-gen game qualifies for both groups -- and went with the Badge Trophies board.)

## File Map

| File | Purpose |
|------|---------|
| `trophies/services/badge_leaderboards.py` | Every board read: the `*_KEYS` orders, `_ahead_q`, `hydrate()`, `BoardPaginator`/`BoardPage`, `board_count()`, `active_editions()` |
| `trophies/services/badge_xp.py` | The write seam: `recompute_standing` materializes the standings (xp, progress, `advanced_at`, badges held). Runs on every sync -- it must never grow a profile-wide aggregate |
| `trophies/services/game_leaderboard_service.py` | Per-game boards. Where the rank-equals-position rule was first solved |
| `trophies/views/badge_views.py` | `BadgeBoardsView`, `GameBoardsView`, `JobBoardsView`, `OverallBadgeLeaderboardsView` |

## How a board is served

Every board is one indexed Postgres read. There is no cache, no cron and no sorted set.

1. `board_store(tab, ...)` picks the standing table for the tab (and the edition slice, if filtered).
2. `_slice()` applies the country filter -- a WHERE served by a composite index, not a post-filter.
3. `BoardPaginator` orders by that board's `*_KEYS` and slices the page.
4. `hydrate()` joins identity (name, avatar, country) at render, so a renamed hunter can never show a
   stale name.
5. `*_rank()` answers "where am I" by COUNTing everyone ahead, expressing the SAME key list via
   `_ahead_q`.

The standings themselves are written by `badge_xp.recompute_standing`, which the sync path already runs.
They are recomputed from scratch each time, so no incremental writer exists to drift.

## Gotchas and Pitfalls

- **A board's rank and a board's row numbering are two different definitions, and they only agree if the
  ordering is TOTAL.** `page()` numbers by slot; `*_rank()` counts everyone ahead. Every board's canonical
  order therefore ends in `profile_id` (the `*_KEYS` tuples) and the rank count expresses that same full
  key list via `_ahead_q`, the way `game_leaderboard_service` already did. Badge Points is quantized to
  `500a + 600b`, so large ties are the norm, not an edge case: without the unique tail every member of a
  tie group was told the group's FIRST slot.

- **A board's membership rule (`> 0`) belongs in the service, beside the rows.** It lived in the view as a
  hand-rolled copy next to the paginator, and the copy drifted -- Career grew the filter in the service and
  not in the view, so the last page ran past the total the footer promised. `board_count()` is now the one
  definition, read by the paginator and the header tally alike.

- **`recompute_standing` must not grow a profile-wide aggregate.** It runs on every sync. It briefly
  carried the badge-game trophy tally (a full-library `EarnedTrophy` scan) and that is exactly what
  `recalc_earn_rates` was created to remove from `sync_complete`. Everything the seam writes should derive
  from the DesiredState it was handed plus bounded per-profile reads.

- **The edition split's `Game` join is gone with the tally.** Recorded because the reasoning still applies
  to anything that reintroduces a per-edition trophy figure: Checked against the
  emitted SQL, not assumed: `game_id IN (SELECT ...)` reads Game under its own alias, so the outer query
  was *not* already joining it. The added join is a PK probe over the rows that survive the IN test, so it
  is strictly cheaper than the `Trophy` join the same query already pays -- a constant factor. It is on the
  SYNC path only. To measure it on real data, time `evaluate_badges --username <whale>` before and after.

- **Rendering a board does no Python aggregation at all.** Every board is an indexed `ORDER BY` plus one
  `hydrate()`. The Python loop in `badge_trophy_tallies` iterates the GROUPED AGGREGATE (one row per
  distinct platform list x tier, bounded by the catalogue's platform vocabulary), never `EarnedTrophy`
  rows. `test_the_aggregate_does_not_grow_with_the_library` is what stops that distinction eroding.

- **Every store with a `country_code` mirror must be in `signals.country_mirrored_standings()`.** Missing
  one does not error. It leaves that board ranking a relocated hunter under their old flag while the others
  have already moved them, which only a reader who emigrated would ever notice.
  `ProfileEditionStanding` shipped with the column and without the entry;
  `test_every_store_with_a_country_mirror_is_in_the_propagation_list` now asks the models rather than the
  list.

- **The editions do not sum to the all-editions row, and that is not a bug.** A cross-gen game qualifies
  for both platform groups (the engine matches on platform INTERSECTION), so its trophies count in both.
  Splitting them (half each, or first-match-wins) would make an edition's trophy count disagree with the
  badges that edition awards.

- **`recompute_standing` may be scoped to a subset of series, so every profile-wide figure must be
  re-summed from ALL the profile's `SeriesBadgeStanding` rows** -- never from the call's own results. That
  is why per-edition XP is stored per series in `group_xp` rather than only aggregated. Summing the call's
  results instead would silently halve a hunter's edition standing every time one series was re-run.

- **A board's membership rule belongs in the row function, not only on the count that pages it.** An
  edition standing survives on zero points when the hunter has trophies but no cleared gating stage there,
  so an unfiltered read would hand the last page rows the count never promised.

- **`BOARD_MIN_ENTRANTS_*` is env-overridable, so tests must pin it.** A dev box that lowered the games
  gate turned an unrelated directory test red for a behaviour that had not changed.

- **A new badge series needs no rebuild.** This used to be a real chore: the sorted sets only caught
  incremental events, so a newly authored series showed nothing until a rebuild ran. Standings are
  recomputed from scratch on every evaluation, so `evaluate_badges --series <slug>` (or just the nightly
  `--all`) is the whole procedure.

## Management Commands

None. The boards read live from the standing tables, so there is nothing to rebuild.

`evaluate_badges --all` (nightly) is what keeps the standings honest, but it is a badge-evaluation
command, not a leaderboard one -- see [badge-system.md](badge-system.md).

> **Removed 2026-08:** `update_leaderboards`. It rebuilt the Redis sorted sets from
> `ProfileGamification`. **Its Render cron entry must be deleted by hand at deploy** -- the schedule
> outlives the code and will keep firing against a command that no longer exists. Tracked in
> [prod-deploy-checklist.md](../design/rebuild/prod-deploy-checklist.md).

## Related Docs

- [Badge System](badge-system.md): Parent system; leaderboards rank badge progress and XP
- [Gamification](gamification.md): ProfileGamification model that powers the XP leaderboard
- [Redis Keys](../reference/redis-keys.md): Complete key map for raw Redis and Django cache
- [Cron Jobs](../guides/cron-jobs.md): the nightly `evaluate_badges --all` that keeps standings fresh
