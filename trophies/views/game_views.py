import json
import logging
import math

from core.services.tracking import track_page_view, track_site_event
from datetime import datetime, timedelta, date
from django.core.cache import cache
from django.contrib import messages
from django.db.models import Q, F, Prefetch, Subquery, OuterRef, Value, IntegerField, FloatField, Avg, Case, When, Count
from django.db.models.functions import Coalesce, Lower
from django.http import HttpResponseRedirect
from django.shortcuts import render
from django.urls import reverse_lazy, reverse
from django.utils import timezone
from django.utils.decorators import method_decorator
from django.views.decorators.csrf import ensure_csrf_cookie
from django.views.generic import ListView, DetailView
from random import choice
from urllib.parse import urlencode
from trophies.mixins import ProfileHotbarMixin
from ..models import Game, Trophy, Profile, EarnedTrophy, ProfileGame, TrophyGroup, Badge, Concept, FeaturedGuide, Stage, Checklist
from ..forms import GameSearchForm, GameDetailForm, UserConceptRatingForm, GuideSearchForm
from trophies.util_modules.constants import MODERN_PLATFORMS, ALL_PLATFORMS

logger = logging.getLogger("psn_api")


class GamesListView(ProfileHotbarMixin, ListView):
    """
    Display paginated list of games with filtering and sorting options.

    Provides comprehensive game browsing functionality with filters for:
    - Platform (PS4, PS5, PS Vita, etc.)
    - Region (NA, EU, JP, global)
    - Alphabetical letter
    - Platinum trophy availability
    - Shovelware exclusion

    Defaults to modern platforms (PS4/PS5) and user's preferred region if authenticated.
    """
    model = Game
    template_name = 'trophies/game_list.html'
    paginate_by = 25

    def dispatch(self, request, *args, **kwargs):
        if not request.GET:
            default_params = {'platform': MODERN_PLATFORMS}
            if request.user.is_authenticated and request.user.default_region:
                default_params['regions'] = ['global', request.user.default_region]

            if default_params:
                query_string = urlencode(default_params, doseq=True)
                url = reverse('games_list') + '?' + query_string
                return HttpResponseRedirect(url)
        return super().dispatch(request, *args, **kwargs)

    def get_queryset(self):
        qs = super().get_queryset()
        form = GameSearchForm(self.request.GET)
        order = [Lower('title_name')]

        platinums_earned = Subquery(Trophy.objects.filter(game=OuterRef('pk'), trophy_type='platinum').values('earned_count')[:1])
        platinums_rate = Subquery(Trophy.objects.filter(game=OuterRef('pk'), trophy_type='platinum').values('earn_rate')[:1])
        qs = qs.annotate(
            platinums_earned_count=Coalesce(platinums_earned, Value(0), output_field=IntegerField()),
            platinums_earn_rate=Coalesce(platinums_rate, Value(0.0), output_field=FloatField())
        )

        if form.is_valid():
            query = form.cleaned_data.get('query')
            platforms = form.cleaned_data.get('platform')
            regions = form.cleaned_data.get('regions')
            letter = form.cleaned_data.get('letter')
            show_only_platinum = form.cleaned_data.get('show_only_platinum')
            filter_shovelware = form.cleaned_data.get('filter_shovelware')
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
            if show_only_platinum:
                qs = qs.filter(trophies__trophy_type='platinum').distinct()
            if filter_shovelware:
                qs = qs.filter(is_shovelware=False)

            if sort_val == 'played':
                order = ['-played_count', Lower('title_name')]
            elif sort_val == 'played_inv':
                order = ['played_count', Lower('title_name')]
            elif sort_val == 'plat_earned':
                order = ['-platinums_earned_count', Lower('title_name')]
            elif sort_val == 'plat_earned_inv':
                order = ['platinums_earned_count', Lower('title_name')]
            elif sort_val == 'plat_rate':
                order = ['-platinums_earn_rate', Lower('title_name')]
            elif sort_val == 'plat_rate_inv':
                order = ['platinums_earn_rate', Lower('title_name')]

        qs = qs.prefetch_related(
            Prefetch('trophies', queryset=Trophy.objects.filter(trophy_type='platinum'), to_attr='platinum_trophy')
        )
        return qs.order_by(*order)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['breadcrumb'] = [
            {'text': 'Home', 'url': reverse_lazy('home')},
            {'text': 'Games'},
        ]

        context['form'] = GameSearchForm(self.request.GET)
        context['selected_platforms'] = self.request.GET.getlist('platform')
        context['selected_regions'] = self.request.GET.getlist('regions')
        context['view_type'] = self.request.GET.get('view', 'grid')
        context['show_only_platinum'] = self.request.GET.get('show_only_platinum', '')
        context['filter_shovelware'] = self.request.GET.get('filter_shovelware', '')

        track_page_view('games_list', 'list', self.request)
        return context


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
                context['profile_trophy_totals'] = {
                    'bronze': ordered_earned_qs.filter(trophy__trophy_type='bronze').count() or 0,
                    'silver': ordered_earned_qs.filter(trophy__trophy_type='silver').count() or 0,
                    'gold': ordered_earned_qs.filter(trophy__trophy_type='gold').count() or 0,
                    'platinum': ordered_earned_qs.filter(trophy__trophy_type='platinum').count() or 0,
                }

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
        if profile_progress and profile_progress['progress'] == 100:
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
        images_timeout = 604800  # 1 week

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
                'bg_url': game.concept.bg_url,
                'screenshot_urls': screenshot_urls,
                'content_rating_url': content_rating_url
            }
            cache.set(images_cache_key, json.dumps(image_urls), timeout=images_timeout)
            return image_urls

        except Exception as e:
            logger.error(f"Game images cache failed for {game.np_communication_id}: {e}")
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
            logger.error(f"Game stats cache failed for {game.np_communication_id}: {e}")
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
            logger.error(f"Game trophies query failed for {game.np_communication_id}: {e}")
            full_trophies = []

        # Get trophy comment counts if game has a concept
        trophy_comment_counts = {}
        if game.concept:
            comment_counts = game.concept.comments.filter(
                trophy_id__isnull=False
            ).values('trophy_id').annotate(
                count=Count('id')
            )
            trophy_comment_counts = {item['trophy_id']: item['count'] for item in comment_counts}

        # Add comment counts to trophy data
        for trophy in full_trophies:
            trophy['comment_count'] = trophy_comment_counts.get(trophy['trophy_id'], 0)

        # Apply filtering and sorting
        if form.is_valid():
            earned_key = form.cleaned_data['earned']
            if profile_earned:
                if earned_key == 'unearned':
                    full_trophies = [t for t in full_trophies if not profile_earned.get(t['trophy_id'], {}).get('earned', False)]
                elif earned_key == 'earned':
                    full_trophies = [t for t in full_trophies if profile_earned.get(t['trophy_id'], {}).get('earned', False)]

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

        context = {}
        today = date.today().isoformat()
        stats_timeout = 3600

        # Community averages
        averages_cache_key = f"concept:averages:{game.concept.concept_id}:{today}"
        cached_averages = cache.get(averages_cache_key)
        if cached_averages:
            averages = json.loads(cached_averages)
        else:
            averages = game.concept.get_community_averages()
            if averages:
                cache.set(averages_cache_key, json.dumps(averages), timeout=stats_timeout)
        context['community_averages'] = averages

        # Related badges
        series_slugs = Stage.objects.filter(concepts__games=game).values_list('series_slug', flat=True).distinct()
        badges = Badge.objects.filter(series_slug__in=Subquery(series_slugs), tier=1).distinct().order_by('tier')
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
        Build user rating context if user has earned platinum.

        Args:
            user: Request user
            game: Game instance

        Returns:
            dict: Rating context or empty dict
        """
        profile = user.profile if user.is_authenticated and hasattr(user, 'profile') and user.profile and user.profile.is_linked else None
        if not profile or not game.concept:
            return {}

        has_platinum = game.concept.has_user_earned_platinum(profile)
        if not has_platinum:
            return {}

        user_rating = game.concept.user_ratings.filter(profile=profile).first()
        return {
            'has_platinum': has_platinum,
            'rating_form': UserConceptRatingForm(instance=user_rating)
        }

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

        # Add checklist count for the concept (for initial badge display)
        if game.concept:
            context['checklist_count'] = Checklist.objects.active().published().filter(concept=game.concept).count()
            # Check if user has draft checklists for this concept
            if user.is_authenticated and hasattr(user, 'profile') and user.profile:
                context['user_draft_checklists'] = Checklist.objects.active().filter(
                    concept=game.concept,
                    profile=user.profile,
                    status='draft'
                )
            else:
                context['user_draft_checklists'] = []
        else:
            context['checklist_count'] = 0
            context['user_draft_checklists'] = []

        # Build user rating context (if earned platinum)
        rating_context = self._build_rating_context(user, game)
        context.update(rating_context)

        # Build breadcrumbs
        context['breadcrumb'] = self._build_breadcrumbs(game, target_profile)

        track_page_view('game', game.id, self.request)
        context['view_count'] = game.view_count

        return context

    def post(self, request, *args, **kwargs):
        self.object = self.get_object()
        game = self.object
        concept = game.concept
        if not concept:
            return HttpResponseRedirect(request.path)

        user = request.user
        profile = user.profile if user.is_authenticated and hasattr(user, 'profile') and user.profile and user.profile.is_linked else None
        if profile and concept.has_user_earned_platinum(profile):
            rating = concept.user_ratings.filter(profile=profile).first()
            form = UserConceptRatingForm(request.POST, instance=rating)
            if form.is_valid():
                rating = form.save(commit=False)
                rating.profile = profile
                rating.concept = concept
                rating.save()
                today = date.today().isoformat()
                averages_cache_key = f"concept:averages:{concept.concept_id}:{today}"
                cache.delete(averages_cache_key)

                # Check for rating milestones
                from trophies.services.milestone_service import check_all_milestones_for_user
                check_all_milestones_for_user(profile, criteria_type='rating_count')

                messages.success(request, 'Your rating has been submitted!')
            else:
                messages.error(request, "Invalid form submission.")

        return HttpResponseRedirect(request.path)


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
                    featured_concept = choice(guides)
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
