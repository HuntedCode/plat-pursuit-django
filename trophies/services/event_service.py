"""Event recording infrastructure for the Pursuit Feed.

This module provides three things:

1. **Event type constants** (`EVENT_TYPE_CHOICES`, `TROPHY_FEED_TYPES`,
   `PURSUIT_FEED_TYPES`) — the canonical taxonomy used by the model, the
   queryset filters, and the feed read surfaces.

2. **`EventCollector`** — a thread-local context manager that mirrors
   `sync_signal_suppressor` (see `trophies/sync_utils.py`). The sync pipeline
   opens an `event_collector(profile_id=...)` context, accumulates events in
   memory across many trophy writes, and flushes them via `bulk_create` on
   exit. This avoids per-row signal overhead on the hottest write path in the
   codebase.

3. **`EventService`** — a stateless module of `record_*` recorders, one per
   event type. Used by service-action emitters (reviews, lists, challenges,
   profile linking) and signal-handled emitters (badges, milestones). Sync
   events use `EventCollector` instead.

The hybrid ingestion strategy and the routing rules are documented in
`docs/architecture/event-system.md`. Future contributors adding new event
sources MUST consult that doc to choose the right ingestion path.
"""
import datetime as _dt
import logging
import threading
from contextlib import contextmanager
from typing import Optional

logger = logging.getLogger(__name__)


def _serialize_metadata(value):
    """Recursively normalize a metadata payload into JSON-safe primitives.

    Postgres' JSONField encoder cannot serialize raw `datetime` (or `date`)
    objects, and a failed insert inside an outer `transaction.atomic()` block
    poisons the whole transaction with a `TransactionManagementError` for
    every subsequent query. Centralizing the conversion here means future
    contributors cannot accidentally break a sync by stuffing a datetime into
    `metadata` somewhere — every recorder runs its payload through this
    helper before passing it to `Event.objects.create`.

    `datetime` and `date` instances become ISO-8601 strings. Lists and dicts
    are walked recursively. Everything else is passed through unchanged so
    primitives, None, ints, floats, strs, and bools are unaffected.
    """
    if isinstance(value, dict):
        return {k: _serialize_metadata(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_serialize_metadata(v) for v in value]
    if isinstance(value, _dt.datetime):
        return value.isoformat()
    if isinstance(value, _dt.date):
        return value.isoformat()
    return value


def _create_event_safely(**fields):
    """Create an Event row inside a savepoint so failures cannot poison the outer transaction.

    Recorders are called from inside service methods that are themselves
    wrapped in `@transaction.atomic` (e.g. `ReviewService.create_review`)
    or run during the sync pipeline's atomic blocks. If `Event.objects.create`
    raises mid-call (bad data, schema drift, DB connection drop), Django
    marks the OUTER transaction as broken and every subsequent query in
    that transaction fails — turning a single bad event into a sync-killing
    cascade. The savepoint isolates the create so its failure rolls back
    only the event, not the surrounding work.

    Returns the created Event on success, None on failure. Always logs the
    exception with full stack trace via `logger.exception`.
    """
    from django.db import transaction
    from trophies.models import Event

    # Normalize the metadata payload before the savepoint so a serialization
    # bug never reaches Postgres in the first place.
    if 'metadata' in fields:
        fields['metadata'] = _serialize_metadata(fields['metadata'])

    try:
        with transaction.atomic():
            return Event.objects.create(**fields)
    except Exception:
        logger.exception("Failed to create Event row (savepoint rolled back)")
        return None


# ---------------------------------------------------------------------------
# Event type constants
# ---------------------------------------------------------------------------

#: Trophy-only feed mode. Used by the /community/feed/?feed_mode=trophy view.
TROPHY_FEED_TYPES = frozenset({
    'platinum_earned',
    'rare_trophy_earned',
    'concept_100_percent',
})

#: Full Pursuit Feed (default mode). Includes everything in the trophy feed
#: PLUS service-action and badge/milestone events.
PURSUIT_FEED_TYPES = frozenset(TROPHY_FEED_TYPES | {
    'badge_earned',
    'milestone_hit',
    'review_posted',
    'game_list_published',
    'challenge_started',
    'challenge_progress',
    'challenge_completed',
    'profile_linked',
})

#: System events with no human author (profile=None). Not in the Pursuit Feed
#: by default but visible on the global feed.
SYSTEM_EVENT_TYPES = frozenset({'day_zero'})

#: All known event types, used as the field's allowed values.
ALL_EVENT_TYPES = frozenset(PURSUIT_FEED_TYPES | SYSTEM_EVENT_TYPES)

#: Display labels for the admin and feed UIs.
EVENT_TYPE_CHOICES = sorted([
    ('platinum_earned', 'Platinum Earned'),
    ('rare_trophy_earned', 'Rare Trophy Earned'),
    ('concept_100_percent', '100% Completion'),
    ('badge_earned', 'Badge Earned'),
    ('milestone_hit', 'Milestone Hit'),
    ('review_posted', 'Review Posted'),
    ('game_list_published', 'Game List Published'),
    ('challenge_started', 'Challenge Started'),
    ('challenge_progress', 'Challenge Progress'),
    ('challenge_completed', 'Challenge Completed'),
    ('profile_linked', 'Profile Linked'),
    ('day_zero', 'Day Zero'),
])

#: Earn-rate threshold for `rare_trophy_earned` events. Trophies with an earn
#: rate strictly less than this percentage are considered "ultra-rare" and
#: surface in the trophy feed alongside platinums and 100% completions.
RARE_TROPHY_EARN_RATE_THRESHOLD = 5.0


# ---------------------------------------------------------------------------
# EventCollector: thread-local context for sync-pipeline events
# ---------------------------------------------------------------------------

_event_collector_context = threading.local()


class EventCollector:
    """Thread-local collector for sync-pipeline events.

    Mirrors the `sync_signal_suppressor` pattern from `trophies/sync_utils.py`.
    The sync pipeline opens an `event_collector(profile_id=...)` context,
    queues events via `add_*` methods inside the trophy write loop, and the
    context flushes them via `bulk_create` on exit.

    Use `EventCollector.is_active()` to gate event recording in code paths
    that may run inside or outside a collector context (e.g.
    `psn_api_service.create_or_update_earned_trophy_from_trophy_data`, which
    is called by both the sync pipeline AND ad-hoc paths). Outside a context,
    `add_*` calls are no-ops.

    The collector is intentionally separate from `sync_signal_suppressor` so
    future callers (backfill commands, direct trophy writes) can opt into
    event collection without also suppressing signals.
    """

    @classmethod
    def is_active(cls) -> bool:
        """Return True iff an event_collector context is open on this thread."""
        return getattr(_event_collector_context, 'active', False)

    @classmethod
    def _activate(cls, profile_id: int) -> None:
        _event_collector_context.active = True
        _event_collector_context.profile_id = profile_id
        _event_collector_context.events = []

    @classmethod
    def _deactivate(cls) -> None:
        _event_collector_context.active = False
        _event_collector_context.profile_id = None
        _event_collector_context.events = []

    @classmethod
    def add_platinum(cls, *, profile_id: int, trophy, earned_at) -> None:
        """Queue a `platinum_earned` event for flush at context exit.

        No-op if the collector is not active. Called from
        `psn_api_service.create_or_update_earned_trophy_from_trophy_data` when
        the in-memory `is_new_earn` flag is True and the trophy is a platinum
        on a non-shovelware game.
        """
        if not cls.is_active():
            return
        _event_collector_context.events.append({
            'event_type': 'platinum_earned',
            'profile_id': profile_id,
            'occurred_at': earned_at,
            'target_type': 'trophy',
            'target_id': trophy.id,
            'metadata': {
                'game_id': trophy.game_id,
                'concept_id': getattr(trophy.game, 'concept_id', None),
                'trophy_name': trophy.trophy_name,
                'earn_rate': float(trophy.trophy_earn_rate) if trophy.trophy_earn_rate else None,
            },
        })

    @classmethod
    def add_rare_trophy(cls, *, profile_id: int, trophy, earned_at) -> None:
        """Queue a `rare_trophy_earned` event for an ultra-rare trophy.

        Only fires for trophies with `trophy_earn_rate < RARE_TROPHY_EARN_RATE_THRESHOLD`.
        The caller is responsible for the threshold check; this method does
        not re-validate.
        """
        if not cls.is_active():
            return
        _event_collector_context.events.append({
            'event_type': 'rare_trophy_earned',
            'profile_id': profile_id,
            'occurred_at': earned_at,
            'target_type': 'trophy',
            'target_id': trophy.id,
            'metadata': {
                'game_id': trophy.game_id,
                'concept_id': getattr(trophy.game, 'concept_id', None),
                'trophy_name': trophy.trophy_name,
                'trophy_type': trophy.trophy_type,
                'earn_rate': float(trophy.trophy_earn_rate),
            },
        })

    @classmethod
    def add_concept_100(cls, *, profile_id: int, concept, occurred_at) -> None:
        """Queue a `concept_100_percent` event for a newly-completed concept.

        Called from `_job_sync_complete` after the post-sync watermark diff
        identifies concepts that crossed 100% during this sync.
        """
        if not cls.is_active():
            return
        _event_collector_context.events.append({
            'event_type': 'concept_100_percent',
            'profile_id': profile_id,
            'occurred_at': occurred_at,
            'target_type': 'concept',
            'target_id': concept.id,
            'metadata': {
                'concept_name': concept.unified_title,
                'concept_slug': concept.slug,
            },
        })

    @classmethod
    def _flush(cls) -> int:
        """Flush queued events to the database via bulk_create.

        Best-effort: failures are logged but do not propagate. Events are
        secondary to sync correctness.
        """
        if not cls.is_active():
            return 0
        events_to_flush = list(getattr(_event_collector_context, 'events', []))
        _event_collector_context.events = []

        if not events_to_flush:
            return 0

        from django.contrib.contenttypes.models import ContentType
        from django.db import transaction
        from trophies.models import Trophy, Concept, Event

        # Resolve ContentTypes once per flush rather than once per row.
        ct_map = {
            'trophy': ContentType.objects.get_for_model(Trophy),
            'concept': ContentType.objects.get_for_model(Concept),
        }

        instances = []
        for ev in events_to_flush:
            target_type = ev.get('target_type')
            # Defensive: run metadata through _serialize_metadata so any
            # future EventCollector.add_* method that includes a datetime
            # in its payload doesn't poison the bulk_create transaction.
            instances.append(Event(
                profile_id=ev['profile_id'],
                event_type=ev['event_type'],
                occurred_at=ev['occurred_at'],
                target_content_type=ct_map.get(target_type) if target_type else None,
                target_object_id=ev.get('target_id'),
                metadata=_serialize_metadata(ev.get('metadata', {})),
            ))

        with transaction.atomic():
            Event.objects.bulk_create(instances, batch_size=500)

        return len(instances)


@contextmanager
def event_collector(profile_id: int):
    """Thread-local context manager for collecting sync-pipeline events.

    Use alongside `sync_signal_suppressor` in the sync trophy loop:

        from trophies.sync_utils import sync_signal_suppressor
        from trophies.services.event_service import event_collector

        with sync_signal_suppressor(), event_collector(profile_id=profile.id):
            for trophy_data in batch:
                PsnApiService.create_or_update_earned_trophy_from_trophy_data(
                    profile, trophy, trophy_data,
                )
        # Events flush here, in their own atomic block, AFTER the sync
        # transactions have committed.

    Events are collected in memory during the context and flushed via
    `bulk_create` on exit. The flush is wrapped in try/except so event
    failures never break sync — events are best-effort, sync correctness is
    paramount.
    """
    EventCollector._activate(profile_id)
    try:
        yield
    finally:
        try:
            EventCollector._flush()
        except Exception:
            logger.exception(
                "EventCollector flush failed; events lost for profile %s",
                profile_id,
            )
        finally:
            EventCollector._deactivate()


# ---------------------------------------------------------------------------
# EventService: stateless recorders for service-action and signal-handled events
# ---------------------------------------------------------------------------

class EventService:
    """Stateless module of event recorders, one per event type.

    Used by service-action emitters (review, list publish, challenge create,
    profile linked) and signal-handled emitters (badges, milestones). Sync
    pipeline events use `EventCollector` instead — see `psn_api_service.py`
    and `token_keeper.py`.

    All recorders return the created `Event` (or `None` on failure) and log
    via `logger.exception()` if anything goes wrong. **Recorder failures must
    never propagate to the caller** — events are best-effort and should never
    block a user action like posting a review or completing a challenge.
    """

    @staticmethod
    def record_review_posted(review) -> Optional['Event']:
        """Record a `review_posted` event for a newly-created Review.

        Called from `ReviewService.create_review` after a successful create.
        """
        from django.contrib.contenttypes.models import ContentType
        from trophies.models import Review

        try:
            # `is_dlc_review` distinguishes DLC reviews from base game reviews
            # in the feed. Reviews always have a CTG attached (FK is non-null),
            # but the 'default' trophy_group_id is the base game by convention.
            ctg = review.concept_trophy_group
            is_dlc_review = bool(ctg and ctg.trophy_group_id != 'default')
            return _create_event_safely(
                profile=review.profile,
                event_type='review_posted',
                occurred_at=review.created_at,
                target_content_type=ContentType.objects.get_for_model(Review),
                target_object_id=review.pk,
                metadata={
                    'concept_id': review.concept_id,
                    'concept_slug': review.concept.slug if review.concept else None,
                    'concept_title': review.concept.unified_title if review.concept else None,
                    'recommended': review.recommended,
                    'is_dlc_review': is_dlc_review,
                    'dlc_name': ctg.display_name if is_dlc_review else None,
                },
            )
        except Exception:
            logger.exception("Failed to record review_posted event for review %s", getattr(review, 'pk', None))
            return None

    @staticmethod
    def record_game_list_published(game_list) -> Optional['Event']:
        """Record a `game_list_published` event when a list flips is_public False -> True.

        Called from `api/game_list_views.py:GameListUpdateView.patch()` after the
        save, only when the previous value was False AND `game_list.game_count > 0`
        (don't surface empty lists).
        """
        from django.contrib.contenttypes.models import ContentType
        from django.utils import timezone
        from trophies.models import GameList

        try:
            return _create_event_safely(
                profile=game_list.profile,
                event_type='game_list_published',
                occurred_at=timezone.now(),
                target_content_type=ContentType.objects.get_for_model(GameList),
                target_object_id=game_list.pk,
                metadata={
                    'list_name': game_list.name,
                    'game_count': getattr(game_list, 'game_count', 0),
                },
            )
        except Exception:
            logger.exception("Failed to record game_list_published event for list %s", getattr(game_list, 'pk', None))
            return None

    @staticmethod
    def record_challenge_started(challenge) -> Optional['Event']:
        """Record a `challenge_started` event for a newly-created Challenge.

        Called from each challenge create API view (AZ, Calendar, Genre).
        """
        from django.contrib.contenttypes.models import ContentType
        from trophies.models import Challenge

        try:
            return _create_event_safely(
                profile=challenge.profile,
                event_type='challenge_started',
                occurred_at=challenge.created_at,
                target_content_type=ContentType.objects.get_for_model(Challenge),
                target_object_id=challenge.pk,
                metadata={
                    'challenge_type': challenge.challenge_type,
                    'name': challenge.name,
                },
            )
        except Exception:
            logger.exception("Failed to record challenge_started event for challenge %s", getattr(challenge, 'pk', None))
            return None

    @staticmethod
    def record_challenge_progress(challenge, slots: list) -> Optional['Event']:
        """Record a coalesced `challenge_progress` event with slot details.

        Called once per `check_*_challenge_progress` invocation, after the
        bulk_update of slot completion. `slots` is a list of dicts describing
        the slots that flipped to completed during this check (NOT the entire
        slot state). Use the latest slot's `completed_at` as a dedup sentinel
        on retries — see the gotcha in event-system.md.
        """
        from django.contrib.contenttypes.models import ContentType
        from django.utils import timezone
        from trophies.models import Challenge

        if not slots:
            return None

        try:
            # _serialize_metadata converts datetimes to ISO strings, but we
            # also compute last_slot_completed_at here for convenience and
            # consistency across the metadata shape.
            last_completed = max(
                (s.get('completed_at') for s in slots if s.get('completed_at')),
                default=None,
            )
            return _create_event_safely(
                profile=challenge.profile,
                event_type='challenge_progress',
                occurred_at=timezone.now(),
                target_content_type=ContentType.objects.get_for_model(Challenge),
                target_object_id=challenge.pk,
                metadata={
                    'challenge_type': challenge.challenge_type,
                    'slots': slots,
                    'count': len(slots),
                    'last_slot_completed_at': (
                        last_completed.isoformat()
                        if hasattr(last_completed, 'isoformat')
                        else last_completed
                    ),
                },
            )
        except Exception:
            logger.exception("Failed to record challenge_progress event for challenge %s", getattr(challenge, 'pk', None))
            return None

    @staticmethod
    def record_challenge_completed(challenge) -> Optional['Event']:
        """Record a `challenge_completed` event when `challenge.is_complete` flips True.

        Called from `challenge_service.py` at each site that sets
        `challenge.is_complete = True`. Do NOT emit on un-completion (the
        False-reset path).
        """
        from django.contrib.contenttypes.models import ContentType
        from trophies.models import Challenge

        try:
            return _create_event_safely(
                profile=challenge.profile,
                event_type='challenge_completed',
                occurred_at=challenge.completed_at or challenge.created_at,
                target_content_type=ContentType.objects.get_for_model(Challenge),
                target_object_id=challenge.pk,
                metadata={
                    'challenge_type': challenge.challenge_type,
                    'name': challenge.name,
                },
            )
        except Exception:
            logger.exception("Failed to record challenge_completed event for challenge %s", getattr(challenge, 'pk', None))
            return None

    @staticmethod
    def record_profile_linked(profile) -> Optional['Event']:
        """Record a `profile_linked` event for a newly-linked profile.

        Called from `verification_service.link_profile_to_user` after the
        milestone check. The Profile model does not store a link timestamp,
        so `occurred_at` uses `timezone.now()` at emit time. This is one of
        the few cases where wall-clock time is correct (the link IS happening
        right now, by definition).
        """
        from django.contrib.contenttypes.models import ContentType
        from django.utils import timezone
        from trophies.models import Profile

        try:
            return _create_event_safely(
                profile=profile,
                event_type='profile_linked',
                occurred_at=timezone.now(),
                target_content_type=ContentType.objects.get_for_model(Profile),
                target_object_id=profile.pk,
                metadata={
                    'psn_username': profile.psn_username,
                },
            )
        except Exception:
            logger.exception("Failed to record profile_linked event for profile %s", getattr(profile, 'pk', None))
            return None

    @staticmethod
    def record_milestone_hit(user_milestone) -> Optional['Event']:
        """Record a `milestone_hit` event for a newly-awarded UserMilestone.

        Called from `milestone_service.check_and_award_milestone` and
        `award_milestone_directly` inside the `if created:` block.
        """
        from django.contrib.contenttypes.models import ContentType
        from trophies.models import UserMilestone

        try:
            milestone = user_milestone.milestone
            return _create_event_safely(
                profile=user_milestone.profile,
                event_type='milestone_hit',
                occurred_at=user_milestone.earned_at,
                target_content_type=ContentType.objects.get_for_model(UserMilestone),
                target_object_id=user_milestone.pk,
                metadata={
                    'milestone_id': milestone.pk,
                    'milestone_name': milestone.name,
                    'criteria_type': milestone.criteria_type,
                    'required_value': milestone.required_value,
                },
            )
        except Exception:
            logger.exception("Failed to record milestone_hit event for user_milestone %s", getattr(user_milestone, 'pk', None))
            return None

    @staticmethod
    def record_badge_earned(user_badge) -> Optional['Event']:
        """Record a single `badge_earned` event for a UserBadge (non-sync path only).

        Called from the `record_badge_earned_event` sibling receiver in
        `signals.py` ONLY when `is_bulk_update_active()` is False. Sync-path
        badge events use `record_bulk_badges_for_profile` instead so a single
        sync produces one coalesced event per profile, not one per badge.
        """
        from django.contrib.contenttypes.models import ContentType
        from trophies.models import UserBadge

        try:
            badge = user_badge.badge
            return _create_event_safely(
                profile=user_badge.profile,
                event_type='badge_earned',
                occurred_at=user_badge.earned_at,
                target_content_type=ContentType.objects.get_for_model(UserBadge),
                target_object_id=user_badge.pk,
                metadata={
                    'badges': [{
                        'badge_id': badge.pk,
                        'series_slug': badge.series_slug,
                        'name': badge.name,
                        'tier': badge.tier,
                    }],
                    'count': 1,
                },
            )
        except Exception:
            logger.exception("Failed to record badge_earned event for user_badge %s", getattr(user_badge, 'pk', None))
            return None

    @staticmethod
    def record_bulk_badges_for_profile(profile, user_badges: list) -> Optional['Event']:
        """Record a single coalesced `badge_earned` event for many badges (sync path).

        Called from `token_keeper.py` after `check_profile_badges` runs in
        `_job_sync_complete`. Avoids per-badge event spam during sync by
        emitting one event per profile per sync, with `metadata['badges']`
        listing every badge awarded during the sync.

        `user_badges` is a list of UserBadge instances created during this
        sync for the given profile.
        """
        if not user_badges:
            return None

        from django.contrib.contenttypes.models import ContentType
        from django.utils import timezone
        from trophies.models import UserBadge

        try:
            badges_meta = []
            for ub in user_badges:
                badge = ub.badge
                badges_meta.append({
                    'badge_id': badge.pk,
                    'series_slug': badge.series_slug,
                    'name': badge.name,
                    'tier': badge.tier,
                })

            # Use the most recent earn time as occurred_at so the coalesced
            # event surfaces near the end of the sync window in the feed.
            most_recent = max(
                (ub.earned_at for ub in user_badges if ub.earned_at),
                default=timezone.now(),
            )

            return _create_event_safely(
                profile=profile,
                event_type='badge_earned',
                occurred_at=most_recent,
                # No single FK target — coalesced events point at the first
                # badge for navigation but list all in metadata.
                target_content_type=ContentType.objects.get_for_model(UserBadge),
                target_object_id=user_badges[0].pk,
                metadata={
                    'badges': badges_meta,
                    'count': len(badges_meta),
                    'coalesced': True,
                },
            )
        except Exception:
            logger.exception("Failed to record bulk badge_earned event for profile %s", getattr(profile, 'pk', None))
            return None

    @staticmethod
    def get_recent_counts(window_hours: int = 24) -> dict:
        """Aggregate recent event counts for the Pursuit Feed "Right Now" rail module.

        Returns a dict shaped::

            {
                'window_hours': 24,
                'total': 1234,
                'platinum_earned': 56,
                'rare_trophy_earned': 89,
                'concept_100_percent': 12,
                'badge_earned': 78,
                'milestone_hit': 23,
                'review_posted': 34,
                'game_list_published': 5,
                'challenge_started': 7,
                'challenge_progress': 11,
                'challenge_completed': 3,
                'profile_linked': 2,
            }

        Cached for 60 seconds in the default Django cache so the feed page
        and any other surface that surfaces these counts only pays one query
        per minute. The cache key includes the window so different windows
        cache independently.
        """
        from django.core.cache import cache
        from django.db.models import Count
        from django.utils import timezone
        from trophies.models import Event

        cache_key = f'event:recent_counts:{window_hours}h'
        cached = cache.get(cache_key)
        if cached is not None:
            return cached

        try:
            since = timezone.now() - timezone.timedelta(hours=window_hours)
            rows = (
                Event.objects
                .feed_visible()
                .filter(occurred_at__gte=since, event_type__in=PURSUIT_FEED_TYPES)
                .values('event_type')
                .annotate(count=Count('id'))
            )
            counts = {'window_hours': window_hours, 'total': 0}
            for row in rows:
                counts[row['event_type']] = row['count']
                counts['total'] += row['count']
        except Exception:
            logger.exception("Failed to compute event recent counts for window %sh", window_hours)
            counts = {'window_hours': window_hours, 'total': 0}

        cache.set(cache_key, counts, 60)
        return counts
