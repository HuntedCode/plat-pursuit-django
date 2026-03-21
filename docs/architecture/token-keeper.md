# Token Keeper & Sync Pipeline

The Token Keeper is PlatPursuit's core background engine for synchronizing PSN trophy data. It manages a pool of authenticated PSN API tokens, distributes work across a multi-priority Redis job queue, and orchestrates the full lifecycle of profile syncs: from fetching a user's trophy list to evaluating badges, milestones, and challenge progress. The system runs as a long-lived singleton process, spawning worker threads that continuously pull jobs from Redis queues, acquire token instances, make PSN API calls, and persist results to PostgreSQL.

## Architecture Overview

### Design Philosophy

The Token Keeper is a **single-process, multi-threaded** worker system (not Celery-based). This design was chosen because PSN API tokens are stateful objects (they hold OAuth access/refresh tokens in memory) that must be shared across concurrent jobs without serialization overhead. Running everything in one process with thread-level concurrency avoids the complexity of distributing token state across multiple Celery workers.

### Data Flow

```
User triggers sync (web UI, cron, admin)
        |
        v
   PSNManager.initial_sync() / profile_refresh()
        |
        v
   Redis job queues (5 priority levels)
        |
        v
   TokenKeeper._job_worker_loop() threads (3 per token group)
        |
        v
   _get_instance_for_job() -> acquires idle TokenInstance
        |
        v
   _execute_api_call() -> PSN API via psnawp library
        |
        v
   PsnApiService -> Django ORM (create/update models)
        |
        v
   _complete_job() -> decrement counters, trigger sync_complete when all jobs finish
        |
        v
   _job_sync_complete() -> badges, milestones, challenges, notifications
```

### Key Decisions

- **Thread-safe rate limiting**: Uses `pyrate_limiter.InMemoryBucket` instead of the psnawp default SQLite bucket, which caused "database is locked" errors in multi-threaded environments.
- **Token release per API call**: Tokens are released in the `finally` block of `_execute_api_call()`, not after the entire job completes. This maximizes token availability since most jobs make only 1-2 API calls but spend significant time on DB writes.
- **Two-phase job assignment**: Orchestrator jobs (`sync_trophy_titles`, `profile_refresh`) first count all needed jobs, set the progress target, then assign jobs. This prevents the progress bar from appearing to go backwards.
- **Deadlock resilience**: Deadlocked jobs are re-queued with a 2-second delay. If the DB lock error rate exceeds a configurable threshold, the entire TokenKeeper restarts itself.

## File Map

| File | Purpose |
|------|---------|
| `trophies/token_keeper.py` | Core engine: singleton, token pool, worker threads, all job implementations (~1,846 lines) |
| `trophies/psn_manager.py` | Public facade for queuing jobs into Redis. All external code calls PSNManager, never TokenKeeper directly (~135 lines) |
| `trophies/services/psn_api_service.py` | Data layer: transforms PSN API responses into Django model creates/updates (~657 lines) |
| `trophies/sync_utils.py` | Thread-local context manager to suppress EarnedTrophy pre_save signals during sync (~42 lines) |
| `trophies/util_modules/cache.py` | Redis client singleton, API audit logging helper (~92 lines) |
| `trophies/management/commands/start_token_keeper.py` | Management command to launch the TokenKeeper process |
| `trophies/management/commands/token_keeper_control.py` | Management command for start/stop/restart operations |
| `trophies/management/commands/redis_admin.py` | Redis admin utilities: flush queues, move whale jobs, set bulk thresholds |

## Key Concepts

### Token Pool

The system authenticates with PSN using NPSSO cookies (long-lived session tokens from PlayStation Network). Each NPSSO cookie creates a `ProxiedPSNAWP` client that holds an OAuth access token (short-lived, ~1 hour) and a refresh token (longer-lived). These are wrapped in `TokenInstance` dataclass objects that track:

- `is_busy`: Whether a worker thread currently holds this instance
- `access_expiry` / `refresh_expiry`: Token lifetimes for proactive refresh
- `user_cache`: Per-instance LRU cache of PSN user lookups (keyed by account_id, 5-minute TTL)
- `outbound_ip`: The IP address used for outbound requests (for proxy tracking)
- `job_start_time`: When the current job started (for stuck detection)

### Worker Groups

Tokens are organized into **groups** of exactly 3 tokens each, configured via the `TOKEN_GROUPS` environment variable (pipe-separated groups, comma-separated tokens within each group). Each group can optionally route through a different proxy IP via `PROXY_IPS`. The system spawns 3 worker threads per group, so with 2 groups you get 6 worker threads sharing 6 token instances.

### Machine IDs

The `MACHINE_ID` environment variable enables running multiple TokenKeeper instances on different machines without conflict. Redis keys for instance locks, running state, and rate limit windows are all namespaced by machine ID. A Redis key `token_keeper:running:{machine_id}` prevents accidental double-starts on the same machine.

### Orchestrator Pattern

Sync operations use a two-tier orchestrator pattern:

1. **Orchestrator jobs** (`sync_trophy_titles`, `profile_refresh`) run first. They page through the PSN API to discover all games for a profile, create Game/ProfileGame records, then fan out hundreds of child jobs.
2. **Child jobs** (`sync_trophies`, `sync_trophy_groups`, `sync_title_stats`, `sync_title_id`) each handle one game. As each completes, `_complete_job()` decrements a per-profile counter.
3. **Completion job** (`sync_complete`) fires automatically when all child job counters reach zero. It runs health checks, badge evaluation, milestone checks, and challenge progress.

### Job Types

| Job Type | Queue | Description |
|----------|-------|-------------|
| `sync_profile_data` | orchestrator | Fetch PSN profile (username, avatar, trophy level, region) |
| `sync_trophy_titles` | orchestrator | Page through all trophy titles, create games, fan out child jobs |
| `profile_refresh` | orchestrator | Incremental sync: only titles updated since `last_synced` |
| `sync_complete` | orchestrator | Post-sync: health check, stats, badges, milestones, challenges |
| `check_profile_health` | high_priority | Verify profile accessibility |
| `handle_privacy_error` | high_priority | Handle PSN privacy settings blocking access |
| `sync_trophy_groups` | medium_priority | Fetch DLC/group metadata for a single game |
| `sync_title_stats` | medium_priority | Fetch play time, play count, first/last played for title IDs |
| `sync_title_id` | medium_priority | Resolve title ID to Concept (game metadata, media, region) |
| `sync_trophies` | low_priority | Fetch all trophies + earned status for a single game |
| `sync_trophies` (whale) | bulk_priority | Same as above but for profiles exceeding the bulk threshold |

## Key Flows

### Full Profile Sync Flow

1. **Trigger**: User links PSN account, or cron triggers refresh for stale profiles.
2. **Entry point**: `PSNManager.initial_sync(profile)` sets `sync_status='syncing'`, resets progress counters, sets a `sync_orchestrator_pending` flag, and queues two orchestrator jobs: `sync_profile_data` and `sync_trophy_titles`.
3. **sync_profile_data**: Calls `get_profile_legacy` and `get_region` PSN endpoints. Updates profile username, avatar, trophy level, region/country. Handles duplicate account_id detection and automatic profile merging.
4. **sync_trophy_titles**: Pages through all trophy titles (400 per page). For each title:
   - Creates or updates the `Game` record
   - Creates or updates the `ProfileGame` record
   - Checks if trophy groups need syncing (new game or trophy count changed)
   - Two-phase: first pass counts jobs and sets the progress target, second pass queues the jobs
   - Stores `pending_sync_complete` data in Redis with the list of touched ProfileGame IDs
5. **sync_trophy_groups** (per game, if needed): Fetches DLC/group structure, creates `TrophyGroup` records, syncs concept-level trophy groups for the Community Hub.
6. **sync_trophies** (per game): Fetches all trophies with earned status. Processes in batches of 50 within `transaction.atomic()` and `sync_signal_suppressor()`. Creates/updates `Trophy` and `EarnedTrophy` records. Triggers shovelware detection for platinums. Creates deferred platinum notifications.
7. **sync_title_stats** (paginated): Fetches play statistics (play time, play count). Maps title IDs to games. For unresolved title IDs, calls `trophy_titles_for_title` to discover the np_communication_id mapping, then queues `sync_title_id` jobs.
8. **sync_title_id** (per title ID): Calls `game_title` to get concept details (publisher, genres, media, release date). Creates or updates `Concept` records. Assigns concepts to games via `Game.add_concept()`. Detects Asian-language regional titles. Falls back to `Concept.create_default_concept()` on any failure.
9. **_complete_job**: After each child job, decrements `profile_jobs:{profile_id}:{queue}`. When all counters reach zero and `pending_sync_complete` exists, queues `sync_complete`.
10. **sync_complete**: The finalization pipeline:
    - Calls `trophy_summary` to get PSN's authoritative trophy counts
    - Compares against local `EarnedTrophy` counts to detect drift
    - If mismatch found: re-queues sync jobs for affected games, sets up a follow-up `pending_sync_complete`, and returns early
    - Checks Trophy/TrophyGroup completeness (games with 0 records despite having defined trophies)
    - Calls `update_plats()`, `update_profilegame_stats()`, `check_profile_badges()`
    - Creates consolidated badge notifications via `DeferredNotificationService`
    - Checks milestones (excluding challenge-specific types)
    - Checks A-Z challenge, Calendar challenge, and Genre challenge progress
    - Updates trophy counts, invalidates timeline cache
    - Sets `sync_status='synced'`

### Profile Refresh Flow

`PSNManager.profile_refresh()` is called for profiles that are already synced. Unlike `initial_sync`, it uses an incremental strategy:

1. Pages through trophy titles until it finds one with `last_updated_datetime <= last_synced`
2. Only queues sync jobs for games that changed since last sync
3. Similarly pages through title stats only until reaching already-synced entries
4. Uses `medium_priority` queue instead of `low_priority` for faster processing
5. If zero changed games are found, triggers `sync_complete` immediately rather than waiting for counter-based completion

### Token Refresh Flow

1. The health monitor thread (`_health_loop`) runs every 60 seconds.
2. For each token instance, `_check_and_refresh()` checks if the access token expires within `refresh_threshold` (300 seconds / 5 minutes).
3. If refresh is needed but the instance is busy, a `pending_refresh` flag is set in Redis and the refresh is deferred.
4. If the instance is idle, it creates a new `ProxiedPSNAWP` client from the NPSSO cookie, which performs a full re-authentication flow (new access + refresh tokens).
5. The `refresh_expiry` is intentionally only set on first initialization to prevent dashboard display issues where all instances show identical expiry times after proactive refreshes.
6. Cache cleanup (`cleanup_cache(ttl_minutes=5)`) removes stale user lookups on every health check.

### Job Queue System

**Queue Architecture**: Five Redis lists serve as FIFO queues, polled via `BRPOP` in priority order:

```
orchestrator_jobs    (highest priority)
high_priority_jobs
medium_priority_jobs
low_priority_jobs
bulk_priority_jobs   (lowest priority)
```

`BRPOP` pops from the first non-empty list in the provided order, giving natural priority scheduling.

**Deduplication**: `PSNManager.assign_sync_trophies()` uses a Redis set `sync_queued_games:{profile_id}` to prevent the same game from being queued twice within a sync cycle (2-hour TTL). Additionally, `_job_sync_trophies()` acquires a per-game Redis lock `sync_trophies_lock:{np_communication_id}` to prevent concurrent execution.

**Per-Profile Job Counting**: Only "counted" queues (`low_priority`, `medium_priority`, `bulk_priority`) increment/decrement per-profile counters (`profile_jobs:{profile_id}:{queue}`). Orchestrator and high-priority jobs are excluded from counting because they are structural/control-flow jobs, not unit-of-work jobs.

**Bulk Priority**: Profiles with more than `sync:bulk_threshold` (default: 5000) total jobs are automatically routed to `bulk_priority` to prevent "whale" accounts from starving normal users. The `redis_admin --move-whale-jobs` command can retroactively move jobs from low to bulk priority.

**Sync Completion Detection**: When all per-profile counters reach zero and a `pending_sync_complete:{profile_id}` key exists, `_complete_job()` triggers the `sync_complete` job. An atomic guard (`sync_complete_in_progress:{profile_id}`) ensures only one sync_complete runs per profile at a time.

## PSN API Service

`PsnApiService` is a stateless service class that translates between PSN API data structures (from the `psnawp_api` library) and Django models. It is called exclusively by TokenKeeper job methods.

### Endpoints Called

| Endpoint | PSN API Method | Used By |
|----------|---------------|---------|
| `get_profile_legacy` | `user.get_profile_legacy()` | `sync_profile_data` |
| `get_region` | `user.get_region()` | `sync_profile_data` |
| `trophy_titles` | `user.trophy_titles()` | `sync_trophy_titles`, `profile_refresh`, `sync_complete` (health check) |
| `title_stats` | `user.title_stats()` | `sync_title_stats`, `profile_refresh` |
| `trophies` | `user.trophies()` | `sync_trophies` |
| `trophy_groups_summary` | `user.trophy_groups_summary()` | `sync_trophy_groups` |
| `trophy_titles_for_title` | `user.trophy_titles_for_title()` | `sync_title_stats` (title ID resolution) |
| `trophy_summary` | `user.trophy_summary()` | `sync_complete` (health check) |
| `game_title` | `client.game_title()` | `sync_title_id` (concept resolution) |

### Rate Limiting

Rate limiting operates at two levels:

1. **Per-request**: `pyrate_limiter` with `InMemoryBucket` enforces a 1-request-per-3-seconds rate at the HTTP session level (configured in `ProxiedPSNAWP`).
2. **Per-token rolling window**: A Redis sorted set `token:{token}:{machine_id}:timestamps` tracks API call timestamps. `_get_calls_in_window()` counts calls within the configurable window (default: 300 calls per 900 seconds). Instance selection prioritizes tokens with fewer recent calls.
3. **Rollback on error**: When an API call fails with `HTTPError` or `PSNAWPForbiddenError`, `_rollback_call()` removes the most recent timestamp entry so the failed call does not count against the rate limit budget.
4. **429 handling**: `_handle_rate_limit()` parks the affected instance for 60 seconds by setting `last_health = 0` (making it ineligible for selection) and then restoring it.

### Error Handling

- **Network errors** (`ConnectionError`, `Timeout`): Retried up to 10 times with exponential backoff (4s to 30s) via `@retry` decorator on `_execute_api_call()`.
- **PSN privacy errors** (`PSNAWPForbiddenError`): Sets `psn_history_public = False` and queues a `handle_privacy_error` job that waits 3 seconds then checks if the flag persisted.
- **HTTP errors**: Logged to `APIAuditLog`, rate limit counter rolled back, exception re-raised.
- **Database deadlocks**: Re-queued with a 2-second delay. Accumulated via `_record_db_lock_error()`. If the threshold is exceeded within the time window, `_initiate_restart()` gracefully shuts down all threads, waits for DB recovery, and reinitializes.

### Proxy Support

Each token group can route through a different proxy via the `PROXY_IPS` environment variable (pipe-separated, matching the group order). The proxy URL is passed to `ProxiedRequestBuilder` which sets `session.proxies`. On initialization and refresh, each instance resolves its outbound IP via `api.ipify.org` for dashboard display.

### Duplicate Profile Detection

`PsnApiService.update_profile_from_legacy()` includes automatic duplicate detection. If a profile's PSN `account_id` matches an existing profile in the database, the system:
1. Transfers user and Discord linkages from the duplicate to the existing profile
2. Continues the sync on the existing profile
3. Attempts safe deletion of the duplicate (only if it has no trophy data or linked accounts)

This handles PSN username changes where a user might have been tracked under their old username.

## Integration Points

### Badge Evaluation

After sync completion, `check_profile_badges(profile, touched_profilegame_ids)` evaluates all badge criteria using a stage completion cache (pre-built in `_build_badge_context()` to avoid N+1 queries). Badge XP is awarded via the gamification system.

### Challenge Progress

Three challenge systems are checked in `_job_sync_complete()`:

- **A-Z Challenge** (`check_az_challenge_progress`): Checks if any new platinums match assigned A-Z slots. Uses `bulk_update` for efficiency.
- **Calendar Challenge** (`check_calendar_challenge_progress`): Checks if platinum earn dates fill calendar days. Early-exits if no new plats since `challenge.updated_at`.
- **Genre Challenge** (`check_genre_challenge_progress`): Checks platinum progress against genre-specific requirements.

### Deferred Notifications

Platinum and badge notifications are not sent immediately during sync. Instead:

1. During `sync_trophies`, platinum notifications are queued to Redis via `DeferredNotificationService.queue_platinum_notification()` (only for plats earned within the last 2 days).
2. After each game's trophies are synced, `create_platinum_notification_for_game()` creates the actual notification from the queued data.
3. Badge notifications are accumulated during badge evaluation and consolidated in `sync_complete` via `create_badge_notifications()` (highest tier only per series).

### Signal Suppression

The `sync_signal_suppressor()` context manager in `sync_utils.py` uses a thread-local flag to disable the `EarnedTrophy` pre_save signal during sync. This signal normally fires a SELECT query per save to track the previous `earned` value for notification detection. During sync, this is redundant because:
- Platinum notifications are handled by `DeferredNotificationService`
- Earned-flip detection is handled directly in `create_or_update_earned_trophy_from_trophy_data()`

The signal handler checks `is_sync_signal_suppressed()` and skips the expensive query when suppression is active.

### Shovelware Detection

When a platinum trophy is created or updated, `ShovelwareDetectionService.evaluate_game(game)` is called to classify the game. The game object is then refreshed from DB (`game.refresh_from_db()`) because the service uses queryset `.update()` which does not modify the in-memory instance. Downstream notification logic checks `game.is_shovelware` to suppress notifications for shovelware titles.

### Discord Notifications

`notify_new_platinum(profile, earned_trophy)` sends a Discord webhook notification when a platinum is earned. This is called directly during trophy sync (not deferred) but only for profiles with a linked Discord account, non-shovelware games, and platinums earned within the last 2 days.

### ConceptTrophyGroup Sync

During `sync_trophy_groups`, after creating TrophyGroup records for a game, `ConceptTrophyGroupService.sync_for_concept()` is called to create/update concept-level trophy group mappings used by the Community Hub rating system.

### High Sync Volume Banner

The health loop calls `_check_high_sync_volume()` which uses hysteresis logic to set/clear a `site:high_sync_volume` Redis key. This drives a site-wide banner informing users that sync times may be longer than usual. Activation threshold: 10+ profiles with 200+ pending jobs. Deactivation threshold: fewer than 5 such profiles.

### PSN Outage Circuit Breaker

Detects PSN infrastructure outages (502/503/504 errors) and prevents profiles from being incorrectly marked as errored for systemic problems.

**Detection**: Each 5xx gateway error from `_execute_api_call()` records a timestamp to the `psn:5xx_timestamps` Redis sorted set. When 5 or more errors accumulate within 60 seconds, the circuit breaker trips: `site:psn_outage` is set in Redis and `_psn_outage_active` is set in memory.

**Behavior when open**:
- `_execute_api_call()` short-circuits immediately with `PSNOutageError` (no API call made)
- Job handlers catch `PSNOutageError` and apply outage recovery instead of setting error status
- Outage recovery follows the deadlock pattern: backdate `last_synced` by 10 days, set `sync_status='synced'`, so the cron picks profiles up after recovery
- `PSNManager.initial_sync()` and `profile_refresh()` skip queueing during outage
- `refresh_profiles` cron exits early
- Manual sync triggers return 503 with a friendly message
- Verification flows return 503 with PSN-down messages
- A site-wide error banner is displayed via the `psn_outage` context processor

**Recovery**: The health loop calls `_check_psn_outage()` every 60 seconds. While the outage flag is active, it probes PSN with a lightweight `trophy_summary` call for a known synced profile. If the probe succeeds, the circuit breaker resets: Redis flags are cleared, the in-memory flag is set to False, and the banner disappears. Profiles with backdated `last_synced` are picked up by the `refresh_profiles` cron on its next run.

**Manual override**: `python manage.py redis_admin --clear-psn-outage` clears the flag immediately.

## Gotchas and Pitfalls

### SQLite Bucket in psnawp

`ProxiedRequestBuilder` and `ProxiedAuthenticator` intentionally **do NOT call `super().__init__()`**. The base classes create an SQLite-backed rate limiter bucket which spawns a daemon Leaker thread. In a multi-threaded environment, this causes "database is locked" errors. The proxied subclasses replicate the parent initialization manually with `InMemoryBucket` instead.

### Token Refresh Expiry Display Bug

`TokenInstance.update_expiry_times()` only sets `refresh_expiry` on first initialization, not on subsequent refreshes. This is intentional: proactive access token refreshes create a new `ProxiedPSNAWP` client which recomputes `refresh_token_expires_at` from `time.time()`, causing all instances to show identical refresh expiry values on the dashboard. The workaround preserves the original expiry.

### PSPC Platform Handling

Several jobs contain the pattern: `game.title_platform[0] if not game.title_platform[0] == 'PSPC' else game.title_platform[1]`. PSPC (PlayStation PC) is not a valid platform for PSN API calls. When a game's first platform is PSPC, the code falls back to the second platform entry (typically PS4 or PS5).

### sync_complete Atomic Guard

Only one `sync_complete` can run per profile at a time, enforced by the `sync_complete_in_progress:{profile_id}` Redis key with `nx=True`. If a second sync_complete is triggered while one is running, the pending data is stored and the duplicate is skipped. The follow-up sync_complete will be triggered by `_complete_job()` when it detects the pending key after the first one finishes.

### Deadlock Recovery Behavior

When a deadlock occurs in `sync_complete`, the profile is NOT marked as `error`. Instead, `last_synced` is backdated by 10 days and `sync_status` is set to `synced`. This causes the cron job to pick the profile up for a normal `profile_refresh` on its next run, avoiding the need for manual intervention.

### Progress Bar Race Condition Prevention

The two-phase job assignment pattern (count first, set target, then assign) prevents a race condition where `_complete_job()` could detect zero remaining jobs and trigger `sync_complete` before all jobs have been assigned. The `sync_orchestrator_pending` flag provides additional protection during the gap between the orchestrator job being queued and actually executing.

### Concept Assignment Fallback Chain

`_job_sync_title_id()` has a multi-layered fallback to ensure every game gets a concept:
1. Normal path: resolve via PSN `game_title` API, create Concept from details
2. Error code returned: detect Asian language from title, create default concept (`PP_N` format)
3. `game_title` returns None: same language detection + default concept
4. Exception thrown: exception recovery block checks `game.concept is None` and creates default concept
5. Health check (in sync_complete): creates default concept for any game discovered without one

This defensive approach ensures no game is left without a concept, which would break downstream features (badges, Community Hub, etc.).

### Token Instance Selection and Locking

`_get_instance_for_job()` uses a Redis SET NX lock (`instance_lock:{machine_id}:{group_id}:{inst_id}`) with a 5-minute expiry as a safety net. After acquiring the Redis lock, it double-checks `inst.is_busy` in memory. If the in-memory flag was already set by another thread, the Redis lock is released immediately. This two-layer locking prevents both cross-machine and cross-thread conflicts.

### DB Connection Management

Worker threads rely on Django's `CONN_MAX_AGE=600` for connection lifecycle management. Django automatically closes and reopens connections older than 600 seconds via `ensure_connection()`. With N worker threads, this means N persistent DB connections recycled every 10 minutes.

**History**: Previously, every job worker iteration called `connection.close()` in the `finally` block to prevent pool exhaustion. This was removed because it forced a new TCP+TLS handshake per job, causing significant CPU overhead on the database at scale (24 workers = dozens of TLS handshakes/second).

### Stuck Syncing Detection (Single-Instance)

The `_check_stuck_syncing_profiles()` health loop check uses a Redis lock (`stuck_sync_check_lock`, 90s TTL, NX) to ensure only one TokenKeeper instance runs the check per cycle, preventing duplicate `sync_complete` job assignments when multiple TK instances are active.

### API Audit Log IP Lookup

`log_api_call()` in `cache.py` makes a synchronous HTTP call to `api.ipify.org` on every successful API call to record the outbound IP. This adds latency to every API call and could cause issues if the ipify service is slow or down. The call is skipped for error cases.

### Trophy Sync Per-Game Lock

`_job_sync_trophies()` acquires a Redis lock `sync_trophies_lock:{np_communication_id}` before executing. This prevents concurrent sync_trophies for the same game (which can happen when health-check re-queuing dispatches multiple jobs for games sharing a concept), avoiding AB/BA deadlocks in `ShovelwareDetectionService`'s concept-sibling updates.

## Management Commands

### `start_token_keeper`

```bash
python manage.py start_token_keeper
```

Launches the TokenKeeper singleton process. Blocks forever with a sleep loop, printing stats every 60 seconds. Registers SIGINT/SIGTERM handlers for graceful shutdown (cleans up Redis state). This is the primary way to run TokenKeeper in production.

### `token_keeper_control`

```bash
python manage.py token_keeper_control --start
python manage.py token_keeper_control --stop
python manage.py token_keeper_control --restart
```

Alternative control mechanism for the singleton. Mutually exclusive flags. Note: since TokenKeeper is a singleton, `--start` on an already-running instance will error.

### `redis_admin`

Key TokenKeeper-related operations:

```bash
# Flush all queues, profile jobs, locks, deferred jobs, active profiles
python manage.py redis_admin --flush-token-keeper

# Flush locks and pending state for a specific profile
python manage.py redis_admin --flush-complete-lock <profile_id>

# Get/set the bulk priority threshold
python manage.py redis_admin --get-bulk-threshold
python manage.py redis_admin --set-bulk-threshold 3000

# Move whale profile jobs from low_priority to bulk_priority
python manage.py redis_admin --move-whale-jobs
```

## Cache Keys / Redis Keys

### Token & Instance State

| Key Pattern | Type | TTL | Description |
|-------------|------|-----|-------------|
| `token_keeper:running:{machine_id}` | string | 3600s | Singleton guard, refreshed every health loop |
| `token_keeper:instance:{machine_id}:{group_id}:{inst_id}:token` | string | none | Stores raw NPSSO token for each instance |
| `token_keeper:pending_refresh:{machine_id}:{group_id}:{inst_id}` | string | 3600s | Flag: instance needs refresh but was busy |
| `token_keeper_stats:{machine_id}` | pub/sub channel | n/a | Real-time stats broadcast |
| `token_keeper_latest_stats:{machine_id}` | string (JSON) | 60s | Latest stats snapshot for dashboard polling |
| `instance_lock:{machine_id}:{group_id}:{inst_id}` | string | 300s | Atomic token acquisition lock |

### Rate Limiting

| Key Pattern | Type | TTL | Description |
|-------------|------|-----|-------------|
| `token:{token}:{machine_id}:timestamps` | sorted set | auto-pruned | Rolling window of API call timestamps |

### Job Queues

| Key Pattern | Type | Description |
|-------------|------|-------------|
| `orchestrator_jobs` | list | Highest priority queue |
| `high_priority_jobs` | list | Health checks, privacy errors |
| `medium_priority_jobs` | list | Trophy groups, title stats, title IDs |
| `low_priority_jobs` | list | Normal sync_trophies |
| `bulk_priority_jobs` | list | Whale account sync_trophies |

### Per-Profile Sync State

| Key Pattern | Type | TTL | Description |
|-------------|------|-----|-------------|
| `profile_jobs:{profile_id}:{queue}` | string (int) | none | Count of pending jobs per queue per profile |
| `active_profiles` | set | none | Set of profile IDs with pending jobs |
| `pending_sync_complete:{profile_id}` | string (JSON) | 21600s (6h) | Stores `touched_profilegame_ids` and `queue_name` for deferred sync_complete |
| `sync_started_at:{profile_id}` | string (timestamp) | 7200s (2h) | When the sync began (for grace period in stuck detection) |
| `sync_orchestrator_pending:{profile_id}` | string | 1800s (30m) | Flag: orchestrator job queued but not yet executed |
| `sync_complete_in_progress:{profile_id}` | string | 1800s (30m) | Atomic guard: prevents concurrent sync_complete |
| `sync_queued_games:{profile_id}` | set | 7200s (2h) | Dedup set of np_communication_ids already queued |
| `sync_trophies_lock:{np_communication_id}` | string | 120s | Per-game lock to prevent concurrent sync_trophies |
| `deferred_jobs:{profile_id}` | list | 86400s (1d) | Deferred jobs for later execution |

### Sync Health & Completeness

| Key Pattern | Type | TTL | Description |
|-------------|------|-----|-------------|
| `trophy_completeness_check:{profile_id}` | string | 21600s (6h) | Cooldown: prevents repeated trophy completeness checks |
| `group_completeness_check:{profile_id}` | string | 21600s (6h) | Cooldown: prevents repeated group completeness checks |

### Site State

| Key Pattern | Type | TTL | Description |
|-------------|------|-----|-------------|
| `site:high_sync_volume` | string (JSON) | 300s | Banner flag: high sync volume detected |
| `sync:bulk_threshold` | string (int) | none | Configurable threshold for bulk_priority routing (default: 5000) |

### Deferred Notifications (used by sync pipeline)

| Key Pattern | Type | Description |
|-------------|------|-------------|
| `pending_platinum:{profile_id}:{game_id}` | string (JSON) | Queued platinum notification for a specific game |
| `pending_badges:{profile_id}` | list (JSON) | Accumulated badge notifications for consolidation |

## Related Docs

- [Dashboard System](../dashboard.md)
- [Community Hub](../community-hub.md)
