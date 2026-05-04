"""Game engine browse + detail pages.

Engine browse is single-pane alongside genres/themes. Engine detail mirrors the
Genre/Theme detail pipeline — flat, paginated game grid with the shared filter
drawer — while keeping a richer poster-hero header (logo, description, "By X",
stat cells, user progress) that's only justified for engines because they
carry metadata genres/themes don't. The list portion itself is identical to
the genre/theme pattern so users get a consistent game-row experience across
all three "tag-style" browse pages.
"""
import logging

from core.services.tracking import track_page_view
from django.db.models import (
    Q, F, Count, Avg, Subquery, OuterRef, IntegerField, FloatField,
)
from django.db.models.functions import Lower
from django.http import Http404
from django.urls import reverse_lazy
from django.views.generic import TemplateView

from trophies.mixins import ProfileHotbarMixin
from trophies.services import game_grouping_service as grouping
from ..models import GameEngine, Game, ConceptEngine
from .genre_views import TagDetailBaseView

logger = logging.getLogger("psn_api")


# Sort options for the engine browse page.
ENGINE_LIST_SORT_CHOICES = [
    ('alpha', 'Alphabetical'),
    ('games', 'Most Games'),
    ('avg_rating', 'Highest Avg Rating'),
    ('players', 'Most Players'),
    ('plats_earned', 'Most Platinums Earned'),
]


class EngineListView(ProfileHotbarMixin, TemplateView):
    """Browse page for game engines (Unreal, Unity, Decima, etc.)."""
    template_name = 'trophies/engine_list.html'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['breadcrumb'] = [
            {'text': 'Home', 'url': reverse_lazy('home')},
            {'text': 'Game Engines'},
        ]

        query = self.request.GET.get('query', '').strip()
        sort_val = self.request.GET.get('sort', 'alpha')

        # Each annotation runs as its own Subquery scoped through this
        # engine's ConceptEngine rows. Keeps the outer queryset shape at one
        # row per engine and avoids piling deep joins onto the sort.
        def _engine_subquery(output_field, **agg_kwargs):
            agg_name, agg_expr = next(iter(agg_kwargs.items()))
            return Subquery(
                ConceptEngine.objects.filter(engine=OuterRef('pk'))
                .values('engine')
                .annotate(**{agg_name: agg_expr})
                .values(agg_name)[:1],
                output_field=output_field,
            )

        # Require at least 2 linked games: historical data had one-off noise
        # (e.g. Photoshop incorrectly listed first for some obscure title).
        # Two-game minimum drops that noise without hiding legitimate niche
        # engines.
        items = GameEngine.objects.annotate(
            game_count=_engine_subquery(IntegerField(), c=Count('concept__games', distinct=True)),
        ).filter(game_count__gte=2)

        if query:
            items = items.filter(name__icontains=query)

        if sort_val == 'games':
            items = items.order_by('-game_count', Lower('name'))
        elif sort_val == 'avg_rating':
            items = items.annotate(
                _avg_rating=_engine_subquery(
                    FloatField(),
                    v=Avg('concept__user_ratings__overall_rating',
                          filter=Q(concept__user_ratings__concept_trophy_group__isnull=True)),
                ),
            ).order_by(F('_avg_rating').desc(nulls_last=True), Lower('name'))
        elif sort_val == 'players':
            items = items.annotate(
                _total_players=_engine_subquery(
                    IntegerField(),
                    c=Count('concept__games__played_by', distinct=True),
                ),
            ).order_by(F('_total_players').desc(nulls_last=True), Lower('name'))
        elif sort_val == 'plats_earned':
            items = items.annotate(
                _total_plats=_engine_subquery(
                    IntegerField(),
                    c=Count('concept__games__played_by',
                            filter=Q(concept__games__played_by__has_plat=True),
                            distinct=True),
                ),
            ).order_by(F('_total_plats').desc(nulls_last=True), Lower('name'))
        else:
            items = items.order_by(Lower('name'))

        context['items'] = items
        context['sort_choices'] = ENGINE_LIST_SORT_CHOICES
        context['current_sort'] = sort_val
        context['seo_description'] = (
            "Browse PlayStation games by the engine powering them. "
            "Find Unreal, Unity, Decima, and RE Engine titles on Platinum Pursuit."
        )

        track_page_view('engines_list', 'list', self.request)
        return context


class EngineDetailView(TagDetailBaseView):
    """Detail page for a single engine.

    Inherits the flat-list + filter-drawer pipeline from ``TagDetailBaseView``
    (shared with Genre / Theme detail) so game rows render with the full
    filter drawer and individual trophy-list cards. Layers the engine-specific
    poster hero on top: logo, description, "By [Company]", total stat cells,
    and viewer progress when authenticated.

    Hero totals are computed across the engine's entire game library via
    ``game_grouping_service.build_igdb_groups`` (same helper used by franchise
    / company hero). The list below the hero is paginated and filtered
    independently, so "this engine has 5,000 games" stays accurate even when
    you've narrowed the visible list to "PS5 only, rated 4+".
    """
    template_name = 'trophies/engine_detail.html'

    def dispatch(self, request, *args, **kwargs):
        self.engine = (
            GameEngine.objects.filter(slug=kwargs['slug'])
            .prefetch_related('companies')
            .first()
        )
        if not self.engine:
            raise Http404
        return super().dispatch(request, *args, **kwargs)

    def get_tag_filter(self):
        return Q(concept__concept_engines__engine=self.engine)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)

        # Identity + filter-drawer wiring. `tag_name` + `detail_url_name` +
        # `detail_slug` are what the shared filter drawer partial reads; the
        # other keys drive the hero.
        context['engine'] = self.engine
        context['engine_companies'] = list(self.engine.companies.all())
        context['tag_name'] = self.engine.name
        context['tag_type'] = 'Engine'
        context['detail_url_name'] = 'engine_detail'
        context['detail_slug'] = self.engine.slug
        context['breadcrumb'] = [
            {'text': 'Home', 'url': reverse_lazy('home')},
            {'text': 'Game Engines', 'url': reverse_lazy('engines_list')},
            {'text': self.engine.name},
        ]

        # Hero totals: computed across the engine's full game library, NOT
        # scoped by the active filter. Franchise/company pages do the same —
        # hero stats reflect the overall library while the list below is
        # filter-responsive. Skip on HTMX partial requests (only the list
        # gets swapped there, hero isn't re-rendered).
        is_htmx = bool(self.request.headers.get('HX-Request'))
        if not is_htmx:
            all_games = list(
                Game.objects.filter(self.get_tag_filter())
                .select_related('concept__igdb_match')
                .defer(
                    # See CLAUDE.md: defer the IGDB raw_response blob from
                    # cover-art querysets. Engine pages can list every game
                    # using a particular engine; the JSON blob is ~30 KB/row
                    # and unused by the card render.
                    'concept__igdb_match__raw_response',
                )
            )
            profile = (
                getattr(self.request.user, 'profile', None)
                if self.request.user.is_authenticated else None
            )
            user_progress_map = grouping.fetch_user_progress_map(profile, all_games)
            groups = grouping.build_igdb_groups(
                all_games, user_progress_map=user_progress_map,
            )

            total_trophies = sum(g['total_trophies'] for g in groups)
            context['total_games'] = len(groups)
            context['total_versions'] = sum(len(g['games']) for g in groups)
            context['total_trophies'] = total_trophies
            context['total_platinums'] = sum(1 for g in groups if g['has_platinum'])
            context['user_progress_stats'] = grouping.compute_user_progress_stats(
                groups, total_trophies, user_progress_map, profile=profile,
            )

        context['seo_description'] = (
            f"Explore PlayStation games built with {self.engine.name} on Platinum Pursuit."
        )

        context = self.get_shared_context(context)
        track_page_view('engine_detail', self.engine.id, self.request)
        return context
