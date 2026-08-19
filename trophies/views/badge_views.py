import logging
from collections import defaultdict

from trophies.constants import EVALUATABLE_BADGE_TYPES, PLATFORM_LABELS

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
from django.http import Http404, HttpResponseRedirect, HttpResponseNotFound, JsonResponse
from urllib.parse import urlencode
from django.shortcuts import get_object_or_404, render
from django.urls import reverse_lazy, reverse
from django.utils.text import slugify
from django.views import View
from django.views.generic import ListView, DetailView, TemplateView

from ..models import (
    Profile, Badge, UserBadge,
    UserTitle, BadgeSeries, GroupBadge, UserGroupBadge, PlatformGroup,
    ProfileCareerStanding, SeriesBadgeStanding,
)
from ..forms import BadgeSearchForm
from trophies.services.badge_detail_service import get_badge_detail
from trophies.services.badge_list_service import build_list_cards, build_series_items
from trophies.services.badge_rarity import (
    annotate_group_rarity, RARITY_CLASSES, RARITY_FILTER_CHOICES, RARITY_UNEARNED,
)
# Leaderboards read from Lane B (indexed DB reads over the standing stores). The Redis sorted-set
# service is no longer imported here -- see docs/design/rebuild/leaderboards-rebuild.md step 2.
from trophies.services import badge_leaderboards as lb
from trophies.views import board_helpers
from trophies.views.board_helpers import suggest_json, window_params

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



class BadgeRanksPanelView(View):
    """`/badges/<series_slug>/ranks/` -- the series board, fetched into badge detail's Ranks tab.

    Lazily fetched rather than server-rendered, copying game detail's Ranks panel: the cost scales with a
    series' popularity, and most visitors come for the badge itself. The page pays for it only when
    someone opens the tab.

    The ROWS are identical for every viewer -- "this one is you" is applied in the browser from
    `data-lb-viewer-rank`, never rendered in, because a row that knows who is reading it cannot be shared
    between readers.

    The FRAGMENT is per-viewer, though: it carries the standing line and the jump-to-me chip, both of
    which need `my_rank`. So it must not be given a shared cache key, and it costs one rank count per tab
    open. Job detail's panel makes the same trade for the same reason.

    This REPLACES `/leaderboards/badges/<slug>/`, which was a whole page for what is a section of the page
    about the badge. Boards live on the thing they rank.

    TWO RESPONSES, one endpoint:

      no `?range=`   the full panel -- meta line, jump bar, the board shell, the first window
      `?range=N`     bare `.lb-row`s for display positions [N, N+count), for the virtualizer

    The `?offset=` "show more" this used to serve is gone with the button. Appending 25 rows at a time
    could not reach row 3,000 of a popular series in any reasonable number of clicks, which is the same
    dead end the prev/next pager had -- see `leaderboard_board.html`.
    """
    #: Fetch granularity, shared with every other board -- see `board_helpers.PAGE_SIZE`.
    PAGE_SIZE = board_helpers.PAGE_SIZE

    def get(self, request, series_slug):
        # The SAME dormant gate BadgeDetailView.get_object applies, and it runs BEFORE the window branch
        # below -- without it this fragment answered for an unreleased series that its own page 404s, so
        # `/badges/<unreleased>/ranks/` confirmed the series exists and handed over its board to anyone
        # who guessed the slug.
        #
        # ONE query for the public path. It was a fetch plus an `.exists()`, which is two round trips
        # before every window a reader scrolls past; the row itself is only needed for the full panel.
        if request.user.is_staff:
            series = BadgeSeries.objects.filter(series_slug=series_slug).first()
            if series is None:
                raise Http404("Series not found")
        else:
            series = BadgeSeries.objects.filter(
                series_slug=series_slug, group_badges__is_live=True).first()
            if series is None:
                raise Http404("Series not found")

        # THE SLICE, resolved once and applied to the rows, the count and the viewer's rank alike. A
        # window that ignored a filter the first window applied would return different hunters halfway
        # down the same board, and the rows keep numbering up -- so it reads as the board, not as a bug.
        #
        # EDITION FIRST, because it decides which STORE is being read, and country then scopes itself to
        # whichever board that is. Resolved in the other order, a country valid for the series board
        # could be applied to an edition board that has nobody from it.
        editions = self._editions(series)
        edition = self._edition(request, editions)

        codes = (lb.series_edition_countries(series_slug, edition) if edition
                 else lb.series_board_countries(series_slug))
        country = self._country(request, codes)

        if request.GET.get('suggest') is not None:
            qs = (lb._series_edition_qs(series_slug, edition, country or None) if edition
                  else lb._series_board_qs(series_slug, country or None))
            keys = lb.SERIES_EDITION_KEYS if edition else lb.SERIES_BOARD_KEYS
            return JsonResponse(suggest_json(
                lb.board_suggest(qs, keys, request.GET.get('suggest', ''))))

        if 'range' in request.GET:
            start, count = window_params(request, self.PAGE_SIZE)
            return render(request, 'trophies/partials/leaderboard_rows.html', {
                'entries': self._window(series_slug, start - 1, count, country, edition),
            })

        profile = getattr(request.user, 'profile', None) if request.user.is_authenticated else None
        if edition:
            total = lb.series_edition_count(series_slug, edition, country=country or None)
            my_rank = (lb.series_edition_rank(series_slug, edition, profile.id, country=country or None)
                       if profile else None)
            meaning = (f"Everyone chasing the {editions[edition]['name']} edition, "
                       f"by the points they have earned on it.")
        else:
            total = lb.series_board_count(series_slug, country=country or None)
            my_rank = (lb.series_board_rank(series_slug, profile.id, country=country or None)
                       if profile else None)
            # ALL editions, and it means it now. This used to rank on the furthest-along EDITION, so
            # "All editions" showed a board that ignored every edition but your best one -- which is what
            # made it read as broken rather than merely odd. Points are already summed across editions
            # before they reach the standing, so the board answers the question its label asks.
            meaning = ('Everyone chasing this badge, by the points they have earned '
                       'across all its editions.')

        return render(request, 'trophies/partials/badge_detail/bd2_ranks.html', {
            'rows': self._window(series_slug, 0, self.PAGE_SIZE, country, edition),
            'total': total,
            'page_size': self.PAGE_SIZE,
            # The endpoint the virtualizer fetches later windows from -- this same view, reversed rather
            # than read off `request.path`, so the panel does not silently depend on having been reached
            # by its canonical URL.
            'rows_url': reverse('badge_ranks_panel', args=[series_slug]),
            # Carried on every later window, so the rest of a filtered board stays filtered -- and for
            # `edition` that is not a filter but the identity of the store being read, so dropping it
            # would fetch the SERIES board's rows into an edition board's spacer.
            'rows_params': urlencode(
                {k: v for k, v in (('edition', edition), ('country', country)) if v}),
            'countries': lb.country_options(codes),
            'selected_country': country,
            'editions': [{'key': k, 'name': v['name']} for k, v in editions.items()],
            'selected_edition': edition,
            'my_rank': my_rank,
            'series': series,
            # The shared board card. `board_label` is the series, because on this page the board IS the
            # series -- and the meaning line moved here off the section subtitle, where it sat above a
            # panel that had not loaded yet.
            'board_label': series.name,
            'board_meaning': meaning,
            # "N hunters HERE" under a slice -- the figure is a claim about a population, and under a
            # filter it is a claim about a smaller one. Edition counts: it names a different, smaller
            # board rather than narrowing this one, which is the same thing from the reader's side.
            'slice_applied': bool(country or edition),
            'standing': self._standing(profile, my_rank),
        })

    @staticmethod
    def _standing(profile, my_rank):
        """What the board card tells a signed-in viewer about themselves.

        `is_linked`, not merely "has a profile": every board population is gated on it
        (`badge_leaderboards._linked`), so an unverified account told "not on this board yet" is being
        promised a board it cannot enter. Ranked viewers get nothing here -- the jump chip beneath
        already says "You're #N", and saying it twice on one card is the kind of duplication the shared
        partial exists to stop."""
        if not (profile and profile.is_linked) or my_rank:
            return ''
        return 'Not on this board yet'

    @staticmethod
    def _editions(series):
        """The editions THIS series is offered in, as {key: {name}}, in the series' own display order.

        Scoped to the series' LIVE group badges, not to `active_editions()` -- that is every edition on
        the site, and offering one this badge was never released in is a board that could only be empty.
        Same scoping rule the country picker follows.

        A single-edition series gets an EMPTY map rather than one option: a picker with one choice plus
        "all editions" is two ways to see the same hunters, which is a control that cannot do anything.
        """
        groups = list(
            series.group_badges.filter(is_live=True)
            .select_related('platform_group')
            .order_by('platform_group__sort_order', 'platform_group__name')
        )
        if len(groups) < 2:
            return {}
        return {g.platform_group.key: {'name': g.platform_group.name} for g in groups}

    @staticmethod
    def _edition(request, editions):
        """Validated against the editions this series HAS. An unknown key would otherwise resolve to no
        group badge and silently fall back to the series board, which is a filter that appears to be
        applied and is not."""
        raw = (request.GET.get('edition') or '').strip()
        return raw if raw in editions else ''

    @staticmethod
    def _country(request, codes):
        """Validated against the countries that actually have hunters on THIS board.

        An unknown code would return an empty window, which reads as a gap in the board rather than as a
        bad parameter -- and this is a public fragment, so it takes whatever a URL hands it. Mirrors
        `LeaderboardRowsView._country`, which makes the same call for the same reason."""
        raw = (request.GET.get('country') or '').strip().upper()
        return raw if raw in set(codes) else ''

    @classmethod
    def _window(cls, series_slug, offset, limit, country='', edition=''):
        """One window of the board, hydrated. Shared by both responses above, which is the point: a rows
        endpoint that built its own `extra` mapping would be a second definition of what this board's
        columns MEAN, and the first thing to drift would be the labels -- so the rest of a board would
        read a different figure from the screenful the reader arrived on."""
        if edition:
            return cls._edition_window(series_slug, edition, offset, limit, country)
        rows = lb.series_board_rows(series_slug, limit=limit, offset=offset, country=country or None)
        # `offset`, not 0: `page()` numbers rows by SLOT, so a window starting at 50 must number from 51.
        # r = (profile_id, xp, advanced_at).
        return lb.page(rows, offset, extra=lambda r: {
            # POINTS, not a stage tally. Points already count what was cleared and weigh what it was
            # worth, so a stages column beside them says the same thing less precisely -- and the stage
            # figure was the FURTHEST-ALONG EDITION's, which made it wrong on a board that sums editions.
            # `points` is the word the Global Boards landing uses for the same quantity.
            'primary': r[1], 'primary_label': 'points',
            'secondary': None, 'secondary_label': '',
            # `advanced_at` is the hunter's most recent advance -- their completion date if they finished,
            # the latest gating stage they cleared if they are still chasing. One column, and the label
            # stays neutral rather than claiming which, because the row does not know.
            'when': r[2], 'when_label': 'since',
        })

    @staticmethod
    def _edition_window(series_slug, edition, offset, limit, country=''):
        """One window of the EDITION board -- the same columns as the series board, scoped.

        That sameness is the point. The reader picked a filter, not a different page: the rank, the
        hunter, the points and the date all mean what they meant a moment ago, with the points now
        counting THIS edition rather than every edition summed. r = (profile_id, ed_xp, advanced_at)."""
        rows = lb.series_edition_rows(series_slug, edition, limit=limit, offset=offset,
                                      country=country or None)
        return lb.page(rows, offset, extra=lambda r: {
            'primary': r[1], 'primary_label': 'points',
            'secondary': None, 'secondary_label': '',
            'when': r[2], 'when_label': 'since',
        })


class OverallBadgeLeaderboardsView(TemplateView):
    """`/leaderboards/` -- Global Boards, the hub landing.

    THREE boards, one per thing worth ranking, as `.pp-switch` tabs:

      Trophies     -> every game, platinums first, total as the tiebreak (Profile's own counters)
      Badge Points -> ProfileBadgeStanding.total_xp
      Career XP    -> the jobs economy (ProfileCareerStanding)

    One board per DOMAIN: overall trophy hunting, badges, career. Badge Points and Career XP are
    deliberately separate rather than one "XP" board -- they are two sealed economies (the badge subsystem
    never reads or writes the jobs one), a hunter can hold very different ranks in each, and a merged total
    would be the one figure on the site that means nothing.

    Trophies replaced a "Badge Trophies" board that counted trophies in badge-covered games. That needed a
    full-library aggregate per profile inside the badge write seam, and once that seam ran on every sync it
    was the only expensive query in the subsystem -- for a figure that mostly measured how many
    badge-covered games somebody had played. See badge_leaderboards.trophy_rows.

    TWO filters, both of which swap what the board reads rather than post-filtering it:

      Country -> a WHERE served by the (country_code, ...board order) composites, on all three boards.
      Edition -> a different STORE (ProfileEditionStanding), on the two BADGE boards only.

    Neither is a board of its own. The old design's answer was a Redis sorted set per country, and an
    edition-per-board would multiply that again; keeping both as filters is what holds this section's
    surface area finite. They compose, and the edition indexes carry country to serve the combination.

    Edition is absent from Career XP because there is nothing to slice: the jobs economy has no platform
    editions, and a control that renders but changes nothing is worse than one that is not there.

    Every board is public, and its ROWS are identical for every viewer -- which is what would let the wall
    be cached, and why a personal marker never goes in one. The response as a whole is NOT cacheable: the
    header carries `my_standing`, which is per-viewer. Do not add `cache_page` on the strength of the first
    sentence; it would serve one logged-in hunter's ranks to every subsequent visitor.
    """
    template_name = 'trophies/overall_badge_leaderboards.html'
    paginate_by = board_helpers.PAGE_SIZE

    # (key, label). Order is the tab order; Badge Trophies leads because it has the most entrants.
    BOARDS = (
        ('trophies', 'Trophies'),
        ('points', 'Badge Points'),
        ('career', 'Career XP'),
    )
    BOARD_KEYS = {k for k, _ in BOARDS}
    # Only Badge Points slices by edition. An edition is a PlatformGroup, i.e. a BADGE concept; the
    # Trophies board counts trophies across every game and Career XP is the jobs economy, so neither has
    # editions to slice. A control that renders but changes nothing is worse than one that is absent.
    EDITION_BOARDS = frozenset({'points'})
    # `xp` was the old key for the Badge Points board; `country` was a TAB before country became a filter;
    # `progress` was this board's key while it was called Progress, a name that described the store rather
    # than what it ranks. Bookmarks carrying any of them still land where they meant to.
    #
    # `series` joins them: it was a DIRECTORY reachable at `?tab=series`, deliberately out of the tab
    # strip, held open as a placeholder for `/leaderboards/badges/`. That page was built and then removed
    # in 2026-08, and the placeholder read the RETIRED tier-era `Badge` model, which has had no writer
    # since cutover 5b -- so it rendered a frozen catalogue beside live standing counts. It maps to the
    # default board rather than 404ing: a stale bookmark should land on a board, not on an error.
    LEGACY_TABS = {'xp': 'points', 'country': 'points', 'progress': 'trophies', 'series': 'trophies'}

    #: (primary_label, secondary_label) per board. ONE definition: the column header, the first window and
    #: every window the rows endpoint serves all read it, so the labels above a column and the labels
    #: inside its rows cannot drift -- which is the failure a separate rows endpoint invites.
    FIGURES = {
        'trophies': ('platinums', 'trophies'),
        'points': ('points', 'badges'),
        'career': ('XP', 'level'),
    }

    #: What each board actually RANKS, in one line. Beside FIGURES because they answer the same question
    #: at different lengths, and a reader who has never met "Badge Points" learns nothing from a lit chip.
    #: The board card is the only place on the page that says what they are looking at.
    #: What each board RANKS, in one line, in the site's own words. Each has to do three things at once:
    #: name the population, name the ordering, and read like somebody wrote it. A reader who has never met
    #: "Badge Points" learns nothing from a lit chip, and this line is the only place on the page that
    #: explains the board they are looking at.
    MEANINGS = {
        'trophies': 'Every hunter on the site, ranked by platinums. Total trophies settles a tie.',
        'points': 'Badge points, earned a stage at a time. Every edition counts toward one total.',
        'career': 'Career XP banked from contracts, across all 25 jobs.',
    }

    @classmethod
    def active_tab(cls, request):
        raw = request.GET.get('tab', 'trophies')
        raw = cls.LEGACY_TABS.get(raw, raw)
        return raw if raw in cls.BOARD_KEYS else 'trophies'

    def _active_tab(self):
        return self.active_tab(self.request)

    def _country(self, codes):
        """The country slice, validated against `codes` -- countries that actually have ranked hunters.

        Validated rather than trusted: an unknown code would silently return an empty board, which reads
        as "nobody from there plays" rather than "that is not a country we rank".

        Takes the codes rather than resolving them, because the picker needs the same set: this used to
        call `active_countries()` here and `country_options()` called it again, so every request ran four
        table-wide DISTINCT aggregates where two would do.
        """
        cc = (self.request.GET.get('country') or '').upper()
        return cc if cc and cc in set(codes) else ''

    def _edition(self, tab, editions):
        """The edition slice, validated against LIVE editions and dropped on boards that have none.

        Validated for the same reason country is: an unrecognised key would render an empty board, which
        reads as "nobody plays that edition" rather than "that is not an edition". Cleared on Career so a
        reader who picked one and then switched boards does not carry an invisible filter with them.
        """
        if tab not in self.EDITION_BOARDS:
            return ''
        key = (self.request.GET.get('edition') or '').strip()
        return key if key in {e.key for e in editions} else ''

    def _href(self, tab, country='', edition=''):
        """A link to one board under one set of filters. The ONE place a leaderboard URL is assembled.

        Note what is never carried: `page`. Landing on page 7 of a board you just opened, or of a filter
        you just cleared, is not where anyone meant to go.
        """
        params = {'tab': tab}
        if country:
            params['country'] = country
        if edition and tab in self.EDITION_BOARDS:
            params['edition'] = edition
        return f'?{urlencode(params)}'

    def _board_links(self, country, edition):
        """[{key, label, href}] for the tab strip and the standing chips.

        Built here rather than assembled in the template because the rule for what each link carries is
        PER TARGET, not per page: country follows you everywhere, edition follows you only to the other
        badge board. A single shared querystring tail was the first attempt and it silently handed Career a
        filter it ignores -- so the link went one place and the rank shown beside it was measured somewhere
        else.
        """
        return [
            {'key': key, 'label': label, 'href': self._href(key, country, edition)}
            for key, label in self.BOARDS
        ]

    @staticmethod
    def _with_ranks(links, standing):
        """Fold the viewer's rank into each tab link, so the strip carries the standing.

        They were two controls stacked: a tab strip that navigates between boards, and a pill row that
        showed your rank on each AND linked to it. One control does both, and your standing stops being
        a block you scroll past -- the strip is always there.

        A board the viewer is not on gets `rank: None`, which the chip renders as a dash rather than
        omitting: a missing rank on a board you could be on is information.
        """
        return [dict(link, rank=(standing or {}).get(link['key'])) for link in links]

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        user = self.request.user
        tab = self._active_tab()
        profile = getattr(user, 'profile', None) if user.is_authenticated else None

        # Resolved ONCE and shared by the validator and the picker.
        codes = lb.active_countries()
        country = self._country(codes)
        countries = lb.country_options(codes)
        editions = lb.active_editions()
        edition = self._edition(tab, editions)

        context.update({
            'boards': self._board_links(country, edition),
            'active_tab': tab,
            'selected_country': country,
            'countries': countries,
            'editions': editions,
            'selected_edition': edition,
            # The display name, so the empty state and the standing label can say "Ultra HD" rather than
            # echo the slug back at the reader.
            'selected_edition_name': next((e.name for e in editions if e.key == edition), ''),
            # Same courtesy for country: the picker beside it shows "United States", so echoing "US" back
            # in the standing label reads as a different thing.
            'selected_country_name': next((c['name'] for c in countries if c['code'] == country), ''),
            # Only offered where it does something. See the class docstring.
            'edition_applies': tab in self.EDITION_BOARDS,
            # The empty state's escape hatches, from the same builder the tab strip uses. Each clears ONE
            # filter and keeps the other, which is the whole point of naming them separately -- assembled
            # inline in the template they were a second, untested copy of the rule `_href` owns.
            'clear_country_href': self._href(tab, '', edition),
            'clear_edition_href': self._href(tab, country, ''),
            'breadcrumb': [
                {'text': 'Home', 'url': reverse_lazy('home')},
                {'text': 'Leaderboards'},
            ],
        })

        # Only the ACTIVE board is built; the others cost nothing until asked for.
        #
        # The header tally counts THIS board, from the same `board_count` the paginator uses, so the figure
        # at the top and the "N total" under the wall are one number read once. It previously counted the
        # badge store unconditionally: on `?tab=career` that printed the badge population above the career
        # wall, and under a country slice it printed the global figure above a sliced one.
        if tab in self.BOARD_KEYS:
            entries, total = self._build_board(tab, country, edition)
            context['board_entries'] = entries
            context['ranked_total'] = total
            context['ranked_label'] = 'hunter'
            # For the virtual wall: the client sizes its spacer and its fetch granularity from these
            # rather than carrying constants of its own, which is how a page size silently desyncs from
            # the server that pages by it.
            context['page_size'] = self.paginate_by
            context['rows_url'] = reverse('leaderboard_rows')
            # The SLICE, carried on every later window. Without it the second screenful of a filtered
            # board comes back unfiltered -- the rows keep counting up, so it reads as the board rather
            # than as a bug. Built with urlencode (urllib's, imported at the top of this module) rather
            # than by hand: a country code is safe but an edition key is arbitrary text from the model.
            context['rows_params'] = urlencode(
                {k: v for k, v in (('tab', tab), ('country', country), ('edition', edition)) if v})
            primary_label, secondary_label = self.FIGURES[tab]
            context['primary_label'] = primary_label
            context['secondary_label'] = secondary_label
            context['board_meaning'] = self.MEANINGS[tab]
            context['board_label'] = dict(self.BOARDS)[tab]
            # "N hunters HERE" rather than "N hunters" when a filter is narrowing the board -- the figure
            # is a claim about a population, and under a slice it is a claim about a smaller one.
            context['slice_applied'] = bool(country or edition)

        # The viewer's own standing, ONCE, in the header -- not per row (see the class docstring).
        # Each rank is read under the SAME slice as the board it links to, so the number the reader sees
        # is the one they would find by scrolling. Career takes country only: it has no editions.
        if profile:
            cc = country or None
            ed = edition or None
            standing = {
                'trophies': lb.trophy_rank(profile.id, country=cc),
                'points': lb.xp_rank(profile.id, country=cc, edition=ed),
                'career': lb.career_xp_rank(profile.id, country=cc),
            }
            # The ACTIVE board's rank, for jump-to-my-rank. Already computed for the header strip, so
            # the affordance costs nothing extra -- it is the same number the reader is looking at.
            context['my_rank'] = standing.get(tab)
            # None when the viewer is on NO board, so the template's `{% if my_standing %}` means what it
            # reads as. A populated dict of all-Nones is still truthy, so the heading rendered with every
            # chip suppressed underneath it -- a bare "Your standing" over empty space, which is exactly
            # what the block's own comment says it is avoiding. That is the default state until the
            # standings are backfilled.
            context['my_standing'] = standing if any(v is not None for v in standing.values()) else None
            # The tab strip carries the ranks now, so it needs them whether or not any exist.
            context['boards'] = self._with_ranks(context['boards'], standing)
        return context

    def _build_board(self, tab, country, edition=''):
        """The FIRST window of the active board, and its size: two queries (the board read + one hydrate)
        plus the count that sizes the virtual spacer.

        `?page=` IS NOT READ, and that is the fix rather than an omission. This offset the first window by
        `(page - 1) * per` back when a pager rendered underneath it, and it survived the pager's deletion
        -- which made it worse than dead. `virtualBoard` seeds its row cache from the server-rendered
        window and marks page 0 as already fetched, so `/leaderboards/?page=2` handed it rows 51-100,
        recorded rows 1-50 as fetched, and then could never request them: the top of the board was fifty
        rows of permanently blank spacer. Job detail dropped the same parameter outright; the two
        surfaces should not disagree about it.
        """
        cc = country or None
        ed = edition or None
        # The board's membership rule lives in the SERVICE, next to the rows it governs -- see
        # `lb.board_count`. This used to be a hand-rolled copy of it here, and the copy drifted.
        total = lb.board_count(tab, country=cc, edition=ed)
        return self.board_window(tab, country, edition, 0, self.paginate_by), total

    #: How each board's store, ordering and column names differ, in one place. `board_window` and
    #: `board_suggest` both read it, so a search can never be scoped to a different population than the
    #: rows it is offering to jump into.
    @staticmethod
    def _store_for(tab, country, edition):
        cc = country or None
        if tab == 'trophies':
            # The Trophies board's store IS Profile, so its id column is `id` and its name columns are
            # unprefixed -- the other two point AT a profile.
            return lb._slice(lb.trophy_store(), cc), lb.TROPHY_KEYS, 'id', ''
        if tab == 'career':
            return lb._slice(lb.career_store(), cc), lb.CAREER_KEYS, 'profile_id', 'profile__'
        return lb._slice(lb.badge_store(edition or None), cc), lb.XP_KEYS, 'profile_id', 'profile__'

    @classmethod
    def board_suggest(cls, tab, country, edition, query):
        """Hunters on THIS board whose name starts with `query`, with their rank on it."""
        qs, keys, id_field, prefix = cls._store_for(tab, country, edition)
        return lb.board_suggest(qs, keys, query, id_field=id_field, name_prefix=prefix)

    @staticmethod
    def board_window(tab, country, edition, offset, limit):
        """ONE WINDOW of the active board, as render-ready entries.

        Shared by the page (which seeds the virtualizer's first window) and by `LeaderboardRowsView`
        (which serves every window after it). The tab branching lived inside the paginated builder, so a
        rows endpoint would have been a second copy of the figure labels -- and those labels are the one
        thing that must not drift between the first window and the rest of the same board.
        """
        cc = country or None
        ed = edition or None
        # The supporting figure is what gives the leading one its meaning: 9 platinums out of 140 trophies
        # is a different hunter from 9 out of 900, and 4,200 points across 30 badges from 4,200 across 6.
        primary_label, secondary_label = OverallBadgeLeaderboardsView.FIGURES[tab]

        if tab == 'trophies':
            rows = lb.trophy_rows(limit=limit, offset=offset, country=cc)
        elif tab == 'points':
            rows = lb.xp_rows(limit=limit, offset=offset, country=cc, edition=ed)
        else:
            rows = lb.career_xp_rows(limit=limit, offset=offset, country=cc)

        return lb.page(rows, offset, extra=lambda r: {
            'primary': r[1], 'primary_label': primary_label,
            'secondary': r[2], 'secondary_label': secondary_label,
        })

class LeaderboardRowsView(View):
    """`/leaderboards/rows/` -- one WINDOW of the active Global Board, as bare `.lb-row` elements.

    The server half of the virtualized wall. The page renders the first window inline, and every window
    after it comes through here: the engine asks for display positions [start, start+count) and splices
    the rows into its spacer.

    Reuses `OverallBadgeLeaderboardsView`'s own option parsing and `board_window`, deliberately. A rows
    endpoint that re-derived the tab, the country slice or the figure labels would be a second definition
    of the board, and the first thing to drift would be the labels -- so the rest of a board would be
    reading a different number from the screenful the reader arrived on.

    PUBLIC and viewer-independent: the rows are identical for everybody, which is what makes them
    cacheable. The viewer's own rank lives in the page header, never in a row.
    """

    def get(self, request):
        view = OverallBadgeLeaderboardsView
        tab = view.active_tab(request)
        codes = lb.active_countries()
        country = self._country(request, codes)
        edition = self._edition(request, tab, view)

        # THE TYPEAHEAD. Same endpoint, same slice, a different shape -- so a suggestion always names a
        # rank on the board being read rather than on some other version of it.
        if request.GET.get('suggest') is not None:
            return JsonResponse(suggest_json(
                view.board_suggest(tab, country, edition, request.GET.get('suggest', ''))))

        # Clamped at BOTH ends by the shared parser -- `range` is an OFFSET straight into the board, so an
        # unbounded value is a nine-figure OFFSET that Postgres honours by walking every skipped row.
        start, count = window_params(request, view.paginate_by)

        entries = view.board_window(tab, country, edition, start - 1, count)
        return render(request, 'trophies/partials/leaderboard_rows.html', {'entries': entries})

    @staticmethod
    def _country(request, codes):
        """Validated against the countries that actually have ranked hunters -- an unknown code would
        return an empty window, which reads as a gap in the board rather than a bad parameter."""
        raw = (request.GET.get('country') or '').strip().upper()
        return raw if raw in set(codes) else ''

    @staticmethod
    def _edition(request, tab, view):
        """Only the boards that HAVE editions accept one, matching the page. An edition on the Trophies or
        Career board would be silently ignored there, and a window that ignores a filter the first window
        applied would return different hunters."""
        if tab not in view.EDITION_BOARDS:
            return ''
        raw = (request.GET.get('edition') or '').strip()
        return raw if raw in {e.key for e in lb.active_editions()} else ''


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
