# Redis Keys Reference

Complete map of all Redis key patterns used across PlatPursuit. The system uses a single Redis instance with two access layers: raw Redis (via `redis_client`) for the sync pipeline and Django cache (via `django.core.cache`) for application caching.

## Two Redis Layers

| Layer | Access | Prefix | Purpose |
|-------|--------|--------|---------|
| Raw Redis | `redis_client` from `trophies/util_modules/cache.py` | None (bare keys) | Job queues, sync state, locks, rate limiting, deferred notifications |
| Django Cache | `django.core.cache.cache` | `{KEY_PREFIX}:1:` (from settings) | Page data, leaderboards, notifications, analytics |

Both layers share the same Redis instance but use different key naming conventions. Raw keys are unprefixed; Django cache keys are auto-prefixed by the cache backend.

---

## Raw Redis Keys

### Job Queue System (Lists)

Workers consume from all 5 queues via priority-ordered `brpop`.

| Key | Type | TTL | Purpose |
|-----|------|-----|---------|
| `orchestrator_jobs` | List | None | Profile-level orchestration jobs (highest priority) |
| `high_priority_jobs` | List | None | Health checks, urgent re-queues |
| `medium_priority_jobs` | List | None | Title stats, title IDs, trophy groups |
| `low_priority_jobs` | List | None | Default `sync_trophies` jobs |
| `bulk_priority_jobs` | List | None | Whale profiles' `sync_trophies` jobs (lowest priority) |

**Files**: `trophies/psn_manager.py`, `trophies/token_keeper.py`

### Per-Profile Job Tracking

| Key Pattern | Type | TTL | Purpose |
|-------------|------|-----|---------|
| `profile_jobs:{profile_id}:{queue}` | String (int) | None | Count of pending jobs per profile per queue |
| `active_profiles` | Set | None | Profile IDs with at least one pending job |
| `deferred_jobs:{profile_id}` | List | 86400s (1d) | Deferred job payloads waiting for current sync to finish |

`{queue}` is one of: `low_priority`, `medium_priority`, `bulk_priority`.

**Files**: `trophies/psn_manager.py`, `trophies/token_keeper.py`

### Sync State

| Key Pattern | Type | TTL | Purpose |
|-------------|------|-----|---------|
| `sync_started_at:{profile_id}` | String (timestamp) | 7200s (2h) | Unix timestamp when sync began; used for queue position and stuck-sync detection |
| `sync_orchestrator_pending:{profile_id}` | String (flag) | 1800s (30m) | Set before orchestrator job runs; prevents stuck-sync false positives |
| `sync_queued_games:{profile_id}` | Set | 7200s (2h) | `np_communication_id` values already queued this cycle (deduplication) |
| `pending_sync_complete:{profile_id}` | String (JSON) | 21600s (6h) | `{touched_profilegame_ids, queue_name}` waiting for jobs to drain |
| `sync_complete_in_progress:{profile_id}` | String (NX lock) | 1800s (30m) | Prevents duplicate concurrent `_job_sync_complete` runs |
| `sync_complete_semaphore` | String (int) | None | Global counter of currently running sync_complete operations |
| `sync_complete_holder:{profile_id}` | String | 1800s (30m) | Per-holder lease for semaphore crash safety |
| `sync:sync_complete_max_concurrent` | String (int) | None | Configurable max concurrent sync_completes (default: 12) |

**Files**: `trophies/psn_manager.py`, `trophies/token_keeper.py`, `trophies/views/sync_views.py`

### Sync Completeness Cooldowns

| Key Pattern | Type | TTL | Purpose |
|-------------|------|-----|---------|
| `trophy_completeness_check:{profile_id}` | String | 21600s (6h) | Prevents repeated re-queuing when games have 0 Trophy records |
| `group_completeness_check:{profile_id}` | String | 21600s (6h) | Prevents repeated re-queuing when games have 0 TrophyGroup records |

**Files**: `trophies/token_keeper.py`

### Locks

| Key Pattern | Type | TTL | Purpose |
|-------------|------|-----|---------|
| `sync_trophies_lock:{np_communication_id}` | String (NX) | 120s | Per-game mutex preventing concurrent `sync_trophies` for same game |
| `shovelware_concept_lock:{concept_id}` | String (NX) | 10s | Short mutex for bulk concept-level shovelware status updates |
| `instance_lock:{machine_id}:{group_id}:{instance_id}` | String (NX) | 300s | Atomic per-instance acquisition lock for token assignment |

**Files**: `trophies/token_keeper.py`, `trophies/services/shovelware_detection_service.py`

### Token Keeper Lifecycle

| Key Pattern | Type | TTL | Purpose |
|-------------|------|-----|---------|
| `token_keeper:running:{machine_id}` | String | 3600s (refreshed every 60s) | Heartbeat proving this machine's TokenKeeper is alive |
| `token_keeper:instance:{machine_id}:{group_id}:{instance_id}:token` | String | None | Raw NPSSO token for each instance slot |
| `token_keeper:pending_refresh:{machine_id}:{group_id}:{instance_id}` | String | 3600s | Flag indicating instance needs token refresh but was busy |
| `token_keeper_latest_stats:{machine_id}` | String (JSON) | 60s | Latest stats snapshot for admin monitoring page |
| `token_keeper_stats:{machine_id}` | Pub/Sub channel | N/A | Real-time stats broadcasting channel |

**Files**: `trophies/token_keeper.py`

### API Rate Limiting (Sorted Sets)

| Key Pattern | Type | TTL | Purpose |
|-------------|------|-----|---------|
| `token:{token}:{machine_id}:timestamps` | Sorted Set | Sliding window (900s default) | Unix timestamps of API calls in rolling window; enforces `MAX_CALLS_PER_WINDOW` (300) |
| `token:{token}:timestamps` | Sorted Set | None | Simplified version without `machine_id`; used by `log_api_call()` for `calls_remaining` |

**Files**: `trophies/token_keeper.py`, `trophies/util_modules/cache.py`

### Deferred Notifications

| Key Pattern | Type | TTL | Purpose |
|-------------|------|-----|---------|
| `pending_platinum:{profile_id}:{game_id}` | String (JSON) | 7200s (2h) | Queued platinum notification; created at sync completion |
| `pending_badges:{profile_id}` | List (JSON items) | 3600s (1h) | Badge context dicts queued for consolidation |

**Files**: `notifications/services/deferred_notification_service.py`

### Site-Wide Flags

| Key Pattern | Type | TTL | Purpose |
|-------------|------|-----|---------|
| `site:high_sync_volume` | String (JSON) | 300s (refreshed while active) | `{activated_at, heavy_count}` for high sync volume banner |
| `sync:bulk_threshold` | String (int) | None (persistent) | Threshold for moving whale profiles to `bulk_priority`; default 5000 |

**Files**: `trophies/token_keeper.py`, `plat_pursuit/context_processors.py`, `trophies/management/commands/redis_admin.py`

### Leaderboard Sorted Sets

Incrementally updated via signals, fully rebuilt by `update_leaderboards` cron every 6 hours.

| Key Pattern | Type | Purpose |
|-------------|------|---------|
| `lb:xp:scores` | Sorted Set | XP leaderboard; member=profile_id, score=xp*10^4+badges |
| `lb:xp:data` | Hash | XP display data; field=profile_id, value=JSON |
| `lb:earners:{slug}:scores` | Sorted Set | Per-series earners; score=tier*10^12+(10^12-timestamp) |
| `lb:earners:{slug}:data` | Hash | Earners display data |
| `lb:progress:{slug}:scores` | Sorted Set | Per-series progress; score=plats*10^9+golds*10^6+silvers*10^3+bronzes |
| `lb:progress:{slug}:data` | Hash | Progress display data |
| `lb:progress:global:scores` | Sorted Set | Global progress leaderboard |
| `lb:progress:global:data` | Hash | Global progress display data |
| `lb:community_xp:{slug}` | String (int) | Community XP total per series, INCRBY delta from gamification updates |
| `lb:meta:last_rebuild` | Hash | Rebuild timestamps per leaderboard key |

**Files**: `trophies/services/redis_leaderboard_service.py`, `trophies/services/xp_service.py`, `trophies/signals.py`

---

## Django Cache Keys

Django auto-prefixes all keys with `{KEY_PREFIX}:1:` from settings. The patterns below show application-level names before prefixing.

### Homepage (Cron-Managed)

| Key Pattern | TTL | Purpose |
|-------------|-----|---------|
| `community_stats_{YYYY-MM-DD}_{HH}` | 7200s | Community stats dict; hourly rotation |
| `featured_games_{YYYY-MM-DD}` | 172800s (2d) | Featured games list; daily rotation |
| `featured_guide_{YYYY-MM-DD}` | 86400s (1d) | Featured guide concept ID |
| `latest_badges_{YYYY-MM-DD}_{HH}` | 7200s | Latest badge awards; hourly rotation |
| `playing_now_{YYYY-MM-DD}` | 172800s (2d) | Currently playing profiles list |
| `featured_badges_{YYYY-MM-DD}` | 172800s (2d) | Featured badges list; daily rotation |
| `featured_checklists_{YYYY-MM-DD}` | 172800s (2d) | Featured checklists list; daily rotation |
| `whats_new_{YYYY-MM-DD}_{HH}` | 7200s | What's New sidebar content; hourly rotation |

**Files**: `core/views.py`, `core/management/commands/refresh_homepage_daily.py`, `core/management/commands/refresh_homepage_hourly.py`

All homepage keys use 2x TTL as safety margin (cron refreshes before expiry). Date/hour keying ensures seamless rotation without stale data windows.

### Game Detail Page

| Key Pattern | TTL | Purpose |
|-------------|-----|---------|
| `game:imageurls:{np_communication_id}` | `CACHE_TIMEOUT_IMAGES` | Image URLs (background, screenshots, content rating) |
| `game:trophygroups:{np_communication_id}` | 604800s (1 week) | Trophy group names and defined counts |
| `game:stats:{np_communication_id}:{YYYY-MM-DD}:{HH}` | 3600s (1h) | Game stats (owners, completers, average progress) |
| `featured_guide:{YYYY-MM-DD}` | 86400s (1d) | Featured guide concept for the guide list page |

**Files**: `trophies/views/game_views.py`, `trophies/models.py`

### Community Hub / Ratings

| Key Pattern | TTL | Purpose |
|-------------|-----|---------|
| `concept:averages:{concept_id}` | 3600s | Aggregated rating averages across all dimensions |
| `concept:averages:{concept_id}:group:{group_id}` | 3600s | Rating averages scoped to a DLC/trophy group |
| `review:recommend:{concept_id}:{group_id}` | 1800s | Recommendation stats `{recommended, not_recommended, total, percent}` |

**Files**: `trophies/services/rating_service.py`, `trophies/services/review_service.py`

### Comments and Checklists

| Key Pattern | TTL | Purpose |
|-------------|-----|---------|
| `comments:concept:{concept_id}` | Invalidate-on-write | Concept-level comment list |
| `comments:concept:{concept_id}:trophy:{trophy_id}` | Invalidate-on-write | Trophy-level comment list |
| `comments:concept:{concept_id}:checklist:{checklist_id}` | Invalidate-on-write | Checklist-level comment list |
| `checklists:concept:{concept_id}` | Invalidate-on-write | Checklists for a concept |
| `banned_words:active` | 300s (5m) | Active banned word list `[{word, use_word_boundaries}]` |

**Files**: `trophies/services/comment_service.py`, `trophies/services/checklist_service.py`

### Profile Timeline

| Key Pattern | TTL | Purpose |
|-------------|-----|---------|
| `profile:timeline:{profile_id}` | 3600s (invalidated on sync) | Serialized timeline events; deleted by `invalidate_timeline_cache()` |

**Files**: `trophies/services/timeline_service.py`

### Notifications

| Key Pattern | TTL | Purpose |
|-------------|-----|---------|
| `notification:unread_count:{user_id}` | 300s (5m) | Unread notification count |
| `notification:recent:{user_id}` | 60s (1m) | Recent notification dicts for dropdown |

**Files**: `notifications/services/notification_cache_service.py`

### Dashboard Modules

| Key Pattern | TTL | Purpose |
|-------------|-----|---------|
| `dashboard:mod:{module_slug}:{profile_id}` | Per-module `cache_ttl` (default 600s) | Lazy-loaded module data; deleted by `invalidate_dashboard_cache()` |

**Files**: `trophies/services/dashboard_service.py`

### Moderation (Context Processors)

| Key Pattern | TTL | Purpose |
|-------------|-----|---------|
| `mod:pending_reports_count` | 60s | Pending CommentReport count (staff navbar) |
| `mod:pending_proposals_count` | 60s | Pending GameFamilyProposal count (superuser navbar) |

**Files**: `plat_pursuit/context_processors.py`

### Fundraiser

| Key Pattern | TTL | Purpose |
|-------------|-----|---------|
| `fundraiser:active_banner` | 60s | Active fundraiser PK (or 0 sentinel) for banner |

**Files**: `plat_pursuit/context_processors.py`

### Analytics

| Key Pattern | TTL | Purpose |
|-------------|-----|---------|
| `analytics_session:{session_uuid}` | 1800s (30m, sliding) | Session metadata dict for page view tracking |

**Files**: `core/services/session_tracking.py`

### PayPal Integration

| Key Pattern | TTL | Purpose |
|-------------|-----|---------|
| `paypal_access_token:{PAYPAL_MODE}` | ~8h (`min(expires_in - 300, 28800)`) | PayPal OAuth access token |
| `paypal_webhook:{transmission_id}` | 604800s (7d) | Webhook idempotency key |

**Files**: `users/services/paypal_service.py`, `users/views.py`

---

## Redis Admin Flush Commands

The `redis_admin.py` management command provides targeted flush operations for operational debugging.

| Flag | Keys Flushed |
|------|-------------|
| `--flush-index` | All homepage keys: `featured_games_*`, `featured_guide_*`, `playing_now_*`, `featured_badges_*`, `featured_checklists_*`, `whats_new_*`, `latest_badges_*` |
| `--flush-game-page {np_id}` | `game:imageurls:{np_id}`, `game:trophygroups:{np_id}`, `game:stats:{np_id}:*` |
| `--flush-token-keeper` | All 5 job queues + `profile_jobs:*`, `deferred_jobs:*`, `pending_sync_complete:*`, `sync_started_at:*`, `sync_trophies_lock:*`, `shovelware_concept_lock:*`, `sync_orchestrator_pending:*`, `sync_queued_games:*`, `sync_complete_in_progress:*`, `sync_complete_holder:*`, `sync_complete_semaphore`, `active_profiles`, `site:high_sync_volume` |
| `--get-sync-complete-max` | (read-only) Shows current max concurrent sync_complete setting and active count |
| `--set-sync-complete-max N` | Sets `sync:sync_complete_max_concurrent` to N (takes effect immediately, no restart needed) |
| `--flush-complete-lock {profile_id}` | `pending_sync_complete:{id}`, `sync_started_at:{id}`, `sync_orchestrator_pending:{id}`, `sync_queued_games:{id}`, `sync_complete_in_progress:{id}` |
| `--flush-dashboard {profile_id}` | `dashboard:mod:{slug}:{id}` for each registered module |
| `--flush-concept {concept_id}` | Game page keys for all games under the concept |
| `--flush-community` | `review:recommend:*`, `concept:averages:*:group:*` |

**File**: `trophies/management/commands/redis_admin.py`

## Gotchas and Pitfalls

- **Two key namespaces**: Raw Redis keys and Django cache keys live on the same Redis instance but are NOT interchangeable. Use `redis_client` for raw keys and `cache.get/set` for Django cache keys. Using the wrong client will silently miss keys.
- **Django cache prefix**: Django auto-prefixes keys with `{KEY_PREFIX}:1:`. When debugging with `redis-cli`, you'll see something like `:1:community_stats_2024-01-15_14`, not the bare application key.
- **NX locks are not reentrant**: `sync_trophies_lock` and `sync_complete_in_progress` use Redis `SET NX` (set-if-not-exists). If a process crashes without releasing, the TTL is the only recovery mechanism.
- **Sliding window rate limits**: The `token:*:timestamps` sorted sets use `zremrangebyscore` to remove old entries, not TTL-based expiry. They grow unbounded if `zremrangebyscore` stops being called.
- **Pub/Sub is fire-and-forget**: `token_keeper_stats:{machine_id}` is a Pub/Sub channel, not a stored key. Messages are lost if no subscriber is listening.
- **Date-keyed cache rotation**: Homepage keys like `community_stats_{date}_{hour}` use 2x TTL as a safety margin. The cron job writes the new key before the old one expires, ensuring seamless transitions.
- **Invalidate-on-write keys**: Comment and checklist caches have no TTL. They persist until explicitly deleted by the service layer when data changes. If the deletion call is missed, stale data persists indefinitely.
- **redis_admin flush is destructive**: `--flush-token-keeper` kills all active sync jobs. Only use when workers are stopped or you intend to reset the entire sync pipeline.

## Related Docs

- [Token Keeper](../architecture/token-keeper.md): Job queue system, sync state machine, rate limiting
- [Notification System](../architecture/notification-system.md): Deferred notification Redis keys
- [Badge System](../architecture/badge-system.md): Leaderboard cache keys
- [Homepage Services](homepage-services.md): Featured content cache patterns
- [Management Commands](../guides/management-commands.md): `redis_admin` command usage
- [Settings Overview](settings-overview.md): Redis connection configuration
