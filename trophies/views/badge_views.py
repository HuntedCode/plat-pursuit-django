import json
import logging
from collections import defaultdict

from core.services.tracking import track_page_view
from datetime import date

from django.core.cache import cache
from django.core.paginator import Paginator
from django.db.models import Q, F, Prefetch, Max
from django.db.models.functions import Lower
from django.http import Http404
from django.shortcuts import get_object_or_404
from django.urls import reverse_lazy, reverse
from django.utils.text import slugify
from django.views.generic import ListView, DetailView, TemplateView

from trophies.mixins import ProfileHotbarMixin
from ..models import (
    Game, Profile, ProfileGame, Badge, UserBadge, UserBadgeProgress,
    Concept, Stage, Milestone, UserMilestone, UserMilestoneProgress,
    UserTitle,
)
from ..forms import BadgeSearchForm
from trophies.milestone_constants import (
    MILESTONE_CATEGORIES, CRITERIA_TYPE_DISPLAY_NAMES,
    CALENDAR_MONTH_TYPES, ONE_OFF_TYPES,
)

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

        track_page_view('badges_list', 'list', self.request)
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
            max_tier = series_badges.aggregate(max_tier=Max('tier'))['max_tier'] or 0
            badge = series_badges.filter(tier=highest_tier_earned).first()
            if not badge:
                badge = series_badges.order_by('tier').first()
                is_earned = False
            context['is_maxed'] = highest_tier_earned > 0 and highest_tier_earned == max_tier

            context['badge'] = badge

            progress = UserBadgeProgress.objects.filter(profile=target_profile, badge=badge).first()
            context['progress'] = progress
            context['progress_percent'] = progress.completed_concepts / badge.required_stages * 100 if progress and badge.required_stages > 0 else 0
        else:
            badge = series_badges.filter(tier=1).first()
            context['badge'] = badge

        stages = Stage.objects.filter(series_slug=badge.series_slug).order_by('stage_number').prefetch_related(
            Prefetch('concepts__games', queryset=Game.objects.all().order_by(Lower('title_name')))
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

        if badge.most_recent_concept:
            context['image_urls'] = {'bg_url': badge.most_recent_concept.bg_url, 'recent_concept_icon_url': badge.most_recent_concept.concept_icon_url}
            context['recent_concept_name'] = badge.most_recent_concept.unified_title
        else:
            context['image_urls'] = {'bg_url': '', 'recent_concept_icon_url': ''}
            context['recent_concept_name'] = ''

        context['breadcrumb'] = [
            {'text': 'Home', 'url': reverse_lazy('home')},
            {'text': 'Badges', 'url': reverse_lazy('badges_list')},
            {'text': context['badge'].effective_display_series},
        ]

        series_slug = self.kwargs['series_slug']
        track_page_view('badge', series_slug, self.request)
        tier1_badge = series_badges.filter(tier=1).first()
        context['view_count'] = tier1_badge.view_count if tier1_badge else 0

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
        if badge.most_recent_concept:
            context['image_urls'] = {'bg_url': badge.most_recent_concept.bg_url, 'recent_concept_icon_url': badge.most_recent_concept.concept_icon_url}
        else:
            context['image_urls'] = {'bg_url': '', 'recent_concept_icon_url': ''}
        context['breadcrumb'] = [
            {'text': 'Home', 'url': reverse_lazy('home')},
            {'text': 'Badges', 'url': reverse_lazy('badges_list')},
            {'text': context['badge'].effective_display_series, 'url': reverse_lazy('badge_detail', kwargs={'series_slug': badge.series_slug})},
            {'text': 'Leaderboards'},
        ]

        track_page_view('badge_leaderboard', badge.series_slug, self.request)
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

        track_page_view('overall_leaderboard', 'global', self.request)
        return context



class MilestoneListView(ProfileHotbarMixin, ListView):
    """
    Display milestones organized by category tabs with tier ladder progression.

    Tabs: Overview, Trophy Hunting, Community, Collection, Challenges,
    Getting Started, Special. Each category shows tier ladders for its
    criteria types with earned/active/locked visual states.
    """
    model = Milestone
    template_name = 'trophies/milestone_list.html'
    context_object_name = 'milestones'

    def get_queryset(self):
        return Milestone.objects.select_related('title').ordered_by_value()

    def _get_user_data(self, profile, milestones):
        """Fetch earned milestones, progress, and earned dates for a profile."""
        if not profile:
            return set(), {}, {}

        earned_qs = UserMilestone.objects.filter(
            profile=profile, milestone__in=milestones
        ).select_related('milestone')
        earned_milestone_ids = set()
        earned_dates = {}
        for um in earned_qs:
            earned_milestone_ids.add(um.milestone_id)
            earned_dates[um.milestone_id] = um.earned_at

        progress_qs = UserMilestoneProgress.objects.filter(
            profile=profile, milestone__in=milestones
        )
        progress_dict = {p.milestone_id: p.progress_value for p in progress_qs}

        return earned_milestone_ids, progress_dict, earned_dates

    def _build_tier_ladder(self, type_milestones, earned_ids, progress_dict, earned_dates, profile):
        """Build tier ladder data for a single criteria_type group."""
        sorted_ms = sorted(type_milestones, key=lambda m: m.required_value)
        total_tiers = len(sorted_ms)
        tiers = []
        found_active = False

        for idx, milestone in enumerate(sorted_ms, start=1):
            is_earned = milestone.id in earned_ids
            progress_value = progress_dict.get(milestone.id, 0)
            required_value = milestone.required_value

            if required_value > 0:
                progress_pct = min((progress_value / required_value) * 100, 100)
            else:
                progress_pct = 100 if is_earned else 0

            # Determine state: earned, active (next target), locked, or preview (guest)
            if is_earned:
                state = 'earned'
            elif not profile:
                state = 'preview'
            elif not found_active:
                state = 'active'
                found_active = True
            else:
                state = 'locked'

            tiers.append({
                'milestone': milestone,
                'tier_number': idx,
                'total_tiers': total_tiers,
                'state': state,
                'is_earned': is_earned,
                'progress_value': progress_value,
                'required_value': required_value,
                'progress_percentage': round(progress_pct, 1),
                'earned_count': milestone.earned_count,
                'earned_at': earned_dates.get(milestone.id),
            })

        earned_count = sum(1 for t in tiers if t['is_earned'])
        return {
            'tiers': tiers,
            'total_tiers': total_tiers,
            'earned_tiers': earned_count,
        }

    def _build_category_data(self, milestones, category_config, profile,
                             earned_ids, progress_dict, earned_dates):
        """Build tier ladders for all criteria_types in a category."""
        criteria_types = category_config['criteria_types']

        # Group milestones by criteria_type
        by_type = defaultdict(list)
        for m in milestones:
            if m.criteria_type in criteria_types:
                by_type[m.criteria_type].append(m)

        # Build ladders, preserving the order from criteria_types
        # Separate tiered types from calendar month types
        tiered_ladders = []
        calendar_months = []
        oneoff_cards = []

        for ct in criteria_types:
            if ct not in by_type:
                continue
            display_name = CRITERIA_TYPE_DISPLAY_NAMES.get(ct, ct)

            if ct in CALENDAR_MONTH_TYPES:
                # Calendar months go into the grid
                ms = by_type[ct][0]  # One milestone per month
                calendar_months.append({
                    'milestone': ms,
                    'criteria_type': ct,
                    'month_abbr': ct.replace('calendar_month_', '').upper(),
                    'is_earned': ms.id in earned_ids,
                    'earned_at': earned_dates.get(ms.id),
                })
            elif ct in ONE_OFF_TYPES:
                # One-off milestones (calendar_complete, psn_linked, etc.)
                ms = by_type[ct][0]
                progress_value = progress_dict.get(ms.id, 0)
                required_value = ms.required_value
                if required_value > 0:
                    pct = min((progress_value / required_value) * 100, 100)
                else:
                    pct = 100 if ms.id in earned_ids else 0
                oneoff_cards.append({
                    'milestone': ms,
                    'criteria_type': ct,
                    'display_name': display_name,
                    'is_earned': ms.id in earned_ids,
                    'earned_at': earned_dates.get(ms.id),
                    'progress_value': progress_value,
                    'required_value': required_value,
                    'progress_percentage': round(pct, 1),
                    'earned_count': ms.earned_count,
                })
            else:
                # Tiered ladder
                ladder = self._build_tier_ladder(
                    by_type[ct], earned_ids, progress_dict, earned_dates, profile
                )
                ladder['criteria_type'] = ct
                ladder['display_name'] = display_name
                tiered_ladders.append(ladder)

        calendar_months_earned = sum(1 for m in calendar_months if m['is_earned'])
        return {
            'tiered_ladders': tiered_ladders,
            'calendar_months': calendar_months,
            'calendar_months_earned': calendar_months_earned,
            'oneoff_cards': oneoff_cards,
        }

    def _build_overview_data(self, milestones, profile, earned_ids, earned_dates):
        """Build overview tab data with per-category stats."""
        # Per-category earned/total counts
        category_stats = []
        for slug, config in MILESTONE_CATEGORIES.items():
            if slug == 'overview':
                continue
            types = config['criteria_types']
            cat_milestones = [m for m in milestones if m.criteria_type in types]
            total = len(cat_milestones)
            earned = sum(1 for m in cat_milestones if m.id in earned_ids) if profile else 0
            pct = round((earned / total * 100), 1) if total > 0 else 0

            # Find most recently earned milestones in this category
            recent_earned = []
            if profile:
                cat_earned = [
                    (m, earned_dates[m.id])
                    for m in cat_milestones
                    if m.id in earned_ids and m.id in earned_dates
                ]
                cat_earned.sort(key=lambda x: x[1], reverse=True)
                recent_earned = [item[0] for item in cat_earned[:2]]

            # Special/manual milestones are "Feats of Strength": they exist and
            # can be earned, but don't count toward totals or show a denominator
            is_feats = (slug == 'special')

            category_stats.append({
                'slug': slug,
                'name': config['name'],
                'icon': config['icon'],
                'total': total,
                'earned': earned,
                'percentage': pct,
                'recent_earned': recent_earned,
                'is_feats_of_strength': is_feats,
            })

        # Overall stats (exclude special/manual milestones from totals)
        countable = [m for m in milestones if m.criteria_type != 'manual']
        total_milestones = len(countable)
        total_earned = sum(1 for m in countable if m.id in earned_ids) if profile else 0
        overall_pct = round((total_earned / total_milestones * 100), 1) if total_milestones > 0 else 0

        # Titles unlocked from milestones
        titles_unlocked = 0
        if profile:
            titles_unlocked = UserTitle.objects.filter(
                profile=profile, source_type='milestone'
            ).count()

        # Most recently earned milestone (derived from earned_dates, no extra query)
        latest_milestone = None
        if profile and earned_dates:
            latest_id = max(earned_dates, key=earned_dates.get)
            latest_ms = next((m for m in milestones if m.id == latest_id), None)
            if latest_ms:
                latest_milestone = {
                    'milestone': latest_ms,
                    'earned_at': earned_dates[latest_id],
                }

        return {
            'category_stats': category_stats,
            'total_milestones': total_milestones,
            'total_earned': total_earned,
            'overall_percentage': overall_pct,
            'titles_unlocked': titles_unlocked,
            'latest_milestone': latest_milestone,
        }

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        milestones = list(context['object_list'])

        user = self.request.user
        profile = user.profile if user.is_authenticated and hasattr(user, 'profile') else None

        # Get current category tab
        current_cat = self.request.GET.get('cat', 'overview')
        if current_cat not in MILESTONE_CATEGORIES:
            current_cat = 'overview'

        # Fetch user data once
        earned_ids, progress_dict, earned_dates = self._get_user_data(profile, milestones)

        # Build tab badge counts for all categories
        tab_data = []
        for slug, config in MILESTONE_CATEGORIES.items():
            if slug == 'overview':
                tab_data.append({'slug': slug, 'name': config['name'], 'icon': config['icon']})
                continue
            types = config['criteria_types']
            cat_ms = [m for m in milestones if m.criteria_type in types]
            total = len(cat_ms)
            earned = sum(1 for m in cat_ms if m.id in earned_ids) if profile else 0
            tab_data.append({
                'slug': slug,
                'name': config['name'],
                'icon': config['icon'],
                'total': total,
                'earned': earned,
                'is_feats_of_strength': (slug == 'special'),
            })

        context['current_cat'] = current_cat
        context['tab_data'] = tab_data

        if current_cat == 'overview':
            context['overview_data'] = self._build_overview_data(
                milestones, profile, earned_ids, earned_dates
            )
        else:
            category_config = MILESTONE_CATEGORIES[current_cat]
            context['category_name'] = category_config['name']
            context['category_data'] = self._build_category_data(
                milestones, category_config, profile,
                earned_ids, progress_dict, earned_dates
            )

        context['breadcrumb'] = [
            {'text': 'Home', 'url': reverse_lazy('home')},
            {'text': 'Badges', 'url': reverse_lazy('badges_list')},
            {'text': 'Milestones'},
        ]

        track_page_view('milestones_list', 'list', self.request)
        return context
