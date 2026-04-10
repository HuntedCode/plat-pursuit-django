import logging

from core.services.tracking import track_page_view
from django.db.models import Q, Count, Avg, Sum, Prefetch, Value, IntegerField, Case, When
from django.db.models.functions import Lower
from django.urls import reverse_lazy
from django.views.generic import ListView, DetailView

from trophies.mixins import ProfileHotbarMixin, HtmxListMixin
from ..models import Company, ConceptCompany, Game, Trophy, UserConceptRating, ProfileGame
from ..forms import CompanySearchForm

logger = logging.getLogger("psn_api")


class CompanyListView(HtmxListMixin, ProfileHotbarMixin, ListView):
    """Browse page for game developers and publishers."""
    model = Company
    template_name = 'trophies/company_list.html'
    partial_template_name = 'trophies/partials/company_list/browse_results.html'
    paginate_by = 32

    def get_queryset(self):
        qs = super().get_queryset().annotate(
            game_count=Count('company_concepts', distinct=True),
        ).filter(game_count__gt=0)

        form = CompanySearchForm(self.request.GET)
        order = [Lower('name')]

        if form.is_valid():
            query = form.cleaned_data.get('query')
            roles = form.cleaned_data.get('role')
            country = form.cleaned_data.get('country')
            sort_val = form.cleaned_data.get('sort')

            if query:
                qs = qs.filter(name__icontains=query)
            if roles:
                role_q = Q()
                for role in roles:
                    if role == 'developer':
                        role_q |= Q(company_concepts__is_developer=True)
                    elif role == 'publisher':
                        role_q |= Q(company_concepts__is_publisher=True)
                    elif role == 'porting':
                        role_q |= Q(company_concepts__is_porting=True)
                    elif role == 'supporting':
                        role_q |= Q(company_concepts__is_supporting=True)
                qs = qs.filter(role_q).distinct()
            if country:
                qs = qs.filter(country__iexact=country)

            if sort_val == 'games':
                order = ['-game_count', Lower('name')]
            elif sort_val == 'games_inv':
                order = ['game_count', Lower('name')]

        return qs.order_by(*order)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['breadcrumb'] = [
            {'text': 'Home', 'url': reverse_lazy('home')},
            {'text': 'Companies'},
        ]
        context['form'] = CompanySearchForm(self.request.GET)
        context['selected_roles'] = self.request.GET.getlist('role')

        context['seo_description'] = (
            "Browse PlayStation game developers and publishers on Platinum Pursuit. "
            "Find games by your favorite studios."
        )

        track_page_view('companies_list', 'list', self.request)
        return context


class CompanyDetailView(ProfileHotbarMixin, DetailView):
    """Detail page for a single company showing their games by role."""
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

        # Games by role (single query, sorted in Python)
        all_concept_companies = list(
            ConceptCompany.objects.filter(company=company)
            .select_related('concept')
            .prefetch_related(
                Prefetch('concept__games', queryset=Game.objects.select_related('concept__igdb_match'))
            )
        )

        role_games = {'developed': [], 'published': [], 'ported': [], 'supported': []}
        seen = {k: set() for k in role_games}  # deduplicate games across concepts

        for cc in all_concept_companies:
            for game in cc.concept.games.all():
                for role_key, flag in [('developed', 'is_developer'), ('published', 'is_publisher'),
                                        ('ported', 'is_porting'), ('supported', 'is_supporting')]:
                    if getattr(cc, flag) and game.id not in seen[role_key]:
                        role_games[role_key].append(game)
                        seen[role_key].add(game.id)

        # Sort each role's games alphabetically
        for key in role_games:
            role_games[key].sort(key=lambda g: g.title_name.lower())

        context['role_games'] = role_games
        context['total_games'] = len(set().union(*seen.values()))

        # Merger chain
        if company.changed_company_id:
            context['current_company'] = company.current_company
        if company.parent_id:
            context['parent_company'] = company.parent

        # Community stats across this company's games
        company_concept_ids = list(ConceptCompany.objects.filter(
            company=company,
        ).values_list('concept_id', flat=True))
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

            # Player stats from games
            company_games = Game.objects.filter(concept_id__in=company_concept_ids)
            game_agg = company_games.aggregate(
                total_players=Sum('played_count'),
            )
            context['company_total_players'] = game_agg.get('total_players')

        context['seo_description'] = (
            f"View games developed and published by {company.name} on Platinum Pursuit."
        )

        track_page_view('company_detail', company.id, self.request)
        return context
