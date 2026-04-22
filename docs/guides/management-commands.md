# Management Commands

PlatPursuit has **69 custom management commands** spread across 5 Django apps: `trophies` (42), `core` (19), `notifications` (5), `users` (2), and `fundraiser` (1). All commands follow the standard Django pattern and are invoked with `python manage.py <command_name>`. Many support `--dry-run` for safe previewing before applying changes.

---

## Commands by App

### trophies

| Command | Purpose | Key Flags | Typical Usage |
|---------|---------|-----------|---------------|
| `start_token_keeper` | Starts the TokenKeeper singleton process for managing PSN API tokens and job queues. Long-running daemon. | (none) | `python manage.py start_token_keeper` |
| `token_keeper_control` | Control TokenKeeper lifecycle: start, stop, or restart. | `--start`, `--stop`, `--restart` (mutually exclusive, required) | `python manage.py token_keeper_control --restart` |
| `populate_profile_plats` | Recalculate platinum counts for all profiles by calling `update_plats()` on each. | (none) | `python manage.py populate_profile_plats` |
| `update_badge_requirements` | Update `required` and `most_recent_concept` fields on all Badge records. | (none) | `python manage.py update_badge_requirements` |
| `populate_profilegame_stats` | Recalculate earned/unearned trophy counts, `has_plat`, and most recent trophy date for a single profile's ProfileGame records. | `--username` (required), `--batch_size` (default: 100) | `python manage.py populate_profilegame_stats --username Jlowe` |
| `check_profile_badge_series` | Check and award badges for a single profile within a specific badge series. | `--username` (required), `--series` (required) | `python manage.py check_profile_badge_series --username Jlowe --series trophy-hunter` |
| `populate_badges` | Run initial badge check for one or all profiles. Evaluates and awards all applicable badges. | `--username`, `--notify` | `python manage.py populate_badges` |
| `populate_region_asian_titles` | Detect and flag Asian-language games without concepts, setting `is_regional` and assigning region codes. | (none) | `python manage.py populate_region_asian_titles` |
| `populate_user_titles` | Backfill UserTitle records for existing badge and milestone awards. | `--dry-run`, `--badges-only`, `--milestones-only` | `python manage.py populate_user_titles --dry-run` |
| `populate_banned_words` | Seed the BannedWord table with a default list of inappropriate words and spam indicators. | `--clear`, `--dry-run` | `python manage.py populate_banned_words --dry-run` |
| `refresh_badge_series` | Re-evaluate a specific badge series for all eligible profiles and process consolidated badge notifications. | `--series` (required) | `python manage.py refresh_badge_series --series trophy-hunter` |
| `lock_admin_concepts` | Lock `concept_lock` on games whose concept has an admin-duplicate suffix (`-1`, `-2`, etc.) or `PP_` stub prefix. | `--dry-run` | `python manage.py lock_admin_concepts --dry-run` |
| `backfill_default_concepts` | Create stub `PP_` Concepts for games that don't have one. | `--dry-run`, `--batch-size` (default: 50) | `python manage.py backfill_default_concepts --dry-run` |
| `populate_user_milestones` | Check and award milestones for one or all users. Supports filtering by criteria type. | `--type`, `--username`, `--silent` | `python manage.py populate_user_milestones --username Jlowe --type plat_count` |
| `check_subscription_milestones` | Check `subscription_months` milestones for users with active subscription periods. Only processes subscribers. | `--silent`, `--dry-run` | `python manage.py check_subscription_milestones` |
| `audit_user_awards` | Audit UserTitle and UserMilestone records, removing unearned ones. Three phases: orphaned badge titles, orphaned milestone titles, and milestone re-validation via handlers. Dry-run by default. | `--commit`, `--username`, `--include-premium`, `--type` (`titles`/`milestones`/`all`) | `python manage.py audit_user_awards --commit` |
| `sync_all_discord_roles` | Bulk sync Discord roles (badge, milestone, premium) for all verified users. | `--dry-run`, `--profile`, `--batch-size` (default: 100) | `python manage.py sync_all_discord_roles --dry-run` |
| `backfill_stub_concept_icons` | Copy `title_icon_url` from associated games to `PP_` stub Concepts missing icons. | `--dry-run`, `--batch-size` (default: 100) | `python manage.py backfill_stub_concept_icons` |
| `backfill_platted_subgenre_count` | Fix `platted_subgenre_count` on genre challenges and revoke incorrectly awarded `subgenre_progress` milestones. | `--dry-run` | `python manage.py backfill_platted_subgenre_count --dry-run` |
| `recalculate_gamification` | Recalculate gamification stats (badge XP, tiers, series XP) for one or all profiles. | `--profile`, `--dry-run` | `python manage.py recalculate_gamification --profile Jlowe` |
| `clean_titles` | Strip TM/registered symbols, normalize Unicode Roman numerals, and remove "trophy set" suffixes from Game, Concept, Trophy, and GameFamily titles. | `--dry-run` | `python manage.py clean_titles --dry-run` |
| `test_psn` | Test PSN API v3.0.0 connection and fetch sample trophy data. Developer debugging tool with many commented-out endpoint tests. | `psn_username` (positional, default: `abu_abu`) | `python manage.py test_psn myusername` |
| `backfill_game_regions` | Populate `Game.region` from TitleID region data (loaded by `populate_title_ids`). | `--dry-run`, `--verbose` | `python manage.py backfill_game_regions --dry-run --verbose` |
| `enforce_az_challenge_rules` | Remove non-completed A-Z challenge slot assignments where the game is now excluded by anti-stack rules. | `--dry-run` | `python manage.py enforce_az_challenge_rules --dry-run` |
| `check_all_badges` | Full badge recheck for all profiles (or a single user). Reports awarded and revoked badges with before/after diffing. | `--username`, `--dry-run` | `python manage.py check_all_badges --dry-run` |
| `backfill_concept_slugs` | Generate URL slugs for Concepts that don't have one. Handles collisions with counter suffixes. | `--dry-run`, `--batch-size` (default: 100) | `python manage.py backfill_concept_slugs` |
| `populate_milestones` | Create/update milestone definitions and associated Title objects from the hardcoded definitions list. Idempotent: safe to re-run. | `--dry-run` | `python manage.py populate_milestones` |
| `grant_milestone` | Manually grant a milestone (with all side effects: UserTitle, Discord role, notification) to one or more users. | `milestone` (positional, required), `--username`, `--usernames` (comma-separated), `--dry-run`, `--silent` | `python manage.py grant_milestone "Platinum Race Winner" --username Jlowe` |
| `redis_admin` | Swiss-army knife for Redis operations: flush caches, manage TokenKeeper queues, adjust bulk thresholds, migrate whale jobs. | `--flushall`, `--flush-index`, `--flush-game-page <np_id>`, `--flush-token-keeper`, `--flush-complete-lock <profile_id>`, `--flush-dashboard <profile_id>`, `--flush-concept <concept_id>`, `--flush-community`, `--get-bulk-threshold`, `--set-bulk-threshold <n>`, `--move-whale-jobs` (all mutually exclusive) | `python manage.py redis_admin --flush-index` |
| `backfill_concept_trophy_groups` | Create ConceptTrophyGroup records from game-level TrophyGroups. Also includes mismatch detection and audit modes. | `--dry-run`, `--check-mismatches`, `--collections-only`, `--audit-missing-trophies`, `--audit-missing-groups` | `python manage.py backfill_concept_trophy_groups --check-mismatches` |
| `audit_calendar` | Audit Calendar Challenge state against actual platinum data, surfacing day cells whose `filled` flag has drifted from the underlying earned trophies. | `--dry-run`, `--username` | `python manage.py audit_calendar --dry-run` |
| `backfill_stage_completions` | Backfill historical `StageCompletionEvent` rows for users whose stage completions predate the event-tracking system. | `--dry-run`, `--username` | `python manage.py backfill_stage_completions --dry-run` |
| `enrich_from_igdb` | Run the IGDB enrichment pipeline against concepts (developer/publisher, genres/themes, time-to-beat, engine, VR detection). Supports targeted, refresh, retry, search, manual-assign, review queue, and unmatched-queue modes. See [IGDB Integration](../architecture/igdb-integration.md). | `--concept-id`, `--refresh`, `--retry-no-match`, `--search`, `--manual`, `--review`, `--unmatched`, `--badge`, `--all`, `--force`, `--verbose`, `--dry-run` | `python manage.py enrich_from_igdb --review` |
| `rematch_auto_accepted` | Re-run the IGDB matching pipeline against every `auto_accepted` match. Clear upgrades (different id, above auto-accept, beats stored confidence) are applied directly; everything else becomes a `RematchSuggestion` for admin review. See [IGDB Integration](../architecture/igdb-integration.md#phase-3-rematch-sweep). | `--dry-run`, `--concept-id`, `--limit N`, `--verbose` | `python manage.py rematch_auto_accepted --dry-run` |
| `rebuild_concept_enrichment` | Wipe stale ConceptCompany/Genre/Theme/Engine/Franchise rows for every accepted IGDB match and re-apply enrichment from the stored `raw_response`. Clears the backlog of doubled-up data left behind by concept-match reassignments. No IGDB API calls. | `--dry-run`, `--concept-id`, `--limit N`, `--verbose` | `python manage.py rebuild_concept_enrichment --dry-run` |
| `rebuild_franchises_from_cache` | Rebuild `Franchise` + `ConceptFranchise` rows from cached `IGDBMatch.raw_response` without hitting the IGDB API. Use after enrichment-logic changes or to recover from corrupted data. | `--wipe` (drop existing rows first), `--dry-run` | `python manage.py rebuild_franchises_from_cache --wipe` |
| `backfill_franchise_main_flag` | Recompute `ConceptFranchise.is_main` from cached raw_response using the current precedence rules. Narrower than a full rebuild — only updates the flag. | `--dry-run`, `--batch-size` (default 500) | `python manage.py backfill_franchise_main_flag --dry-run` |
| `franchise_stats` | Read-only diagnostic reporting franchise/collection totals, per-concept coverage, browse-page surfacing counts, and sample names. Useful for auditing enrichment coverage and deciding whether the collection-orphan rule is producing sensible results. | `--samples N` (default 10, 0 to skip names) | `python manage.py franchise_stats --samples 20` |
| `inspect_franchise_data` | Read-only diagnostic: compare raw IGDB response to stored links for a concept or franchise. First stop when investigating mis-linked games. Shows drift detection (what's in IGDB but not the DB, or vice versa). | `--search`, `--concept-id`, `--franchise-name` (one required) | `python manage.py inspect_franchise_data --search "College Football"` |
| `recalculate_calendars` | Recalculate Calendar Challenge fill state and platinum counts for all users, repairing drift between cached counts and the underlying data. | `--dry-run`, `--username` | `python manage.py recalculate_calendars --dry-run` |
| `render_profile_sigs` | Pre-render forum-signature PNG and SVG variants of the profile card image. Used as a one-time backfill and as a periodic refresh after design changes. | `--username`, `--all` | `python manage.py render_profile_sigs --all` |
| `trigger_concept_health_checks` | Queue `sync_title_stats` jobs for PS4/PS5 games whose concept is missing or stuck on a stub. Identifies users by `psn_username`, not `user.username`. | `--dry-run`, `--limit` | `python manage.py trigger_concept_health_checks --dry-run` |

### core

| Command | Purpose | Key Flags | Typical Usage |
|---------|---------|-----------|---------------|
| `refresh_profiles` | Queue profiles for PSN sync based on tier and last update time. Premium every 6h, basic every 12h, Discord-verified every 24h, unregistered every 7d. | `--premium-hours` (default: 6), `--basic-hours` (default: 12), `--discord-hours` (default: 24), `--unreg-days` (default: 7) | `python manage.py refresh_profiles` |
| `recalc_earn_rates` | Recalculate `played_count` on Games and `earned_count`/`earn_rate` on Trophies from source data. | `--dry-run`, `--batch-size` (default: 1000) | `python manage.py recalc_earn_rates --dry-run` |
| `recalculate_profile_counts` | Recalculate trophy counts for all profiles using `update_profile_trophy_counts()`. | (none) | `python manage.py recalculate_profile_counts` |
| `process_scheduled_notifications` | Process pending scheduled notifications that are due for delivery. | `--dry-run` | `python manage.py process_scheduled_notifications` |
| `generate_monthly_recaps` | Generate monthly recap data for active profiles. Defaults to previous month. | `--dry-run`, `--finalize`, `--profile-id`, `--year`, `--month`, `--current-month` | `python manage.py generate_monthly_recaps --finalize` |
| `send_monthly_recap_emails` | Send monthly recap emails and in-app notifications to users with finalized recaps. Respects email opt-out preferences. | `--dry-run`, `--year`, `--month`, `--profile-id`, `--force`, `--batch-size` (default: 100) | `python manage.py send_monthly_recap_emails --dry-run` |
| `mark_recaps_sent` | One-time fix: mark all existing recaps as `email_sent` and `notification_sent` to prevent stale sends. | `--dry-run` | `python manage.py mark_recaps_sent` |
| `cleanup_old_analytics` | Delete old AnalyticsSession records and anonymize IP addresses from PageView records for GDPR compliance. | `--dry-run`, `--days` (default: 90), `--force` | `python manage.py cleanup_old_analytics --force` |
| `refresh_homepage_hourly` | Compute and cache the site heartbeat ribbon data ("PlatPursuit at a Glance"). Single cache key per hour. See [Homepage Services](../reference/homepage-services.md). | (none) | `python manage.py refresh_homepage_hourly` |
| `populate_title_ids` | Populate TitleID table from external PlayStation Titles GitHub repository (PS4 + PS5 TSV files). | (none) | `python manage.py populate_title_ids` |
| `backfill_game_families_from_igdb` | Populate `GameFamily` records from accepted `IGDBMatch` rows, keyed on `igdb_id`. One-shot historical pass; live enrichment hooks handle new matches. | `--dry-run` | `python manage.py backfill_game_families_from_igdb --dry-run` |
| `backfill_guide_view_counts` | Reconcile `Checklist.view_count` from actual PageView records after the `page_type` rename from `checklist` to `guide`. | `--dry-run` | `python manage.py backfill_guide_view_counts` |
| `send_weekly_digest` | Send "This Week in PlatPursuit" community newsletter with site-wide stats, top platted games, review of the week, and condensed personal stats. Community data fetched once per batch. Only suppressed if the community had zero activity. | `--dry-run`, `--profile-id`, `--force`, `--batch-size` (default: 100) | `python manage.py send_weekly_digest --dry-run` |
| `test_email_system` | Send test emails for any template to verify email delivery. Supports 17+ email template previews. | `recipient_email` (positional, required), `--recap-preview`, `--verification-preview`, `--password-reset-preview`, `--payment-failed-preview`, `--payment-failed-final-preview`, `--cancelled-preview`, `--welcome-preview`, `--payment-succeeded-preview`, `--payment-action-required-preview`, `--donation-receipt-preview`, `--badge-claim-preview`, `--artwork-complete-preview`, `--badge-earned-preview`, `--milestone-preview`, `--free-welcome-preview`, `--broadcast-preview`, `--weekly-digest-preview` | `python manage.py test_email_system your@email.com --recap-preview` |
| `update_leaderboards` | Recompute and cache all badge leaderboards: per-series earners, per-series progress, total progress, total XP, country XP, and community series XP. | `--series <slug>`, `--country <CC>` | `python manage.py update_leaderboards` |
| `lock_shovelware` | Lock or unlock a game's shovelware status. Propagates to all games sharing the same concept. | `np_communication_id` (positional, required), `--flag`, `--clear`, `--unlock` (mutually exclusive, required) | `python manage.py lock_shovelware NPWR12345_00 --flag` |
| `update_shovelware` | Surgical shovelware reconciliation. Walks a targeted candidate set and applies `evaluate_concept` idempotently, only writing where state has drifted. Preserves `shovelware_updated_at` on unchanged games. | `--verbose` | `python manage.py update_shovelware` |
| `backfill_shovelware` | One-shot wipe + rebuild of shovelware state using the IGDB developer algorithm. Use after schema migrations or major data corrections. | `--dry-run`, `--verbose` | `python manage.py backfill_shovelware --dry-run --verbose` |
| `audit_genre_data` | Report genre and subgenre coverage stats, unique values with counts, and genre-to-subgenre relationships. Filters to challenge-eligible concepts by default. | `--all` | `python manage.py audit_genre_data` |
| `audit_profile_gamification` | Compare stored ProfileGamification XP values against recalculated totals. Finds and optionally fixes discrepancies. | `--fix`, `--profile`, `--verbose` | `python manage.py audit_profile_gamification --fix --verbose` |

### notifications

| Command | Purpose | Key Flags | Typical Usage |
|---------|---------|-----------|---------------|
| `debug_signals` | Debug signal connection issues by inspecting registered `post_save` receivers for `EarnedTrophy`. | (none) | `python manage.py debug_signals` |
| `test_signals` | Verify that the platinum notification signal handler is properly connected and the template exists. | (none) | `python manage.py test_signals` |
| `create_test_notification` | Create a test notification with enhanced metadata for inbox testing. Supports platinum and challenge types. | `--username`, `--type` (`platinum`/`challenge`, default: `platinum`) | `python manage.py create_test_notification --type challenge` |
| `test_platinum_signal` | Create a test platinum trophy earning to trigger the signal. Modifies the database by creating/updating an EarnedTrophy record. | (none) | `python manage.py test_platinum_signal` |
| `force_platinum_notification` | Bypass normal signal flow and directly call the platinum notification handler using an existing earned platinum. | (none) | `python manage.py force_platinum_notification` |

### users

| Command | Purpose | Key Flags | Typical Usage |
|---------|---------|-----------|---------------|
| `backfill_subscription_periods` | Create SubscriptionPeriod records for existing premium subscribers who don't have one, using a specified start date. | `--start` (required, format: YYYY-MM-DD), `--dry-run` | `python manage.py backfill_subscription_periods --start 2026-01-20 --dry-run` |
| `audit_subscription_status` | Audit users marked as premium against actual Stripe/PayPal subscription status. Optionally revoke premium for users with unpaid or missing subscriptions. | `--fix`, `--dry-run` | `python manage.py audit_subscription_status --fix --dry-run` |

### fundraiser

| Command | Purpose | Key Flags | Typical Usage |
|---------|---------|-----------|---------------|
| `fix_badge_picks` | Recompute `badge_picks_earned` for fundraiser donations from users who donated multiple times across overlapping campaigns. One-time repair, safe to re-run. | `--dry-run` | `python manage.py fix_badge_picks --dry-run` |

---

## Command Categories

### Cron Jobs

These commands run on automated schedules. See your hosting provider's cron configuration for exact timing.

| Command | Schedule | Notes |
|---------|----------|-------|
| `refresh_profiles` | Every 30 minutes | Queues profiles for PSN sync by tier |
| `refresh_homepage_hourly` | Every hour | Site heartbeat ribbon (single cache key) |
| `update_leaderboards` | Every 6 hours | Badge leaderboards (7h cache TTL) |
| `process_scheduled_notifications` | Every hour | Delivers due scheduled notifications |
| `check_subscription_milestones` | Daily | Checks subscription duration milestones |
| `cleanup_old_analytics` | Weekly or monthly | GDPR cleanup of old session/IP data |
| `generate_monthly_recaps` | 3rd of month, 00:05 UTC | Generate and finalize previous month's recaps |
| `send_monthly_recap_emails` | 3rd of month, 06:00 UTC | Send recap emails + in-app notifications |
| `send_weekly_digest` | Monday 08:00 UTC | Send "This Week in PlatPursuit" community newsletter |
| `populate_title_ids` | Daily or weekly | Sync TitleID table from GitHub |
| `update_shovelware` | Weekly | Surgical shovelware reconciliation (idempotent drift correction) |

### Admin Tools

Commands for staff to run manually as needed.

| Command | Purpose |
|---------|---------|
| `redis_admin` | Cache management: flush specific page caches, TokenKeeper queues, bulk thresholds |
| `lock_shovelware` | Manually flag/clear/unlock a game's shovelware status |
| `backfill_shovelware` | One-shot wipe + rebuild of shovelware state (use after migrations / major data corrections) |
| `lock_admin_concepts` | Lock concepts with admin-duplicate suffixes |
| `refresh_badge_series` | Re-evaluate a badge series after stage/concept changes |
| `check_all_badges` | Full badge recheck with awarded/revoked reporting |
| `grant_milestone` | Manually grant milestones to specific users |
| `audit_user_awards` | Audit and clean up orphaned titles/milestones |
| `audit_subscription_status` | Verify premium users against payment provider status |
| `audit_profile_gamification` | Find XP discrepancies in gamification records |
| `audit_calendar` | Audit Calendar Challenge fill state against actual platinums |
| `sync_all_discord_roles` | Re-push Discord roles for all verified users |
| `clean_titles` | Strip symbols and normalize titles across all models |
| `enforce_az_challenge_rules` | Remove excluded games from A-Z challenge slots |
| `recalculate_calendars` | Repair Calendar Challenge fill drift |
| `test_email_system` | Send preview emails for any template |
| `create_test_notification` | Create test notifications for inbox development |
| `enrich_from_igdb` | IGDB enrichment pipeline (review queue, manual assign, refresh) |
| `find_igdb_family_ties` | Surface concepts that share IGDB ID but not GameFamily |
| `rebuild_franchises_from_cache` | Rebuild Franchise/ConceptFranchise from cached IGDBMatch (no API calls) |
| `backfill_franchise_main_flag` | Recompute ConceptFranchise.is_main from cached data |
| `franchise_stats` | Diagnostic: franchise/collection coverage and browse surfacing |
| `inspect_franchise_data` | Diagnostic: raw IGDB response vs. stored links for a concept |
| `trigger_concept_health_checks` | Queue sync for PS4/PS5 games stuck on stub concepts |
| `render_profile_sigs` | Pre-render forum-signature PNG/SVG variants of the profile card |

### One-Time Backfills

Commands that were run once (or a few times) for data migration. They remain in the codebase for reference and potential re-use.

| Command | Purpose |
|---------|---------|
| `backfill_default_concepts` | Create stub Concepts for games without one |
| `backfill_concept_slugs` | Generate URL slugs for existing Concepts |
| `backfill_stub_concept_icons` | Copy game icons to PP_ stub Concepts |
| `backfill_concept_trophy_groups` | Create ConceptTrophyGroup records from game TrophyGroups |
| `backfill_game_regions` | Populate Game.region from TitleID data |
| `backfill_guide_view_counts` | Fix guide view counts after page_type rename |
| `backfill_subscription_periods` | Create SubscriptionPeriod for existing subscribers |
| `backfill_stage_completions` | Backfill historical StageCompletionEvent rows |
| `backfill_platted_subgenre_count` | Fix subgenre counts and revoke bad milestones |
| `fix_badge_picks` | Recompute fundraiser badge_picks_earned for multi-donation users |
| `mark_recaps_sent` | Prevent stale recap sends after email fix |
| `populate_badges` | Initial badge evaluation for all profiles |
| `populate_profile_plats` | Recalculate platinum counts for all profiles |
| `populate_profilegame_stats` | Recalculate per-game stats for a profile |
| `populate_region_asian_titles` | Detect and tag Asian-language games |
| `populate_user_titles` | Backfill UserTitle records from badge/milestone awards |
| `populate_banned_words` | Seed banned words for content moderation |
| `populate_milestones` | Create/update milestone definitions (idempotent, re-runnable) |
| `recalculate_profile_counts` | Full profile trophy count recalculation |
| `recalculate_gamification` | Full gamification XP recalculation |
| `recalc_earn_rates` | Recalculate played_count, earned_count, earn_rate |
| `populate_user_milestones` | Backfill milestones for existing users |
| `update_badge_requirements` | Refresh badge requirements and most_recent_concept |

### Diagnostics

Commands for debugging and monitoring. These do not modify data (except where noted).

| Command | Purpose |
|---------|---------|
| `test_psn` | Test PSN API connection and fetch sample data |
| `debug_signals` | Inspect signal connections for EarnedTrophy |
| `test_signals` | Verify platinum notification signal is connected |
| `test_platinum_signal` | Trigger a real platinum signal (modifies DB) |
| `force_platinum_notification` | Directly invoke the platinum notification handler |
| `audit_genre_data` | Report genre/subgenre coverage statistics |
| `check_profile_badge_series` | Test badge evaluation for a specific profile + series |

---

## Common Patterns

### `--dry-run`

The most common flag across the codebase. When provided, the command previews what changes would be made without writing to the database. Always run with `--dry-run` first when using a command for the first time or on production data.

Commands that support `--dry-run`: `backfill_default_concepts`, `backfill_concept_slugs`, `backfill_stub_concept_icons`, `backfill_game_regions`, `backfill_guide_view_counts`, `backfill_subscription_periods`, `backfill_platted_subgenre_count`, `check_all_badges`, `check_subscription_milestones`, `clean_titles`, `cleanup_old_analytics`, `enforce_az_challenge_rules`, `generate_monthly_recaps`, `grant_milestone`, `lock_admin_concepts`, `mark_recaps_sent`, `match_game_families`, `populate_banned_words`, `populate_milestones`, `populate_user_titles`, `process_scheduled_notifications`, `recalc_earn_rates`, `recalculate_gamification`, `send_monthly_recap_emails`, `send_weekly_digest`, `sync_all_discord_roles`, `update_shovelware`.

### `--username` / `--profile`

Many commands accept a PSN username to target a single profile instead of processing all profiles. The flag name varies:

- `--username`: `populate_profilegame_stats`, `check_profile_badge_series`, `populate_badges`, `populate_user_milestones`, `check_all_badges`, `audit_user_awards`, `grant_milestone`
- `--profile`: `recalculate_gamification`, `sync_all_discord_roles`, `audit_profile_gamification`
- `--profile-id`: `generate_monthly_recaps`, `send_monthly_recap_emails`, `send_weekly_digest`

### `--batch-size`

Controls the number of records processed per database batch. Used by commands that handle large datasets:

- `populate_profilegame_stats`, `backfill_default_concepts`, `backfill_concept_slugs`, `backfill_stub_concept_icons`, `recalc_earn_rates`, `sync_all_discord_roles`, `send_monthly_recap_emails`, `send_weekly_digest`

### `--verbose`

Shows detailed per-record output. Used by: `backfill_game_regions`, `update_shovelware`, `audit_profile_gamification`.

### `--commit` vs `--dry-run`

Most commands default to making changes and require `--dry-run` to preview. The exception is `audit_user_awards`, which defaults to dry-run mode and requires `--commit` to apply changes.

### `--silent`

Suppresses in-app notifications when awarding milestones. Used by: `populate_user_milestones`, `check_subscription_milestones`, `grant_milestone`.

### `--force`

Skips confirmation prompts or re-processes already-completed records. Used by: `cleanup_old_analytics` (skip prompt), `send_monthly_recap_emails` (resend to already-emailed users), `send_weekly_digest` (bypass EmailLog dedup).

---

## Related Docs

- [Local Setup](local-setup.md): Getting the development environment running
