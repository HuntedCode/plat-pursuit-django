import logging

from core.services.tracking import track_page_view
from django.db.models import (
    Q, F, Count, Avg, Subquery, OuterRef, Prefetch, Value, IntegerField,
    FloatField, Case, When,
)
from django.db.models.functions import Lower
from django.http import Http404
from django.urls import reverse_lazy
from django.views.generic import ListView, TemplateView

from trophies.mixins import ProfileHotbarMixin, HtmxListMixin
from ..models import (
    Genre, Theme, Game, Trophy, Badge, UserConceptRating, ProfileGame,
    ConceptGenre, ConceptTheme,
)
from ..forms import GameSearchForm
from trophies.util_modules.constants import ALL_PLATFORMS
from .browse_helpers import (
    get_badge_picker_context, annotate_ascii_name, apply_game_browse_filters,
    apply_game_browse_sort,
)

logger = logging.getLogger("psn_api")


class GenreThemeListView(ProfileHotbarMixin, TemplateView):
    """Combined browse page for genres and themes with a tab toggle."""
    template_name = 'trophies/genre_theme_list.html'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['breadcrumb'] = [
            {'text': 'Home', 'url': reverse_lazy('home')},
            {'text': 'Genres & Themes'},
        ]

        active_tab = self.request.GET.get('tab', 'genres')
        query = self.request.GET.get('query', '').strip()
        sort_val = self.request.GET.get('sort', 'alpha')

        context['active_tab'] = active_tab

        # Pick the through-model and concept join field for whichever tab is
        # active. Each Subquery scoped through this row's tag → ConceptX →
        # Concept → Game keeps the outer query shape simple (one row per tag).
        if active_tab == 'themes':
            ThroughModel = ConceptTheme
            tag_field = 'theme'
            items = Theme.objects.all()
            context['item_type'] = 'theme'
            context['detail_url_name'] = 'theme_detail'
        else:
            ThroughModel = ConceptGenre
            tag_field = 'genre'
            items = Genre.objects.all()
            context['item_type'] = 'genre'
            context['detail_url_name'] = 'genre_detail'

        def _through_subquery(*aggregate_args, **aggregate_kwargs):
            """Build a Subquery scoped to this tag row.

            Each annotation needs to count/avg something across this tag's
            ConceptGenre/ConceptTheme rows. Wrapping each one in its own
            Subquery keeps the outer queryset shape at one row per tag, so
            chained sort annotations don't pile joins onto each other.
            """
            agg_name, agg_expr = next(iter(aggregate_kwargs.items()))
            return Subquery(
                ThroughModel.objects.filter(**{tag_field: OuterRef('pk')})
                .values(tag_field)
                .annotate(**{agg_name: agg_expr})
                .values(agg_name)[:1],
                output_field=aggregate_args[0] if aggregate_args else IntegerField(),
            )

        items = items.annotate(
            game_count=_through_subquery(IntegerField(), c=Count('concept__games', distinct=True)),
        ).filter(game_count__gt=0)

        if query:
            items = items.filter(name__icontains=query)

        if sort_val == 'games':
            items = items.order_by('-game_count', 'name')
        elif sort_val == 'avg_rating':
            items = items.annotate(
                _avg_rating=_through_subquery(
                    FloatField(),
                    v=Avg('concept__user_ratings__overall_rating',
                          filter=Q(concept__user_ratings__concept_trophy_group__isnull=True)),
                ),
            ).order_by(F('_avg_rating').desc(nulls_last=True), 'name')
        elif sort_val == 'players':
            items = items.annotate(
                _total_players=_through_subquery(
                    IntegerField(),
                    c=Count('concept__games__played_by', distinct=True),
                ),
            ).order_by(F('_total_players').desc(nulls_last=True), 'name')
        elif sort_val == 'plats_earned':
            items = items.annotate(
                _total_plats=_through_subquery(
                    IntegerField(),
                    c=Count('concept__games__played_by',
                            filter=Q(concept__games__played_by__has_plat=True),
                            distinct=True),
                ),
            ).order_by(F('_total_plats').desc(nulls_last=True), 'name')
        else:
            items = items.order_by('name')

        context['items'] = items

        context['seo_description'] = (
            "Browse PlayStation games by genre and theme. "
            "Find shooters, RPGs, horror games, and more on Platinum Pursuit."
        )

        track_page_view('genres_list', 'list', self.request)
        return context


class TagDetailBaseView(HtmxListMixin, ProfileHotbarMixin, ListView):
    """Base view for genre and theme detail pages. Shares filter/sort logic."""
    model = Game
    partial_template_name = 'trophies/partials/tag_detail/browse_results.html'
    paginate_by = 30

    def get_tag_filter(self):
        """Subclasses return the Q filter for their tag type."""
        raise NotImplementedError

    def get_filter_form(self):
        if not hasattr(self, '_filter_form'):
            self._filter_form = GameSearchForm(self.request.GET)
        return self._filter_form

    def get_queryset(self):
        # No .distinct() needed — Game.concept is a FK (1:1) and the through
        # tables enforce unique_together on (concept, tag), so the tag filter
        # produces one row per matching Game.
        qs = Game.objects.filter(self.get_tag_filter())
        form = self.get_filter_form()

        if form.is_valid():
            sort_val = form.cleaned_data.get('sort', '')
            qs, annotations = apply_game_browse_filters(qs, form, sort_val)
            qs, order = apply_game_browse_sort(qs, sort_val, annotations)
        else:
            qs = annotate_ascii_name(qs)
            order = ['is_ascii_name', Lower('title_name')]

        qs = qs.select_related(
            'concept', 'concept__igdb_match',
        ).prefetch_related(
            Prefetch('trophies', queryset=Trophy.objects.filter(trophy_type='platinum'), to_attr='platinum_trophy')
        )
        return qs.order_by(*order)

    def get_shared_context(self, context):
        """Adds filter form, platform choices, and post-pagination data."""
        # Total unfiltered game count for this tag (used in header flavor text)
        context['total_game_count'] = Game.objects.filter(
            self.get_tag_filter()
        ).count()

        form = self.get_filter_form()
        context['form'] = form
        context['selected_platforms'] = self.request.GET.getlist('platform')
        context['selected_regions'] = self.request.GET.getlist('regions')
        context['platform_choices'] = ALL_PLATFORMS
        context['show_only_platinum'] = self.request.GET.get('show_only_platinum', '')
        context['filter_shovelware'] = self.request.GET.get('filter_shovelware', '')
        context['show_delisted'] = self.request.GET.get('show_delisted', '')
        context['show_unobtainable'] = self.request.GET.get('show_unobtainable', '')
        context['show_online'] = self.request.GET.get('show_online', '')
        context['show_buggy'] = self.request.GET.get('show_buggy', '')
        context['selected_genres'] = self.request.GET.getlist('genres')
        context['selected_themes'] = self.request.GET.getlist('themes')
        context['view_type'] = self.request.GET.get('view', 'grid')

        context['has_advanced_filters'] = any(
            v for k, v in self.request.GET.lists()
            if k not in ('page', 'view') and any(v)
        )

        # Badge picker modal data
        context.update(get_badge_picker_context(self.request))

        # Rating map for page games
        page_games = context['object_list']
        concept_ids = [g.concept_id for g in page_games if g.concept_id]
        if concept_ids:
            ratings = UserConceptRating.objects.filter(
                concept_id__in=concept_ids,
                concept_trophy_group__isnull=True,
            ).values('concept_id').annotate(
                avg_difficulty=Avg('difficulty'),
                avg_fun=Avg('fun_ranking'),
                avg_rating=Avg('overall_rating'),
                rating_count=Count('id'),
            )
            context['rating_map'] = {r['concept_id']: r for r in ratings}

        # User game map
        if self.request.user.is_authenticated and hasattr(self.request.user, 'profile'):
            game_ids = [g.id for g in page_games]
            user_games = ProfileGame.objects.filter(
                profile=self.request.user.profile,
                game_id__in=game_ids,
            ).values('game_id', 'progress', 'has_plat', 'earned_trophies_count')
            context['user_game_map'] = {pg['game_id']: pg for pg in user_games}

        return context


class GenreDetailView(TagDetailBaseView):
    """Detail page for a single genre, showing all games in that genre."""
    template_name = 'trophies/tag_detail.html'

    def dispatch(self, request, *args, **kwargs):
        self.genre = Genre.objects.filter(slug=kwargs['slug']).first()
        if not self.genre:
            raise Http404
        return super().dispatch(request, *args, **kwargs)

    def get_tag_filter(self):
        return Q(concept__concept_genres__genre=self.genre)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['genre'] = self.genre
        context['tag_name'] = self.genre.name
        context['tag_type'] = 'Genre'
        context['tag_intro_suffix'] = 'in this genre. Find your next platinum.'
        context['detail_url_name'] = 'genre_detail'
        context['detail_slug'] = self.genre.slug
        context['breadcrumb'] = [
            {'text': 'Home', 'url': reverse_lazy('home')},
            {'text': 'Genres & Themes', 'url': reverse_lazy('genres_list')},
            {'text': self.genre.name},
        ]
        context['seo_description'] = (
            f"Browse {self.genre.name} games on Platinum Pursuit. "
            f"Find trophies, track progress, and discover new games."
        )
        context = self.get_shared_context(context)
        track_page_view('genre_detail', self.genre.id, self.request)
        return context


class ThemeDetailView(TagDetailBaseView):
    """Detail page for a single theme, showing all games with that theme."""
    template_name = 'trophies/tag_detail.html'

    def dispatch(self, request, *args, **kwargs):
        self.theme = Theme.objects.filter(slug=kwargs['slug']).first()
        if not self.theme:
            raise Http404
        return super().dispatch(request, *args, **kwargs)

    def get_tag_filter(self):
        return Q(concept__concept_themes__theme=self.theme)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['theme'] = self.theme
        context['tag_name'] = self.theme.name
        context['tag_type'] = 'Theme'
        context['tag_intro_suffix'] = 'with this theme.'
        context['detail_url_name'] = 'theme_detail'
        context['detail_slug'] = self.theme.slug
        context['breadcrumb'] = [
            {'text': 'Home', 'url': reverse_lazy('home')},
            {'text': 'Genres & Themes', 'url': reverse_lazy('genres_list')},
            {'text': self.theme.name},
        ]
        context['seo_description'] = (
            f"Browse {self.theme.name} themed games on Platinum Pursuit."
        )
        context = self.get_shared_context(context)
        track_page_view('theme_detail', self.theme.id, self.request)
        return context


