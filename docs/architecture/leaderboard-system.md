# Leaderboard System

The leaderboard system ranks users by badge progress and XP across individual badge series and globally. Rankings are maintained in **Redis sorted sets** with incremental signal-driven updates, providing near-real-time leaderboards with O(log n) rank lookups and efficient pagination.

## Architecture Overview

The system uses **Redis sorted sets** for all leaderboard data:

- Each leaderboard is a sorted set (scores) paired with a hash (display data)
- Scores are updated incrementally via Django signals when badges are earned, XP changes, or trophies are synced
- Views query sorted sets directly for pagination (`ZREVRANGE`) and rank lookup (`ZREVRANK`)
- A reconciliation cron (`update_leaderboards`) runs periodically to fully rebuild all sorted sets from source data, catching any drift from missed signals

The XP leaderboard benefits from `ProfileGamification` denormalization: XP totals are maintained in real-time via signals, so both the sorted set update and the rebuild query read pre-aggregated data.

## File Map

| File | Purpose |
|------|---------|
| `trophies/services/redis_leaderboard_service.py` | All sorted set operations, RedisPaginator/RedisPage, rebuild functions |
| `trophies/services/leaderboard_service.py` | ORM computation functions (used by rebuilds) |
| `trophies/services/xp_service.py` | XP + country XP + community XP sorted set writes via `update_profile_gamification()`, bulk pipeline via `bulk_gamification_update()` |
| `trophies/signals.py` | Earners sorted set writes on UserBadge post_save/post_delete |
| `core/management/commands/update_leaderboards.py` | Cron: rebuilds all sorted sets, supports `--series` and `--country` flags |
| `trophies/management/commands/refresh_badge_series.py` | Calls `rebuild_series_leaderboards()` after badge awards |
| `trophies/views/badge_views.py` | `BadgeLeaderboardsView`, `OverallBadgeLeaderboardsView`, `BadgeDetailView` |
| `trophies/services/dashboard_service.py` | `provide_badge_xp_leaderboard()` and `provide_country_xp_leaderboard()` dashboard modules |

## Leaderboard Types

### Per-Series (one sorted set per live badge series)

| Type | Redis Key | Score Formula | Update Trigger |
|------|-----------|---------------|----------------|
| Earners | `lb:earners:{slug}:scores` | `tier * 10^12 + (10^12 - earned_at_unix)` | UserBadge post_save/post_delete signal |
| Progress | `lb:progress:{slug}:scores` | `plats * 10^9 + golds * 10^6 + silvers * 10^3 + bronzes` | Sync-complete (bulk_gamification_update exit) |
| Community XP | `lb:community_xp:{slug}` | N/A (scalar, INCRBY delta) | `update_profile_gamification()` delta + cron reconciliation |

### Global

| Type | Redis Key | Score Formula | Update Trigger |
|------|-----------|---------------|----------------|
| Total XP | `lb:xp:scores` | `total_badge_xp * 10^4 + total_badges` | `update_profile_gamification()` signal |
| Total Progress | `lb:progress:global:scores` | Same as per-series progress | Sync-complete |

### Per-Country (one sorted set per country with active users)

| Type | Redis Key | Score Formula | Update Trigger |
|------|-----------|---------------|----------------|
| Country XP | `lb:xp:country:{cc}:scores` | Same as Total XP | `update_profile_gamification()` signal |
| Country Index | `lb:xp:country:index` | N/A (SET of active country codes) | SADD during incremental updates + cron rebuild |

Country leaderboards use the same composite score as the global XP leaderboard but are partitioned by ISO 3166-1 alpha-2 country code (from `Profile.country_code`). Profiles without a country code are excluded. The country index SET tracks which countries have active leaderboards, used by the country picker UI.

## Key Flows

### Incremental Updates (Real-Time)

**XP Leaderboard + Country XP + Community XP**: Signal fires on UserBadgeProgress/UserBadge change -> `update_profile_gamification()` -> `update_xp_entry()` writes to global sorted set + `update_country_xp_entry()` writes to per-country sorted set (if profile has country_code) + `update_community_xp_deltas()` applies per-series XP deltas via INCRBY. During bulk sync, writes are pipelined via `bulk_gamification_update()`.

**Earners Leaderboard**: Signal fires on UserBadge post_save/post_delete -> `_update_earner_leaderboard_on_badge_change()` finds highest tier -> ZADD or ZREM.

**Progress Leaderboard**: After `bulk_gamification_update()` exits -> `update_progress_leaderboards_for_profile()` computes per-profile trophy counts for affected series -> ZADD/ZREM per series + global.

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
| `lb:progress:{slug}:scores` | Sorted Set | Per-series progress |
| `lb:progress:{slug}:data` | Hash | Progress display data |
| `lb:progress:global:scores` | Sorted Set | Global progress |
| `lb:progress:global:data` | Hash | Global progress display data |
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

- **Sorted sets must be seeded before first use**: Run `python manage.py update_leaderboards` after deployment to populate all sorted sets from existing data. Without this, leaderboard pages will show empty results.

- **New series need explicit rebuild**: Incremental updates only catch new events. When a badge series is created, existing trophy data won't appear in the progress sorted set until a rebuild runs. `refresh_badge_series` does this automatically.

- **Bulk pipeline scope**: During `bulk_gamification_update()`, XP and community XP sorted set writes are collected into a Redis pipeline and executed together. Progress leaderboard updates run after the pipeline executes (they have their own pipeline internally).

- **Display data staleness**: Username, avatar, and premium status are stored in Redis hashes and refreshed during gamification updates. Changes outside of sync (e.g., admin edits) won't reflect until the next cron reconciliation.

- **ProfileGamification drift**: If XP signal handlers fail silently, both the denormalized table and sorted set scores drift. Use `audit_profile_gamification` to detect mismatches, then run `update_leaderboards` to reconcile sorted sets.

- **Community XP uses INCRBY deltas**: Updated incrementally by computing the difference between old and new `series_badge_xp` values in `update_profile_gamification()`. If the delta calculation drifts (e.g., missed signal, Redis flush), the cron reconciliation does a full recompute via `rebuild_community_xp(slug)`.

- **Country leaderboard stale entries on region change**: If a user's PSN region changes (extremely rare), the old country's sorted set retains a stale entry until the next cron reconciliation. The new country gets the correct entry immediately. This is by design: adding eager cleanup would add complexity for a near-zero-frequency event.

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
