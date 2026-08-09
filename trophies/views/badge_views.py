import logging
from collections import defaultdict

from core.services.tracking import track_page_view
from trophies.constants import EVALUATABLE_BADGE_TYPES
from trophies.services.xp_service import get_tier_xp
from trophies.util_modules.constants import BADGE_TIER_XP


def _badge_xp(badge):
    """Compute total XP value for a badge tier."""
    return badge.required_stages * get_tier_xp(badge.tier) + BADGE_TIER_XP


# Personal-state chips on the group-badge list (Gallery + Series): binary hold only (per-badge in-progress is
# engine-derived, not whale-safe across a catalog -- it lives on the badge detail page).
_GALLERY_STATES = ('earned', 'unearned')
GALLERY_PAGE_SIZE = 48  # medallions per page (a multiple of common 2/3/4/6-column grids)
SERIES_PAGE_SIZE = 30   # series rows per page (Series view infinite scroll)
# (key, label). Order mirrors the Collection Gallery's sort dropdown.
GALLERY_SORTS = [
    ('set_number', 'Set order'),
    ('name', 'Name (A-Z)'),
    ('rarity', 'Rarest first'),
    ('popular', 'Most earned'),
    ('newest', 'Newest'),
]
GALLERY_SORT_KEYS = {k for k, _ in GALLERY_SORTS}
GALLERY_SORT_DEFAULT = 'set_number'
# Series-view sorts (per-series tiles). No tier/XP/closest sorts -- the grouping model has no tier ladder.
SERIES_SORTS = [
    ('name', 'Name (A-Z)'),
    ('popular', 'Most earned'),
    ('rarity', 'Rarest first'),
    ('newest', 'Newest'),
]
SERIES_SORT_KEYS = {k for k, _ in SERIES_SORTS}
SERIES_SORT_DEFAULT = 'name'

from django.core.cache import cache
from django.core.exceptions import ObjectDoesNotExist
from django.core.paginator import Paginator
from django.db.models import Q, F, Exists, OuterRef, Count, Sum
from django.db.models.functions import Lower
from django.http import Http404, HttpResponseRedirect, HttpResponseNotFound
from urllib.parse import urlencode
from django.shortcuts import get_object_or_404, render
from django.urls import reverse_lazy, reverse
from django.utils.text import slugify
from django.views import View
from django.views.generic import ListView, DetailView, TemplateView

from ..models import (
    Profile, Badge, UserBadge,
    Milestone, UserMilestone, UserMilestoneProgress,
    UserTitle, ProfileGamification, BadgeSeries, GroupBadge, UserGroupBadge, PlatformGroup,
)
from ..forms import BadgeSearchForm
from trophies.services.badge_detail_service import get_badge_detail
from trophies.services.badge_list_service import build_list_cards, build_series_items
from trophies.services.badge_rarity import (
    annotate_group_rarity, RARITY_CLASSES, RARITY_FILTER_CHOICES, RARITY_UNEARNED,
)
from trophies.services.frame_service import build_badge_frame
from trophies.services.redis_leaderboard_service import (
    RedisPaginator, RedisPage,
    get_xp_page, get_xp_rank, get_xp_count,
    get_earners_page, get_earners_rank, get_earners_count,
    get_progress_page, get_progress_rank, get_progress_count,
    get_community_xp,
    get_country_xp_page, get_country_xp_rank, get_country_xp_count,
    get_active_country_codes,
)

logger = logging.getLogger("psn_api")


class BadgeListView(ListView):
    """
    Display list of all badge series with progress tracking for authenticated users.

    Shows tier 1 badges for each series, with earned status and completion progress
    for logged-in users. Includes trophy totals and game counts for each series.
    """
    model = Badge
    template_name = 'trophies/badge_list.html'
    context_object_name = 'display_data'
    paginate_by = None

    def _view_mode(self):
        """'gallery' (the per-tier medallion wall) or 'series' (the default per-series rows)."""
        return 'gallery' if self.request.GET.get('view') == 'gallery' else 'series'

    def _profile(self):
        user = self.request.user
        return user.profile if user.is_authenticated and hasattr(user, 'profile') else None

    def _live_platform_groups(self):
        """Platform groups with at least one live group badge -- the Gallery's group filter chips."""
        return list(
            PlatformGroup.objects.filter(group_badges__is_live=True).distinct().order_by('sort_order', 'name')
        )

    def get_template_names(self):
        gallery = self._view_mode() == 'gallery'
        htmx_results = getattr(self.request, 'htmx', False) and self.request.htmx.target == 'browse-results'
        htmx_view = getattr(self.request, 'htmx', False) and self.request.htmx.target == 'badge-view'
        xhr = self.request.headers.get('X-Requested-With') == 'XMLHttpRequest'
        if gallery:
            # The view-toggle HTMX swap (target='badge-view') returns just the Gallery island; an
            # InfiniteScroller page fetch (XHR) or a filter HTMX swap returns just the grid; else the full page.
            if htmx_view:
                return ['trophies/partials/badge_list/gallery.html']
            if htmx_results or xhr:
                return ['trophies/partials/badge_list/gallery_results.html']
            return ['trophies/badge_list.html']
        # Series: same model -- the view-toggle swap returns the Series island; a filter HTMX swap OR an
        # InfiniteScroller page fetch returns just the rows partial; the full page otherwise.
        if htmx_view:
            return ['trophies/partials/badge_list/series_view.html']
        if htmx_results or xhr:
            return ['trophies/partials/badge_list/browse_results.html']
        return super().get_template_names()

    def get_filter_form(self):
        if not hasattr(self, '_filter_form'):
            self._filter_form = BadgeSearchForm(self.request.GET)
        return self._filter_form

    def get_queryset(self):
        if self._view_mode() == 'gallery':
            return self._gallery_queryset()
        return self._series_queryset()

    def _series_queryset(self):
        """Series view: one BadgeSeries row per series that has a live group badge. Every filter (name search /
        type / auth completion) + sort is DB-side so it paginates at catalog scale. _live_groups gates to
        series that actually ship a live badge; _earned_total (the sum of the series' live-group earners) drives
        the popularity + rarity sorts. Personal state is a binary hold (holds >=1 live group / holds none) --
        whale-safe; per-badge tier progress is engine-derived and lives on the detail page."""
        qs = BadgeSeries.objects.annotate(
            _live_groups=Count('group_badges', filter=Q(group_badges__is_live=True)),
            _earned_total=Sum('group_badges__earned_count', filter=Q(group_badges__is_live=True)),
        ).filter(_live_groups__gt=0).select_related('franchise', 'collection', 'developer')
        g = self.request.GET

        form = self.get_filter_form()
        if form.is_valid():
            raw = (form.cleaned_data.get('series_slug') or '').strip()
            if raw:
                qs = qs.filter(Q(series_slug__icontains=slugify(raw)) | Q(name__icontains=raw))
        types = [t for t in g.getlist('badge_type') if t]
        if types:
            qs = qs.filter(badge_type__in=types)

        # Personal-state chips (auth only): Earned = holds >=1 live group badge in the series; Not-earned =
        # holds none. One binary-hold EXISTS probe; both chips selected = no filter.
        profile = self._profile()
        states = [s for s in g.getlist('state') if s in _GALLERY_STATES] if profile else []
        if states and not ('earned' in states and 'unearned' in states):
            held = Exists(UserGroupBadge.objects.filter(
                profile=profile, group_badge__series=OuterRef('pk'), group_badge__is_live=True,
            ))
            qs = qs.annotate(_held=held).filter(_held=('earned' in states))

        # Live-rarity multi-select (OR'd): keep series that ship at least one live group badge in a selected
        # class (or, for "Be the first", a not-yet-earned one). Reuses the SAME rarity annotation on the group
        # badges (so it agrees with the displayed grade), wrapped in an EXISTS -- no per-series Python work.
        raw_rarity = g.getlist('rarity')
        rarities = [r for r in raw_rarity if r in RARITY_CLASSES]
        want_unearned = RARITY_UNEARNED in raw_rarity
        if rarities or want_unearned:
            inner = GroupBadge.objects.filter(series=OuterRef('pk'), is_live=True)
            cond = Q()
            if rarities:
                inner = annotate_group_rarity(inner)
                cond |= Q(_rarity__in=rarities)
            if want_unearned:
                cond |= Q(earned_count=0)
            qs = qs.filter(Exists(inner.filter(cond)))

        # Every order_by ends on 'pk' -- a unique final tiebreak so infinite-scroll pages don't reorder ties.
        name_key = Lower('name')
        sort = g.get('sort') if g.get('sort') in SERIES_SORT_KEYS else SERIES_SORT_DEFAULT
        if sort == 'popular':
            qs = qs.order_by(F('_earned_total').desc(nulls_last=True), name_key, 'pk')
        elif sort == 'rarity':
            qs = qs.order_by(F('_earned_total').asc(nulls_last=True), name_key, 'pk')
        elif sort == 'newest':
            qs = qs.order_by('-created_at', name_key, 'pk')
        else:                                                       # name (default)
            qs = qs.order_by(name_key, 'pk')
        return qs

    def _gallery_queryset(self):
        """The Browse Gallery's per-GROUP-BADGE queryset: one row per live GroupBadge (series x platform group),
        every filter + sort DB-side so it paginates at catalog scale. select_related covers the series +
        platform_group FKs the batched card builder (badge_list_service) reads, so it issues no per-card FK
        queries. Personal state is a binary hold Exists (earned / not) -- whale-safe; per-badge in-progress is
        engine-derived and lives on the detail page."""
        qs = GroupBadge.objects.filter(is_live=True).select_related(
            'series', 'series__franchise', 'series__collection', 'series__developer', 'platform_group',
        )
        g = self.request.GET

        # MULTI-select chips (OR'd): platform group (Legacy HD / Ultra HD ...) + badge type (on the series).
        groups = [k for k in g.getlist('group') if k]
        if groups:
            qs = qs.filter(platform_group__key__in=groups)
        types = [t for t in g.getlist('badge_type') if t]
        if types:
            qs = qs.filter(series__badge_type__in=types)
        q = (g.get('q') or '').strip()
        if q:
            search_q = Q(series__series_slug__icontains=slugify(q)) | Q(series__name__icontains=q)
            # A numeric query (optionally "#0042") also matches the badge's edition/set number.
            numeric = q.lstrip('#')
            if numeric.isdigit() and len(numeric) <= 9:   # fits a PositiveIntegerField; guards absurd input
                search_q |= Q(set_number=int(numeric))
            qs = qs.filter(search_q)

        # Personal-state multi-select (auth only): Earned / Not-earned, OR'd. One binary-hold EXISTS probe.
        profile = self._profile()
        states = [s for s in g.getlist('state') if s in _GALLERY_STATES] if profile else []
        if states and not ('earned' in states and 'unearned' in states):   # both selected = no filter
            held = Exists(UserGroupBadge.objects.filter(profile=profile, group_badge=OuterRef('pk')))
            qs = qs.annotate(_earned=held).filter(_earned=('earned' in states))

        # Live-rarity multi-select (OR'd): keep badges whose derived class is selected, plus a "Be the first"
        # option for the not-yet-earned (earned_count == 0, exactly like the card nudge). The pursuer subquery is
        # added ONLY when a real rarity class is picked, so "Be the first" alone stays a plain indexed filter.
        raw_rarity = g.getlist('rarity')
        rarities = [r for r in raw_rarity if r in RARITY_CLASSES]
        want_unearned = RARITY_UNEARNED in raw_rarity
        if rarities or want_unearned:
            cond = Q()
            if rarities:
                qs = annotate_group_rarity(qs)
                cond |= Q(_rarity__in=rarities)
            if want_unearned:
                cond |= Q(earned_count=0)
            qs = qs.filter(cond)

        # SET ORDER is the canonical default + the tiebreaker within every other sort (so cards fall back to a
        # stable order on ties). Every order_by ends on 'pk' -- a unique final tiebreak so infinite-scroll
        # pages don't reorder ties (duplicated / skipped medallions).
        name_key = Lower('series__name')
        set_order = (F('set_number').asc(nulls_last=True), name_key)
        sort = g.get('sort') if g.get('sort') in GALLERY_SORT_KEYS else GALLERY_SORT_DEFAULT
        if sort == 'name':
            qs = qs.order_by(name_key, *set_order, 'pk')
        elif sort == 'rarity':
            qs = qs.order_by('earned_count', *set_order, 'pk')    # fewest earners = rarest first
        elif sort == 'popular':
            qs = qs.order_by('-earned_count', *set_order, 'pk')
        elif sort == 'newest':
            qs = qs.order_by('-created_at', *set_order, 'pk')
        else:                                                      # set_number (default)
            qs = qs.order_by(*set_order, 'pk')
        return qs

    def _gallery_context_data(self, **kwargs):
        """Build the Browse Gallery context: paginate the per-tier queryset (self.object_list), then
        batch-build SHOWCASE frames for the page. Whale-safe -- three bulk maps + include_live_stats=False
        means zero per-badge queries/Redis (the frame_service prescribed batch path)."""
        context = super().get_context_data(**kwargs)
        paginator = Paginator(self.object_list, GALLERY_PAGE_SIZE)
        page_obj = paginator.get_page(self.request.GET.get('page'))
        profile = self._profile()

        # InfiniteScroller walks pages 2,3,... via XHR; Paginator.get_page CLAMPS an out-of-range page to
        # the last one, which would loop forever re-appending it. For an XHR fetch past the end, emit no
        # cards so the scroller sees zero and stops.
        try:
            requested_page = int(self.request.GET.get('page') or 1)
        except (TypeError, ValueError):
            requested_page = 1
        is_xhr = (
            self.request.headers.get('X-Requested-With') == 'XMLHttpRequest'
            or (getattr(self.request, 'htmx', False) and self.request.htmx.target == 'browse-results')
        )
        page_badges = [] if (is_xhr and requested_page > paginator.num_pages) else list(page_obj)

        # Batched, whale-safe: two bulk queries (pursuer counts + the viewer's holds) for the whole page, not
        # per card. Live rarity + binary hold + the showcase medallion frame come back on each card dict.
        cards = build_list_cards(page_badges, profile)

        g = self.request.GET
        context.update({
            'view': 'gallery',
            'gallery_cards': cards,
            'page_obj': page_obj,
            'paginator': paginator,
            'is_paginated': page_obj.has_other_pages(),
            'form': self.get_filter_form(),                 # supplies the badge_type choices for the chips
            'gallery_authed': profile is not None,
            'gallery_groups': self._live_platform_groups(),  # platform-group filter chips (Legacy HD / Ultra HD)
            'gallery_groups_selected': g.getlist('group'),
            'gallery_states': g.getlist('state'),
            'gallery_types': g.getlist('badge_type'),
            'rarity_choices': RARITY_FILTER_CHOICES,           # shared rarity filter chips (both views)
            'selected_rarities': g.getlist('rarity'),
            'gallery_sort': g.get('sort') if g.get('sort') in GALLERY_SORT_KEYS else GALLERY_SORT_DEFAULT,
            'gallery_q': g.get('q', ''),
            'gallery_sorts': GALLERY_SORTS,
            'gallery_page_size': GALLERY_PAGE_SIZE,          # keeps the JS paginateBy in sync (no magic 48)
            'catalog_stats': self._catalog_header_stats(),  # generalized collection stats (hourly-cached)
            'forge_meds': self._forge_medallions(),          # sample edition medallions for the header explainer
            'breadcrumb': [
                {'text': 'Home', 'url': reverse_lazy('home')},
                {'text': 'Badges'},
            ],
            'seo_description': (
                "Browse every badge on Platinum Pursuit -- filter by platform, rarity, and type to find "
                "your next platinum to chase."
            ),
        })
        # Only count a real page view, not each infinite-scroll ?page=N XHR fetch (which would inflate it).
        if not is_xhr:
            track_page_view('badges_list', 'gallery', self.request)
        return context

    def _catalog_header_stats(self):
        """The badge-COLLECTION catalog stats for the header (what the collection OFFERS, not the
        viewer's own progress -- this is a browse page): badge series, stages to complete, total
        earnable XP, and new-this-week. All read from the hourly-cached site heartbeat, so it's
        zero DB cost on the request path; the grid only shows once the cron has warmed the cache
        (the template gates on earnable_xp)."""
        from core.services.site_heartbeat import get_cached_heartbeat
        expanded = (get_cached_heartbeat() or {}).get('expanded') or {}

        def _value(key):
            return (expanded.get(key) or {}).get('value')

        return {
            'series': _value('badges_total'),
            'series_new': (expanded.get('badges_total') or {}).get('delta'),
            'stages': _value('badge_stages_total'),
            'earnable_xp': _value('badge_earnable_xp'),
        }

    @staticmethod
    def _forge_medallions():
        """Edition medallions for the header's 'how badges work' forge journey. A teaching abstraction, so it
        composes a REAL badge's subject art onto each edition's metal plate (Ultra HD -> platinum, Legacy HD ->
        gold): the journey's claim/master beats + the editions legend show the genuine .pp-med OBJECT with real
        artwork rather than a bare plate. Picks a representative live badge that HAS custom art (most-earned
        first, bounded scan); falls back to the plain metal plate on an empty catalog (fresh install / tests)."""
        from django.templatetags.static import static

        def plate(metal):
            n = 4 if metal == 'platinum' else 3   # 4_backdrop = platinum plate, 3_backdrop = gold plate
            return static(f'images/badges/backdrops/{n}_backdrop.png')

        subject, name, source_id, is_avatar = None, 'Platinum Pursuit', None, False
        for cand in (GroupBadge.objects.filter(is_live=True)
                     .select_related('series', 'series__submitted_by', 'platform_group')
                     .order_by('-earned_count', 'id')[:12]):
            art = cand.art_layers()
            if art.get('has_custom_image'):      # a real subject (override / series / avatar), not default.png
                subject, name, source_id, is_avatar = art['main'], cand.series.name, cand.id, art['is_avatar']
                break

        def frame(metal, holo=False):
            # Subject rides on OUR metal plate (the subject is metal-agnostic -- the plate carries the edition),
            # so the same badge shows in both editions' metals. No subject -> the plate alone (graceful).
            return {
                'tier': metal,
                'state': 'earned',
                'art_layers': [plate(metal)] + ([subject] if subject else []),
                'is_avatar': is_avatar,          # a user-badge avatar subject -> circle-masked + shrunk
                'is_holographic': holo,
                'series_name': name,
            }

        return {
            'earned': frame('platinum'),               # beat 3: the badge, claimed (solid)
            'mastered': frame('platinum', holo=True),  # beat 4: mastered -> holographic
            'ultra': frame('platinum'),                # editions legend: Ultra HD
            'legacy': frame('gold'),                   # editions legend: Legacy HD
            # The real badge behind the art -- tapping any forge medallion opens its quick-peek (like every
            # other medallion on the page). None on an empty catalog -> the illustrations are non-interactive.
            'source_id': source_id,
        }

    def get_context_data(self, **kwargs):
        if self._view_mode() == 'gallery':
            return self._gallery_context_data(**kwargs)
        return self._series_context_data(**kwargs)

    def _series_context_data(self, **kwargs):
        """Build the Series view context: paginate the per-SERIES queryset (self.object_list), then batch-build
        the page's tiles via build_series_items. Whale-safe -- one group-badge fetch for the page plus the two
        bulk maps (pursuer counts + the viewer's holds), independent of how many series render."""
        context = super().get_context_data(**kwargs)
        profile = self._profile()
        g = self.request.GET

        # Paginate the series rows. InfiniteScroller walks pages 2,3,... via XHR; get_page clamps an
        # out-of-range page to the last (which would loop forever), so an XHR fetch past the end emits NO rows
        # and the scroller stops.
        paginator = Paginator(self.object_list, SERIES_PAGE_SIZE)
        page_number = g.get('page')
        page_obj = paginator.get_page(page_number)
        try:
            requested_page = int(page_number or 1)
        except (TypeError, ValueError):
            requested_page = 1
        is_xhr = (
            self.request.headers.get('X-Requested-With') == 'XMLHttpRequest'
            or (getattr(self.request, 'htmx', False) and self.request.htmx.target == 'browse-results')
        )
        page_series = [] if (is_xhr and requested_page > paginator.num_pages) else list(page_obj)
        items = build_series_items(page_series, profile)

        context.update({
            'view': 'series',
            'display_data': items,
            'page_obj': page_obj,
            'paginator': paginator,
            'is_paginated': page_obj.has_other_pages(),
            'series_page_size': SERIES_PAGE_SIZE,
            'catalog_stats': self._catalog_header_stats(),
            'forge_meds': self._forge_medallions(),   # sample edition medallions for the header explainer
            'form': self.get_filter_form(),
            'series_authed': profile is not None,
            'series_states': [s for s in g.getlist('state') if s in _GALLERY_STATES],
            'selected_badge_types': g.getlist('badge_type'),
            'rarity_choices': RARITY_FILTER_CHOICES,           # shared rarity filter chips (both views)
            'selected_rarities': g.getlist('rarity'),
            'series_sort': g.get('sort') if g.get('sort') in SERIES_SORT_KEYS else SERIES_SORT_DEFAULT,
            'series_sorts': SERIES_SORTS,
            'series_q': g.get('series_slug', ''),
            'breadcrumb': [
                {'text': 'Home', 'url': reverse_lazy('home')},
                {'text': 'Badges'},
            ],
            'seo_description': (
                "Explore every badge series on Platinum Pursuit -- track your progress across game "
                "collections and platform generations."
            ),
        })
        # Only count a real page view, not each infinite-scroll ?page=N XHR fetch.
        if not is_xhr:
            track_page_view('badges_list', 'list', self.request)
        return context


class BadgeQuickPeekView(View):
    """PUBLIC quick-peek modal for one badge (the Series/Gallery 'pick it up'): the medallion big + facts
    ABOUT this badge tier, fetched on tap so the grids stay light. Deliberately GENERIC / viewer-independent
    -- a display piece, like the sample on a showroom floor, not the viewer's own copy. So it's always the
    showcase (full-colour) medallion + catalog stats (tier, requirement, XP on offer, rarity, earned-by, set
    number); no personal progress / earn stats / owner engraving. (Those live on the badge detail page, and
    on the collection's own CollectionBadgeModalView for a Pursuer's held badges.)"""

    def get(self, request, badge_id):
        badge = (
            Badge.objects.filter(id=badge_id, is_live=True)
            .select_related(
                'base_badge', 'franchise', 'collection', 'developer', 'funded_by', 'submitted_by',
                'base_badge__franchise', 'base_badge__collection',
                'base_badge__developer', 'base_badge__funded_by', 'base_badge__submitted_by',
            ).first()
        )
        if badge is None:
            return HttpResponseNotFound()   # explicit 404 (the project's handler404 renders at 200)
        frame = build_badge_frame(badge, None)   # profile=None -> the generic showcase ('earned') look
        frame['series_slug'] = badge.series_slug
        frame['tier_xp'] = _badge_xp(badge)      # XP on offer for earning this tier (a catalog fact)
        return render(request, 'components/badge_peek_modal.html', {'frame': frame})


class BadgeProgressPeekView(View):
    """Profile-aware badge peek for the badge detail page: the medallion in the DISPLAYED profile's REAL
    state (earned / in-progress / unearned) + personalised base, for whichever tier is inspected. Keyed to
    the profile in the URL (the page's target_profile), so it's correct whether you're viewing your OWN page
    or another Pursuer's. Auth-gated -- a specific Pursuer's progress is only shown to signed-in viewers,
    matching the badge detail page; anonymous visitors use the generic showcase BadgeQuickPeekView."""

    def get(self, request, psn_username, badge_id):
        if not request.user.is_authenticated:
            return HttpResponseNotFound()
        profile = get_object_or_404(Profile, psn_username__iexact=psn_username)
        badge = (
            Badge.objects.filter(id=badge_id, is_live=True)
            .select_related(
                'base_badge', 'franchise', 'collection', 'developer', 'funded_by', 'submitted_by',
                'base_badge__franchise', 'base_badge__collection',
                'base_badge__developer', 'base_badge__funded_by', 'base_badge__submitted_by',
            ).first()
        )
        if badge is None:
            return HttpResponseNotFound()
        frame = build_badge_frame(badge, profile)   # single hero: full stats + live rank/XP in the real state
        frame['series_slug'] = badge.series_slug
        frame['badge_id'] = badge.id
        frame['tier_xp'] = _badge_xp(badge)
        if frame.get('state') in ('earned', 'maintenance'):
            frame['owner_name'] = profile.display_psn_username or profile.psn_username   # engraved on the base
        # When the inspected profile isn't the viewer's own (the /badges/<slug>/<username>/ variant), tell the
        # modal whose progress this is so it can't be mistaken for your own.
        if profile != getattr(request.user, 'profile', None):
            frame['viewing_other_name'] = profile.display_psn_username or profile.psn_username
        return render(request, 'components/collection_badge_modal.html', {'frame': frame})


class GroupBadgeInspectView(View):
    """Medallion "pick it up" for the NEW grouping badges (Legacy HD / Ultra HD): the badge big + its facts,
    fetched on tap into the badge detail page's #badge-peek dialog. One view, two entry points mirroring the
    old tier peek:
      - group_badge_quick_peek (anon / no profile): the GENERIC full-colour showcase -- a display piece, no
        personal state or owner engraving.
      - group_badge_progress_peek (auth + a profile on display): that Pursuer's REAL state (earned / in
        progress / unearned) + live earners rank + owner engraving, correct on your own page AND another's.
    Reuses badge_detail_service so the frame + facts match the page exactly (a rare on-tap fetch, not a hot
    path)."""

    def get(self, request, group_badge_id, psn_username=None):
        gb = (
            GroupBadge.objects
            .select_related('series', 'series__franchise', 'series__collection', 'series__developer',
                            'series__funded_by', 'platform_group')
            .filter(id=group_badge_id, is_live=True).first()
        )
        if gb is None:
            return HttpResponseNotFound()   # explicit 404 (the project's handler404 renders at 200)

        profile, viewing_other = None, None
        if psn_username:
            if not request.user.is_authenticated:
                return HttpResponseNotFound()   # a specific Pursuer's progress is signed-in-only
            profile = get_object_or_404(Profile, psn_username__iexact=psn_username)
            if profile != getattr(request.user, 'profile', None):
                viewing_other = profile.display_psn_username or profile.psn_username

        detail = get_badge_detail(gb.series, profile)
        gv = next((g for g in detail.groups if g.group_badge.id == gb.id), None)
        if gv is None:
            return HttpResponseNotFound()

        showcase = profile is None
        if showcase:
            # Anon peek = full-colour display piece (the tier system's showcase), never the greyed unearned art.
            gv.frame['state'] = 'earned'
            gv.frame['owner_name'] = None

        return render(request, 'components/group_badge_modal.html', {
            'gv': gv, 'series': gb.series, 'detail': detail,
            'viewing_other': viewing_other, 'showcase': showcase,
        })


class BadgeDetailView(DetailView):
    """Badge series detail: a series' parallel platform-group badges (Legacy HD / Ultra HD), the viewer's
    per-group state + live progress, live earners rank, per-group rarity, and series XP. Reads the NEW
    grouping-badge models via badge_detail_service -- no tiers. See docs/design/rebuild/badge-backend-rebuild.md."""
    model = BadgeSeries
    template_name = 'trophies/badge_detail.html'
    slug_field = 'series_slug'
    slug_url_kwarg = 'series_slug'
    context_object_name = 'series'

    def dispatch(self, request, *args, **kwargs):
        # Profile-scoped variant (/badges/<slug>/<username>/) requires auth; anon -> canonical page with a
        # from_profile hint that drives the sign-up banner. (Mirrors GameDetailView.dispatch.)
        psn_username = kwargs.get('psn_username')
        if psn_username and not request.user.is_authenticated:
            canonical = reverse('badge_detail', kwargs={'series_slug': kwargs['series_slug']})
            params = {'from_profile': psn_username}
            existing_qs = request.META.get('QUERY_STRING', '')
            suffix = f'&{existing_qs}' if existing_qs else ''
            return HttpResponseRedirect(f'{canonical}?{urlencode(params)}{suffix}')
        return super().dispatch(request, *args, **kwargs)

    def get_object(self, queryset=None):
        series = get_object_or_404(
            BadgeSeries.objects.select_related(
                'franchise', 'collection', 'developer', 'submitted_by', 'funded_by', 'title',
            ),
            series_slug=self.kwargs[self.slug_url_kwarg],
        )
        # Staff preview gate: a series with no LIVE group badge is dormant (pre-cutover) -> staff-only.
        if not self.request.user.is_staff and not series.group_badges.filter(is_live=True).exists():
            raise Http404("Series not found")
        return series

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        series = context['series']

        psn_username = self.kwargs.get('psn_username')
        if psn_username:
            target_profile = get_object_or_404(Profile, psn_username__iexact=psn_username)
        elif self.request.user.is_authenticated and hasattr(self.request.user, 'profile'):
            target_profile = self.request.user.profile
        else:
            target_profile = None

        viewer_profile = (
            self.request.user.profile
            if (self.request.user.is_authenticated and hasattr(self.request.user, 'profile')) else None
        )
        context['target_profile'] = target_profile
        # When showing SOMEONE ELSE'S progress (the /<slug>/<username>/ variant), surface whose.
        context['viewing_other_profile'] = target_profile if (target_profile and target_profile != viewer_profile) else None

        detail = context['detail'] = get_badge_detail(series, target_profile)

        # Deep-link the platform-group tab: the list page's medallions/cells link to ?group=<key> so a click
        # lands on that edition directly. Validate against the series' live groups; default to the first.
        group_keys = [gv.platform_group.key for gv in detail.groups]
        requested = self.request.GET.get('group')
        context['active_group_key'] = requested if requested in group_keys else (group_keys[0] if group_keys else None)

        context['breadcrumb'] = [
            {'text': 'Home', 'url': reverse_lazy('home')},
            {'text': 'Badges', 'url': reverse_lazy('badges_list')},
            {'text': series.name},
        ]
        context['seo_description'] = (
            f"{series.name} badge series on Platinum Pursuit. Earn the badge on each platform, "
            f"track your progress, and climb the leaderboards."
        )
        track_page_view('badge', series.series_slug, self.request)
        return context


class BadgeLeaderboardsView(DetailView):
    """
    Display leaderboards for a specific badge series.

    Shows two leaderboards:
    1. Earners - Users who have earned the highest tier
    2. Progress - Users making progress on the badge series

    Leaderboard data is served from Redis sorted sets with near-real-time updates.
    """
    model = Badge
    template_name = 'trophies/badge_leaderboards.html'
    slug_field = 'series_slug'
    slug_url_kwarg = 'series_slug'
    context_object_name = 'badge'

    def get_object(self, queryset=None):
        series_slug = self.kwargs[self.slug_url_kwarg]
        # cover_url on most_recent_concept reads igdb_match; prefetch to avoid N+1.
        badge = get_object_or_404(
            Badge.objects.select_related(
                'most_recent_concept', 'most_recent_concept__igdb_match',
            ).defer('most_recent_concept__igdb_match__raw_response'),
            series_slug=series_slug, tier=1,
        )
        if not badge.is_live and not self.request.user.is_staff:
            raise Http404("Series not found")
        return badge

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        badge = self.object
        series_slug = badge.series_slug
        user = self.request.user
        paginate_by = 50

        earners_page_num = max(1, int(self.request.GET.get('lb_earners_page', 1) or 1))
        progress_page_num = max(1, int(self.request.GET.get('lb_progress_page', 1) or 1))

        # Earners leaderboard
        earners_total = get_earners_count(series_slug)
        earners_entries = get_earners_page(series_slug, earners_page_num, paginate_by)
        earners_paginator = RedisPaginator(earners_total, paginate_by)
        earners_page_num = min(earners_page_num, earners_paginator.num_pages)
        context['lb_earners_page_obj'] = RedisPage(earners_entries, earners_page_num, earners_paginator)
        context['lb_earners_paginator'] = earners_paginator

        # Progress leaderboard
        progress_total = get_progress_count(series_slug)
        progress_entries = get_progress_page(series_slug, progress_page_num, paginate_by)
        progress_paginator = RedisPaginator(progress_total, paginate_by)
        progress_page_num = min(progress_page_num, progress_paginator.num_pages)
        context['lb_progress_page_obj'] = RedisPage(progress_entries, progress_page_num, progress_paginator)
        context['lb_progress_paginator'] = progress_paginator

        if user.is_authenticated and hasattr(user, 'profile'):
            profile = user.profile
            earners_rank = get_earners_rank(series_slug, profile.id)
            if earners_rank:
                context['lb_earners_user_rank'] = earners_rank
                context['lb_earners_user_page'] = (earners_rank - 1) // paginate_by + 1
            progress_rank = get_progress_rank(series_slug, profile.id)
            if progress_rank:
                context['lb_progress_user_rank'] = progress_rank
                context['lb_progress_user_page'] = (progress_rank - 1) // paginate_by + 1

            # User stats for this series
            highest_user_badge = UserBadge.objects.filter(
                profile=profile, badge__series_slug=series_slug
            ).select_related('badge').order_by('-badge__tier').first()
            context['user_highest_tier'] = highest_user_badge.badge.tier if highest_user_badge else 0

            try:
                gamification = profile.gamification
                context['user_series_xp'] = gamification.series_badge_xp.get(series_slug, 0)
            except ProfileGamification.DoesNotExist:
                context['user_series_xp'] = 0

        context['badge'] = badge
        if badge.most_recent_concept:
            context['image_urls'] = {'recent_concept_icon_url': badge.most_recent_concept.cover_url}
        else:
            context['image_urls'] = {'recent_concept_icon_url': ''}
        context['breadcrumb'] = [
            {'text': 'Home', 'url': reverse_lazy('home')},
            {'text': 'Badges', 'url': reverse_lazy('badges_list')},
            {'text': 'Leaderboards', 'url': reverse_lazy('overall_badge_leaderboards')},
            {'text': context['badge'].effective_display_series, 'url': reverse_lazy('badge_detail', kwargs={'series_slug': badge.series_slug})},
            {'text': 'Series Leaderboards'},
        ]

        active_tab = self.request.GET.get('tab', 'earners')
        if active_tab not in ('earners', 'progress'):
            active_tab = 'earners'
        context['active_tab'] = active_tab

        track_page_view('badge_leaderboard', badge.series_slug, self.request)
        return context


class OverallBadgeLeaderboardsView(TemplateView):
    """
    Display overall badge leaderboards across all badge series.

    Shows two global leaderboards:
    1. Total XP - Users with the most badge experience points
    2. Total Progress - Users with the most badge completion percentage

    Data is served from Redis sorted sets with near-real-time updates.
    """
    template_name = 'trophies/overall_badge_leaderboards.html'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        user = self.request.user
        paginate_by = 50

        xp_page_num = max(1, int(self.request.GET.get('lb_total_xp_page', 1) or 1))
        progress_page_num = max(1, int(self.request.GET.get('lb_total_progress_page', 1) or 1))

        # XP leaderboard
        xp_total = get_xp_count()
        xp_entries = get_xp_page(xp_page_num, paginate_by)
        xp_paginator = RedisPaginator(xp_total, paginate_by)
        xp_page_num = min(xp_page_num, xp_paginator.num_pages)
        context['lb_total_xp_page_obj'] = RedisPage(xp_entries, xp_page_num, xp_paginator)
        context['lb_total_xp_paginator'] = xp_paginator

        # Progress leaderboard (global)
        progress_total = get_progress_count(slug=None)
        progress_entries = get_progress_page(slug=None, page=progress_page_num, page_size=paginate_by)
        progress_paginator = RedisPaginator(progress_total, paginate_by)
        progress_page_num = min(progress_page_num, progress_paginator.num_pages)
        context['lb_total_progress_page_obj'] = RedisPage(progress_entries, progress_page_num, progress_paginator)
        context['lb_total_progress_paginator'] = progress_paginator

        if user.is_authenticated and hasattr(user, 'profile'):
            profile = user.profile
            xp_rank = get_xp_rank(profile.id)
            if xp_rank:
                context['lb_total_xp_user_rank'] = xp_rank
                context['lb_total_xp_user_page'] = (xp_rank - 1) // paginate_by + 1

            progress_rank = get_progress_rank(slug=None, profile_id=profile.id)
            if progress_rank:
                context['lb_total_progress_user_rank'] = progress_rank
                context['lb_total_progress_user_page'] = (progress_rank - 1) // paginate_by + 1

            try:
                gamification = profile.gamification
                context['user_total_xp'] = gamification.total_badge_xp
                context['user_total_badges'] = gamification.total_badges_earned
            except ProfileGamification.DoesNotExist:
                context['user_total_xp'] = 0
                context['user_total_badges'] = 0

        context['breadcrumb'] = [
            {'text': 'Home', 'url': reverse_lazy('home')},
            {'text': 'Badges', 'url': reverse_lazy('badges_list')},
            {'text': 'Leaderboards'},
        ]

        active_tab = self.request.GET.get('tab', 'xp')
        if active_tab not in ('xp', 'progress', 'series', 'country'):
            active_tab = 'xp'
        context['active_tab'] = active_tab

        if active_tab == 'series':
            series_badges = Badge.objects.live().filter(
                tier=1
            ).select_related(
                'base_badge', 'most_recent_concept', 'most_recent_concept__igdb_match',
                'base_badge__most_recent_concept', 'base_badge__most_recent_concept__igdb_match',
                'base_badge__title', 'title',
            ).exclude(
                series_slug__isnull=True
            ).exclude(series_slug='').order_by(Lower('display_series'))

            directory = []
            for badge in series_badges:
                badge.progress_count = get_progress_count(badge.series_slug)
                directory.append(badge)
            context['series_directory'] = directory

        elif active_tab == 'country':
            context.update(self._get_country_tab_context(user, paginate_by))

        track_page_view('overall_leaderboard', 'global', self.request)
        return context

    def _get_country_tab_context(self, user, paginate_by):
        """Build context for the Country XP leaderboard tab."""
        ctx = {}

        # Determine selected country (default to user's country, fallback to first active)
        active_codes = get_active_country_codes()
        selected_cc = self.request.GET.get('country', '').upper()

        user_country_code = None
        if user.is_authenticated and hasattr(user, 'profile') and user.profile.country_code:
            user_country_code = user.profile.country_code

        if selected_cc not in active_codes:
            selected_cc = user_country_code if user_country_code in active_codes else ''

        # Build country list for picker (single DB query for display names/flags)
        if active_codes:
            country_list = list(
                Profile.objects.filter(
                    country_code__in=active_codes
                ).exclude(
                    country__isnull=True
                ).exclude(
                    country=''
                ).values_list('country', 'country_code', 'flag').distinct().order_by('country')
            )
            # Deduplicate by country_code (multiple profiles have the same data)
            seen = set()
            deduplicated = []
            for country_name, cc, flag in country_list:
                if cc not in seen:
                    seen.add(cc)
                    deduplicated.append({
                        'name': country_name or cc,
                        'code': cc,
                        'flag': flag or '',
                    })
            ctx['country_list'] = deduplicated
        else:
            ctx['country_list'] = []

        # Selected country info
        ctx['selected_country_code'] = selected_cc
        selected_info = next((c for c in ctx['country_list'] if c['code'] == selected_cc), None)
        ctx['selected_country_name'] = selected_info['name'] if selected_info else ''
        ctx['selected_country_flag'] = selected_info['flag'] if selected_info else ''
        ctx['user_country_code'] = user_country_code

        if not selected_cc:
            return ctx

        # Country XP leaderboard page
        country_page_num = max(1, int(self.request.GET.get('lb_country_xp_page', 1) or 1))
        country_total = get_country_xp_count(selected_cc)
        country_entries = get_country_xp_page(selected_cc, country_page_num, paginate_by)
        country_paginator = RedisPaginator(country_total, paginate_by)
        country_page_num = min(country_page_num, country_paginator.num_pages)
        ctx['lb_country_xp_page_obj'] = RedisPage(country_entries, country_page_num, country_paginator)
        ctx['lb_country_xp_paginator'] = country_paginator
        ctx['lb_country_xp_total'] = country_total

        # User's country rank
        if user.is_authenticated and hasattr(user, 'profile'):
            profile = user.profile
            if profile.country_code == selected_cc:
                country_rank = get_country_xp_rank(selected_cc, profile.id)
                if country_rank:
                    ctx['lb_country_xp_user_rank'] = country_rank
                    ctx['lb_country_xp_user_page'] = (country_rank - 1) // paginate_by + 1

        return ctx

