import json
import logging
import math

from core.services.tracking import track_page_view, track_site_event
from datetime import datetime, timedelta, date
from django.core.cache import cache
from django.contrib import messages
from django.db.models import Q, F, Prefetch, Subquery, OuterRef, Value, IntegerField, FloatField, BooleanField, Avg, Case, When, Count
from django.db.models.functions import Coalesce, Lower
from django.http import HttpResponseRedirect
from django.shortcuts import render
from django.urls import reverse_lazy, reverse
from django.utils import timezone
from django.utils.decorators import method_decorator
from django.views.decorators.csrf import ensure_csrf_cookie
from django.views import View
from django.views.generic import ListView, DetailView
from urllib.parse import urlencode
from trophies.mixins import ProfileHotbarMixin, HtmxListMixin
from ..constants import CACHE_TIMEOUT_IMAGES
from ..models import Game, Trophy, Profile, EarnedTrophy, ProfileGame, TrophyGroup, Badge, Concept, FeaturedGuide, Stage, UserConceptRating
from ..forms import GameSearchForm, GameDetailForm, GuideSearchForm
from trophies.util_modules.constants import MODERN_PLATFORMS, ALL_PLATFORMS
from .browse_helpers import (
    get_badge_picker_context, annotate_ascii_name, apply_game_browse_filters,
    apply_game_browse_sort,
)

logger = logging.getLogger("psn_api")


class GamesListView(HtmxListMixin, ProfileHotbarMixin, ListView):
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
            self._filter_form = GameSearchForm(self.request.GET)
        return self._filter_form

    def dispatch(self, request, *args, **kwargs):
        if not request.GET:
            if request.user.is_authenticated:
                defaults = (request.user.browse_defaults or {}).get('games', {})
                if defaults:
                    return HttpResponseRedirect(
                        reverse('games_list') + '?' + urlencode(defaults, doseq=True)
                    )
            # Anonymous or no saved defaults: modern platforms only
            return HttpResponseRedirect(
                reverse('games_list') + '?' + urlencode({'platform': MODERN_PLATFORMS}, doseq=True)
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

        qs = qs.select_related(
            'concept', 'concept__igdb_match',
        ).prefetch_related(
            Prefetch('trophies', queryset=Trophy.objects.filter(trophy_type='platinum'), to_attr='platinum_trophy')
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
        context['selected_platforms'] = self.request.GET.getlist('platform')
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

        # Check if any filters are active (for badge + auto-expanding the drawer)
        context['has_advanced_filters'] = any(
            v for k, v in self.request.GET.lists()
            if k not in ('page', 'view') and any(v)
        )

        # Badge picker modal data
        context.update(get_badge_picker_context(self.request))

        context['seo_description'] = (
            "Browse PlayStation games on Platinum Pursuit. "
            "Search by name, filter by platform, and track your trophy progress."
        )

        # Post-pagination data (only for the 25 games on this page)
        page_games = context['object_list']
        game_ids = [g.id for g in page_games]

        # User-specific game data (1 query)
        if self.request.user.is_authenticated and hasattr(self.request.user, 'profile'):
            user_games = ProfileGame.objects.filter(
                profile=self.request.user.profile,
                game_id__in=game_ids
            ).values('game_id', 'progress', 'has_plat', 'earned_trophies_count')
            context['user_game_map'] = {pg['game_id']: pg for pg in user_games}

        # Community ratings (1 query)
        concept_ids = [g.concept_id for g in page_games if g.concept_id]
        if concept_ids:
            ratings = UserConceptRating.objects.filter(
                concept_id__in=concept_ids,
                concept_trophy_group__isnull=True
            ).values('concept_id').annotate(
                avg_difficulty=Avg('difficulty'),
                avg_fun=Avg('fun_ranking'),
                avg_rating=Avg('overall_rating'),
                rating_count=Count('id')
            )
            context['rating_map'] = {r['concept_id']: r for r in ratings}

        track_page_view('games_list', 'list', self.request)
        return context


class RandomGameView(View):
    """Redirect to a random game detail page, respecting active browse filters."""

    def get(self, request):
        form = GameSearchForm(request.GET)
        qs = Game.objects.all()

        if form.is_valid():
            qs, _ = apply_game_browse_filters(qs, form)

        random_game = qs.order_by('?').only('np_communication_id').first()

        if random_game:
            return HttpResponseRedirect(
                reverse('game_detail', args=[random_game.np_communication_id])
            )

        messages.info(request, "No games match your current filters. Try broadening your search!")
        referer_params = request.GET.urlencode()
        return HttpResponseRedirect(
            reverse('games_list') + ('?' + referer_params if referer_params else '')
        )


@method_decorator(ensure_csrf_cookie, name='dispatch')
class GameDetailView(ProfileHotbarMixin, DetailView):
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

    def get_queryset(self):
        return super().get_queryset().select_related('concept', 'concept__igdb_match').prefetch_related(
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

            if has_trophies:
                # Get earned trophies data
                earned_qs = EarnedTrophy.objects.filter(profile=profile, trophy__game=game).order_by('trophy__trophy_id')
                context['profile_earned'] = {
                    e.trophy.trophy_id: {
                        'earned': e.earned,
                        'progress': e.progress,
                        'progress_rate': e.progress_rate,
                        'progressed_date_time': e.progressed_date_time,
                        'earned_date_time': e.earned_date_time
                    } for e in earned_qs
                }

                # Calculate trophy type totals
                ordered_earned_qs = earned_qs.filter(earned=True).order_by(F('earned_date_time').asc(nulls_last=True))
                context['profile_trophy_totals'] = ordered_earned_qs.aggregate(
                    bronze=Count('id', filter=Q(trophy__trophy_type='bronze')),
                    silver=Count('id', filter=Q(trophy__trophy_type='silver')),
                    gold=Count('id', filter=Q(trophy__trophy_type='gold')),
                    platinum=Count('id', filter=Q(trophy__trophy_type='platinum')),
                )

                # Calculate group totals
                profile_group_totals = {}
                for e in ordered_earned_qs:
                    group_id = e.trophy.trophy_group_id or 'default'
                    trophy_type = e.trophy.trophy_type
                    if group_id not in profile_group_totals:
                        profile_group_totals[group_id] = {'bronze': 0, 'silver': 0, 'gold': 0, 'platinum': 0}
                    profile_group_totals[group_id][trophy_type] += 1
                context['profile_group_totals'] = profile_group_totals

                # Build milestones
                context['timeline_events'] = self._build_timeline_events(ordered_earned_qs, len(earned_qs), context['profile_progress'], profile_game)

        except ProfileGame.DoesNotExist:
            pass

        return context

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
        Build timeline events for the player's journey
        (started, first, 25%, 50%, 75%, platinum, 100%).

        Args:
            ordered_earned_qs: QuerySet of earned trophies ordered by date
            total_trophies: Total number of trophies in game
            profile_progress: Profile progress dict with 'progress' key
            profile_game: ProfileGame instance for first_played_date_time

        Returns:
            list: List of timeline event dicts
        """
        events = []
        earned_list = list(ordered_earned_qs)

        # Started Playing
        first_played = profile_game.first_played_date_time
        events.append(self._make_timeline_event(
            'Started Playing', 'started', first_played is not None, date=first_played
        ))

        # First trophy
        if len(earned_list) > 0:
            first = earned_list[0]
            events.append(self._make_timeline_event(
                'First Trophy', 'trophy', True, date=first.earned_date_time, trophy=first.trophy
            ))
        else:
            events.append(self._make_timeline_event('First Trophy', 'trophy', False))

        # 25% trophy
        quarter_idx = math.ceil((total_trophies - 1) * 0.25)
        if len(earned_list) > quarter_idx:
            quarter = earned_list[quarter_idx]
            events.append(self._make_timeline_event(
                '25% Trophy', 'trophy', True, date=quarter.earned_date_time, trophy=quarter.trophy
            ))
        else:
            events.append(self._make_timeline_event('25% Trophy', 'trophy', False))

        # 50% trophy
        mid_idx = math.ceil((total_trophies - 1) * 0.5)
        if len(earned_list) > mid_idx:
            mid = earned_list[mid_idx]
            events.append(self._make_timeline_event(
                '50% Trophy', 'trophy', True, date=mid.earned_date_time, trophy=mid.trophy
            ))
        else:
            events.append(self._make_timeline_event('50% Trophy', 'trophy', False))

        # 75% trophy
        three_quarter_idx = math.ceil((total_trophies - 1) * 0.75)
        if len(earned_list) > three_quarter_idx:
            three_quarter = earned_list[three_quarter_idx]
            events.append(self._make_timeline_event(
                '75% Trophy', 'trophy', True, date=three_quarter.earned_date_time, trophy=three_quarter.trophy
            ))
        else:
            events.append(self._make_timeline_event('75% Trophy', 'trophy', False))

        # Platinum trophy
        plat_entry = None
        if len(earned_list) > 0:
            plat_entry = next((e for e in reversed(earned_list) if e.trophy.trophy_type == 'platinum'), None)
        if plat_entry:
            events.append(self._make_timeline_event(
                'Platinum Trophy', 'trophy', True, date=plat_entry.earned_date_time, trophy=plat_entry.trophy
            ))
        else:
            events.append(self._make_timeline_event('Platinum Trophy', 'trophy', False))

        # 100% trophy
        if profile_progress and profile_progress['progress'] == 100 and earned_list:
            complete = earned_list[-1]
            events.append(self._make_timeline_event(
                '100% Trophy', 'trophy', True, date=complete.earned_date_time, trophy=complete.trophy
            ))
        else:
            events.append(self._make_timeline_event('100% Trophy', 'trophy', False))

        return events

    def _build_images_context(self, game):
        """
        Build cached image URLs for game background, screenshots, and content rating.

        Args:
            game: Game instance

        Returns:
            dict: Image URLs or empty dict on error
        """
        images_cache_key = f"game:imageurls:{game.np_communication_id}"
        images_timeout = CACHE_TIMEOUT_IMAGES

        try:
            cached_images = cache.get(images_cache_key)
            if cached_images:
                return json.loads(cached_images)

            if not game.concept:
                return {}

            screenshot_urls = []
            content_rating_url = None

            if game.concept.media:
                # Prefer screenshots
                for img in game.concept.media:
                    if img.get('type') == 'SCREENSHOT':
                        screenshot_urls.append(img.get('url'))

                # Fallback to other image types if no screenshots
                if len(screenshot_urls) < 1:
                    for img in game.concept.media:
                        img_type = img.get('type')
                        if img_type in ['GAMEHUB_COVER_ART', 'LOGO', 'MASTER']:
                            screenshot_urls.append(img.get('url'))

            if game.concept.content_rating:
                content_rating_url = game.concept.content_rating.get('url')

            image_urls = {
                'bg_url': None,  # Disabled on body during redesign
                'header_bg_url': game.concept.get_cover_url(),  # Used for frosted glass header only
                'screenshot_urls': screenshot_urls,
                'content_rating_url': content_rating_url
            }
            cache.set(images_cache_key, json.dumps(image_urls), timeout=images_timeout)
            return image_urls

        except Exception as e:
            logger.exception(f"Game images cache failed for {game.np_communication_id}")
            return {}

    def _build_game_stats_context(self, game):
        """
        Build game statistics including player counts, completions, and average progress.

        Args:
            game: Game instance

        Returns:
            dict: Game statistics or empty dict on error
        """
        today = date.today().isoformat()
        now_utc = timezone.now()
        stats_cache_key = f"game:stats:{game.np_communication_id}:{today}:{now_utc.hour:02d}"
        stats_timeout = 3600  # 1 hour

        try:
            cached_stats = cache.get(stats_cache_key)
            if cached_stats:
                return json.loads(cached_stats)

            stats = {
                'total_players': game.played_count,
                'monthly_players': ProfileGame.objects.filter(
                    game=game,
                    last_played_date_time__gte=timezone.now() - timedelta(days=30)
                ).count(),
                'plats_earned': EarnedTrophy.objects.filter(
                    trophy__game=game,
                    trophy__trophy_type='platinum',
                    earned=True
                ).count(),
                'total_earns': EarnedTrophy.objects.filter(
                    trophy__game=game,
                    earned=True
                ).count(),
                'completes': ProfileGame.objects.filter(game=game).completed().count(),
                'avg_progress': ProfileGame.objects.filter(game=game).aggregate(avg=Avg('progress'))['avg'] or 0.0
            }
            cache.set(stats_cache_key, json.dumps(stats), timeout=stats_timeout)
            return stats

        except Exception as e:
            logger.exception(f"Game stats cache failed for {game.np_communication_id}")
            return {}

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
        has_trophies = Trophy.objects.filter(game=game).exists()
        if not has_trophies:
            return [], {}, {}, False

        try:
            # Get all trophies
            trophies_qs = Trophy.objects.filter(game=game).order_by('trophy_id')
            full_trophies = [
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
        except Exception as e:
            logger.exception(f"Game trophies query failed for {game.np_communication_id}")
            full_trophies = []

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

        # Get trophy groups
        trophy_groups_cache_key = f"game:trophygroups:{game.np_communication_id}"
        trophy_groups_timeout = 604800  # 1 week
        try:
            cached_trophy_groups = cache.get(trophy_groups_cache_key)
            if cached_trophy_groups:
                trophy_groups = json.loads(cached_trophy_groups)
            else:
                trophy_groups_qs = TrophyGroup.objects.filter(game=game)
                trophy_groups = {
                    g.trophy_group_id: {
                        'trophy_group_name': g.trophy_group_name,
                        'trophy_group_icon_url': g.trophy_group_icon_url,
                        'defined_trophies': g.defined_trophies,
                    } for g in trophy_groups_qs
                }
                cache.set(trophy_groups_cache_key, json.dumps(trophy_groups), timeout=trophy_groups_timeout)
        except Exception as e:
            logger.error(f"Trophy groups cache failed for {game.np_communication_id}: {e}")
            trophy_groups = {}

        # Group trophies
        grouped_trophies = {}
        for trophy in full_trophies:
            group_id = trophy.get('trophy_group_id', 'default')
            if group_id not in grouped_trophies:
                grouped_trophies[group_id] = []
            grouped_trophies[group_id].append(trophy)

        # Sort groups (default first, then alphabetically)
        sorted_groups = sorted(grouped_trophies.keys(), key=lambda x: (x != 'default', x))
        sorted_grouped = {gid: grouped_trophies[gid] for gid in sorted_groups}

        return full_trophies, trophy_groups, sorted_grouped, has_trophies

    def _build_concept_context(self, game):
        """
        Build concept-related context including community ratings, badges, and other versions.

        Args:
            game: Game instance

        Returns:
            dict: Concept context data or empty dict if no concept
        """
        if not game.concept:
            return {}

        from trophies.services.rating_service import RatingService

        context = {}

        # Community averages (base game, for backward compat)
        context['community_averages'] = RatingService.get_cached_community_averages(game.concept)

        # Per-CTG community data for tabbed display
        from trophies.models import ConceptTrophyGroup, Review
        from trophies.services.review_service import ReviewService

        ctgs = list(
            ConceptTrophyGroup.objects.filter(concept=game.concept)
            .order_by('sort_order', 'trophy_group_id')
        )
        context['concept_trophy_groups'] = ctgs

        community_tabs = []
        for ctg in ctgs:
            community_tabs.append({
                'ctg': ctg,
                'averages': RatingService.get_cached_community_averages_for_group(game.concept, ctg),
            })
        context['community_tabs'] = community_tabs

        base_ctg = next((c for c in ctgs if c.trophy_group_id == 'default'), None)
        if base_ctg:
            context['recommendation_stats'] = ReviewService.get_recommendation_stats(
                game.concept, base_ctg
            )
            context['review_count'] = Review.objects.filter(
                concept=game.concept,
                concept_trophy_group=base_ctg,
                is_deleted=False,
            ).count()

            # User review context
            user = self.request.user
            profile = getattr(user, 'profile', None)
            if user.is_authenticated and profile and profile.is_linked:
                context['user_review'] = Review.objects.filter(
                    concept=game.concept,
                    concept_trophy_group=base_ctg,
                    profile=profile,
                    is_deleted=False,
                ).first()

                from trophies.services.concept_trophy_group_service import ConceptTrophyGroupService
                can_review, can_review_reason = ConceptTrophyGroupService.can_review_group(
                    profile, game.concept, base_ctg
                )
                context['can_review'] = can_review
                context['can_review_reason'] = can_review_reason

        # Related badges
        series_slugs = Stage.objects.filter(concepts__games=game).values_list('series_slug', flat=True).distinct()
        badges = Badge.objects.live().filter(series_slug__in=Subquery(series_slugs), tier=1).distinct().order_by('tier')
        context['badges'] = badges

        # Other platform versions
        other_versions_qs = game.concept.games.exclude(pk=game.pk)
        platform_order = {plat: idx for idx, plat in enumerate(ALL_PLATFORMS)}
        other_versions_qs = other_versions_qs.annotate(
            platform_order=Case(*[When(title_platform__contains=plat, then=Value(idx)) for plat, idx in platform_order.items()], default=999, output_field=IntegerField())
        ).order_by('platform_order', 'title_name')
        context['other_versions'] = list(other_versions_qs)

        return context

    def _build_rating_context(self, user, game):
        """
        Build user platinum context for share card button.

        Args:
            user: Request user
            game: Game instance

        Returns:
            dict: Platinum context or empty dict
        """
        profile = user.profile if user.is_authenticated and hasattr(user, 'profile') and user.profile and user.profile.is_linked else None
        if not profile or not game.concept:
            return {}

        has_platinum = game.concept.has_user_earned_platinum(profile)
        if not has_platinum:
            return {}

        result = {'has_platinum': has_platinum}

        # Query earned trophy ID for share card button
        earned_trophy_id = EarnedTrophy.objects.filter(
            profile=profile,
            trophy__game=game,
            trophy__trophy_type='platinum',
            earned=True
        ).values_list('id', flat=True).first()

        if earned_trophy_id:
            result['earned_trophy_id'] = earned_trophy_id
            result['concept_bg_url'] = game.concept.get_cover_url() or ''

        return result

    def _build_breadcrumbs(self, game, target_profile):
        """
        Build breadcrumb navigation.

        Args:
            game: Game instance
            target_profile: Profile instance or None

        Returns:
            list: Breadcrumb items
        """
        return [
            {'text': 'Home', 'url': reverse_lazy('home')},
            {'text': 'Games', 'url': reverse_lazy('games_list')},
            {'text': f"{game.title_name}"}
        ]

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
        else:
            context['profile'] = None
            context['profile_progress'] = None
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

        # Build game images context
        context['image_urls'] = self._build_images_context(game)

        # Build game statistics context
        context['game_stats'] = self._build_game_stats_context(game)

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

        # Build concept-related context (community ratings, badges, other versions)
        concept_context = self._build_concept_context(game)
        context.update(concept_context)

        # Roadmap data for game detail page
        roadmap_preview = (
            self.request.GET.get('preview') == 'true'
            and user.is_authenticated
            and user.is_staff
        )
        context['roadmap_preview_mode'] = roadmap_preview
        if game.concept:
            from trophies.services.roadmap_service import RoadmapService
            if roadmap_preview:
                roadmap = RoadmapService.get_roadmap_for_preview(game.concept)
            else:
                roadmap = RoadmapService.get_roadmap_for_display(game.concept)
            context['roadmap'] = roadmap
            if roadmap:
                # Map CTG ID -> roadmap tab for template lookup
                context['roadmap_tabs_by_ctg'] = {
                    tab.concept_trophy_group_id: tab
                    for tab in roadmap.tabs.all()
                }
                roadmap_trophy_ids = set()
                for tab in roadmap.tabs.all():
                    for step in tab.steps.all():
                        for st in step.step_trophies.all():
                            roadmap_trophy_ids.add(st.trophy_id)
                    for tg in tab.trophy_guides.all():
                        roadmap_trophy_ids.add(tg.trophy_id)
                context['roadmap_trophies'] = {
                    t.trophy_id: t
                    for t in game.trophies.filter(trophy_id__in=roadmap_trophy_ids)
                } if roadmap_trophy_ids else {}
            else:
                context['roadmap_trophies'] = {}
                context['roadmap_tabs_by_ctg'] = {}
        else:
            context['roadmap'] = None
            context['roadmap_trophies'] = {}
            context['roadmap_tabs_by_ctg'] = {}

        # Build user rating context (if earned platinum)
        rating_context = self._build_rating_context(user, game)
        context.update(rating_context)

        # Add share card dependencies if user has earned platinum
        if rating_context.get('earned_trophy_id'):
            from trophies.themes import get_available_themes_for_grid
            context['available_themes'] = get_available_themes_for_grid(include_game_art=True, grouped=True)

        # Build breadcrumbs
        context['breadcrumb'] = self._build_breadcrumbs(game, target_profile)

        context['seo_description'] = (
            f"{game.title_name} on {game.platforms_display}. "
            f"{game.get_total_defined_trophies()} trophies including "
            f"{game.defined_trophies.get('platinum', 0)} platinum. "
            f"Track your progress on Platinum Pursuit."
        )

        track_page_view('game', game.id, self.request)
        context['view_count'] = game.view_count

        # Game Detail Tour: auto-show once, only after Welcome Tour is done
        if target_profile and getattr(target_profile, 'is_linked', False):
            welcome_done = getattr(target_profile, 'tour_completed_at', None) is not None
            game_tour_done = getattr(target_profile, 'game_detail_tour_completed_at', None) is not None
            context['show_game_detail_tour'] = welcome_done and not game_tour_done
        else:
            context['show_game_detail_tour'] = False

        return context

class GuideListView(ProfileHotbarMixin, ListView):
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
        track_page_view('guides_list', 'list', self.request)

        return context


class FlaggedGamesView(HtmxListMixin, ProfileHotbarMixin, ListView):
    """Dedicated browse page for games with community-reported flag status.

    Landing state shows category cards with counts. Selecting a category
    displays a filtered game grid with secondary filters.
    """
    model = Game
    template_name = 'trophies/flagged_games.html'
    partial_template_name = 'trophies/partials/flagged_games/browse_results.html'
    paginate_by = 30

    FLAG_CATEGORIES = {
        'delisted': {
            'filter': {'is_delisted': True},
            'label': 'Delisted Games',
            'description': 'Games removed from the PlayStation Store. Get them while you can.',
            'color': 'error',
            'icon': 'store-slash',
        },
        'unobtainable': {
            'filter': {'is_obtainable': False},
            'label': 'Unobtainable Trophies',
            'description': 'Games with trophies that can no longer be earned.',
            'color': 'error',
            'icon': 'lock',
        },
        'online': {
            'filter': {'has_online_trophies': True},
            'label': 'Online Trophies',
            'description': 'Games with trophies requiring online connectivity.',
            'color': 'warning',
            'icon': 'wifi',
        },
        'buggy': {
            'filter': {'has_buggy_trophies': True},
            'label': 'Buggy Trophies',
            'description': 'Games with trophies affected by known bugs.',
            'color': 'warning',
            'icon': 'bug',
        },
    }

    def get_filter_form(self):
        if not hasattr(self, '_filter_form'):
            self._filter_form = GameSearchForm(self.request.GET)
        return self._filter_form

    def get_queryset(self):
        qs = super().get_queryset()
        category = self.request.GET.get('category', '')

        if category in self.FLAG_CATEGORIES:
            qs = qs.filter(**self.FLAG_CATEGORIES[category]['filter'])
        else:
            return qs.none()

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

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['breadcrumb'] = [
            {'text': 'Home', 'url': reverse_lazy('home')},
            {'text': 'Games', 'url': reverse_lazy('games_list')},
            {'text': 'Flagged Games'},
        ]

        category = self.request.GET.get('category', '')
        context['active_category'] = category
        context['categories'] = self.FLAG_CATEGORIES
        context['form'] = self.get_filter_form()

        context['selected_platforms'] = self.request.GET.getlist('platform')
        context['selected_regions'] = self.request.GET.getlist('regions')
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
            if k not in ('page', 'view', 'category') and any(v)
        )

        # Badge picker modal data (only when a category is active, since the modal
        # is conditionally rendered in the template)
        if category:
            context.update(get_badge_picker_context(self.request))

        # Counts for each category (cheap queries on indexed boolean fields)
        context['category_counts'] = {
            key: Game.objects.filter(**info['filter']).count()
            for key, info in self.FLAG_CATEGORIES.items()
        }

        # Rating map + user game map for page games
        if category:
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

            if self.request.user.is_authenticated and hasattr(self.request.user, 'profile'):
                game_ids = [g.id for g in page_games]
                user_games = ProfileGame.objects.filter(
                    profile=self.request.user.profile,
                    game_id__in=game_ids,
                ).values('game_id', 'progress', 'has_plat', 'earned_trophies_count')
                context['user_game_map'] = {pg['game_id']: pg for pg in user_games}

        context['seo_description'] = (
            "Browse PlayStation games flagged by the community: delisted games, "
            "unobtainable trophies, online-required trophies, and buggy trophies."
        )

        track_page_view('flagged_games', 'list', self.request)
        return context


class RecentlyAddedView(HtmxListMixin, ProfileHotbarMixin, ListView):
    """Browse recently discovered base games and DLC trophy lists.

    Landing state shows two category cards (base games, DLC) with 30-day counts.
    Selecting a category displays a paginated grid sorted by discovery date.
    """
    template_name = 'trophies/recently_added.html'
    partial_template_name = 'trophies/partials/recently_added/browse_results.html'
    paginate_by = 30

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
        return self.request.GET.get('category', '')

    @property
    def model(self):
        if self.get_category() == 'dlc':
            return TrophyGroup
        return Game

    def get_queryset(self):
        category = self.get_category()

        if category == 'base_games':
            return (
                Game.objects
                .select_related('concept', 'concept__igdb_match')
                .prefetch_related(
                    Prefetch(
                        'trophies',
                        queryset=Trophy.objects.filter(trophy_type='platinum'),
                        to_attr='platinum_trophy',
                    )
                )
                .order_by('-created_at')
            )

        if category == 'dlc':
            return (
                TrophyGroup.objects
                .exclude(trophy_group_id='default')
                .select_related('game', 'game__concept', 'game__concept__igdb_match')
                .order_by('-created_at')
            )

        return Game.objects.none()

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['breadcrumb'] = [
            {'text': 'Home', 'url': reverse_lazy('home')},
            {'text': 'Games', 'url': reverse_lazy('games_list')},
            {'text': 'Recently Added'},
        ]

        category = self.get_category()
        context['active_category'] = category
        context['categories'] = self.CATEGORIES

        # 30-day counts for category cards
        thirty_days_ago = timezone.now() - timedelta(days=30)
        context['category_counts'] = {
            'base_games': Game.objects.filter(created_at__gte=thirty_days_ago).count(),
            'dlc': TrophyGroup.objects.exclude(
                trophy_group_id='default',
            ).filter(created_at__gte=thirty_days_ago).count(),
        }

        # User game data for base games category
        if category == 'base_games':
            page_games = context['object_list']
            game_ids = [g.id for g in page_games]

            if self.request.user.is_authenticated and hasattr(self.request.user, 'profile'):
                user_games = ProfileGame.objects.filter(
                    profile=self.request.user.profile,
                    game_id__in=game_ids,
                ).values('game_id', 'progress', 'has_plat', 'earned_trophies_count')
                context['user_game_map'] = {pg['game_id']: pg for pg in user_games}

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

        context['seo_description'] = (
            "Browse recently added PlayStation trophy lists: new games "
            "and DLC packs discovered by the Platinum Pursuit scout network."
        )

        track_page_view('recently_added', 'list', self.request)
        return context
