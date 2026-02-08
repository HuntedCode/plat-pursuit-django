"""
Unit tests for MonthlyRecapMessageService.

Tests the shared context building logic used by both email and notification systems.
"""
from django.test import TestCase
from django.conf import settings
from trophies.models import MonthlyRecap, Profile
from users.models import CustomUser
from core.services.monthly_recap_message_service import MonthlyRecapMessageService


class MonthlyRecapMessageServiceTests(TestCase):
    """Test MonthlyRecapMessageService context building."""

    def setUp(self):
        """Create test user, profile, and recap."""
        self.user = CustomUser.objects.create_user(
            email='testuser@example.com',
            password='testpass123'
        )
        self.profile = Profile.objects.create(
            psn_username='testuser',
            user=self.user,
            is_linked=True
        )
        self.recap = MonthlyRecap.objects.create(
            profile=self.profile,
            year=2026,
            month=1,
            total_trophies_earned=157,
            platinums_earned=3,
            games_started=5,
            games_completed=2,
            badges_earned_count=4,
            bronzes_earned=100,
            silvers_earned=40,
            golds_earned=14,
            activity_calendar={'total_active_days': 23, 'days': [], 'max_count': 10, 'first_day_weekday': 0},
            streak_data={'longest_streak': 7, 'total_active_days': 23},
            platinums_data=[],
            rarest_trophy_data={},
            most_active_day={},
            time_analysis_data={},
            comparison_data={},
            quiz_data=[],
            badges_data=[],
            is_finalized=True,
        )

    def test_get_trophy_tier_zero(self):
        """Test trophy tier categorization for 0 trophies."""
        self.assertEqual(MonthlyRecapMessageService.get_trophy_tier(0), '0')

    def test_get_trophy_tier_single_digit(self):
        """Test trophy tier categorization for single digit."""
        self.assertEqual(MonthlyRecapMessageService.get_trophy_tier(5), '5')
        self.assertEqual(MonthlyRecapMessageService.get_trophy_tier(9), '9')

    def test_get_trophy_tier_10_plus(self):
        """Test trophy tier categorization for 10+."""
        self.assertEqual(MonthlyRecapMessageService.get_trophy_tier(10), '10+')
        self.assertEqual(MonthlyRecapMessageService.get_trophy_tier(15), '10+')
        self.assertEqual(MonthlyRecapMessageService.get_trophy_tier(24), '10+')

    def test_get_trophy_tier_25_plus(self):
        """Test trophy tier categorization for 25+."""
        self.assertEqual(MonthlyRecapMessageService.get_trophy_tier(25), '25+')
        self.assertEqual(MonthlyRecapMessageService.get_trophy_tier(35), '25+')
        self.assertEqual(MonthlyRecapMessageService.get_trophy_tier(49), '25+')

    def test_get_trophy_tier_100_plus(self):
        """Test trophy tier categorization for 100+."""
        self.assertEqual(MonthlyRecapMessageService.get_trophy_tier(100), '100+')
        self.assertEqual(MonthlyRecapMessageService.get_trophy_tier(157), '100+')
        self.assertEqual(MonthlyRecapMessageService.get_trophy_tier(249), '100+')

    def test_get_trophy_tier_1000_plus(self):
        """Test trophy tier categorization for 1000+."""
        self.assertEqual(MonthlyRecapMessageService.get_trophy_tier(1000), '1000+')
        self.assertEqual(MonthlyRecapMessageService.get_trophy_tier(5000), '1000+')

    def test_build_base_context_required_fields(self):
        """Test base context contains all required fields."""
        context = MonthlyRecapMessageService.build_base_context(self.recap)

        # Verify all required keys present
        required_keys = [
            'username', 'month_name', 'year', 'active_days', 'trophy_tier',
            'games_started', 'total_trophies', 'platinums_earned',
            'games_completed', 'badges_earned', 'has_streak', 'recap_url'
        ]
        for key in required_keys:
            self.assertIn(key, context, f"Missing required key: {key}")

    def test_build_base_context_values(self):
        """Test base context values are correct."""
        context = MonthlyRecapMessageService.build_base_context(self.recap)

        self.assertEqual(context['username'], 'testuser')
        self.assertEqual(context['month_name'], 'January')
        self.assertEqual(context['year'], 2026)
        self.assertEqual(context['active_days'], 23)
        self.assertEqual(context['trophy_tier'], '100+')
        self.assertEqual(context['games_started'], 5)
        self.assertEqual(context['total_trophies'], 157)
        self.assertEqual(context['platinums_earned'], 3)
        self.assertEqual(context['games_completed'], 2)
        self.assertEqual(context['badges_earned'], 4)
        self.assertTrue(context['has_streak'])  # longest_streak=7 > 1
        self.assertIn('/recap/2026/1/', context['recap_url'])

    def test_build_base_context_no_streak(self):
        """Test has_streak is False when longest_streak <= 1."""
        self.recap.streak_data = {'longest_streak': 1, 'total_active_days': 5}
        self.recap.save()

        context = MonthlyRecapMessageService.build_base_context(self.recap)
        self.assertFalse(context['has_streak'])

    def test_build_base_context_display_username(self):
        """Test display_psn_username takes precedence over psn_username."""
        self.profile.display_psn_username = 'DisplayName'
        self.profile.save()

        context = MonthlyRecapMessageService.build_base_context(self.recap)
        self.assertEqual(context['username'], 'DisplayName')

    def test_build_base_context_fallback_active_days(self):
        """Test active days fallback to streak_data if activity_calendar empty."""
        self.recap.activity_calendar = {'days': []}  # No total_active_days
        self.recap.streak_data = {'total_active_days': 15}
        self.recap.save()

        context = MonthlyRecapMessageService.build_base_context(self.recap)
        self.assertEqual(context['active_days'], 15)

    def test_build_email_context_includes_base(self):
        """Test email context includes all base context fields."""
        email_context = MonthlyRecapMessageService.build_email_context(self.recap)
        base_context = MonthlyRecapMessageService.build_base_context(self.recap)

        # All base keys should be in email context
        for key in base_context:
            self.assertIn(key, email_context)

    def test_build_email_context_additional_fields(self):
        """Test email context includes email-specific fields."""
        context = MonthlyRecapMessageService.build_email_context(self.recap)

        self.assertIn('site_url', context)
        self.assertIn('preference_url', context)
        self.assertEqual(context['site_url'], settings.SITE_URL)
        self.assertIn('/users/email-preferences/', context['preference_url'])

    def test_build_notification_message_structure(self):
        """Test notification message formatting."""
        message = MonthlyRecapMessageService.build_notification_message(self.recap)

        # Should be multi-line
        self.assertIn('\n', message)

        # Should contain key stats
        self.assertIn('January', message)
        self.assertIn('157 trophies', message)
        self.assertIn('3 platinum', message)
        self.assertIn('2 game', message)
        self.assertIn('4 badge', message)

    def test_build_notification_message_pluralization(self):
        """Test notification message handles pluralization correctly."""
        # Single platinum
        self.recap.platinums_earned = 1
        self.recap.games_completed = 1
        self.recap.badges_earned_count = 1
        self.recap.save()

        message = MonthlyRecapMessageService.build_notification_message(self.recap)

        self.assertIn('1 platinum', message)  # No 's'
        self.assertNotIn('1 platinums', message)
        self.assertIn('1 game', message)
        self.assertNotIn('1 games', message)
        self.assertIn('1 badge', message)
        self.assertNotIn('1 badges', message)

    def test_build_notification_message_zero_stats(self):
        """Test notification message when stats are zero."""
        self.recap.platinums_earned = 0
        self.recap.games_completed = 0
        self.recap.badges_earned_count = 0
        self.recap.total_trophies_earned = 0
        self.recap.save()

        message = MonthlyRecapMessageService.build_notification_message(self.recap)

        # Should still have header
        self.assertIn('January', message)
        self.assertIn('trophy hunting highlights', message)

        # Should not include zero stats
        self.assertNotIn('platinum', message)
        self.assertNotIn('game', message)
        self.assertNotIn('badge', message)
        self.assertNotIn('0 trophies', message)

    def test_build_notification_message_only_trophies(self):
        """Test notification message with only trophies (no platinums/games/badges)."""
        self.recap.total_trophies_earned = 50
        self.recap.platinums_earned = 0
        self.recap.games_completed = 0
        self.recap.badges_earned_count = 0
        self.recap.save()

        message = MonthlyRecapMessageService.build_notification_message(self.recap)

        self.assertIn('50 trophies', message)
        self.assertNotIn('platinum', message)
        self.assertNotIn('game', message)
        self.assertNotIn('badge', message)

    # --- build_notification_context tests ---

    def test_build_notification_context_includes_base(self):
        """Test notification context includes all base context fields."""
        notif_context = MonthlyRecapMessageService.build_notification_context(self.recap)
        base_context = MonthlyRecapMessageService.build_base_context(self.recap)

        for key in base_context:
            self.assertIn(key, notif_context)

    def test_build_notification_context_trophy_breakdown(self):
        """Test notification context includes trophy type breakdown."""
        context = MonthlyRecapMessageService.build_notification_context(self.recap)

        self.assertEqual(context['bronzes_earned'], 100)
        self.assertEqual(context['silvers_earned'], 40)
        self.assertEqual(context['golds_earned'], 14)
        self.assertEqual(context['platinums_earned'], 3)

    def test_build_notification_context_rarest_trophy(self):
        """Test notification context maps rarest_trophy_data."""
        self.recap.rarest_trophy_data = {
            'name': 'Ultra Rare Trophy',
            'game': 'Hard Game',
            'earn_rate': 0.5,
            'icon_url': '/media/trophy.png',
            'trophy_type': 'gold',
        }
        self.recap.save()

        context = MonthlyRecapMessageService.build_notification_context(self.recap)
        self.assertEqual(context['rarest_trophy']['name'], 'Ultra Rare Trophy')
        self.assertEqual(context['rarest_trophy']['earn_rate'], 0.5)

    def test_build_notification_context_streak(self):
        """Test notification context maps streak_data."""
        context = MonthlyRecapMessageService.build_notification_context(self.recap)

        self.assertEqual(context['streak']['longest_streak'], 7)
        self.assertEqual(context['streak']['total_active_days'], 23)

    def test_build_notification_context_badges(self):
        """Test notification context maps badges_data and badge_xp_earned."""
        self.recap.badge_xp_earned = 9000
        self.recap.badges_data = [
            {'name': 'Collector', 'tier': 2, 'tier_name': 'Silver'},
        ]
        self.recap.save()

        context = MonthlyRecapMessageService.build_notification_context(self.recap)
        self.assertEqual(context['badge_xp_earned'], 9000)
        self.assertEqual(len(context['badges_data']), 1)
        self.assertEqual(context['badges_data'][0]['name'], 'Collector')

    def test_build_notification_context_comparison(self):
        """Test notification context maps comparison_data."""
        self.recap.comparison_data = {
            'vs_prev_month_pct': 25,
            'personal_bests': ['Most trophies in a day'],
        }
        self.recap.save()

        context = MonthlyRecapMessageService.build_notification_context(self.recap)
        self.assertEqual(context['comparison']['vs_prev_month_pct'], 25)
        self.assertEqual(len(context['comparison']['personal_bests']), 1)

    def test_build_notification_context_platinums_data_limit(self):
        """Test platinums_data is capped at 5 entries."""
        self.recap.platinums_data = [
            {'game_name': f'Game {i}', 'game_image': '', 'earned_date': '', 'earn_rate': 5.0}
            for i in range(8)
        ]
        self.recap.save()

        context = MonthlyRecapMessageService.build_notification_context(self.recap)
        self.assertEqual(len(context['platinums_data']), 5)

    def test_build_notification_context_badges_data_limit(self):
        """Test badges_data is capped at 5 entries."""
        self.recap.badges_data = [
            {'name': f'Badge {i}', 'tier': 1, 'tier_name': 'Bronze'}
            for i in range(8)
        ]
        self.recap.save()

        context = MonthlyRecapMessageService.build_notification_context(self.recap)
        self.assertEqual(len(context['badges_data']), 5)

    def test_build_notification_context_empty_fields(self):
        """Test notification context handles empty/default fields gracefully."""
        context = MonthlyRecapMessageService.build_notification_context(self.recap)

        # Empty dicts/lists should default gracefully
        self.assertEqual(context['rarest_trophy'], {})
        self.assertEqual(context['most_active_day'], {})
        self.assertEqual(context['time_analysis'], {})
        self.assertEqual(context['platinums_data'], [])
        self.assertEqual(context['comparison'], {})
