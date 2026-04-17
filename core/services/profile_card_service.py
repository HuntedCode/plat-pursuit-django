"""
Service for collecting all data needed for profile card / forum signature rendering.

Centralizes data gathering from Profile, ProfileGamification, UserBadge,
UserTitle, and Redis leaderboards into a single dict suitable for template
rendering and change-detection hashing.
"""
import hashlib
import json
import logging

logger = logging.getLogger(__name__)


class ProfileCardDataService:
    """Gather profile data for share card and forum signature rendering."""

    @staticmethod
    def get_profile_card_data(profile):
        """
        Collect all data needed for profile card templates.

        Returns a dict with all stats, badge info, title, leaderboard rank, etc.
        All values are plain Python types (no model instances) for cache safety
        and hash stability.
        """
        from trophies.models import UserBadge, UserTitle, ProfileGamification
        from trophies.services.redis_leaderboard_service import (
            get_xp_rank, get_xp_count,
            get_country_xp_rank, get_country_xp_count,
        )

        # ---- Core profile fields ----
        data = {
            'psn_username': profile.display_psn_username or profile.psn_username,
            'avatar_url': profile.avatar_url or '',
            'country': profile.country or '',
            'country_code': profile.country_code or '',
            'flag': profile.flag or '',
            'is_plus': profile.is_plus,
            'is_premium': profile.user_is_premium,
            'trophy_level': profile.trophy_level,
            'tier': profile.tier,
        }

        # ---- Trophy counts ----
        total_earned = (
            profile.total_trophies - profile.total_unearned
        )
        # Trophy breakdown percentages (for visual proportion bar)
        # Use sum of type counts as denominator (avoids mismatch with total_earned
        # which may include hidden trophies differently)
        type_total = (
            profile.total_plats + profile.total_golds
            + profile.total_silvers + profile.total_bronzes
        )
        pct_plats = round(profile.total_plats / type_total * 100, 1) if type_total else 0
        pct_golds = round(profile.total_golds / type_total * 100, 1) if type_total else 0
        pct_silvers = round(profile.total_silvers / type_total * 100, 1) if type_total else 0
        pct_bronzes = round(profile.total_bronzes / type_total * 100, 1) if type_total else 0

        data.update({
            'total_trophies': profile.total_trophies,
            'total_earned': total_earned,
            'total_unearned': profile.total_unearned,
            'total_bronzes': profile.total_bronzes,
            'total_silvers': profile.total_silvers,
            'total_golds': profile.total_golds,
            'total_plats': profile.total_plats,
            'pct_plats': pct_plats,
            'pct_golds': pct_golds,
            'pct_silvers': pct_silvers,
            'pct_bronzes': pct_bronzes,
            'total_games': profile.total_games,
            'total_completes': profile.total_completes,
            'avg_progress': round(profile.avg_progress, 1),
            'earn_rate': round(total_earned / profile.total_trophies * 100, 1) if profile.total_trophies else 0,
        })

        # ---- Displayed title ----
        displayed_title = None
        try:
            ut = UserTitle.objects.filter(
                profile=profile, is_displayed=True,
            ).select_related('title').first()
            if ut:
                displayed_title = ut.title.name
        except Exception:
            logger.exception('Error fetching displayed title for profile %s', profile.pk)
        data['displayed_title'] = displayed_title

        # ---- Latest earned badge (most recently unlocked, with custom art) ----
        badge_data = ProfileCardDataService._get_latest_badge(profile)
        data.update(badge_data)

        # ---- Gamification / XP ----
        gamification = {
            'total_badge_xp': 0,
            'total_badges_earned': 0,
            'unique_badges_earned': 0,
        }
        try:
            gam = ProfileGamification.objects.filter(profile=profile).first()
            if gam:
                gamification = {
                    'total_badge_xp': gam.total_badge_xp,
                    'total_badges_earned': gam.total_badges_earned,
                    'unique_badges_earned': gam.unique_badges_earned,
                }
        except Exception:
            logger.exception('Error fetching gamification for profile %s', profile.pk)
        data.update(gamification)

        # ---- Leaderboard ranks ----
        xp_rank = None
        xp_total_users = 0
        try:
            xp_rank = get_xp_rank(profile.pk)
            xp_total_users = get_xp_count()
        except Exception:
            logger.exception('Error fetching XP rank for profile %s', profile.pk)

        # DB fallback if Redis doesn't have the rank but user has XP
        if xp_rank is None and gamification['total_badge_xp'] > 0:
            try:
                xp_rank = (
                    ProfileGamification.objects
                    .filter(
                        profile__is_linked=True,
                        total_badge_xp__gt=gamification['total_badge_xp'],
                    )
                    .count()
                ) + 1
                if xp_total_users == 0:
                    xp_total_users = (
                        ProfileGamification.objects
                        .filter(profile__is_linked=True, total_badge_xp__gt=0)
                        .count()
                    )
            except Exception:
                logger.exception('Error in DB fallback for XP rank, profile %s', profile.pk)

        data['xp_rank'] = xp_rank
        data['xp_total_users'] = xp_total_users

        # Country XP rank (Redis primary, DB fallback)
        country_xp_rank = None
        country_xp_total = 0
        try:
            if profile.country_code and gamification['total_badge_xp'] > 0:
                country_xp_rank = get_country_xp_rank(profile.country_code, profile.pk)
                country_xp_total = get_country_xp_count(profile.country_code)

                # DB fallback if Redis doesn't have the rank yet
                if country_xp_rank is None:
                    country_xp_rank = (
                        ProfileGamification.objects
                        .filter(
                            profile__country_code=profile.country_code,
                            profile__is_linked=True,
                            total_badge_xp__gt=gamification['total_badge_xp'],
                        )
                        .count()
                    ) + 1
                    if country_xp_total == 0:
                        country_xp_total = (
                            ProfileGamification.objects
                            .filter(
                                profile__country_code=profile.country_code,
                                profile__is_linked=True,
                                total_badge_xp__gt=0,
                            )
                            .count()
                        )
        except Exception:
            logger.exception('Error fetching country XP rank for profile %s', profile.pk)
        data['country_xp_rank'] = country_xp_rank
        data['country_xp_total'] = country_xp_total

        # ---- Recent / Rarest platinum ----
        data.update(ProfileCardDataService._get_notable_plats(profile))

        # ---- Card theme ----
        card_theme = 'default'
        try:
            if hasattr(profile, 'card_settings'):
                card_theme = profile.card_settings.card_theme or 'default'
        except Exception:
            pass
        data['card_theme'] = card_theme

        return data

    @staticmethod
    def _get_badge_image_url(badge):
        """
        Get a full URL for a badge image suitable for share card rendering.
        Returns a full URL (http/media) or empty string.
        Only returns URLs for badges with custom artwork.
        """
        from django.conf import settings

        try:
            layers = badge.get_badge_layers()
            if not layers.get('has_custom_image'):
                return ''
            main_url = layers.get('main', '')
            if not main_url:
                return ''
            # If it's already a full URL (external avatar, etc.), return as-is
            if main_url.startswith('http'):
                return main_url
            # If it's a media URL (starts with /media/ or MEDIA_URL), make absolute
            if main_url.startswith('/'):
                return main_url
            # It's a relative media path from ImageField.url
            return main_url
        except Exception:
            return ''

    @staticmethod
    def _get_latest_badge(profile):
        """
        Get the user's most recently earned badge with custom artwork.
        """
        from trophies.models import UserBadge

        result = {
            'badge_name': None,
            'badge_series': None,
            'badge_tier': None,
            'badge_image_url': None,
        }

        try:
            earned_badges = (
                UserBadge.objects
                .filter(profile=profile)
                .select_related('badge', 'badge__base_badge')
                .order_by('-earned_at')
            )

            for ub in earned_badges:
                badge = ub.badge
                image_url = ProfileCardDataService._get_badge_image_url(badge)
                if image_url:
                    series_name = badge.effective_display_series or badge.series_slug
                    result['badge_name'] = series_name
                    result['badge_series'] = series_name
                    result['badge_tier'] = badge.tier
                    result['badge_image_url'] = image_url
                    break

        except Exception:
            logger.exception('Error fetching latest badge for profile %s', profile.pk)

        return result

    @staticmethod
    def _get_notable_plats(profile):
        """Get recent and rarest platinum data."""
        result = {
            'recent_plat_name': None,
            'recent_plat_icon': None,
            'rarest_plat_name': None,
            'rarest_plat_icon': None,
            'rarest_plat_earn_rate': None,
        }

        try:
            if profile.recent_plat_id:
                rp = profile.recent_plat
                if rp and rp.trophy and rp.trophy.game:
                    game = rp.trophy.game
                    result['recent_plat_name'] = (
                        game.concept.unified_title if game.concept else game.title_name
                    )
                    result['recent_plat_icon'] = game.display_image_url
        except Exception:
            logger.exception('Error fetching recent plat for profile %s', profile.pk)

        try:
            if profile.rarest_plat_id:
                rp = profile.rarest_plat
                if rp and rp.trophy and rp.trophy.game:
                    game = rp.trophy.game
                    result['rarest_plat_name'] = (
                        game.concept.unified_title if game.concept else game.title_name
                    )
                    result['rarest_plat_icon'] = game.display_image_url
                    result['rarest_plat_earn_rate'] = rp.trophy.earn_rate
        except Exception:
            logger.exception('Error fetching rarest plat for profile %s', profile.pk)

        return result

    @staticmethod
    def compute_data_hash(data):
        """
        Compute MD5 hash of card data for change detection.

        Used by the pre-rendering pipeline to skip re-rendering when data
        hasn't changed since the last render.
        """
        # Sort keys for deterministic serialization
        serialized = json.dumps(data, sort_keys=True, default=str)
        return hashlib.md5(serialized.encode()).hexdigest()
