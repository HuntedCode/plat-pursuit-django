import logging
from datetime import timedelta

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.paginator import Paginator
from django.db.models import (
    Q, F, Max, Case, When, Value, IntegerField, FloatField,
    Subquery, OuterRef, OrderBy,
)
from django.db.models.functions import Lower, Coalesce, Cast
from django.http import HttpResponseRedirect, JsonResponse
from django.shortcuts import redirect, render, get_object_or_404
from django.urls import reverse, reverse_lazy
from django.utils import timezone
from django.utils.decorators import method_decorator
from django.views.generic import ListView, DetailView, View, TemplateView
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
    Trophy,
    UserConceptRating,
    UserTitle,
)
from trophies.mixins import HtmxListMixin
from .browse_helpers import annotate_community_ratings
from trophies.psn_manager import PSNManager

logger = logging.getLogger("psn_api")


class ProfilesListView(HtmxListMixin, ListView):
    """Browse hunters at `/hunters/` -- a DISCOVERY surface, not a ranking one.

    Rebuilt 2026-08. The framing is the load-bearing decision: `/leaderboards/` owns ranking (it already
    boards hunters by badges), so this page is a directory of PEOPLE -- who is here, who is active, who
    just arrived -- and everything that made it a second scoreboard was removed rather than restyled.

    What that cost the sort list, and why each one went:

    - `badges_earned` / `badge_xp` -> Leaderboards. Duplicating that hub's job here is what made the two
      surfaces read as the same page twice.
    - `rarest_avg_plat` -> dropped. A connoisseur ranking, and the only sort with no index behind it: a
      correlated AVG over every EarnedTrophy row per profile, sitewide.
    - `games` / `completes` / `avg_progress` -> dropped. Three more "who is biggest" orderings on a page
      that is no longer about biggest.

    The five that remain (alphabetical, recently active, recently joined, trophies, platinums) are each
    served by an index on Profile, so ordering never costs a scan.

    Card data is deliberately thin -- identity plus two proof stats. `recent_platinum` used to be
    prefetched for every row and is gone: the card is meant to scan, not to brief.
    """
    model = Profile
    template_name = 'trophies/profile_list.html'
    partial_template_name = 'trophies/partials/profile_list/browse_results.html'
    paginate_by = 30

    #: Ordering per sort value. Every one is index-backed; `Lower('psn_username')` is the stable
    #: tie-breaker so equal stats never shuffle between pages of the same result set.
    SORTS = {
        'alpha': [Lower('psn_username')],
        'recently_active': [F('last_synced').desc(nulls_last=True), Lower('psn_username')],
        'recently_joined': ['-created_at', Lower('psn_username')],
        'trophies': ['-total_trophies', Lower('psn_username')],
        'plats': ['-total_plats', Lower('psn_username')],
    }
    DEFAULT_SORT = 'alpha'

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

        # The displayed title, folded into the ROW rather than fetched per card. `Profile.displayed_title`
        # is a method that runs `user_titles.filter(...).first()` and then hops the `title` FK -- two
        # queries per profile, so ~60 on a 30-card page purely to print a word under each name. The
        # subquery is served by `usertitle_display_idx` (profile, is_displayed) and costs no extra round
        # trip. Templates read `display_title`; the method stays for callers rendering ONE profile.
        qs = qs.annotate(
            display_title=Subquery(
                UserTitle.objects
                .filter(profile_id=OuterRef('pk'), is_displayed=True)
                .values('title__name')[:1]
            ),
        )
        # Only what a card draws. Profile is a wide row (sync bookkeeping, JSON summaries, PSN payloads)
        # and none of it is on this page; without this the page pays to haul all of it 30 times.
        qs = qs.only(
            # `country_code` is deliberately absent: the card shows `flag` (which holds the code) and the
            # only other reader was a `title` attribute dropped when the code stopped being aria-hidden.
            # Anything added here must be READ by the card, and anything the card reads must be here --
            # a miss is a per-row deferred fetch, i.e. an N+1 wearing a different hat.
            'psn_username', 'display_psn_username', 'avatar_url', 'flag',
            'trophy_level', 'total_trophies', 'total_plats', 'total_games', 'user_is_premium',
            'last_synced', 'created_at',
        )

        # ONE bad field must not take the others down with it. `is_valid()` is all-or-nothing, and both
        # things a stale `browse_defaults` can carry -- a retired sort, or a country that no longer has any
        # hunters in the choice list -- invalidate the whole form. Gating the filters on it meant a hunter
        # whose saved default named a since-removed sort also silently lost their SEARCH, which is a much
        # stranger failure than landing on the default order. So the filters fall back to the raw params;
        # both are parameterised by the ORM, and an unknown country simply matches nothing.
        if form.is_valid():
            query = form.cleaned_data.get('query') or ''
            country = form.cleaned_data.get('country') or ''
        else:
            query = self.request.GET.get('query', '').strip()
            country = self.request.GET.get('country', '').strip()

        if query:
            qs = qs.filter(Q(psn_username__icontains=query))
        if country:
            qs = qs.filter(country_code=country)

        # Read from the raw param against our own map rather than from `cleaned_data`, for the same
        # reason: an unrecognised sort resolves to the default instead of invalidating anything.
        order = self.SORTS.get(self.request.GET.get('sort'), self.SORTS[self.DEFAULT_SORT])
        return qs.order_by(*order)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        # Breadcrumb
        context['breadcrumb'] = [
            {'text': 'Home', 'url': reverse_lazy('home')},
            {'text': 'Hunters'},
        ]

        context['form'] = self.get_filter_form()
        context['selected_country'] = self.request.GET.get('country', '')

        # The active sort, resolved the SAME way the queryset resolves it (raw param against the map, with
        # an unknown value falling back rather than invalidating). The card's third stat follows this, so
        # the wall always shows the axis it is ordered by -- the page defaults to Recently Active, and
        # without this it sorts by something no card displays. Set in `get_context_data` so the HTMX
        # partial gets it too; the grid is what re-renders on a sort change.
        sort = self.request.GET.get('sort')
        context['active_sort'] = sort if sort in self.SORTS else self.DEFAULT_SORT

        # No longer mentions leaderboards: ranking moved to /leaderboards/, and describing this page as a
        # board would set the wrong expectation in a result snippet for what is now a directory.
        context['seo_description'] = (
            "Browse PlayStation trophy hunters on Platinum Pursuit. Find hunters by name or country, "
            "and see who is active."
        )

        return context


class ProfileDetailView(DetailView):
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
        game_has_plat = form.cleaned_data.get('game_has_plat')
        plat_earned = form.cleaned_data.get('plat_earned')
        is_100 = form.cleaned_data.get('is_100')
        sort_val = form.cleaned_data.get('sort')

        # Build queryset
        # `game.display_image_url` resolves a trusted IGDB cover FIRST, so rendering a grid of games walks
        # Game -> Concept -> IGDBMatch for every row. With only `game` selected that was two extra queries
        # per card -- measured at 52 Concept + 52 IGDBMatch fetches on a 26-game page, ~104 of the tab's
        # ~115 queries.
        #
        # The `defer` is not optional (project CLAUDE.md): `raw_response` is the ~30 KB IGDB API blob that
        # no cover-art template reads, and hauling it per row is what triggered the May 2026 web-server
        # OOM. Widening the join without deferring it trades a query storm for a memory one.
        games_qs = profile.played_games.all().select_related(
            'game', 'game__concept', 'game__concept__igdb_match',
        ).defer('game__concept__igdb_match__raw_response').annotate(
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

        # Apply plat status filters (three independent axes)
        if game_has_plat == 'yes':
            games_qs = games_qs.filter(game__defined_trophies__platinum__gt=0)
        elif game_has_plat == 'no':
            games_qs = games_qs.exclude(game__defined_trophies__platinum__gt=0)
        if plat_earned == 'yes':
            games_qs = games_qs.filter(has_plat=True)
        elif plat_earned == 'no':
            games_qs = games_qs.filter(has_plat=False)
        if is_100 == 'yes':
            games_qs = games_qs.filter(progress=100)
        elif is_100 == 'no':
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

        # --- Community flag filters (hide wins on conflict) ---
        if form.cleaned_data.get('hide_delisted'):
            games_qs = games_qs.filter(game__is_delisted=False)
        elif form.cleaned_data.get('show_delisted'):
            games_qs = games_qs.filter(game__is_delisted=True)
        if form.cleaned_data.get('hide_unobtainable'):
            games_qs = games_qs.filter(game__is_obtainable=True)
        elif form.cleaned_data.get('show_unobtainable'):
            games_qs = games_qs.filter(game__is_obtainable=False)
        if form.cleaned_data.get('hide_online'):
            games_qs = games_qs.filter(game__has_online_trophies=False)
        elif form.cleaned_data.get('show_online'):
            games_qs = games_qs.filter(game__has_online_trophies=True)
        if form.cleaned_data.get('hide_buggy'):
            games_qs = games_qs.filter(game__has_buggy_trophies=False)
        elif form.cleaned_data.get('show_buggy'):
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
            # Trusted matches only — pending/rejected matches have TTB
            # populated but not reviewed.
            time_q = Q(
                game__concept__igdb_match__time_to_beat_completely__isnull=False,
                game__concept__igdb_match__status__in=('accepted', 'auto_accepted'),
            )
            if igdb_lo > 0:
                time_q &= Q(game__concept__igdb_match__time_to_beat_completely__gte=int(igdb_lo) * 3600)
            if igdb_hi < 1000:
                time_q &= Q(game__concept__igdb_match__time_to_beat_completely__lte=int(igdb_hi) * 3600)
            games_qs = games_qs.filter(time_q)

        # --- Sort ---
        order = ['-last_updated_datetime']
        if sort_val == 'oldest':
            order = ['last_updated_datetime']
        elif sort_val == 'latest_trophy':
            order = [OrderBy(F('most_recent_trophy_date'), descending=True, nulls_last=True), Lower('game__title_name')]
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
                _time_to_beat=Case(
                    When(
                        game__concept__igdb_match__status__in=('accepted', 'auto_accepted'),
                        then=F('game__concept__igdb_match__time_to_beat_completely'),
                    ),
                    default=None,
                    output_field=IntegerField(),
                ),
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

        # Build shared context (header stats + timeline)
        context['header_stats'] = self._build_header_stats(profile)

        # Showcases render for everyone, anonymous included: a shared profile link is
        # mostly opened logged-out, which is exactly the audience the customization is
        # for. Every remaining provider is bounded by config or by a small owned table
        # (<= 20 selected platinums, <= 6 game ids, <= 5 badges, <= 6 titles, 6
        # date-indexed platinums), so the whole set is cheap regardless of account size.
        # The one provider that was NOT bounded -- Rarest Trophies, which ranked the
        # profile's entire earned set on a joined column -- was removed outright rather
        # than gated, because its cost came from "rank everything I own" and not from
        # who was looking. See showcase_service.py and migration 0275.
        from trophies.services.showcase_service import ProfileShowcaseService
        context['rendered_showcases'] = ProfileShowcaseService.get_rendered_showcases(profile)

        # The timeline IS still gated. It is cached per profile, so a crawler
        # enumerating distinct profiles has a 0% hit rate by construction -- per-entity
        # caching cannot protect an enumerable URL space, only gating can. The partial
        # (profile_timeline.html) self-hides on an empty value, so the anonymous page
        # loses a section rather than gaining a hole.
        #
        # The four Platinum Highlight cards in the header are deliberately NOT gated:
        # they render a "None" empty state when absent, so skipping them would misreport
        # the profile to logged-out visitors instead of hiding a section. They are also
        # cheap (two denormed FKs, plus two lookups bounded by the profile's
        # ProfileGame rows).
        if self.request.user.is_authenticated and profile.psn_history_public:
            context['timeline_events'] = self._build_timeline(profile)

        # Public game lists count (shown in tab header regardless of active tab)
        public_lists_qs = GameList.objects.filter(profile=profile, is_public=True, is_deleted=False)
        context['profile_lists_count'] = public_lists_qs.count()

        # Delegate to tab-specific handler methods
        if tab == 'games':
            tab_context = self._build_games_tab_context(profile, per_page, page_number)
        elif tab == 'trophies':
            tab_context = self._build_trophies_tab_context(profile, per_page, page_number)
        elif tab == 'badges':
            tab_context = self._build_badges_tab_context(profile)
        elif tab == 'lists':
            tab_context = self._build_lists_tab_context(public_lists_qs)
        else:
            # Default to games tab if invalid tab specified
            tab_context = self._build_games_tab_context(profile, per_page, page_number)

        context.update(tab_context)

        # Add shared metadata
        context['breadcrumb'] = [
            {'text': 'Home', 'url': reverse_lazy('home')},
            {'text': 'Hunters', 'url': reverse_lazy('profiles_list')},
            {'text': f"{profile.display_psn_username}"}
        ]
        context['current_tab'] = tab

        # Tab template mapping for {% include %} and HTMX partial returns
        tab_templates = {
            'games': 'trophies/partials/profile_detail/tabs/games_tab.html',
            'trophies': 'trophies/partials/profile_detail/tabs/trophies_tab.html',
            'badges': 'trophies/partials/profile_detail/tabs/badges_tab.html',
            'lists': 'trophies/partials/profile_detail/tabs/lists_tab.html',
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


        return context

    # Template maps for HTMX partial responses
    _TAB_TEMPLATES = {
        'games': 'trophies/partials/profile_detail/tabs/games_tab.html',
        'trophies': 'trophies/partials/profile_detail/tabs/trophies_tab.html',
        'badges': 'trophies/partials/profile_detail/tabs/badges_tab.html',
        'lists': 'trophies/partials/profile_detail/tabs/lists_tab.html',
    }
    _RESULTS_TEMPLATES = {
        'games': 'trophies/partials/profile_detail/tabs/games_results.html',
        'trophies': 'trophies/partials/profile_detail/tabs/trophies_results.html',
        'badges': 'trophies/partials/profile_detail/tabs/badges_results.html',
    }
    _INFINITE_SCROLL_TEMPLATES = {
        'games': 'trophies/partials/profile_detail/game_list_items.html',
        'trophies': 'trophies/partials/profile_detail/trophy_list_items.html',
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


class ProfileEditorView(LoginRequiredMixin, TemplateView):
    """
    Steam-style profile customization editor. Users pick showcase types,
    reorder them, and configure per-type item selection.
    """
    template_name = 'trophies/profile_editor.html'

    def get_context_data(self, **kwargs):
        from trophies.services.showcase_service import (
            SHOWCASE_REGISTRY, ProfileShowcaseService,
            FREE_SLOT_LIMIT, PREMIUM_SLOT_LIMIT, slot_limit_for,
        )

        context = super().get_context_data(**kwargs)

        profile = self.request.user.profile
        is_premium = profile.user_is_premium

        all_showcases = ProfileShowcaseService.get_all_showcases(profile)
        active = [s for s in all_showcases if s.is_active]
        inactive = [s for s in all_showcases if not s.is_active]

        active_types = {s.showcase_type for s in active}

        # Build available + active lists with descriptor metadata
        available = []
        inactive_types = {s.showcase_type for s in inactive}
        for slug, descriptor in SHOWCASE_REGISTRY.items():
            if slug in active_types:
                continue
            available.append({
                'slug': slug,
                'descriptor': descriptor,
                'locked': descriptor['requires_premium'] and not is_premium,
                'has_preserved_config': slug in inactive_types,
            })

        active_with_descriptors = []
        for showcase in active:
            descriptor = SHOWCASE_REGISTRY.get(showcase.showcase_type)
            if not descriptor:
                continue
            active_with_descriptors.append({
                'showcase': showcase,
                'descriptor': descriptor,
            })

        inactive_with_descriptors = []
        for showcase in inactive:
            descriptor = SHOWCASE_REGISTRY.get(showcase.showcase_type)
            if not descriptor:
                continue
            inactive_with_descriptors.append({
                'showcase': showcase,
                'descriptor': descriptor,
            })

        # Data for pickers: only fetch if that showcase is active
        fav_showcase = next(
            (s for s in active if s.showcase_type == 'favorite_games'), None
        )
        favorite_games_data = None
        if fav_showcase:
            from trophies.models import ProfileGame
            from django.db.models.functions import Lower

            games_qs = (
                ProfileGame.objects
                .filter(profile=profile)
                .select_related('game', 'game__concept', 'game__concept__igdb_match')
                .defer(
                    # Profile trophy case can list 500+ games; the IGDB raw_response
                    # blob (~30 KB per row) is unused by the card render.
                    'game__concept__igdb_match__raw_response',
                )
                .order_by(Lower('game__title_name'))
            )
            all_games = [
                {
                    'game_id': pg.game_id,
                    'title_name': pg.game.title_name,
                    'icon_url': (
                        pg.game.title_image
                        or (pg.game.concept.cover_url if pg.game.concept else '')
                        or pg.game.title_icon_url
                        or ''
                    ),
                    'progress': pg.progress,
                    'has_plat': pg.has_plat,
                    'is_shovelware': pg.game.shovelware_status in ('auto_flagged', 'manually_flagged'),
                }
                for pg in games_qs
            ]
            favorite_games_data = {
                'games': all_games,
                'selected_ids': fav_showcase.config.get('game_ids', []),
            }

        # Badge picker data
        badge_showcase_entry = next(
            (s for s in active if s.showcase_type == 'badge_showcase'), None
        )
        badge_showcase_data = None
        if badge_showcase_entry:
            from trophies.models import UserBadge, ProfileBadgeShowcase

            earned = (
                UserBadge.objects.filter(profile=profile)
                .select_related(
                    'badge', 'badge__base_badge',
                    'badge__most_recent_concept',
                )
                .order_by('-earned_at')
            )
            selected_ids = list(
                ProfileBadgeShowcase.objects.filter(profile=profile)
                .order_by('display_order')
                .values_list('badge_id', flat=True)
            )

            # Keep only the highest tier earned per series_slug
            tier_names = {1: 'Bronze', 2: 'Silver', 3: 'Gold', 4: 'Platinum'}
            highest_by_series = {}  # series_slug -> (badge, layers)
            for ub in earned:
                badge = ub.badge
                try:
                    layers = badge.get_badge_layers()
                except Exception:
                    continue
                if not layers.get('has_custom_image'):
                    continue
                key = badge.series_slug
                current = highest_by_series.get(key)
                if not current or badge.tier > current[0].tier:
                    highest_by_series[key] = (badge, layers)

            # If a selected badge isn't the highest tier (shouldn't happen normally
            # but could if the user earned a higher tier after selecting), include
            # it so the user can see/deselect it.
            selected_id_set = set(selected_ids)
            for ub in earned:
                badge = ub.badge
                if badge.id not in selected_id_set:
                    continue
                current = highest_by_series.get(badge.series_slug)
                if current and current[0].id == badge.id:
                    continue
                try:
                    layers = badge.get_badge_layers()
                except Exception:
                    continue
                if layers.get('has_custom_image'):
                    highest_by_series[f"{badge.series_slug}__sel_{badge.id}"] = (badge, layers)

            badges = []
            for _, (badge, layers) in highest_by_series.items():
                badges.append({
                    'badge_id': badge.id,
                    'name': badge.effective_display_series or badge.series_slug,
                    'tier': badge.tier,
                    'tier_name': tier_names.get(badge.tier, ''),
                    'icon_url': layers.get('main') or '',
                })
            # Sort by tier desc, then name asc for a stable display
            badges.sort(key=lambda b: (-b['tier'], b['name'].lower()))

            badge_showcase_data = {
                'badges': badges,
                'selected_ids': selected_ids,
            }

        context['breadcrumb'] = [
            {'text': 'Home', 'url': reverse('home')},
            {'text': 'My Pursuit', 'url': reverse('my_pursuit_hub')},
            {'text': 'Profile Editor'},
        ]
        context['profile'] = profile
        context['is_premium'] = is_premium
        context['available_showcases'] = available
        context['active_showcases'] = active_with_descriptors
        context['inactive_showcases'] = inactive_with_descriptors
        context['slot_limit'] = slot_limit_for(is_premium)
        context['slots_used'] = len(active)
        context['slots_remaining'] = max(0, context['slot_limit'] - context['slots_used'])
        context['free_slot_limit'] = FREE_SLOT_LIMIT
        context['premium_slot_limit'] = PREMIUM_SLOT_LIMIT
        # Review picker data
        review_showcase_entry = next(
            (s for s in active if s.showcase_type == 'review_showcase'), None
        )
        review_showcase_data = None
        if review_showcase_entry:
            from trophies.models import Review

            reviews = (
                Review.objects.filter(profile=profile, is_deleted=False)
                .select_related('concept', 'concept_trophy_group')
                .order_by('-created_at')
            )
            review_showcase_data = {
                'reviews': [
                    {
                        'review_id': r.id,
                        'concept_title': r.concept.unified_title if r.concept else 'Unknown',
                        'icon_url': r.concept.cover_url if r.concept else '',
                        'recommended': r.recommended,
                        'body_preview': (r.body or '')[:200],
                        'helpful_count': r.helpful_count,
                        'group_label': (
                            r.concept_trophy_group.display_name
                            if r.concept_trophy_group and r.concept_trophy_group.trophy_group_id != 'default'
                            else ''
                        ),
                    }
                    for r in reviews
                ],
                'selected_ids': review_showcase_entry.config.get('review_ids', []),
            }

        # Title picker data
        title_showcase_entry = next(
            (s for s in active if s.showcase_type == 'title_showcase'), None
        )
        title_showcase_data = None
        if title_showcase_entry:
            from trophies.models import UserTitle

            user_titles = (
                UserTitle.objects.filter(profile=profile)
                .select_related('title')
                .order_by('-earned_at')
            )
            title_showcase_data = {
                'titles': [
                    {
                        'user_title_id': ut.id,
                        'name': ut.title.name,
                        'source_type': ut.source_type,
                    }
                    for ut in user_titles
                ],
                'selected_ids': title_showcase_entry.config.get('user_title_ids', []),
            }

        context['favorite_games_data'] = favorite_games_data
        context['badge_showcase_data'] = badge_showcase_data
        context['review_showcase_data'] = review_showcase_data
        context['title_showcase_data'] = title_showcase_data
        return context


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

