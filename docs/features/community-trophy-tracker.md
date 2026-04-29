# Community Trophy Tracker

A daily Discord webhook post that summarizes the previous calendar day's trophy activity from Discord-linked profiles. Posted at ~12:30 PM ET via the platinum webhook channel and tracks all-time records to give the community a moving target. Builds engagement through scoreboard-chasing without spotlighting individuals.

## Architecture Overview

The tracker is a single-flow pipeline: a Render cron fires the management command twice daily (16:30 and 17:30 UTC for DST safety), the command computes the previous ET day's aggregates from `EarnedTrophy` records, persists them as a `CommunityTrophyDay` row, detects records vs prior history, and queues a Discord webhook embed via the existing platinum webhook plumbing.

A second access path (`/api/community-stats/today/`) serves a live, in-progress snapshot of the current day for PlatBot slash-command consumption. Today's snapshot is Redis-cached for 60 seconds, keyed on the ET date so the cache rolls over naturally at midnight ET.

The compute is a single PostgreSQL aggregate query with three conditional `Count` filters, fully covered by the existing `(profile, earned, earned_date_time)` composite index on `EarnedTrophy`. No N+1, no subquery, no row scan.

The four tracked stats are intentionally redundant (a platinum is a trophy; an Ultra Rare can be a platinum). The PP Score formula embraces the overlap as a feature: a UR platinum scores 1 + 5 + 3 = 9 points instead of being deduplicated.

## File Map

| File | Purpose |
|------|---------|
| `core/models.py` | `CommunityTrophyDay` daily aggregate model |
| `core/services/community_trophy_tracker.py` | Compute logic, ET day boundary math, embed builder, records detection |
| `core/management/commands/post_community_trophy_tracker.py` | Cron entrypoint: compute -> store -> post -> mark posted |
| `api/community_stats_views.py` | `/today/`, `/<date>/`, `/records/` read-only public endpoints |
| `api/urls.py` | URL routes (under `/api/community-stats/`) |
| `trophies/discord_utils/discord_notifications.py` | Reused `queue_webhook_send` for Discord delivery |

## Data Model

### `core.CommunityTrophyDay`

| Field | Type | Notes |
|---|---|---|
| `date` | `DateField` (unique) | The ET calendar day these stats cover. |
| `total_trophies` | `PositiveIntegerField` | All earned trophies in the window from eligible profiles in clean games. |
| `total_platinums` | `PositiveIntegerField` | Subset where `Trophy.trophy_type='platinum'`. |
| `total_ultra_rares` | `PositiveIntegerField` | Subset where `Trophy.trophy_rarity=0` (PSN Ultra Rare). |
| `pp_score` | `PositiveIntegerField` | `total_trophies + 5*total_platinums + 3*total_ultra_rares` (frozen at compute time). |
| `eligible_profile_count` | `PositiveIntegerField` | Snapshot of `Profile.discord_id IS NOT NULL` count when this row was first created. Diagnostic only. |
| `posted_at` | `DateTimeField` (nullable) | Set when the Discord webhook is queued. Idempotency gate. |
| `computed_at` | `DateTimeField` (auto_now_add) | First-creation timestamp. |

Indexes on each tracked stat (descending) so the records query (`Max()` per stat) hits an index scan instead of a sequential scan as the table grows.

## Eligibility Rules

A `(profile, earned_trophy)` pair counts toward a day's totals when:

1. `Profile.discord_id IS NOT NULL` (user has linked Discord via PlatBot).
2. `EarnedTrophy.earned=True`.
3. `EarnedTrophy.earned_date_time` falls in the ET calendar day's UTC bounds.
4. `Trophy.game.shovelware_status='clean'` (auto/manually flagged shovelware games are excluded).

**Note**: `manually_cleared` is NOT counted. Only `clean` qualifies. Tighter than some other site queries; intentional, since the tracker is a community celebration of legitimate achievement.

## Key Flows

### Daily Post

1. Render cron fires at **16:30 UTC** (12:30 PM EDT) and **17:30 UTC** (12:30 PM EST).
2. The management command resolves the target date as **yesterday in ET** (regardless of UTC date at run time).
3. `select_for_update` opens a transaction and either creates a fresh `CommunityTrophyDay` row or locks the existing one.
4. If the row's `posted_at` is set and `--force-repost` was not passed, the command exits silently. This is what makes the second cron a no-op 95% of the year and a successful post during DST transitions.
5. `compute_day_stats()` runs the single aggregate query and saves the four stats to the row.
6. `get_current_records(exclude_pk=day.pk)` fetches the prior maxima for each stat.
7. `build_embed_payload()` constructs the Discord embed with NEW RECORD badges where applicable. Embed color flips to gold (`0xFFD700`) on any record-breaking stat, otherwise platinum brand blue (`0x003791`).
8. `queue_webhook_send()` enqueues the payload on the existing `webhook_queue` background thread. Rate-limit / retry / proxy concerns are handled there.
9. `posted_at` is set after the queue call returns. Failures before this point leave the row unposted; the next manual run picks up where it left off.

### Live Today Endpoint

1. PlatBot (or any client) hits `GET /api/community-stats/today/`.
2. View resolves "today in ET" and constructs a cache key like `community_trophy_today:2026-04-29`.
3. On cache hit, the cached payload is returned. On miss, `build_today_payload()` computes the same aggregate against the current open-ended ET window, tacks on a freshness note and a UTC `computed_at` timestamp, caches for 60s, and returns.
4. Cache rolls over automatically at ET midnight because the date changes.

### Records Endpoint

1. `GET /api/community-stats/records/` returns the highest historical value for each tracked stat plus the date that record was set.
2. Uses Python-side `max()` over a small queryset (one row per day, ordered by date DESC). For sub-second response even with years of data; would only need to switch to per-stat aggregates with a window function if the table ever grew impractically large (unlikely).
3. Returns null per stat when no rows exist (pre-launch state).

## API Endpoints

| Method | Path | Auth | Purpose |
|--------|------|------|---------|
| GET | `/api/community-stats/<YYYY-MM-DD>/` | Public | Historical daily summary; 404 if row missing |
| GET | `/api/community-stats/today/` | Public | Live in-progress totals for today (ET); 60s cache |
| GET | `/api/community-stats/records/` | Public | All-time max per stat with the date set |

All three return JSON. AllowAny permissions: aggregate community data, no PII.

## Integration Points

- **PlatBot**: Future slash commands (`/trophystats yesterday`, `/trophystats today`, `/trophystats records`) consume the three API endpoints above. No PlatPursuit changes required when those commands ship.
- **`refresh_profiles` cron** (see [cron-jobs.md](../guides/cron-jobs.md)): Discord-verified profiles refresh every 12h (was 24h, dropped specifically for this feature so trophies earned in the final hour of an ET day are reliably synced before the 12:30 PM ET post).
- **`trophies/discord_utils/discord_notifications.py`**: Reused for webhook delivery. The same channel that receives platinum and badge notifications also receives the daily tracker. To split into a dedicated channel, change the `webhook_url` argument in the management command.

## Gotchas and Pitfalls

- **PP Score weights are frozen at compute time.** The values stored in `pp_score` reflect the formula at the moment of the post. If you change the constants in `community_trophy_tracker.py`, historical rows do NOT recompute. Records will become apples-to-oranges; consider running a one-shot recompute migration if the change is significant.
- **`posted_at` is sticky.** A bad post (wrong stats, broken embed, etc.) does not unposting itself. To re-post: edit the row in admin to clear `posted_at`, OR run with `--force-repost`. Either way you must ALSO manually delete the bad message in Discord (the webhook does not edit prior messages).
- **The two-cron DST strategy is intentional.** Do NOT consolidate to one cron. Removing either entry will cause a half-year of off-time posts.
- **`/today/` cache TTL is 60 seconds.** If you change the underlying compute logic and need everyone to see fresh values immediately, either flush the cache (`cache.delete(cache_key)`) or wait a minute.
- **`/today/` is sync-lag-aware, not sync-lag-free.** A user who hasn't synced since 2 AM won't have their post-2 AM trophies reflected until their next refresh cycle (up to 12h for Discord-only, 6h for premium, 12h for basic). The `data_freshness_note` field in the response surfaces this. The yesterday post avoids the issue by waiting until 12:30 PM ET, by which time everyone has synced at least once.
- **Never write `CommunityTrophyDay` rows for in-progress days.** The model assumes the date is a complete ET calendar day. The `/today/` endpoint deliberately does not persist; partial days would muddy the records logic.
- **Adding a new stat requires backfill thinking.** If you add (say) `total_gold_trophies`, every prior row stores zero by default. Records on the new stat will look broken until enough days have passed to establish a baseline. Either backfill via management command or accept the gradual ramp-up.
- **Shovelware filter is `status='clean'` only.** Manually cleared games (`manually_cleared`) are excluded along with auto/manually flagged. If a community member's pursuit is dominated by manually-cleared games, their contribution is invisible. This is a deliberate trade-off; revisit only if it becomes a noticeable engagement issue.

## Management Commands

| Command | Purpose | Typical Usage |
|---------|---------|---------------|
| `post_community_trophy_tracker` | Compute and post the previous ET day's tracker. Default behavior is "yesterday." | `python manage.py post_community_trophy_tracker --dry-run` |

Flags:
- `--date YYYY-MM-DD`: Override target date (e.g., to backfill a missed day).
- `--force-repost`: Bypass `posted_at` idempotency gate. Use after manual remediation.
- `--dry-run`: Compute and print embed JSON. Does not write to DB or post.
- `--test-data`: Skip the DB entirely and post a fake-data preview to `DISCORD_TEST_WEBHOOK_URL` so you can eyeball the embed format. Refuses to send if the test webhook is empty (use `--use-platinum-webhook` to override, not recommended).
- `--test-scenario [record|normal]`: Only with `--test-data`. `record` (default) flags every stat as NEW RECORD (gold embed); `normal` suppresses the badges (blue embed). Run with both to verify both color states render correctly.

## Cache Keys

| Key Pattern | TTL | Purpose |
|-------------|-----|---------|
| `community_trophy_today:{YYYY-MM-DD}` | 60s | Live `/today/` endpoint response. ET date in key ensures natural midnight rollover. |

## Tuning the PP Score

Weights live in `core/services/community_trophy_tracker.py`:

```python
PP_SCORE_TROPHY_WEIGHT = 1
PP_SCORE_PLATINUM_WEIGHT = 5
PP_SCORE_ULTRA_RARE_WEIGHT = 3
```

Changing any of these affects ALL future computations. Historical rows keep their pre-change scores. If you tune the formula and need a clean record reset, truncate the table or zero out `pp_score` on prior rows.

## Related Docs

- [Cron Jobs](../guides/cron-jobs.md): Render cron registration for the daily post
- [Management Commands](../guides/management-commands.md): Full command reference
- [Notification System](../architecture/notification-system.md): Other ways the platinum webhook is used
- [Token Keeper](../architecture/token-keeper.md): Why the 12h Discord-verified sync cadence matters here
