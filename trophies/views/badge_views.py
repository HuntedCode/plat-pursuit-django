import logging
from collections import defaultdict
from datetime import datetime, timedelta

from core.services.tracking import track_page_view
from trophies.constants import EVALUATABLE_BADGE_TYPES
from trophies.services.xp_service import get_tier_xp
from trophies.util_modules.constants import (
    BADGE_TIER_XP, BRONZE_STAGE_XP, SILVER_STAGE_XP,
    GOLD_STAGE_XP, PLAT_STAGE_XP, CONTRACT_XP_TOTAL,
    platform_display_rank,
)


def _badge_xp(badge):
    """Compute total XP value for a badge tier."""
    return badge.required_stages * get_tier_xp(badge.tier) + BADGE_TIER_XP


# Browse badge Gallery (the per-TIER medallion wall, ?view=gallery) -- the catalog-discovery cousin of the
# Series view. Every filter/sort below maps to a real Badge column so it stays DB-side + paginated at scale.
_TIER_NAME_TO_INT = {'bronze': 1, 'silver': 2, 'gold': 3, 'platinum': 4}
_GALLERY_STATES = ('earned', 'in_progress', 'maintenance', 'unearned')  # multi-select personal-state chips
# Badge-type display order (matches the Collection's sets) -- used to group the set-number sort by type,
# since set numbers restart per type (Series #1 and Franchise #1 both exist).
_TYPE_ORDER = ('series', 'franchise', 'collection', 'megamix', 'developer', 'user', 'event')
GALLERY_PAGE_SIZE = 48  # medallions per page (a multiple of common 2/3/4/6-column grids)
SERIES_PAGE_SIZE = 30   # series rows per page (Series view infinite scroll)
# A tier face renders a per-stage SEGMENTED meter only up to this many stages; above it, the smooth Horizon
# bar (coherent at any count). Kept low because the tile's narrowest column (2-col mobile, ~100px of bar)
# turns more segments than this into indistinct slivers -- big-series tiers (many games -> many stages) get
# the smooth bar instead.
TILE_SEGMENT_CAP = 8
# (key, label). Order mirrors the Collection Gallery's sort dropdown (name, rarest, tier, ..., set last).
GALLERY_SORTS = [
    ('set_number', 'Set order'),
    ('name', 'Name (A-Z)'),
    ('rarity', 'Rarest first'),
    ('tier', 'Tier (Platinum first)'),
    ('popular', 'Most earned'),
    ('newest', 'Newest'),
]
GALLERY_SORT_KEYS = {k for k, _ in GALLERY_SORTS}
GALLERY_SORT_DEFAULT = 'set_number'

from django.contrib.postgres.aggregates import ArrayAgg
from django.core.cache import cache
from django.core.exceptions import ObjectDoesNotExist
from django.core.paginator import Paginator
from django.db.models import Q, F, Prefetch, Max, Exists, OuterRef, Case, When, Value, IntegerField
from django.db.models.functions import Lower
from django.http import Http404, HttpResponseRedirect, HttpResponseNotFound
from urllib.parse import urlencode
from django.shortcuts import get_object_or_404, render
from django.urls import reverse_lazy, reverse
from django.utils.text import slugify
from django.views import View
from django.views.generic import ListView, DetailView, TemplateView

from ..models import (
    Game, Profile, ProfileGame, Badge, UserBadge, UserBadgeProgress,
    Concept, Stage, Milestone, UserMilestone, UserMilestoneProgress,
    UserTitle, ProfileGamification, BadgeSeries,
)
from ..forms import BadgeSearchForm
from trophies.services.badge_detail_service import get_badge_detail
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
from trophies.milestone_constants import (
    MILESTONE_CATEGORIES, CRITERIA_TYPE_DISPLAY_NAMES,
    CALENDAR_MONTH_TYPES, ONE_OFF_TYPES,
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
        # The tile renders per-tier medallions (build_badge_frame) + the affiliation name; it no longer shows
        # cover art or the Title, so only the FKs those frames read are joined. (Dropping the concept/
        # igdb_match joins also stops pulling the ~30KB raw_response blob per badge.) submitted_by stays --
        # get_badge_layers uses it for user-type badge art.
        qs = super().get_queryset().live().select_related(
            'base_badge', 'submitted_by', 'base_badge__submitted_by',
            'franchise', 'collection', 'developer', 'funded_by',
            'base_badge__franchise', 'base_badge__collection', 'base_badge__developer', 'base_badge__funded_by',
        )
        form = self.get_filter_form()

        if form.is_valid():
            series_slug = slugify(form.cleaned_data.get('series_slug'))
            if series_slug:
                qs = qs.filter(series_slug__icontains=series_slug)
        # badge_type is now MULTI-select (chips) -> __in; read the raw list (the form field is single).
        types = [t for t in self.request.GET.getlist('badge_type') if t]
        if types:
            qs = qs.filter(badge_type__in=types)
        return qs

    def _gallery_queryset(self):
        """The Browse Gallery's per-TIER queryset: one row per individual badge tier (not per series),
        every filter + sort DB-side so it paginates at catalog scale. Mirrors the game-browse pattern
        (Exists subqueries for personal state, real-column order_by). select_related covers every FK
        build_badge_frame reads so the batched frame build issues no per-badge FK queries."""
        qs = Badge.objects.live().select_related(
            'base_badge', 'franchise', 'collection', 'developer', 'funded_by', 'submitted_by',
            'base_badge__franchise', 'base_badge__collection',
            'base_badge__developer', 'base_badge__funded_by', 'base_badge__submitted_by',
        )
        g = self.request.GET

        # All filters are MULTI-select (chips): tier and type are `__in`; several selected = OR.
        tiers = [_TIER_NAME_TO_INT[t] for t in g.getlist('tier') if t in _TIER_NAME_TO_INT]
        if tiers:
            qs = qs.filter(tier__in=tiers)
        types = [t for t in g.getlist('badge_type') if t]
        if types:
            qs = qs.filter(badge_type__in=types)
        q = (g.get('q') or '').strip()
        if q:
            search_q = (
                Q(series_slug__icontains=slugify(q)) | Q(name__icontains=q) | Q(display_title__icontains=q)
            )
            # A numeric query (optionally "#0042") also matches the badge's edition/set number.
            numeric = q.lstrip('#')
            if numeric.isdigit() and len(numeric) <= 9:   # fits a PositiveIntegerField; guards absurd input
                search_q |= Q(set_number=int(numeric))
            qs = qs.filter(search_q)

        # Personal-state multi-select (auth only): pick any of earned / in-progress / maintenance / unearned;
        # the chosen states are OR'd. Derived from EXISTS probes annotated once, then a Q per selected state
        # (this also subsumes the old "hide" toggles -- to hide a state, just don't select it). Anonymous
        # viewers get the pure catalog (the state chips aren't shown).
        profile = self._profile()
        states = [s for s in g.getlist('state') if s in _GALLERY_STATES] if profile else []
        if states:
            held = UserBadge.objects.filter(profile=profile, badge=OuterRef('pk'))
            started = UserBadgeProgress.objects.filter(
                profile=profile, badge=OuterRef('pk'), completed_concepts__gt=0,
            )
            qs = qs.annotate(
                _earned=Exists(held.filter(status='earned')),
                _maint=Exists(held.filter(status='maintenance')),
                _started=Exists(started),
            )
            state_q = {
                'earned': Q(_earned=True),
                'maintenance': Q(_maint=True),
                'in_progress': Q(_started=True, _earned=False, _maint=False),
                'unearned': Q(_earned=False, _maint=False, _started=False),
            }
            combined = Q()
            for s in states:
                combined |= state_q[s]
            qs = qs.filter(combined)

        # Every order_by ends on 'pk' -- a unique final tiebreaker so ties (same earned_count / name / date)
        # get a DEFINED, stable order across the page-1 render and the ?page=N infinite-scroll fetches
        # (otherwise Postgres could reorder ties between pages -> a duplicated or skipped medallion).
        name_key = Lower('name')
        # SET ORDER is the catalog's canonical ordering (default sort) AND the tiebreaker within every other
        # sort, so cards always fall back into set order on a tie. Set numbers restart per badge type, so
        # group by type first, then edition order (unnumbered last), then tier -- keeping each type's numbered
        # run contiguous. ('pk' still ends every order_by as the unique final tiebreak for stable pagination.)
        type_order = Case(
            *[When(badge_type=t, then=Value(i)) for i, t in enumerate(_TYPE_ORDER)],
            default=Value(len(_TYPE_ORDER)), output_field=IntegerField(),
        )
        set_order = (type_order, F('set_number').asc(nulls_last=True), 'tier')
        sort = g.get('sort') if g.get('sort') in GALLERY_SORT_KEYS else GALLERY_SORT_DEFAULT
        if sort == 'set_number':
            qs = qs.order_by(*set_order, 'pk')
        elif sort == 'rarity':
            qs = qs.order_by('earned_count', *set_order, 'pk')    # fewest earners = rarest first
        elif sort == 'popular':
            qs = qs.order_by('-earned_count', *set_order, 'pk')
        elif sort == 'newest':
            qs = qs.order_by('-created_at', *set_order, 'pk')
        elif sort == 'tier':
            qs = qs.order_by('-tier', *set_order, 'pk')           # platinum first (matches the Collection)
        else:
            qs = qs.order_by(name_key, *set_order, 'pk')          # A-Z, set order on ties
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
        badge_ids = [b.id for b in page_badges]
        earned_map, progress_map = {}, {}
        if profile:
            earned_map = {
                ub.badge_id: ub
                for ub in UserBadge.objects.filter(profile=profile, badge_id__in=badge_ids)
            }
            progress_map = {
                pr.badge_id: pr
                for pr in UserBadgeProgress.objects.filter(profile=profile, badge_id__in=badge_ids)
            }

        frames = []
        for b in page_badges:
            if profile:
                frame = build_badge_frame(
                    b, profile, earned=earned_map.get(b.id), progress=progress_map.get(b.id),
                    include_live_stats=False, showcase=True,
                )
            else:
                frame = build_badge_frame(b, None, include_live_stats=False, showcase=True)
            frame['series_slug'] = b.series_slug
            frame['badge_id'] = b.id
            frames.append(frame)

        g = self.request.GET
        context.update({
            'view': 'gallery',
            'gallery_frames': frames,
            'page_obj': page_obj,
            'paginator': paginator,
            'is_paginated': page_obj.has_other_pages(),
            'form': self.get_filter_form(),                 # supplies the badge_type choices for the chips
            'gallery_authed': profile is not None,
            'gallery_tiers': g.getlist('tier'),             # selected chip values (multi-select)
            'gallery_states': g.getlist('state'),
            'gallery_types': g.getlist('badge_type'),
            'gallery_sort': g.get('sort') if g.get('sort') in GALLERY_SORT_KEYS else GALLERY_SORT_DEFAULT,
            'gallery_q': g.get('q', ''),
            'gallery_sorts': GALLERY_SORTS,
            'gallery_page_size': GALLERY_PAGE_SIZE,          # keeps the JS paginateBy in sync (no magic 48)
            'catalog_stats': self._catalog_header_stats(),  # generalized collection stats (hourly-cached)
            'breadcrumb': [
                {'text': 'Home', 'url': reverse_lazy('home')},
                {'text': 'Badges'},
            ],
            'seo_description': (
                "Browse every badge on Platinum Pursuit -- filter by tier, rarity, and type to find "
                "your next platinum to chase."
            ),
        })
        # Only count a real page view, not each infinite-scroll ?page=N XHR fetch (which would inflate it).
        if not is_xhr:
            track_page_view('badges_list', 'gallery', self.request)
        return context

    def _calculate_all_series_stats(self, series_slugs):
        """
        Calculate total games and trophy counts (series-level AND per-tier) for multiple series in bulk.

        One query fetches every game across the requested series with the tier-set of the stage that links
        it, then groups in memory (eliminates N*2 per-series queries). Per-tier trophy totals let each tile
        face show that tier's own trophy spread; since higher tiers require more stages, their totals are
        supersets of the lower tiers'.

        Args:
            series_slugs: Iterable of series slug strings

        Returns:
            dict: {series_slug: (total_games, trophy_types, per_tier_trophies)} where per_tier_trophies is
                  {tier_int: {'bronze','silver','gold','platinum'}} for tiers 1-4.
        """
        ALL_TIERS = (1, 2, 3, 4)
        rows = Game.objects.filter(
            concept__stages__series_slug__in=series_slugs
        ).values_list(
            'id', 'concept__stages__series_slug',
            'concept__stages__required_tiers', 'defined_trophies',
        ).distinct()

        # Per (slug, game): its trophies + the UNION of tiers it counts toward. A stage with empty
        # required_tiers applies to every tier; otherwise to the listed tiers. A game linked through several
        # stages unions their tier-sets (one row per stage; we merge them).
        series_games = defaultdict(dict)  # slug -> {game_id: {'trophies': ..., 'tiers': set()}}
        for game_id, slug, req_tiers, trophies in rows:
            entry = series_games[slug].setdefault(game_id, {'trophies': trophies, 'tiers': set()})
            # Empty required_tiers = applies to every tier. Clamp to 1-4 so a stray out-of-range value in the
            # ArrayField can never KeyError per_tier below and take down the whole page render.
            entry['tiers'].update(t for t in (req_tiers or ALL_TIERS) if t in ALL_TIERS)

        result = {}
        for slug in series_slugs:
            games_map = series_games.get(slug, {})
            total_games = len(games_map)
            trophy_types = {'bronze': 0, 'silver': 0, 'gold': 0, 'platinum': 0}
            per_tier = {t: {'bronze': 0, 'silver': 0, 'gold': 0, 'platinum': 0} for t in ALL_TIERS}
            for entry in games_map.values():
                trophies = entry['trophies']
                if not trophies:
                    continue
                for kind in ('bronze', 'silver', 'gold', 'platinum'):
                    n = trophies.get(kind, 0)
                    trophy_types[kind] += n
                    for t in entry['tiers']:
                        per_tier[t][kind] += n
            result[slug] = (total_games, trophy_types, per_tier)

        return result

    def _build_badge_display_data(self, grouped_badges, profile=None):
        """
        Build display data for badges with optional progress tracking.

        Consolidates logic for both authenticated and unauthenticated states.

        Args:
            grouped_badges: Dict of {series_slug: [badge list]}
            profile: Profile instance or None

        Returns:
            list: Display data dicts for each badge series
        """
        display_data = []

        # Get user progress data if authenticated
        earned_dict = {}
        maint_dict = {}
        progress_dict = {}
        if profile:
            user_earned = UserBadge.objects.filter(profile=profile).values('badge__series_slug').annotate(max_tier=Max('badge__tier'))
            earned_dict = {e['badge__series_slug']: e['max_tier'] for e in user_earned}
            # Maintenance (lapsed) tiers -- HELD but need re-earning. A UserBadge is never deleted; when a series
            # grows and the user lapses, its status flips to 'maintenance'. It still counts as held (so it's in
            # earned_dict's max, earn_rank stays permanent), but it must read as a REPAIR state and be the tile's
            # resting face -- not a clean 'earned' tier the working rung skips past. DB-aggregated to a
            # {series_slug: [tiers]} map (one row per series), matching earned_dict's whale-safe pattern.
            maint_dict = {
                m['badge__series_slug']: set(m['tiers'])
                for m in UserBadge.objects.filter(profile=profile, status='maintenance')
                .values('badge__series_slug').annotate(tiers=ArrayAgg('badge__tier'))
            }

            all_badges_ids = [b.id for group in grouped_badges.values() for b in group]
            progress_qs = UserBadgeProgress.objects.filter(
                profile=profile, badge__id__in=all_badges_ids
            ).select_related('badge')
            progress_dict = {p.badge_id: p for p in progress_qs}

        # Bulk-fetch series stats for all series at once (1 query instead of N*2)
        all_series_stats = self._calculate_all_series_stats(grouped_badges.keys())

        # Build display data for each series
        for slug, group in grouped_badges.items():
            sorted_group = sorted(group, key=lambda b: b.tier)
            if not sorted_group:
                continue

            tier1_badge = next((b for b in sorted_group if b.tier == 1), None)
            if not tier1_badge:
                continue

            # Look up pre-computed series stats (series-level + per-tier trophy spreads)
            _zero = {'bronze': 0, 'silver': 0, 'gold': 0, 'platinum': 0}
            total_games, trophy_types, per_tier_trophies = all_series_stats.get(slug, (0, dict(_zero), {}))
            tier1_earned_count = tier1_badge.earned_count

            # Determine display badge and progress
            highest_tier = earned_dict.get(slug, 0)   # 0 for anon / not started
            if profile:
                display_badge = next((b for b in sorted_group if b.tier == highest_tier), None) if highest_tier > 0 else tier1_badge
                if not display_badge:
                    continue

                is_earned = highest_tier > 0
                next_badge = next((b for b in sorted_group if b.tier > highest_tier), None)
                progress_badge = next_badge if next_badge else display_badge

                # Calculate progress
                progress = progress_dict.get(progress_badge.id) if progress_badge else None
                required_stages = progress_badge.required_stages
                if progress and progress_badge.badge_type in EVALUATABLE_BADGE_TYPES:
                    completed_concepts = progress.completed_concepts
                    progress_percentage = (completed_concepts / required_stages) * 100 if required_stages > 0 else 0
                else:
                    completed_concepts = 0
                    progress_percentage = 0
            else:
                # Unauthenticated user - show tier 1
                display_badge = tier1_badge
                is_earned = False
                completed_concepts = 0
                required_stages = tier1_badge.required_stages
                progress_percentage = 0

            # Per-tier ladder faces (the swappable faces on the card): each tier's state + progress, built
            # ONLY from data already fetched (grouped_badges + earned_dict + progress_dict) -- no new queries,
            # whale-safe. The resting face (`default_tier`) is the tier you're working on (the lowest unearned
            # tier); Bronze if nothing is started, the top tier if the series is finished; anon sees Bronze.
            present_tiers = [b.tier for b in sorted_group]
            # Intersect with the tiers actually rendered on this tile: a held tier whose badge went non-live
            # would otherwise make default_tier point at a face that doesn't exist (every pane stays hidden).
            maint_tiers = maint_dict.get(slug, set()) & set(present_tiers)
            working_rung = next((t for t in present_tiers if t > highest_tier), None)
            # A lapsed (maintenance) tier needs re-earning, so it -- not the next unearned rung -- is the
            # resting face (the lowest such tier). Otherwise: the tier you're working on, then the top tier.
            default_tier = min(maint_tiers) if maint_tiers else (working_rung or present_tiers[-1])
            tier_faces = []
            for b in sorted_group:
                req_t = b.required_stages
                if b.tier in maint_tiers:
                    # Held but lapsed -> a repair state showing the CURRENT progress, not a clean earned bar.
                    t_state = 'maintenance'
                    pr = progress_dict.get(b.id)
                    if pr and b.badge_type in EVALUATABLE_BADGE_TYPES:
                        t_done = pr.completed_concepts
                        t_pct = round((t_done / req_t) * 100, 1) if req_t else 0
                    else:
                        t_done, t_pct = 0, 0
                elif b.tier <= highest_tier:
                    t_state, t_done, t_pct = 'earned', req_t, 100
                elif b.tier == working_rung:
                    t_state = 'active'
                    pr = progress_dict.get(b.id)
                    if pr and b.badge_type in EVALUATABLE_BADGE_TYPES:
                        t_done = pr.completed_concepts
                        t_pct = round((t_done / req_t) * 100, 1) if req_t else 0
                    else:
                        t_done, t_pct = 0, 0
                else:
                    # Locked (beyond the tier you're working on): no progress shown, even if a stale
                    # UserBadgeProgress row happens to exist for that tier.
                    t_state, t_done, t_pct = 'locked', 0, 0

                # XP earned-so-far / total on offer for this tier: stage XP accrues per completed stage; the
                # flat tier bonus lands only once the tier is earned. (Megamix uses required_stages =
                # min_required, so its "/ total" here is the min-completion XP, not the full-set XP -- the
                # detail page computes the exact figure; the browse tile stays cheap.)
                stage_xp = get_tier_xp(b.tier)
                xp_total = req_t * stage_xp + BADGE_TIER_XP
                xp_earned = xp_total if t_state == 'earned' else t_done * stage_xp

                # Per-stage segmented meter when countable (<= cap); above it the face uses a smooth bar.
                # 'done' = completed, 'active' = the current stage, '' = still to do.
                segments = None
                if 0 < req_t <= TILE_SEGMENT_CAP:
                    segments = ['done'] * min(t_done, req_t)
                    if t_state in ('active', 'maintenance') and t_done < req_t:
                        segments.append('active')
                    segments += [''] * (req_t - len(segments))

                tier_faces.append({
                    'tier': b.tier,
                    'badge_id': b.id,
                    'state': t_state,
                    'completed': t_done,
                    'required': req_t,
                    'progress_pct': t_pct,
                    'remaining': max(req_t - t_done, 0),
                    'xp_earned': xp_earned,
                    'xp_total': xp_total,
                    'trophies': per_tier_trophies.get(b.tier, dict(_zero)),
                    'segments': segments,
                })

            # ONE medallion per tile (not four): a series' tiers share the subject art, and only the tier
            # tint + the four site-wide, tier-keyed, cached STATIC backdrop/foreground images differ. So we
            # render just the default tier's medallion here and retint + swap those cached images client-side
            # on a face change (see the scardSelect handler in badge_list.html). Cuts the heaviest per-tile
            # work -- the frame build + medallion render -- from 4x to 1x. Anon look, so no per-badge queries.
            default_badge = next((b for b in sorted_group if b.tier == default_tier), tier1_badge)
            default_frame = build_badge_frame(default_badge, None, include_live_stats=False)
            # A lapsed default tier is HELD but not clean-earned -> no seal (it reads as a repair state).
            default_earned = default_tier <= highest_tier and default_tier not in maint_tiers

            # Card name: the badge's affiliation takes precedence -- Franchise > Series (IGDB collection) >
            # Developer -- else the series' Display Series (then the display title). Mirrors the medallion's
            # engraved affiliation label so the card and the object agree.
            _fr = display_badge.effective_franchise
            _co = display_badge.effective_collection
            _dv = display_badge.effective_developer
            card_name = (
                (_fr.name if _fr else '')
                or (_co.name if _co else '')
                or (_dv.name if _dv else '')
                or display_badge.effective_display_series
                or display_badge.effective_display_title
                or display_badge.name   # final fallback (matches the medallion's series_name) -- never None
            )

            display_data.append({
                'badge': display_badge,
                'card_name': card_name,
                'tier1_earned_count': tier1_earned_count,
                'completed_concepts': completed_concepts,
                'required_stages': required_stages,
                'progress_percentage': round(progress_percentage, 1),
                'trophy_types': trophy_types,
                'total_games': total_games,
                'is_earned': is_earned,
                'user_highest_tier': highest_tier,
                'tiers': tier_faces,
                'default_tier': default_tier,
                'default_frame': default_frame,
                'default_earned': default_earned,
                # Any lapsed rung flips the whole tile into a maintenance treatment (corner "M" + red floor),
                # so the "needs re-earning" signal reads at a glance without opening every face.
                'has_maintenance': bool(maint_tiers),
            })

        return display_data

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

    def get_context_data(self, **kwargs):
        """
        Build context for badge list page.

        Groups badges by series, calculates progress for authenticated users,
        and handles sorting and pagination.

        Returns:
            dict: Context with paginated badge display data
        """
        if self._view_mode() == 'gallery':
            return self._gallery_context_data(**kwargs)
        context = super().get_context_data(**kwargs)
        badges = context['object_list']

        # Group badges by series. Visibility is controlled by the is_live flag
        # (already filtered via .live() in the queryset).
        grouped_badges = defaultdict(list)
        for badge in badges:
            grouped_badges[badge.series_slug].append(badge)

        # Build display data (unified for auth/unauth users)
        user = self.request.user
        profile = user.profile if user.is_authenticated and hasattr(user, 'profile') else None
        display_data = self._build_badge_display_data(grouped_badges, profile)

        # Enrich with auth-only sort data (games owned, last progress date)
        sort_val = self.request.GET.get('sort', 'name')
        if profile and sort_val in ('games_owned', 'games_owned_inv', 'recently_progressed'):
            if sort_val in ('games_owned', 'games_owned_inv'):
                # Count how many games in each badge series the user owns
                user_game_ids = set(
                    ProfileGame.objects.filter(profile=profile).values_list('game_id', flat=True)
                )
                for d in display_data:
                    slug = d['badge'].series_slug
                    badge_concept_ids = set(
                        Stage.objects.filter(series_slug=slug).values_list('concepts__id', flat=True)
                    )
                    badge_game_ids = set(
                        Game.objects.filter(concept_id__in=badge_concept_ids).values_list('id', flat=True)
                    ) if badge_concept_ids else set()
                    d['games_owned_count'] = len(user_game_ids & badge_game_ids)
            elif sort_val == 'recently_progressed':
                # Last progress check date per badge
                progress_dates = {
                    p.badge.series_slug: p.last_checked
                    for p in UserBadgeProgress.objects.filter(
                        profile=profile,
                    ).select_related('badge')
                }
                for d in display_data:
                    d['last_progress_date'] = progress_dates.get(d['badge'].series_slug)

        # Filter by completion status (auth-only) -- now MULTI-select chips, OR'd. Keep the exact original
        # per-status predicates (they intentionally overlap: a not-started-but-started series matches both
        # not_started and in_progress).
        completion_statuses = [s for s in self.request.GET.getlist('completion_status') if s]
        if completion_statuses and profile:
            max_tier_lookup = {}
            for d in display_data:
                slug = d['badge'].series_slug
                max_possible = max(
                    (b.tier for b in grouped_badges.get(slug, []) if b.is_live),
                    default=0,
                )
                max_tier_lookup[slug] = max_possible

            def _status_match(d):
                mt = d['user_highest_tier']
                cap = max_tier_lookup.get(d['badge'].series_slug, 0)
                prog = d['progress_percentage']
                for s in completion_statuses:
                    if s == 'not_started' and mt == 0:
                        return True
                    if s == 'in_progress' and (0 < mt < cap or (mt == 0 and prog > 0)):
                        return True
                    if s == 'completed' and (mt > 0 and mt >= cap):
                        return True
                return False

            display_data = [d for d in display_data if _status_match(d)]

        # Sort data
        sort_val = self.request.GET.get('sort', 'name')
        _title = lambda d: (d['badge'].effective_display_title or '').lower()
        if sort_val == 'earned':
            display_data.sort(key=lambda d: (-d['tier1_earned_count'], _title(d)))
        elif sort_val == 'earned_inv':
            display_data.sort(key=lambda d: (d['tier1_earned_count'], _title(d)))
        elif sort_val == 'my_tier' and profile:
            display_data.sort(key=lambda d: (d['user_highest_tier'], _title(d)))
        elif sort_val == 'my_tier_desc' and profile:
            display_data.sort(key=lambda d: (-d['user_highest_tier'], _title(d)))
        elif sort_val == 'stages':
            display_data.sort(key=lambda d: (-d['badge'].required_stages, _title(d)))
        elif sort_val == 'stages_inv':
            display_data.sort(key=lambda d: (d['badge'].required_stages, _title(d)))
        elif sort_val == 'newest':
            display_data.sort(key=lambda d: d['badge'].created_at or datetime.min, reverse=True)
        elif sort_val == 'oldest_added':
            display_data.sort(key=lambda d: d['badge'].created_at or datetime.min)
        elif sort_val == 'xp':
            display_data.sort(key=lambda d: (-_badge_xp(d['badge']), _title(d)))
        elif sort_val == 'xp_inv':
            display_data.sort(key=lambda d: (_badge_xp(d['badge']), _title(d)))
        elif sort_val == 'closest' and profile:
            # Closest to completing next tier: highest progress_percentage first, exclude completed
            display_data.sort(key=lambda d: (-d['progress_percentage'], _title(d)))
        elif sort_val == 'games_owned' and profile:
            display_data.sort(key=lambda d: (-d.get('games_owned_count', 0), _title(d)))
        elif sort_val == 'games_owned_inv' and profile:
            display_data.sort(key=lambda d: (d.get('games_owned_count', 0), _title(d)))
        elif sort_val == 'recently_progressed' and profile:
            display_data.sort(
                key=lambda d: d.get('last_progress_date') or datetime.min,
                reverse=True,
            )
        else:
            display_data.sort(key=lambda d: _title(d))

        # Paginate. InfiniteScroller walks pages 2,3,... via XHR; get_page clamps an out-of-range page to the
        # last (which would loop it forever), so an XHR fetch past the end emits NO rows and the scroller stops.
        paginator = Paginator(display_data, SERIES_PAGE_SIZE)
        page_number = self.request.GET.get('page')
        page_obj = paginator.get_page(page_number)
        try:
            requested_page = int(page_number or 1)
        except (TypeError, ValueError):
            requested_page = 1
        is_xhr = (
            self.request.headers.get('X-Requested-With') == 'XMLHttpRequest'
            or (getattr(self.request, 'htmx', False) and self.request.htmx.target == 'browse-results')
        )
        context['display_data'] = [] if (is_xhr and requested_page > paginator.num_pages) else page_obj
        context['page_obj'] = page_obj
        context['paginator'] = paginator
        context['is_paginated'] = page_obj.has_other_pages()
        context['series_page_size'] = SERIES_PAGE_SIZE

        # Generalized badge-collection catalog stats in the header (hourly-cached, no profile needed).
        context['catalog_stats'] = self._catalog_header_stats()
        # series_badge_xp is Series-tile-only (per-tile XP), so it stays profile-scoped here.
        if profile:
            try:
                context['series_badge_xp'] = profile.gamification.series_badge_xp or {}
            except ProfileGamification.DoesNotExist:
                context['series_badge_xp'] = {}

        # Breadcrumbs and form
        context['breadcrumb'] = [
            {'text': 'Home', 'url': reverse_lazy('home')},
            {'text': 'Badges'},
        ]
        context['form'] = self.get_filter_form()
        # Multi-select chip state for the rebuilt toolbar.
        context['selected_badge_types'] = self.request.GET.getlist('badge_type')
        context['selected_completion_statuses'] = self.request.GET.getlist('completion_status')
        context['series_sort'] = self.request.GET.get('sort', 'name')
        context['series_q'] = self.request.GET.get('series_slug', '')

        context['seo_description'] = (
            "Explore all badge series on Platinum Pursuit. "
            "Track your progress across game collections and earn every tier."
        )

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

        context['detail'] = get_badge_detail(series, target_profile)

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



class MilestoneListView(ListView):
    """
    Display milestones organized by category tabs with tier ladder progression.

    Tabs: Overview, Trophy Hunting, Community, Collection, Challenges,
    Getting Started, Special. Each category shows tier ladders for its
    criteria types with earned/active/locked visual states.
    """
    model = Milestone
    template_name = 'trophies/milestone_list.html'
    context_object_name = 'milestones'

    def get_queryset(self):
        # active() excludes retired milestones (is_active=False) so the page never shows them.
        return Milestone.objects.active().select_related('title').ordered_by_value()

    def _get_user_data(self, profile, milestones):
        """Fetch earned milestones, progress, and earned dates for a profile."""
        if not profile:
            return set(), {}, {}

        earned_qs = UserMilestone.objects.filter(
            profile=profile, milestone__in=milestones
        ).select_related('milestone')
        earned_milestone_ids = set()
        earned_dates = {}
        for um in earned_qs:
            earned_milestone_ids.add(um.milestone_id)
            earned_dates[um.milestone_id] = um.earned_at

        progress_qs = UserMilestoneProgress.objects.filter(
            profile=profile, milestone__in=milestones
        )
        progress_dict = {p.milestone_id: p.progress_value for p in progress_qs}

        return earned_milestone_ids, progress_dict, earned_dates

    def _build_tier_ladder(self, type_milestones, earned_ids, progress_dict, earned_dates, profile):
        """Build tier ladder data for a single criteria_type group."""
        sorted_ms = sorted(type_milestones, key=lambda m: m.required_value)
        total_tiers = len(sorted_ms)
        tiers = []
        found_active = False

        for idx, milestone in enumerate(sorted_ms, start=1):
            is_earned = milestone.id in earned_ids
            progress_value = progress_dict.get(milestone.id, 0)
            required_value = milestone.required_value

            if required_value > 0:
                progress_pct = min((progress_value / required_value) * 100, 100)
            else:
                progress_pct = 100 if is_earned else 0

            # Determine state: earned, active (next target), locked, or preview (guest)
            if is_earned:
                state = 'earned'
            elif not profile:
                state = 'preview'
            elif not found_active:
                state = 'active'
                found_active = True
            else:
                state = 'locked'

            tiers.append({
                'milestone': milestone,
                'tier_number': idx,
                'total_tiers': total_tiers,
                'state': state,
                'is_earned': is_earned,
                'progress_value': progress_value,
                'required_value': required_value,
                'progress_percentage': round(progress_pct, 1),
                'earned_count': milestone.earned_count,
                'earned_at': earned_dates.get(milestone.id),
            })

        earned_count = sum(1 for t in tiers if t['is_earned'])
        return {
            'tiers': tiers,
            'total_tiers': total_tiers,
            'earned_tiers': earned_count,
        }

    def _build_category_data(self, milestones, category_config, profile,
                             earned_ids, progress_dict, earned_dates):
        """Build tier ladders for all criteria_types in a category."""
        criteria_types = category_config['criteria_types']

        # Group milestones by criteria_type
        by_type = defaultdict(list)
        for m in milestones:
            if m.criteria_type in criteria_types:
                by_type[m.criteria_type].append(m)

        # Build ladders, preserving the order from criteria_types
        # Separate tiered types from calendar month types
        tiered_ladders = []
        calendar_months = []
        oneoff_cards = []

        for ct in criteria_types:
            if ct not in by_type:
                continue
            display_name = CRITERIA_TYPE_DISPLAY_NAMES.get(ct, ct)

            if ct in CALENDAR_MONTH_TYPES:
                # Calendar months go into the grid
                ms = by_type[ct][0]  # One milestone per month
                calendar_months.append({
                    'milestone': ms,
                    'criteria_type': ct,
                    'month_abbr': ct.replace('calendar_month_', '').upper(),
                    'is_earned': ms.id in earned_ids,
                    'earned_at': earned_dates.get(ms.id),
                })
            elif ct in ONE_OFF_TYPES:
                # One-off milestones (calendar_complete, psn_linked, etc.)
                # Manual milestones are "Feats of Strength": show all of them,
                # but only display earned ones (hidden until unlocked).
                milestones_for_type = by_type[ct] if ct == 'manual' else [by_type[ct][0]]
                for ms in milestones_for_type:
                    if ct == 'manual' and ms.id not in earned_ids:
                        continue
                    progress_value = progress_dict.get(ms.id, 0)
                    required_value = ms.required_value
                    if required_value > 0:
                        pct = min((progress_value / required_value) * 100, 100)
                    else:
                        pct = 100 if ms.id in earned_ids else 0
                    oneoff_cards.append({
                        'milestone': ms,
                        'criteria_type': ct,
                        'display_name': display_name,
                        'is_earned': ms.id in earned_ids,
                        'earned_at': earned_dates.get(ms.id),
                        'progress_value': progress_value,
                        'required_value': required_value,
                        'progress_percentage': round(pct, 1),
                        'earned_count': ms.earned_count,
                    })
            else:
                # Tiered ladder
                ladder = self._build_tier_ladder(
                    by_type[ct], earned_ids, progress_dict, earned_dates, profile
                )
                ladder['criteria_type'] = ct
                ladder['display_name'] = display_name
                tiered_ladders.append(ladder)

        calendar_months_earned = sum(1 for m in calendar_months if m['is_earned'])
        return {
            'tiered_ladders': tiered_ladders,
            'calendar_months': calendar_months,
            'calendar_months_earned': calendar_months_earned,
            'oneoff_cards': oneoff_cards,
        }

    def _build_overview_data(self, milestones, profile, earned_ids, earned_dates):
        """Build overview tab data with per-category stats."""
        # Per-category earned/total counts
        category_stats = []
        for slug, config in MILESTONE_CATEGORIES.items():
            if slug == 'overview':
                continue
            types = config['criteria_types']
            cat_milestones = [m for m in milestones if m.criteria_type in types]
            # Special/manual milestones are only visible when earned,
            # so total should reflect only earned ones for accurate counts
            if slug == 'special':
                cat_milestones = [m for m in cat_milestones if m.id in earned_ids] if profile else []
            total = len(cat_milestones)
            earned = sum(1 for m in cat_milestones if m.id in earned_ids) if profile else 0
            pct = round((earned / total * 100), 1) if total > 0 else 0

            # Find most recently earned milestones in this category
            recent_earned = []
            if profile:
                cat_earned = [
                    (m, earned_dates[m.id])
                    for m in cat_milestones
                    if m.id in earned_ids and m.id in earned_dates
                ]
                cat_earned.sort(key=lambda x: x[1], reverse=True)
                recent_earned = [item[0] for item in cat_earned[:2]]

            # Special/manual milestones are "Feats of Strength": they exist and
            # can be earned, but don't count toward totals or show a denominator
            is_feats = (slug == 'special')

            category_stats.append({
                'slug': slug,
                'name': config['name'],
                'icon': config['icon'],
                'total': total,
                'earned': earned,
                'percentage': pct,
                'recent_earned': recent_earned,
                'is_feats_of_strength': is_feats,
            })

        # Overall stats (exclude special/manual milestones from totals)
        countable = [m for m in milestones if m.criteria_type != 'manual']
        total_milestones = len(countable)
        total_earned = sum(1 for m in countable if m.id in earned_ids) if profile else 0
        overall_pct = round((total_earned / total_milestones * 100), 1) if total_milestones > 0 else 0

        # Titles unlocked from milestones
        titles_unlocked = 0
        if profile:
            titles_unlocked = UserTitle.objects.filter(
                profile=profile, source_type='milestone'
            ).count()

        # Most recently earned milestone (derived from earned_dates, no extra query)
        latest_milestone = None
        if profile and earned_dates:
            latest_id = max(earned_dates, key=earned_dates.get)
            latest_ms = next((m for m in milestones if m.id == latest_id), None)
            if latest_ms:
                latest_milestone = {
                    'milestone': latest_ms,
                    'earned_at': earned_dates[latest_id],
                }

        return {
            'category_stats': category_stats,
            'total_milestones': total_milestones,
            'total_earned': total_earned,
            'overall_percentage': overall_pct,
            'titles_unlocked': titles_unlocked,
            'latest_milestone': latest_milestone,
        }

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        milestones = list(context['object_list'])

        user = self.request.user
        profile = user.profile if user.is_authenticated and hasattr(user, 'profile') else None

        # Get current category tab
        current_cat = self.request.GET.get('cat', 'overview')
        if current_cat not in MILESTONE_CATEGORIES:
            current_cat = 'overview'

        # Fetch user data once
        earned_ids, progress_dict, earned_dates = self._get_user_data(profile, milestones)

        # Lightweight overall summary, always available (the page header shows it on every
        # tab). Excludes manual "Feats of Strength" from the denominator, matching the
        # overview totals. Bounded (~50 milestones), no extra query.
        countable = [m for m in milestones if m.criteria_type != 'manual']
        summary_total = len(countable)
        summary_earned = sum(1 for m in countable if m.id in earned_ids) if profile else 0
        context['ms_summary'] = {
            'total': summary_total,
            'earned': summary_earned,
            'pct': round(summary_earned / summary_total * 100) if summary_total else 0,
        }

        # Build tab badge counts for all categories
        tab_data = []
        for slug, config in MILESTONE_CATEGORIES.items():
            if slug == 'overview':
                tab_data.append({'slug': slug, 'name': config['name'], 'icon': config['icon']})
                continue
            types = config['criteria_types']
            cat_ms = [m for m in milestones if m.criteria_type in types]
            # Special/manual milestones are only visible when earned,
            # so total should reflect only earned ones for accurate tab counts
            if slug == 'special':
                cat_ms = [m for m in cat_ms if m.id in earned_ids] if profile else []
            total = len(cat_ms)
            earned = sum(1 for m in cat_ms if m.id in earned_ids) if profile else 0
            tab_data.append({
                'slug': slug,
                'name': config['name'],
                'icon': config['icon'],
                'total': total,
                'earned': earned,
                'is_feats_of_strength': (slug == 'special'),
            })

        context['current_cat'] = current_cat
        context['tab_data'] = tab_data

        if current_cat == 'overview':
            context['overview_data'] = self._build_overview_data(
                milestones, profile, earned_ids, earned_dates
            )
        else:
            category_config = MILESTONE_CATEGORIES[current_cat]
            context['category_name'] = category_config['name']
            context['category_data'] = self._build_category_data(
                milestones, category_config, profile,
                earned_ids, progress_dict, earned_dates
            )

        context['breadcrumb'] = [
            {'text': 'Home', 'url': reverse_lazy('home')},
            {'text': 'My Pursuit', 'url': reverse_lazy('my_pursuit_hub')},
            {'text': 'Milestones'},
        ]

        track_page_view('milestones_list', 'list', self.request)
        return context
