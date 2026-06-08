"""Counting + reveal reconciliation for the Badge Art Reveal event.

``compute_badge_platinum_count`` and ``reconcile_event`` are HEAVY (community-
wide aggregation) and must run only off the request path (the cron). The request
path reads the cheap cached ``ArtRevealEvent.last_platinum_count`` instead.
"""

from django.core.cache import cache
from django.db import transaction
from django.utils import timezone

from trophies.models import Badge, Concept, EarnedTrophy

from .models import ArtRevealEvent

# Games whose shovelware status disqualifies their platinums from the count.
_SHOVELWARE_FLAGGED = ('auto_flagged', 'manually_flagged')

# Cache key for the active-event lookup (mirrors plat_pursuit.context_processors
# .active_fundraiser). Stores the pk, or 0 to mean "no active event".
_ACTIVE_CACHE_KEY = 'art_reveal:active_event_pk'
_ACTIVE_CACHE_TTL = 60


def _badge_concept_ids():
    """All concept ids covered by at least one badge's stages (Badge and Stage
    are linked by ``series_slug``; Stage.concepts is the M2M to Concept)."""
    return (
        Concept.objects
        .filter(stages__series_slug__in=Badge.objects.values_list('series_slug', flat=True))
        .values_list('id', flat=True)
        .distinct()
    )


def compute_badge_platinum_count(*, since):
    """Community-wide count of platinum trophies earned since ``since`` on
    non-shovelware games covered by a badge. DB-aggregated (no Python iteration).

    The badge-covered concept ids are passed as an ``__in`` subquery so each
    EarnedTrophy is counted exactly once (no join multiplication, no DISTINCT)."""
    return (
        EarnedTrophy.objects
        .filter(
            earned=True,
            earned_date_time__gte=since,
            trophy__trophy_type='platinum',
            trophy__game__concept_id__in=_badge_concept_ids(),
        )
        .exclude(trophy__game__shovelware_status__in=_SHOVELWARE_FLAGGED)
        .count()
    )


def reconcile_event(event, *, now=None):
    """Recount community platinums, store the result, and release any items whose
    threshold has been crossed. Idempotent and concurrency-safe (the event row is
    locked, so two overlapping crons can't double-release)."""
    now = now or timezone.now()
    count = compute_badge_platinum_count(since=event.started_at)
    per = event.platinums_per_reveal or 1

    released = []
    with transaction.atomic():
        ev = ArtRevealEvent.objects.select_for_update().get(pk=event.pk)
        total = ev.items.count()
        target = min(count // per, total)
        for item in ev.items.filter(released=False, order__lte=target).order_by('order'):
            if item.release(now=now):
                released.append(item.order)
        ArtRevealEvent.objects.filter(pk=ev.pk).update(
            last_platinum_count=count, last_counted_at=now,
        )
    return {'count': count, 'target': min(count // per, event.items.count()), 'released': released}


def get_active_event():
    """The single live event for banner/page use, cached by pk for 60s (mirrors
    the fundraiser banner lookup). Returns None when nothing is live."""
    def _fetch_pk():
        ev = (
            ArtRevealEvent.objects
            .filter(is_active=True)
            .order_by('-started_at')
            .first()
        )
        # Re-validate is_live() (date window) outside the cache below; cache only
        # the candidate pk so a date rollover invalidates within the TTL.
        return ev.pk if ev else 0

    pk = cache.get_or_set(_ACTIVE_CACHE_KEY, _fetch_pk, _ACTIVE_CACHE_TTL)
    if not pk:
        return None
    event = ArtRevealEvent.objects.filter(pk=pk).first()
    if event and event.is_live():
        return event
    return None
