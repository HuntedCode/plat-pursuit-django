# Cron Jobs

PlatPursuit uses **Render Cron Jobs** to run scheduled management commands. Each cron job is configured through the Render dashboard (not a config file) and executes a Django management command via `python manage.py <command>`. The TokenKeeper worker process handles real-time PSN sync jobs separately as a long-running daemon; the cron jobs described here cover everything else: profile refresh queuing, cache warming, leaderboard computation, analytics cleanup, and monthly recap delivery.

---

## Schedule Overview

| Time (UTC) | Command | Frequency | Dependencies |
|------------|---------|-----------|--------------|
| Every 30 min | `refresh_profiles` | Every 30 minutes | TokenKeeper must be running to process queued syncs |
| Top of every hour | `refresh_homepage_hourly` | Hourly | None |
| Top of every hour | `process_scheduled_notifications` | Hourly | None |
| Every 6 hours | `update_leaderboards` | Every 6 hours | Badge data should be reasonably current |
| 00:00 UTC daily | `check_subscription_milestones` | Daily | None |
| 02:00 UTC daily | `populate_title_ids` | Daily | None |
| 04:00 UTC daily | `update_shovelware` | Daily | None |
| 16:30 UTC daily | `post_community_trophy_tracker` | Daily (DST-summer) | TokenKeeper sync caught up |
| 17:30 UTC daily | `post_community_trophy_tracker` | Daily (DST-winter) | TokenKeeper sync caught up |
| Weekly (Sunday) | `cleanup_old_analytics --force` | Weekly | None |
| Weekly (Monday 08:00 UTC) | `send_weekly_digest` | Weekly | None |
| 3rd of month, 00:05 UTC | `generate_monthly_recaps --finalize` | Monthly | All profile syncs for the previous month should be complete |
| 3rd of month, 06:00 UTC | `send_monthly_recap_emails` | Monthly | `generate_monthly_recaps` must have completed first |

---

## Job Details

### refresh_profiles

- **Schedule**: Every 30 minutes
- **Command**: `python manage.py refresh_profiles`
- **What it does**: Scans all profiles and queues those whose data is stale for a PSN sync via TokenKeeper. Processes scouts first (per-scout configurable cadence, default 2h, capped at `--max-scouts` per run), then tier-based profiles: premium (6h), basic (12h), Discord-verified (12h), unregistered (7d). The command only *queues* profiles; the actual sync work happens asynchronously in the TokenKeeper worker.
- **Dependencies**: TokenKeeper must be running to process the queued jobs. If TokenKeeper is down, profiles will queue up but not sync.
- **Idempotency**: Fully safe to re-run. Profiles already queued or recently synced are skipped by the threshold check. Double-running causes no harm because `PSNManager.profile_refresh()` deduplicates.
- **Failure impact**: Profiles stop getting updated. Scout discovery and premium users are affected first. The site continues to serve cached data but it becomes increasingly stale.

### refresh_homepage_hourly

- **Schedule**: Every hour (top of the hour)
- **Command**: `python manage.py refresh_homepage_hourly`
- **What it does**: Computes and caches the site heartbeat (the "PlatPursuit at a Glance" / "Built for Hunters" ribbon shown across all four home shells and the dashboard). Single cache key per hour: `site_heartbeat_{YYYY-MM-DD}_{HH}`. See [Homepage Services](../reference/homepage-services.md) for the data shape.
- **Dependencies**: None. Reads directly from the database.
- **Idempotency**: Fully safe to re-run. Overwrites the same cache key with fresh data.
- **Failure impact**: The ribbon falls back to the previous hour's cached data. If two consecutive hours fail, the entire ribbon silently hides itself across every home shell. This is the only homepage data source left that is invisible to dashboard module caching, so monitor it explicitly.

> **Removed: `refresh_homepage_daily`.** This command was retired when the homepage redesign collapsed onto the dashboard. There are no daily-cached "featured games / featured badges / featured checklists" sources anymore; the dashboard's Recent Platinums, Recent Badges, and Top Studios modules took their place. Disable any legacy Render Cron entry pointing at it.

### update_leaderboards

- **Schedule**: Every 6 hours
- **Command**: `python manage.py update_leaderboards`
- **What it does**: Recomputes all badge leaderboards and caches the results with a 7-hour TTL. Covers: per-series earners leaderboard, per-series progress leaderboard, total progress leaderboard, total XP leaderboard, and community series XP totals. Iterates over every live badge series.
- **Dependencies**: Badge data should be reasonably current. No hard ordering dependency, but running after a badge series refresh gives more accurate results.
- **Idempotency**: Fully safe to re-run. Overwrites existing cache keys.
- **Failure impact**: Leaderboard pages show stale data until the cache expires (7h TTL). Individual series failures are caught and logged without blocking other series.

### process_scheduled_notifications

- **Schedule**: Every hour
- **Command**: `python manage.py process_scheduled_notifications`
- **What it does**: Finds all `ScheduledNotification` records with status `pending` and `scheduled_at <= now`, then delivers them to their target audience (all users, premium tiers, Discord-verified, or individual users). Uses `select_for_update(skip_locked=True)` to prevent double-processing.
- **Dependencies**: None. Staff schedule notifications through the admin UI; this command just delivers them when they are due.
- **Idempotency**: Safe to re-run. The `skip_locked` and status transition (`pending` to `processing` to `sent`) prevent double delivery. Already-sent notifications are ignored.
- **Failure impact**: Scheduled announcements are delayed until the next successful run. Failed individual notifications are marked with `failed` status for admin visibility.

### check_subscription_milestones

- **Schedule**: Daily
- **Command**: `python manage.py check_subscription_milestones`
- **What it does**: Evaluates `subscription_months` milestones for every user with an active (open-ended) `SubscriptionPeriod`. Awards milestones at duration thresholds (e.g., 1 month, 3 months, 6 months, 1 year). Only queries profiles whose `SubscriptionPeriod.ended_at` is NULL, so non-subscribers are skipped entirely.
- **Dependencies**: None. Subscription periods are maintained by webhook handlers.
- **Idempotency**: Fully safe to re-run. The milestone service skips already-awarded milestones.
- **Failure impact**: Users receive subscription milestones a day late. No data loss.

### populate_title_ids

- **Schedule**: Daily (recommended)
- **Command**: `python manage.py populate_title_ids`
- **What it does**: Downloads PS4 and PS5 title ID lists from the [PlayStation Titles GitHub repository](https://github.com/andshrew/PlayStation-Titles) and upserts them into the `TitleID` table. This data is used for region detection, concept matching, and platform identification.
- **Dependencies**: Requires network access to GitHub. No internal dependencies.
- **Idempotency**: Fully safe to re-run. Uses `update_or_create` for each record, so duplicate runs produce no side effects.
- **Failure impact**: New games released since the last successful run won't have TitleID entries. This can affect region detection for newly synced games but does not break core functionality.

### ~~match_game_families~~ (removed in Phase 2.6)

**Removed**: the heuristic name/trophy-based family matcher was deleted in
Phase 2.6. `GameFamily` records are now populated automatically by the IGDB
enrichment pipeline keyed on `igdb_id` — see
[Game Family System](../features/game-family.md) for the new flow. No cron
job replaces it; linking happens inline on every match acceptance. A
one-shot backfill (`backfill_game_families_from_igdb`) exists for the
historical pass after Phase 3's rematch run.
- **Failure impact**: New cross-generation game relationships are not automatically discovered. Existing families remain intact.

### update_shovelware

- **Schedule**: Daily (recommended)
- **Command**: `python manage.py update_shovelware`
- **What it does**: Full rebuild of the shovelware detection list. Resets all auto-flagged statuses, scans for games with platinum earn rates above the threshold, updates the publisher blacklist, and applies concept shielding to protect legitimate games from blacklisted publishers. Respects manual locks and flags.
- **Dependencies**: None, but having current earn rate data (from recent syncs) improves accuracy.
- **Idempotency**: Fully safe to re-run. The command resets and rebuilds from scratch each time. Locked and manually flagged games are preserved.
- **Failure impact**: The shovelware list becomes stale. New shovelware games are not excluded from challenge eligibility until the next successful run.

### post_community_trophy_tracker

- **Schedule**: Twice daily at **16:30 UTC** and **17:30 UTC** (both entries by design)
- **Command**: `python manage.py post_community_trophy_tracker`
- **What it does**: Computes the previous ET calendar day's community trophy stats (total trophies, platinums, ultra rares, and a weighted PP Score) for profiles with Discord linked, stores them as a `CommunityTrophyDay` row, and posts a Discord embed via `DISCORD_PLATINUM_WEBHOOK_URL`. Detects new all-time records per stat and tags the embed accordingly. See [Community Trophy Tracker](../features/community-trophy-tracker.md) for the full data flow.
- **Why two crons?**: Render schedules in UTC. 16:30 UTC = 12:30 PM EDT (summer), 17:30 UTC = 12:30 PM EST (winter). Whichever fires first at the right ET time succeeds; the other becomes a no-op via the `posted_at` idempotency gate. **This is intentional, not a duplicate config bug.**
- **Dependencies**: `refresh_profiles` should have completed at least one cycle for each tier after midnight ET so trophies earned in the final hour of the target day are synced. With the 12h Discord-verified cadence and the ~30 min sync buffer before noon ET, all eligible profiles will have synced at least once before this job runs.
- **Idempotency**: Fully safe to re-run. The `CommunityTrophyDay.posted_at` field gates against double-posts. Use `--force-repost` to override (e.g., after editing a row in the admin to fix a bad post). Use `--dry-run` to preview the embed JSON without writing to the DB or posting.
- **Failure impact**: That day's tracker post is skipped. The data is reconstructable any time later by running `python manage.py post_community_trophy_tracker --date=YYYY-MM-DD`. Records are not lost (they're computed from stored rows on every post).

### cleanup_old_analytics

- **Schedule**: Weekly (recommended)
- **Command**: `python manage.py cleanup_old_analytics --force`
- **What it does**: Deletes `AnalyticsSession` records older than 90 days and anonymizes IP addresses in `PageView` records older than 90 days (sets `ip_address` to NULL). The `--force` flag skips the interactive confirmation prompt required for unattended cron execution. PageView records themselves are preserved (view counts remain intact).
- **Dependencies**: None.
- **Idempotency**: Fully safe to re-run. Deleting already-deleted records and nullifying already-null IPs are both no-ops.
- **Failure impact**: Old analytics data accumulates in the database. No user-facing impact, but storage grows and GDPR compliance may be affected if sessions/IPs are retained beyond the 90-day window.

### generate_monthly_recaps

- **Schedule**: 3rd of month at 00:05 UTC
- **Command**: `python manage.py generate_monthly_recaps --finalize`
- **What it does**: Generates monthly recap data for all profiles that had trophy activity in the previous month. The `--finalize` flag marks recaps as immutable after generation, which is a prerequisite for the email command. Defaults to the previous month automatically (so a run on March 3rd generates February recaps).
- **Dependencies**: Profile syncs for the previous month should be complete. Running on the 3rd gives two days of buffer for end-of-month syncs to finish.
- **Idempotency**: Safe to re-run. Uses `get_or_generate_recap()` which returns existing recaps if already generated. The finalize step is also idempotent (already-finalized recaps are skipped).
- **Failure impact**: Recap emails cannot be sent (they require finalized recaps). Users cannot view their monthly recap page until recaps are generated.

### send_weekly_digest

- **Schedule**: Monday at 08:00 UTC
- **Command**: `python manage.py send_weekly_digest`
- **What it does**: Sends the "This Week in PlatPursuit" community newsletter to all linked profiles. Community-focused content: site-wide stats (trophies, platinums, active hunters, reviews, new signups), top 5 most-platted games, review of the week. Condensed personal section: trophy contribution with percentage, challenge progress with weekly deltas, badge updates. Community data is pre-fetched once per batch to avoid redundant queries.
- **Dependencies**: None. Reads trophy, challenge, badge, and review data directly from the database.
- **Idempotency**: Safe to re-run. Uses `EmailLog` deduplication with a 6-day window. Profiles that already received a digest within the past 6 days are skipped. Use `--force` to bypass the dedup check.
- **Failure impact**: Users don't receive their weekly digest. No data loss. Can be retried on Tuesday by re-running the command.
- **Smart suppression**: Only suppressed if the community itself had zero activity (e.g., site downtime). The newsletter is community-focused, so it has value even when an individual user had a quiet week.

### send_monthly_recap_emails

- **Schedule**: 3rd of month at 06:00 UTC
- **Command**: `python manage.py send_monthly_recap_emails`
- **What it does**: Finds all finalized recaps for the previous month that haven't had emails sent yet, and sends personalized HTML emails plus in-app notifications. Respects email opt-out preferences (in-app notifications are sent regardless). Processes in batches of 100 by default.
- **Dependencies**: `generate_monthly_recaps --finalize` **must** have completed successfully. The 6-hour gap between recap generation (00:05) and email sending (06:00) provides ample buffer.
- **Idempotency**: Safe to re-run. Each recap tracks `email_sent` and `notification_sent` booleans, so already-sent recaps are skipped. Use `--force` to intentionally resend.
- **Failure impact**: Users don't receive their monthly recap email or notification. They can still access recaps directly through the website. Failed sends can be retried by re-running the command.

---

## Long-Running Processes

These are not cron jobs but continuously running worker processes that cron jobs depend on.

### TokenKeeper

- **Command**: `python manage.py start_token_keeper`
- **Deployment**: Runs as a separate Render worker service (see `docker-compose.yml` for the local equivalent)
- **What it does**: Manages PSN API tokens and processes the sync job queue. When `refresh_profiles` queues a profile for sync, TokenKeeper picks it up, authenticates with PSN, and syncs trophies, badges, challenges, and gamification data.
- **Failure impact**: All profile syncing stops. The site continues to serve existing data but nothing updates. The `refresh_profiles` cron will keep queuing profiles, which will be processed once TokenKeeper recovers.

---

## Timing Dependencies

The following diagram shows ordering constraints between jobs. Jobs on the same line have no ordering dependency on each other.

```
                    CONTINUOUS
                    ----------
                    TokenKeeper (always running)
                        ^
                        |  (queues jobs for)
                        |
    EVERY 30 MIN ──── refresh_profiles


    HOURLY ─────────── refresh_homepage_hourly
                        process_scheduled_notifications


    EVERY 6 HOURS ──── update_leaderboards


    DAILY ──────────── check_subscription_milestones
                        populate_title_ids
                            |
                            v
                        update_shovelware
                        post_community_trophy_tracker (16:30 + 17:30 UTC, DST safety net)


    WEEKLY ─────────── cleanup_old_analytics --force       [Sunday]
                        send_weekly_digest                  [Monday 08:00 UTC]


    MONTHLY (3rd) ──── generate_monthly_recaps --finalize   [00:05 UTC]
                            |
                            v  (requires finalized recaps)
                        send_monthly_recap_emails            [06:00 UTC]
```

Key ordering rules:

1. `refresh_profiles` depends on TokenKeeper being alive to process queued syncs.
2. `send_monthly_recap_emails` **must** run after `generate_monthly_recaps --finalize`. The 6-hour gap (00:05 to 06:00) on the 3rd of each month ensures this.
3. `send_weekly_digest` runs Monday morning, covering the previous ISO week (Monday to Sunday). The Sunday `cleanup_old_analytics` job has no dependency relationship.
4. All other jobs are independent and can run in any order relative to each other.

---

## Monitoring

### Checking if a job ran

There is no centralized cron job monitoring dashboard. Use these approaches to verify job execution:

- **Render dashboard**: Each cron job shows its last run time and exit status in the Render Cron Jobs panel. Check the job's log output for success/error messages.
- **Redis cache keys**: For cache-warming jobs (`refresh_homepage_hourly`, `refresh_homepage_daily`, `update_leaderboards`), you can verify freshness by checking the cache key timestamps:
  - `python manage.py redis_admin --flush-index` lists current index cache keys (use with caution, this flushes them)
  - Leaderboard keys include a `_refresh_time` companion key (e.g., `lb_total_progress_refresh_time`) storing an ISO timestamp
- **Database records**: For monthly recap jobs, check `MonthlyRecap.email_sent`, `email_sent_at`, `notification_sent`, `notification_sent_at` fields.
- **Django admin**: `ScheduledNotification` records show `status` (`sent`/`failed`/`pending`) and `processed_at` timestamps.

### Detecting failures

- **Missing site heartbeat ribbon**: If the "PlatPursuit at a Glance" ribbon disappears from every home shell, `refresh_homepage_hourly` has been failing for at least two consecutive hours (the partial silently hides when both the current and fallback buckets are empty). Check the Render cron logs.
- **Leaderboard staleness**: Each leaderboard page shows a "Last updated" timestamp sourced from the `_refresh_time` cache key.
- **Sync queue backlog**: If profiles are not updating, check the TokenKeeper stats via `redis_admin` or the token monitoring admin page (`/staff/token-monitoring/`).
- **Missing recap emails**: On the 3rd-4th of each month, spot-check that recap emails were sent by querying `MonthlyRecap.objects.filter(email_sent=False, is_finalized=True)`.
- **Subscription milestones**: If a subscriber reports missing a milestone, run `check_subscription_milestones --dry-run` to verify the job would catch them.

### Manual re-runs

Every cron job can be re-run manually at any time. All jobs are idempotent (safe to double-run). For jobs with `--dry-run` support, always preview first:

```bash
python manage.py <command> --dry-run    # preview
python manage.py <command>              # execute
```

---

## Related Docs

- [Management Commands](management-commands.md): Full reference for all 65+ management commands
- [Token Keeper](../architecture/token-keeper.md): Architecture of the PSN sync worker
- [Monthly Recap](../features/monthly-recap.md): Recap generation and delivery pipeline
- [Notification System](../architecture/notification-system.md): Scheduled notification delivery
- [Badge System](../architecture/badge-system.md): Badge evaluation and leaderboard computation
