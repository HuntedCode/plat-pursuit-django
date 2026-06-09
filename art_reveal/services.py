"""Counting + reveal reconciliation for the Badge Art Reveal event.

``compute_badge_platinum_count`` and ``reconcile_event`` are HEAVY (community-
wide aggregation) and must run only off the request path (the cron). The request
path reads the cheap cached ``ArtRevealEvent.last_platinum_count`` instead.
"""

import logging

from django.core.cache import cache
from django.db import transaction
from django.utils import timezone

from trophies.models import Badge, Concept, EarnedTrophy

from .models import ArtRevealEvent

logger = logging.getLogger(__name__)

# Games whose shovelware status disqualifies their platinums from the count.
_SHOVELWARE_EXCLUDED = ('auto_flagged', 'manually_flagged')

# Cache key for the active-event lookup (mirrors plat_pursuit.context_processors
# .active_fundraiser). Stores the pk, or 0 to mean "no active event".
_ACTIVE_CACHE_KEY = 'art_reveal:active_event_pk'
_ACTIVE_CACHE_TTL = 60

# Cache key for the fully-rendered banner payload (plain primitives). Lets the
# site-wide banner do zero per-request DB work after a warm cache.
_BANNER_CACHE_KEY = 'art_reveal:banner_payload'
_BANNER_CACHE_TTL = 60


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
        .exclude(trophy__game__shovelware_status__in=_SHOVELWARE_EXCLUDED)
        .count()
    )


def reconcile_event(event, *, now=None):
    """Recount community platinums, store the result, and release any items whose
    threshold has been crossed. Idempotent and concurrency-safe (the event row is
    locked, so two overlapping crons can't double-release)."""
    now = now or timezone.now()
    # Count BEFORE locking: the aggregation can take seconds, and we don't want to
    # hold the event row lock that whole time. Releases are forward-only and
    # idempotent, so a count that drifts between here and the lock self-corrects
    # on the next run.
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
    # Refresh the banner immediately after a reveal rather than waiting out the TTL.
    cache.delete_many([_ACTIVE_CACHE_KEY, _BANNER_CACHE_KEY])

    # Post-commit: complete the fundraiser badge-artwork claim for every REVEALED
    # badge whose claim is still pending (credits the funder on all tiers + sends
    # the artwork-complete email/notification). An event-wide sweep, so it
    # self-heals badges that were revealed before this hook existed or via any
    # other path. Kept out of the locked transaction above.
    _complete_pending_claims_for_event(event)

    return {'count': count, 'target': min(count // per, event.items.count()), 'released': released}


def _complete_pending_claims_for_event(event):
    """Complete the fundraiser badge-artwork claim for every revealed badge in the
    event whose claim isn't done yet. Cross-app and best-effort: a fundraiser-side
    failure must not break the reveal. One query finds only the still-pending claims,
    so a fully-credited event costs nothing here."""
    try:
        from fundraiser.models import DonationBadgeClaim
        from fundraiser.services.donation_service import DonationService

        pending = (
            DonationBadgeClaim.objects
            .filter(
                badge__art_reveal_items__event=event,
                badge__art_reveal_items__released=True,
            )
            .exclude(status='completed')
            .select_related('profile')
            .distinct()
        )
        for claim in pending:
            DonationService.complete_badge_claim(claim)
    except Exception:
        logger.exception("art_reveal: failed to complete pending badge claims on reveal")


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


def get_active_banner():
    """Request-path-safe banner payload (plain primitives) for the active event, or
    None when no banner should show. Cached for the TTL so the site-wide banner adds
    no per-request DB work after a warm cache (the count + latest-unlock lookups run
    at most once per TTL, not once per render). Invalidated on each reveal."""
    MISS = object()
    cached = cache.get(_BANNER_CACHE_KEY, MISS)
    if cached is not MISS:
        return cached  # may legitimately be None ("no active banner")

    event = get_active_event()
    if not event or not event.show_banner():
        cache.set(_BANNER_CACHE_KEY, None, _BANNER_CACHE_TTL)
        return None

    latest = (
        event.items.filter(released=True)
        .select_related('badge', 'badge__base_badge').order_by('-order').first()
    )
    payload = {'name': event.name, 'slug': event.slug, 'progress': event.progress(), 'latest': None}
    if latest:
        payload['latest'] = {
            'series_title': latest.badge.effective_display_series or latest.badge.name,
            'series_slug': latest.badge.series_slug,
            'artwork_url': latest.artwork.url,
        }
    cache.set(_BANNER_CACHE_KEY, payload, _BANNER_CACHE_TTL)
    return payload
