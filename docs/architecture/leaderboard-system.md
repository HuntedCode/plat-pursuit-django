# Leaderboard System

The leaderboard system ranks hunters by badge progress and Badge Points, per series and globally.

> **This system is MID-MIGRATION (2026-08).** Two backends run side by side. Read
> [the rebuild plan](../design/rebuild/leaderboards-rebuild.md) before changing anything here.

## Architecture Overview

### The current state: two backends

| Board | Backend | Notes |
|---|---|---|
| Badge Points (global + country) | Redis sorted sets | Still read by `profile_card_service` + 2 dashboard modules, which display the LEGACY `ProfileGamification.total_badge_xp`. Ranking that against the new store would print a figure beside a rank derived from a different number |
| Per-series earners | Redis sorted sets | Still read by `frame_service` for the legacy badge frame |
| Community XP | Redis scalar | Unrelated to the boards; per-series total |
| **Trophies** | **Postgres** (`Profile`'s own counters) | All games, not badge-scoped. Was "Global Progress" -> "Badge Trophies" -> this |
| **Per-series board** | **Postgres** (`SeriesBadgeStanding`) | Redis version DELETED. Earners + chasers MERGED into one board |
| **Career XP** | **Postgres** (`ProfileCareerStanding`) | New; no Redis equivalent ever existed |
| **Badge Points, per edition** | **Postgres** (`ProfileEditionStanding`) | The edition FILTER. Same columns, same names, pre-sliced |

The Postgres side is `trophies/services/badge_leaderboards.py` ("Lane B"): indexed reads over
denormalized standing columns, written by the recompute the sync path already runs. No cron, no
sorted sets, and identity is read live at render so a renamed hunter cannot show a stale name.

The Redis remainder goes with the **badge cutover**, which repoints its consumers off the legacy
`Badge`/`UserBadge` models.

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
| `trophies/services/redis_leaderboard_service.py` | REMAINING sorted set operations (earners, XP, country, community XP). Progress boards deleted 2026-08 |
| `trophies/services/badge_leaderboards.py` | **Lane B**: every Postgres-backed board, `hydrate()`, `BoardPaginator`/`BoardPage` |
| `trophies/services/badge_xp.py` | The write seam: `recompute_standing` materializes the standings (xp, progress, `advanced_at`, badges held). Runs on every sync -- it must never grow a profile-wide aggregate |
| `trophies/services/leaderboard_service.py` | ORM computation functions (used by rebuilds) |
| `trophies/services/xp_service.py` | XP + country XP + community XP sorted set writes via `update_profile_gamification()`, bulk pipeline via `bulk_gamification_update()` |
| `trophies/signals.py` | Earners sorted set writes on UserBadge post_save/post_delete |
| `core/management/commands/update_leaderboards.py` | LEGACY Redis rebuild. Its cron entry is retired -- delete it when the rebuild branch deploys |
| `trophies/management/commands/refresh_badge_series.py` | Calls `rebuild_series_leaderboards()` after badge awards |
| `trophies/views/badge_views.py` | `BadgeLeaderboardsView`, `OverallBadgeLeaderboardsView`, `BadgeDetailView` |
| `trophies/services/dashboard_service.py` | `provide_badge_xp_leaderboard()` and `provide_country_xp_leaderboard()` dashboard modules |

## Leaderboard Types

### Per-Series (one sorted set per live badge series)

| Type | Redis Key | Score Formula | Update Trigger |
|------|-----------|---------------|----------------|
| Earners | `lb:earners:{slug}:scores` | `tier * 10^12 + (10^12 - earned_at_unix)` | UserBadge post_save/post_delete signal + sync-complete (bulk exit) |
| Community XP | `lb:community_xp:{slug}` | N/A (scalar, INCRBY delta) | `update_profile_gamification()` delta + cron reconciliation |

### Global

| Type | Redis Key | Score Formula | Update Trigger |
|------|-----------|---------------|----------------|
| Total XP | `lb:xp:scores` | `total_badge_xp * 10^4 + total_badges` | `update_profile_gamification()` signal |

### Per-Country (one sorted set per country with active users)

| Type | Redis Key | Score Formula | Update Trigger |
|------|-----------|---------------|----------------|
| Country XP | `lb:xp:country:{cc}:scores` | Same as Total XP | `update_profile_gamification()` signal |
| Country Index | `lb:xp:country:index` | N/A (SET of active country codes) | SADD during incremental updates + cron rebuild |

Country leaderboards use the same composite score as the global XP leaderboard but are partitioned by ISO 3166-1 alpha-2 country code (from `Profile.country_code`). Profiles without a country code are excluded. The country index SET tracks which countries have active leaderboards, used by the country picker UI.

## Key Flows

### Incremental Updates (Real-Time)

**XP Leaderboard + Country XP + Community XP**: Signal fires on UserBadgeProgress/UserBadge change -> `update_profile_gamification()` -> `update_xp_entry()` writes to global sorted set + `update_country_xp_entry()` writes to per-country sorted set (if profile has country_code) + `update_community_xp_deltas()` applies per-series XP deltas via INCRBY. During bulk sync, writes are pipelined via `bulk_gamification_update()`.

**Earners Leaderboard**: Signal fires on UserBadge post_save/post_delete -> `_update_earner_leaderboard_on_badge_change()` finds highest tier -> ZADD or ZREM. During bulk sync, earner updates are also applied at `bulk_gamification_update()` exit via `update_earner_leaderboards_for_profile()`, which finds the highest tier per series for the profile and writes all entries in a single pipeline.

**Progress Leaderboard**: After `bulk_gamification_update()` exits -> `update_progress_leaderboards_for_profile()` computes per-profile trophy counts for affected series -> ZADD/ZREM per series + global.

### Profile Linking Backfill

When a `Profile` is linked to a `User` (either a brand-new account's first verification or a claim of a previously-unowned synced profile), `VerificationService.link_profile_to_user()` calls `_backfill_leaderboards_for_newly_linked_profile()` to write XP, country XP, earner, and progress entries from the existing `ProfileGamification` row. This is required because all incremental writers gate on `profile.is_linked` and any updates that ran before linking were silently skipped. Without the backfill, the first sync's leaderboard updates are lost and the profile is invisible until the next sync_complete or the reconciliation cron.

The backfill reads pre-aggregated data (the same source the cron rebuild uses), so it cannot place stale or inconsistent entries on the leaderboard. If `ProfileGamification` does not exist yet (e.g., a claimed unowned profile with zero badges), the backfill is a safe no-op and the next sync_complete picks the profile up via the now-unblocked incremental path.

### Reconciliation Cron

1. `update_leaderboards` runs periodically (recommended: every 12-24 hours)
2. Calls `rebuild_xp_leaderboard()`, `rebuild_global_progress_leaderboard()`, `rebuild_country_xp_leaderboards()`
3. For each live series: `rebuild_series_leaderboards(slug)` (earners + progress + community XP)
4. Individual failures caught and logged without blocking

### New Series Bootstrap

When adding a new badge series:
1. Run `refresh_badge_series --series <slug>` to award badges
2. Command automatically calls `rebuild_series_leaderboards(slug)` to backfill progress + community XP data
3. Or run `update_leaderboards --series <slug>` manually

### View Page Load

1. `ZREVRANGE` for the requested page, `HMGET` for display data
2. `ZREVRANK` for the current user's rank
3. `ZCARD` for total participant count
4. `RedisPaginator`/`RedisPage` provide template-compatible paginator interface

## Redis Keys (Raw Redis, DB 0)

| Key | Type | Purpose |
|-----|------|---------|
| `lb:xp:scores` | Sorted Set | XP leaderboard; member=profile_id, score=composite |
| `lb:xp:data` | Hash | XP display data; field=profile_id, value=JSON |
| `lb:earners:{slug}:scores` | Sorted Set | Per-series earners |
| `lb:earners:{slug}:data` | Hash | Earners display data |
| `lb:xp:country:{cc}:scores` | Sorted Set | Per-country XP leaderboard; same score as global XP |
| `lb:xp:country:{cc}:data` | Hash | Per-country XP display data |
| `lb:xp:country:index` | Set | Active country codes with leaderboard entries |
| `lb:community_xp:{slug}` | String (int) | Community XP total per series, maintained via INCRBY delta |
| `lb:meta:last_rebuild` | Hash | Rebuild timestamps per leaderboard key |

## Composite Score Precision

Redis sorted set scores are 64-bit IEEE 754 doubles, representing integers exactly up to 2^53 (~9 * 10^15).

- **XP**: `total_xp * 10^4 + total_badges` -> max ~10^10 (safe)
- **Earners**: `tier * 10^12 + (10^12 - timestamp)` -> max ~5 * 10^12 (safe)
- **Progress**: `plats * 10^9 + golds * 10^6 + silvers * 10^3 + bronzes` -> max ~10^12 (safe)

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

- **Sorted sets must be seeded before first use**: Run `python manage.py update_leaderboards` after deployment to populate all sorted sets from existing data. Without this, leaderboard pages will show empty results.

- **New series need explicit rebuild**: Incremental updates only catch new events. When a badge series is created, existing trophy data won't appear in the progress sorted set until a rebuild runs. `refresh_badge_series` does this automatically.

- **Bulk pipeline scope**: During `bulk_gamification_update()`, XP and community XP sorted set writes are collected into a Redis pipeline and executed together. Earner and progress leaderboard updates run after the pipeline executes (they each create their own pipeline internally).

- **Display data staleness**: Username, avatar, and premium status are stored in Redis hashes and refreshed during gamification updates. Changes outside of sync (e.g., admin edits) won't reflect until the next cron reconciliation.

- **ProfileGamification drift**: If XP signal handlers fail silently, both the denormalized table and sorted set scores drift. Use `audit_profile_gamification` to detect mismatches, then run `update_leaderboards` to reconcile sorted sets.

- **Community XP uses INCRBY deltas**: Updated incrementally by computing the difference between old and new `series_badge_xp` values in `update_profile_gamification()`. If the delta calculation drifts (e.g., missed signal, Redis flush), the cron reconciliation does a full recompute via `rebuild_community_xp(slug)`.

- **Country leaderboard stale entries on region change**: If a user's PSN region changes (extremely rare), the old country's sorted set retains a stale entry until the next cron reconciliation. The new country gets the correct entry immediately. This is by design: adding eager cleanup would add complexity for a near-zero-frequency event.

- **All incremental writers gate on `is_linked`**: `update_xp_entry`, `update_country_xp_entry`, `update_earner_leaderboards_for_profile`, and `update_progress_leaderboards_for_profile` all skip profiles where `is_linked=False`. This is correct (unowned profiles should not appear on user-facing leaderboards), but it means linking a profile must explicitly backfill leaderboards from `ProfileGamification`, otherwise the new user is invisible until the next sync_complete or the cron reconciliation. `VerificationService.link_profile_to_user()` handles this; any other code that flips `is_linked` to `True` (none today) must do the same.

## Management Commands

| Command | Purpose | Usage |
|---------|---------|-------|
| `update_leaderboards` | Full rebuild of all leaderboards (reconciliation) | `python manage.py update_leaderboards` (cron) |
| `update_leaderboards --series <slug>` | Targeted rebuild for one series | After adding a new badge series |
| `update_leaderboards --country <CC>` | Targeted rebuild for one country | After data fixes for a specific country |
| `refresh_badge_series --series <slug>` | Award badges + rebuild series leaderboards | New series setup |

## Related Docs

- [Badge System](badge-system.md): Parent system; leaderboards rank badge progress and XP
- [Gamification](gamification.md): ProfileGamification model that powers the XP leaderboard
- [Redis Keys](../reference/redis-keys.md): Complete key map for raw Redis and Django cache
- [Cron Jobs](../guides/cron-jobs.md): Scheduling for `update_leaderboards`
