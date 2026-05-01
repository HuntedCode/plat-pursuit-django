import logging

from core.services.tracking import track_page_view
from django.db.models import (
    Q, F, Count, Avg, Sum, Prefetch, Subquery, OuterRef, Exists, FloatField,
    IntegerField,
)
from django.db.models.functions import Lower
from django.urls import reverse_lazy
from django.views.generic import ListView, DetailView

from trophies.mixins import ProfileHotbarMixin, HtmxListMixin
from trophies.services import game_grouping_service as grouping
from ..models import Company, ConceptCompany, Game, UserConceptRating, ProfileGame
from ..forms import CompanySearchForm

logger = logging.getLogger("psn_api")


# Cover-art subquery annotations for company browse cards. The through-path
# (Game -> Concept -> ConceptCompany -> Company) is the same for all three
# tiers so we factor it out once.
def _company_cover_annotations():
    """Returns the four cover-art Subquery annotations for Company rows."""
    path = 'concept__concept_companies__company'
    return {
        'representative_title_image': grouping.representative_title_image_subquery(
            through_path=path,
        ),
        'representative_concept_icon': grouping.representative_concept_icon_subquery(
            through_path=path,
        ),
        'representative_igdb_cover_id': grouping.representative_igdb_cover_id_subquery(
            through_path=path,
        ),
        'representative_title_icon': grouping.representative_title_icon_subquery(
            through_path=path,
        ),
    }


# Detail-page role metadata. Driven by this list so ordering, slugs, and the
# ConceptCompany role flag all stay in one place.
_ROLE_SPECS = [
    # (slug, display label, ConceptCompany flag field)
    ('developed', 'Developed', 'is_developer'),
    ('published', 'Published', 'is_publisher'),
    ('ported', 'Ported', 'is_porting'),
    ('supported', 'Supporting Development', 'is_supporting'),
]


class CompanyListView(HtmxListMixin, ProfileHotbarMixin, ListView):
    """Browse page for game developers and publishers."""
    model = Company
    template_name = 'trophies/company_list.html'
    partial_template_name = 'trophies/partials/company_list/browse_results.html'
    paginate_by = 32

    def get_queryset(self):
        qs = super().get_queryset().annotate(
            # game_count: distinct IGDB game IDs (the true "game" count).
            # Two concepts sharing the same igdb_id count as ONE game.
            game_count=Count('company_concepts__concept__igdb_match__igdb_id', distinct=True),
            # version_count: distinct Games (individual PSN trophy lists).
            version_count=Count('company_concepts__concept__games', distinct=True),
            **_company_cover_annotations(),
        ).filter(version_count__gt=0)

        form = CompanySearchForm(self.request.GET)
        order = [Lower('name')]

        if form.is_valid():
            query = form.cleaned_data.get('query')
            roles = form.cleaned_data.get('role')
            country = form.cleaned_data.get('country')
            sort_val = form.cleaned_data.get('sort')

            if query:
                qs = qs.filter(name__icontains=query)
            if country:
                # Company.country is ISO 3166-1 numeric (IntegerField). The
                # form coerces choices to strings; cast back here.
                try:
                    qs = qs.filter(country=int(country))
                except (TypeError, ValueError):
                    pass

            # Each filter below uses an independent Exists() subquery against
            # ConceptCompany. Doing one Exists per filter preserves "any-link"
            # semantics (a company matches if SOME of its links satisfy each
            # criterion, not necessarily the same link), and avoids the join
            # cardinality explosion we'd get from chained .filter().distinct()
            # over multi-relation paths.
            if roles:
                role_q = Q()
                for role in roles:
                    if role == 'developer':
                        role_q |= Q(is_developer=True)
                    elif role == 'publisher':
                        role_q |= Q(is_publisher=True)
                    elif role == 'porting':
                        role_q |= Q(is_porting=True)
                    elif role == 'supporting':
                        role_q |= Q(is_supporting=True)
                qs = qs.filter(Exists(
                    ConceptCompany.objects.filter(company=OuterRef('pk')).filter(role_q)
                ))

            platforms = form.cleaned_data.get('platform')
            if platforms:
                platform_q = Q()
                for plat in platforms:
                    platform_q |= Q(concept__games__title_platform__contains=plat)
                qs = qs.filter(Exists(
                    ConceptCompany.objects.filter(company=OuterRef('pk')).filter(platform_q)
                ))

            genres = form.cleaned_data.get('genres')
            if genres:
                qs = qs.filter(Exists(
                    ConceptCompany.objects.filter(
                        company=OuterRef('pk'),
                        concept__concept_genres__genre_id__in=genres,
                    )
                ))

            badge_series = form.cleaned_data.get('badge_series')
            if badge_series:
                from ..models import Badge
                live_slugs = Badge.objects.filter(
                    is_live=True,
                ).values_list('series_slug', flat=True)
                qs = qs.filter(Exists(
                    ConceptCompany.objects.filter(
                        company=OuterRef('pk'),
                        concept__stages__series_slug=badge_series,
                        concept__stages__series_slug__in=live_slugs,
                    )
                ))

            if sort_val == 'games':
                order = ['-game_count', Lower('name')]
            elif sort_val == 'games_inv':
                order = ['game_count', Lower('name')]
            elif sort_val == 'avg_rating':
                # Subquery avoids the join multiplication that would skew the
                # average when other filters are layered on top.
                qs = qs.annotate(
                    _avg_rating=Subquery(
                        UserConceptRating.objects.filter(
                            concept__concept_companies__company=OuterRef('pk'),
                            concept_trophy_group__isnull=True,
                        ).values('concept__concept_companies__company')
                        .annotate(v=Avg('overall_rating'))
                        .values('v')[:1],
                        output_field=FloatField(),
                    ),
                )
                order = [F('_avg_rating').desc(nulls_last=True), Lower('name')]
            elif sort_val == 'total_players':
                qs = qs.annotate(
                    _total_players=Subquery(
                        Game.objects.filter(
                            concept__concept_companies__company=OuterRef('pk'),
                        ).values('concept__concept_companies__company')
                        .annotate(s=Sum('played_count'))
                        .values('s')[:1],
                        output_field=IntegerField(),
                    ),
                )
                order = [F('_total_players').desc(nulls_last=True), Lower('name')]
            elif sort_val == 'plats_earned':
                qs = qs.annotate(
                    _total_plats=Subquery(
                        ProfileGame.objects.filter(
                            game__concept__concept_companies__company=OuterRef('pk'),
                            has_plat=True,
                        ).values('game__concept__concept_companies__company')
                        .annotate(c=Count('id'))
                        .values('c')[:1],
                        output_field=IntegerField(),
                    ),
                )
                order = [F('_total_plats').desc(nulls_last=True), Lower('name')]

        return qs.order_by(*order)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['breadcrumb'] = [
            {'text': 'Home', 'url': reverse_lazy('home')},
            {'text': 'Companies'},
        ]
        context['form'] = CompanySearchForm(self.request.GET)
        context['selected_roles'] = self.request.GET.getlist('role')
        context['selected_platforms'] = self.request.GET.getlist('platform')
        context['selected_genres'] = self.request.GET.getlist('genres')
        context['selected_country'] = self.request.GET.get('country', '')

        context['seo_description'] = (
            "Browse PlayStation game developers and publishers on Platinum Pursuit. "
            "Find games by your favorite studios."
        )

        track_page_view('companies_list', 'list', self.request)
        return context


class CompanyDetailView(ProfileHotbarMixin, DetailView):
    """Detail page for a single company showing their games by role.

    Games are grouped by IGDB id so multi-platform releases stack as versions
    of one card (same pattern as FranchiseDetailView). When the viewer has a
    linked profile, per-game progress rings replace trophy counts on each
    version row, and a "Your Progress" stat strip appears in the header.
    """
    model = Company
    template_name = 'trophies/company_detail.html'
    slug_field = 'slug'
    slug_url_kwarg = 'slug'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        company = self.object

        context['breadcrumb'] = [
            {'text': 'Home', 'url': reverse_lazy('home')},
            {'text': 'Companies', 'url': reverse_lazy('companies_list')},
            {'text': company.name},
        ]

        # Fetch every ConceptCompany link in one go, pre-fetching the concept's
        # games + IGDB match. We iterate these records twice — once to bucket
        # into roles, once to build the cross-role Game list for grouping —
        # but only run the DB query once.
        all_concept_companies = list(
            ConceptCompany.objects.filter(company=company)
            .select_related('concept')
            .prefetch_related(
                Prefetch('concept__games', queryset=Game.objects.select_related('concept__igdb_match'))
            )
        )

        # Per-role game_id sets (for partitioning groups below) and a unique
        # Game lookup keyed by id.
        role_game_ids = {slug: set() for slug, *_ in _ROLE_SPECS}
        all_games_by_id: dict[int, Game] = {}
        for cc in all_concept_companies:
            for game in cc.concept.games.all():
                all_games_by_id[game.id] = game
                for slug, _label, flag in _ROLE_SPECS:
                    if getattr(cc, flag):
                        role_game_ids[slug].add(game.id)

        all_games = sorted(all_games_by_id.values(), key=lambda g: g.title_name.lower())

        # Viewer's per-game progress, scoped to games that appear under any role.
        profile = (
            getattr(self.request.user, 'profile', None)
            if self.request.user.is_authenticated else None
        )
        user_progress_map = grouping.fetch_user_progress_map(profile, all_games)

        # Build ONE grouped list covering every game this company touched in
        # any role, then filter copies per role. Sharing the group identity
        # across roles means a game that's both dev'd AND published by this
        # company shows the same stats in both sections.
        all_groups = grouping.build_igdb_groups(
            all_games, user_progress_map=user_progress_map,
        )

        # Partition groups into per-role lists. A group belongs to a role if
        # ANY of its constituent games is linked to this company with that
        # role flag. Same group object ends up in multiple lists — safe
        # because we never mutate groups after this point.
        role_groups: dict[str, list[dict]] = {slug: [] for slug, *_ in _ROLE_SPECS}
        for group in all_groups:
            group_game_ids = {g.id for g in group['games']}
            for slug, *_ in _ROLE_SPECS:
                if group_game_ids & role_game_ids[slug]:
                    role_groups[slug].append(group)

        # Sort each role list by alpha default.
        for slug, *_ in _ROLE_SPECS:
            role_groups[slug] = grouping.sort_groups(role_groups[slug], 'alpha')

        # Aggregate stats across ALL games (deduplicated by IGDB group).
        total_trophies = sum(g['total_trophies'] for g in all_groups)
        total_platinums = sum(1 for g in all_groups if g['has_platinum'])
        total_games = len(all_groups)
        total_versions = sum(len(g['games']) for g in all_groups)

        user_progress_stats = grouping.compute_user_progress_stats(
            all_groups, total_trophies, user_progress_map, profile=profile,
        )

        # Hero cover: most recent release across all the company's output.
        hero_cover = grouping.pick_hero_cover(all_groups)

        # Role sections list (template iterates this): only roles that have
        # groups get rendered, in _ROLE_SPECS order.
        sections = [
            {
                'slug': slug,
                'label': label,
                'groups': role_groups[slug],
                'count': len(role_groups[slug]),
            }
            for slug, label, _flag in _ROLE_SPECS
            if role_groups[slug]
        ]

        # Merger chain (surfaced in the detail header as "Subsidiary of X" /
        # "Now operating as Y").
        if company.changed_company_id:
            context['current_company'] = company.current_company
        if company.parent_id:
            context['parent_company'] = company.parent

        # Community stats across this company's games (unchanged from the
        # pre-rebuild version — existing aggregation still correct).
        company_concept_ids = [cc.concept_id for cc in all_concept_companies]
        if company_concept_ids:
            rating_agg = UserConceptRating.objects.filter(
                concept_id__in=company_concept_ids,
                concept_trophy_group__isnull=True,
            ).aggregate(
                avg_rating=Avg('overall_rating'),
                avg_difficulty=Avg('difficulty'),
                avg_fun=Avg('fun_ranking'),
                avg_grindiness=Avg('grindiness'),
                avg_hours=Avg('hours_to_platinum'),
                rating_count=Count('id'),
            )
            context['company_avg_rating'] = rating_agg.get('avg_rating')
            context['company_avg_difficulty'] = rating_agg.get('avg_difficulty')
            context['company_avg_fun'] = rating_agg.get('avg_fun')
            context['company_avg_grindiness'] = rating_agg.get('avg_grindiness')
            context['company_avg_hours'] = rating_agg.get('avg_hours')
            context['company_rating_count'] = rating_agg.get('rating_count', 0)
            context['company_rated_games'] = UserConceptRating.objects.filter(
                concept_id__in=company_concept_ids,
                concept_trophy_group__isnull=True,
            ).values('concept_id').distinct().count()

            # Player stats summed across the company's games.
            game_agg = Game.objects.filter(
                concept_id__in=company_concept_ids,
            ).aggregate(total_players=Sum('played_count'))
            context['company_total_players'] = game_agg.get('total_players')

        context['sections'] = sections
        context['hero_cover'] = hero_cover
        context['total_games'] = total_games
        context['total_versions'] = total_versions
        context['total_trophies'] = total_trophies
        context['total_platinums'] = total_platinums
        context['user_progress_stats'] = user_progress_stats

        context['seo_description'] = (
            f"View games developed and published by {company.name} on Platinum Pursuit."
        )

        track_page_view('company_detail', company.id, self.request)
        return context
