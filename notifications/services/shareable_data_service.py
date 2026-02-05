"""
Centralized service for collecting data needed for shareable images.
Extracts logic from signals.py to be reusable across the application.
"""
from trophies.models import EarnedTrophy, ProfileGame, Stage, Badge, UserBadgeProgress, UserConceptRating
import logging

logger = logging.getLogger(__name__)


class ShareableDataService:
    """
    Centralized service for collecting metadata needed for share images.
    Used by both notification signals and the My Shareables page API.
    """

    @staticmethod
    def get_rarity_label(rarity):
        """Convert trophy_rarity (0-3) to display label."""
        rarity_map = {
            0: 'Ultra Rare',
            1: 'Very Rare',
            2: 'Rare',
            3: 'Common',
        }
        return rarity_map.get(rarity, 'Unknown')

    @staticmethod
    def get_tier_name(tier):
        """Convert badge tier number to name."""
        tier_map = {
            1: 'Bronze',
            2: 'Silver',
            3: 'Gold',
            4: 'Platinum',
        }
        return tier_map.get(tier, 'Bronze')

    @staticmethod
    def get_badge_main_image(badge):
        """Get the main badge image URL."""
        try:
            layers = badge.get_badge_layers()
            return layers.get('main', '')
        except Exception:
            return ''

    @staticmethod
    def get_tier_xp(tier):
        """
        Get the XP value for a specific badge tier.

        Delegates to the centralized xp_service.
        """
        from trophies.services.xp_service import get_tier_xp
        return get_tier_xp(tier)

    @classmethod
    def get_badge_xp_for_game(cls, profile, game):
        """
        Calculate total badge XP earned from this specific game/platinum.

        Delegates to the centralized xp_service for consistency.

        Returns:
            int: Total XP earned from badge progress related to this game's concept
        """
        from trophies.services.xp_service import get_badge_xp_for_game
        return get_badge_xp_for_game(profile, game)

    @classmethod
    def get_tier1_badges_for_game(cls, profile, game):
        """
        Get tier 1 badge progress information for badges that this game contributes to.

        Returns:
            list: List of dicts with tier 1 badge progress info
            [
                {
                    'badge_name': 'Series Name',
                    'series_slug': 'series-slug',
                    'completed_stages': 5,
                    'required_stages': 10,
                    'progress_percentage': 50,
                    'badge_image_url': '...'
                }
            ]
        """
        if not game.concept:
            return []

        tier1_badges = []
        seen_series = set()

        # Find stages that include this game's concept
        stages = Stage.objects.filter(concepts=game.concept, stage_number__gt=0)

        for stage in stages:
            if stage.series_slug in seen_series:
                continue
            seen_series.add(stage.series_slug)

            # Find tier 1 badge for this series
            tier1_badge = Badge.objects.filter(series_slug=stage.series_slug, tier=1).first()
            if not tier1_badge:
                continue

            # Get user's progress for this badge
            progress = UserBadgeProgress.objects.filter(
                profile=profile,
                badge=tier1_badge
            ).first()

            completed = progress.completed_concepts if progress else 0
            required = tier1_badge.required_stages or 1
            percentage = min(100, int((completed / required) * 100)) if required > 0 else 0

            tier1_badges.append({
                'badge_name': tier1_badge.effective_display_series or tier1_badge.name,
                'series_slug': tier1_badge.series_slug,
                'completed_stages': completed,
                'required_stages': required,
                'progress_percentage': percentage,
                'badge_image_url': cls.get_badge_main_image(tier1_badge),
            })

        return tier1_badges

    @classmethod
    def get_user_rating_for_game(cls, profile, game):
        """
        Get user's personal rating for a game's concept.

        Args:
            profile: Profile instance
            game: Game instance

        Returns:
            dict or None: Rating data if exists, None otherwise
        """
        if not game.concept:
            return None

        rating = UserConceptRating.objects.filter(
            profile=profile,
            concept=game.concept
        ).first()

        if not rating:
            return None

        return {
            'overall_rating': rating.overall_rating,
            'difficulty': rating.difficulty,
            'grindiness': rating.grindiness,
            'fun_ranking': rating.fun_ranking,
            'hours_to_platinum': rating.hours_to_platinum,
        }

    @classmethod
    def get_platinum_share_data(cls, earned_trophy):
        """
        Collect all data needed for a platinum share image from an EarnedTrophy.

        Args:
            earned_trophy: EarnedTrophy instance (must be a platinum)

        Returns:
            dict: Complete metadata for share image generation
        """
        profile = earned_trophy.profile
        trophy = earned_trophy.trophy
        game = trophy.game

        # Get ProfileGame data
        profile_game = ProfileGame.objects.filter(
            profile=profile,
            game=game
        ).first()

        # Count total platinums earned up to and including this one
        earned_date = earned_trophy.earned_date_time
        earned_year = None

        if earned_date:
            earned_year = earned_date.year
            # Count platinums earned on or before this platinum's earned date
            total_plats = EarnedTrophy.objects.filter(
                profile=profile,
                earned=True,
                trophy__trophy_type='platinum',
                earned_date_time__lte=earned_date
            ).count()

            # Count platinums earned in the same year, up to and including this one
            from datetime import datetime
            year_start = datetime(earned_year, 1, 1, tzinfo=earned_date.tzinfo)
            yearly_plats = EarnedTrophy.objects.filter(
                profile=profile,
                earned=True,
                trophy__trophy_type='platinum',
                earned_date_time__gte=year_start,
                earned_date_time__lte=earned_date
            ).count()
        else:
            # No earned date - fall back to current totals
            total_plats = EarnedTrophy.objects.filter(
                profile=profile,
                earned=True,
                trophy__trophy_type='platinum'
            ).count()
            yearly_plats = 0

        # Get badge XP and tier 1 badge progress
        badge_xp = cls.get_badge_xp_for_game(profile, game)
        tier1_badges = cls.get_tier1_badges_for_game(profile, game)

        # Get user's personal rating for this game
        user_rating = cls.get_user_rating_for_game(profile, game)

        return {
            'username': profile.display_psn_username or profile.psn_username,
            'game_name': game.title_name,
            'game_id': game.id,
            'np_communication_id': game.np_communication_id,
            'concept_id': game.concept.id if game.concept else None,
            'trophy_name': trophy.trophy_name,
            'trophy_detail': trophy.trophy_detail or '',
            'trophy_earn_rate': trophy.trophy_earn_rate or 0,
            'trophy_rarity': trophy.trophy_rarity,
            'trophy_icon_url': trophy.trophy_icon_url or '',
            'game_image': game.title_image or game.title_icon_url or '',
            'concept_bg_url': game.concept.bg_url if game.concept and game.concept.bg_url else '',
            'rarity_label': cls.get_rarity_label(trophy.trophy_rarity),
            'title_platform': game.title_platform,
            'region': game.region,
            'is_regional': game.is_regional,
            'first_played_date_time': profile_game.first_played_date_time.isoformat() if profile_game and profile_game.first_played_date_time else None,
            'last_played_date_time': profile_game.last_played_date_time.isoformat() if profile_game and profile_game.last_played_date_time else None,
            'play_duration_seconds': profile_game.play_duration.total_seconds() if profile_game and profile_game.play_duration else None,
            'earned_trophies_count': profile_game.earned_trophies_count if profile_game else 0,
            'total_trophies_count': profile_game.total_trophies if profile_game else 0,
            'progress_percentage': profile_game.progress if profile_game else 0,
            'user_total_platinums': total_plats,
            'user_avatar_url': profile.avatar_url or '',
            'earned_date_time': earned_trophy.earned_date_time.isoformat() if earned_trophy.earned_date_time else None,
            'yearly_plats': yearly_plats,
            'earned_year': earned_year,
            'badge_xp': badge_xp,
            'tier1_badges': tier1_badges,
            'user_rating': user_rating,
        }
