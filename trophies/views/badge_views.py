import json
import logging
from collections import defaultdict
from datetime import date

from django.core.cache import cache
from django.core.paginator import Paginator
from django.db.models import Q, F, Prefetch, Max
from django.http import Http404
from django.shortcuts import get_object_or_404
from django.urls import reverse_lazy, reverse
from django.utils.text import slugify
from django.views.generic import ListView, DetailView, TemplateView

from trophies.mixins import ProfileHotbarMixin
from ..models import (
    Game, Profile, ProfileGame, Badge, UserBadge, UserBadgeProgress,
    Concept, Stage, Milestone, UserMilestone, UserMilestoneProgress,
)
from ..forms import BadgeSearchForm

logger = logging.getLogger("psn_api")


class BadgeListView(ProfileHotbarMixin, ListView):
    """
    Display list of all badge series with progress tracking for authenticated users.

    Shows tier 1 badges for each series, with earned status and completion progress
    for logged-in users. Includes trophy totals and game counts for each series.
    """
    model = Badge
    template_name = 'trophies/badge_list.html'
    context_object_name = 'display_data'
    paginate_by = None

    def get_queryset(self):
        qs = super().get_queryset()
        form = BadgeSearchForm(self.request.GET)

        if form.is_valid():
            series_slug = slugify(form.cleaned_data.get('series_slug'))
            if series_slug:
                qs = qs.filter(series_slug__icontains=series_slug)
        return qs

    def _calculate_series_stats(self, series_slug):
        """
        Calculate total games and trophy counts for a badge series.

        Args:
            series_slug: Badge series slug

        Returns:
            tuple: (total_games, trophy_types_dict)
        """
        all_games = Game.objects.filter(concept__stages__series_slug=series_slug).distinct()
        total_games = all_games.count()
        trophy_types = {
            'bronze': sum(game.defined_trophies['bronze'] for game in all_games),
            'silver': sum(game.defined_trophies['silver'] for game in all_games),
            'gold': sum(game.defined_trophies['gold'] for game in all_games),
            'platinum': sum(game.defined_trophies['platinum'] for game in all_games),
        }
        return total_games, trophy_types

    def _build_badge_display_data(self, grouped_badges, profile=None):
        """
        Build display data for badges with optional progress tracking.

        Consolidates logic for both authenticated and unauthenticated states.

        Args:
            grouped_badges: Dict of {series_slug: [badge list]}
            profile: Profile instance or None

        Returns:
            list: Display data dicts for each badge series
        """
        display_data = []

        # Get user progress data if authenticated
        earned_dict = {}
        progress_dict = {}
        if profile:
            user_earned = UserBadge.objects.filter(profile=profile).values('badge__series_slug').annotate(max_tier=Max('badge__tier'))
            earned_dict = {e['badge__series_slug']: e['max_tier'] for e in user_earned}

            all_badges_ids = [b.id for group in grouped_badges.values() for b in group]
            progress_qs = UserBadgeProgress.objects.filter(profile=profile, badge__id__in=all_badges_ids)
            progress_dict = {p.badge.id: p for p in progress_qs}

        # Build display data for each series
        for slug, group in grouped_badges.items():
            sorted_group = sorted(group, key=lambda b: b.tier)
            if not sorted_group:
                continue

            tier1_badge = next((b for b in sorted_group if b.tier == 1), None)
            if not tier1_badge:
                continue

            # Calculate series stats
            total_games, trophy_types = self._calculate_series_stats(tier1_badge.series_slug)
            tier1_earned_count = tier1_badge.earned_count

            # Determine display badge and progress
            if profile:
                highest_tier = earned_dict.get(slug, 0)
                display_badge = next((b for b in sorted_group if b.tier == highest_tier), None) if highest_tier > 0 else tier1_badge
                if not display_badge:
                    continue

                is_earned = highest_tier > 0
                next_badge = next((b for b in sorted_group if b.tier > highest_tier), None)
                progress_badge = next_badge if next_badge else display_badge

                # Calculate progress
                progress = progress_dict.get(progress_badge.id) if progress_badge else None
                required_stages = progress_badge.required_stages
                if progress and progress_badge.badge_type in ['series', 'collection', 'megamix']:
                    completed_concepts = progress.completed_concepts
                    progress_percentage = (completed_concepts / required_stages) * 100 if required_stages > 0 else 0
                else:
                    completed_concepts = 0
                    progress_percentage = 0
            else:
                # Unauthenticated user - show tier 1
                display_badge = tier1_badge
                is_earned = False
                completed_concepts = 0
                required_stages = tier1_badge.required_stages
                progress_percentage = 0

            display_data.append({
                'badge': display_badge,
                'tier1_earned_count': tier1_earned_count,
                'completed_concepts': completed_concepts,
                'required_stages': required_stages,
                'progress_percentage': round(progress_percentage, 1),
                'trophy_types': trophy_types,
                'total_games': total_games,
                'is_earned': is_earned,
            })

        return display_data

    def get_context_data(self, **kwargs):
        """
        Build context for badge list page.

        Groups badges by series, calculates progress for authenticated users,
        and handles sorting and pagination.

        Returns:
            dict: Context with paginated badge display data
        """
        context = super().get_context_data(**kwargs)
        badges = context['object_list']

        # Group badges by series
        grouped_badges = defaultdict(list)
        for badge in badges:
            if badge.effective_user_title:
                grouped_badges[badge.series_slug].append(badge)

        # Build display data (unified for auth/unauth users)
        user = self.request.user
        profile = user.profile if user.is_authenticated and hasattr(user, 'profile') else None
        display_data = self._build_badge_display_data(grouped_badges, profile)

        # Sort data
        sort_val = self.request.GET.get('sort', 'tier')
        if sort_val == 'name':
            display_data.sort(key=lambda d: d['badge'].effective_display_title or '')
        elif sort_val == 'tier':
            display_data.sort(key=lambda d: (d['badge'].tier, d['badge'].effective_display_title or ''))
        elif sort_val == 'tier_desc':
            display_data.sort(key=lambda d: (-d['badge'].tier, d['badge'].effective_display_title or ''))
        elif sort_val == 'earned':
            display_data.sort(key=lambda d: (-d['tier1_earned_count'], d['badge'].effective_display_title or ''))
        elif sort_val == 'earned_inv':
            display_data.sort(key=lambda d: (d['tier1_earned_count'], d['badge'].effective_display_title or ''))
        else:
            display_data.sort(key=lambda d: d['badge'].effective_display_series or '')

        # Paginate
        paginate_by = 25
        paginator = Paginator(display_data, paginate_by)
        page_number = self.request.GET.get('page')
        page_obj = paginator.get_page(page_number)

        context['display_data'] = page_obj
        context['page_obj'] = page_obj
        context['paginator'] = paginator
        context['is_paginated'] = page_obj.has_other_pages()

        # Breadcrumbs and form
        context['breadcrumb'] = [
            {'text': 'Home', 'url': reverse_lazy('home')},
            {'text': 'Badges'},
        ]
        context['form'] = BadgeSearchForm(self.request.GET)
        context['selected_tiers'] = self.request.GET.getlist('tier')

        return context


class BadgeDetailView(ProfileHotbarMixin, DetailView):
    """
    Display detailed badge series information with progress tracking.

    Shows all tiers in a badge series, user's progress (if authenticated),
    required games organized by stages, and completion statistics.
    Dynamically displays highest earned tier or next tier to unlock.
    """
    model = Badge
    template_name = 'trophies/badge_detail.html'
    slug_field = 'series_slug'
    slug_url_kwarg = 'series_slug'
    context_object_name = 'series_badges'

    def get_object(self, queryset=None):
        series_slug = self.kwargs[self.slug_url_kwarg]
        return Badge.objects.by_series(series_slug)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        series_badges = context['object']

        if not series_badges.exists():
            raise Http404("Series not found")

        psn_username = self.kwargs.get('psn_username')
        if psn_username:
            target_profile = get_object_or_404(Profile, psn_username__iexact=psn_username)
        elif self.request.user.is_authenticated and hasattr(self.request.user, 'profile'):
            target_profile = self.request.user.profile
        else:
            target_profile = None

        context['target_profile'] = target_profile

        badge = None
        is_earned = True
        if target_profile:
            highest_tier_earned = UserBadge.objects.filter(profile=target_profile, badge__series_slug=self.kwargs['series_slug']).aggregate(max_tier=Max('badge__tier'))['max_tier'] or 0
            badge = series_badges.filter(tier=highest_tier_earned).first()
            if not badge:
                badge = series_badges.order_by('tier').first()
                context['is_maxed'] = True
                is_earned = False
            else:
                context['is_maxed'] = False

            context['badge'] = badge

            progress = UserBadgeProgress.objects.filter(profile=target_profile, badge=badge).first()
            context['progress'] = progress
            context['progress_percent'] = progress.completed_concepts / badge.required_stages * 100 if progress and badge.required_stages > 0 else 0
        else:
            badge = series_badges.filter(tier=1).first()
            context['badge'] = badge

        stages = Stage.objects.filter(series_slug=badge.series_slug).order_by('stage_number').prefetch_related(
            Prefetch('concepts__games', queryset=Game.objects.all().order_by('title_name'))
        )
        context['stage_count'] = stages.count()

        today = date.today().isoformat()
        stats_timeout = 3600
        structured_data = []
        for stage in stages:
            games = set()
            for concept in stage.concepts.all():
                games.update(concept.games.all())
            games = sorted(games, key=lambda g: g.title_name)

            profile_games = {}
            if target_profile:
                profile_games_qs = ProfileGame.objects.filter(profile=target_profile, game__in=games).select_related('game')
                profile_games = {pg.game: pg for pg in profile_games_qs}

            community_ratings = {}
            for game in games:
                averages_cache_key = f"concept:averages:{game.concept.concept_id}:{today}"
                cached_averages = cache.get(averages_cache_key)
                if cached_averages:
                    averages = json.loads(cached_averages)
                else:
                    averages = game.concept.get_community_averages()
                    if averages:
                        cache.set(averages_cache_key, json.dumps(averages), timeout=stats_timeout)
                community_ratings[game] = averages

            structured_data.append({
                'stage': stage,
                'games': [{'game': game, 'profile_game': profile_games.get(game, None), 'community_ratings': community_ratings.get(game, None)} for game in games]
            })

        all_badges = Badge.objects.by_series(badge.series_slug)
        badge_completion = {b.tier: b.get_stage_completion(target_profile, b.badge_type) for b in all_badges}

        # Add required_stages for each tier (useful for megamix badges)
        badge_requirements = {b.tier: b.required_stages for b in all_badges}

        logger.debug(f"Badge detail loaded {len(structured_data)} stage data entries for {badge.series_slug}")
        context['stage_data'] = structured_data
        context['completion'] = badge_completion
        context['badge_requirements'] = badge_requirements
        context['is_earned'] = is_earned

        context['image_urls'] = {'bg_url': badge.most_recent_concept.bg_url, 'recent_concept_icon_url': badge.most_recent_concept.concept_icon_url}
        context['recent_concept_name'] = badge.most_recent_concept.unified_title

        context['breadcrumb'] = [
            {'text': 'Home', 'url': reverse_lazy('home')},
            {'text': 'Badges', 'url': reverse_lazy('badges_list')},
            {'text': context['badge'].effective_display_series},
        ]

        return context


class BadgeLeaderboardsView(ProfileHotbarMixin, DetailView):
    """
    Display leaderboards for a specific badge series.

    Shows two leaderboards:
    1. Earners - Users who have earned the highest tier
    2. Progress - Users making progress on the badge series

    Leaderboards are cached and refreshed periodically. Shows user's rank if authenticated.
    """
    model = Badge
    template_name = 'trophies/badge_leaderboards.html'
    slug_field = 'series_slug'
    slug_url_kwarg = 'series_slug'
    context_object_name = 'series_badges'

    def get_object(self, queryset=None):
        series_slug = self.kwargs[self.slug_url_kwarg]
        return Badge.objects.get(series_slug=series_slug, tier=1)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        badge = self.object
        series_slug = badge.series_slug
        user = self.request.user

        earners_key = f"lb_earners_{series_slug}"
        progress_key = f"lb_progress_{series_slug}"

        lb_earners = cache.get(earners_key, [])
        lb_earners_paginate_by = 50
        lb_progress = cache.get(progress_key, [])
        lb_progress_paginate_by = 50

        context['lb_earners_refresh_time'] = cache.get(f"{earners_key}_refresh_time")
        context['lb_progress_refresh_time'] = cache.get(f"{progress_key}_refresh_time")

        if user.is_authenticated and hasattr(user, 'profile'):
            # Find user profile
            user_psn = user.profile.display_psn_username
            for idx, entry in enumerate(lb_earners):
                if entry['psn_username'] == user_psn:
                    context['lb_earners_user_page'] = (idx // lb_earners_paginate_by) + 1
                    context['lb_earners_user_rank'] = idx + 1
                    break
            for idx, entry in enumerate(lb_progress):
                if entry['psn_username'] == user_psn:
                    context['lb_progress_user_page'] = (idx // lb_progress_paginate_by) + 1
                    context['lb_progress_user_rank'] = idx + 1

        lb_earners_paginator = Paginator(lb_earners, lb_earners_paginate_by)
        lb_earners_page = self.request.GET.get('lb_earners_page', 1)
        context['lb_earners_page_obj'] = lb_earners_paginator.get_page(lb_earners_page)
        context['lb_earners_paginator'] = lb_earners_paginator

        lb_progress_paginator = Paginator(lb_progress, lb_progress_paginate_by)
        lb_progress_page = self.request.GET.get('lb_progress_page', 1)
        context['lb_progress_page_obj'] = lb_progress_paginator.get_page(lb_progress_page)
        context['lb_progress_paginator'] = lb_progress_paginator

        context['badge'] = badge
        context['image_urls'] = {'bg_url': badge.most_recent_concept.bg_url, 'recent_concept_icon_url': badge.most_recent_concept.concept_icon_url}
        context['breadcrumb'] = [
            {'text': 'Home', 'url': reverse_lazy('home')},
            {'text': 'Badges', 'url': reverse_lazy('badges_list')},
            {'text': context['badge'].effective_display_series, 'url': reverse_lazy('badge_detail', kwargs={'series_slug': badge.series_slug})},
            {'text': 'Leaderboards'},
        ]

        return context


class OverallBadgeLeaderboardsView(ProfileHotbarMixin, TemplateView):
    """
    Display overall badge leaderboards across all badge series.

    Shows two global leaderboards:
    1. Total XP - Users with the most badge experience points
    2. Total Progress - Users with the most badge completion percentage

    Leaderboards are cached and refreshed periodically. Shows user's rank if authenticated.
    """
    template_name = 'trophies/overall_badge_leaderboards.html'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        user = self.request.user

        xp_key = f"lb_total_xp"
        progress_key = f"lb_total_progress"

        lb_total_xp = cache.get(xp_key, [])
        lb_total_xp_paginate_by = 50
        lb_total_progress = cache.get(progress_key, [])
        lb_total_progress_paginate_by = 50

        context['lb_total_xp_refresh_time'] = cache.get(f"{xp_key}_refresh_time")
        context['lb_total_progress_refresh_time'] = cache.get(f"{progress_key}_refresh_time")

        if user.is_authenticated and hasattr(user, 'profile'):
            # Find user profile
            user_psn = user.profile.display_psn_username
            for idx, entry in enumerate(lb_total_xp):
                if entry['psn_username'] == user_psn:
                    context['lb_total_xp_user_page'] = (idx // lb_total_xp_paginate_by) + 1
                    context['lb_total_xp_user_rank'] = idx + 1
                    break
            for idx, entry in enumerate(lb_total_progress):
                if entry['psn_username'] == user_psn:
                    context['lb_total_progress_user_page'] = (idx // lb_total_progress_paginate_by) + 1
                    context['lb_total_progress_user_rank'] = idx + 1

        lb_total_xp_paginator = Paginator(lb_total_xp, lb_total_xp_paginate_by)
        lb_total_xp_page = self.request.GET.get('lb_total_xp_page', 1)
        context['lb_total_xp_page_obj'] = lb_total_xp_paginator.get_page(lb_total_xp_page)
        context['lb_total_xp_paginator'] = lb_total_xp_paginator

        lb_total_progress_paginator = Paginator(lb_total_progress, lb_total_progress_paginate_by)
        lb_total_progress_page = self.request.GET.get('lb_total_progress_page', 1)
        context['lb_total_progress_page_obj'] = lb_total_progress_paginator.get_page(lb_total_progress_page)
        context['lb_total_progress_paginator'] = lb_total_progress_paginator

        context['breadcrumb'] = [
            {'text': 'Home', 'url': reverse_lazy('home')},
            {'text': 'Badges', 'url': reverse_lazy('badges_list')},
            {'text': 'Leaderboards'},
        ]

        return context


class MilestoneListView(ProfileHotbarMixin, ListView):
    """
    Display list of all milestones with progress tracking for authenticated users.

    Shows all milestones ordered by required value, with earned status and
    completion progress for logged-in users. Basic info shown for guests.
    """
    model = Milestone
    template_name = 'trophies/milestone_list.html'
    context_object_name = 'milestones'

    def get_queryset(self):
        """
        Fetch milestones ordered by required_value.

        Returns:
            QuerySet: All milestones ordered by required value ascending
        """
        return Milestone.objects.ordered_by_value()

    def _build_milestone_display_data(self, milestones, profile=None):
        """
        Build display data for milestones with optional progress tracking.

        For authenticated users, filters milestones to show only:
        - All earned milestones
        - The next unearned milestone for each criteria_type

        Args:
            milestones: QuerySet of Milestone objects
            profile: Profile instance or None

        Returns:
            list: Display data dicts for each milestone
        """
        display_data = []

        # Get user progress/earned data if authenticated
        earned_milestone_ids = set()
        progress_dict = {}

        if profile:
            # Get earned milestones
            earned_milestone_ids = set(
                UserMilestone.objects.filter(profile=profile)
                .values_list('milestone_id', flat=True)
            )

            # Get progress for all milestones
            progress_qs = UserMilestoneProgress.objects.filter(
                profile=profile,
                milestone__in=milestones
            )
            progress_dict = {p.milestone_id: p.progress_value for p in progress_qs}

        # Group milestones by criteria_type
        milestones_by_type = {}
        for milestone in milestones:
            criteria_type = milestone.criteria_type
            if criteria_type not in milestones_by_type:
                milestones_by_type[criteria_type] = []
            milestones_by_type[criteria_type].append(milestone)

        # Calculate tier info for each milestone type (total tiers and current tier)
        tier_info = {}
        for criteria_type, type_milestones in milestones_by_type.items():
            # Sort by required_value to ensure proper ordering
            sorted_milestones = sorted(type_milestones, key=lambda m: m.required_value)
            total_tiers = len(sorted_milestones)

            # Find current tier (1-indexed) - the tier the user is working on
            current_tier = 1
            for idx, m in enumerate(sorted_milestones, start=1):
                if m.id in earned_milestone_ids:
                    # User has completed this tier, move to next
                    current_tier = idx + 1
                else:
                    # Found first unearned tier - this is what they're working on
                    current_tier = idx
                    break

            # If all tiers are earned, current_tier will be total_tiers + 1
            # Cap it at total_tiers
            if current_tier > total_tiers:
                current_tier = total_tiers

            tier_info[criteria_type] = {
                'total_tiers': total_tiers,
                'current_tier': current_tier
            }

        # Filter to show only earned + next unearned per criteria_type (only for authenticated users)
        if profile:
            filtered_milestones = []
            for criteria_type, type_milestones in milestones_by_type.items():
                # Sort by required_value to ensure proper ordering
                type_milestones.sort(key=lambda m: m.required_value)

                # Add all earned milestones and track if we found the next unearned
                found_next_unearned = False
                for milestone in type_milestones:
                    is_earned = milestone.id in earned_milestone_ids

                    if is_earned:
                        # Include all earned milestones
                        filtered_milestones.append(milestone)
                    elif not found_next_unearned:
                        # Include the first unearned milestone (the next one to work towards)
                        filtered_milestones.append(milestone)
                        found_next_unearned = True
                    # Skip all other unearned milestones
        else:
            # For guests, show all milestones
            filtered_milestones = list(milestones)

        # Build display data for filtered milestones
        for milestone in filtered_milestones:
            is_earned = milestone.id in earned_milestone_ids
            progress_value = progress_dict.get(milestone.id, 0)
            required_value = milestone.required_value

            # Calculate progress percentage
            if required_value > 0:
                progress_percentage = min((progress_value / required_value) * 100, 100)
            else:
                progress_percentage = 100 if is_earned else 0

            # Get tier information for this milestone
            criteria_type = milestone.criteria_type
            milestone_tier_info = tier_info.get(criteria_type, {'total_tiers': 1, 'current_tier': 1})

            display_data.append({
                'milestone': milestone,
                'is_earned': is_earned,
                'progress_value': progress_value,
                'required_value': required_value,
                'progress_percentage': round(progress_percentage, 1),
                'earned_count': milestone.earned_count,
                'total_tiers': milestone_tier_info['total_tiers'],
                'current_tier': milestone_tier_info['current_tier'],
            })

        return display_data

    def get_context_data(self, **kwargs):
        """
        Build context for milestone list page.

        Returns:
            dict: Context with milestone display data
        """
        context = super().get_context_data(**kwargs)
        milestones = context['object_list']

        # Get profile for authenticated users
        user = self.request.user
        profile = user.profile if user.is_authenticated and hasattr(user, 'profile') else None

        # Build display data
        display_data = self._build_milestone_display_data(milestones, profile)

        # Sort display data: unearned milestones first (by progress % descending),
        # then earned milestones (by required_value ascending)
        if profile:
            display_data.sort(
                key=lambda x: (
                    x['is_earned'],  # False (0) before True (1) - unearned first
                    -x['progress_percentage'] if not x['is_earned'] else 0,  # Higher progress first for unearned
                    x['milestone'].required_value if x['is_earned'] else 0  # Lower required_value first for earned
                )
            )

        context['display_data'] = display_data

        # Breadcrumbs
        context['breadcrumb'] = [
            {'text': 'Home', 'url': reverse_lazy('home')},
            {'text': 'Badges', 'url': reverse_lazy('badges_list')},
            {'text': 'Milestones'},
        ]

        return context
