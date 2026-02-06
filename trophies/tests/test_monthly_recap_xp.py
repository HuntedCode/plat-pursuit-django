"""
Tests for monthly recap badge XP calculations.

This test suite verifies that badge XP calculations correctly include:
- Badge completion bonuses (3000 XP per badge)
- Stage progress XP (tier-specific: Bronze/Gold=250, Silver/Plat=75 per stage)
- Proper handling of multiple badges in same month
- Correct tier-specific XP rates
"""
from django.test import TestCase
from django.utils import timezone
from datetime import datetime, timedelta
from trophies.models import Profile, Badge, UserBadge, UserBadgeProgress, ProfileGamification
from trophies.services.monthly_recap_service import MonthlyRecapService
from trophies.services.xp_service import calculate_progress_xp_for_badge, update_profile_gamification
from trophies.util_modules.constants import BADGE_TIER_XP, TIER_XP_MAP
from users.models import CustomUser


class MonthlyRecapBadgeXPTest(TestCase):
    """Test badge XP calculations in monthly recaps."""

    def setUp(self):
        """Set up test data."""
        # Create user and profile
        self.user = CustomUser.objects.create_user(
            email='test@example.com',
            password='testpass123'
        )
        self.profile = Profile.objects.create(
            user=self.user,
            psn_username='testuser',
            avatar_url='https://example.com/avatar.jpg'
        )

        # Create ProfileGamification
        ProfileGamification.objects.create(profile=self.profile)

        # Create test badges with different tiers
        self.bronze_badge = Badge.objects.create(
            name='Bronze Test Badge',
            series_slug='test-series',
            tier=1,  # Bronze
            required_stages=10
        )

        self.silver_badge = Badge.objects.create(
            name='Silver Test Badge',
            series_slug='test-series',
            tier=2,  # Silver
            required_stages=8
        )

        self.gold_badge = Badge.objects.create(
            name='Gold Test Badge',
            series_slug='test-series',
            tier=3,  # Gold
            required_stages=6
        )

        self.platinum_badge = Badge.objects.create(
            name='Platinum Test Badge',
            series_slug='test-series',
            tier=4,  # Platinum
            required_stages=4
        )

    def test_single_bronze_badge_xp(self):
        """Test XP calculation for a single Bronze badge."""
        # Earn badge in January 2026
        earned_date = datetime(2026, 1, 15, 12, 0, 0, tzinfo=timezone.utc)
        UserBadge.objects.create(
            profile=self.profile,
            badge=self.bronze_badge,
            earned_at=earned_date
        )

        # Get recap stats
        stats = MonthlyRecapService.get_badge_stats_for_month(self.profile, 2026, 1)

        # Expected XP: 3000 (completion) + (10 stages × 250 XP) = 5500
        expected_xp = BADGE_TIER_XP + (10 * TIER_XP_MAP[1])  # 3000 + 2500 = 5500

        self.assertEqual(stats['xp_earned'], expected_xp)
        self.assertEqual(stats['badges_count'], 1)

    def test_single_silver_badge_xp(self):
        """Test XP calculation for a single Silver badge."""
        earned_date = datetime(2026, 1, 15, 12, 0, 0, tzinfo=timezone.utc)
        UserBadge.objects.create(
            profile=self.profile,
            badge=self.silver_badge,
            earned_at=earned_date
        )

        stats = MonthlyRecapService.get_badge_stats_for_month(self.profile, 2026, 1)

        # Expected XP: 3000 (completion) + (8 stages × 75 XP) = 3600
        expected_xp = BADGE_TIER_XP + (8 * TIER_XP_MAP[2])  # 3000 + 600 = 3600

        self.assertEqual(stats['xp_earned'], expected_xp)
        self.assertEqual(stats['badges_count'], 1)

    def test_single_gold_badge_xp(self):
        """Test XP calculation for a single Gold badge."""
        earned_date = datetime(2026, 1, 15, 12, 0, 0, tzinfo=timezone.utc)
        UserBadge.objects.create(
            profile=self.profile,
            badge=self.gold_badge,
            earned_at=earned_date
        )

        stats = MonthlyRecapService.get_badge_stats_for_month(self.profile, 2026, 1)

        # Expected XP: 3000 (completion) + (6 stages × 250 XP) = 4500
        expected_xp = BADGE_TIER_XP + (6 * TIER_XP_MAP[3])  # 3000 + 1500 = 4500

        self.assertEqual(stats['xp_earned'], expected_xp)
        self.assertEqual(stats['badges_count'], 1)

    def test_single_platinum_badge_xp(self):
        """Test XP calculation for a single Platinum badge."""
        earned_date = datetime(2026, 1, 15, 12, 0, 0, tzinfo=timezone.utc)
        UserBadge.objects.create(
            profile=self.profile,
            badge=self.platinum_badge,
            earned_at=earned_date
        )

        stats = MonthlyRecapService.get_badge_stats_for_month(self.profile, 2026, 1)

        # Expected XP: 3000 (completion) + (4 stages × 75 XP) = 3300
        expected_xp = BADGE_TIER_XP + (4 * TIER_XP_MAP[4])  # 3000 + 300 = 3300

        self.assertEqual(stats['xp_earned'], expected_xp)
        self.assertEqual(stats['badges_count'], 1)

    def test_multiple_badges_same_month(self):
        """Test XP calculation for multiple badges earned in same month."""
        earned_date = datetime(2026, 1, 15, 12, 0, 0, tzinfo=timezone.utc)

        # Earn Bronze, Silver, and Gold badges
        UserBadge.objects.create(
            profile=self.profile,
            badge=self.bronze_badge,
            earned_at=earned_date
        )
        UserBadge.objects.create(
            profile=self.profile,
            badge=self.silver_badge,
            earned_at=earned_date + timedelta(days=1)
        )
        UserBadge.objects.create(
            profile=self.profile,
            badge=self.gold_badge,
            earned_at=earned_date + timedelta(days=2)
        )

        stats = MonthlyRecapService.get_badge_stats_for_month(self.profile, 2026, 1)

        # Expected XP:
        # Bronze: 3000 + (10 × 250) = 5500
        # Silver: 3000 + (8 × 75) = 3600
        # Gold: 3000 + (6 × 250) = 4500
        # Total: 13,600
        expected_xp = (
            (BADGE_TIER_XP + 10 * TIER_XP_MAP[1]) +
            (BADGE_TIER_XP + 8 * TIER_XP_MAP[2]) +
            (BADGE_TIER_XP + 6 * TIER_XP_MAP[3])
        )

        self.assertEqual(stats['xp_earned'], expected_xp)
        self.assertEqual(stats['badges_count'], 3)

    def test_no_badges_earned(self):
        """Test XP calculation when no badges earned in month."""
        stats = MonthlyRecapService.get_badge_stats_for_month(self.profile, 2026, 1)

        self.assertEqual(stats['xp_earned'], 0)
        self.assertEqual(stats['badges_count'], 0)
        self.assertEqual(len(stats['badges_data']), 0)

    def test_badge_earned_different_month(self):
        """Test that badges earned in different months don't count."""
        # Earn badge in February
        earned_date = datetime(2026, 2, 15, 12, 0, 0, tzinfo=timezone.utc)
        UserBadge.objects.create(
            profile=self.profile,
            badge=self.bronze_badge,
            earned_at=earned_date
        )

        # Check January stats
        stats = MonthlyRecapService.get_badge_stats_for_month(self.profile, 2026, 1)

        self.assertEqual(stats['xp_earned'], 0)
        self.assertEqual(stats['badges_count'], 0)

    def test_badge_with_zero_stages(self):
        """Test badge with zero required stages (edge case)."""
        zero_stage_badge = Badge.objects.create(
            name='Zero Stage Badge',
            series_slug='test-series',
            tier=1,
            required_stages=0
        )

        earned_date = datetime(2026, 1, 15, 12, 0, 0, tzinfo=timezone.utc)
        UserBadge.objects.create(
            profile=self.profile,
            badge=zero_stage_badge,
            earned_at=earned_date
        )

        stats = MonthlyRecapService.get_badge_stats_for_month(self.profile, 2026, 1)

        # Expected XP: 3000 (completion) + 0 (no stages) = 3000
        self.assertEqual(stats['xp_earned'], BADGE_TIER_XP)
        self.assertEqual(stats['badges_count'], 1)

    def test_calculate_progress_xp_for_badge_helper(self):
        """Test the helper function directly for tier-specific XP."""
        # Bronze: 10 stages × 250 XP = 2500
        bronze_xp = calculate_progress_xp_for_badge(self.bronze_badge, 10)
        self.assertEqual(bronze_xp, 2500)

        # Silver: 8 stages × 75 XP = 600
        silver_xp = calculate_progress_xp_for_badge(self.silver_badge, 8)
        self.assertEqual(silver_xp, 600)

        # Gold: 6 stages × 250 XP = 1500
        gold_xp = calculate_progress_xp_for_badge(self.gold_badge, 6)
        self.assertEqual(gold_xp, 1500)

        # Platinum: 4 stages × 75 XP = 300
        platinum_xp = calculate_progress_xp_for_badge(self.platinum_badge, 4)
        self.assertEqual(platinum_xp, 300)

    def test_tier_xp_map_values(self):
        """Verify TIER_XP_MAP constants are correct."""
        self.assertEqual(TIER_XP_MAP[1], 250)  # Bronze
        self.assertEqual(TIER_XP_MAP[2], 75)   # Silver
        self.assertEqual(TIER_XP_MAP[3], 250)  # Gold
        self.assertEqual(TIER_XP_MAP[4], 75)   # Platinum
        self.assertEqual(BADGE_TIER_XP, 3000)  # Completion bonus

    def test_badges_data_structure(self):
        """Test that badges_data contains expected fields."""
        earned_date = datetime(2026, 1, 15, 12, 0, 0, tzinfo=timezone.utc)
        UserBadge.objects.create(
            profile=self.profile,
            badge=self.bronze_badge,
            earned_at=earned_date
        )

        stats = MonthlyRecapService.get_badge_stats_for_month(self.profile, 2026, 1)

        self.assertEqual(len(stats['badges_data']), 1)
        badge_data = stats['badges_data'][0]

        # Verify structure
        self.assertIn('name', badge_data)
        self.assertIn('tier', badge_data)
        self.assertIn('tier_name', badge_data)
        self.assertIn('series_slug', badge_data)
        self.assertIn('has_image', badge_data)
        self.assertIn('image_url', badge_data)
