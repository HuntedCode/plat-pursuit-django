# Cron Jobs

PlatPursuit uses **Render Cron Jobs** to run scheduled management commands. Each cron job is configured through the Render dashboard (not a config file) and executes a Django management command via `python manage.py <command>`. The TokenKeeper worker process handles real-time PSN sync jobs separately as a long-running daemon; the cron jobs described here cover everything else: profile refresh queuing, cache warming, leaderboard computation, analytics cleanup, and monthly recap delivery.

---

## Schedule Overview

| Time (UTC) | Command | Frequency | Dependencies |
|------------|---------|-----------|--------------|
| Every 30 min | `refresh_profiles` | Every 30 minutes | TokenKeeper must be running to process queued syncs |
| Top of every hour | `refresh_homepage_hourly` | Hourly | None |
| ~~Top of every hour~~ | ~~`process_scheduled_notifications`~~ | **PAUSED (2026-08)** | Notification system hidden |
| 04:00 UTC daily | `nightly` | Daily | TokenKeeper sync caught up. Runs the badge chain: `evaluate_badges --all` -> `detect_dlc_and_refresh` -> `audit_badge_coverage` |
| Every 15 min (only while an event runs) | `process_art_reveals` | Every 15 minutes | None |
| 02:00 UTC daily | `populate_title_ids` | Daily | None |
| 04:00 UTC daily | `update_shovelware` | Daily | None |
| 03:00 UTC daily | `recalc_earn_rates` | Daily | None |
| 03:30 UTC daily | `recalc_profile_counters` | Daily | None |
| 03:45 UTC daily | `recompute_tag_covers` | Daily | None |
| 05:30 UTC daily | `recompute_milestones` | Daily | Profile counters current (`recalc_profile_counters` at 03:30) |
| 16:30 UTC daily | `post_community_trophy_tracker` | Daily (DST-summer) | TokenKeeper sync caught up |
| 17:30 UTC daily | `post_community_trophy_tracker` | Daily (DST-winter) | TokenKeeper sync caught up |
| Weekly (Saturday 09:00 UTC) | `enrich_from_igdb --missing-or-no-match --max-minutes 60` | Weekly | None |
| Weekly (Sunday 07:00 UTC) | `enrich_from_igdb --refresh --max-minutes 90` | Weekly | None |
| Weekly (Monday 08:00 UTC) | `send_weekly_digest` | Weekly | None |
| 3rd of month, 00:05 UTC | `generate_monthly_recaps --finalize` | Monthly | All profile syncs for the previous month should be complete |
| ~~3rd of month, 06:00 UTC~~ | ~~`send_monthly_recap_emails`~~ | **PAUSED (2026-08)** | Monthly recap rebuild in progress. `MONTHLY_RECAP_SEND_ENABLED` defaults to False, so the command no-ops even if the job runs. Stops the in-app notification too (dispatched from inside the email loop). |

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

> ### FOLLOW-UP: fold the remaining nightly commands into `nightly`
>
> **The badge chain is folded into `nightly`; the rest of the nightly work is not, yet.** These six still
> sit on their own Render entries, with ordering expressed as wall-clock spacing:
>
> | time (UTC) | command |
> |---|---|
> | 02:00 | `populate_title_ids` |
> | 03:00 | `recalc_earn_rates` |
> | 03:30 | `recalc_profile_counters` |
> | 03:45 | `recompute_tag_covers` |
> | 04:00 | `update_shovelware` |
> | 05:30 | `recompute_milestones` |
>
> At least one dependency there is already **documented rather than enforced**: `recompute_milestones`
> states it needs "profile counters current" and implements that as a two-hour gap after
> `recalc_profile_counters`. That is exactly the hazard `nightly` was built to remove for the badge chain
> -- a guess that holds until the upstream step outgrows its window, and then the downstream step reads a
> half-written table without failing.
>
> **How to do it:** add entries to `STEPS` in `core/management/commands/nightly.py`, then delete the
> matching Render cron entries. `test_every_step_names_a_real_command` already guards typos; add an
> ordering assertion (in the shape of `test_the_dlc_sweep_runs_AFTER_the_badge_evaluation`) for
> any dependency you newly declare.
>
> This was left out of the change that introduced `nightly` deliberately, to keep the blast radius of a
> leaderboard change to one subsystem.

### nightly

**This is the only nightly cron entry for the badge chain** (see the follow-up above for the six that are
still separate). It runs the badge maintenance steps in DEPENDENCY order and
replaces three separate entries (`evaluate_badges --all`, `detect_dlc_and_refresh`,
`audit_badge_coverage`).

- **Command**: `python manage.py nightly`
- **Order** (dependency, not preference):
  1. `evaluate_badges --all` -- writes the standing tables
  2. `detect_dlc_and_refresh` -- re-evaluates series whose games gained DLC (writes the same tables)
  3. `audit_badge_coverage` -- read-only curator email, least urgent

  (There was a fourth, `recalc_board_entrants`, which counted the standings the first two write. It went
  with the board directories in 2026-08 -- the `BadgeSeries.entrants` / `Job.entrants` columns it
  maintained existed only so those directories could gate and sort across the catalogue, and the
  per-entity Ranks panels each do one scoped, indexed count instead.)
- **Why one entry**: the ordering used to be wall-clock spacing (04:00 and 04:30). Thirty minutes is a
  guess, and `evaluate_badges --all` walks every linked profile -- when it outgrows the gap the two
  overlap, and two processes call `recompute_standing` for the same profiles. That now takes a
  per-profile lock, so a race serializes rather than corrupting -- but two full passes over ~300,000
  profiles serializing is not a thing to leave scheduled.
- **Failure behaviour**: each step is isolated, so one failure does not cancel the rest -- "the DLC sweep
  failed" should not also cost you the coverage email. The command still exits NON-ZERO if any step
  failed, so the run goes red rather than green-with-an-error-in-the-logs.
- **Operator flags**: `--dry-run` lists the order, `--only '<label>'` re-runs one step after a failure
  without repeating the expensive evaluation, `--skip '<label>'` is repeatable.
- **Adding nightly work**: add a step to `STEPS` in `core/management/commands/nightly.py`, NOT a new cron
  entry. A test asserts every step names a real command, and another asserts the DLC sweep stays after
  the evaluation whose tables it rewrites.

### evaluate_badges --all

- **Schedule**: Daily, 04:00 UTC
- **Command**: `python manage.py evaluate_badges --all`
- **What it does**: Re-evaluates every live group badge for every profile and rewrites the standings from
  scratch (`UserGroupBadge`, `SeriesBadgeStanding`, `SeriesEditionStanding`, `ProfileBadgeStanding`,
  `ProfileEditionStanding`).
- **What it costs**: unchanged by the 2026-08 addition of `SeriesEditionStanding` on the CPU side (the
  per-edition figures were already computed in the loop that writes `group_progress`), but it is more
  rows WRITTEN -- one per started edition per engaged series, on top of the one per series. Bounded by
  storing only STARTED editions rather than every earnable one, which is roughly half the map on a
  two-edition series and the difference between a table that tracks engagement and one that tracks the
  catalogue.
- **Why it exists**: sync evaluates only the series a hunter TOUCHED, which is what keeps `sync_complete`
  cheap. That leaves three gaps this pass closes: a badge authored after a hunter's last sync, a stage or
  `PlatformGroup` edited by a curator, and any evaluation that failed and was swallowed by the non-fatal
  wrapper in `_job_sync_complete`. Same shape as `recalc_earn_rates`: incremental in steady state, a
  nightly recompute-from-scratch as the drift-correction net.
- **Dependencies**: TokenKeeper sync caught up, so the completion signals it reads are current.
- **Idempotency**: Fully safe to re-run — the engine is pure and the standings are a full replace.
- **Failure impact**: newly authored badges and curator edits do not reach hunters who did not touch those
  series; already-evaluated standings stay correct. Announcements are NOT sent by this path -- `evaluate_and_apply_batch`
  has no `notify` parameter at all, so silence is structural rather than a default -- and a re-run cannot
  spam Discord.

> **Removed: `update_leaderboards`.** It rebuilt the LEGACY Redis badge leaderboards (per-series earners +
> progress, total progress, total XP, community series XP) every 6 hours. Every board it fed now reads
> indexed Postgres columns written by the badge write seam -- see
> [leaderboard-system](../architecture/leaderboard-system.md). **DELETE the Render Cron entry when the rebuild
> branch deploys.** Not merely disable, and not harmless: the command FILE is deleted, so the entry now
> fails with `Unknown command: update_leaderboards` every 6 hours and will alert.

### detect_dlc_and_refresh

- **Schedule**: Daily (04:30 UTC)
- **Command**: `python manage.py detect_dlc_and_refresh`
- **What it does**: Detects games that gained **new DLC** since the last run -- a new `TrophyGroup` on a game that already existed before the scan window (a brand-new game's groups are all created together with none predating it, so it is ignored). New DLC can drop earners below 100%, so for each affected concept the command re-evaluates the **whole badge series** it belongs to, across every live edition, via `badge_apply.evaluate_and_apply_batch` over every profile that has played a game in the series. Awards and revokes both fall out of that: DLC can newly qualify a hunter as easily as it lapses one. The batch entry point takes no `notify` parameter, so an automated sweep is silent by construction. It **also recomputes every owner's completion %** for the affected games: DLC grows the trophy total, leaving each owner's stored `ProfileGame.progress` (a PSN-reported, grade-weighted value) overstated until they re-sync. The recompute is a bounded DB-side `progress = round(earned_trophies_count / new_total * 100)` UPDATE per game (whale-safe; no per-row iteration). It is a count-based approximation of PSN's grade-weighted %, but **exact at the 100%->below boundary** (the visible "falsely completed" bug) since new DLC trophies are unearned by all -- only the denominator moved; PSN restores the exact value on each owner's next sync. Uses a Redis watermark (`dlc_detection:last_run`); `--since <iso>` overrides it, `--dry-run` reports affected series + games without writing or advancing the watermark.
- **Dependencies**: TokenKeeper sync should be reasonably current (a game's new DLC TrophyGroup is created during sync, which is what this detects).
- **Idempotency**: Safe to re-run. Re-refreshing a series is idempotent (it re-evaluates from current state). If the watermark is lost (Redis flush), it falls back to a 3-day lookback and re-scans -- harmless.
- **Failure impact**: Badge series with new DLC stay un-refreshed until the next run, so a few earners may show a stale (still-earned) badge tier they've technically lapsed. Per-series failures are caught and logged without blocking the others.
- **Lapse behavior**: holds are binary in the current engine -- a revoke DELETES the `UserGroupBadge` row. There is no maintenance state.

### process_art_reveals

- **Schedule**: Every 15 minutes, but only needs to run while a Badge Art Reveal event is live. Safe to leave registered year-round (it no-ops when nothing is active).
- **Command**: `python manage.py process_art_reveals`
- **What it does**: For each live `ArtRevealEvent`, recounts community badge-platinums (since the event's `started_at`, on non-shovelware badge-covered games) via `reconcile_event`, stores the count on `last_platinum_count`, and releases any items whose threshold has been crossed (copying their artwork onto the badge so it goes live). See [Badge Art Reveal](../features/badge-art-reveal.md).
- **Dependencies**: None. Reads directly from the database. Sync being caught up makes the count fresher but isn't required.
- **Idempotency**: Fully safe to re-run. It reconciles the released set to the current count each run (forward-only), and the event row is locked so overlapping runs can't double-release. A missed run self-heals on the next.
- **Failure impact**: Artwork reveals lag behind the true community count until the next successful run; the banner/page show the last stored count. No data loss.

### process_scheduled_notifications — PAUSED (2026-08)

> Paused on Render while the notification system is [hidden pending rebuild](../architecture/notification-system.md). This was the only outbound delivery path still live, and with the staff compose UI unrouted nothing new can be scheduled anyway — but the job would keep delivering rows already queued. The command and its schedule are otherwise unchanged; un-pausing is a dashboard toggle.

- **Schedule**: Every hour
- **Command**: `python manage.py process_scheduled_notifications`
- **What it does**: Finds all `ScheduledNotification` records with status `pending` and `scheduled_at <= now`, then delivers them to their target audience (all users, premium tiers, Discord-verified, or individual users). Uses `select_for_update(skip_locked=True)` to prevent double-processing.
- **Dependencies**: None. Staff schedule notifications through the admin UI; this command just delivers them when they are due.
- **Idempotency**: Safe to re-run. The `skip_locked` and status transition (`pending` to `processing` to `sent`) prevent double delivery. Already-sent notifications are ignored.
- **Failure impact**: Scheduled announcements are delayed until the next successful run. Failed individual notifications are marked with `failed` status for admin visibility.

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

### recalc_earn_rates

- **Schedule**: Daily, 03:00 UTC
- **Command**: `python manage.py recalc_earn_rates`
- **What it does**: Recomputes `Game.played_count`, the game's denormalized community stats (`plats_earned_count`, `full_completion_count`, `avg_completion`, `monthly_players_count`, `monthly_earners_count` — all from the same single ProfileGame `GROUP BY`, so they share one population/denominator with `played_count`; the last two look alike but are NOT interchangeable, `monthly_players_count` counting owners who LAUNCHED the game in 30 days and `monthly_earners_count` those who EARNED A TROPHY in it, which is the signal the Browse Games Trending sort orders by), `Game.total_earns_count` (summed from the per-trophy earned counts already in memory, so it costs no extra query), and `Trophy.earned_count` / `Trophy.earn_rate` site-wide using bulk `GROUP BY` aggregates. Processes games in chunks (default 200/chunk); Trophy rows are `bulk_update`d only when a value changed, while Game rows are refreshed every run (the seven Game stats are rewritten each pass). Hard caps wall time via `--max-minutes` (default 30) so the cron can't run away.
- **Why it exists**: These global aggregates used to be recomputed inline on every per-profile `sync_complete` job (in `psn_api_service.update_profilegame_stats`). Under concurrent sync completions that pattern fanned out into many simultaneous full-table aggregation queries, pegging DB CPU and starving the web service of capacity until containers OOM'd (May 2026 incident). Decoupling this into a single daily reconcile run was the structural fix.
- **Dependencies**: None. Read-heavy; ideally runs in a low-traffic window.
- **Idempotency**: Fully safe to re-run. Computes deltas and skips rows whose values already match. `--dry-run` reports counts without writing.
- **Failure impact**: `Trophy.earn_rate` and `Game.played_count` get up to 24 hours stale from the daily run alone. With the incremental signal updates layered on top (see [signal-driven counter updates](../architecture/data-model.md)), values stay live in steady state and the cron acts purely as a drift-correction safety net. Rerun manually if a one-day gap is unacceptable.
- **Resume cursor**: a run that exhausts `--max-minutes` stores the last fully-processed `Game.id` in `recalc_earn_rates:cursor` (see [redis-keys](../reference/redis-keys.md)); the next run resumes just past it and wraps around. Without this, a job that always hits its budget would restart at id 0 every night and never reach the tail of the catalogue. A completed pass clears the cursor; `--game-ids` and `--dry-run` leave it untouched.
- **Statement timeout**: the command raises `statement_timeout` on its own connection, so it behaves identically whichever service's shell launches it — the web service deliberately runs a 15s ceiling (`DB_STATEMENT_TIMEOUT_MS`), which would kill these chunk aggregates.
- **Gotcha — this cron is now on the render path.** The game-detail community stats row reads these six columns directly (zero queries, no cache). If this job stops running, that row silently freezes rather than erroring; if the columns were never backfilled after the migration that added them, it reads `0`. Any newly added stat on that row must be denormalized here rather than computed in the view: they used to be live per-request aggregates, and `total_earns` in particular counted EarnedTrophy across every trophy in the game, which could outlast the gunicorn worker timeout on a popular title. This is also why the resume cursor matters: an unreached game now serves `0` rather than merely stale data.

### recompute_tag_covers

- **Schedule**: Daily, 03:45 UTC
- **Command**: `python manage.py recompute_tag_covers`
- **What it does**: Materializes browse read-models across four grouping models — `Genre`, `Theme`, `Franchise`, and `Company`. (1) **`representative_game`** (all four) — the grouping's browse-tile + detail-hero cover: a CONTRACT game (curated Job-Board entry, so tiles favour recognizable titles) with a STABLE per-grouping variety shuffle (a hash of grouping+game id, so adjacent tiles differ but a tile never reshuffles between page loads), bounded to the most-recent `POOL_CAP` (50) contract members; falls back to the most-recent member with real cover art, then any most-recent member. The **franchise** cover pick additionally honors the `ConceptFranchise.is_excluded` / `is_spinoff` link flags (curated exclusions + spin-offs never provide the art); companies have no such flags. (2) **`related_tags`** (Genre/Theme only) — the detail-page rail: the top-`RELATED_N` (6) OTHER same-type tags ranked by game co-occurrence (genres/themes whose games overlap this one's the most), stored as an ordered slug list. `bulk_update`s only the rows whose picks changed. All four models are driven off a shared `CONFIGS` list (one config entry per model).
- **Why it exists**: All were originally per-request queries whose cost grows with the catalogue / contract-catalogue size (a correlated cover subquery per tile; a co-occurrence GROUP BY over a tag's games per detail load). Materializing them off the request path makes the pages read them O(1), so they scale regardless of how large contracts or franchises get. Slow-changing derived data → a textbook denorm/read-model (same pattern as `recalc_earn_rates`' community stats).
- **Dependencies**: None. Uses an indexed `EXISTS` on `Contract.igdb_id`; read-heavy over a bounded taxonomy (~20 genres / ~40 themes) plus the franchise/collection + company sets.
- **Idempotency**: Fully safe to re-run — recomputes from scratch and the pick is stable (a re-run does not reshuffle). `--dry-run` reports how many covers would change without writing.
- **Failure impact**: Tile covers get up to 24 hours stale (they shift only as new games sync into a grouping). A brand-new genre/theme/franchise with no materialized pick yet renders the neutral glyph placeholder until the next run — never an error.

### recalc_profile_counters

- **Schedule**: Daily, 03:30 UTC (after `recalc_earn_rates`)
- **Command**: `python manage.py recalc_profile_counters`
- **What it does**: Reconciles the four denormalized per-profile type counters (`Profile.total_bronzes`, `total_silvers`, `total_golds`, `total_plats`) against EarnedTrophy ground truth. Uses bulk `GROUP BY` aggregates with chunked processing (default 200 profiles/chunk) and a wall-clock budget (default 30 min).
- **Why it exists**: Those four counters are now maintained incrementally via `EarnedTrophy` post-save / post-delete signals (see `trophies/signals.py`). The signal path catches the common case but does not catch updates that bypass it: `bulk_create` / `bulk_update` / `queryset.update()` on `EarnedTrophy`, or signal handler exceptions. This cron is the drift-correction safety net so users never see counters drifted up or down for long.
- **Note**: `Profile.total_trophies`, `total_unearned`, and `avg_progress` are NOT recomputed here — they're filter-respecting (hide_hiddens / hide_zeros) and are recomputed on demand via `update_profile_trophy_counts()` from sync_complete and the profile settings POST.
- **Dependencies**: None. Read-heavy; off-peak window.
- **Idempotency**: Fully safe to re-run. Computes deltas and skips rows whose values already match. `--dry-run` reports counts without writing.
- **Failure impact**: Type counters drift up to 24h until the next run if signals miss something. Users with active trophy hunting could see slightly off bronze/silver/gold/plat counts during that window. No user-facing breakage.

### audit_badge_coverage

- **Schedule**: Daily, 05:00 UTC
- **Command**: `python manage.py audit_badge_coverage` (add `--always` for a daily heartbeat email even when there are no gaps)
- **What it does**: For each tier-1 badge that tracks a franchise and/or developer, checks that every non-excluded franchise-linked concept / developed game is covered by one of the badge's series stages. Emails any gaps to `badge-alerts@platpursuit.com`. A gap usually means a new game shipped and needs adding to the badge (or a data error). See [Management Commands](management-commands.md). Logic lives in `trophies/services/badge_coverage_service.py`.
- **Dependencies**: None. Read-only. More accurate after IGDB enrichment (franchise/developer + concept links) is current.
- **Idempotency**: Fully safe to re-run; pure read + email. By default sends mail only when gaps exist.
- **Failure impact**: Staff miss a day of "new game not in its badge" alerts; no data effect. Re-running catches up.

### recompute_milestones

- **Schedule**: Daily, 05:30 UTC
- **Command**: `python manage.py recompute_milestones`
- **What it does**: Sweeps every community-member profile (a site account OR a verified Discord link — `milestones.services.member_q`; scouts / unregistered syncs excluded), recomputing each active milestone ladder (platinums, trophies, completions, badges, Pursuer level, playtime, tenure, premium), awarding any newly-crossed tiers and writing the materialized progress read-model. Then drift-corrects every tier's `earned_count` and refreshes the cached rarity denominator (`total_hunters`). Milestones are also recomputed per-profile at the end of each PSN sync (`token_keeper` `sync_complete`); this daily sweep is the safety-net + the **only** refresh of the rarity denominator. Logic in `milestones/services.py`; see [milestones-revamp](../design/milestones-revamp.md).
- **Dependencies**: Denormalized profile counters current — schedule after `recalc_profile_counters` (03:30). Whale-safe: one bounded aggregate per distinct metric per profile; profiles are streamed with `.iterator()`.
- **Discord**: Runs WITHOUT `--reconcile-discord` (roles are kept current by the per-sync + on-link reconcile). Add `--reconcile-discord` for a periodic role safety-net (heavier — one bot call per linked profile with a role delta). **After (re)configuring a milestone's Discord `discord_role_id` on an already-earned tier, or retiring a milestone, run a one-off `python manage.py recompute_milestones --reconcile-discord`** — the per-sync path only reconciles on a *newly crossed* rung, so a config change to existing holders won't propagate on its own.
- **Idempotency**: Fully safe to re-run; already-earned rungs are never re-awarded. `--reset` (optionally `--milestone <slug>`) wipes + re-derives a ladder against changed thresholds.
- **Failure impact**: Milestone pages show yesterday's progress + a stale/absent rarity denominator until the next run; per-sync recompute still updates any actively-syncing hunter. Re-running catches up.

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

### enrich_from_igdb (weekly retry)

- **Schedule**: Weekly, Saturday 09:00 UTC
- **Command**: `python manage.py enrich_from_igdb --missing-or-no-match --max-minutes 60`
- **What it does**: Re-runs the IGDB matching pipeline against the union of (a) concepts that have no IGDBMatch row at all and (b) concepts whose IGDBMatch row is `status='no_match'`. Oldest first by `IGDBMatch.last_synced_at` (NULLS FIRST), so concepts that were never attempted process before stale `no_match` rows. The `--max-minutes 60` cap exits the loop cleanly after 60 minutes regardless of remaining backlog, keeping Render billing predictable. New games not yet in IGDB on first sync get re-attempted on every weekly run until they appear and match. See [IGDB Integration](../architecture/igdb-integration.md#management-commands) for the full flag inventory.
- **Dependencies**: None. Pure IGDB work, no PSN dependency. Shares the distributed Redis rate limiter (3 req/sec) with any inline sync-time enrichment running concurrently.
- **Idempotency**: Fully safe to re-run. Successful matches are written via `process_match`; failed matches go through `record_no_match`, which refuses to overwrite any non-`no_match` status. Re-running mid-week (e.g., manually via web shell) only refreshes `last_synced_at` on already-tried concepts.
- **Failure impact**: The `no_match` backlog grows. No user-facing impact until the backlog gets stale enough that recently-released games take longer to enrich after their IGDB entry appears. A skipped week can be recovered by manually running the same command (no `--max-minutes` if you want to drain the full queue).

### enrich_from_igdb (weekly refresh)

- **Schedule**: Weekly, Sunday 07:00 UTC
- **Command**: `python manage.py enrich_from_igdb --refresh --max-minutes 90`
- **What it does**: Re-fetches IGDB data for already-accepted matches by `igdb_id` (no re-matching, just data refresh). Picks up new IGDB additions to existing rows: filled-in release-date status info, summary edits, new franchise/collection members, fresh time-to-beat aggregates, etc. Groups matches by `igdb_id` and fetches IGDB data once per group, then applies it to every IGDBMatch row sharing that id. PSN regional variants and PS3/PS4 separate-concept entries that point to the same IGDB game stay in lockstep (same data, same `last_synced_at`). Groups are ordered by the group's oldest match's `last_synced_at` ASC NULLS FIRST, so the staleest groups refresh first. The `--max-minutes 90` cap means a single run hits roughly 7,000-10,000 matches (IGDB rate limit allows ~120 group fetches/min); consecutive weekly runs naturally roll through the entire catalog. After this finishes, the per-platform release-date field, time-to-beat numbers, and external URLs all reflect IGDB's current state. Summary line reports "API calls saved" — the number of matches refreshed beyond the count of unique IGDB ids fetched.
- **Dependencies**: None. Pure IGDB work, no PSN dependency. Shares the distributed Redis rate limiter (3 req/sec) with the Saturday retry job — running them on different days avoids contention.
- **Idempotency**: Fully safe to re-run. `IGDBService.refresh_match` writes the same fields the original enrichment wrote; calling it twice in a row produces the same data. The oldest-group-first ordering means a manually-triggered mid-week run pushes the next week's automatic run forward by however many groups it processed (no double work).
- **Failure impact**: IGDB-side data slowly ages. No user-facing impact for several weeks (IGDB metadata changes slowly), but eventually new franchise relationships, time-to-beat updates, and release-date status data won't surface in PlatPursuit. A skipped week is recovered automatically on the next run since the queue rolls forward.
- **One-time backfill**: After deploying changes that broaden the IGDB query (new fields requested), run `enrich_from_igdb --refresh` **without** `--max-minutes` from the web shell to drain the entire catalog in one pass (1-2 hours typical). The weekly cron then keeps things fresh from there.

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




    DAILY ──────────── populate_title_ids
                            |
                            v
                        update_shovelware
                        post_community_trophy_tracker (16:30 + 17:30 UTC, DST safety net)


    WEEKLY ─────────── enrich_from_igdb --missing-or-no-match --max-minutes 60  [Saturday 09:00 UTC]
                        enrich_from_igdb --refresh --max-minutes 90   [Sunday 07:00 UTC]
                        send_weekly_digest                  [Monday 08:00 UTC]


    MONTHLY (3rd) ──── generate_monthly_recaps --finalize   [00:05 UTC]
                            |
                            v  (requires finalized recaps)
                        send_monthly_recap_emails            [06:00 UTC]
```

Key ordering rules:

1. `refresh_profiles` depends on TokenKeeper being alive to process queued syncs.
2. `send_monthly_recap_emails` **must** run after `generate_monthly_recaps --finalize`. The 6-hour gap (00:05 to 06:00) on the 3rd of each month ensures this.
3. `send_weekly_digest` runs Monday morning, covering the previous ISO week (Monday to Sunday).
4. All other jobs are independent and can run in any order relative to each other.

---

## Monitoring

### Checking if a job ran

There is no centralized cron job monitoring dashboard. Use these approaches to verify job execution:

- **Render dashboard**: Each cron job shows its last run time and exit status in the Render Cron Jobs panel. Check the job's log output for success/error messages.
- **Redis cache keys**: For cache-warming jobs (`refresh_homepage_hourly`), you can verify freshness by checking the cache key timestamps:
  - `python manage.py redis_admin --flush-index` lists current index cache keys (use with caution, this flushes them)
  - Leaderboard keys include a `_refresh_time` companion key (e.g., `lb_total_progress_refresh_time`) storing an ISO timestamp
- **Database records**: For monthly recap jobs, check `MonthlyRecap.email_sent`, `email_sent_at`, `notification_sent`, `notification_sent_at` fields.
- **Django admin**: `ScheduledNotification` records show `status` (`sent`/`failed`/`pending`) and `processed_at` timestamps.

### Detecting failures

- **Missing site heartbeat ribbon**: If the "PlatPursuit at a Glance" ribbon disappears from every home shell, `refresh_homepage_hourly` has been failing for at least two consecutive hours (the partial silently hides when both the current and fallback buckets are empty). Check the Render cron logs.
- **Leaderboard staleness**: Each leaderboard page shows a "Last updated" timestamp sourced from the `_refresh_time` cache key.
- **Sync queue backlog**: If profiles are not updating, check the TokenKeeper stats via `redis_admin` or the token monitoring admin page (`/staff/token-monitoring/`).
- **Missing recap emails**: On the 3rd-4th of each month, spot-check that recap emails were sent by querying `MonthlyRecap.objects.filter(email_sent=False, is_finalized=True)`.
- **Premium tenure milestones**: Now the `premium_months` ladder in the milestones app — if a subscriber reports a missing tier, run `recompute_milestones --profile <psn_username>`.

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
