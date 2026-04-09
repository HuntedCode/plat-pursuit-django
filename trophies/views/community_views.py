"""Views for the standalone Community Hub surfaces.

Currently houses CommunityFeedView (the /community/feed/ page introduced in
Phase 8 of the Community Hub initiative). The Community Hub landing page
itself lives in core/views.py:CommunityHubView for symmetry with HomeView.
"""
import logging
from datetime import timedelta

from django.urls import reverse_lazy
from django.utils import timezone
from django.views.generic import ListView

from trophies.mixins import HtmxListMixin, ProfileHotbarMixin
from trophies.models import Event
from trophies.services.event_service import (
    EventService,
    PURSUIT_FEED_TYPES,
    TROPHY_FEED_TYPES,
)

logger = logging.getLogger(__name__)


# Feed mode constants. The query string uses ?feed_mode=pursuit (default)
# or ?feed_mode=trophy. Anything else falls back to pursuit.
FEED_MODE_PURSUIT = 'pursuit'
FEED_MODE_TROPHY = 'trophy'
VALID_FEED_MODES = (FEED_MODE_PURSUIT, FEED_MODE_TROPHY)

# Time range options for the ?time_range= filter. Maps query value to
# a timedelta or None for "all time". Default is the most recent 7 days
# so the page lands on a fresh, recently-active slice rather than the
# full history (which can be heavy on a mature feed).
TIME_RANGE_DELTAS = {
    '24h': timedelta(hours=24),
    '7d': timedelta(days=7),
    '30d': timedelta(days=30),
    'all': None,
}
DEFAULT_TIME_RANGE = '7d'

# Display labels for the event-type chip set. Any valid type that's missing
# from this map falls back to a Title Case rendering of the slug, but we'd
# rather give every shipped type a hand-tuned label so the UI reads cleanly.
EVENT_TYPE_LABELS = {
    'platinum_earned': 'Platinums',
    'rare_trophy_earned': 'Ultra-rare trophies',
    'concept_100_percent': '100% completions',
    'badge_earned': 'Badges',
    'milestone_hit': 'Milestones',
    'review_posted': 'Reviews',
    'game_list_published': 'Lists',
    'challenge_started': 'Challenges started',
    'challenge_progress': 'Challenge progress',
    'challenge_completed': 'Challenges completed',
    'profile_linked': 'New hunters',
}


def _resolve_feed_mode(raw):
    """Normalize the ?feed_mode= query parameter to a valid mode."""
    if raw in VALID_FEED_MODES:
        return raw
    return FEED_MODE_PURSUIT


def _resolve_time_range(raw):
    """Normalize the ?time_range= query parameter to a valid key."""
    if raw in TIME_RANGE_DELTAS:
        return raw
    return DEFAULT_TIME_RANGE


def _allowed_event_types_for_mode(mode):
    """Return the set of event types valid for a given feed mode."""
    if mode == FEED_MODE_TROPHY:
        return TROPHY_FEED_TYPES
    return PURSUIT_FEED_TYPES


class CommunityFeedView(HtmxListMixin, ProfileHotbarMixin, ListView):
    """Standalone full-page Pursuit Feed at /community/feed/.

    Two switchable modes via ?feed_mode= query param: 'pursuit' (default)
    surfaces every event type in PURSUIT_FEED_TYPES, 'trophy' restricts
    to TROPHY_FEED_TYPES (platinums, ultra-rare trophies, 100% completions).

    Filter set:
    - feed_mode: 'pursuit' | 'trophy' (default 'pursuit')
    - event_type: multi-select. When given, must be a subset of the valid
      types for the active mode (otherwise the filter is silently dropped).
    - time_range: '24h' | '7d' | '30d' | 'all' (default '7d')

    HTMX-driven via the existing browse-filters.js + HtmxListMixin pattern.
    Standard Django ListView pagination provides page_obj/paginator/is_paginated
    for the htmx_pagination.html partial.

    Public access — no auth required. The viewer profile is used only for
    "your own row" highlights inside the feed entries (Phase 7 introduces
    the same pattern in the leaderboard modules); for v1 we leave that
    visual touch to a follow-up since the feed entries don't yet have a
    "current user" highlight design.
    """
    model = Event
    template_name = 'community/feed.html'
    partial_template_name = 'community/partials/feed_results.html'
    paginate_by = 25
    context_object_name = 'events'

    def get_queryset(self):
        # `feed_visible()` filters out events whose target was soft-deleted
        # (e.g. a review_posted whose Review was later soft-deleted). All
        # read surfaces use this — see docs/architecture/event-system.md.
        qs = (
            Event.objects
            .feed_visible()
            .select_related('profile', 'target_content_type')
            .order_by('-occurred_at')
        )

        # ── Mode filter ─────────────────────────────────────────────────
        mode = _resolve_feed_mode(self.request.GET.get('feed_mode'))
        valid_types = _allowed_event_types_for_mode(mode)

        # ── Event type chips (multi-select) ─────────────────────────────
        # `getlist` returns all values for the same key. Filter out anything
        # that's not valid for the current mode so a stale URL from a
        # mode-switch doesn't return an empty page.
        requested_types = self.request.GET.getlist('event_type')
        active_types = [t for t in requested_types if t in valid_types]
        if active_types:
            qs = qs.filter(event_type__in=active_types)
        else:
            # No type chips selected: fall back to all valid types for the
            # current mode. This is what makes the mode toggle do anything.
            qs = qs.filter(event_type__in=valid_types)

        # ── Time range ──────────────────────────────────────────────────
        time_range = _resolve_time_range(self.request.GET.get('time_range'))
        delta = TIME_RANGE_DELTAS[time_range]
        if delta is not None:
            qs = qs.filter(occurred_at__gte=timezone.now() - delta)

        return qs

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)

        # Resolve the same way the queryset does so the template can render
        # the active state of the mode toggle and chips.
        mode = _resolve_feed_mode(self.request.GET.get('feed_mode'))
        time_range = _resolve_time_range(self.request.GET.get('time_range'))
        valid_types = _allowed_event_types_for_mode(mode)
        requested_types = self.request.GET.getlist('event_type')
        active_types = set(t for t in requested_types if t in valid_types)

        context['feed_mode'] = mode
        context['time_range'] = time_range
        # Sorted list of (slug, label) tuples for the current mode's chip set.
        # Labels come from EVENT_TYPE_LABELS so the UI reads cleanly instead
        # of rendering raw slugs like "platinum_earned".
        context['valid_event_types'] = sorted(
            ((t, EVENT_TYPE_LABELS.get(t, t.replace('_', ' ').title())) for t in valid_types),
            key=lambda pair: pair[1],
        )
        context['active_event_types'] = active_types
        context['time_range_options'] = [
            ('24h', 'Last 24h'),
            ('7d', 'Last 7 days'),
            ('30d', 'Last 30 days'),
            ('all', 'All time'),
        ]
        context['feed_mode_options'] = [
            (FEED_MODE_PURSUIT, 'Pursuit Feed', 'Everything: trophies, badges, reviews, lists, challenges'),
            (FEED_MODE_TROPHY, 'Trophy Feed', 'Platinums, ultra-rare trophies, and 100% completions only'),
        ]

        # "Right Now" rail module: live event counts in the last 24h.
        # Cached for 60s inside get_recent_counts so re-renders are cheap.
        # Wrapped in try/except so a failed cache fetch never breaks the
        # whole page render — the rail module gracefully hides on None.
        try:
            context['recent_counts'] = EventService.get_recent_counts(window_hours=24)
        except Exception:
            logger.exception("Failed to load recent_counts for community feed page")
            context['recent_counts'] = None

        # SEO + breadcrumb (only on the full-page render — HtmxListMixin
        # short-circuits to the partial template on htmx requests, where
        # this context is unused).
        context['seo_title'] = 'Pursuit Feed - Community Hub'
        context['seo_description'] = (
            "Browse the full Pursuit Feed: every platinum, badge, review, "
            "and challenge across the PlatPursuit community."
        )
        context['breadcrumb'] = [
            {'text': 'Home', 'url': reverse_lazy('home')},
            {'text': 'Community Hub', 'url': reverse_lazy('community_hub')},
            {'text': 'Pursuit Feed'},
        ]
        return context
