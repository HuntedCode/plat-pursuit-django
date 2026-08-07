import logging

from core.services.tracking import track_page_view
from django.db.models import (
    Q, F, Count, Avg, Subquery, OuterRef, Prefetch, IntegerField, FloatField,
)
from django.db.models.functions import Lower
from django.http import Http404
from django.urls import reverse_lazy
from django.views.generic import ListView

from trophies.mixins import HtmxListMixin
from ..models import (
    Genre, Theme, Game, Trophy, UserConceptRating, ProfileGame,
    ConceptGenre, ConceptTheme,
)
from ..forms import GameSearchForm
from trophies.util_modules.constants import ALL_PLATFORMS
from .browse_helpers import (
    annotate_ascii_name, apply_game_browse_filters,
    apply_game_browse_sort,
)

logger = logging.getLogger("psn_api")


class GenreThemeListView(HtmxListMixin, ListView):
    """Combined browse page for genres and themes with a `?tab=` toggle.

    A bounded taxonomy (~20 genres / ~40 themes with games), so there is no
    pagination -- the whole tab renders in one grid. HTMX search/sort swap the
    `#browse-results` partial (like Browse Games), replacing the old full-page
    `hx-select` re-render. Each tag's category-tile cover is the materialized
    `representative_game` FK (recompute_tag_covers), read O(1) here -- no live
    cover subquery -- so the tiles scale regardless of catalogue size.
    """
    template_name = 'trophies/genre_theme_list.html'
    partial_template_name = 'trophies/partials/genre_theme_list/browse_results.html'
    context_object_name = 'items'

    VALID_TABS = ('genres', 'themes')

    def get_template_names(self):
        # Two HTMX swap scopes: the Genres/Themes switcher swaps the whole #gt-view island (toolbar + grid, so
        # the toolbar re-renders in sync); a search/sort change swaps only the inner #browse-results grid.
        htmx = getattr(self.request, 'htmx', False)
        if htmx and self.request.htmx.target == 'gt-view':
            return ['trophies/partials/genre_theme_list/view.html']
        xhr = self.request.headers.get('X-Requested-With') == 'XMLHttpRequest'
        if (htmx and self.request.htmx.target == 'browse-results') or xhr:
            return [self.partial_template_name]
        return [self.template_name]

    def get_tab(self):
        tab = self.request.GET.get('tab', 'genres')
        return tab if tab in self.VALID_TABS else 'genres'

    def _tab_config(self):
        """Per-tab model / through-table / join wiring for the active tab."""
        if self.get_tab() == 'themes':
            return {
                'model': Theme, 'through': ConceptTheme, 'tag_field': 'theme',
                'through_path': 'concept__concept_themes__theme',
                'item_type': 'theme', 'detail_url_name': 'theme_detail',
            }
        return {
            'model': Genre, 'through': ConceptGenre, 'tag_field': 'genre',
            'through_path': 'concept__concept_genres__genre',
            'item_type': 'genre', 'detail_url_name': 'genre_detail',
        }

    def get_queryset(self):
        cfg = self._tab_config()
        Through = cfg['through']
        tag_field = cfg['tag_field']
        query = self.request.GET.get('query', '').strip()
        sort_val = self.request.GET.get('sort', 'alpha')

        def _through_subquery(output_field, **agg):
            """A Subquery scoped to this tag row -- keeps the outer queryset at one
            row per tag so chained sort annotations don't pile joins onto each other."""
            name, expr = next(iter(agg.items()))
            return Subquery(
                Through.objects.filter(**{tag_field: OuterRef('pk')})
                .values(tag_field).annotate(**{name: expr}).values(name)[:1],
                output_field=output_field,
            )

        # Representative cover is materialized as an FK (recompute_tag_covers), read O(1) here -- no live cover
        # subquery, so the tile scales regardless of catalogue / contract-catalogue size. select_related the
        # game + its concept/igdb_match for display_image_url; defer the never-read raw_response blob.
        items = cfg['model'].objects.annotate(
            game_count=_through_subquery(IntegerField(), c=Count('concept__games', distinct=True)),
        ).filter(game_count__gt=0).select_related(
            'representative_game', 'representative_game__concept', 'representative_game__concept__igdb_match',
        ).defer('representative_game__concept__igdb_match__raw_response')

        if query:
            items = items.filter(name__icontains=query)

        # Sort. The secondary-stat sorts annotate a non-underscore field (template-accessible) so the tile
        # can surface the stat it's sorted by. Lower('name') keeps Unicode/emoji names sorting correctly.
        if sort_val == 'games':
            return items.order_by('-game_count', Lower('name'))
        if sort_val == 'avg_rating':
            return items.annotate(
                stat_rating=_through_subquery(
                    FloatField(),
                    v=Avg('concept__user_ratings__overall_rating',
                          filter=Q(concept__user_ratings__concept_trophy_group__isnull=True)),
                ),
            ).order_by(F('stat_rating').desc(nulls_last=True), Lower('name'))
        if sort_val == 'players':
            return items.annotate(
                stat_players=_through_subquery(
                    IntegerField(),
                    # Distinct PROFILES, not ProfileGame rows -- a hunter owning N games in the tag is one player.
                    c=Count('concept__games__played_by__profile', distinct=True),
                ),
            ).order_by(F('stat_players').desc(nulls_last=True), Lower('name'))
        if sort_val == 'plats_earned':
            return items.annotate(
                stat_plats=_through_subquery(
                    IntegerField(),
                    c=Count('concept__games__played_by',
                            filter=Q(concept__games__played_by__has_plat=True),
                            distinct=True),
                ),
            ).order_by(F('stat_plats').desc(nulls_last=True), Lower('name'))
        return items.order_by(Lower('name'))

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        cfg = self._tab_config()
        context['breadcrumb'] = [
            {'text': 'Home', 'url': reverse_lazy('home')},
            {'text': 'Genres & Themes'},
        ]
        context['active_tab'] = self.get_tab()
        context['item_type'] = cfg['item_type']
        context['detail_url_name'] = cfg['detail_url_name']
        context['current_sort'] = self.request.GET.get('sort', 'alpha')
        context['current_query'] = self.request.GET.get('query', '').strip()
        context['item_count'] = len(context['items'])

        # Header stats: how many genres / themes actually carry games. A distinct existence count is lighter
        # than a per-tag COUNT(DISTINCT games) aggregate -- we only need "has >=1 game", not the tally.
        context['genre_count'] = (
            Genre.objects.filter(genre_concepts__concept__games__isnull=False).distinct().count()
        )
        context['theme_count'] = (
            Theme.objects.filter(theme_concepts__concept__games__isnull=False).distinct().count()
        )

        context['seo_description'] = (
            "Browse PlayStation games by genre and theme. "
            "Find shooters, RPGs, horror games, and more on Platinum Pursuit."
        )

        track_page_view('genres_list', 'list', self.request)
        return context


class TagDetailBaseView(HtmxListMixin, ListView):
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


