# Event System

A unified, append-only timeline of community activity that powers PlatPursuit's feeds. The Event system records noteworthy moments — platinums earned, reviews posted, badges awarded, challenges completed, and so on — into a single polymorphic table that the Community Hub, the Pursuit Feed, the per-profile Activity tab, and the dashboard's "recent activity" module all read from.

> **Status**: planned. This doc describes the design committed to during the Community Hub initiative; the implementation lands incrementally across Phases 1-4 of the [Community Hub](../features/community-hub.md) work.

## Architecture Overview

Events are facts about things that already happened. They are not commands, queues, notifications, or webhooks. The Event table is the single source of truth for "what is the community doing right now", and every read surface (hub feed preview, full feed page, profile activity tab, dashboard activity module) queries the same table.

The system has three components:

1. **The `Event` model** — a lean polymorphic row with `profile`, `event_type`, `occurred_at`, a generic FK target, and a JSONB `metadata` blob.
2. **`EventService`** — a stateless module of `record_*` recorders, one per event type. Each takes the source object (a `UserBadge`, a `Review`, etc.) and constructs an Event. This is the API for everywhere outside the sync pipeline.
3. **`EventCollector`** — a thread-local context manager that mirrors `sync_signal_suppressor` (in [Token Keeper](token-keeper.md)). The sync pipeline opens a collector, accumulates events in memory across many trophy writes, and flushes them via `Event.objects.bulk_create()` at the end of each per-game pass. This avoids the per-row overhead that signals would impose on a path that already deliberately suppresses signals for performance.

The combination is intentional: signals are great for low-volume single-write paths and terrible for the sync pipeline; explicit calls are great for bounded, traceable user actions and tedious for high-volume bulk paths. The Event system uses the right tool for each job, with a clear routing rule (see "Ingestion routing").

### Why polymorphic, not per-type tables

The lean polymorphic shape was chosen over either per-type tables (one table per event kind) or fully-denormalized snapshots (display fields stored inline). Per-type tables would multiply migration churn every time a new event type is added and force the feed to UNION across N tables. Denormalized snapshots are faster to read but go stale when the source object is renamed or soft-deleted, and they bloat the row. The lean polymorphic schema joins out via the generic FK to the live source object on read; soft-deleted targets are filtered with a manager helper.

### Why no historical backfill

The feed begins empty on launch day with one celebratory `day_zero` seed event. This was a deliberate product decision: backfilling years of historical platinums for every existing user would have produced a massive write burst, distorted the feed's "Last 24h" view, and given users an underwhelming "look at all this stale data" first impression. Starting fresh produces a clean chronicle that fills up organically as users sync. The Day Zero event is created via a data migration so every environment gets it idempotently.

## Data Model

### Event

| Field | Type | Notes |
|-------|------|-------|
| `id` | BigAutoField | PK |
| `profile` | FK(Profile, null=True, on_delete=CASCADE) | The actor who caused the event. **Nullable** so the system can record `day_zero` and any future system events with no human author. |
| `event_type` | CharField(max_length=32, choices=EVENT_TYPE_CHOICES) | One of the constants below. |
| `occurred_at` | DateTimeField(db_index=True) | **Historical truth**, not sync time. For trophies this is `EarnedTrophy.earned_date_time`; for reviews/comments/etc. it is `created_at`; for system events it is the moment the migration runs. **Never use `timezone.now()` at insert time** — see the gotcha about feed flooding below. |
| `target_content_type` | FK(ContentType, null=True) | The model class of the linked object (e.g. `Review`, `UserBadge`, `Concept`). |
| `target_object_id` | PositiveIntegerField(null=True) | The PK of the linked object. |
| `target` | GenericForeignKey | Resolves to the live source object. The feed `select_related`s/prefetches this on render. |
| `metadata` | JSONField(default=dict) | Per-event-type structured payload (rare-trophy earn rate, badge tier, list game count, coalesced sub-event lists, etc.). |
| `created_at` | DateTimeField(auto_now_add=True) | When the row was inserted. Useful for "events synced today" diagnostics; never used for feed ordering. |

**Indexes (created at table creation, not added later):**

- `(-occurred_at)` — global feed ordering
- `(profile, -occurred_at)` — profile activity tab + dashboard module
- `(event_type, -occurred_at)` — Trophy Feed mode + filtered feed views
- `(target_content_type, target_object_id)` — reverse lookups ("show all events about this review")

A GIN index on `metadata` is **deferred to v2** until a hot query path proves it necessary.

### Event types

The choices live as constants in `trophies/services/event_service.py`:

```python
TROPHY_FEED_TYPES = {
    'platinum_earned',
    'rare_trophy_earned',     # only ultra-rare (<5% earn rate)
    'concept_100_percent',    # base game or DLC group reached 100%
}

PURSUIT_FEED_TYPES = TROPHY_FEED_TYPES | {
    'badge_earned',
    'milestone_hit',
    'review_posted',
    'game_list_published',
    'challenge_started',
    'challenge_progress',
    'challenge_completed',
    'profile_linked',
}

SYSTEM_EVENT_TYPES = {'day_zero'}
EVENT_TYPE_CHOICES = sorted(PURSUIT_FEED_TYPES | SYSTEM_EVENT_TYPES)
```

**Dropped from v1**: `title_unlocked`. Titles are always paired with a badge or milestone event, so they would only add visual noise.

**Not recorded in v1**: top-level comments. Could be added as a `comment_posted` type later if needed; deferred to keep volume bounded.

## Ingestion Routing

The hybrid strategy is the load-bearing convention. New event sources should be added per these rules; future contributors should not pick "whichever feels right" or the system will drift.

| Source | Strategy | Insertion point |
|---|---|---|
| **Sync pipeline** (platinum, rare trophy, concept_100) | Explicit `EventCollector.add_*()` from inside `psn_api_service.py:create_or_update_earned_trophy_from_trophy_data`, gated by `EventCollector.is_active()`. The sync loop in `token_keeper.py:_do_sync_trophies` opens an `event_collector(profile_id=profile.id)` context alongside the existing `sync_signal_suppressor()`. | `psn_api_service.py:458` (just before return), wrapped by `token_keeper.py:1914-1922` |
| **Badges (sync path)** | Bulk-per-sync emission. After `check_profile_badges()` runs in `_job_sync_complete`, **one** `badge_earned` event is recorded per profile per sync, with `metadata['badges']` listing every badge awarded that sync. Avoids per-day coalescing race conditions entirely. | `token_keeper.py` after `check_profile_badges` (~line 1520) |
| **Badges (non-sync path)** | Sibling `post_save` receiver in `trophies/signals.py` next to the existing `update_gamification_on_badge_earned` receiver. Early-returns when `is_bulk_update_active()` is True so sync-time awards don't double-emit. Catches admin tools, manual rechecks, and any future non-sync badge writes. | `trophies/signals.py` (bottom of badge receivers) |
| **Milestones** | Direct `EventService.record_milestone_hit(user_milestone)` call from inside the `if created` block in `milestone_service.check_and_award_milestone` and `award_milestone_directly`. | `milestone_service.py:86` and `:254` |
| **Reviews** | Direct call from `ReviewService.create_review()` after the cache invalidation, before the milestone check. Inside the existing `@transaction.atomic` decorator. | `review_service.py:90` |
| **Game lists** | Fired on the `is_public` false→true flip in `GameListUpdateView.patch()`, only when `game_list.game_count > 0` (don't surface empty lists). | `api/game_list_views.py:247-266` |
| **Challenges (started)** | Direct call after each `Challenge.objects.create()` in the three challenge create API views. | `api/az_challenge_views.py`, `api/calendar_challenge_views.py`, `api/genre_challenge_views.py` |
| **Challenges (progress)** | One coalesced `challenge_progress` event per `check_*_challenge_progress` call, with `metadata['slots']` listing every slot that flipped to completed during this check. Coalescing happens at the source (per check call), not per day. | `challenge_service.py:107, 448, 497, 779` |
| **Challenges (completed)** | Direct call when `challenge.is_complete` flips True. Same insertion sites as progress. | Same as above |
| **Profile linked** | Direct call from `verification_service.link_profile_to_user` after the milestone check. | `verification_service.py:~126` |
| **Day Zero** | Created once via data migration, idempotent (`Event.objects.get_or_create(event_type='day_zero', ...)`). Not emitted by any code path. | `trophies/migrations/00XY_day_zero_event.py` |

### Why hybrid

The codebase already proves a hybrid pattern works: badge XP and gamification use signals, while sync trophy writes use explicit `sync_signal_suppressor` because the SELECT-per-row overhead would kill sync performance. Events extend the same partition: signals where they are cheap and natural, explicit calls where they are necessary for performance or for traceability.

A pure-signal approach would re-introduce the exact perf class that `sync_utils.sync_signal_suppressor` was built to kill. A pure-explicit approach would miss admin badge awards and any future write site that bypasses the badge service. The hybrid keeps both paths fast and complete.

## EventCollector Lifecycle

`event_collector(profile_id)` is a context manager defined in `trophies/services/event_service.py`. It mirrors `sync_signal_suppressor` exactly:

```python
@contextmanager
def event_collector(profile_id):
    EventCollector._activate(profile_id)
    try:
        yield
    finally:
        try:
            EventCollector._flush()
        except Exception:
            logger.exception("EventCollector flush failed; events lost for profile %s", profile_id)
        finally:
            EventCollector._deactivate()
```

Key properties:

- **Thread-local**: each sync worker has its own collector. No cross-thread contention, no in-memory races.
- **`is_active()` is the gate**, not `is_sync_signal_suppressed()`. This decouples event collection from the suppressor's lifetime so the same code path also fires for backfill commands or any future direct trophy writer that opts into collection.
- **Flush is its own atomic block**, never inside the per-batch `transaction.atomic()` from the sync loop. The flush happens once per `_do_sync_trophies` call (i.e. once per game), so transactions stay short.
- **Failures log and continue**. Events are best-effort; sync correctness is paramount. Better to lose a few feed entries than to fail a sync.
- **Always flushes in `finally`**, even on exception. The trade-off is that a partially-failed sync will have flushed partial events; the alternative (flush only on success) loses everything from a partially-successful sync, which is worse for the feed.

### Why `bulk_create` and not `Event.objects.create()` per row

`bulk_create(batch_size=500, ignore_conflicts=False)` is the primitive used inside the flush. It is dramatically faster than per-row `create()` calls and does not fire `post_save` signals — which is desirable here because nothing should react to Event inserts (no recursive event-on-event situations).

## Coalescing

Two event types are inherently high-volume and need coalescing to keep the feed readable:

### Badge events

**Strategy**: bulk-per-sync emission, NOT per-day coalescing.

After `check_profile_badges()` runs inside `_job_sync_complete`, the bulk-gamification context already has the list of badges awarded during this sync. The token keeper records one `badge_earned` event per profile per sync, with `metadata['badges']` listing every badge:

```python
{
    'event_type': 'badge_earned',
    'profile': profile,
    'occurred_at': timezone.now(),
    'metadata': {
        'badges': [
            {'series_slug': 'horror', 'name': 'Horror III', 'tier': 3},
            {'series_slug': 'rpg', 'name': 'RPG II', 'tier': 2},
        ],
        'count': 2,
    }
}
```

This sidesteps per-day coalescing race conditions entirely. The per-game lock at `token_keeper.py:1887-1896` and the per-profile lock in `_job_sync_complete` already prevent concurrent workers from stomping each other on the same profile, so the bulk emission is naturally race-free.

### Challenge progress

**Strategy**: one coalesced event per `check_*_challenge_progress` call.

When the sync triggers a challenge progress check, the function may flip several slots from incomplete to completed in one pass (e.g. earning multiple platinums from a backlog clearing trophies in 4 different A-Z slots simultaneously). Instead of one event per slot, the recorder emits a single event with `metadata['slots']` listing the slot details:

```python
{
    'event_type': 'challenge_progress',
    'profile': profile,
    'target': challenge,  # FK to AZChallenge / CalendarChallenge / GenreChallenge
    'occurred_at': timezone.now(),
    'metadata': {
        'slots': [
            {'letter': 'A', 'concept_id': 1234, 'concept_name': 'A Plague Tale'},
            {'letter': 'B', 'concept_id': 2345, 'concept_name': 'Bloodborne'},
        ],
        'count': 2,
        'last_slot_completed_at': '2026-04-07T20:14:33Z',
    }
}
```

The `metadata['last_slot_completed_at']` is used as a dedup sentinel: if a sync retry causes the same `check_*_challenge_progress` call to re-fire, the recorder skips emission if the latest slot's `completed_at` is older than the most recent `challenge_progress` event for that challenge.

## Soft-deleted targets

Several event source models support soft delete (`Review.is_deleted`, `Comment.is_deleted`, etc.). When the source is deleted, the corresponding Event row is **not** deleted — events are immutable facts. Instead, the feed query filters them out via a manager helper:

```python
class EventManager(models.Manager):
    def feed_visible(self):
        """Filter out events whose target has been soft-deleted."""
        # Implementation: prefetch GFK targets and exclude is_deleted=True
        ...
```

Every read surface (Community Hub feed preview, full feed page, profile Activity tab, dashboard activity module) calls `Event.objects.feed_visible()` rather than `Event.objects.all()`.

## Reading the feed

There are four read surfaces, all backed by the same table:

1. **Community Hub feed preview** (`/community/`) — last 10 globally-visible events, ordered by `-occurred_at`, "View Full Feed" CTA links to the standalone page. See the [Community Hub feature doc](../features/community-hub.md).
2. **Standalone full feed** (`/community/feed/`) — paginated, HTMX-filtered. Two modes: **Pursuit Feed** (everything in `PURSUIT_FEED_TYPES`) and **Trophy Feed** (only `TROPHY_FEED_TYPES`). Filterable by `event_type` and `time_range`. Uses `HtmxListMixin` and `InfiniteScroller` like the existing browse pages.
3. **Profile Activity tab** (`/profiles/<u>/?tab=activity`) — events filtered by `profile=target_profile`. v1 shows ONLY events authored BY the user (not events ABOUT them). Same partial-template + InfiniteScroller pattern as the Reviews tab.
4. **Dashboard `pursuit_activity` module** — hybrid: events for the current user PLUS direct EarnedTrophy queries for trophy_group cards (the "8 trophies in Persona today" UX that pure-event-backed reads would lose because we deliberately don't track non-rare individual trophies as events). See the [Dashboard doc](../features/dashboard.md).

## Volume Estimates

A 50,000-trophy first sync produces roughly:

- ~50-200 `platinum_earned` events (depending on the user's trophy mix)
- ~500-1500 `rare_trophy_earned` events
- ~10-50 `concept_100_percent` events
- 1 `badge_earned` event (bulk-per-sync coalescing)
- 0-N `challenge_progress` events (only if the user is mid-challenge during the sync)

**Total**: ~750-1750 events per first sync.

Site-wide worst case with 100 first-time syncs in a day: ~150k events/day. Postgres handles this comfortably with the indexes above. Re-evaluate partitioning at 100M rows or when the global feed query latency exceeds 50ms.

## Gotchas and Pitfalls

- **`occurred_at` MUST be historical truth, never `timezone.now()` for trophy events.** A first-time sync produces a wave of platinum events with `occurred_at` set to the actual earn date (sometimes years ago). If you mistakenly use `now()`, all of a new user's historical platinums will surge into the global feed's "Last 24h" view at sync time, drowning out genuine recent activity. The default global feed sort is `-occurred_at` filtered to recent dates, so historical earns naturally fall out of "Last 24h" / "Last 7d" views. The "All Time" view sorts them in their true chronological position, which is desirable.

- **Activity tab vs global feed sort**: both order by `-occurred_at`. The dashboard `pursuit_activity` module also orders by `-occurred_at`. Never mix `created_at` ordering into the feed; it produces nonsensical results during initial syncs.

- **`bulk_create` does NOT fire `post_save` signals.** This is deliberate (no recursive event-on-event), but it means downstream listeners that want to react to event creation cannot use `post_save` on the Event model. There are currently no such listeners.

- **Profile FK is nullable, but Activity tab filters out null-profile rows.** Day Zero and any future system events have `profile=None`. The global Pursuit Feed shows them; the per-user Activity tab does not.

- **`event_collector` flushes ALWAYS in its `finally`, even on exception.** A partially-failed sync will have flushed partial events. This is intentional: events are best-effort, sync correctness is the priority. Never let event flushing failures cascade into a sync failure — wrap the flush in try/except and log via `logger.exception()`.

- **Sync re-runs do NOT double-emit platinum events.** If `_job_sync_complete` re-queues games due to a health-check mismatch, the affected `EarnedTrophy` rows already have `earned=True` from the prior pass. On rerun, the `is_new_earn` flag computed in `psn_api_service.create_or_update_earned_trophy_from_trophy_data` returns False (no flip detected) and the recorder skips the row. The existing flip-detection logic is the right primitive — do not add a separate dedup layer.

- **Coalescing race conditions are sidestepped, not solved.** Per-day coalescing (e.g., "if a profile already has a badge_earned event today, update it instead of inserting a new one") would race between concurrent sync workers. The bulk-per-sync emission strategy eliminates this entire class of bug because the per-profile lock in `_job_sync_complete` ensures only one worker is in the badge-emission window at a time. If you ever add a new event type that needs coalescing, prefer source-level coalescing (one event per service call, with metadata listing sub-events) over time-bucket coalescing.

- **Soft-deleted targets must be filtered, not deleted.** `Review.is_deleted=True` does not delete the corresponding `review_posted` Event. Use `Event.objects.feed_visible()` for any read surface that should hide deleted-target events. **Do NOT cascade-delete events when their source is soft-deleted** — that would lose audit trail.

- **`EventCollector.is_active()` is the gate, NOT `is_sync_signal_suppressed()`.** The two contexts are nested but conceptually independent. Future callers that want to collect events without suppressing signals (e.g., a backfill management command) should be able to do so cleanly.

- **`occurred_at` ties feed visibility to history, but JSON-LD sitemaps should NOT include events.** The Pursuit Feed is dynamic and per-request; it does not need a sitemap entry. The hub landing page itself (`/community/`) and the full feed page (`/community/feed/`) DO get sitemap entries because they are stable URLs.

- **Index strategy is fixed at table creation.** The four indexes above are created in the initial migration. Adding more later requires either downtime or `CREATE INDEX CONCURRENTLY` (Postgres-specific). Defer GIN-on-metadata until a hot read query proves it necessary.

- **No partitioning in v1.** Postgres handles ~50M rows fine with the indexes above. Re-evaluate at 100M rows or when feed query latency > 50ms. Partitioning by `occurred_at` month is the natural strategy if it becomes necessary.

## Management Commands

| Command | Purpose | Typical Usage |
|---------|---------|---------------|
| `seed_synthetic_events` | Generate N synthetic events of varying types for a target profile. Useful for testing the feed/dashboard module locally without waiting for real activity. | `python manage.py seed_synthetic_events --profile <id> --count 100 --types platinum,badge,review` |

The Day Zero seed event is created via a data migration, not a management command, so it ships in every environment without manual ops.

## Cache Keys

The Event system itself does not currently cache. The Community Hub page-data assembler may cache aggregated reads (top reviewers, leaderboards), but the feed itself reads fresh from Postgres on every request. If feed latency becomes a problem, the natural caching layer is the hub-page-data assembler in `core/services/community_hub_service.py`, not the raw Event table.

## Related Docs

- [Community Hub](../features/community-hub.md): the primary read surface and the UX framing for the Event system
- [Token Keeper](token-keeper.md): the sync pipeline and the existing `sync_signal_suppressor` pattern that `event_collector` mirrors
- [Dashboard](../features/dashboard.md): the `pursuit_activity` module that replaces `recent_activity` and `recent_platinums`
- [Review Hub](../features/review-hub.md): the source of `review_posted` events
- [Badge System](badge-system.md): the source of `badge_earned` events
- [Notification System](notification-system.md): a parallel system that handles user-targeted notifications (different from events, which are public facts)
