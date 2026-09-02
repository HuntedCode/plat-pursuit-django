# Management Commands

PlatPursuit has **87 custom management commands** spread across 7 Django apps: `trophies` (55), `core` (21), `notifications` (5), `users` (2), `milestones` (2), `fundraiser` (1), and `art_reveal` (1). All commands follow the standard Django pattern and are invoked with `python manage.py <command_name>`. Many support `--dry-run` for safe previewing before applying changes.

---

## Commands by App

### trophies

| Command | Purpose | Key Flags | Typical Usage |
|---------|---------|-----------|---------------|
| `start_token_keeper` | Starts the TokenKeeper singleton process for managing PSN API tokens and job queues. Long-running daemon. | (none) | `python manage.py start_token_keeper` |
| `token_keeper_control` | Control TokenKeeper lifecycle: start, stop, or restart. | `--start`, `--stop`, `--restart` (mutually exclusive, required) | `python manage.py token_keeper_control --restart` |
| `populate_profile_plats` | Recalculate platinum counts for all profiles by calling `update_plats()` on each. | (none) | `python manage.py populate_profile_plats` |
| `populate_profilegame_stats` | Recalculate earned/unearned trophy counts, `has_plat`, and most recent trophy date for a single profile's ProfileGame records. | `--username` (required), `--batch_size` (default: 100) | `python manage.py populate_profilegame_stats --username Jlowe` |
| `populate_region_asian_titles` | Detect and flag Asian-language games without concepts, setting `is_regional` and assigning region codes. | (none) | `python manage.py populate_region_asian_titles` |
| `populate_user_titles` | Backfill UserTitle records for existing badge and milestone awards. **LEGACY** (writes `source_type='badge'`); for the new badge system use `sync_series_titles`. | `--dry-run`, `--badges-only`, `--milestones-only` | `python manage.py populate_user_titles --dry-run` |
| `sync_series_titles` | Reconcile `UserTitle` against the group badges actually held (NEW badge system). Grants titles the one-shot `award` path missed, and adopts rows another system wrote on a shared Title. Fixes under-counted title rarity and "Be the first" on a title you hold. | `--series`, `--dry-run`, `--prune` | `python manage.py sync_series_titles --dry-run` |
| `populate_banned_words` | Seed the BannedWord table with a default list of inappropriate words and spam indicators. | `--clear`, `--dry-run` | `python manage.py populate_banned_words --dry-run` |
| `lock_admin_concepts` | Lock `concept_lock` on games whose concept has an admin-duplicate suffix (`-1`, `-2`, etc.) or `PP_` stub prefix. | `--dry-run` | `python manage.py lock_admin_concepts --dry-run` |
| `backfill_default_concepts` | Create stub `PP_` Concepts for games that don't have one. | `--dry-run`, `--batch-size` (default: 50) | `python manage.py backfill_default_concepts --dry-run` |
| `sync_all_discord_roles` | Bulk sync Discord roles (badge, milestone, premium) for all verified users. | `--dry-run`, `--profile`, `--batch-size` (default: 100) | `python manage.py sync_all_discord_roles --dry-run` |
| `backfill_stub_concept_icons` | Copy `title_icon_url` from associated games to `PP_` stub Concepts missing icons. | `--dry-run`, `--batch-size` (default: 100) | `python manage.py backfill_stub_concept_icons` |
| `clean_titles` | Strip TM/registered symbols, normalize Unicode Roman numerals, and remove "trophy set" suffixes from Game, Concept, Trophy, and GameFamily titles. | `--dry-run` | `python manage.py clean_titles --dry-run` |
| `psn_probe` | Probe PSN API endpoints directly and dump raw payloads. Troubleshooting tool for sync discrepancies. 12 endpoints: `profile`, `profile_legacy`, `presence`, `region`, `friendship`, `trophy_summary`, `trophy_titles`, `trophy_titles_for_title`, `title_stats`, `trophies`, `trophy_groups_summary`, `game_details`. Requires `NPSSO_TOKEN` in `.env`. | `endpoint` (positional), `--user` (default: `abu_abu`), `--np-comm-id`, `--np-title-id`, `--platform` (default: `PS5`), `--trophy-group-id` (default: `all`), `--include-progress`, `--title-ids`, `--limit`, `--offset`, `--page-size`, `--first` (default: `5`, `0` = all) | `python manage.py psn_probe profile_legacy --user abu_abu` |
| `backfill_game_regions` | Populate `Game.region` from TitleID region data (loaded by `populate_title_ids`). | `--dry-run`, `--verbose` | `python manage.py backfill_game_regions --dry-run --verbose` |
| `audit_badge_coverage` | For each badge SERIES tracking a franchise/collection/developer, find concepts of that source not covered by the series' stages, and email the gaps to `badge-alerts@platpursuit.com`. A gap usually means a new game needs adding to the series. Emails only when gaps exist unless `--always`. | `--dry-run`, `--always` | `python manage.py audit_badge_coverage --dry-run` |
| `nightly` | **The nightly maintenance run, one cron entry.** Executes the badge chain in DEPENDENCY order (evaluate -> DLC sweep -> coverage audit). Each step is isolated so one failure does not cancel the rest, and the command exits non-zero if any failed. Add nightly work as a STEP here, not as a new cron entry. | `--dry-run`, `--only <label>`, `--skip <label>` | `python manage.py nightly --dry-run` |
| `evaluate_badges` | **The badge engine's only runner.** Evaluates a hunter's grouping badges and applies earns/revokes, then recomputes their standings. The nightly `--all` is the reconcile that keeps every badge figure honest. `--series` is LIVE-only: a write run through a dormant edition inflated Badge Points and left holds the live-scoped nightly can never revoke. Preview an unreleased series with `--series <slug> --include-dormant --dry-run`, which is refused without `--dry-run`. | `username` (positional), `--all`, `--series <slug>`, `--dry-run`, `--compare-legacy` | `python manage.py evaluate_badges --all` |
| `detect_dlc_and_refresh` | Detect games that gained new DLC since the last run and re-evaluate every affected badge series across all live editions, for every hunter who has played one of its games. Also recomputes owner completion %, since DLC grows the trophy denominator. | `--since <iso>`, `--dry-run` | `python manage.py detect_dlc_and_refresh --dry-run` |
| `convert_series_to_groups` | Cutover seeder: builds a `BadgeSeries` + a dormant `GroupBadge` per detected platform group from a legacy series. Also works fresh (stages + series authored by hand, no legacy Badge). **A hard prerequisite for the fundraiser/art_reveal repoint migrations.** | `slug` (positional), `--all`, `--dry-run` | `python manage.py convert_series_to_groups --all --dry-run` |
| `backfill_concept_slugs` | Generate URL slugs for Concepts that don't have one. Handles collisions with counter suffixes. | `--dry-run`, `--batch-size` (default: 100) | `python manage.py backfill_concept_slugs` |
| `redis_admin` | Swiss-army knife for Redis operations: flush caches, manage TokenKeeper queues, adjust bulk thresholds, migrate whale jobs. | `--flushall`, `--flush-index`, `--flush-game-page <np_id>`, `--flush-token-keeper`, `--flush-complete-lock <profile_id>`, `--flush-stats <profile_id>`, `--flush-concept <concept_id>`, `--flush-community`, `--get-bulk-threshold`, `--set-bulk-threshold <n>`, `--move-whale-jobs` (all mutually exclusive) | `python manage.py redis_admin --flush-index` |
| `backfill_concept_trophy_groups` | Create ConceptTrophyGroup records from game-level TrophyGroups. Also includes mismatch detection and audit modes. `--audit-orphaned-groups` finds games whose trophies reference a `trophy_group_id` with no matching TrophyGroup row (corrupted/missing DLC groups while trophies survive); add `--fix` to re-queue `sync_trophy_groups` to rebuild them (requires the TokenKeeper worker running). | `--dry-run`, `--check-mismatches`, `--collections-only`, `--audit-missing-trophies`, `--audit-missing-groups`, `--audit-orphaned-groups`, `--fix` | `python manage.py backfill_concept_trophy_groups --audit-orphaned-groups --fix` |
| `resync_trophy_groups` | Enqueue `sync_trophy_groups` to refresh games' trophy groups from PSN, catching DLC/trophy groups added to a title after our last sync (common for low-popularity games no active user keeps synced, which can't be detected from our own DB). The PSN call is title-level, so one driver profile refreshes any game's groups, including games with zero players. Drains on `bulk_priority` so it never starves live syncs. Idempotent. The driver carries the whole sweep's job counter, so prefer a dedicated/pausable scout. Requires the TokenKeeper worker running. | `--dry-run`, `--driver-profile <psn_username>`, `--missing-only`, `--platform <P>`, `--limit <N>` | `python manage.py resync_trophy_groups --dry-run` |
| `audit_psn_capture` | Read-only report on what PSN metadata capture is actually storing: row counts, how many rows are empty per parsed field, the distribution of answering storefronts, game-level title-observation coverage, and one sample row's media/raw response keys. Exists because the PSN response shapes were inferred from how the sync code reads them rather than from a recorded fixture, so **a field empty on 100% of rows means we are reading the wrong key** (that is what `media` looked like before it was found to be a dict, not a list). Run it after deploy and after each backfill batch. All counts are DB-side aggregates, so cost does not grow with the table. `--gap` classifies the concepts that have NO PSN row (no games / no title_id / reachable-but-uncaptured, plus a platform breakdown and a PP_* stub count), so a shortfall after a sweep can be split into "we never asked" and "PSN had nothing to give" without spending a single PSN call. | `--gap`, `--sample <N>` (default 1, 0 to skip the detail block) | `python manage.py audit_psn_capture --gap` |
| `backfill_psn_game_observations` | Queue FORCED-WALK profile refreshes so `PSNTitleObservation` (game-level, append-on-change: every distinct title tuple PSN has sent, including the raw uncleaned `title_name` that `Game` has never stored) backfills from real syncs. A normal refresh fast-paths when the fingerprint matches and walks nothing, so `force_walk` makes the orchestrator walk the full library; each profile costs only its trophy_titles pagination, and libraries overlap so a few large accounts cover most of the catalogue. The table also fills organically from every slow-path sync and from fast-path page 1. Refuses to run when `PSN_METADATA_CAPTURE_ENABLED` is False; prompts unless `--yes`. Queue tens per session, not hundreds (orchestrator lane). | `--usernames <a,b>`, `--top <N>` (largest libraries), `--dry-run`, `--yes` | `python manage.py backfill_psn_game_observations --top 10 --dry-run` |
| `backfill_psn_concept_data` | Re-run PSN concept resolution across the catalogue so every game gets a fresh `get_details` response, and with it a `PSNConceptData` + `PSNRawPayload` capture. Capture only happens on the sync path, so games resolved before it shipped have no PSN row and will never get one on their own (an already-anchored, already-matched game is never re-queued by a normal profile sync). **Not capture-only**: it enqueues `sync_title_id`, the full concept-resolution pipeline, which may also correct TitleID platform/region, add Game regions, refresh a PSN-native concept's release date/media/English fields, and anchor a concept-less Game. Those are the same writes a normal sync performs; IGDB-anchored concepts are preserved untouched. Drains on `bulk_priority`. Each game costs at least one PSN call, so use `--missing-only` to resume and `--limit` to run in sessions. The driver carries the sweep's job counter and gets `sync_progress_value` incremented per job, so use a dedicated scout and reset its progress after. Refuses to run when `PSN_METADATA_CAPTURE_ENABLED` is False, since the sweep would spend the whole budget and capture nothing. Prompts for confirmation unless `--yes`. Skips `TITLE_ID_BLACKLIST`. By default sweeps only `title_ids[0]` (append-order, i.e. whichever the first syncing user owned); `--all-title-ids` gathers the regional storefronts too at multiplied cost. `--missing-only` shrinks on SUCCESS only: titles PSN answers sparsely for write no row and are re-swept every run, so expect a floor rather than zero. Requires the TokenKeeper worker running. | `--dry-run`, `--yes` (skip the confirmation prompt), `--driver-profile <psn_username>`, `--missing-only`, `--all-title-ids`, `--platform <P>`, `--limit <N>` | `python manage.py backfill_psn_concept_data --dry-run` |
| `enrich_from_igdb` | Run the IGDB enrichment pipeline against concepts (developer/publisher, genres/themes, time-to-beat, engine, VR detection). Supports targeted, refresh, retry, search, manual-assign, review queue, and unmatched-queue modes. See [IGDB Integration](../architecture/igdb-integration.md). | `--concept-id`, `--refresh`, `--retry-no-match`, `--search`, `--manual`, `--review`, `--unmatched`, `--badge`, `--all`, `--force`, `--verbose`, `--dry-run` | `python manage.py enrich_from_igdb --review` |
| `rematch_auto_accepted` | Re-run the IGDB matching pipeline against every `auto_accepted` match. Clear upgrades (different id, above auto-accept, beats stored confidence) are applied directly; everything else becomes a `RematchSuggestion` for admin review. See [IGDB Integration](../architecture/igdb-integration.md#phase-3-rematch-sweep). | `--dry-run`, `--concept-id`, `--limit N`, `--verbose` | `python manage.py rematch_auto_accepted --dry-run` |
| `rebuild_concept_enrichment` | Wipe stale ConceptCompany/Genre/Theme/Engine/Franchise rows for every accepted IGDB match and re-apply enrichment from the stored `raw_response`. Clears the backlog of doubled-up data left behind by concept-match reassignments. No IGDB API calls. | `--dry-run`, `--concept-id`, `--limit N`, `--verbose` | `python manage.py rebuild_concept_enrichment --dry-run` |
| `review_title_merges` | Interactive: surface concepts whose trusted IGDB match carries a title that differs from the concept's `unified_title` or any game's `title_name`, and merge them to the IGDB canonical. Concept gets an auto-suggested platform suffix (e.g. ` - (PS3)`) for legacy-only concepts; games receive the raw IGDB name verbatim. Sets `Concept.title_lock=True` + `Game.lock_title=True` on merge so PSN sync doesn't regress the curation. | `--clear-locks`, `--concept-id`, `--limit N`, `--legacy-only`, `--badge` | `python manage.py review_title_merges --legacy-only` |
| `rebuild_franchises_from_cache` | Rebuild `Franchise` + `ConceptFranchise` rows from cached `IGDBMatch.raw_response` without hitting the IGDB API. Use after enrichment-logic changes or to recover from corrupted data. `--force` bypasses `concept.franchises_locked` (use only when curated data is also corrupted); combine with `--wipe` for a full reset that ignores the lock. | `--wipe`, `--force`, `--dry-run`, `--batch-size` | `python manage.py rebuild_franchises_from_cache --wipe` |
| `backfill_collection_spinoffs` | Stamp `ConceptFranchise.is_spinoff` for collection links by querying IGDB `/collection_memberships` (type 2 = Spin-off). Unlike the franchise backfills, this HITS THE IGDB API — the membership type is not in cached `raw_response`. Idempotent. | `--dry-run`, `--limit N`, `--batch-size` (default 500) | `python manage.py backfill_collection_spinoffs` |
| `franchise_stats` | Read-only diagnostic reporting franchise/collection totals, per-concept coverage, browse-page surfacing counts, and sample names. Useful for auditing enrichment coverage and deciding whether the collection-orphan rule is producing sensible results. | `--samples N` (default 10, 0 to skip names) | `python manage.py franchise_stats --samples 20` |
| `inspect_franchise_data` | Read-only diagnostic: compare raw IGDB response to stored links for a concept or franchise. First stop when investigating mis-linked games. Shows drift detection (what's in IGDB but not the DB, or vice versa). | `--search`, `--concept-id`, `--franchise-name` (one required) | `python manage.py inspect_franchise_data --search "College Football"` |
| `render_profile_sigs` | Pre-render forum-signature PNG and SVG variants of the profile card image. Used as a one-time backfill and as a periodic refresh after design changes. | `--username`, `--all` | `python manage.py render_profile_sigs --all` |
| `trigger_concept_health_checks` | Resolve a concept for every concept-less Game inline (no PSN/worker): tries the IGDB anchor, falls back to a PP_ stub so nothing stays null. Same anchor-or-stub recovery `sync_complete`'s orphan reconcile runs, on demand. Reaches games PSN's title_stats endpoint omits. PP_ stubs are out of scope (use `anchor_concepts` to re-evaluate those). | `--dry-run`, `--profile-id`, `--limit` | `python manage.py trigger_concept_health_checks --dry-run` |

### core

| Command | Purpose | Key Flags | Typical Usage |
|---------|---------|-----------|---------------|
| `refresh_profiles` | Queue profiles for PSN sync based on tier and last update time. Premium every 6h, basic every 12h, Discord-verified every 12h, unregistered every 7d. | `--premium-hours` (default: 6), `--basic-hours` (default: 12), `--discord-hours` (default: 12), `--unreg-days` (default: 7) | `python manage.py refresh_profiles` |
| `recalc_earn_rates` | Recalculate `played_count` + community stats (`plats_earned_count`, `full_completion_count`, `avg_completion`, `monthly_players_count`, `total_earns_count`) on Games and `earned_count`/`earn_rate` on Trophies from source data. The game-detail stats row reads these columns directly, so this is on the render path. | `--dry-run`, `--chunk-size` (default: 200), `--max-minutes` (default: 30), `--game-ids` | `python manage.py recalc_earn_rates --dry-run` |
| `recalculate_profile_counts` | Recalculate trophy counts for all profiles using `update_profile_trophy_counts()`. | (none) | `python manage.py recalculate_profile_counts` |
| `process_scheduled_notifications` | Process pending scheduled notifications that are due for delivery. | `--dry-run` | `python manage.py process_scheduled_notifications` |
| `generate_monthly_recaps` | Generate monthly recap data for active profiles. Defaults to previous month. | `--dry-run`, `--finalize`, `--profile-id`, `--year`, `--month`, `--current-month` | `python manage.py generate_monthly_recaps --finalize` |
| `send_monthly_recap_emails` | Send monthly recap emails and in-app notifications to users with finalized recaps. Respects email opt-out preferences. | `--dry-run`, `--year`, `--month`, `--profile-id`, `--force`, `--batch-size` (default: 100) | `python manage.py send_monthly_recap_emails --dry-run` |
| `mark_recaps_sent` | One-time fix: mark all existing recaps as `email_sent` and `notification_sent` to prevent stale sends. | `--dry-run` | `python manage.py mark_recaps_sent` |
| `refresh_homepage_hourly` | Compute and cache the site heartbeat ribbon data ("PlatPursuit at a Glance"). Single cache key per hour. See [Homepage Services](../reference/homepage-services.md). | (none) | `python manage.py refresh_homepage_hourly` |
| `post_community_trophy_tracker` | Compute previous ET day's community trophy stats from Discord-linked profiles and post a daily summary to Discord via webhook. Idempotent via `CommunityTrophyDay.posted_at`. See [Community Trophy Tracker](../features/community-trophy-tracker.md). | `--date YYYY-MM-DD`, `--force-repost`, `--dry-run`, `--test-data`, `--test-scenario {record\|normal}`, `--use-platinum-webhook` | `python manage.py post_community_trophy_tracker --test-data` |
| `announce_contracts` | Post newly published Contracts to Discord, grouped by JOB, with a link to the board with Latest applied. **Silent when nothing is new** (most runs). "New" = `is_live=True` + `went_live_at` stamped + `announced_at` null, which excludes staged/review-queued candidates and the `went_live_at`-null launch set structurally. Idempotent via the `Contract.announced_at` COLUMN, stamped only after a confirmed 2xx. Refuses a wave over 40 without `--force` (a bulk publish would post a wall); use `--baseline` once after the launch seed to record the backlog as already known. `--test-webhook` deliberately does NOT stamp, so a preview cannot consume a wave. See [Cron Jobs](cron-jobs.md#announce_contracts). | `--dry-run`, `--test-webhook`, `--limit N`, `--baseline`, `--force` | `python manage.py announce_contracts --dry-run` |
| `populate_title_ids` | Populate TitleID table from external PlayStation Titles GitHub repository (PS4 + PS5 TSV files). | (none) | `python manage.py populate_title_ids` |
| `backfill_game_families_from_igdb` | Populate `GameFamily` records from accepted `IGDBMatch` rows, keyed on `igdb_id`. One-shot historical pass; live enrichment hooks handle new matches. | `--dry-run` | `python manage.py backfill_game_families_from_igdb --dry-run` |
| `send_launch_announcement` | Send the one-time "PlatPursuit 1.0 is here" email to accounts that existed before `PP_LAUNCH_DATE`. **Dry run by default**; `--send` additionally requires `LAUNCH_ANNOUNCEMENT_SEND_ENABLED`. Idempotent per user via EmailLog (no `--force` on purpose). Refuses to run without `PP_LAUNCH_DATE`. | `--send`, `--batch-size` (default 100), `--sleep` (default 2.0), `--limit`, `--user-id` | `python manage.py send_launch_announcement` |
| `send_weekly_digest` | Send "This Week in PlatPursuit" community newsletter with site-wide stats, top platted games, review of the week, and condensed personal stats. Community data fetched once per batch. Only suppressed if the community had zero activity. | `--dry-run`, `--profile-id`, `--force`, `--batch-size` (default: 100) | `python manage.py send_weekly_digest --dry-run` |
| `test_email_system` | Send test emails for any template to verify email delivery. Supports 17+ email template previews. | `recipient_email` (positional, required), `--recap-preview`, `--verification-preview`, `--password-reset-preview`, `--payment-failed-preview`, `--payment-failed-final-preview`, `--cancelled-preview`, `--welcome-preview`, `--payment-succeeded-preview`, `--payment-action-required-preview`, `--donation-receipt-preview`, `--badge-claim-preview`, `--artwork-complete-preview`, `--badge-earned-preview`, `--milestone-preview`, `--free-welcome-preview`, `--broadcast-preview`, `--weekly-digest-preview` | `python manage.py test_email_system your@email.com --recap-preview` |
| `lock_shovelware` | Lock or unlock a game's shovelware status. Propagates to all games sharing the same concept. | `np_communication_id` (positional, required), `--flag`, `--clear`, `--unlock` (mutually exclusive, required) | `python manage.py lock_shovelware NPWR12345_00 --flag` |
| `update_shovelware` | Surgical shovelware reconciliation. Walks a targeted candidate set and applies `evaluate_concept` idempotently, only writing where state has drifted. Preserves `shovelware_updated_at` on unchanged games. | `--verbose` | `python manage.py update_shovelware` |
| `backfill_shovelware` | One-shot reset + rebuild of shovelware state using the median + proportional-developer algorithm (resets blacklist status but preserves admin whitelists and notes). Use after rule changes or major data corrections. | `--dry-run`, `--verbose` | `python manage.py backfill_shovelware --dry-run --verbose` |
| `review_shovelware_blacklist` | Read-only review sheet of currently-blacklisted developers (shovelware proportion, dominant genres/themes, sample games, count flagged) to decide whitelist candidates. Sorted by impact. `--compact` gives a one-line-per-developer summary for easy staff hand-off; `--csv` emits spreadsheet-ready CSV (redirect to a file). | `--compact`, `--csv`, `--samples N`, `--limit N`, `--include-whitelisted` | `python manage.py review_shovelware_blacklist --csv > blacklist.csv` |
| `audit_genre_data` | Report genre and subgenre coverage stats, unique values with counts, and genre-to-subgenre relationships. Filters to challenge-eligible concepts by default. | `--all` | `python manage.py audit_genre_data` |

### notifications

| Command | Purpose | Key Flags | Typical Usage |
|---------|---------|-----------|---------------|
| `debug_signals` | Debug signal connection issues by inspecting registered `post_save` receivers for `EarnedTrophy`. | (none) | `python manage.py debug_signals` |
| `test_signals` | Verify that the platinum notification signal handler is properly connected and the template exists. | (none) | `python manage.py test_signals` |
| `create_test_notification` | Create a test notification with enhanced metadata for inbox testing. Supports platinum and challenge types. | `--username`, `--type` (`platinum`/`challenge`, default: `platinum`) | `python manage.py create_test_notification --type challenge` |
| `test_platinum_signal` | Create a test platinum trophy earning to trigger the signal. Modifies the database by creating/updating an EarnedTrophy record. | (none) | `python manage.py test_platinum_signal` |
| `force_platinum_notification` | Bypass normal signal flow and directly call the platinum notification handler using an existing earned platinum. | (none) | `python manage.py force_platinum_notification` |

### milestones

| Command | Purpose | Key Flags | Typical Usage |
|---------|---------|-----------|---------------|
| `seed_milestones` | Upsert the milestone catalogue (ladders + tiers). Idempotent: preserves each tier's `earned_count`, `discord_role_id`, and every earned record. Never auto-shrinks a ladder (warns instead). | none | `python manage.py seed_milestones` |
| `recompute_milestones` | Recompute progress + award newly-crossed tiers for every community member (nightly cron), then drift-correct tier `earned_count` and refresh the rarity denominator. | `--profile`, `--reconcile-discord`, `--reset`, `--milestone`, `--yes` | `python manage.py recompute_milestones` |

### users

| Command | Purpose | Key Flags | Typical Usage |
|---------|---------|-----------|---------------|
| `backfill_subscription_periods` | Create SubscriptionPeriod records for existing premium subscribers who don't have one, using a specified start date. | `--start` (required, format: YYYY-MM-DD), `--dry-run` | `python manage.py backfill_subscription_periods --start 2026-01-20 --dry-run` |
| `audit_subscription_status` | Audit users marked as premium against actual Stripe/PayPal subscription status. Optionally revoke premium for users with unpaid or missing subscriptions. | `--fix`, `--dry-run` | `python manage.py audit_subscription_status --fix --dry-run` |

### fundraiser

| Command | Purpose | Key Flags | Typical Usage |
|---------|---------|-----------|---------------|
| `fix_badge_picks` | Recompute `badge_picks_earned` for fundraiser donations from users who donated multiple times across overlapping campaigns. One-time repair, safe to re-run. | `--dry-run` | `python manage.py fix_badge_picks --dry-run` |

### art_reveal

| Command | Purpose | Key Flags | Typical Usage |
|---------|---------|-----------|---------------|
| `process_art_reveals` | Recount community badge-platinums for each live Badge Art Reveal event and release any newly-unlocked artwork (copies the art onto the badge). Idempotent; reconciles to the current count each run. | (none) | `python manage.py process_art_reveals` |

---

## Command Categories

### Cron Jobs

These commands run on automated schedules. See your hosting provider's cron configuration for exact timing.

| Command | Schedule | Notes |
|---------|----------|-------|
| `refresh_profiles` | Every 30 minutes | Queues profiles for PSN sync by tier |
| `refresh_homepage_hourly` | Every hour | Site heartbeat ribbon (single cache key) |
| `process_scheduled_notifications` | Every hour | Delivers due scheduled notifications |
| `generate_monthly_recaps` | 3rd of month, 00:05 UTC | Generate and finalize previous month's recaps |
| `send_monthly_recap_emails` | 3rd of month, 06:00 UTC | Send recap emails + in-app notifications |
| `send_weekly_digest` | Monday 08:00 UTC | Send "This Week in PlatPursuit" community newsletter |
| `populate_title_ids` | Daily or weekly | Sync TitleID table from GitHub |
| `update_shovelware` | Weekly | Surgical shovelware reconciliation (idempotent drift correction) |
| `nightly` | Daily 04:00 UTC | Runs the badge chain in order (see cron-jobs.md) |
| ~~`evaluate_badges --all`~~ | folded into `nightly` | The badge reconcile. Without it, badge figures are only as fresh as whatever each hunter's own sync last touched |
| ~~`detect_dlc_and_refresh`~~ | folded into `nightly` | Re-evaluates series whose games gained DLC, and repairs owner completion % |
| ~~`audit_badge_coverage`~~ | folded into `nightly` | Still runnable standalone |
| `process_art_reveals` | Per schedule | Advances Badge Art Reveal events and releases due artwork |

### Admin Tools

Commands for staff to run manually as needed.

| Command | Purpose |
|---------|---------|
| `redis_admin` | Cache management: flush specific page caches, TokenKeeper queues, bulk thresholds |
| `lock_shovelware` | Manually flag/clear/unlock a game's shovelware status |
| `backfill_shovelware` | One-shot wipe + rebuild of shovelware state (use after migrations / major data corrections) |
| `lock_admin_concepts` | Lock concepts with admin-duplicate suffixes |
| `audit_subscription_status` | Verify premium users against payment provider status |
| `sync_all_discord_roles` | Re-push Discord roles for all verified users |
| `clean_titles` | Strip symbols and normalize titles across all models |
| `test_email_system` | Send preview emails for any template |
| `create_test_notification` | Create test notifications for inbox development |
| `enrich_from_igdb` | IGDB enrichment pipeline (review queue, manual assign, refresh) |
| `find_igdb_family_ties` | Surface concepts that share IGDB ID but not GameFamily |
| `rebuild_franchises_from_cache` | Rebuild Franchise/ConceptFranchise from cached IGDBMatch (no API calls); `--force` bypasses franchises_locked |
| `backfill_collection_spinoffs` | Stamp ConceptFranchise.is_spinoff for collection links (queries IGDB /collection_memberships) |
| `franchise_stats` | Diagnostic: franchise/collection coverage and browse surfacing |
| `inspect_franchise_data` | Diagnostic: raw IGDB response vs. stored links for a concept |
| `trigger_concept_health_checks` | Resolve concept-less games inline via IGDB anchor, else PP_ stub |
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
| `backfill_subscription_periods` | Create SubscriptionPeriod for existing subscribers |
| `fix_badge_picks` | Recompute fundraiser badge_picks_earned for multi-donation users |
| `mark_recaps_sent` | Prevent stale recap sends after email fix |
| `populate_profile_plats` | Recalculate platinum counts for all profiles |
| `populate_profilegame_stats` | Recalculate per-game stats for a profile |
| `populate_region_asian_titles` | Detect and tag Asian-language games |
| `populate_user_titles` | Backfill UserTitle records from badge/milestone awards (legacy system) |
| `sync_series_titles` | Reconcile UserTitle against held group badges (new badge system) |
| `populate_banned_words` | Seed banned words for content moderation |
| `recalculate_profile_counts` | Full profile trophy count recalculation |
| `recalc_earn_rates` | Recalculate played_count + community stats, earned_count, earn_rate |

### verify_profile_sync

`python manage.py verify_profile_sync <psn_username> [--verbose]`

Reconciles ONE profile's denormalized state against ground truth. **Read-only** (a test pins that it
issues no INSERT/UPDATE/DELETE), and exits non-zero when anything drifted, so it can gate a smoke test.

Run a real sync, then run this. It answers "did the sync actually land" in one place, which previously
took four tools and some squinting. Every value a rebuilt page renders is a denorm written by the sync
path or the nightly chain, and a broken writer shows up as a plausible zero rather than an error -- so
the failure mode it exists to catch is "the page looks fine and is wrong".

Checks: the four trophy-type counters against `EarnedTrophy` (signal-maintained, so drift means a
signal did not fire); `total_games` / `total_completes` / `total_trophies` against `ProfileGame` (no
cron reconciles these, so a missed write persists until the next sync); every series with a held badge
has a `SeriesBadgeStanding` row (the Collection reads standings and never live-evaluates, so a hold
without one is simply absent from the page); and contracts whose games are complete but which are not
stamped reachable (the gap `process_contracts`, nightly step 3, exists to close).

All three library totals are filter-respecting, and the two writers do not use the same filters:
`update_profile_games` honours `hide_hiddens` alone, `update_profile_trophy_counts` honours
`hide_hiddens` AND `hide_zeros`. The command mirrors both exactly, so a hunter with either toggle on
still passes cleanly. (It did not at first, and reported DRIFT on healthy profiles -- a verifier that
cries wolf gets ignored, which is worse than not having one.)

## Diagnostics

Commands for debugging and monitoring. These do not modify data (except where noted).

| Command | Purpose |
|---------|---------|
| `psn_probe` | Probe PSN API endpoints directly and dump raw payloads (sync troubleshooting) |
| `debug_signals` | Inspect signal connections for EarnedTrophy |
| `test_signals` | Verify platinum notification signal is connected |
| `test_platinum_signal` | Trigger a real platinum signal (modifies DB) |
| `force_platinum_notification` | Directly invoke the platinum notification handler |
| `audit_genre_data` | Report genre/subgenre coverage statistics |
| `measure_leaderboard` | Read-only probe for per-game leaderboard feasibility: data scale, top-20 page timing, and deep rank-lookup timing. Prod-safe (planner estimate instead of a `COUNT(*)` seq scan). Flags: `--games`, `--depth`, `--explain` |

---

## Common Patterns

### `--dry-run`

The most common flag across the codebase. When provided, the command previews what changes would be made without writing to the database. Always run with `--dry-run` first when using a command for the first time or on production data.

Commands that support `--dry-run`: `backfill_default_concepts`, `backfill_concept_slugs`, `backfill_stub_concept_icons`, `backfill_game_regions`, `backfill_subscription_periods`, `clean_titles`, `generate_monthly_recaps`, `lock_admin_concepts`, `mark_recaps_sent`, `match_game_families`, `populate_banned_words`, `populate_user_titles`, `process_scheduled_notifications`, `recalc_earn_rates`, `send_launch_announcement` (dry run is the DEFAULT there), `send_monthly_recap_emails`, `send_weekly_digest`, `sync_all_discord_roles`, `update_shovelware`.

### `--username` / `--profile`

Many commands accept a PSN username to target a single profile instead of processing all profiles. The flag name varies:

- `--username`: `populate_profilegame_stats`, 
- `--profile`: `sync_all_discord_roles`, 
- `--profile-id`: `generate_monthly_recaps`, `send_monthly_recap_emails`, `send_weekly_digest`

### `--batch-size`

Controls the number of records processed per database batch. Used by commands that handle large datasets:

- `populate_profilegame_stats`, `backfill_default_concepts`, `backfill_concept_slugs`, `backfill_stub_concept_icons`, `recalc_earn_rates`, `sync_all_discord_roles`, `send_monthly_recap_emails`, `send_weekly_digest`

### `--verbose`

Shows detailed per-record output. Used by: `backfill_game_regions`, `update_shovelware`, .

### `--commit` vs `--dry-run`

Most commands default to making changes and require `--dry-run` to preview. The exception is which defaults to dry-run mode and requires `--commit` to apply changes.

### `--force`

Skips confirmation prompts or re-processes already-completed records. Used by: `send_monthly_recap_emails` (resend to already-emailed users), `send_weekly_digest` (bypass EmailLog dedup).

---

## Related Docs

- [Local Setup](local-setup.md): Getting the development environment running
