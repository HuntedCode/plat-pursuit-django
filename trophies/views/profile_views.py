import logging
from datetime import timedelta

from core.services.tracking import track_page_view
from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.paginator import Paginator
from django.db.models import (
    Q, F, Prefetch, Max, Case, When, Value, IntegerField, FloatField,
    Subquery, OuterRef, Avg, OrderBy,
)
from django.db.models.functions import Lower, Coalesce, Cast
from django.http import HttpResponseRedirect, JsonResponse
from django.shortcuts import redirect, render, get_object_or_404
from django.urls import reverse, reverse_lazy
from django.utils import timezone
from django.utils.decorators import method_decorator
from django.views.generic import ListView, DetailView, View
from django_ratelimit.decorators import ratelimit
from urllib.parse import urlencode

from trophies.util_modules.cache import redis_client
from ..forms import (
    ProfileSearchForm,
    ProfileGamesForm,
    ProfileTrophiesForm,
    ProfileBadgesForm,
    LinkPSNForm,
)
from ..models import (
    Profile,
    EarnedTrophy,
    ProfileGame,
    UserTrophySelection,
    Badge,
    UserBadge,
    UserBadgeProgress,
    GameList,
    Challenge,
    Review,
    Trophy,
    UserConceptRating,
)
from trophies.mixins import ProfileHotbarMixin, HtmxListMixin
from .browse_helpers import annotate_community_ratings
from trophies.psn_manager import PSNManager

logger = logging.getLogger("psn_api")


class ProfilesListView(HtmxListMixin, ProfileHotbarMixin, ListView):
    """
    Display paginated list of user profiles with filtering and sorting.

    Provides profile browsing functionality with filters for:
    - Username search
    - Country
    - Sort options (trophies, platinums, games, completions, average progress)

    Useful for discovering other trophy hunters and viewing leaderboards.
    """
    model = Profile
    template_name = 'trophies/profile_list.html'
    partial_template_name = 'trophies/partials/profile_list/browse_results.html'
    paginate_by = 30

    def dispatch(self, request, *args, **kwargs):
        if not request.GET and request.user.is_authenticated:
            defaults = (request.user.browse_defaults or {}).get('profiles', {})
            if defaults:
                return HttpResponseRedirect(
                    reverse('profiles_list') + '?' + urlencode(defaults, doseq=True)
                )
        return super().dispatch(request, *args, **kwargs)

    def get_filter_form(self):
        if not hasattr(self, '_filter_form'):
            self._filter_form = ProfileSearchForm(self.request.GET)
        return self._filter_form

    def get_queryset(self):
        qs = super().get_queryset()
        form = self.get_filter_form()
        order = [Lower('psn_username')]

        # Always prefetch recent platinum (needed by template regardless of form state)
        recent_plat_qs = EarnedTrophy.objects.filter(earned=True, trophy__trophy_type='platinum').select_related('trophy', 'trophy__game', 'trophy__game__concept', 'trophy__game__concept__igdb_match').order_by(F('earned_date_time').desc(nulls_last=True))[:1]
        qs = qs.prefetch_related(Prefetch('earned_trophy_entries', queryset=recent_plat_qs, to_attr='recent_platinum'))

        if form.is_valid():
            query = form.cleaned_data.get('query')
            country = form.cleaned_data.get('country')
            sort_val = form.cleaned_data.get('sort')

            if query:
                qs = qs.filter(Q(psn_username__icontains=query))
            if country:
                qs = qs.filter(country_code=country)

            if sort_val == 'trophies':
                order = ['-total_trophies', Lower('psn_username')]
            elif sort_val == 'plats':
                order = ['-total_plats', Lower('psn_username')]
            elif sort_val == 'games':
                order = ['-total_games', Lower('psn_username')]
            elif sort_val == 'completes':
                order = ['-total_completes', Lower('psn_username')]
            elif sort_val == 'avg_progress':
                order = ['-avg_progress', Lower('psn_username')]
            elif sort_val == 'recently_active':
                order = [F('last_synced').desc(nulls_last=True), Lower('psn_username')]
            elif sort_val == 'badges_earned':
                qs = qs.annotate(
                    _badges_earned=Coalesce(
                        F('gamification__total_badges_earned'), Value(0),
                        output_field=IntegerField(),
                    ),
                )
                order = ['-_badges_earned', Lower('psn_username')]
            elif sort_val == 'badge_xp':
                qs = qs.annotate(
                    _badge_xp=Coalesce(
                        F('gamification__total_badge_xp'), Value(0),
                        output_field=IntegerField(),
                    ),
                )
                order = ['-_badge_xp', Lower('psn_username')]
            elif sort_val == 'rarest_avg_plat':
                plat_avg = Subquery(
                    EarnedTrophy.objects.filter(
                        profile_id=OuterRef('pk'),
                        earned=True,
                        trophy__trophy_type='platinum',
                    ).values('profile_id').annotate(
                        val=Avg('trophy__earn_rate'),
                    ).values('val')[:1],
                    output_field=FloatField(),
                )
                qs = qs.annotate(_avg_plat_rate=plat_avg)
                qs = qs.filter(_avg_plat_rate__isnull=False)
                order = ['_avg_plat_rate', Lower('psn_username')]
            elif sort_val == 'recently_joined':
                order = ['-created_at', Lower('psn_username')]

        return qs.order_by(*order)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        # Breadcrumb
        context['breadcrumb'] = [
            {'text': 'Home', 'url': reverse_lazy('home')},
            {'text': 'Profiles'},
        ]

        context['form'] = self.get_filter_form()
        context['selected_country'] = self.request.GET.get('country', '')

        context['seo_description'] = (
            "Browse PlayStation trophy hunter profiles and leaderboards on Platinum Pursuit."
        )

        track_page_view('profiles_list', 'list', self.request)
        return context


class ProfileDetailView(ProfileHotbarMixin, DetailView):
    """
    Display profile detail page with tabbed interface for games, trophies, and badges.

    Shows header stats, trophy case selections, and tab-specific content with
    filtering, sorting, and pagination.
    """
    model = Profile
    template_name = 'trophies/profile_detail.html'
    slug_field = 'psn_username'
    slug_url_kwarg = 'psn_username'
    context_object_name = 'profile'

    def get_object(self, queryset=None):
        psn_username = self.kwargs[self.slug_url_kwarg].lower()
        queryset = queryset or self.get_queryset()
        return get_object_or_404(queryset, **{self.slug_field: psn_username})

    def _build_header_stats(self, profile):
        """
        Build header statistics for profile.

        Args:
            profile: Profile instance

        Returns:
            dict: Header stats with trophy counts, completions, and notable trophies
        """
        header_stats = {
            'total_games': profile.total_games,
            'total_earned_trophies': profile.total_trophies,
            'total_unearned_trophies': profile.total_unearned,
            'total_completions': profile.total_completes,
            'average_completion': profile.avg_progress,
        }

        # Recent platinum
        if profile.recent_plat:
            header_stats['recent_platinum'] = {
                'trophy': profile.recent_plat.trophy,
                'game': profile.recent_plat.trophy.game,
                'earned_date': profile.recent_plat.earned_date_time,
            }
        else:
            header_stats['recent_platinum'] = None

        # Rarest platinum
        if profile.rarest_plat:
            header_stats['rarest_platinum'] = {
                'trophy': profile.rarest_plat.trophy,
                'game': profile.rarest_plat.trophy.game,
                'earned_date': profile.rarest_plat.earned_date_time,
            }
        else:
            header_stats['rarest_platinum'] = None

        # Fastest platinum (shortest play_duration on a game where plat was earned)
        fastest_plat_game = ProfileGame.objects.filter(
            profile=profile,
            has_plat=True,
            play_duration__isnull=False,
            play_duration__gt=timedelta(0),
        ).select_related('game', 'game__concept', 'game__concept__igdb_match').order_by('play_duration').first()

        if fastest_plat_game:
            fastest_plat_trophy = EarnedTrophy.objects.filter(
                profile=profile,
                trophy__game=fastest_plat_game.game,
                trophy__trophy_type='platinum',
                earned=True,
            ).select_related('trophy', 'trophy__game', 'trophy__game__concept', 'trophy__game__concept__igdb_match').first()
            if fastest_plat_trophy:
                header_stats['fastest_platinum'] = {
                    'trophy': fastest_plat_trophy.trophy,
                    'game': fastest_plat_game.game,
                    'play_duration': fastest_plat_game.play_duration,
                    'earned_date': fastest_plat_trophy.earned_date_time,
                }
            else:
                header_stats['fastest_platinum'] = None
        else:
            header_stats['fastest_platinum'] = None

        # Milestone platinum (most recent round-number plat: 10, 20, 30, etc.)
        header_stats['milestone_platinum'] = None
        if profile.total_plats >= 10:
            # Find the highest milestone reached (10, 20, 30, ...)
            milestone_number = (profile.total_plats // 10) * 10
            # Get the Nth platinum earned chronologically
            milestone_earned = EarnedTrophy.objects.filter(
                profile=profile,
                trophy__trophy_type='platinum',
                earned=True,
                earned_date_time__isnull=False,
            ).select_related('trophy', 'trophy__game', 'trophy__game__concept', 'trophy__game__concept__igdb_match').order_by('earned_date_time')

            # Use array slicing to get the Nth item (0-indexed)
            try:
                milestone_entry = milestone_earned[milestone_number - 1]
                header_stats['milestone_platinum'] = {
                    'trophy': milestone_entry.trophy,
                    'game': milestone_entry.trophy.game,
                    'milestone_number': milestone_number,
                    'earned_date': milestone_entry.earned_date_time,
                }
            except (IndexError, Exception):
                header_stats['milestone_platinum'] = None

        return header_stats

    def _build_trophy_case(self, profile):
        """
        Build trophy case selections list.

        Args:
            profile: Profile instance

        Returns:
            list: Trophy case selections padded to max_trophies
        """
        max_trophies = 10
        trophy_case = list(
            UserTrophySelection.objects.filter(profile=profile)
            .select_related('earned_trophy__trophy__game')
            .order_by('-earned_trophy__earned_date_time')
        )
        # Pad with None to reach max_trophies
        trophy_case = trophy_case + [None] * (max_trophies - len(trophy_case))
        return trophy_case

    def _build_badge_showcase(self, profile):
        """
        Build badge showcase data for the profile carousel (premium feature).

        Returns:
            list[dict]: Showcase badge data, or empty list
        """
        if not profile.user_is_premium:
            return []

        from trophies.models import ProfileBadgeShowcase
        showcase_entries = (
            ProfileBadgeShowcase.objects
            .filter(profile=profile)
            .select_related('badge', 'badge__base_badge', 'badge__most_recent_concept', 'badge__most_recent_concept__igdb_match')
            .order_by('display_order')
        )

        badges = []
        tier_names = {1: 'Bronze', 2: 'Silver', 3: 'Gold', 4: 'Platinum'}
        for entry in showcase_entries:
            badge = entry.badge
            try:
                layers = badge.get_badge_layers()
            except Exception:
                continue
            if not layers.get('has_custom_image'):
                continue
            concept = badge.most_recent_concept
            bg_url = concept.get_cover_url() if concept else ''
            badges.append({
                    'layers': layers,
                    'name': badge.effective_display_series or badge.series_slug,
                    'tier': badge.tier,
                    'tier_name': tier_names.get(badge.tier, ''),
                    'series_slug': badge.series_slug,
                    'bg_url': bg_url or '',
                })
        # Pad to 5 with None for placeholder rendering
        badges += [None] * (5 - len(badges))
        return badges

    def _build_timeline(self, profile):
        """
        Build timeline events for profile header.

        Args:
            profile: Profile instance

        Returns:
            list[dict] or None: Timeline events, or None if too few events
        """
        from trophies.services.timeline_service import get_cached_timeline_events
        return get_cached_timeline_events(profile)

    def _build_games_tab_context(self, profile, per_page, page_number):
        """
        Build context for games tab with filtering and pagination.

        Args:
            profile: Profile instance
            per_page: Items per page
            page_number: Current page number

        Returns:
            dict: Context with profile_games and form
        """
        form = ProfileGamesForm(self.request.GET)
        context = {'trophy_log': []}

        if not form.is_valid():
            context['profile_games'] = []
            context['form'] = form
            return context

        # Get form data
        query = form.cleaned_data.get('query')
        platforms = form.cleaned_data.get('platform')
        plat_status = form.cleaned_data.get('plat_status')
        sort_val = form.cleaned_data.get('sort')

        # Build queryset
        games_qs = profile.played_games.all().select_related('game').annotate(
            annotated_total_trophies=F('earned_trophies_count') + F('unearned_trophies_count')
        )

        # Apply profile settings
        if profile.hide_hiddens:
            games_qs = games_qs.exclude(user_hidden=True)
        if profile.hide_zeros:
            games_qs = games_qs.exclude(earned_trophies_count=0)

        # Apply filters
        if query:
            games_qs = games_qs.filter(Q(game__title_name__icontains=query))
        if platforms:
            platform_filter = Q()
            for plat in platforms:
                platform_filter |= Q(game__title_platform__contains=plat)
            games_qs = games_qs.filter(platform_filter)
            context['selected_platforms'] = platforms

        # Apply plat status filters
        if plat_status:
            if plat_status in ['plats', 'plats_100s', 'plats_no_100s']:
                games_qs = games_qs.platinum_earned()
            elif plat_status in ['no_plats', 'no_plats_100s']:
                games_qs = games_qs.filter(has_plat=False)
            if plat_status in ['100s', 'plats_100s', 'no_plats_100s']:
                games_qs = games_qs.completed()
            elif plat_status in ['no_100s']:
                games_qs = games_qs.exclude(progress=100)
            if plat_status == 'plats_no_100s':
                games_qs = games_qs.exclude(progress=100)

        # --- Genre / Theme filters ---
        genres = form.cleaned_data.get('genres')
        if genres:
            games_qs = games_qs.filter(
                game__concept__concept_genres__genre_id__in=genres,
            ).distinct()
        themes = form.cleaned_data.get('themes')
        if themes:
            games_qs = games_qs.filter(
                game__concept__concept_themes__theme_id__in=themes,
            ).distinct()

        # --- Completion range ---
        comp_min = form.cleaned_data.get('completion_min') or 0
        comp_max = form.cleaned_data.get('completion_max') or 100
        if comp_min > 0:
            games_qs = games_qs.filter(progress__gte=comp_min)
        if comp_max < 100:
            games_qs = games_qs.filter(progress__lte=comp_max)

        # --- Community flag filters ---
        if form.cleaned_data.get('show_delisted'):
            games_qs = games_qs.filter(game__is_delisted=True)
        if form.cleaned_data.get('show_unobtainable'):
            games_qs = games_qs.filter(game__is_obtainable=False)
        if form.cleaned_data.get('show_online'):
            games_qs = games_qs.filter(game__has_online_trophies=True)
        if form.cleaned_data.get('show_buggy'):
            games_qs = games_qs.filter(game__has_buggy_trophies=True)
        if form.cleaned_data.get('filter_shovelware'):
            games_qs = games_qs.exclude(
                game__shovelware_status__in=['auto_flagged', 'manually_flagged'],
            )

        # --- Community rating filters (dual-range) ---
        rating_min = form.cleaned_data.get('rating_min') or 0
        rating_max = form.cleaned_data.get('rating_max') or 5
        diff_min = form.cleaned_data.get('difficulty_min') or 1
        diff_max = form.cleaned_data.get('difficulty_max') or 10
        fun_lo = form.cleaned_data.get('fun_min') or 1
        fun_hi = form.cleaned_data.get('fun_max') or 10
        has_rating_filter = (
            rating_min > 0 or rating_max < 5
            or diff_min > 1 or diff_max < 10
            or fun_lo > 1 or fun_hi < 10
        )
        needs_rating = has_rating_filter or sort_val in ('rating', 'rating_inv')

        if needs_rating:
            games_qs = annotate_community_ratings(games_qs, 'game__concept_id')
            if rating_min > 0:
                games_qs = games_qs.filter(_avg_rating__gte=float(rating_min))
            if rating_max < 5:
                games_qs = games_qs.filter(_avg_rating__lte=float(rating_max))
            if diff_min > 1:
                games_qs = games_qs.filter(_avg_difficulty__gte=float(diff_min))
            if diff_max < 10:
                games_qs = games_qs.filter(_avg_difficulty__lte=float(diff_max))
            if fun_lo > 1:
                games_qs = games_qs.filter(_avg_fun__gte=float(fun_lo))
            if fun_hi < 10:
                games_qs = games_qs.filter(_avg_fun__lte=float(fun_hi))

        # --- Time-to-beat filter (dual-range, in hours) ---
        igdb_lo = form.cleaned_data.get('igdb_time_min') or 0
        igdb_hi = form.cleaned_data.get('igdb_time_max') or 1000
        if igdb_lo > 0 or igdb_hi < 1000:
            time_q = Q(game__concept__igdb_match__time_to_beat_completely__isnull=False)
            if igdb_lo > 0:
                time_q &= Q(game__concept__igdb_match__time_to_beat_completely__gte=int(igdb_lo) * 3600)
            if igdb_hi < 1000:
                time_q &= Q(game__concept__igdb_match__time_to_beat_completely__lte=int(igdb_hi) * 3600)
            games_qs = games_qs.filter(time_q)

        # --- Sort ---
        order = ['-last_updated_datetime']
        if sort_val == 'oldest':
            order = ['last_updated_datetime']
        elif sort_val == 'alpha':
            order = [Lower('game__title_name')]
        elif sort_val == 'completion':
            order = ['-progress', Lower('game__title_name')]
        elif sort_val == 'completion_inv':
            order = ['progress', Lower('game__title_name')]
        elif sort_val == 'trophies':
            order = ['-annotated_total_trophies', Lower('game__title_name')]
        elif sort_val == 'earned':
            order = ['-earned_trophies_count', Lower('game__title_name')]
        elif sort_val == 'unearned':
            order = ['-unearned_trophies_count', Lower('game__title_name')]
        elif sort_val == 'rating' and needs_rating:
            games_qs = games_qs.filter(_avg_rating__isnull=False)
            order = ['-_avg_rating', Lower('game__title_name')]
        elif sort_val == 'rating_inv' and needs_rating:
            games_qs = games_qs.filter(_avg_rating__isnull=False)
            order = ['_avg_rating', Lower('game__title_name')]
        elif sort_val in ('time_to_beat', 'time_to_beat_inv'):
            games_qs = games_qs.annotate(
                _time_to_beat=F('game__concept__igdb_match__time_to_beat_completely'),
            )
            if sort_val == 'time_to_beat':
                order = [OrderBy(F('_time_to_beat'), nulls_last=True)]
            else:
                order = [OrderBy(F('_time_to_beat'), descending=True, nulls_last=True)]
        elif sort_val in ('plat_rarest', 'plat_common'):
            plat_rate = Subquery(
                Trophy.objects.filter(
                    game_id=OuterRef('game_id'), trophy_type='platinum',
                ).values('earn_rate')[:1],
                output_field=FloatField(),
            )
            games_qs = games_qs.annotate(
                _plat_rate=Coalesce(plat_rate, Value(0.0), output_field=FloatField()),
            )
            if sort_val == 'plat_rarest':
                order = ['_plat_rate', Lower('game__title_name')]
            else:
                order = ['-_plat_rate', Lower('game__title_name')]
        elif sort_val in ('trophy_count', 'trophy_count_inv'):
            games_qs = games_qs.annotate(
                _defined_trophy_count=(
                    Coalesce(Cast(F('game__defined_trophies__bronze'), IntegerField()), Value(0))
                    + Coalesce(Cast(F('game__defined_trophies__silver'), IntegerField()), Value(0))
                    + Coalesce(Cast(F('game__defined_trophies__gold'), IntegerField()), Value(0))
                    + Coalesce(Cast(F('game__defined_trophies__platinum'), IntegerField()), Value(0))
                ),
            )
            if sort_val == 'trophy_count':
                order = ['-_defined_trophy_count', Lower('game__title_name')]
            else:
                order = ['_defined_trophy_count', Lower('game__title_name')]

        games_qs = games_qs.order_by(*order)

        # Paginate
        games_paginator = Paginator(games_qs, per_page)
        if int(page_number) > games_paginator.num_pages:
            game_page_obj = []
        else:
            game_page_obj = games_paginator.get_page(page_number)

        context['profile_games'] = game_page_obj
        context['form'] = form
        context['selected_genres'] = self.request.GET.getlist('genres')
        context['selected_themes'] = self.request.GET.getlist('themes')
        return context

    def _build_trophies_tab_context(self, profile, per_page, page_number):
        """
        Build context for trophies tab with filtering and pagination.

        Args:
            profile: Profile instance
            per_page: Items per page
            page_number: Current page number

        Returns:
            dict: Context with trophy_log and form
        """
        form = ProfileTrophiesForm(self.request.GET)
        context = {'profile_games': []}

        if not form.is_valid():
            context['trophy_log'] = []
            context['form'] = form
            return context

        # Get form data
        query = form.cleaned_data.get('query')
        platforms = form.cleaned_data.get('platform')
        trophy_type = form.cleaned_data.get('type')
        sort_val = form.cleaned_data.get('sort', 'recent')

        # Build queryset
        trophies_qs = profile.earned_trophy_entries.filter(earned=True).select_related(
            'trophy', 'trophy__game',
        )

        # Apply filters
        if query:
            trophies_qs = trophies_qs.filter(
                Q(trophy__trophy_name__icontains=query) | Q(trophy__game__title_name__icontains=query)
            )
        if platforms:
            platform_filter = Q()
            for plat in platforms:
                platform_filter |= Q(trophy__game__title_platform__contains=plat)
            trophies_qs = trophies_qs.filter(platform_filter)
            context['selected_platforms'] = platforms
        if trophy_type:
            trophies_qs = trophies_qs.filter(trophy__trophy_type=trophy_type)

        # Rarity range filter (PSN earn rate, 0-100%)
        rarity_min = form.cleaned_data.get('rarity_min') or 0
        rarity_max = form.cleaned_data.get('rarity_max') or 100
        if rarity_min > 0:
            trophies_qs = trophies_qs.filter(trophy__trophy_earn_rate__gte=float(rarity_min))
        if rarity_max < 100:
            trophies_qs = trophies_qs.filter(trophy__trophy_earn_rate__lte=float(rarity_max))

        # Sort
        if sort_val == 'oldest':
            trophies_qs = trophies_qs.order_by(
                F('earned_date_time').asc(nulls_last=True),
            )
        elif sort_val == 'alpha':
            trophies_qs = trophies_qs.order_by(
                Lower('trophy__trophy_name'),
            )
        elif sort_val == 'rarest_psn':
            trophies_qs = trophies_qs.order_by(
                'trophy__trophy_earn_rate',
                F('earned_date_time').desc(nulls_last=True),
            )
        elif sort_val == 'common_psn':
            trophies_qs = trophies_qs.order_by(
                '-trophy__trophy_earn_rate',
                F('earned_date_time').desc(nulls_last=True),
            )
        elif sort_val == 'rarest_pp':
            trophies_qs = trophies_qs.order_by(
                'trophy__earn_rate',
                F('earned_date_time').desc(nulls_last=True),
            )
        elif sort_val == 'common_pp':
            trophies_qs = trophies_qs.order_by(
                '-trophy__earn_rate',
                F('earned_date_time').desc(nulls_last=True),
            )
        elif sort_val == 'type':
            trophies_qs = trophies_qs.annotate(
                _type_order=Case(
                    When(trophy__trophy_type='platinum', then=Value(0)),
                    When(trophy__trophy_type='gold', then=Value(1)),
                    When(trophy__trophy_type='silver', then=Value(2)),
                    When(trophy__trophy_type='bronze', then=Value(3)),
                    default=Value(4),
                    output_field=IntegerField(),
                ),
            ).order_by(
                '_type_order',
                Lower('trophy__trophy_name'),
            )
        else:  # 'recent' (default)
            trophies_qs = trophies_qs.order_by(
                F('earned_date_time').desc(nulls_last=True),
            )

        # Paginate
        trophy_paginator = Paginator(trophies_qs, per_page)
        if int(page_number) > trophy_paginator.num_pages:
            trophy_page_obj = []
        else:
            trophy_page_obj = trophy_paginator.get_page(page_number)

        context['trophy_log'] = trophy_page_obj
        context['form'] = form
        return context

    @staticmethod
    def _compute_badge_xp(badge_group):
        """Compute total XP value for a badge group's highest earned tier."""
        from trophies.services.xp_service import get_tier_xp
        from trophies.util_modules.constants import BADGE_TIER_XP
        badge = badge_group['highest_badge']
        return badge.required_stages * get_tier_xp(badge.tier) + BADGE_TIER_XP

    def _sort_badge_groups(self, badge_list, sort_val):
        """Apply consistent sorting to a list of badge group dicts."""
        _title = lambda d: (d['highest_badge'].effective_display_title or '').lower()
        if sort_val == 'name':
            badge_list.sort(key=lambda d: _title(d))
        elif sort_val == 'tier':
            badge_list.sort(key=lambda d: (d['max_tier'], _title(d)))
        elif sort_val == 'tier_desc':
            badge_list.sort(key=lambda d: (-d['max_tier'], _title(d)))
        elif sort_val == 'stages':
            badge_list.sort(key=lambda d: (-d['highest_badge'].required_stages, _title(d)))
        elif sort_val == 'stages_inv':
            badge_list.sort(key=lambda d: (d['highest_badge'].required_stages, _title(d)))
        elif sort_val == 'xp':
            badge_list.sort(key=lambda d: (-self._compute_badge_xp(d), _title(d)))
        elif sort_val == 'xp_inv':
            badge_list.sort(key=lambda d: (self._compute_badge_xp(d), _title(d)))
        elif sort_val == 'recent':
            from datetime import datetime
            badge_list.sort(
                key=lambda d: d.get('earned_at') or datetime.min,
                reverse=True,
            )
        else:  # 'series' (default)
            badge_list.sort(key=lambda d: (d['highest_badge'].effective_display_series or '').lower())

    def _build_badges_tab_context(self, profile):
        """
        Build context for badges tab with earned badges, in-progress badges, and progress.

        Args:
            profile: Profile instance

        Returns:
            dict: Context with grouped_earned_badges, in_progress_badges, and form
        """
        form = ProfileBadgesForm(self.request.GET)
        context = {}

        if not form.is_valid():
            context['grouped_earned_badges'] = []
            context['in_progress_badges'] = []
            context['form'] = form
            return context

        sort_val = form.cleaned_data.get('sort')
        badge_type_filter = form.cleaned_data.get('badge_type')
        tier_filter_val = form.cleaned_data.get('tier')

        # Get earned badge series with max tier per series
        earned_badges_qs = UserBadge.objects.filter(profile=profile).values(
            'badge__series_slug'
        ).annotate(max_tier=Max('badge__tier')).distinct()

        # Collect all series slugs and needed tiers for bulk fetch
        series_tier_pairs = []
        earned_series_slugs = set()
        for entry in earned_badges_qs:
            slug = entry['badge__series_slug']
            max_tier = entry['max_tier']
            earned_series_slugs.add(slug)
            series_tier_pairs.append((slug, max_tier))
            series_tier_pairs.append((slug, max_tier + 1))  # next tier

        # Bulk fetch all needed Badge objects in one query
        if series_tier_pairs:
            tier_filter = Q()
            for slug, tier in series_tier_pairs:
                tier_filter |= Q(series_slug=slug, tier=tier)
            all_badges = Badge.objects.live().filter(tier_filter).select_related(
                'base_badge', 'title', 'base_badge__title'
            )
            badge_lookup = {(b.series_slug, b.tier): b for b in all_badges}
        else:
            badge_lookup = {}

        # Bulk fetch all UserBadgeProgress for this profile
        progress_lookup = {
            p.badge_id: p
            for p in UserBadgeProgress.objects.filter(profile=profile).select_related('badge')
        }

        # Bulk fetch earned_at per series (for "Recently Earned" sort)
        earned_at_lookup = {}
        if sort_val == 'recent':
            for ub in UserBadge.objects.filter(profile=profile).values(
                'badge__series_slug',
            ).annotate(latest_earned=Max('earned_at')):
                earned_at_lookup[ub['badge__series_slug']] = ub['latest_earned']

        # Build earned badges list using lookups instead of per-item queries
        grouped_earned = []
        for entry in earned_badges_qs:
            series_slug = entry['badge__series_slug']
            max_tier = entry['max_tier']
            highest_badge = badge_lookup.get((series_slug, max_tier))
            if not highest_badge:
                continue

            next_badge = badge_lookup.get((series_slug, max_tier + 1))
            is_maxed = next_badge is None
            if is_maxed:
                next_badge = highest_badge

            progress_entry = progress_lookup.get(next_badge.id)
            if progress_entry and next_badge.required_stages > 0:
                progress_percentage = (progress_entry.completed_concepts / next_badge.required_stages) * 100
            else:
                progress_percentage = 0
            if is_maxed:
                progress_percentage = 100

            grouped_earned.append({
                'highest_badge': highest_badge,
                'next_badge': next_badge,
                'progress': progress_entry,
                'percentage': progress_percentage,
                'max_tier': max_tier,
                'earned_at': earned_at_lookup.get(series_slug),
            })

        # In-memory filters
        if badge_type_filter:
            grouped_earned = [
                g for g in grouped_earned
                if g['highest_badge'].badge_type in badge_type_filter
            ]
        if tier_filter_val:
            tier_ints = [int(t) for t in tier_filter_val]
            grouped_earned = [
                g for g in grouped_earned
                if g['max_tier'] in tier_ints
            ]

        self._sort_badge_groups(grouped_earned, sort_val)
        context['grouped_earned_badges'] = grouped_earned

        # Build in-progress badges (tier 1, some progress, not yet earned)
        in_progress_qs = UserBadgeProgress.objects.filter(
            profile=profile,
            badge__tier=1,
            completed_concepts__gt=0,
        ).exclude(
            badge__series_slug__in=earned_series_slugs,
        ).select_related('badge', 'badge__base_badge', 'badge__title', 'badge__base_badge__title')

        earned_badge_ids = {b.id for b in badge_lookup.values()}

        in_progress_badges = []
        for progress in in_progress_qs:
            badge = progress.badge
            if badge.id in earned_badge_ids:
                continue

            if badge.required_stages > 0:
                percentage = (progress.completed_concepts / badge.required_stages) * 100
            else:
                percentage = 0

            in_progress_badges.append({
                'highest_badge': badge,
                'next_badge': badge,
                'progress': progress,
                'percentage': percentage,
                'max_tier': 0,
            })

        in_progress_badges.sort(key=lambda d: (-d['percentage'], (d['highest_badge'].effective_display_title or '').lower()))
        context['in_progress_badges'] = in_progress_badges
        context['form'] = form
        context['selected_badge_types'] = self.request.GET.getlist('badge_type')
        context['selected_tiers'] = self.request.GET.getlist('tier')
        return context

    def _build_lists_tab_context(self, public_lists_qs):
        """Build context for lists tab — public game lists for this profile."""
        return {'profile_lists': public_lists_qs.order_by('-like_count', '-created_at')}

    def _build_challenges_tab_context(self, profile):
        """Build context for challenges tab — all challenges by this profile."""
        challenges = Challenge.objects.filter(
            profile=profile, is_deleted=False
        ).order_by('-created_at')
        return {'profile_challenges': challenges}

    def _build_reviews_tab_context(self, profile, per_page, page_number):
        """Build context for reviews tab — reviews written by this profile."""
        reviews_qs = Review.objects.filter(
            profile=profile, is_deleted=False
        ).select_related(
            'concept_trophy_group__concept'
        ).order_by('-created_at')

        paginator = Paginator(reviews_qs, per_page)
        if int(page_number) > paginator.num_pages:
            page_obj = []
        else:
            page_obj = paginator.get_page(page_number)

        return {'profile_reviews': page_obj}

    def get_context_data(self, **kwargs):
        """Build context for profile detail page with tab-specific content.

        This method delegates to tab-specific helper methods to keep the code
        organized and maintainable. Each tab (games, trophies, badges) has its
        own focused handler method.

        Args:
            **kwargs: Standard Django context keyword arguments

        Returns:
            dict: Context dictionary with profile data, tab content, and metadata
        """
        context = super().get_context_data(**kwargs)
        profile: Profile = self.object
        tab = self.request.GET.get('tab', 'games')
        per_page = 50
        page_number = self.request.GET.get('page', 1)

        # Efficiently load profile with denormalized plat FKs
        profile = Profile.objects.select_related(
            'recent_plat__trophy__game', 'rarest_plat__trophy__game'
        ).get(id=profile.id)

        # Build shared context (header stats, trophy case, badge showcase, and timeline)
        context['header_stats'] = self._build_header_stats(profile)
        context['trophy_case'] = self._build_trophy_case(profile)
        context['trophy_case_count'] = len(context['trophy_case'])
        showcase_badges = self._build_badge_showcase(profile)
        context['profile_showcase_badges'] = showcase_badges
        context['has_showcase_badges'] = any(b for b in showcase_badges)
        if profile.psn_history_public:
            context['timeline_events'] = self._build_timeline(profile)

        # Public game lists count (shown in tab header regardless of active tab)
        public_lists_qs = GameList.objects.filter(profile=profile, is_public=True, is_deleted=False)
        context['profile_lists_count'] = public_lists_qs.count()

        # Challenge and review counts (shown in tab headers and quick links)
        context['profile_challenge_count'] = Challenge.objects.filter(profile=profile, is_deleted=False).count()
        context['profile_review_count'] = Review.objects.filter(profile=profile, is_deleted=False).count()

        # Delegate to tab-specific handler methods
        if tab == 'games':
            tab_context = self._build_games_tab_context(profile, per_page, page_number)
        elif tab == 'trophies':
            tab_context = self._build_trophies_tab_context(profile, per_page, page_number)
        elif tab == 'badges':
            tab_context = self._build_badges_tab_context(profile)
        elif tab == 'lists':
            tab_context = self._build_lists_tab_context(public_lists_qs)
        elif tab == 'challenges':
            tab_context = self._build_challenges_tab_context(profile)
        elif tab == 'reviews':
            tab_context = self._build_reviews_tab_context(profile, per_page, page_number)
        else:
            # Default to games tab if invalid tab specified
            tab_context = self._build_games_tab_context(profile, per_page, page_number)

        context.update(tab_context)

        # Add shared metadata
        context['breadcrumb'] = [
            {'text': 'Home', 'url': reverse_lazy('home')},
            {'text': 'Profiles', 'url': reverse_lazy('profiles_list')},
            {'text': f"{profile.display_psn_username}"}
        ]
        context['current_tab'] = tab

        # Tab template mapping for {% include %} and HTMX partial returns
        tab_templates = {
            'games': 'trophies/partials/profile_detail/tabs/games_tab.html',
            'trophies': 'trophies/partials/profile_detail/tabs/trophies_tab.html',
            'badges': 'trophies/partials/profile_detail/tabs/badges_tab.html',
            'lists': 'trophies/partials/profile_detail/tabs/lists_tab.html',
            'challenges': 'trophies/partials/profile_detail/tabs/challenges_tab.html',
            'reviews': 'trophies/partials/profile_detail/tabs/reviews_tab.html',
        }
        context['tab_template'] = tab_templates.get(tab, tab_templates['games'])

        # Premium profile personalization
        if profile.user_is_premium:
            # Theme accent colors
            if profile.selected_theme:
                from trophies.themes import get_theme, get_theme_css
                theme = get_theme(profile.selected_theme)
                if theme:
                    context['profile_theme_accent'] = theme['accent_color']
                    context['profile_theme_gradient'] = get_theme_css(profile.selected_theme)

            # Profile banner image from selected background concept
            if profile.selected_background and profile.selected_background.bg_url:
                context['profile_banner_url'] = profile.selected_background.bg_url
                context['profile_banner_position'] = profile.banner_position
                import json
                context['profile_banner_data_json'] = json.dumps({
                    'concept_id': profile.selected_background.id,
                    'title_name': profile.selected_background.unified_title or '',
                    'icon_url': profile.selected_background.concept_icon_url or '',
                    'bg_url': profile.selected_background.bg_url or '',
                })

        # Own profile check (for edit controls)
        context['is_own_profile'] = (
            self.request.user.is_authenticated and
            hasattr(self.request.user, 'profile') and
            self.request.user.profile == profile
        )

        context['seo_description'] = (
            f"{profile.display_psn_username}'s PlayStation trophy profile. "
            f"Level {profile.trophy_level}, {profile.total_trophies} trophies, "
            f"{profile.total_games} games."
        )

        track_page_view('profile', profile.id, self.request)
        context['view_count'] = profile.view_count

        return context

    # Template maps for HTMX partial responses
    _TAB_TEMPLATES = {
        'games': 'trophies/partials/profile_detail/tabs/games_tab.html',
        'trophies': 'trophies/partials/profile_detail/tabs/trophies_tab.html',
        'badges': 'trophies/partials/profile_detail/tabs/badges_tab.html',
        'lists': 'trophies/partials/profile_detail/tabs/lists_tab.html',
        'challenges': 'trophies/partials/profile_detail/tabs/challenges_tab.html',
        'reviews': 'trophies/partials/profile_detail/tabs/reviews_tab.html',
    }
    _RESULTS_TEMPLATES = {
        'games': 'trophies/partials/profile_detail/tabs/games_results.html',
        'trophies': 'trophies/partials/profile_detail/tabs/trophies_results.html',
        'badges': 'trophies/partials/profile_detail/tabs/badges_results.html',
    }
    _INFINITE_SCROLL_TEMPLATES = {
        'games': 'trophies/partials/profile_detail/game_list_items.html',
        'trophies': 'trophies/partials/profile_detail/trophy_list_items.html',
        'reviews': 'trophies/partials/profile_detail/review_list_items.html',
    }

    def get_template_names(self):
        tab = self.request.GET.get('tab', 'games')

        # HTMX partial swap (tab switch or filter change)
        if getattr(self.request, 'htmx', False):
            target = self.request.htmx.target
            if target == 'tab-results' and tab in self._RESULTS_TEMPLATES:
                return [self._RESULTS_TEMPLATES[tab]]
            if tab in self._TAB_TEMPLATES:
                return [self._TAB_TEMPLATES[tab]]

        # Infinite scroll (XMLHttpRequest from InfiniteScroller)
        if self.request.headers.get('X-Requested-With') == 'XMLHttpRequest':
            if tab in self._INFINITE_SCROLL_TEMPLATES:
                return [self._INFINITE_SCROLL_TEMPLATES[tab]]

        return super().get_template_names()


class LinkPSNView(LoginRequiredMixin, View):
    """
    Multi-step view for linking PSN account to web account.

    Steps:
    1. User enters PSN username
    2. System generates verification code and syncs profile
    3. User adds code to PSN "About Me" section
    4. System verifies code presence via PSN API
    5. Profile is linked to authenticated user account

    Handles profile creation, sync, verification code generation, and final verification.
    """
    template_name = 'account/link_psn.html'
    login_url = reverse_lazy('login')
    form_class = LinkPSNForm

    def get(self, request):
        if hasattr(request.user, 'profile') and request.user.profile.is_linked:
            messages.info(request, 'This PSN account is already linked to a web account.')
            return redirect('link_psn')

        form = self.form_class()
        context = {'form': form, 'step': 1}
        return render(request, self.template_name, context)

    def post(self, request):
        action = request.POST.get('action')
        psn_outage = bool(redis_client.get('site:psn_outage'))

        if action == 'submit_username':
            form = self.form_class(request.POST)
            if form.is_valid():
                psn_username = form.cleaned_data['psn_username'].lower().strip()
                try:
                    profile, created = Profile.objects.get_or_create(psn_username=psn_username)
                    if profile.user and profile.user != request.user:
                        raise ValueError('This PSN account is already linked to another user.')

                    time_since_last_sync = profile.get_time_since_last_sync()
                    if created:
                        PSNManager.initial_sync(profile)
                    else:
                        profile.attempt_sync()

                    if not profile.verification_code or profile.verification_expires_at < timezone.now():
                        profile.generate_verification_code()

                    if psn_outage:
                        messages.warning(
                            request,
                            'PlayStation Network is currently unavailable. '
                            'Verification will not work until PSN recovers. '
                            'Your code has been generated and will be ready when service returns.'
                        )

                    context = {
                        'form': form,
                        'step': 2,
                        'verification_code': profile.verification_code,
                        'profile': profile,
                    }
                    return render(request, self.template_name, context)
                except ValueError as e:
                    messages.error(request, str(e))
                except Exception as e:
                    messages.error(request, 'An error occured during sync. Please try again later.')
            return render(request, self.template_name, {'form': form, 'step': 1})
        elif action == 'verify':
            if psn_outage:
                form = self.form_class(request.POST)
                psn_username = form.data.get('psn_username', '')
                messages.error(
                    request,
                    'PlayStation Network is currently unavailable. '
                    'Please try verifying again once service recovers.'
                )
                try:
                    profile = Profile.objects.get(psn_username=psn_username.lower())
                    return render(request, self.template_name, {
                        'form': self.form_class(initial={'psn_username': psn_username}),
                        'step': 2,
                        'verification_code': profile.verification_code,
                        'profile': profile,
                    })
                except Profile.DoesNotExist:
                    return redirect('link_psn')

            form = self.form_class(request.POST)
            if form.is_valid():
                psn_username = form.cleaned_data['psn_username'].lower()
                try:
                    start_time = timezone.now().timestamp()
                    profile = Profile.objects.get(psn_username=psn_username.lower())
                    is_syncing = profile.attempt_sync()
                    if not is_syncing:
                        PSNManager.sync_profile_data(profile)

                    messages.info(request, "Verification in progress...")
                    context = {
                        'form': self.form_class(initial={'psn_username': psn_username}),
                        'step': 3,
                        'verification_code': profile.verification_code,
                        'profile': profile,
                        'start_time': str(start_time),
                    }
                    return render(request, self.template_name, context)
                except Profile.DoesNotExist:
                    messages.error(request, "Profile not found. Please start over.")
                    return redirect('link_psn')
                except Exception as e:
                    messages.error(request, f"An error occurred during verification. Please try again.")
                    return render(request, self.template_name, {'form': self.form_class(initial={'psn_username': psn_username}), 'step': 2})

        return redirect('link_psn')


class ProfileVerifyView(LoginRequiredMixin, View):
    """
    AJAX endpoint for polling PSN verification status during link flow.

    Checks if profile has been synced since verification started and
    if verification code appears in PSN "About Me" section.
    Links profile to user account upon successful verification.

    Rate limited to 60 requests per minute per user.
    """
    @method_decorator(ratelimit(key='user', rate='60/m', method='GET'))
    def get(self, request):
        if redis_client.get('site:psn_outage'):
            return JsonResponse({
                'psn_outage': True,
                'error': 'PlayStation Network is currently unavailable. '
                         'Verification will resume when PSN recovers.',
            }, status=503)

        user = request.user
        profile_id = request.GET.get('profile_id')
        start_time = request.GET.get('start_time')
        if not profile_id:
            return JsonResponse({'error': 'Profile id required'}, status=400)
        if not start_time:
            return JsonResponse({'error': 'start_time required'}, status=400)

        try:
            start_time_float = float(start_time)
        except ValueError:
            return JsonResponse({'error': 'Invalid start_time format'}, status=400)

        try:
            profile = Profile.objects.get(id=profile_id)
        except Profile.DoesNotExist:
            return JsonResponse({'error': 'Profile not found'}, status=404)

        if profile.sync_status == 'error':
            return JsonResponse({'error': 'Sync error. Make sure your "Gaming History" permission is set to "Anybody"'}, status=400)

        verified = False
        synced = False
        if profile.last_synced.timestamp() > start_time_float:
            synced = True
            verified = profile.verify_code(profile.about_me)
            if verified:
                profile.link_to_user(user)

        return JsonResponse({
            'synced': synced,
            'verified': verified,
        })

