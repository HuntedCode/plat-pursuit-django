import logging
from collections import defaultdict

from trophies.constants import EVALUATABLE_BADGE_TYPES, PLATFORM_LABELS
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

from django.conf import settings
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
    UserTitle, ProfileGamification, BadgeSeries, GroupBadge, UserGroupBadge, PlatformGroup,
    ProfileBadgeStanding, ProfileCareerStanding, SeriesBadgeStanding, Game,
)
from ..forms import BadgeSearchForm
from trophies.services.badge_detail_service import get_badge_detail
from trophies.services.badge_list_service import build_list_cards, build_series_items
from trophies.services.badge_rarity import (
    annotate_group_rarity, RARITY_CLASSES, RARITY_FILTER_CHOICES, RARITY_UNEARNED,
)
from trophies.mixins import HtmxListMixin
from trophies.services.frame_service import build_badge_frame
# Leaderboards read from Lane B (indexed DB reads over the standing stores). The Redis sorted-set
# service is no longer imported here -- see docs/design/rebuild/leaderboards-rebuild.md step 2.
from trophies.services import badge_leaderboards as lb
from trophies.services.redis_leaderboard_service import get_community_xp

logger = logging.getLogger("psn_api")


def badge_catalog_stats():
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


def badge_forge_medallions():
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


def badge_subject_art(limit=4):
    """Raw SUBJECT artwork for the how-it-works page's handmade claim: the drawings themselves, not the
    composed medallion.

    One per SERIES, deliberately. The plate, shape and backing are shared by every badge in an edition, so
    four medallions from one series would look like four copies of the same object and undersell exactly
    the thing being claimed. The subject is the part an artist drew.

    Avatar subjects are skipped: a submitter's profile picture is a real custom image as far as
    `art_layers()` is concerned, but it is not a commissioned piece and putting one here would make the
    claim false. Bounded scan, most-earned first, so a big catalog does not turn this into a table sweep.
    """
    seen, out = set(), []
    for cand in (GroupBadge.objects.filter(is_live=True)
                 .select_related('series', 'series__submitted_by', 'platform_group')
                 .order_by('-earned_count', 'id')[:60]):
        if cand.series_id in seen:
            continue
        art = cand.art_layers()
        if not art.get('has_custom_image') or art.get('is_avatar'):
            continue
        seen.add(cand.series_id)
        out.append({'src': art['main'], 'series': cand.series.name, 'badge_id': cand.id})
        if len(out) >= limit:
            break
    return out


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
            'catalog_stats': badge_catalog_stats(),  # generalized collection stats (hourly-cached)
            'forge_meds': badge_forge_medallions(),          # sample edition medallions for the header explainer
            'breadcrumb': [
                {'text': 'Home', 'url': reverse_lazy('home')},
                {'text': 'Badges'},
            ],
            'seo_description': (
                "Browse every badge on Platinum Pursuit -- filter by platform, rarity, and type to find "
                "your next platinum to chase."
            ),
        })
        return context


    def get_context_data(self, **kwargs):
        context = (self._gallery_context_data(**kwargs) if self._view_mode() == 'gallery'
                   else self._series_context_data(**kwargs))
        # Dev-only replay control for the first-run modal. It is one-shot by design, gated on a
        # localStorage flag, so once dismissed there is no way back to it in a browser short of clearing
        # site data by hand -- which makes iterating on the thing needlessly painful. Same shape as the
        # Collection's `dev_mint` ceremony replay, and it never renders in prod.
        context['dev_howto'] = settings.DEBUG
        return context

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
            'catalog_stats': badge_catalog_stats(),
            'forge_meds': badge_forge_medallions(),   # sample edition medallions for the header explainer
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
        return context


class BoardDirectoryView(HtmxListMixin, ListView):
    """Shared base for the board directories (Badge Boards, Game Boards).

    A directory is a catalogue of BOARDS: entity identity, a top slice, a link to the full board. It owns
    no data and is not a second home for one -- the full board lives on the entity.

    THIN, deliberately (see docs/design/rebuild/leaderboards-rebuild.md). Search plus exactly TWO sorts:

      name     -> alphabetical, the default. A catalogue that reorders between visits is disorienting, and
                  anyone hunting a specific entity uses search.
      entrants -> the only sort that answers "which boards are actually alive".

    No filter panel, no facets, no country. These directories catalogue the same entities `/games/` and
    `/badges/` already catalogue; without a stated differentiator they converge into second copies of
    those browse pages, and then there are two walls, two filter sets and a drift risk. If one ever grows
    a filter drawer it has become a second Browse Games and belongs folded back in as a view mode.

    The MINIMUM-PARTICIPANTS gate is what keeps the wall worth scrolling: a directory full of
    one-entrant boards reads as broken and drowns the boards worth looking at. It also pays for the
    "entrants" sort, since the counts are needed either way.
    """
    paginate_by = 24
    context_object_name = 'cards'
    SORTS = (('name', 'Name (A-Z)'), ('entrants', 'Most entrants'))
    SORT_KEYS = {k for k, _ in SORTS}
    DEFAULT_SORT = 'name'
    MIN_ENTRANTS = 1          # a board needs at least one name on it to be worth listing
    PREVIEW = 5               # top slice per card; the template shows 3 of them on a phone

    def _q(self):
        return (self.request.GET.get('q') or '').strip()

    def _sort(self):
        raw = self.request.GET.get('sort') or self.DEFAULT_SORT
        return raw if raw in self.SORT_KEYS else self.DEFAULT_SORT

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context.update({
            'q': self._q(),
            'sort': self._sort(),
            'sorts': self.SORTS,
            'directory_kind': self.kind,
        })
        return context


class BadgeBoardsView(BoardDirectoryView):
    """`/leaderboards/badges/` -- every badge series with a live board.

    Replaces the old `?tab=series` directory, which was built on `Badge.objects.filter(tier=1)` -- the
    tier concept the badge rebuild retired. This one reads BadgeSeries + the standing store.
    """
    kind = 'badges'
    template_name = 'trophies/board_directory.html'
    partial_template_name = 'trophies/partials/board_directory_results.html'

    def get_queryset(self):
        # Only series with a LIVE group badge: a dormant series has no board to preview.
        qs = BadgeSeries.objects.filter(
            Exists(GroupBadge.objects.filter(series=OuterRef('pk'), is_live=True))
        )
        q = self._q()
        if q:
            qs = qs.filter(name__icontains=q)

        counts = lb.series_board_counts(
            list(qs.values_list('series_slug', flat=True))
        )
        live = {slug for slug, n in counts.items() if n >= self.MIN_ENTRANTS}
        qs = qs.filter(series_slug__in=live)

        if self._sort() == 'entrants':
            # Ordered in PYTHON off the counts map rather than by re-querying: the map is already built
            # for the gate, and the alternative is a correlated subquery per row.
            ordered = sorted(qs, key=lambda s: (-counts.get(s.series_slug, 0), s.name.lower()))
            return ordered
        return qs.order_by(Lower('name'))

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        series = list(context['cards'])
        slugs = [s.series_slug for s in series]

        previews = lb.series_board_previews(slugs, n=self.PREVIEW)
        counts = lb.series_board_counts(slugs)
        hydrated = lb.hydrate([r[0] for rows in previews.values() for r in rows])

        context['boards'] = [{
            'key': s.series_slug,
            'name': s.name,
            'url': reverse('badge_detail', args=[s.series_slug]) + '#ranks',
            'entrants': counts.get(s.series_slug, 0),
            'rows': [
                dict(lb._entry(hydrated, r[0], i + 1), primary=r[2], primary_label='stages')
                for i, r in enumerate(previews.get(s.series_slug, []))
            ],
        } for s in series]
        return context


class GameBoardsView(BoardDirectoryView):
    """`/leaderboards/games/` -- every game with a board worth looking at.

    The full board is game detail's Ranks panel, which is already rebuilt; this is discovery over it.
    """
    kind = 'games'
    template_name = 'trophies/board_directory.html'
    partial_template_name = 'trophies/partials/board_directory_results.html'
    MIN_ENTRANTS = 3          # games are numerous; a 1-2 name board is noise in a catalogue this size

    def get_queryset(self):
        # `played_count` is a denormalized column on Game, so the gate and the "entrants" sort are both
        # free -- no aggregate over ProfileGame, which is one of the largest tables in the schema.
        qs = Game.objects.filter(played_count__gte=self.MIN_ENTRANTS).select_related(
            'concept', 'concept__igdb_match',
        ).defer('concept__igdb_match__raw_response')

        q = self._q()
        if q:
            qs = qs.filter(title_name__icontains=q)

        if self._sort() == 'entrants':
            return qs.order_by('-played_count', Lower('title_name'))
        return qs.order_by(Lower('title_name'))

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        games = list(context['cards'])
        ids = [g.id for g in games]

        previews = lb.game_board_previews(ids, n=self.PREVIEW)
        hydrated = lb.hydrate([r[0] for rows in previews.values() for r in rows])

        context['boards'] = [{
            'key': g.id,
            'name': g.title_name,
            'art': g.display_image_url,
            'url': reverse('game_detail', args=[g.np_communication_id]),
            'entrants': g.played_count,
            'rows': [
                dict(lb._entry(hydrated, r[0], i + 1), primary=r[1], primary_label='%')
                for i, r in enumerate(previews.get(g.id, []))
            ],
        } for g in games]
        return context


class JobBoardsView(BoardDirectoryView):
    """`/leaderboards/jobs/` -- the 24 job boards.

    The smallest directory by far, which is exactly why it must follow the same rules: 24 entities is
    where a filter panel looks harmless and a third sort looks free. Same base, same two sorts, same gate.
    """
    kind = 'jobs'
    template_name = 'trophies/board_directory.html'
    partial_template_name = 'trophies/partials/board_directory_results.html'

    def get_queryset(self):
        from trophies.models import Job

        qs = Job.objects.all()
        q = self._q()
        if q:
            qs = qs.filter(name__icontains=q)

        counts = lb.job_board_counts(list(qs.values_list('slug', flat=True)))
        live = {slug for slug, n in counts.items() if n >= self.MIN_ENTRANTS}
        qs = qs.filter(slug__in=live)

        if self._sort() == 'entrants':
            return sorted(qs, key=lambda j: (-counts.get(j.slug, 0), j.name.lower()))
        return qs.order_by(Lower('name'))

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        jobs = list(context['cards'])
        slugs = [j.slug for j in jobs]

        previews = lb.job_board_previews(slugs, n=self.PREVIEW)
        counts = lb.job_board_counts(slugs)
        hydrated = lb.hydrate([r[0] for rows in previews.values() for r in rows])

        context['boards'] = [{
            'key': j.slug,
            'name': j.name,
            'url': reverse('job_detail', args=[j.slug]) + '?tab=ranks',
            'entrants': counts.get(j.slug, 0),
            'rows': [
                dict(lb._entry(hydrated, r[0], i + 1), primary=r[1], primary_label='XP')
                for i, r in enumerate(previews.get(j.slug, []))
            ],
        } for j in jobs]
        return context


class BadgeRanksPanelView(View):
    """`/badges/<series_slug>/ranks/` -- the series board, fetched into badge detail's Ranks section.

    Lazily fetched rather than server-rendered, copying game detail's Ranks panel: the cost scales with a
    series' popularity, and most visitors come for the badge itself and never scroll to the board. The
    page pays for it only when someone looks.

    Public: the board is identical for every viewer, which is what keeps it cacheable. A signed-in
    viewer's own position is passed alongside the rows rather than marked inside one, for the same reason
    the Global Boards landing puts it in the header.

    This REPLACES `/leaderboards/badges/<slug>/`, which was a whole page for what is a section of the page
    about the badge. Boards live on the thing they rank.
    """
    PREVIEW = 25

    def get(self, request, series_slug):
        series = BadgeSeries.objects.filter(series_slug=series_slug).first()
        if series is None:
            raise Http404("Series not found")

        total = lb.series_board_count(series_slug)
        rows = lb.series_board_rows(series_slug, limit=self.PREVIEW)
        entries = lb.page(rows, 0, extra=lambda r: {
            'primary': r[2], 'primary_label': 'stages',
            'secondary': None, 'secondary_label': '',
        })

        my_rank = None
        profile = getattr(request.user, 'profile', None) if request.user.is_authenticated else None
        if profile:
            my_rank = lb.series_board_rank(series_slug, profile.id)

        return render(request, 'trophies/partials/badge_detail/bd2_ranks.html', {
            'rows': entries,
            'total': total,
            'my_rank': my_rank,
            'has_more': total > len(entries),
            'series': series,
        })

class OverallBadgeLeaderboardsView(TemplateView):
    """`/leaderboards/` -- Global Boards, the hub landing.

    THREE boards, one per thing worth ranking, as `.pp-switch` tabs:

      Progress     -> trophies across badge games (platinums first, total as the tiebreak)
      Badge Points -> ProfileBadgeStanding.total_xp
      Career XP    -> the jobs economy (ProfileCareerStanding)

    Progress and Badge Points are DELIBERATELY separate tabs rather than one "XP" board: they are two
    sealed economies (the badge subsystem never reads or writes the jobs one), a hunter can hold very
    different ranks in each, and a merged total would be the one figure on the site that means nothing.

    Country is a FILTER across all three, never a board of its own -- one WHERE served by the
    (country_code, ...board order) composites, versus the old design's separate Redis sorted set per
    country. That single decision is what keeps this section's surface area finite.

    Every board is public and identical for every viewer, which is what makes the page cacheable; a
    personal rank on a row would forfeit that. The viewer's OWN standing is shown once, in the header.
    """
    template_name = 'trophies/overall_badge_leaderboards.html'
    paginate_by = 50

    # (key, label). Order is the tab order; Progress leads because it is the board with the most entrants.
    BOARDS = (
        ('progress', 'Progress'),
        ('points', 'Badge Points'),
        ('career', 'Career XP'),
    )
    BOARD_KEYS = {k for k, _ in BOARDS}
    # `xp` was the old key for the Badge Points board; `country` was a TAB before country became a filter.
    # Bookmarks carrying either still land somewhere sensible instead of silently defaulting.
    LEGACY_TABS = {'xp': 'points', 'country': 'points'}

    def _active_tab(self):
        raw = self.request.GET.get('tab', 'progress')
        raw = self.LEGACY_TABS.get(raw, raw)
        return raw if (raw in self.BOARD_KEYS or raw == 'series') else 'progress'

    def _country(self):
        """The country slice, validated against countries that actually have ranked hunters.

        Validated rather than trusted: an unknown code would silently return an empty board, which reads
        as "nobody from there plays" rather than "that is not a country we rank".
        """
        cc = (self.request.GET.get('country') or '').upper()
        return cc if cc and cc in set(lb.active_countries()) else ''

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        user = self.request.user
        tab = self._active_tab()
        country = self._country()
        profile = getattr(user, 'profile', None) if user.is_authenticated else None

        countries = lb.country_options()
        context.update({
            'boards': self.BOARDS,
            'active_tab': tab,
            'selected_country': country,
            'countries': countries,
            'ranked_total': ProfileBadgeStanding.objects.count(),
            'country_total': len(countries),
            'breadcrumb': [
                {'text': 'Home', 'url': reverse_lazy('home')},
                {'text': 'Leaderboards'},
            ],
        })

        # Only the ACTIVE board is built; the others cost nothing until asked for.
        if tab in self.BOARD_KEYS:
            context['board'] = self._build_board(tab, country)
        elif tab == 'series':
            context['series_directory'] = self._series_directory()

        # The viewer's own standing, ONCE, in the header -- not per row (see the class docstring).
        if profile:
            cc = country or None
            context['my_standing'] = {
                'progress': lb.progress_rank(profile.id, country=cc),
                'points': lb.xp_rank(profile.id, country=cc),
                'career': lb.career_xp_rank(profile.id, country=cc),
            }
        return context

    def _build_board(self, tab, country):
        """One page of the active board: two queries (the board read + one hydrate) plus its count."""
        per = self.paginate_by
        page_num = max(1, int(self.request.GET.get('page', 1) or 1))
        cc = country or None

        # `> 0` on the sort key, so the board holds hunters who have actually done the thing it ranks.
        # A wall of zeroes is not a leaderboard, and it would also make the count disagree with the rows.
        if tab == 'career':
            qs = ProfileCareerStanding.objects.filter(total_xp__gt=0)
        elif tab == 'points':
            qs = ProfileBadgeStanding.objects.filter(total_xp__gt=0)
        else:
            qs = ProfileBadgeStanding.objects.filter(trophies_total__gt=0)
        if country:
            qs = qs.filter(country_code=country)

        paginator = lb.BoardPaginator(qs.count(), per)
        page_num = min(page_num, paginator.num_pages)
        offset = (page_num - 1) * per

        if tab == 'progress':
            rows = lb.progress_rows(limit=per, offset=offset, country=cc)
            entries = lb.page(rows, offset, extra=lambda r: {
                'primary': r[1], 'primary_label': 'platinums',
                'secondary': r[5], 'secondary_label': 'trophies',
            })
        elif tab == 'points':
            rows = lb.xp_rows(limit=per, offset=offset, country=cc)
            entries = lb.page(rows, offset, extra=lambda r: {
                'primary': r[1], 'primary_label': 'points',
                'secondary': None, 'secondary_label': 'badges',
            })
        else:
            rows = lb.career_xp_rows(limit=per, offset=offset, country=cc)
            entries = lb.page(rows, offset, extra=lambda r: {
                'primary': r[1], 'primary_label': 'XP',
                'secondary': r[2], 'secondary_label': 'level',
            })
        return lb.BoardPage(entries, page_num, paginator)

    def _series_directory(self):
        """The per-series board directory, still on the LEGACY Badge model.

        Temporary: step 6 of the leaderboards rebuild promotes this to a proper Badge Boards directory at
        `/leaderboards/badges/`, built on BadgeSeries/GroupBadge. It stays reachable at `?tab=series` in
        the meantime so the section has no gap, and is deliberately NOT in the tab strip -- that strip is
        for boards, and a directory sitting in it was the incoherence this rebuild is removing.
        """
        badges = Badge.objects.live().filter(tier=1).select_related(
            'base_badge', 'most_recent_concept', 'most_recent_concept__igdb_match',
            'base_badge__most_recent_concept', 'base_badge__most_recent_concept__igdb_match',
            'base_badge__title', 'title',
        ).defer(
            'most_recent_concept__igdb_match__raw_response',
            'base_badge__most_recent_concept__igdb_match__raw_response',
        ).exclude(series_slug__isnull=True).exclude(series_slug='').order_by(Lower('display_series'))

        directory = list(badges)
        counts = dict(
            SeriesBadgeStanding.objects
            .filter(series_slug__in=[b.series_slug for b in directory])
            .values('series_slug').annotate(n=Count('id'))
            .values_list('series_slug', 'n')
        )
        for badge in directory:
            badge.progress_count = counts.get(badge.series_slug, 0)
        return directory

class BadgeHowItWorksView(TemplateView):
    """`/badges/how-it-works/` -- the permanent, addressable home for the badge teaching.

    It existed only as a first-run modal on Browse Badges, which meant the explanation for a vocabulary
    the whole badge system speaks ("Ultra HD", "Legacy HD") had no URL: support could not link it, search
    could not index it, and no other surface could reach it. Three surfaces render those names straight
    off `PlatformGroup.name` without being able to say what they mean -- badge detail as the group-switch
    TABS, the Browse Badges gallery as PLATFORM FILTER CHIPS, and the Collection as edition stat labels
    (plus a per-card caption). Each of the three now carries a `.pp-edhint` link pointing here. The modal
    keeps its onboarding job: it greets a first visit once and links here.

    The edition table is read from `PlatformGroup`, never retyped. That model already owns the mapping
    (`name` + `platforms`), the badge engine routes games by it, and the class docstring calls adding a
    group "a row, not a schema change" -- so a page that hardcoded two editions would be the one place
    that stopped being true the day a third is seeded.
    """
    template_name = 'trophies/badge_how_it_works.html'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)

        # Ordered by the model's own Meta (sort_order, name), which is the same order the badge detail
        # tabs use -- so the page teaches the editions in the order the reader meets them.
        groups = PlatformGroup.objects.filter(is_active=True).only(
            'key', 'name', 'platforms', 'exclude_delisted', 'sort_order',
        )
        context['editions'] = [
            {
                'key': g.key,
                'name': g.name,
                # Raw PSN codes are what the column stores; PSVITA is the only one that reads wrong.
                'platforms': [PLATFORM_LABELS.get(p, p) for p in (g.platforms or [])],
                'delisted_gates': g.exclude_delisted,
            }
            for g in groups
        ]

        # The ARTWORK, not an illustration of it. `badge_forge_medallions()` composes a real live badge's
        # subject onto each edition's metal plate, which is the whole point on a page teaching a system
        # whose moat IS the custom art: "if the chrome ever fights the art, the chrome loses"
        # (visual-identity.md). It falls back to the bare plate on an empty catalog, so a fresh install
        # still renders. Shared verbatim with the browse page's first-run modal -- one source, so the
        # page and the sheet cannot show different badges.
        context['forge_meds'] = badge_forge_medallions()

        # Real numbers, because the brief makes them first-class material: "watching them climb is half
        # the joy of the hobby and we should never bury it." Read from the hourly-cached site heartbeat,
        # so this costs nothing on the request path and simply omits itself on a cold cache.
        context['catalog_stats'] = badge_catalog_stats()

        # Raw subject art for the handmade claim -- the pieces themselves, off the medallion. A sentence
        # asserting the art is hand-drawn is worth less than four pieces of it sitting there, and the
        # medallion form would work against the point: the plate and shape are the same on every badge,
        # so it is the SUBJECT that shows a person drew this one.
        context['craft_art'] = badge_subject_art(limit=4)

        context['breadcrumb'] = [
            {'text': 'Home', 'url': reverse_lazy('home')},
            {'text': 'Badges', 'url': reverse_lazy('badges_list')},
            {'text': 'How badges work'},
        ]
        context['seo_description'] = (
            'How Platinum Pursuit badges work: what a badge series is, how you earn one by platinuming '
            'every game in the set, and how platform editions are earned independently.'
        )
        return context
