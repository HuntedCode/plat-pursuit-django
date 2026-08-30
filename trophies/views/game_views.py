import logging
import math

from core.services import completion_card_service as cards
from core.services.tracking import track_site_event
from core.services.site_heartbeat import get_cached_heartbeat
from datetime import datetime, timedelta
from django.core.cache import cache
from django.contrib import messages
from django.db.models import Q, F, Exists, OuterRef, Value, IntegerField, FloatField, BooleanField, Avg, Count
from django.db.models.functions import Coalesce, Lower, Cast
from django.http import HttpResponseRedirect
from django.shortcuts import render
from django.urls import reverse_lazy, reverse
from django.utils import timezone
from django.utils.decorators import method_decorator
from django.views.decorators.csrf import ensure_csrf_cookie
from django.views import View
from django.views.generic import ListView, DetailView
from urllib.parse import urlencode
from trophies.mixins import HtmxListMixin
from trophies.views.concept_context import ConceptContextMixin
from ..models import Game, Trophy, Profile, EarnedTrophy, ProfileGame, ProfileTrophyGroup, TrophyGroup, Concept, FeaturedGuide, Stage, UserConceptRating
from ..forms import GameSearchForm, GameDetailForm, GuideSearchForm
from trophies.util_modules.constants import MODERN_PLATFORMS, ALL_PLATFORMS
from .browse_helpers import (
    annotate_ascii_name, annotate_community_ratings,
    apply_game_browse_filters, apply_game_browse_sort, get_active_filter_chips,
)

logger = logging.getLogger("psn_api")


def build_game_card_context(page_games, request, condensed=False):
    """Batched, whale-safe context for a page of `.pp-gcard` game cards.

    Builds everything the shared `game_list/game_cards.html` card consumes for a
    single page of `Game` objects: the viewer's per-game progress, DLC-pack
    counts, community ratings, and the pursuer hooks (badge SERIES + home
    CONTRACT). Every map is keyed off the page's <=30 games/concepts in a handful
    of bounded queries -- never per-card -- so it stays safe for whale accounts.

    Returns a dict to `context.update(...)` into any ListView that renders the
    shared card (Browse Games, Recently Added). `show_game_hooks` is always set,
    so the card's pursuer band renders wherever this helper is used; browse pages
    that don't call it leave the band off (the card gates on the flag).

    `condensed=True` (the elected one-card-per-page-identity grids: Browse Games,
    tag detail -- NEVER Recently Added, whose per-list rows are its point): adds
    `list_count_map` + `platform_union_map` (elected-game-id keyed) and sets
    `condensed_cards` so the card links the Game page and shows the union. The
    sibling grouping is the DESTINATION PAGE's membership rule -- trust-UNGATED
    igdb grouping, same-concept for unmatched, GamePageView's np floor -- which
    deliberately diverges from the trust-GATED election partition, so the card's
    "N lists" always agrees with the switcher the click lands on. Viewer progress
    is rolled up partition-BEST over the same siblings (still one query), so a
    PS4-progress hunter sees their fill on the PS5-elected card
    (game_grouping_service precedent). Net cost: +1 bounded query.
    """
    from collections import defaultdict
    from trophies.constants import badge_attribution_rank
    from trophies.models import BadgeSeries
    from trophies.services.contract_service import contract_by_concept_map
    from trophies.util_modules.constants import CONTRACT_XP_TOTAL, ordered_platform_union

    ctx = {'show_game_hooks': True}
    game_ids = [g.id for g in page_games]

    sibling_ids_by_elected = {}
    if condensed:
        ctx['condensed_cards'] = True
        # Destination-page grouping: elected rows whose concept holds a TRUSTED match with an
        # igdb id group by that id (membership itself ungated, mirroring GamePageView._resolve);
        # concept-bearing rows without one group by concept (the c/ page); conceptless rows
        # stand alone and keep their list-detail link.
        igdb_by_elected, concept_by_elected = {}, {}
        for g in page_games:
            match = getattr(g.concept, 'igdb_match', None) if g.concept_id else None
            if match is not None and match.is_trusted and match.igdb_id is not None:
                igdb_by_elected[g.id] = match.igdb_id
            elif g.concept_id:
                concept_by_elected[g.id] = g.concept_id
        sibling_filter = Q()
        if igdb_by_elected:
            sibling_filter |= Q(concept__igdb_match__igdb_id__in=set(igdb_by_elected.values()))
        if concept_by_elected:
            sibling_filter |= Q(concept_id__in=set(concept_by_elected.values()))
        rows = []
        if sibling_filter:
            # GamePageView's np floor: a blank/null np row is not a linkable list there either.
            rows = list(
                Game.objects
                .filter(sibling_filter, np_communication_id__isnull=False)
                .exclude(np_communication_id='')
                .values_list('id', 'concept_id', 'concept__igdb_match__igdb_id',
                             'concept__igdb_match__status', 'title_platform')
            )
        by_igdb, by_concept = defaultdict(list), defaultdict(list)
        for row in rows:
            if row[2] is not None:
                by_igdb[row[2]].append(row)
            if row[1] is not None:
                by_concept[row[1]].append(row)
        list_count_map, platform_union_map = {}, {}
        for eid in game_ids:
            if eid in igdb_by_elected:
                members = by_igdb.get(igdb_by_elected[eid], [])
            elif eid in concept_by_elected:
                # The c/ page's set: same-concept rows only. (An untrusted match on the concept
                # does NOT widen membership -- GamePageView's concept route filters by concept.)
                members = by_concept.get(concept_by_elected[eid], [])
            else:
                continue
            list_count_map[eid] = len(members)
            platform_union_map[eid] = ordered_platform_union(m[4] for m in members)
            sibling_ids_by_elected[eid] = [m[0] for m in members]
        ctx['list_count_map'] = list_count_map
        ctx['platform_union_map'] = platform_union_map

    # User-specific game data (1 query): progress + plat state for the card's bottom-edge fill.
    # Condensed grids query over ALL partition siblings (same single query, wider id set) and
    # roll up partition-BEST per elected id in the SAME value shape, so the card's five-state
    # fill logic never knows the difference.
    if request.user.is_authenticated and hasattr(request.user, 'profile'):
        lookup_ids = game_ids
        if condensed and sibling_ids_by_elected:
            lookup_ids = list({sid for sids in sibling_ids_by_elected.values() for sid in sids}
                              | set(game_ids))
        user_games = ProfileGame.objects.filter(
            profile=request.user.profile,
            game_id__in=lookup_ids,
        ).values('game_id', 'progress', 'has_plat', 'earned_trophies_count')
        user_map = {pg['game_id']: pg for pg in user_games}
        if condensed:
            for eid, sids in sibling_ids_by_elected.items():
                # Seed with the elected row's own entry even if a population quirk ever leaves
                # eid out of its sibling set (final-audit #2) -- the fold must never DELETE the
                # viewer's real progress.
                candidates = [user_map.get(sid) for sid in set(sids) | {eid}]
                best = max((pg for pg in candidates if pg),
                           key=lambda pg: (pg['progress'] or 0), default=None)
                if best is None:
                    user_map.pop(eid, None)
                    continue
                user_map[eid] = {
                    'game_id': eid,
                    'progress': best['progress'],
                    'has_plat': any((user_map.get(sid) or {}).get('has_plat')
                                    for sid in set(sids) | {eid}),
                    'earned_trophies_count': best['earned_trophies_count'],
                }
        ctx['user_game_map'] = user_map

    # DLC pack count per game (1 grouped query): trophy groups beyond the base 'default' group.
    dlc_counts = (
        TrophyGroup.objects.filter(game_id__in=game_ids)
        .exclude(trophy_group_id='default')
        .values('game_id').annotate(n=Count('id'))
    )
    ctx['dlc_map'] = {d['game_id']: d['n'] for d in dlc_counts}

    concept_ids = [g.concept_id for g in page_games if g.concept_id]
    if not concept_ids:
        return ctx

    # Community ratings (1 query): base-game overall average for the card's star fact.
    ratings = UserConceptRating.objects.filter(
        concept_id__in=concept_ids,
        concept_trophy_group__isnull=True,
    ).values('concept_id').annotate(
        avg_difficulty=Avg('difficulty'),
        avg_fun=Avg('fun_ranking'),
        avg_rating=Avg('overall_rating'),
        rating_count=Count('id'),
    )
    ctx['rating_map'] = {r['concept_id']: r for r in ratings}

    # ── Pursuer hooks: the badge SERIES a game belongs to + its home CONTRACT. Both concept-keyed and
    #    batched over the page's <=30 concepts (whale-safe -- 3 bounded queries, never per-card). ──
    badge_cap = 3
    concept_id_set = set(concept_ids)

    # concept -> distinct badge series_slugs it appears in (1 query over the M2M).
    concept_series = defaultdict(set)
    for cid, slug in (
        Stage.objects.filter(concepts__in=concept_ids)
        .exclude(series_slug__isnull=True).exclude(series_slug='')
        # .order_by() strips Stage.Meta.ordering (stage_number), which would otherwise ride the
        # SELECT + defeat .distinct() (a concept in two same-series stages -> duplicate rows).
        .values_list('concepts', 'series_slug').order_by().distinct()
    ):
        if cid in concept_id_set:
            concept_series[cid].add(slug)

    # series_slug -> {label, attribution ids} for each SERIES that ships a live group badge (1 query).
    # The three FK ids ride along so the sort below can rank by attribution without materialising the
    # related Franchise/Company rows -- they are already columns on the row being read.
    all_slugs = {s for slugs in concept_series.values() for s in slugs}
    series_badge = {}
    if all_slugs:
        series_badge = {
            b['series_slug']: {**b, 'label': b['display_series'] or b['name']}
            for b in BadgeSeries.objects.filter(series_slug__in=all_slugs, group_badges__is_live=True)
            .values('series_slug', 'name', 'display_series', 'badge_type',
                    'collection_id', 'franchise_id', 'developer_id').distinct()
        }

    badge_map = {}
    for cid, slugs in concept_series.items():
        items = [series_badge[s] for s in slugs if s in series_badge]
        if not items:
            continue
        items.sort(key=lambda b: (
            badge_attribution_rank(b['collection_id'], b['franchise_id'], b['developer_id']),
            b['label'].lower(),
        ))
        badge_map[cid] = {
            'total': len(items),
            'names': [b['label'] for b in items[:badge_cap]],
            'more': max(0, len(items) - badge_cap),
        }
    ctx['badge_map'] = badge_map

    # concept -> home contract (live only) + its jobs (1 query + jobs prefetch).
    contract_map = {}
    for concept_id, ct in contract_by_concept_map(concept_ids, live_only=True).items():
        jobs = list(ct.jobs.all())
        discs = []
        for j in jobs:
            if j.discipline and j.discipline not in discs:
                discs.append(j.discipline)
        if len(discs) >= 2:
            stops = ', '.join(
                f'color-mix(in oklab, var(--disc-{d}) 18%, var(--pp-bg-2))' for d in discs
            )
            band_bg = f'linear-gradient(120deg, {stops})'
        elif discs:
            band_bg = f'color-mix(in oklab, var(--disc-{discs[0]}) 15%, var(--pp-bg-2))'
        else:
            band_bg = ''
        contract_map[concept_id] = {
            'name': ct.name,
            'slug': ct.slug,
            'xp': ct.xp_total_override or CONTRACT_XP_TOTAL,
            'jobs': [{'name': j.name, 'icon': j.icon, 'discipline': j.discipline} for j in jobs[:6]],
            'band_bg': band_bg,
            'accent': f'var(--disc-{discs[0]})' if discs else 'var(--pp-secondary)',
        }
    ctx['contract_map'] = contract_map

    return ctx


class GamesListView(HtmxListMixin, ListView):
    """
    Display paginated list of games with filtering and sorting options.

    Provides comprehensive game browsing functionality with filters for:
    - Platform (PS4, PS5, PS Vita, etc.)
    - Region (NA, EU, JP, global)
    - Alphabetical letter, platinum availability, shovelware exclusion
    - Community flags (delisted, unobtainable, online trophies, buggy trophies)
    - Community ratings (min rating, max difficulty, min fun)
    - Time-to-beat ranges (IGDB estimate and community reported)
    - Genre, Theme, and Game Engine (normalized IGDB data)

    Defaults to modern platforms (PS4/PS5) and user's preferred region if authenticated.
    """
    model = Game
    template_name = 'trophies/game_list.html'
    partial_template_name = 'trophies/partials/game_list/browse_results.html'
    paginate_by = 30

    def get_filter_form(self):
        if not hasattr(self, '_filter_form'):
            data = self.request.GET
            if not data:
                # A bare hit renders the default view IN PLACE (SEO Lane 1): the old force-302
                # to ?platform=... meant the hub's canonical URL never returned a page, so the
                # site's largest hub had no indexable front door. The form binds the same
                # defaults the redirect used to carry; the template then history.replaceState()s
                # the params into the URL, so the scroller, pagination and filter form all
                # carry them exactly as the redirected flow did (the audit's find: without
                # that, page 2 silently dropped the platform filter and duplicated cards).
                data = {'platform': MODERN_PLATFORMS}
                self._applied_defaults = True
            self._filter_form = GameSearchForm(data)
        return self._filter_form

    def dispatch(self, request, *args, **kwargs):
        # Personalization keeps its redirect: a signed-in hunter with saved browse defaults
        # lands on them explicitly (the URL should SHOW their filter state). Crawlers are
        # anonymous, so the hub itself stays a 200.
        if not request.GET and request.user.is_authenticated:
            defaults = (request.user.browse_defaults or {}).get('games', {})
            if defaults:
                return HttpResponseRedirect(
                    reverse('games_list') + '?' + urlencode(defaults, doseq=True)
                )
        return super().dispatch(request, *args, **kwargs)

    def get_queryset(self):
        qs = super().get_queryset()
        form = self.get_filter_form()

        if form.is_valid():
            sort_val = form.cleaned_data.get('sort', '')
            qs, annotations = apply_game_browse_filters(qs, form, sort_val)
            qs, order = apply_game_browse_sort(qs, sort_val, annotations)
        else:
            qs = annotate_ascii_name(qs)
            order = ['is_ascii_name', Lower('title_name')]

        # ONE CARD PER PAGE IDENTITY (Games/Trophy Lists IA phase 3): the sitemap's window
        # election dedupes regional/platform siblings AND deliberately-split concepts sharing a
        # trusted igdb id onto the card whose page they all resolve to. ORDER MATTERS and is
        # load-bearing: a .filter() chained AFTER the window's _election_rank=1 lands INSIDE the
        # subquery and silently narrows the election POPULATION instead of filtering elected rows
        # (verified SQL) -- so every filter above, including apply_game_browse_sort's own
        # narrowing filters (rating-null etc.), runs BEFORE the election, and only ordering /
        # select_related / defer come after (the verified-safe shapes). Filtering first is also
        # the right SEMANTICS: ?platform=PS3 removes the PS5 sibling from the population, so the
        # PS3 row wins its partition and the card shows the version you asked for -- the same
        # promotion rule the shovelware election test pins.
        #
        # The np floor BEFORE the election (final-audit #1): every destination this grid links
        # applies it (GamePageView, the sitemaps, the sibling query), so a blank/null-np row
        # winning its partition would mint a card whose click 404s.
        qs = (
            qs.filter(np_communication_id__isnull=False)
            .exclude(np_communication_id='')
            .game_page_canonicals()
        )

        qs = qs.select_related(
            'concept', 'concept__igdb_match',
        ).defer(
            # IGDBMatch.raw_response is the full IGDB API blob (~30 KB per row).
            # Browse-style listings paginate many games per page; without this
            # defer each page would pull hundreds of KB of raw_response payload
            # that is never used by the card render. See CLAUDE.md "IGDB cover-art
            # querysets" and the May 2026 OOM postmortem.
            'concept__igdb_match__raw_response',
        )
        return qs.order_by(*order)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['breadcrumb'] = [
            {'text': 'Home', 'url': reverse_lazy('home')},
            {'text': 'Games'},
        ]

        form = self.get_filter_form()
        context['form'] = form
        context.update(get_active_filter_chips(self.request, form))   # dismissable active-filter chips
        # From the FORM, not request.GET: on a bare-defaulted hit the GET is empty while the
        # queryset IS filtered, and unchecked boxes would submit no platform on first touch.
        form = self.get_filter_form()
        context['selected_platforms'] = (
            form.cleaned_data.get('platform', []) if form.is_valid()
            else self.request.GET.getlist('platform')
        )
        if getattr(self, '_applied_defaults', False):
            context['applied_default_query'] = urlencode({'platform': MODERN_PLATFORMS}, doseq=True)
        # ItemList schema rows for this page (SEO Lane 2) -- bounded by paginate_by. Condensed
        # cards link the concept GAME page, so the ItemList must claim the same URLs the grid
        # renders; the conceptless floor keeps its list URL (no Game page exists).
        context['seo_item_list'] = [
            {'name': (g.concept.unified_title or g.title_name) if g.concept_id else g.title_name,
             'url': g.concept.game_page_url() if g.concept_id
             else reverse('game_detail', kwargs={'np_communication_id': g.np_communication_id})}
            for g in context.get('page_obj').object_list if g.np_communication_id
        ] if context.get('page_obj') else []
        context['selected_regions'] = self.request.GET.getlist('regions')
        context['view_type'] = self.request.GET.get('view', 'grid')
        context['show_only_platinum'] = self.request.GET.get('show_only_platinum', '')
        context['filter_shovelware'] = self.request.GET.get('filter_shovelware', '')

        # New filter state
        context['show_delisted'] = self.request.GET.get('show_delisted', '')
        context['show_unobtainable'] = self.request.GET.get('show_unobtainable', '')
        context['show_online'] = self.request.GET.get('show_online', '')
        context['show_buggy'] = self.request.GET.get('show_buggy', '')
        context['selected_genres'] = self.request.GET.getlist('genres')
        context['selected_themes'] = self.request.GET.getlist('themes')
        context['selected_contract_jobs'] = self.request.GET.getlist('contract_jobs')
        context['in_contract'] = self.request.GET.get('in_contract', '')

        # Discipline -> jobs roster for the contract filter drill-down. Full page only (the advanced panel
        # isn't re-rendered on the HTMX filter swap / infinite-scroll XHR), so it's 1 bounded query (~24
        # jobs) per page load, not per swap.
        is_xhr = self.request.headers.get('X-Requested-With') == 'XMLHttpRequest'
        if not self.request.htmx and not is_xhr:
            from collections import defaultdict as _dd
            from trophies.models import Job
            _jobs_by_disc = _dd(list)
            for _j in Job.objects.exclude(is_fallback=True).order_by('discipline', 'display_order', 'name'):
                _jobs_by_disc[_j.discipline].append(_j)
            context['contract_disciplines'] = [
                {'slug': slug, 'label': label, 'jobs': _jobs_by_disc.get(slug, [])}
                for slug, label in Job.DISCIPLINES
            ]

            # Header discovery stats from the hourly-cached site heartbeat (zero DB cost on the request path):
            # catalogue scale + how many games are part of a badge series / a contract.
            from core.services.site_heartbeat import get_cached_heartbeat
            _hb = get_cached_heartbeat() or {}
            _always = _hb.get('always') or {}
            _expanded = _hb.get('expanded') or {}
            context['catalog_games_total'] = (_always.get('games_total') or {}).get('value')
            context['catalog_games_new_this_week'] = (_always.get('games_total') or {}).get('delta')
            context['catalog_games_in_badges'] = (_expanded.get('games_in_badges') or {}).get('value')
            context['catalog_games_in_contracts'] = (_expanded.get('games_in_contracts') or {}).get('value')

        # Check if any filters are active (for badge + auto-expanding the drawer)
        context['has_advanced_filters'] = any(
            v for k, v in self.request.GET.lists()
            if k not in ('page', 'view') and any(v)
        )

        context['seo_description'] = (
            "Browse PlayStation games on Platinum Pursuit. "
            "Search by name, filter by platform, and track your trophy progress."
        )

        # Post-pagination card data (progress / DLC counts / ratings / pursuer hooks) for the <=30 games on
        # this page. Shared, batched, whale-safe -- see build_game_card_context (also used by Recently Added).
        context.update(build_game_card_context(context['object_list'], self.request, condensed=True))

        return context


class TrophyListsBrowseView(HtmxListMixin, ListView):
    """Trophy Lists browse (/games/lists/) -- the LAST canonical page of the Games/Trophy Lists
    IA: the LIST-level catalogue. One card per trophy list, deliberately UN-condensed -- this is
    the browse home for exactly what Browse Games' election dedupes away (regional variants,
    platform stacks, editions).

    The sibling-browse shape is TagDetailBaseView's, not GamesListView's: a plain GameSearchForm
    (no defaults injection) and NO browse_defaults dispatch redirect -- the bare URL must return
    200 without a hop, because the page is static-sitemap-advertised (test_seo_closing's
    contract). The pipeline is the shared filter -> sort chain, SKIPPING game_page_canonicals()
    (no election) and applying the destination np floor directly: every card links game_detail,
    and reverse() happily builds /games// from a blank np (the ListSitemap note).
    """

    model = Game
    template_name = 'trophies/trophy_lists.html'
    partial_template_name = 'trophies/partials/trophy_lists/browse_results.html'
    paginate_by = 30

    def get_filter_form(self):
        if not hasattr(self, '_filter_form'):
            self._filter_form = GameSearchForm(self.request.GET)
        return self._filter_form

    def get_queryset(self):
        qs = Game.objects.all()
        form = self.get_filter_form()

        if form.is_valid():
            sort_val = form.cleaned_data.get('sort', '')
            qs, annotations = apply_game_browse_filters(qs, form, sort_val)
            qs, order = apply_game_browse_sort(qs, sort_val, annotations)
        else:
            qs = annotate_ascii_name(qs)
            order = ['is_ascii_name', Lower('title_name')]

        # The np floor WITHOUT the election: per-list is this page's point, but an un-linkable
        # row is still not a card (same floor as GamePageView / the sitemaps).
        qs = qs.filter(np_communication_id__isnull=False).exclude(np_communication_id='')

        qs = qs.select_related('concept', 'concept__igdb_match').defer(
            # The ~30 KB IGDB blob, never read by the card (house OOM rule).
            'concept__igdb_match__raw_response',
        )
        # 'pk' tiebreaker: this page is the ONE grid where title ties are the NORM (sibling
        # stacks share a cleaned title_name), and without a unique key Postgres may reorder a
        # tie block between the per-page LIMIT/OFFSET queries the InfiniteScroller issues --
        # duplicating or dropping cards across a page boundary.
        return qs.order_by(*order, 'pk')

    @staticmethod
    def _build_header_stats():
        """The four list-level header scards: catalogue scale, the regional slice (this page's
        point), platinum coverage, fresh-sync momentum. All DB aggregates over the same np
        floor the grid renders with."""
        base = Game.objects.filter(np_communication_id__isnull=False).exclude(np_communication_id='')
        return {
            'total': base.count(),
            'regional': base.filter(is_regional=True).count(),
            'with_plat': base.filter(Exists(
                Trophy.objects.filter(game=OuterRef('pk'), trophy_type='platinum'))).count(),
            'new_this_week': base.filter(
                created_at__gte=timezone.now() - timedelta(days=7)).count(),
        }

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['breadcrumb'] = [
            {'text': 'Home', 'url': reverse_lazy('home')},
            {'text': 'Games', 'url': reverse_lazy('games_list')},
            {'text': 'Trophy Lists'},
        ]

        form = self.get_filter_form()
        context['form'] = form
        context.update(get_active_filter_chips(self.request, form))
        context['selected_platforms'] = self.request.GET.getlist('platform')
        context['selected_regions'] = self.request.GET.getlist('regions')
        context['show_only_platinum'] = self.request.GET.get('show_only_platinum', '')
        context['filter_shovelware'] = self.request.GET.get('filter_shovelware', '')
        context['selected_genres'] = self.request.GET.getlist('genres')
        context['selected_themes'] = self.request.GET.getlist('themes')
        context['has_advanced_filters'] = any(
            v for k, v in self.request.GET.lists()
            if k not in ('page', 'view') and any(v)
        )

        context['seo_description'] = (
            "Browse every PlayStation trophy list on Platinum Pursuit -- regional variants, "
            "platform stacks, and edition lists. Filter by platform, region, and platinum "
            "availability."
        )

        # Header substance scards (the browse-family header standard), full page only -- the
        # panel/grid swaps never re-render the header. Whole-catalogue COUNTs, so they are
        # cached for an hour (the game_list header reads the site heartbeat for the same
        # reason); a cold cache pays four bounded aggregate queries, never per-row work.
        is_xhr = self.request.headers.get('X-Requested-With') == 'XMLHttpRequest'
        if not self.request.htmx and not is_xhr:
            context['tlb_stats'] = cache.get_or_set(
                'trophy_lists:header_stats', self._build_header_stats, 3600)

        # LIST-IDENTITY cards (the page's third card mode): titles come from the observed PSN
        # list names -- Game.display_list_names, the batch that is the ONLY supported grid read
        # (one indexed query for the page's <=30 rows; a per-card property would be the N+1 the
        # batch exists to prevent). Runs for full AND partial/XHR renders alike.
        context['list_identity_cards'] = True
        context['list_name_map'] = Game.display_list_names(context['object_list'])

        # ItemList rows are per-LIST game_detail URLs -- exactly the pages these cards link, and
        # every list page is self-canonical (the slim-down). Names read the SAME dict the grid
        # shows (no second observation query; the schema must claim what renders).
        context['seo_item_list'] = [
            {'name': context['list_name_map'].get(g.np_communication_id) or g.title_name,
             'url': reverse('game_detail', kwargs={'np_communication_id': g.np_communication_id})}
            for g in context.get('page_obj').object_list
        ] if context.get('page_obj') else []

        # UNCONDENSED on purpose (the anti-Browse-Games): one card per list, list links, the
        # game/concept-keyed maps all behave per-list (the Recently Added precedent).
        context.update(build_game_card_context(context['object_list'], self.request))

        return context


class RandomGameView(View):
    """Redirect to a random game detail page, respecting active browse filters.

    Also honors page-level scope that lives outside the standard filter form:
    the Lucky button on tag-detail pages (genre/theme) carries `genres`/`themes`
    ids forward via its `data-lucky-extra` attribute (see browse-filters.js).
    """

    def get(self, request):
        form = GameSearchForm(request.GET)
        qs = Game.objects.all()

        if form.is_valid():
            qs, _ = apply_game_browse_filters(qs, form)

        # No election needed for Lucky: any sibling resolves to the same Game page. NOTE the
        # distribution is LIST-weighted (a 6-stack work is 6x likelier than a single-list one)
        # -- accepted: popularity correlates with stacks, and an election here would buy
        # uniformity at window-query cost. The np floor keeps the redirect target renderable.
        random_game = (
            qs.filter(np_communication_id__isnull=False)
            .exclude(np_communication_id='')
            .select_related('concept', 'concept__igdb_match')
            .defer('concept__igdb_match__raw_response')
            .order_by('?').first()
        )

        if random_game:
            if random_game.concept_id:
                return HttpResponseRedirect(random_game.concept.game_page_url())
            return HttpResponseRedirect(
                reverse('game_detail', args=[random_game.np_communication_id])
            )

        messages.info(request, "No games match your current filters. Try broadening your search!")
        referer = request.META.get('HTTP_REFERER')
        if referer:
            return HttpResponseRedirect(referer)
        referer_params = request.GET.urlencode()
        return HttpResponseRedirect(
            reverse('games_list') + ('?' + referer_params if referer_params else '')
        )


# ─── List-level trophy helpers, shared with the concept Game page ─────────────────────────────────
# Module functions rather than GameDetailView methods so GamePageView's list viewport renders the
# SAME rows/groups/grouping without inheriting a DetailView. The view methods above/below delegate
# here; their unit tests keep calling the methods, which is what pins the delegation.

def build_trophy_rows(game):
    """The ORM-decoupled trophy dicts for one list -- the shared grid's row contract.
    Returns (rows, has_trophies)."""
    has_trophies = Trophy.objects.filter(game=game).exists()
    if not has_trophies:
        return [], False
    try:
        trophies_qs = Trophy.objects.filter(game=game).order_by('trophy_id')
        rows = [
            {
                'trophy_id': t.trophy_id,
                'trophy_type': t.trophy_type,
                'trophy_name': t.trophy_name,
                'trophy_detail': t.trophy_detail,
                'trophy_icon_url': t.trophy_icon_url,
                'trophy_group_id': t.trophy_group_id,
                'progress_target_value': t.progress_target_value,
                'trophy_rarity': t.trophy_rarity,
                'trophy_earn_rate': t.trophy_earn_rate,
                'earned_count': t.earned_count,
                'earn_rate': t.earn_rate,
                'pp_rarity': t.get_pp_rarity_tier()
            } for t in trophies_qs
        ]
    except Exception:
        logger.exception(f"Game trophies query failed for {game.np_communication_id}")
        rows = []
    return rows, has_trophies


def build_trophy_groups(game):
    """Group metadata keyed by trophy_group_id -- the shared grid's `groups` contract param."""
    return {
        g.trophy_group_id: {
            'trophy_group_name': g.trophy_group_name,
            'trophy_group_icon_url': g.trophy_group_icon_url,
            'defined_trophies': g.defined_trophies,
        } for g in TrophyGroup.objects.filter(game=game)
    }


def compute_group_pct(trophy_groups, group_totals):
    """Per-group completion % keyed by group_id, for the grid's group headers. Same earned/total
    math as _build_group_bars, reading the already-aggregated totals + defined counts."""
    pct = {}
    for gid, group in trophy_groups.items():
        total = sum(int(v or 0) for v in (group.get('defined_trophies') or {}).values())
        earned = sum((group_totals.get(gid) or {}).values())
        pct[gid] = round(earned / total * 100) if total else 0
    return pct


def group_trophy_rows(rows):
    """Bucket rows by group, default first then alphabetically -- the grid's `trophies` param."""
    grouped = {}
    for trophy in rows:
        grouped.setdefault(trophy.get('trophy_group_id', 'default'), []).append(trophy)
    return {gid: grouped[gid] for gid in sorted(grouped, key=lambda x: (x != 'default', x))}


def build_earned_state(game, profile):
    """The viewer's earned-state for ONE list: the per-trophy map plus the DB-side totals.

    Extracted so the concept Game page's list viewport can price a list switch at exactly this
    (per docs/design/games-and-trophy-lists-ia.md) without dragging in timeline/plat-card/
    play-hours. Returns (context_dict, ordered_earned_qs, earned_count) -- the trailing pair
    exists solely for _build_timeline_events, which shares the ordered queryset.

    `select_related('trophy')` is load-bearing, not an optimization nicety: the dict build
    reads e.trophy.trophy_id and the timeline reads e.trophy.trophy_type per row, which was one
    query PER EARNED TROPHY (~200 extra queries per authenticated render of a 100-trophy game).
    A regression here multiplies by every list a switcher fetches.

    Whale-safety shape is inherited unchanged: bounded by ONE list's trophy count, totals
    aggregated in the DB (never iterate the per-user earned queryset in Python -- a 250K-trophy
    profile would materialize hundreds of MB), the `.order_by()` strip keeping the earned-date
    sort out of the GROUP BY.
    """
    earned_qs = (
        EarnedTrophy.objects
        .filter(profile=profile, trophy__game=game)
        .select_related('trophy')
        .order_by('trophy__trophy_id')
    )
    state = {
        'profile_earned': {
            e.trophy.trophy_id: {
                'earned': e.earned,
                'progress': e.progress,
                'progress_rate': e.progress_rate,
                'progressed_date_time': e.progressed_date_time,
                'earned_date_time': e.earned_date_time
            } for e in earned_qs
        },
    }

    ordered_earned_qs = earned_qs.filter(earned=True).order_by(F('earned_date_time').asc(nulls_last=True))
    state['profile_trophy_totals'] = ordered_earned_qs.aggregate(
        bronze=Count('id', filter=Q(trophy__trophy_type='bronze')),
        silver=Count('id', filter=Q(trophy__trophy_type='silver')),
        gold=Count('id', filter=Q(trophy__trophy_type='gold')),
        platinum=Count('id', filter=Q(trophy__trophy_type='platinum')),
    )

    profile_group_totals = {}
    group_rows = (
        ordered_earned_qs.order_by()
        .values('trophy__trophy_group_id', 'trophy__trophy_type')
        .annotate(c=Count('id'))
    )
    for row in group_rows:
        group_id = row['trophy__trophy_group_id'] or 'default'
        bucket = profile_group_totals.setdefault(
            group_id, {'bronze': 0, 'silver': 0, 'gold': 0, 'platinum': 0}
        )
        bucket[row['trophy__trophy_type']] = row['c']
    state['profile_group_totals'] = profile_group_totals

    return state, ordered_earned_qs, len(earned_qs)



@method_decorator(ensure_csrf_cookie, name='dispatch')
class GameDetailView(ConceptContextMixin, DetailView):
    """
    Display detailed game information including trophies, statistics, and user progress.

    Shows trophy list with optional filtering/sorting, game statistics (players, completions),
    milestone progress for linked profiles, and community ratings if applicable.
    """
    model = Game
    template_name = 'trophies/game_detail.html'
    slug_field = 'np_communication_id'
    slug_url_kwarg = 'np_communication_id'
    context_object_name = 'game'

    def dispatch(self, request, *args, **kwargs):
        # Profile-scoped variants (/games/<np>/<username>/) require auth. They
        # are the most expensive class of rendered page on the site and have
        # been the primary driver of container-level OOM crashes from bot
        # fan-out. Anonymous visitors are redirected to the canonical game
        # page with a from_profile hint that drives a sign-up banner — cheap
        # to render, still useful to casual visitors, cuts scraper access.
        # The Ratings and About tabs left this page for the concept Game page (the slim-down,
        # owner decision 4), but the old tabs WROTE ?view= into the address bar on every switch,
        # so bookmarked/shared deep links to them exist in the wild. Send them where the content
        # went -- a 302, not 301: cheap to change if the Game page's view names ever shift. The
        # username segment and other params are dropped deliberately (ratings are concept-level,
        # not profile-level). Conceptless games have no Game page and fall through to Trophies.
        # ABOVE the anon-username redirect on purpose: anon + /<username>/ + ?view=ratings would
        # otherwise pay two hops (audit #7) -- both roads end at the Game page anyway.
        view_param = request.GET.get('view')
        if view_param in ('ratings', 'about'):
            game = (
                Game.objects.filter(np_communication_id=kwargs['np_communication_id'])
                .select_related('concept__igdb_match')
                .only('np_communication_id', 'concept__concept_id',
                      'concept__igdb_match__igdb_id', 'concept__igdb_match__status')
                .first()
            )
            if game is not None and game.concept_id:
                return HttpResponseRedirect(f'{game.concept.game_page_url()}?view={view_param}')

        psn_username = kwargs.get('psn_username')
        if psn_username and not request.user.is_authenticated:
            canonical = reverse('game_detail', kwargs={'np_communication_id': kwargs['np_communication_id']})
            params = {'from_profile': psn_username}
            existing_qs = request.META.get('QUERY_STRING', '')
            suffix = f'&{existing_qs}' if existing_qs else ''
            return HttpResponseRedirect(f'{canonical}?{urlencode(params)}{suffix}')

        return super().dispatch(request, *args, **kwargs)

    def get_queryset(self):
        return super().get_queryset().select_related('concept', 'concept__igdb_match').defer(
            # The ~30KB IGDB API blob is never read here -- every field the hero renders
            # uses a parsed column. select_related drags it into the join for free
            # otherwise; deferring it keeps the per-render payload lean.
            'concept__igdb_match__raw_response',
        ).prefetch_related(
            # The HERO's concept facts only (dev/publisher, genre/theme tags). The About
            # panel's engines + franchises prefetches left with the About tab (the slim-down's
            # final audit caught them still riding every List-detail render); GamePageView
            # carries its own copies for the panel it now hosts.
            'concept__concept_companies__company',
            'concept__concept_genres__genre',
            'concept__concept_themes__theme',
        )

    def _get_target_profile(self):
        """
        Get the target profile from URL parameter or authenticated user.

        Returns:
            Profile: Target profile instance or None if not found/authenticated
        """
        psn_username = self.kwargs.get('psn_username')
        user = self.request.user

        if psn_username:
            try:
                return Profile.objects.get(psn_username__iexact=psn_username)
            except Profile.DoesNotExist:
                messages.error(self.request, "Profile not found.")
                return None
        elif user.is_authenticated and hasattr(user, 'profile') and user.profile and user.profile.is_linked:
            return user.profile
        return None

    def _build_profile_context(self, game, profile):
        """
        Build profile-specific context including progress, earned trophies, and milestones.

        Args:
            game: Game instance
            profile: Profile instance

        Returns:
            dict: Context dictionary with profile progress, trophy totals, earned status, and milestones
        """
        context = {
            'profile_progress': None,
            'profile_earned': {},
            'profile_trophy_totals': {},
            'profile_group_totals': {},
            'timeline_events': [
                self._make_timeline_event('Started Playing', 'started', False),
                self._make_timeline_event('First Trophy', 'trophy', False),
                self._make_timeline_event('25% Trophy', 'trophy', False),
                self._make_timeline_event('50% Trophy', 'trophy', False),
                self._make_timeline_event('75% Trophy', 'trophy', False),
                self._make_timeline_event('Platinum Trophy', 'trophy', False),
                self._make_timeline_event('100% Trophy', 'trophy', False),
            ]
        }

        has_trophies = Trophy.objects.filter(game=game).exists()

        try:
            profile_game = ProfileGame.objects.get(profile=profile, game=game)
            context['profile_progress'] = {
                'progress': profile_game.progress,
                'play_count': profile_game.play_count,
                'play_duration': profile_game.play_duration,
                'last_played': profile_game.last_played_date_time
            }

            # Plat card CTA -- shown only when the VIEWER has earned a card for this game.
            #
            # Gated on the profile being the viewer's own: this page also renders another hunter's
            # progress at /games/<np>/<username>/, and a card is personal. Linking someone else's
            # completion would send the viewer to their OWN shareables page with a group id it will
            # refuse, because the destination re-checks ownership with this same predicate.
            #
            # That predicate is `eligible_completions` -- the one the browse page and every card
            # endpoint use -- so this link can never offer a card they would deny, or hide one they
            # would show. One indexed read on the viewer's own default-group standings.
            #
            # It DEEP-LINKS to /shareables/?c=<group>, which opens the card there. Deliberately not a
            # modal on this page: the share flow is a whole surface (preview, theme picker, rating), and
            # a second copy of it here is exactly the drift the rebuild removed.
            # `self.request` via getattr: this method is also exercised directly in unit tests, where the
            # view has no request bound. No request means no viewer, which correctly yields no CTA.
            #
            # `is_linked` is part of the gate, not decoration: the IMPLICIT /games/<np>/ route already
            # requires it (_get_target_profile), but the explicit /games/<np>/<username>/ route does not,
            # and the destination hard-requires it (_RequireLinkedProfileMixin redirects to link_psn).
            # Without this an unlinked-but-claimed profile gets a button that bounces to the link flow.
            request = getattr(self, 'request', None)
            user = getattr(request, 'user', None)
            viewer = getattr(user, 'profile', None) if (user is not None and user.is_authenticated) else None
            if viewer and viewer.is_linked and profile.pk == viewer.pk:
                # NOT cards.eligible_completions(): that is built for the browse page and carries an OR'd
                # `game_id IN (profile's 100% ProfileGames)` subquery, four select_related joins and a JSON
                # cast. Postgres short-circuits the OR left-to-right, so on an UNFINISHED game -- the common
                # case on this page -- the left arm is false and the subplan runs, hashing every one of the
                # viewer's ProfileGames to answer "no". That is a whale-scaled cost on the site's
                # highest-traffic page, for a boolean.
                #
                # Same predicate, narrowly: this group's own progress, plus the whole-game percentage the
                # page already fetched above. `ProfileGame.progress == 100` implies every trophy including
                # DLC, so it implies the base list -- which is the staleness guard the OR arm exists for.
                # Two unique-index seeks, no subplan, no joins.
                row = (
                    ProfileTrophyGroup.objects
                    .filter(profile=profile, trophy_group__game=game,
                            trophy_group__trophy_group_id='default')
                    .values_list('trophy_group_id', 'progress')
                    .first()
                )
                if row and (row[1] == 100 or profile_game.progress == 100):
                    context['plat_card'] = {
                        'url': f"{reverse('my_shareables')}?c={row[0]}",
                        'variant': cards.resolve_variant(game),
                    }

            if has_trophies:
                earned_state, ordered_earned_qs, earned_count = self._build_earned_state(game, profile)
                context.update(earned_state)

                # Build milestones
                context['timeline_events'] = self._build_timeline_events(ordered_earned_qs, earned_count, context['profile_progress'], profile_game)

        except ProfileGame.DoesNotExist:
            pass

        return context

    def _build_earned_state(self, game, profile):
        """Delegates to the module-level build_earned_state (shared with GamePageView's viewport);
        kept as a method so the existing unit tests keep pinning the delegation."""
        return build_earned_state(game, profile)

    def _make_timeline_event(self, label, event_type, earned, date=None, trophy=None):
        """Create a uniform timeline event dict."""
        event = {
            'label': label,
            'event_type': event_type,
            'earned': earned,
            'date': date,
            'trophy_name': None,
            'trophy_id': None,
            'trophy_icon_url': None,
            'trophy_earn_rate': None,
            'trophy_rarity': None,
            'trophy_detail': None,
        }
        if trophy:
            event.update({
                'trophy_name': trophy.trophy_name,
                'trophy_id': trophy.trophy_id,
                'trophy_icon_url': trophy.trophy_icon_url,
                'trophy_earn_rate': trophy.trophy_earn_rate,
                'trophy_rarity': trophy.trophy_rarity,
                'trophy_detail': trophy.trophy_detail,
            })
        return event

    def _build_timeline_events(self, ordered_earned_qs, total_trophies, profile_progress, profile_game):
        """
        Build the player's journey timeline (started, first, 25/50/75%, platinum, 100%).

        Events are ordered by WHERE they land in the earn sequence (index into the
        date-sorted earned list), not a fixed slot. This matters because overall
        completion counts DLC: a base-game platinum can be earned well before the
        75%/100% overall milestones, so its position must float to when it actually
        happened. 'Started Playing' is pinned first; '100%' is pinned last.

        Args:
            ordered_earned_qs: QuerySet of earned trophies ordered by date
            total_trophies: Total number of trophies in game
            profile_progress: Profile progress dict with 'progress' key
            profile_game: ProfileGame instance for first_played_date_time

        Returns:
            list: List of timeline event dicts, ordered by earn sequence
        """
        earned_list = list(ordered_earned_qs)
        n = max(total_trophies, 1)
        events = []   # (order, event) pairs, sorted at the end

        # Started Playing -- pinned first.
        first_played = profile_game.first_played_date_time
        events.append((-1, self._make_timeline_event(
            'Started Playing', 'started', first_played is not None, date=first_played
        )))

        # First trophy.
        if earned_list:
            first = earned_list[0]
            events.append((0, self._make_timeline_event(
                'First Trophy', 'trophy', True, date=first.earned_date_time, trophy=first.trophy
            )))
        else:
            events.append((0, self._make_timeline_event('First Trophy', 'trophy', False)))

        # Count-based milestones: the trophy sitting at each fraction of the full set, by earn order.
        for label, frac in (('25% Trophy', 0.25), ('50% Trophy', 0.5), ('75% Trophy', 0.75)):
            idx = math.ceil((n - 1) * frac)
            if len(earned_list) > idx:
                t = earned_list[idx]
                events.append((idx, self._make_timeline_event(
                    label, 'trophy', True, date=t.earned_date_time, trophy=t.trophy
                )))
            else:
                events.append((idx, self._make_timeline_event(label, 'trophy', False)))

        # Platinum -- positioned dynamically by its index in the earn sequence, so a
        # base-game plat correctly precedes the 75%/100% milestones when DLC exists.
        plat_entry = next((e for e in reversed(earned_list) if e.trophy.trophy_type == 'platinum'), None)
        if plat_entry:
            plat_order = earned_list.index(plat_entry)
            events.append((plat_order, self._make_timeline_event(
                'Platinum Trophy', 'trophy', True, date=plat_entry.earned_date_time, trophy=plat_entry.trophy
            )))
        else:
            # Unearned: keep its default slot near the end, just before 100%.
            events.append((n - 0.5, self._make_timeline_event('Platinum Trophy', 'trophy', False)))

        # 100% -- pinned last (order beyond any real index).
        if profile_progress and profile_progress['progress'] == 100 and earned_list:
            complete = earned_list[-1]
            events.append((n + 1, self._make_timeline_event(
                '100% Trophy', 'trophy', True, date=complete.earned_date_time, trophy=complete.trophy
            )))
        else:
            events.append((n + 1, self._make_timeline_event('100% Trophy', 'trophy', False)))

        # Stable sort by sequence order; ties keep build order (Started, First, 25, 50, 75, Plat, 100).
        events.sort(key=lambda pair: pair[0])
        return [event for _, event in events]

    def _build_trophy_context(self, game, form, profile_earned):
        """
        Build trophy data with groups, filtering, and sorting.

        Args:
            game: Game instance
            form: GameDetailForm with filtering/sorting options
            profile_earned: Dict of earned trophy data by trophy_id

        Returns:
            tuple: (full_trophies list, trophy_groups dict, grouped_trophies dict, has_trophies bool)
        """
        full_trophies, has_trophies = build_trophy_rows(game)
        if not has_trophies:
            return [], {}, {}, False

        # Apply filtering and sorting
        if form.is_valid():
            earned_key = form.cleaned_data['earned']
            if profile_earned:
                if earned_key == 'unearned':
                    full_trophies = [t for t in full_trophies if not profile_earned.get(t['trophy_id'], {}).get('earned', False)]
                elif earned_key == 'earned':
                    full_trophies = [t for t in full_trophies if profile_earned.get(t['trophy_id'], {}).get('earned', False)]

            # Trophy type filter
            trophy_type_filter = form.cleaned_data.get('trophy_type')
            if trophy_type_filter:
                full_trophies = [t for t in full_trophies if t['trophy_type'] in trophy_type_filter]

            # Rarity bracket filter (PSN rarity tiers)
            rarity_filter = form.cleaned_data.get('rarity_bracket')
            if rarity_filter:
                def _matches_rarity(rate, brackets):
                    if rate <= 1:
                        return 'ultra_rare' in brackets
                    elif rate <= 5:
                        return 'very_rare' in brackets
                    elif rate <= 25:
                        return 'rare' in brackets
                    else:
                        return 'common' in brackets
                full_trophies = [t for t in full_trophies if _matches_rarity(t['trophy_earn_rate'], rarity_filter)]

            # DLC / Base game filter
            dlc_filter = form.cleaned_data.get('dlc_filter')
            if dlc_filter == 'base':
                full_trophies = [t for t in full_trophies if t['trophy_group_id'] == 'default']
            elif dlc_filter == 'dlc':
                full_trophies = [t for t in full_trophies if t['trophy_group_id'] != 'default']

            sort_key = form.cleaned_data['sort']
            if sort_key == 'earned_date':
                full_trophies.sort(
                    key=lambda t: (
                        profile_earned.get(t['trophy_id'], {}).get('earned_date_time') is None,
                        profile_earned.get(t['trophy_id'], {}).get('earned_date_time') or timezone.make_aware(datetime.min)
                    )
                )
            elif sort_key == 'psn_rarity':
                full_trophies.sort(key=lambda t: t['trophy_earn_rate'], reverse=False)
            elif sort_key == 'pp_rarity':
                full_trophies.sort(key=lambda t: t['earn_rate'], reverse=False)
            elif sort_key == 'alpha':
                full_trophies.sort(key=lambda t: t['trophy_name'].lower())
            elif sort_key == 'earned_count':
                full_trophies.sort(key=lambda t: (-t['earned_count'], t['trophy_name'].lower()))
            elif sort_key == 'earned_count_inv':
                full_trophies.sort(key=lambda t: (t['earned_count'], t['trophy_name'].lower()))
            elif sort_key == 'type':
                type_order = {'platinum': 0, 'gold': 1, 'silver': 2, 'bronze': 3}
                full_trophies.sort(key=lambda t: (type_order.get(t['trophy_type'], 4), t['trophy_name'].lower()))

        return full_trophies, build_trophy_groups(game), group_trophy_rows(full_trophies), has_trophies

    def _build_group_bars(self, trophy_groups, profile_group_totals):
        """Composite per-group progress: ONE segment per trophy group (base + DLCs), each filled to
        that group's earned/total %. The base segment always takes >= 50% of the width -- its flex
        weight is (shown DLC count + 1), so with one DLC it's ~67/33 and it eases toward 50% as DLCs
        grow. DLC segments are capped. Cheap: reads the already-aggregated group totals + defined
        counts, no new queries. Returns None when there are no trophies to show.
        """
        DLC_CAP = 6

        def entry(gid, group):
            defined = group.get('defined_trophies') or {}
            total = sum(int(v or 0) for v in defined.values())
            earned = sum((profile_group_totals.get(gid) or {}).values())
            pct = round(earned / total * 100) if total else 0
            return {'name': group.get('trophy_group_name') or '', 'pct': pct, 'earned': earned, 'total': total}

        main = None
        dlcs = []
        for gid, group in trophy_groups.items():
            e = entry(gid, group)
            if not e['total']:
                continue
            if gid == 'default':
                e['name'] = 'Base Game'
                main = e
            else:
                dlcs.append(e)
        if main is None:
            return None
        dlc_total = len(dlcs)
        combined = dlc_total > DLC_CAP
        if combined:
            # Too many DLC packs to show individually -> collapse to ONE "All DLC" segment at the
            # aggregate %, so every DLC trophy is still represented (nothing dropped from the bar).
            t = sum(d['total'] for d in dlcs)
            e = sum(d['earned'] for d in dlcs)
            dlcs = [{'name': 'All DLC', 'pct': round(e / t * 100) if t else 0, 'earned': e, 'total': t}]
        return {'main': main, 'dlcs': dlcs, 'dlc_total': dlc_total, 'combined_dlc': combined}

    def _build_group_pct(self, trophy_groups, profile_group_totals):
        """Delegates to compute_group_pct (shared with GamePageView's viewport)."""
        return compute_group_pct(trophy_groups, profile_group_totals)

    def _build_breadcrumbs(self, game, target_profile):
        """
        Build breadcrumb navigation.

        Args:
            game: Game instance
            target_profile: Profile instance or None

        Returns:
            list: Breadcrumb items
        """
        crumbs = [
            {'text': 'Home', 'url': reverse_lazy('home')},
            {'text': 'Games', 'url': reverse_lazy('games_list')},
        ]
        # Games/Trophy Lists IA: the concept Game page sits between the hub and this list, so the
        # trail reads Home > Games > <the work> > <this list's name>.
        if game.concept_id and game.concept.unified_title:
            crumbs.append({'text': game.concept.unified_title, 'url': game.concept.game_page_url()})
        crumbs.append({'text': f"{game.title_name}"})
        return crumbs

    # PSN-global rarity tiers (Trophy.trophy_rarity): Common=3 .. Ultra_Rare=0.
    _RARITY_LABELS = {0: 'Ultra Rare', 1: 'Very Rare', 2: 'Rare', 3: 'Common'}

    # Contract-row status tag -> (label, CSS variant). Always shown on the pursuit spine row.

    def _build_outlook_context(self, game):
        """
        Anonymous "Platinum Outlook" -- the logged-out counterpart to a member's
        progress readout (this is the SEO inbound funnel). Leads with the PSN-GLOBAL
        platinum rarity (a dense, platform-wide difficulty signal that does NOT depend
        on our userbase size), plus denormed community reach and a cached site-wide
        hunter count for the CTA.

        Whale-safe: one indexed single-game platinum lookup, denorm reads straight off
        `game`, and the hourly-cached heartbeat (never a request-path COUNT). Returns {}
        on any failure so the panel degrades to absent.
        """
        try:
            plat = (
                Trophy.objects.filter(game=game, trophy_type='platinum')
                .only('trophy_earn_rate', 'trophy_rarity').first()
            )
            plat_rate = plat.trophy_earn_rate if plat and plat.trophy_earn_rate else None
            plat_rarity = plat.trophy_rarity if plat and plat.trophy_rarity is not None else None

            heartbeat = get_cached_heartbeat()
            hunters = (heartbeat or {}).get('always', {}).get('profiles_total', {}).get('value')

            return {
                'outlook': {
                    'has_platinum': plat is not None,
                    'plat_rate': plat_rate,
                    'plat_rarity_label': self._RARITY_LABELS.get(plat_rarity),
                    # 4 = Ultra Rare (hardest) .. 1 = Common, for a difficulty meter.
                    'difficulty_level': (4 - plat_rarity) if plat_rarity is not None else None,
                    'site_hunters': hunters,
                    'played_count': game.played_count,
                }
            }
        except Exception:
            logger.exception(
                "Failed to build outlook context for game %s",
                getattr(game, 'np_communication_id', '?'),
            )
            return {}

    def get_template_names(self):
        if getattr(self.request, 'htmx', False) and self.request.htmx.target == 'browse-results':
            return ['trophies/partials/game_detail/trophy_browse_results.html']
        return super().get_template_names()

    def get_context_data(self, **kwargs):
        """
        Build context for game detail page.

        Delegates to helper methods for profile data, images, stats, trophies, and concept info.

        Returns:
            dict: Complete context for template rendering
        """
        context = super().get_context_data(**kwargs)
        game = self.object
        user = self.request.user

        # Get target profile (from URL or authenticated user)
        target_profile = self._get_target_profile()
        psn_username = self.kwargs.get('psn_username')
        context['url_psn_username'] = psn_username
        logger.info(f"Target Profile: {target_profile} | Profile Username: {psn_username}")

        # Build profile-specific context (progress, timeline events, earned trophies)
        if target_profile:
            profile_context = self._build_profile_context(game, target_profile)
            context['profile'] = target_profile
            context['profile_progress'] = profile_context['profile_progress']
            context['profile_earned'] = profile_context['profile_earned']
            context['profile_trophy_totals'] = profile_context['profile_trophy_totals']
            context['profile_group_totals'] = profile_context['profile_group_totals']
            context['timeline_events'] = profile_context['timeline_events']
            # NOTE: this merge is key-by-key, not a context.update() -- a new key added inside
            # _build_profile_context does NOT reach the template until it is listed here.
            context['plat_card'] = profile_context.get('plat_card')
        else:
            context['profile'] = None
            context['profile_progress'] = None
            context['plat_card'] = None
            context['profile_earned'] = {}
            context['profile_trophy_totals'] = {}
            context['profile_group_totals'] = {}
            context['timeline_events'] = [
                self._make_timeline_event('Started Playing', 'started', False),
                self._make_timeline_event('First Trophy', 'trophy', False),
                self._make_timeline_event('50% Trophy', 'trophy', False),
                self._make_timeline_event('Platinum Trophy', 'trophy', False),
                self._make_timeline_event('100% Trophy', 'trophy', False),
            ]

        # Expose the displayed profile under the badge-detail-style name too, so the
        # hero's ownership-aware progress readout can gate on `target_profile`.
        context['target_profile'] = target_profile

        # Ownership-aware header: is the displayed profile someone OTHER than the
        # viewer? Drives the "Viewing X's progress" banner (badge-detail pattern) so
        # the hero reads correctly on the /games/<np>/<username>/ variant.
        viewer_profile = getattr(user, 'profile', None) if user.is_authenticated else None
        context['viewing_other_profile'] = (
            target_profile
            if target_profile and (viewer_profile is None or target_profile.pk != viewer_profile.pk)
            else None
        )

        # Build game images context
        context['image_urls'] = self._build_images_context(game)

        # `game_stats` is gone: the rebuilt hero and ratings panel read the denormed Game
        # columns (played_count / plats_earned_count / full_completion_count /
        # avg_completion) straight off the object, so the wrapper dict had no consumer left
        # once the old header partial was replaced. Its provider survived on `main` only
        # because the pre-rebuild header still rendered it.

        # Build trophy context with filtering/sorting
        form = GameDetailForm(self.request.GET)
        context['form'] = form
        context['selected_trophy_types'] = self.request.GET.getlist('trophy_type')
        context['selected_rarity_brackets'] = self.request.GET.getlist('rarity_bracket')
        profile_earned = context.get('profile_earned', {})
        full_trophies, trophy_groups, grouped_trophies, has_trophies = self._build_trophy_context(game, form, profile_earned)

        if has_trophies:
            context['grouped_trophies'] = grouped_trophies
            context['trophy_groups'] = trophy_groups
        else:
            context['trophies_syncing'] = True
            context['grouped_trophies'] = {}
            context['trophy_groups'] = {}

        # Trophies shown after the active filters (drives the count-up in the toolbar/minibar). Bounded
        # to ~#groups sums; grouped_trophies is already materialized for rendering.
        context['trophy_shown_count'] = sum(len(t) for t in context['grouped_trophies'].values())

        # Composite per-trophy-group progress bar (base + DLC segments), for a displayed profile.
        context['group_bars'] = (
            self._build_group_bars(context['trophy_groups'], context.get('profile_group_totals') or {})
            if target_profile and context['trophy_groups'] else None
        )

        # Per-group completion % keyed by group_id, for the trophy-panel group headers (authed only).
        context['profile_group_pct'] = (
            self._build_group_pct(context['trophy_groups'], context.get('profile_group_totals') or {})
            if target_profile and context['trophy_groups'] else {}
        )

        # Concept-level SUBSET only (the slim-down): the hero's badge spine + versions modal.
        # The rest of _build_concept_context (About facts, community_tabs, the ratings assembly)
        # left with the Ratings/About tabs -- the concept Game page is their host now.
        context.update(self._build_badges_context(game))
        context.update(self._build_versions_context(game))

        # Spine cross-link: this game's Contract + the Jobs it levels (hero band).
        context.update(self._build_pursuit_context(game, target_profile))

        # Platinum Outlook: PSN-global difficulty + community reach. Only the logged-out hero renders it,
        # so skip the work (a trophy lookup) for members who'll never see it.
        if not target_profile:
            context.update(self._build_outlook_context(game))

        # Build breadcrumbs
        context['breadcrumb'] = self._build_breadcrumbs(game, target_profile)

        # The hero's "Part of <game> - View game" link + the breadcrumb's Game-page crumb. NOT
        # the canonical anymore: the slim-down gave this page distinct stack content (trophies,
        # Ranks, the community snapshot -- Ratings/About moved up), so it earned back the
        # self-canonical the IA doc's slice-1 interim promised.
        context['concept_page_url'] = (
            f"{self.request.scheme}://{self.request.get_host()}{game.concept.game_page_url()}"
            if game.concept_id else None
        )
        # One absolute self-canonical, computed once (the GamePageView pattern): the rel=canonical,
        # og:url and the jsonld VideoGame node all read this single value. An EXPLICIT bare list
        # URL, never base.html's request.path default -- that would mint per-viewer canonicals on
        # the /games/<np>/<username>/ variant and per-state ones under ?view=.
        context['page_canonical_url'] = (
            f"{self.request.scheme}://{self.request.get_host()}"
            f"{reverse('game_detail', kwargs={'np_communication_id': game.np_communication_id})}"
        )

        context['seo_description'] = (
            f"{game.title_name} on {game.platforms_display}. "
            f"{game.get_total_defined_trophies()} trophies including "
            f"{game.defined_trophies.get('platinum', 0)} platinum. "
            f"Track your progress on Platinum Pursuit."
        )


        # Game Detail Tour: auto-show once, only after Welcome Tour is done.
        # Always keyed to the viewer's own profile, regardless of whose page is being viewed.
        return context

class GuideListView(ListView):
    """
    Display list of available trophy guides (PPTV section).

    Shows game concepts that have associated guide content, with options to:
    - Search by game name
    - Sort by release date
    - View featured guide of the day

    Featured guide is cached daily and rotates based on priority or randomly.
    """
    model = Concept
    template_name = 'trophies/guide_list.html'
    context_object_name = 'guides'
    paginate_by = 6

    def get_queryset(self):
        qs = Concept.objects.exclude(Q(guide_slug__isnull=True) | Q(guide_slug=''))
        form = GuideSearchForm(self.request.GET)
        order = ['unified_title']

        if form.is_valid():
            query = form.cleaned_data.get('query')
            sort_val = form.cleaned_data.get('sort')

            if query:
                qs = qs.filter(Q(unified_title__icontains=query))

            if sort_val == 'release_asc':
                order = ['release_date', 'unified_title']
            elif sort_val == 'release_desc':
                order = ['-release_date', 'unified_title']

        return qs.order_by(*order)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        today_utc = timezone.now().date().isoformat()
        cache_key = f"featured_guide:{today_utc}"

        cached_value = cache.get(cache_key)
        if cached_value is None:
            featured_qs = FeaturedGuide.objects.filter(
                Q(start_date__lte=timezone.now()) & (Q(end_date__gte=timezone.now()) | Q(end_date__isnull=True))
            ).order_by('-priority').first()
            if featured_qs:
                featured_concept = featured_qs.concept
            else:
                guides = Concept.objects.exclude(Q(guide_slug__isnull=True) | Q(guide_slug=''))
                if guides.exists():
                    featured_concept = guides.order_by('?').first()
                else:
                    featured_concept = None

            if featured_concept:
                cache.set(cache_key, featured_concept.id, timeout=86400)
            else:
                cache.set(cache_key, -1, timeout=86400)
        else:
            if cached_value == -1:
                featured_concept = None
            else:
                featured_concept = Concept.objects.get(id=cached_value)

        context['breadcrumb'] = [
            {'text': 'Home', 'url': reverse_lazy('home')},
            {'text': 'PPTV'}
        ]

        context['featured_concept'] = featured_concept
        context['form'] = GuideSearchForm(self.request.GET)

        track_site_event('guide_visit', 'list', self.request)

        return context


class RecentlyAddedView(HtmxListMixin, ListView):
    """Browse recently discovered base games and DLC trophy lists.

    Landing state shows two category cards (base games, DLC) with 30-day counts.
    Selecting a category displays a paginated grid sorted by discovery date.
    """
    template_name = 'trophies/recently_added.html'
    partial_template_name = 'trophies/partials/recently_added/browse_results.html'
    paginate_by = 30

    def get_template_names(self):
        # Two HTMX swap scopes: the New Games/New DLC switcher swaps the whole #ra-view island (toolbar + grid,
        # so the category-scoped sorts + Has Platinum filter re-render); a filter/sort change or an
        # InfiniteScroller page fetch (XHR) swaps only the inner #browse-results grid.
        htmx = getattr(self.request, 'htmx', False)
        if htmx and self.request.htmx.target == 'ra-view':
            return ['trophies/partials/recently_added/view.html']
        xhr = self.request.headers.get('X-Requested-With') == 'XMLHttpRequest'
        if (htmx and self.request.htmx.target == 'browse-results') or xhr:
            return [self.partial_template_name]
        return [self.template_name]

    CATEGORIES = {
        'base_games': {
            'label': 'New Games',
            'description': 'Base game trophy lists recently added to the database.',
            'color': 'info',
            'icon': 'gamepad-2',
        },
        'dlc': {
            'label': 'New DLC',
            'description': 'DLC trophy packs recently discovered.',
            'color': 'secondary',
            'icon': 'puzzle',
        },
    }

    def get_category(self):
        category = self.request.GET.get('category', 'base_games')
        return category if category in self.CATEGORIES else 'base_games'

    @property
    def model(self):
        if self.get_category() == 'dlc':
            return TrophyGroup
        return Game

    WINDOW_DAYS = 30       # "Recently added" = discovered within this many days (matches the header stats).
    POOL_SIZE = 200        # Safety ceiling on the window -- guards the page against an unbounded mass-import.

    def get_window_start(self):
        return timezone.now() - timedelta(days=self.WINDOW_DAYS)

    def get_queryset(self):
        category = self.get_category()
        sort_val = self.request.GET.get('sort', 'recent')
        cutoff = self.get_window_start()

        if category == 'base_games':
            # Base games discovered within the window (most-recent first), ceilinged at POOL_SIZE.
            recent_ids = list(
                Game.objects.filter(created_at__gte=cutoff).order_by('-created_at')
                .values_list('id', flat=True)[:self.POOL_SIZE]
            )
            qs = (
                Game.objects.filter(id__in=recent_ids)
                .select_related('concept', 'concept__igdb_match')
                # The ~30 KB IGDB raw_response is never read by the card; defer it so many-card renders
                # don't pile the blob into the join payload (whale-safe cover-render rule).
                .defer('concept__igdb_match__raw_response')
            )

            # Filters
            platforms = self.request.GET.getlist('platform')
            if platforms:
                platform_q = Q()
                for plat in platforms:
                    platform_q |= Q(title_platform__contains=plat)
                qs = qs.filter(platform_q)

            has_plat = self.request.GET.get('has_platinum')
            if has_plat:
                qs = qs.filter(trophies__trophy_type='platinum').distinct()

            if self.request.GET.get('hide_shovelware'):
                qs = qs.exclude(shovelware_status__in=['auto_flagged', 'manually_flagged'])

            # Sort within the capped pool
            if sort_val == 'alpha':
                qs = annotate_ascii_name(qs)
                return qs.order_by('is_ascii_name', Lower('title_name'))
            elif sort_val == 'played':
                return qs.order_by('-played_count', '-created_at')
            elif sort_val == 'trophy_count':
                qs = qs.annotate(
                    _total_trophies=(
                        Coalesce(Cast(F('defined_trophies__bronze'), IntegerField()), Value(0))
                        + Coalesce(Cast(F('defined_trophies__silver'), IntegerField()), Value(0))
                        + Coalesce(Cast(F('defined_trophies__gold'), IntegerField()), Value(0))
                        + Coalesce(Cast(F('defined_trophies__platinum'), IntegerField()), Value(0))
                    ),
                )
                return qs.order_by('-_total_trophies', '-created_at')
            elif sort_val == 'rating':
                qs = annotate_community_ratings(qs, 'concept_id')
                return qs.order_by(F('_avg_rating').desc(nulls_last=True), '-created_at')
            else:  # 'recent' (default)
                return qs.order_by('-created_at')

        if category == 'dlc':
            # DLC packs discovered within the window (most-recent first), ceilinged at POOL_SIZE.
            recent_ids = list(
                TrophyGroup.objects.exclude(trophy_group_id='default')
                .filter(created_at__gte=cutoff)
                .order_by('-created_at')
                .values_list('id', flat=True)[:self.POOL_SIZE]
            )
            qs = (
                TrophyGroup.objects.filter(id__in=recent_ids)
                .exclude(trophy_group_id='default')
                .select_related('game', 'game__concept', 'game__concept__igdb_match')
                # Defer the IGDB blob (never read by the DLC card) off the join payload.
                .defer('game__concept__igdb_match__raw_response')
            )

            # Filters
            platforms = self.request.GET.getlist('platform')
            if platforms:
                platform_q = Q()
                for plat in platforms:
                    platform_q |= Q(game__title_platform__contains=plat)
                qs = qs.filter(platform_q)

            if self.request.GET.get('hide_shovelware'):
                qs = qs.exclude(game__shovelware_status__in=['auto_flagged', 'manually_flagged'])

            # Sort within the capped pool
            if sort_val == 'alpha':
                return qs.order_by(Lower('game__title_name'), 'trophy_group_name')
            elif sort_val == 'trophy_count':
                qs = qs.annotate(
                    _dlc_trophies=(
                        Coalesce(Cast(F('defined_trophies__bronze'), IntegerField()), Value(0))
                        + Coalesce(Cast(F('defined_trophies__silver'), IntegerField()), Value(0))
                        + Coalesce(Cast(F('defined_trophies__gold'), IntegerField()), Value(0))
                        + Coalesce(Cast(F('defined_trophies__platinum'), IntegerField()), Value(0))
                    ),
                )
                return qs.order_by('-_dlc_trophies', '-created_at')
            else:  # 'recent' (default)
                return qs.order_by('-created_at')

        return Game.objects.none()

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        # Applied-filters signal for the pre-paint drawer collapse (category/sort/page are
        # display state, mirroring the filterPanel skip set in recently-added.js).
        context['has_advanced_filters'] = any(
            self.request.GET.getlist(k) for k in ('platform', 'has_platinum', 'hide_shovelware')
        )
        context['breadcrumb'] = [
            {'text': 'Home', 'url': reverse_lazy('home')},
            {'text': 'Games', 'url': reverse_lazy('games_list')},
            {'text': 'Recently Added'},
        ]

        category = self.get_category()
        context['active_category'] = category
        context['categories'] = self.CATEGORIES
        context['current_sort'] = self.request.GET.get('sort', 'recent')
        context['selected_platforms'] = self.request.GET.getlist('platform')
        context['has_platinum_checked'] = bool(self.request.GET.get('has_platinum'))
        context['hide_shovelware_checked'] = bool(self.request.GET.get('hide_shovelware'))
        context['platform_choices'] = ALL_PLATFORMS

        # Windowed discovery counts for the header stats + switcher captions (2 bounded counts). Same window
        # the grid uses (get_window_start) -- these are the raw discovery scale for the window, so they read
        # a touch higher than the grid when active filters narrow it or a POOL_SIZE-exceeding burst is capped.
        window_start = self.get_window_start()
        dlc_qs = TrophyGroup.objects.exclude(trophy_group_id='default')
        context['category_counts'] = {
            'base_games': Game.objects.filter(created_at__gte=window_start).count(),
            'dlc': dlc_qs.filter(created_at__gte=window_start).count(),
        }

        # Freshest add across both categories -> header recency stat (2 indexed LIMIT-1 lookups).
        newest_game = Game.objects.order_by('-created_at').values_list('created_at', flat=True).first()
        newest_dlc = dlc_qs.order_by('-created_at').values_list('created_at', flat=True).first()
        context['newest_added_at'] = max(
            [t for t in (newest_game, newest_dlc) if t is not None], default=None,
        )

        # Base-games cards render the shared `.pp-gcard`, so feed it the same batched, whale-safe card
        # context Browse Games uses (progress / DLC counts / ratings / badge + contract pursuer hooks).
        if category == 'base_games':
            context.update(build_game_card_context(context['object_list'], self.request))

        context['seo_description'] = (
            "Browse recently added PlayStation trophy lists: new games "
            "and DLC packs discovered by the Platinum Pursuit scout network."
        )

        return context
