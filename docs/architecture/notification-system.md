# Notification System

The notification system is a multi-layered architecture that delivers in-app notifications, Discord webhook embeds, and shareable platinum images to PlatPursuit users. It handles everything from real-time platinum/badge/milestone achievements to admin-scheduled bulk announcements, using a combination of Django signals, Redis-backed deferred queues, template-driven rendering, and a background daemon thread for Discord rate-limited delivery. The system is designed around the principle that sync-time data is often stale, so notifications are intentionally deferred until accurate counts are available.

## Architecture Overview

The notification system is organized into six cooperating layers:

1. **Signal Layer** (`signals.py`): Django `pre_save`/`post_save` receivers that detect achievement events (platinum earned, badge awarded, Discord verified) and either create notifications immediately or delegate to the deferred queue.

2. **Service Layer** (`services/`): Stateless service classes following the project-wide static-method pattern. Each service owns one responsibility: core CRUD, deferred queuing, scheduled delivery, caching, template rendering, share image generation, or shareable data collection.

3. **Deferred Queue** (`deferred_notification_service.py`): Redis-backed queue that holds platinum and badge notifications during sync, then materializes them with fresh data at the right completion point (per-game for platinums, full-sync for badges with series consolidation).

4. **Scheduled Delivery** (`scheduled_notification_service.py`): Admin-created notifications with future send times, audience targeting, and an hourly cron processor.

5. **Cache Layer** (`notification_cache_service.py`): Redis cache for unread counts (5min TTL) and recent notification lists (1min TTL), invalidated on every write operation.

6. **Discord Integration** (`discord_notifications.py`): Queue-based webhook sender running on a daemon thread with retry logic and rate-limit handling.

```
Signal fires (post_save)
    |
    +-- Profile syncing? --> DeferredNotificationService (Redis queue)
    |                            |
    |                            +-- Per-game sync done --> create_platinum_notification_for_game()
    |                            +-- Full sync done    --> create_badge_notifications() [consolidated]
    |
    +-- Not syncing? --> NotificationService.create_from_template()
                              |
                              +-- TemplateService.render_template()
                              +-- Notification.objects.create()
                              +-- NotificationCacheService.invalidate_all_for_user()
```

## File Map

| File | Purpose |
|------|---------|
| `notifications/models.py` | Data models: Notification, NotificationTemplate, ScheduledNotification, DeviceToken, PlatinumShareImage, NotificationLog |
| `notifications/signals.py` | Django signal handlers for EarnedTrophy, UserBadge, UserMilestone, Profile events |
| `notifications/apps.py` | AppConfig that imports signals on `ready()` |
| `notifications/validators.py` | SectionValidator for structured notification sections |
| `notifications/services/notification_service.py` | Core CRUD: create, bulk create, mark read, get unread count, user targeting |
| `notifications/services/deferred_notification_service.py` | Redis-backed queue for sync-time platinum/badge notifications with consolidation |
| `notifications/services/scheduled_notification_service.py` | Scheduled/immediate bulk notifications with audience targeting and cron processing |
| `notifications/services/notification_cache_service.py` | Redis cache for unread counts and recent notification lists |
| `notifications/services/template_service.py` | `{variable}` substitution engine for NotificationTemplate rendering |
| `notifications/services/share_image_service.py` | Pillow-based share image generator (landscape 1200x630, portrait 1080x1350) |
| `notifications/services/shareable_data_service.py` | Data collection for share images: rarity labels, badge XP, tier progress, user ratings |
| `trophies/discord_utils/discord_notifications.py` | Discord webhook queue, daemon sender thread, embed builders |
| `trophies/apps.py` | Starts the Discord webhook daemon thread on app ready |
| `trophies/sync_utils.py` | `sync_signal_suppressor()` context manager to skip pre_save signals during sync |
| `core/management/commands/process_scheduled_notifications.py` | Hourly cron command for scheduled notification delivery |
| `notifications/management/commands/create_test_notification.py` | Dev tool: create test platinum/challenge notifications |
| `notifications/management/commands/force_platinum_notification.py` | Dev tool: bypass signal flow and call handler directly |

## Data Model

### Notification

The core model. Stores rendered (already-substituted) content for a single user.

| Field | Type | Notes |
|-------|------|-------|
| `recipient` | FK(CustomUser) | CASCADE delete |
| `notification_type` | CharField | Indexed. One of 14 types (see below) |
| `template` | FK(NotificationTemplate) | Nullable. SET_NULL on delete |
| `title` | CharField(255) | Rendered title |
| `message` | TextField(1000) | Rendered message |
| `detail` | TextField(2500) | Rich text/markdown detail |
| `sections` | JSONField | Structured sections (alternative to markdown detail) |
| `banner_image` | ImageField | Optional banner for detail view |
| `icon` | CharField(50) | Emoji or icon name |
| `action_url` | URLField | Optional CTA link |
| `action_text` | CharField(100) | Optional CTA button text |
| `priority` | CharField | low / normal / high / urgent |
| `metadata` | JSONField | Full context dict (game_id, badge_id, trophy stats, etc.) |
| `is_read` | BooleanField | Indexed |
| `read_at` | DateTimeField | Nullable |
| `created_at` | DateTimeField | Indexed |

Composite indexes: `(recipient, -created_at)`, `(recipient, is_read, -created_at)`, `(notification_type, -created_at)`.

### NotificationTemplate

Reusable templates with `{variable}` placeholders. Looked up by `name` (unique) and `auto_trigger_enabled` flag.

Key fields: `title_template`, `message_template`, `action_url_template`, `trigger_event`, `auto_trigger_enabled`.

Templates are rendered by `TemplateService.render_template()` using Python's `str.format(**context)`.

### ScheduledNotification

Admin-created notifications with future delivery. Processed by the hourly cron command.

| Field | Type | Notes |
|-------|------|-------|
| `target_type` | CharField | all / premium_monthly / premium_yearly / premium_supporter / premium_all / discord_verified / individual |
| `target_criteria` | JSONField | e.g. `{"user_ids": [1,2,3]}` for individual targeting |
| `scheduled_at` | DateTimeField | When to deliver |
| `status` | CharField | pending / processing / sent / cancelled / failed |
| `sections` | JSONField | Structured sections (max 5, validated) |
| `created_by` | FK(CustomUser) | Staff user who created it |
| `recipient_count` | PositiveIntegerField | Estimated at creation, actual at send |

Status lifecycle: `pending` -> `processing` -> `sent` (or `failed`). Can be `cancelled` while still `pending`.

### PlatinumShareImage

Generated share images stored in S3.

| Field | Type | Notes |
|-------|------|-------|
| `notification` | FK(Notification) | CASCADE |
| `format` | CharField | landscape (1200x630) or portrait (1080x1350) |
| `image` | ImageField | S3 path: `platinum-share-images/%Y/%m/` |
| `download_count` | PositiveIntegerField | Tracks downloads |

Unique constraint: `(notification, format)` prevents duplicate generation.

### DeviceToken

Push notification device tokens for mobile.

| Field | Type | Notes |
|-------|------|-------|
| `user` | FK(CustomUser) | CASCADE |
| `token` | CharField(512) | Unique. Expo push token or raw FCM/APNs token |
| `platform` | CharField | ios / android |
| `last_used` | DateTimeField | auto_now |

### NotificationLog

Audit trail for bulk sends (both immediate and scheduled).

Captures a snapshot of what was sent: notification_type, title, message, detail, target_type, target_criteria, recipient_count, sent_by, was_scheduled.

## Notification Types

| Type | Trigger | Signal/Service | Notes |
|------|---------|----------------|-------|
| `platinum_earned` | EarnedTrophy post_save (platinum, earned=True flip) | `notify_platinum_earned` signal | 2-day threshold, shovelware filter, deferred during sync |
| `badge_awarded` | UserBadge post_save (created=True) | `notify_badge_awarded` signal | Always deferred for series consolidation |
| `milestone_achieved` | Called from milestone_service.py | `create_milestone_notification()` function | Not a signal; called directly for batch consolidation |
| `monthly_recap` | Monthly recap generation | Created externally | Monthly recap availability |
| `subscription_created` | Subscription activation | Created externally | Welcome notification |
| `subscription_updated` | Subscription change | Created externally | Plan change notification |
| `discord_verified` | Profile post_save (is_discord_verified flip) | `notify_discord_linked` signal | Only on False->True transition |
| `challenge_completed` | Challenge completion | Created externally | A-Z or Calendar challenge |
| `review_reply` | Review reply received | Created externally | Community reviews system |
| `review_milestone` | Review count milestone | Created externally | Community reviews system |
| `admin_announcement` | Staff admin panel | ScheduledNotificationService | Bulk targeted delivery |
| `system_alert` | Staff admin panel | ScheduledNotificationService | Urgent system messages |
| `payment_failed` | Stripe/PayPal webhook | Created externally | Subscription payment failure |
| `payment_action_required` | Stripe webhook | Created externally | Payment requires user action |

## Key Flows

### Standard Notification Flow

This flow applies when a notification event occurs outside of a sync operation (manual trophy update, Discord verification, etc.).

1. **Django signal fires** (e.g., `post_save` on `EarnedTrophy`).
2. **Pre-save hook** captured the previous state of the relevant field (e.g., `earned` value) so post_save can detect the flip.
3. **Signal handler** validates conditions (is platinum? is newly earned? not shovelware? within 2-day window?).
4. **Duplicate check**: queries `Notification.objects.filter(metadata__game_id=...)` to prevent duplicate notifications.
5. **TOCTOU guard**: wraps creation in `transaction.atomic()` with a re-check inside the transaction to close the race window.
6. **Template lookup**: `NotificationTemplate.objects.get(name='platinum_earned', auto_trigger_enabled=True)`.
7. **Context assembly**: gathers ProfileGame stats, total plat count, yearly plat count, rarity labels, etc.
8. **Creation**: `NotificationService.create_from_template()` renders the template via `TemplateService` and creates the `Notification` row.
9. **Cache invalidation**: `NotificationCacheService.invalidate_all_for_user()` clears both the unread count and recent notifications cache.

### Deferred Notification Flow (Sync)

During a PSN sync, trophy counts and badge progress are stale until the sync pipeline completes specific stages. The deferred flow ensures notifications show accurate data.

#### Platinum Notifications (Per-Game Deferred)

1. **Signal detects sync**: `notify_platinum_earned` checks `profile.sync_status == 'syncing'`.
2. **Queue to Redis**: `DeferredNotificationService.queue_platinum_notification()` stores minimal context (profile_id, game_id, trophy_id, earned_date) in Redis key `pending_platinum:{profile_id}:{game_id}` with a 2-hour TTL.
3. **Game sync completes**: `_job_sync_trophies()` in `token_keeper.py` calls `DeferredNotificationService.create_platinum_notification_for_game(profile_id, game_id)` after all trophies for that game are synced.
4. **Fresh data fetch**: The service reads the Redis key, fetches fresh trophy counts directly from `EarnedTrophy` (not the stale `ProfileGame.earned_trophies_count`), and assembles the full context.
5. **Creation + cleanup**: Creates the notification via `NotificationService.create_from_template()` and deletes the Redis key.

#### Badge Notifications (Full-Sync Deferred with Consolidation)

1. **Signal always defers**: `notify_badge_awarded` always calls `DeferredNotificationService.queue_badge_notification()` regardless of sync state, because badge notifications benefit from consolidation even outside sync.
2. **Queue to Redis**: Uses `RPUSH` to atomically append badge context to the list at `pending_badges:{profile_id}` with a 1-hour TTL.
3. **Sync completes**: `_job_sync_complete()` in `token_keeper.py` calls `DeferredNotificationService.create_badge_notifications(profile_id)`.
4. **Consolidation**: The service atomically fetches all items and deletes the key (Redis pipeline). Badges are grouped by `series_slug`. For each series, only the highest tier badge produces a notification. Badges without a series slug get individual notifications.
5. **Also called by admin commands**: `refresh_badge_series` calls `create_badge_notifications()` at the end to process any queued badges.

### Scheduled Notification Flow

1. **Staff creates**: Via admin panel, `ScheduledNotificationService.create_scheduled()` creates a `ScheduledNotification` with status `pending`, estimated recipient count, and a future `scheduled_at` time.
2. **Cron fires**: `process_scheduled_notifications` management command runs hourly on Render cron.
3. **Processing**: `ScheduledNotificationService.process_pending()` queries all pending notifications where `scheduled_at <= now`, using `select_for_update(skip_locked=True)` to prevent concurrent processing.
4. **Delivery**: For each scheduled notification, `_process_single()` resolves the target audience via `get_target_users_extended()`, then calls `NotificationService.send_bulk_notification()` which uses `bulk_create(batch_size=500)`.
5. **Logging**: A `NotificationLog` row is created as an audit record.
6. **Cache invalidation**: `NotificationCacheService.invalidate_unread_counts_bulk()` clears cached unread counts for all recipients.

Immediate (non-scheduled) bulk notifications follow the same path but skip step 2-3, going directly through `send_immediate()`.

### Discord Webhook Flow

Discord notifications are completely separate from the in-app notification system. They use a producer-consumer pattern with a daemon thread.

1. **Producer**: `notify_new_platinum()`, `notify_new_badge()`, `send_batch_role_notification()`, or `send_subscription_notification()` builds a Discord embed payload and calls `queue_webhook_send(payload, webhook_url)`.
2. **Queue**: Payloads are pushed onto a `queue.Queue()` (thread-safe, in-memory).
3. **Consumer**: `webhook_sender_worker()` runs on a daemon thread started in `TrophiesConfig.ready()`. It loops forever, pulling payloads from the queue.
4. **Delivery with retries**: Each payload is POSTed to the Discord webhook URL (optionally via proxy). On HTTP 429 (rate limit), it sleeps for `Retry-After + 0.5s` and retries. Other HTTP errors or request exceptions also retry with a 1-second backoff. Max 5 retries before dropping.
5. **Cooldown**: A 1-second sleep between each webhook send to stay well within Discord's rate limits.

The proxy is configured via `PROXY_URL` environment variable and validated at startup. On daemon thread start, `check_proxy_ip()` verifies the outbound IP.

### Share Image Generation

Platinum share images are generated server-side using Pillow (PIL).

1. **Request**: User triggers share image generation from the notification detail view.
2. **Service call**: `ShareImageService.generate_image(notification, format_type)` is called.
3. **Background creation**: Creates a gradient background, loads fonts (Poppins Bold, Poppins SemiBold, Inter Regular from `static/fonts/`, with system font fallbacks).
4. **Layout rendering**: Two layouts are supported:
   - **Landscape (1200x630)**: Optimized for Facebook/Twitter/Discord. Game image with trophy overlay on left, stats on right.
   - **Portrait (1080x1350)**: Optimized for Instagram. Game image banner at top, centered stats below.
5. **Remote image fetch**: Game images and trophy icons are fetched from PSN CDN URLs with a 10-second timeout, resized/cropped to fit.
6. **Output**: Returns a Django `InMemoryUploadedFile` (PNG) that gets saved to the `PlatinumShareImage` model (S3 storage).

`ShareableDataService` provides the data collection layer for share images. It gathers comprehensive metadata including badge XP, tier 1 badge progress, user ratings, and historical platinum counts (counting only platinums earned on or before the share target's date for accurate "Platinum #N" display).

## Integration Points

| System | Integration | Direction |
|--------|------------|-----------|
| **Sync Pipeline** (`token_keeper.py`) | Calls `DeferredNotificationService` at game-sync and full-sync completion | Sync -> Notifications |
| **Signal Suppression** (`sync_utils.py`) | `sync_signal_suppressor()` disables EarnedTrophy pre_save signal during sync batches | Sync -> Signals |
| **Badge Service** (`check_profile_badges`) | Awards badges, triggering `post_save` on `UserBadge` | Badges -> Signals |
| **Milestone Service** (`milestone_service.py`) | Calls `create_milestone_notification()` directly (not via signal) for consolidation | Milestones -> Notifications |
| **Subscription System** (`users/views.py`) | Creates subscription/payment notifications and Discord webhooks | Subscriptions -> Notifications |
| **Challenge System** | Creates `challenge_completed` notifications on completion | Challenges -> Notifications |
| **Admin Panel** | Staff create/schedule/send notifications via ScheduledNotificationService | Admin -> Notifications |
| **Discord** | Separate webhook system for platinums, badges, roles, subscriptions | Notifications -> Discord |
| **XP/Gamification** (`xp_service.py`) | Badge notifications fetch fresh XP via `calculate_series_xp()` and `calculate_total_xp()` | Gamification -> Signals |
| **Admin Commands** (`refresh_badge_series`) | Calls `create_badge_notifications()` at end to flush queued badge notifications | Commands -> Deferred |

## Gotchas and Pitfalls

### 1. Signal Suppression During Sync is Critical

The `sync_signal_suppressor()` context manager in `sync_utils.py` uses thread-local storage to suppress the `EarnedTrophy` pre_save signal. If you add a new pre_save or post_save signal on EarnedTrophy, you must check `is_sync_signal_suppressed()` at the top of the handler. Without this, every `EarnedTrophy.save()` during sync fires an extra SELECT query (thousands per sync), and platinum notifications may be created prematurely with stale data.

### 2. Badge Notifications Are Always Deferred

Unlike platinums (which are only deferred during sync), badge notifications are **always** queued to Redis and never created inline. This is because badges benefit from series consolidation even outside sync (e.g., `refresh_badge_series` can award multiple tiers at once). If you add a code path that awards badges, make sure `create_badge_notifications()` is called afterward to flush the queue.

### 3. The 2-Day Threshold Prevents Initial Sync Spam

When a user first links their PSN account, the sync imports their entire trophy history. Without the 2-day threshold in `notify_platinum_earned`, they would receive hundreds of platinum notifications for trophies earned years ago. This threshold is applied in both the signal handler and must be respected in any new notification creation paths.

### 4. Duplicate Prevention Has a TOCTOU Window

The signal handler checks for existing notifications, then creates one inside `transaction.atomic()` with a re-check. This closes the race window for concurrent signals on the same trophy. If you create notifications outside the signal flow, use the same pattern: check, wrap in atomic, re-check, create.

### 5. Metadata Stores the Full Context

`Notification.metadata` stores the entire context dict passed to `create_from_template()`. This means the metadata JSON can be large (trophy stats, badge layers, XP values, etc.). This is intentional: share images and notification detail views read data from metadata rather than re-querying the database. However, be mindful of storage implications when adding new context fields.

### 6. Badge XP is Calculated Fresh in Signals, Not Read from Denormalized Table

`_calculate_badge_xp()` in `signals.py` calls `calculate_series_xp()` and `calculate_total_xp()` directly rather than reading from `ProfileGamification`. This is because the denormalized gamification table may not be updated yet when the notification signal fires. Always calculate XP fresh in notification contexts.

### 7. Discord Webhooks Are Fire-and-Forget with In-Memory Queue

The Discord webhook queue is a Python `queue.Queue()` (in-memory). If the process crashes, queued webhooks are lost. This is acceptable because Discord notifications are supplementary to the in-app notifications. The daemon thread also means webhooks only send from the web process, not from Celery workers or management commands running in separate processes.

### 8. Scheduled Notification Processing Uses `select_for_update(skip_locked=True)`

This prevents double-processing if the cron job overlaps (unlikely with hourly runs, but safe). However, it means a failed notification that stays in `processing` status will not be retried automatically. Failed notifications are marked with status `failed` and `error_message` for manual investigation.

### 9. Share Image Remote Fetch Timeout

`ShareImageService._fetch_and_process_image()` fetches game images from PSN CDN URLs with a 10-second timeout. If the CDN is slow or the URL is invalid, the image slot is left blank (not errored). This means share images may render without game art in degraded conditions.

### 10. Template Variables Must Match Exactly

`TemplateService.render_template()` uses Python `str.format(**context)`. A missing variable raises `KeyError`, which is caught and logged but returns `None` (notification not created). When adding new templates, use `TemplateService.validate_context()` to verify all placeholders are satisfied before rendering.

### 11. Milestone Notifications Bypass Signals

`create_milestone_notification()` is a plain function, not a signal handler. It is called directly from `milestone_service.py` to allow batch consolidation (only the highest tier per criteria type gets a notification). If you refactor milestone processing, ensure this function is still called at the right consolidation point.

## Management Commands

| Command | Schedule | Purpose |
|---------|----------|---------|
| `process_scheduled_notifications` | Hourly (Render cron) | Processes pending scheduled notifications that are due. Supports `--dry-run` flag. |
| `create_test_notification` | Manual (dev) | Creates test platinum or challenge notifications. Supports `--type` (platinum/challenge) and `--username` flags. |
| `force_platinum_notification` | Manual (dev) | Bypasses signal flow and calls `notify_platinum_earned` directly for the first superuser's platinum trophy. Useful for debugging the signal handler. |
| `debug_signals` | Manual (dev) | Signal debugging utility. |
| `test_signals` | Manual (dev) | Signal testing utility. |

## Cache Keys

| Key Pattern | TTL | Invalidated By |
|-------------|-----|----------------|
| `notification:unread_count:{user_id}` | 300s (5 min) | `create_notification()`, `mark_as_read()`, `mark_all_as_read()`, `send_bulk_notification()` |
| `notification:recent:{user_id}` | 60s (1 min) | `create_notification()` (via `invalidate_all_for_user`) |
| `pending_platinum:{profile_id}:{game_id}` | 7200s (2 hr) | `create_platinum_notification_for_game()` (deleted after creation or on error) |
| `pending_badges:{profile_id}` | 3600s (1 hr) | `create_badge_notifications()` (atomically read + deleted via Redis pipeline) |

## Related Docs

- [Dashboard System](../dashboard.md): Dashboard modules may display notification counts
- [Community Hub](../community-hub.md): Review reply and review milestone notification types
- [Sync Pipeline Optimization](../../MEMORY.md): Details on `sync_signal_suppressor()` and batch query patterns
