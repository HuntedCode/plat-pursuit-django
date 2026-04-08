import logging

from core.services.tracking import track_page_view
from django.db.models import Q, Count, Avg, Subquery, OuterRef, Prefetch, Value, IntegerField, FloatField, Case, When
from django.db.models.functions import Lower
from django.http import Http404
from django.urls import reverse_lazy
from django.views.generic import ListView, TemplateView

from trophies.mixins import ProfileHotbarMixin, HtmxListMixin
from ..models import (
    Genre, Theme, GameEngine, Game, Trophy, Badge, UserConceptRating, ProfileGame,
)
from ..forms import GameSearchForm
from trophies.util_modules.constants import ALL_PLATFORMS

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

        if active_tab == 'themes':
            items = Theme.objects.annotate(
                game_count=Count('theme_concepts__concept__games', distinct=True),
            ).filter(game_count__gt=0)
            if query:
                items = items.filter(name__icontains=query)
            if sort_val == 'games':
                items = items.order_by('-game_count', 'name')
            else:
                items = items.order_by('name')
            context['items'] = items
            context['item_type'] = 'theme'
            context['detail_url_name'] = 'theme_detail'
        else:
            items = Genre.objects.annotate(
                game_count=Count('genre_concepts__concept__games', distinct=True),
            ).filter(game_count__gt=0)
            if query:
                items = items.filter(name__icontains=query)
            if sort_val == 'games':
                items = items.order_by('-game_count', 'name')
            else:
                items = items.order_by('name')
            context['items'] = items
            context['item_type'] = 'genre'
            context['detail_url_name'] = 'genre_detail'

        context['seo_description'] = (
            "Browse PlayStation games by genre and theme. "
            "Find shooters, RPGs, horror games, and more on Platinum Pursuit."
        )

        track_page_view('genres_list', 'list', self.request)
        return context


class TagDetailBaseView(HtmxListMixin, ProfileHotbarMixin, ListView):
    """Base view for genre and theme detail pages. Shares filter/sort logic."""
    model = Game
    partial_template_name = 'trophies/partials/genre_detail/browse_results.html'
    paginate_by = 30

    # IGDB time-to-beat stored in seconds
    TIME_BUCKETS = {
        'under10': (None, 36000),
        '10to25': (36000, 90000),
        '25to50': (90000, 180000),
        '50to100': (180000, 360000),
        '100plus': (360000, None),
    }
    COMMUNITY_TIME_BUCKETS = {
        'under10': (None, 10),
        '10to25': (10, 25),
        '25to50': (25, 50),
        '50to100': (50, 100),
        '100plus': (100, None),
    }

    def get_tag_filter(self):
        """Subclasses return the Q filter for their tag type."""
        raise NotImplementedError

    def get_filter_form(self):
        if not hasattr(self, '_filter_form'):
            self._filter_form = GameSearchForm(self.request.GET)
        return self._filter_form

    def get_queryset(self):
        qs = Game.objects.filter(self.get_tag_filter()).distinct()

        form = self.get_filter_form()
        order = ['is_ascii_name', Lower('title_name')]

        # Base annotations
        qs = qs.annotate(
            is_ascii_name=Case(
                When(title_name__regex=r'^[A-Za-z0-9]', then=Value(0)),
                default=Value(1),
                output_field=IntegerField(),
            ),
        )

        if form.is_valid():
            query = form.cleaned_data.get('query')
            platforms = form.cleaned_data.get('platform')
            regions = form.cleaned_data.get('regions')
            letter = form.cleaned_data.get('letter')
            sort_val = form.cleaned_data.get('sort')

            if query:
                from trophies.util_modules.roman_numerals import expand_numeral_query
                query_variants = expand_numeral_query(query)
                q_filter = Q()
                for variant in query_variants:
                    q_filter |= Q(title_name__icontains=variant)
                qs = qs.filter(q_filter)
            if platforms:
                qs = qs.for_platform(platforms)
            if regions:
                qs = qs.for_region(regions)
            if letter:
                if letter == '0-9':
                    qs = qs.filter(title_name__regex=r'^[0-9]')
                else:
                    qs = qs.filter(title_name__istartswith=letter)

            # Quick filters
            if form.cleaned_data.get('show_only_platinum'):
                qs = qs.filter(trophies__trophy_type='platinum').distinct()
            if form.cleaned_data.get('filter_shovelware'):
                qs = qs.exclude(shovelware_status__in=['auto_flagged', 'manually_flagged'])
            badge_series = form.cleaned_data.get('badge_series')
            if badge_series:
                qs = qs.filter(
                    concept__stages__series_slug=badge_series,
                    concept__stages__series_slug__in=Badge.objects.filter(
                        is_live=True,
                    ).values_list('series_slug', flat=True),
                ).distinct()
            elif form.cleaned_data.get('in_badge'):
                live_slugs = Badge.objects.filter(is_live=True).values_list('series_slug', flat=True)
                qs = qs.filter(concept__stages__series_slug__in=live_slugs).distinct()

            # Community flags
            if form.cleaned_data.get('show_delisted'):
                qs = qs.filter(is_delisted=True)
            if form.cleaned_data.get('show_unobtainable'):
                qs = qs.filter(is_obtainable=False)
            if form.cleaned_data.get('show_online'):
                qs = qs.filter(has_online_trophies=True)
            if form.cleaned_data.get('show_buggy'):
                qs = qs.filter(has_buggy_trophies=True)

            # Rating filters
            min_rating = form.cleaned_data.get('min_rating')
            difficulty_max = form.cleaned_data.get('difficulty_max')
            fun_min = form.cleaned_data.get('fun_min')
            needs_rating_annotation = min_rating or difficulty_max or fun_min or sort_val in ('rating', 'rating_inv', 'difficulty', 'difficulty_inv')

            if needs_rating_annotation:
                base_ratings = UserConceptRating.objects.filter(
                    concept_id=OuterRef('concept_id'),
                    concept_trophy_group__isnull=True,
                )
                qs = qs.annotate(
                    _avg_rating=Subquery(
                        base_ratings.values('concept_id').annotate(val=Avg('overall_rating')).values('val')[:1],
                        output_field=FloatField(),
                    ),
                    _avg_difficulty=Subquery(
                        base_ratings.values('concept_id').annotate(val=Avg('difficulty')).values('val')[:1],
                        output_field=FloatField(),
                    ),
                )
                if min_rating:
                    qs = qs.filter(_avg_rating__gte=float(min_rating))
                if difficulty_max:
                    qs = qs.filter(_avg_difficulty__lte=float(difficulty_max))
                if fun_min:
                    fun_sq = Subquery(
                        base_ratings.values('concept_id').annotate(val=Avg('fun_ranking')).values('val')[:1],
                        output_field=FloatField(),
                    )
                    qs = qs.annotate(_avg_fun=fun_sq).filter(_avg_fun__gte=float(fun_min))

            # Time-to-beat
            igdb_time = form.cleaned_data.get('igdb_time')
            if igdb_time and igdb_time in self.TIME_BUCKETS:
                lo, hi = self.TIME_BUCKETS[igdb_time]
                time_q = Q(concept__igdb_match__time_to_beat_completely__isnull=False)
                if lo is not None:
                    time_q &= Q(concept__igdb_match__time_to_beat_completely__gte=lo)
                if hi is not None:
                    time_q &= Q(concept__igdb_match__time_to_beat_completely__lt=hi)
                qs = qs.filter(time_q)

            community_time = form.cleaned_data.get('community_time')
            if community_time and community_time in self.COMMUNITY_TIME_BUCKETS:
                lo, hi = self.COMMUNITY_TIME_BUCKETS[community_time]
                avg_hours_sq = UserConceptRating.objects.filter(
                    concept_id=OuterRef('concept_id'),
                    concept_trophy_group__isnull=True,
                ).values('concept_id').annotate(val=Avg('hours_to_platinum')).values('val')[:1]
                qs = qs.annotate(_community_hours=Subquery(avg_hours_sq, output_field=FloatField()))
                hours_q = Q(_community_hours__isnull=False)
                if lo is not None:
                    hours_q &= Q(_community_hours__gte=lo)
                if hi is not None:
                    hours_q &= Q(_community_hours__lt=hi)
                qs = qs.filter(hours_q)

            # Engine filter
            engine = form.cleaned_data.get('engine')
            if engine:
                qs = qs.filter(concept__concept_engines__engine_id=engine).distinct()

            # Sort
            if sort_val == 'played':
                order = ['-played_count', 'is_ascii_name', Lower('title_name')]
            elif sort_val == 'played_inv':
                order = ['played_count', 'is_ascii_name', Lower('title_name')]
            elif sort_val == 'rating' and needs_rating_annotation:
                qs = qs.filter(_avg_rating__isnull=False)
                order = ['-_avg_rating', 'is_ascii_name', Lower('title_name')]
            elif sort_val == 'rating_inv' and needs_rating_annotation:
                qs = qs.filter(_avg_rating__isnull=False)
                order = ['_avg_rating', 'is_ascii_name', Lower('title_name')]
            elif sort_val == 'difficulty' and needs_rating_annotation:
                qs = qs.filter(_avg_difficulty__isnull=False)
                order = ['-_avg_difficulty', 'is_ascii_name', Lower('title_name')]
            elif sort_val == 'difficulty_inv' and needs_rating_annotation:
                qs = qs.filter(_avg_difficulty__isnull=False)
                order = ['_avg_difficulty', 'is_ascii_name', Lower('title_name')]
            elif sort_val == 'newest':
                order = ['-created_at', 'is_ascii_name', Lower('title_name')]
            elif sort_val == 'oldest':
                order = ['created_at', 'is_ascii_name', Lower('title_name')]

        qs = qs.prefetch_related(
            Prefetch('trophies', queryset=Trophy.objects.filter(trophy_type='platinum'), to_attr='platinum_trophy')
        )
        return qs.order_by(*order)

    def get_shared_context(self, context):
        """Adds filter form, platform choices, and post-pagination data."""
        # Total unfiltered game count for this tag (used in header flavor text)
        context['total_game_count'] = Game.objects.filter(
            self.get_tag_filter()
        ).distinct().count()

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
    template_name = 'trophies/genre_detail.html'

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
    template_name = 'trophies/theme_detail.html'

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
