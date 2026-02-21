import logging
from collections import defaultdict

from core.services.tracking import track_page_view
from datetime import timedelta
from trophies.util_modules.constants import (
    BADGE_TIER_XP, BRONZE_STAGE_XP, SILVER_STAGE_XP,
    GOLD_STAGE_XP, PLAT_STAGE_XP,
)

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
    UserTitle, ProfileGamification,
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
        qs = super().get_queryset().live().select_related(
            'base_badge', 'most_recent_concept', 'title',
            'base_badge__most_recent_concept', 'base_badge__title',
        )
        form = BadgeSearchForm(self.request.GET)

        if form.is_valid():
            series_slug = slugify(form.cleaned_data.get('series_slug'))
            if series_slug:
                qs = qs.filter(series_slug__icontains=series_slug)
        return qs

    def _calculate_all_series_stats(self, series_slugs):
        """
        Calculate total games and trophy counts for multiple badge series in bulk.

        Single query fetches all games across all requested series, then groups
        in memory. Eliminates N*2 queries (count + iteration per series).

        Args:
            series_slugs: Iterable of series slug strings

        Returns:
            dict: {series_slug: (total_games, trophy_types_dict)}
        """
        games_with_series = Game.objects.filter(
            concept__stages__series_slug__in=series_slugs
        ).values_list(
            'id', 'concept__stages__series_slug',
            'defined_trophies',
        ).distinct()

        # Group games by series slug
        series_games = defaultdict(dict)
        for game_id, slug, trophies in games_with_series:
            if game_id not in series_games[slug]:
                series_games[slug][game_id] = trophies

        result = {}
        for slug in series_slugs:
            games_map = series_games.get(slug, {})
            total_games = len(games_map)
            trophy_types = {'bronze': 0, 'silver': 0, 'gold': 0, 'platinum': 0}
            for trophies in games_map.values():
                if trophies:
                    trophy_types['bronze'] += trophies.get('bronze', 0)
                    trophy_types['silver'] += trophies.get('silver', 0)
                    trophy_types['gold'] += trophies.get('gold', 0)
                    trophy_types['platinum'] += trophies.get('platinum', 0)
            result[slug] = (total_games, trophy_types)

        return result

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
            progress_qs = UserBadgeProgress.objects.filter(
                profile=profile, badge__id__in=all_badges_ids
            ).select_related('badge')
            progress_dict = {p.badge_id: p for p in progress_qs}

        # Bulk-fetch series stats for all series at once (1 query instead of N*2)
        all_series_stats = self._calculate_all_series_stats(grouped_badges.keys())

        # Build display data for each series
        for slug, group in grouped_badges.items():
            sorted_group = sorted(group, key=lambda b: b.tier)
            if not sorted_group:
                continue

            tier1_badge = next((b for b in sorted_group if b.tier == 1), None)
            if not tier1_badge:
                continue

            # Look up pre-computed series stats
            total_games, trophy_types = all_series_stats.get(slug, (0, {'bronze': 0, 'silver': 0, 'gold': 0, 'platinum': 0}))
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

        # Group badges by series. Badges without an effective_user_title are
        # treated as unpublished/WIP and hidden from the public listing.
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

        # User badge stats for authenticated users
        if profile:
            try:
                gamification = profile.gamification
                series_completed = UserBadge.objects.filter(
                    profile=profile
                ).values('badge__series_slug').distinct().count()
                total_series = Badge.objects.live().filter(tier=1).exclude(
                    series_slug__isnull=True
                ).exclude(series_slug='').count()

                # Global stage completion stats (all badge series)
                total_stages = Stage.objects.filter(stage_number__gt=0).count()
                plat_eligible_stages = Stage.objects.filter(
                    stage_number__gt=0,
                    concepts__games__defined_trophies__platinum__gt=0,
                ).distinct().count()
                user_stages_platted = Stage.objects.filter(
                    stage_number__gt=0,
                    concepts__games__played_by__profile=profile,
                    concepts__games__played_by__has_plat=True,
                ).distinct().count()
                user_stages_completed = Stage.objects.filter(
                    stage_number__gt=0,
                    concepts__games__played_by__profile=profile,
                    concepts__games__played_by__progress=100,
                ).distinct().count()

                context['user_badge_stats'] = {
                    'total_xp': gamification.total_badge_xp,
                    'total_badges_earned': gamification.total_badges_earned,
                    'stages_platted': user_stages_platted,
                    'plat_eligible_stages': plat_eligible_stages,
                    'stages_completed': user_stages_completed,
                    'total_stages': total_stages,
                    'series_completed': series_completed,
                    'total_series': total_series,
                    'completion_pct': round(
                        (series_completed / total_series * 100), 1
                    ) if total_series > 0 else 0,
                }
                context['series_badge_xp'] = gamification.series_badge_xp or {}
            except ProfileGamification.DoesNotExist:
                context['user_badge_stats'] = None
                context['series_badge_xp'] = {}

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
        return Badge.objects.by_series(series_slug).select_related('funded_by')

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        series_badges = context['object']

        if not series_badges.exists():
            raise Http404("Series not found")

        # Staff preview gate: non-live badges are staff-only
        first_badge = series_badges.first()
        if first_badge and not first_badge.is_live:
            if not self.request.user.is_staff:
                raise Http404("Series not found")
            context['is_staff_preview'] = True

        psn_username = self.kwargs.get('psn_username')
        if psn_username:
            target_profile = get_object_or_404(Profile, psn_username__iexact=psn_username)
        elif self.request.user.is_authenticated and hasattr(self.request.user, 'profile'):
            target_profile = self.request.user.profile
        else:
            target_profile = None

        context['target_profile'] = target_profile

        badge = None
        is_earned = False
        highest_tier_earned = 0
        max_tier = series_badges.aggregate(max_tier=Max('tier'))['max_tier'] or 0

        # Bulk-fetch progress for all badges in this series (single query, reused below)
        badge_progress_dict = {}
        if target_profile:
            badge_progress_dict = {
                p.badge_id: p for p in
                UserBadgeProgress.objects.filter(
                    profile=target_profile, badge__series_slug=self.kwargs['series_slug']
                )
            }

        if target_profile:
            highest_tier_earned = UserBadge.objects.filter(profile=target_profile, badge__series_slug=self.kwargs['series_slug']).aggregate(max_tier=Max('badge__tier'))['max_tier'] or 0
            badge = series_badges.filter(tier=highest_tier_earned).first()
            if badge and highest_tier_earned > 0:
                is_earned = True
            else:
                badge = series_badges.order_by('tier').first()
            context['is_maxed'] = highest_tier_earned > 0 and highest_tier_earned == max_tier

            context['badge'] = badge

            progress = badge_progress_dict.get(badge.id)
            context['progress'] = progress
            context['progress_percent'] = progress.completed_concepts / badge.required_stages * 100 if progress and badge.required_stages > 0 else 0
        else:
            badge = series_badges.filter(tier=1).first()
            context['badge'] = badge

        # Tier selector: determine which tier tab to show
        tier_param = self.request.GET.get('tier')
        try:
            selected_tier = int(tier_param)
            if selected_tier < 1 or selected_tier > max_tier:
                selected_tier = None
        except (TypeError, ValueError):
            selected_tier = None

        if selected_tier is None:
            if target_profile and highest_tier_earned > 0:
                selected_tier = min(highest_tier_earned + 1, max_tier)
            else:
                selected_tier = 1
        context['selected_tier'] = selected_tier
        context['max_tier'] = max_tier
        # Whether selected tier requires platinum (tiers 1/3) or 100% (tiers 2/4)
        context['selected_tier_is_plat'] = selected_tier in [1, 3]

        stages = list(Stage.objects.filter(series_slug=badge.series_slug).order_by('stage_number').prefetch_related(
            Prefetch('concepts__games', queryset=Game.objects.select_related('concept').order_by(Lower('title_name')))
        ))
        context['stage_count'] = len(stages)

        # Collect all games across all stages first (uses prefetched data)
        stage_games_map = {}
        all_games_set = set()
        for stage in stages:
            games = set()
            for concept in stage.concepts.all():
                games.update(concept.games.all())
            stage_games_map[stage.id] = sorted(games, key=lambda g: g.title_name)
            all_games_set.update(games)

        # Single bulk ProfileGame query instead of one per stage
        profile_games = {}
        if target_profile and all_games_set:
            profile_games_qs = ProfileGame.objects.filter(
                profile=target_profile, game__in=all_games_set
            ).select_related('game')
            profile_games = {pg.game_id: pg for pg in profile_games_qs}

        from trophies.services.rating_service import RatingService

        structured_data = []
        for stage in stages:
            games = stage_games_map[stage.id]

            community_ratings = {}
            for game in games:
                community_ratings[game] = RatingService.get_cached_community_averages(game.concept)

            structured_data.append({
                'stage': stage,
                'games': [{
                    'game': game,
                    'profile_game': profile_games.get(game.id),
                    'community_ratings': community_ratings.get(game),
                    'has_guide': bool(game.concept.guide_slug),
                } for game in games],
            })

        all_badges = Badge.objects.by_series(badge.series_slug)
        all_badges_list = list(all_badges)
        badge_completion = {b.tier: b.get_stage_completion(target_profile, b.badge_type) for b in all_badges_list}


        # Add required_stages for each tier (useful for megamix badges)
        badge_requirements = {b.tier: b.required_stages for b in all_badges_list}

        # Tier selector context
        context['all_tier_badges'] = sorted(all_badges_list, key=lambda b: b.tier)
        selected_tier_badge = next((b for b in all_badges_list if b.tier == selected_tier), None)
        context['selected_tier_badge'] = selected_tier_badge
        context['selected_tier_completion'] = badge_completion.get(selected_tier, {})
        context['selected_tier_requirements'] = badge_requirements.get(selected_tier, 0)

        # Badge series stats (computed from existing data, no new DB queries)
        tier_earner_counts = {b.tier: b.earned_count for b in all_badges_list}

        total_games = sum(len(data['games']) for data in structured_data)

        user_series_xp = 0
        user_lb_rank = None
        user_lb_progress_rank = None
        user_total_playtime = None
        user_games_played = 0
        user_platinums = 0
        series_slug = self.kwargs['series_slug']
        lb_earners = cache.get(f"lb_earners_{series_slug}", [])
        lb_progress = cache.get(f"lb_progress_{series_slug}", [])
        if target_profile:
            try:
                xp_data = target_profile.gamification.series_badge_xp
                user_series_xp = (xp_data or {}).get(series_slug, 0)
            except Exception:
                pass
            user_psn = target_profile.display_psn_username
            for idx, entry in enumerate(lb_earners):
                if entry['psn_username'] == user_psn:
                    user_lb_rank = idx + 1
                    break
            for idx, entry in enumerate(lb_progress):
                if entry['psn_username'] == user_psn:
                    user_lb_progress_rank = idx + 1
                    break

            # User playtime stats from already-fetched profile_games
            total_duration = timedelta()
            for pg in profile_games.values():
                user_games_played += 1
                if pg.play_duration:
                    total_duration += pg.play_duration
                if pg.has_plat:
                    user_platinums += 1
            user_total_playtime = total_duration if total_duration.total_seconds() > 0 else None

            # Per-stage user stats and progress
            # For stage progress: tiers 1/3 and megamix check has_plat, tiers 2/4 check 100%
            is_plat_tier = selected_tier in [1, 3] or badge.badge_type == 'megamix'
            for data in structured_data:
                stage_game_ids = {g['game'].id for g in data['games']}
                stage_duration = timedelta()
                stage_played = 0
                stage_plats = 0
                stage_completed = 0
                for game_id in stage_game_ids:
                    pg = profile_games.get(game_id)
                    if pg:
                        stage_played += 1
                        if pg.play_duration:
                            stage_duration += pg.play_duration
                        if pg.has_plat:
                            stage_plats += 1
                        if (is_plat_tier and pg.has_plat) or (not is_plat_tier and pg.progress == 100):
                            stage_completed += 1
                total_stage_games = len(stage_game_ids)
                data['user_stage_stats'] = {
                    'total_playtime': stage_duration if stage_duration.total_seconds() > 0 else None,
                    'games_played': stage_played,
                    'total_games': total_stage_games,
                    'platinums': stage_plats,
                }
                data['stage_progress'] = {
                    'completed': stage_completed,
                    'total': total_stage_games,
                    'percentage': round(stage_completed / total_stage_games * 100, 1) if total_stage_games else 0,
                }
                # 3-state completion indicator
                has_any_100 = any(
                    profile_games.get(gid) and profile_games[gid].progress == 100
                    for gid in stage_game_ids
                )
                has_any_plat = stage_plats > 0
                if has_any_100:
                    data['stage_completion_state'] = 'complete'
                elif has_any_plat:
                    data['stage_completion_state'] = 'partial'
                else:
                    data['stage_completion_state'] = 'incomplete'

        # Aggregated stats from already-fetched profile_games (no new queries)
        avg_progress = 0
        total_trophies_earned = 0
        user_trophy_breakdown = {'bronze': 0, 'silver': 0, 'gold': 0, 'platinum': 0}
        first_played = None
        most_recent_trophy = None
        if target_profile and profile_games:
            total_progress = sum(pg.progress for pg in profile_games.values())
            avg_progress = round(total_progress / len(profile_games), 1)
            total_trophies_earned = sum(pg.earned_trophies_count for pg in profile_games.values())
            for pg in profile_games.values():
                et = pg.earned_trophies or {}
                user_trophy_breakdown['bronze'] += et.get('bronze', 0)
                user_trophy_breakdown['silver'] += et.get('silver', 0)
                user_trophy_breakdown['gold'] += et.get('gold', 0)
                user_trophy_breakdown['platinum'] += et.get('platinum', 0)
                if pg.first_played_date_time:
                    if first_played is None or pg.first_played_date_time < first_played:
                        first_played = pg.first_played_date_time
                if pg.most_recent_trophy_date:
                    if most_recent_trophy is None or pg.most_recent_trophy_date > most_recent_trophy:
                        most_recent_trophy = pg.most_recent_trophy_date

        # Avg community difficulty / hours across all series games
        total_difficulty = 0
        total_hours = 0
        rated_games_count = 0
        for data in structured_data:
            for game_entry in data['games']:
                ratings = game_entry.get('community_ratings')
                if ratings:
                    total_difficulty += ratings.get('avg_difficulty', 0)
                    total_hours += ratings.get('avg_hours', 0)
                    rated_games_count += 1
        series_avg_difficulty = round(total_difficulty / rated_games_count, 1) if rated_games_count else None
        series_avg_hours = round(total_hours / rated_games_count, 1) if rated_games_count else None

        # Max Tier Distribution: exclusive % showing each earner's highest tier
        t1 = tier_earner_counts.get(1, 0)
        t2 = tier_earner_counts.get(2, 0)
        t3 = tier_earner_counts.get(3, 0)
        t4 = tier_earner_counts.get(4, 0)
        max_tier_counts = {1: max(0, t1 - t2), 2: max(0, t2 - t3), 3: max(0, t3 - t4), 4: t4}
        max_tier_pcts = {
            tier: round(count / t1 * 100, 1) if t1 else 0
            for tier, count in max_tier_counts.items()
        }

        # Community total XP for this series (cached by update_leaderboards cron)
        community_total_xp = cache.get(f"lb_community_xp_{series_slug}", 0)

        # Total series XP available (sum across all tiers, no new queries)
        # Uses actual stage counts from structured_data instead of badge.required_stages,
        # which stores min_required for megamix badges (not the real stage count)
        tier_xp_map = {1: BRONZE_STAGE_XP, 2: SILVER_STAGE_XP, 3: GOLD_STAGE_XP, 4: PLAT_STAGE_XP}
        total_series_xp_available = 0
        for b in all_badges_list:
            per_stage_xp = tier_xp_map.get(b.tier, 0)
            tier_stage_count = sum(
                1 for data in structured_data
                if data['stage'].stage_number != 0 and data['stage'].applies_to_tier(b.tier)
            )
            total_series_xp_available += BADGE_TIER_XP + (tier_stage_count * per_stage_xp)

        # Selected tier total XP (for tier selector display)
        selected_tier_stage_xp = tier_xp_map.get(selected_tier, 0)
        selected_tier_stage_count = sum(
            1 for data in structured_data
            if data['stage'].stage_number != 0 and data['stage'].applies_to_tier(selected_tier)
        )
        selected_tier_total_xp = BADGE_TIER_XP + (selected_tier_stage_count * selected_tier_stage_xp)
        context['selected_tier_total_xp'] = selected_tier_total_xp
        context['badge_tier_xp_bonus'] = BADGE_TIER_XP

        # User's earned XP for the selected tier
        selected_tier_user_xp = 0
        if target_profile and selected_tier_badge:
            sel_progress = badge_progress_dict.get(selected_tier_badge.id)
            sel_completed = sel_progress.completed_concepts if sel_progress else 0
            selected_tier_user_xp = sel_completed * tier_xp_map.get(selected_tier, 0)
            if highest_tier_earned >= selected_tier:
                selected_tier_user_xp += BADGE_TIER_XP
        context['selected_tier_user_xp'] = selected_tier_user_xp

        # Series completion for radial progress
        series_stages_completed = 0
        series_stages_total = 0
        if target_profile and selected_tier in badge_completion:
            tier_comp = badge_completion[selected_tier]
            for stage_num, is_complete in tier_comp.items():
                if stage_num != 0:  # Skip optional stages
                    series_stages_total += 1
                    if is_complete:
                        series_stages_completed += 1

        # Stage-level user stats (excluding stage 0)
        user_stages_played = 0
        user_stages_platinumed = 0
        total_required_stages = 0
        if target_profile:
            for data in structured_data:
                if data['stage'].stage_number == 0:
                    continue
                total_required_stages += 1
                stage_stats = data.get('user_stage_stats')
                if stage_stats:
                    if stage_stats['games_played'] > 0:
                        user_stages_played += 1
                    if stage_stats['platinums'] > 0:
                        user_stages_platinumed += 1
        else:
            total_required_stages = sum(1 for d in structured_data if d['stage'].stage_number != 0)

        context['badge_series_stats'] = {
            'tier_earner_counts': tier_earner_counts,
            'total_games': total_games,
            'user_series_xp': user_series_xp,
            'user_lb_rank': user_lb_rank,
            'user_lb_progress_rank': user_lb_progress_rank,
            'total_earners_count': len(lb_earners),
            'total_progressers_count': len(lb_progress),
            'user_total_playtime': user_total_playtime,
            'user_stages_played': user_stages_played,
            'user_stages_platinumed': user_stages_platinumed,
            'total_required_stages': total_required_stages,
            'avg_progress': avg_progress,
            'total_trophies_earned': total_trophies_earned,
            'series_stages_completed': series_stages_completed,
            'series_stages_total': series_stages_total,
            'series_completion_pct': round(series_stages_completed / series_stages_total * 100) if series_stages_total else 0,
            'series_avg_difficulty': series_avg_difficulty,
            'series_avg_hours': series_avg_hours,
            'rated_games_count': rated_games_count,
            'user_trophy_breakdown': user_trophy_breakdown,
            'max_tier_pcts': max_tier_pcts,
            'total_unique_earners': t1,
            'first_played': first_played,
            'most_recent_trophy': most_recent_trophy,
            'total_series_xp_available': total_series_xp_available,
            'community_total_xp': community_total_xp,
        }

        # Build tier requirements stage list (for the tier selector panel)
        # Uses structured_data to avoid re-querying stages
        tier_req_stages = []
        tier_comp = badge_completion.get(selected_tier, {})
        for data in structured_data:
            stage = data['stage']
            if stage.stage_number == 0:
                continue
            if not stage.applies_to_tier(selected_tier):
                continue
            tier_req_stages.append({
                'stage': stage,
                'is_complete': tier_comp.get(stage.stage_number, False),
            })
        context['tier_req_stages'] = tier_req_stages

        logger.debug(f"Badge detail loaded {len(structured_data)} stage data entries for {series_slug}")
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
    context_object_name = 'badge'

    def get_object(self, queryset=None):
        series_slug = self.kwargs[self.slug_url_kwarg]
        badge = get_object_or_404(Badge, series_slug=series_slug, tier=1)
        if not badge.is_live and not self.request.user.is_staff:
            raise Http404("Series not found")
        return badge

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
                    break

            # User stats for this series
            highest_user_badge = UserBadge.objects.filter(
                profile=user.profile, badge__series_slug=series_slug
            ).select_related('badge').order_by('-badge__tier').first()
            context['user_highest_tier'] = highest_user_badge.badge.tier if highest_user_badge else 0

            try:
                gamification = user.profile.gamification
                context['user_series_xp'] = gamification.series_badge_xp.get(series_slug, 0)
            except ProfileGamification.DoesNotExist:
                context['user_series_xp'] = 0

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

        active_tab = self.request.GET.get('tab', 'earners')
        if active_tab not in ('earners', 'progress'):
            active_tab = 'earners'
        context['active_tab'] = active_tab

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

        xp_key = "lb_total_xp"
        progress_key = "lb_total_progress"

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
                    break

            # User stats for overall leaderboards
            try:
                gamification = user.profile.gamification
                context['user_total_xp'] = gamification.total_badge_xp
                context['user_total_badges'] = gamification.total_badges_earned
            except ProfileGamification.DoesNotExist:
                context['user_total_xp'] = 0
                context['user_total_badges'] = 0

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

        active_tab = self.request.GET.get('tab', 'xp')
        if active_tab not in ('xp', 'progress', 'series'):
            active_tab = 'xp'
        context['active_tab'] = active_tab

        if active_tab == 'series':
            series_badges = Badge.objects.live().filter(
                tier=1
            ).select_related(
                'base_badge', 'most_recent_concept',
                'base_badge__most_recent_concept',
                'base_badge__title', 'title',
            ).exclude(
                series_slug__isnull=True
            ).exclude(series_slug='').order_by(Lower('display_series'))

            titled_badges = [b for b in series_badges if b.effective_user_title]
            progress_keys = [f"lb_progress_{b.series_slug}" for b in titled_badges]
            progress_data = cache.get_many(progress_keys) if progress_keys else {}
            directory = []
            for badge in titled_badges:
                badge.progress_count = len(progress_data.get(f"lb_progress_{badge.series_slug}", []))
                directory.append(badge)
            context['series_directory'] = directory

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
