"""
Comprehensive test suite for GuideRatingService.

Tests all functionality outlined in the implementation document including:
- Rating submission and validation
- Stats management
- Query helpers
- Author trust integration
"""
from django.test import TestCase
from django.core.exceptions import ValidationError
from django.contrib.auth import get_user_model
from decimal import Decimal

from trophies.models import (
    Profile, Game, Concept, Guide, GuideSection, GuideRating, AuthorTrust
)
from trophies.services.guide_service import GuideService
from trophies.services.guide_rating_service import GuideRatingService
from trophies.constants import TRUSTED_MIN_APPROVED_GUIDES, TRUSTED_MIN_TOTAL_STARS

User = get_user_model()


class GuideRatingServiceTestCase(TestCase):
    """Base test case with common fixtures for guide rating tests."""

    def setUp(self):
        """Create common test fixtures."""
        # Create users
        self.user1 = User.objects.create_user(
            username='testuser1',
            email='test1@example.com',
            password='testpass123'
        )
        self.user2 = User.objects.create_user(
            username='testuser2',
            email='test2@example.com',
            password='testpass123'
        )
        self.user3 = User.objects.create_user(
            username='testuser3',
            email='test3@example.com',
            password='testpass123'
        )

        # Create profiles
        self.author_profile = Profile.objects.create(
            psn_username='AuthorUser',
            is_linked=True,
            user=self.user1
        )
        self.rater_profile = Profile.objects.create(
            psn_username='RaterUser',
            is_linked=True,
            user=self.user2
        )
        self.rater_profile2 = Profile.objects.create(
            psn_username='RaterUser2',
            is_linked=True,
            user=self.user3
        )

        # Create concept and game
        self.concept = Concept.objects.create(
            unified_title='Test Concept',
        )
        self.game = Game.objects.create(
            title_name='Test Game',
            np_communication_id='NPWR12345_00',
            concept=self.concept
        )

        # Create a published guide
        self.guide = GuideService.create_guide(
            profile=self.author_profile,
            game=self.game,
            title='Test Guide',
            summary='A comprehensive test guide'
        )
        GuideService.add_section(self.guide, 'Section 1', 'Content 1')
        self.guide.status = 'published'
        self.guide.save()


class RatingSubmissionTests(GuideRatingServiceTestCase):
    """Tests for rating submission functionality."""

    def test_rate_guide_creates_rating(self):
        """Successfully submit a rating for a guide."""
        rating = GuideRatingService.rate_guide(
            guide=self.guide,
            profile=self.rater_profile,
            stars=5
        )

        self.assertIsNotNone(rating.id)
        self.assertEqual(rating.guide, self.guide)
        self.assertEqual(rating.profile, self.rater_profile)
        self.assertEqual(rating.stars, 5)

    def test_rate_guide_updates_existing_rating(self):
        """Updating an existing rating should not create a duplicate."""
        # Create initial rating
        rating1 = GuideRatingService.rate_guide(
            guide=self.guide,
            profile=self.rater_profile,
            stars=3
        )

        # Update rating
        rating2 = GuideRatingService.rate_guide(
            guide=self.guide,
            profile=self.rater_profile,
            stars=5
        )

        # Should be the same object
        self.assertEqual(rating1.id, rating2.id)
        rating1.refresh_from_db()
        self.assertEqual(rating1.stars, 5)

        # Should only have one rating
        self.assertEqual(
            GuideRating.objects.filter(
                guide=self.guide,
                profile=self.rater_profile
            ).count(),
            1
        )

    def test_rate_guide_validates_min_stars(self):
        """Rating with 0 stars should raise ValidationError."""
        with self.assertRaises(ValidationError) as context:
            GuideRatingService.rate_guide(
                guide=self.guide,
                profile=self.rater_profile,
                stars=0
            )
        self.assertIn('between 1 and 5', str(context.exception))

    def test_rate_guide_validates_max_stars(self):
        """Rating with 6 stars should raise ValidationError."""
        with self.assertRaises(ValidationError) as context:
            GuideRatingService.rate_guide(
                guide=self.guide,
                profile=self.rater_profile,
                stars=6
            )
        self.assertIn('between 1 and 5', str(context.exception))

    def test_rate_guide_cannot_rate_own_guide(self):
        """Authors cannot rate their own guides."""
        with self.assertRaises(ValidationError) as context:
            GuideRatingService.rate_guide(
                guide=self.guide,
                profile=self.author_profile,
                stars=5
            )
        self.assertIn('cannot rate your own guide', str(context.exception))

    def test_rate_guide_only_published_guides(self):
        """Can only rate published guides."""
        draft_guide = GuideService.create_guide(
            profile=self.author_profile,
            game=self.game,
            title='Draft Guide',
            summary='A draft guide'
        )
        GuideService.add_section(draft_guide, 'Section', 'Content')

        with self.assertRaises(ValidationError) as context:
            GuideRatingService.rate_guide(
                guide=draft_guide,
                profile=self.rater_profile,
                stars=5
            )
        self.assertIn('only rate published guides', str(context.exception))

    def test_rate_guide_all_valid_star_values(self):
        """All star values from 1-5 should be accepted."""
        for stars in range(1, 6):
            # Create separate profiles for each rating
            user = User.objects.create_user(
                username=f'user_{stars}',
                email=f'user{stars}@example.com',
                password='testpass123'
            )
            profile = Profile.objects.create(
                psn_username=f'User{stars}',
                is_linked=True,
                user=user
            )

            rating = GuideRatingService.rate_guide(
                guide=self.guide,
                profile=profile,
                stars=stars
            )
            self.assertEqual(rating.stars, stars)


class StatsManagementTests(GuideRatingServiceTestCase):
    """Tests for stats management methods."""

    def test_update_guide_stats_single_rating(self):
        """Stats should be correct with a single rating."""
        GuideRatingService.rate_guide(self.guide, self.rater_profile, 4)

        self.guide.refresh_from_db()
        self.assertEqual(self.guide.rating_count, 1)
        self.assertEqual(self.guide.rating_sum, 4)
        self.assertEqual(self.guide.average_rating, Decimal('4.00'))

    def test_update_guide_stats_multiple_ratings(self):
        """Stats should be correct with multiple ratings."""
        GuideRatingService.rate_guide(self.guide, self.rater_profile, 5)
        GuideRatingService.rate_guide(self.guide, self.rater_profile2, 3)

        self.guide.refresh_from_db()
        self.assertEqual(self.guide.rating_count, 2)
        self.assertEqual(self.guide.rating_sum, 8)
        self.assertEqual(self.guide.average_rating, Decimal('4.00'))

    def test_update_guide_stats_calculates_average_correctly(self):
        """Average should be calculated and rounded to 2 decimals."""
        # Create additional profiles for testing
        profiles = []
        for i in range(3, 6):
            user = User.objects.create_user(
                username=f'user_{i}',
                email=f'user{i}@example.com',
                password='testpass123'
            )
            profile = Profile.objects.create(
                psn_username=f'User{i}',
                is_linked=True,
                user=user
            )
            profiles.append(profile)

        # Submit ratings: 5, 4, 3 = avg 4.00
        GuideRatingService.rate_guide(self.guide, self.rater_profile, 5)
        GuideRatingService.rate_guide(self.guide, self.rater_profile2, 4)
        GuideRatingService.rate_guide(self.guide, profiles[0], 3)

        self.guide.refresh_from_db()
        self.assertEqual(self.guide.rating_count, 3)
        self.assertEqual(self.guide.rating_sum, 12)
        self.assertEqual(self.guide.average_rating, Decimal('4.00'))

    def test_update_guide_stats_handles_rounding(self):
        """Average should round correctly to 2 decimal places."""
        # Create profiles
        profiles = []
        for i in range(3, 6):
            user = User.objects.create_user(
                username=f'user_{i}',
                email=f'user{i}@example.com',
                password='testpass123'
            )
            profile = Profile.objects.create(
                psn_username=f'User{i}',
                is_linked=True,
                user=user
            )
            profiles.append(profile)

        # Submit ratings: 5, 5, 4 = 14/3 = 4.666... should round to 4.67
        GuideRatingService.rate_guide(self.guide, self.rater_profile, 5)
        GuideRatingService.rate_guide(self.guide, self.rater_profile2, 5)
        GuideRatingService.rate_guide(self.guide, profiles[0], 4)

        self.guide.refresh_from_db()
        self.assertEqual(self.guide.average_rating, Decimal('4.67'))

    def test_update_guide_stats_no_ratings(self):
        """Stats should be zero when guide has no ratings."""
        # Create a new guide with no ratings
        new_guide = GuideService.create_guide(
            profile=self.author_profile,
            game=self.game,
            title='New Guide',
            summary='A new guide with no ratings'
        )
        GuideService.add_section(new_guide, 'Section', 'Content')
        new_guide.status = 'published'
        new_guide.save()

        GuideRatingService.update_guide_stats(new_guide)

        new_guide.refresh_from_db()
        self.assertEqual(new_guide.rating_count, 0)
        self.assertEqual(new_guide.rating_sum, 0)
        self.assertEqual(new_guide.average_rating, Decimal('0.00'))

    def test_update_author_total_stars_single_guide(self):
        """Total stars should be correct for single guide."""
        GuideRatingService.rate_guide(self.guide, self.rater_profile, 5)
        GuideRatingService.rate_guide(self.guide, self.rater_profile2, 4)

        author_trust = AuthorTrust.objects.get(profile=self.author_profile)
        self.assertEqual(author_trust.total_stars_received, 9)

    def test_update_author_total_stars_multiple_guides(self):
        """Total stars should sum across all published guides."""
        # Create second guide
        guide2 = GuideService.create_guide(
            profile=self.author_profile,
            game=self.game,
            title='Second Guide',
            summary='Second guide summary'
        )
        GuideService.add_section(guide2, 'Section', 'Content')
        guide2.status = 'published'
        guide2.save()

        # Rate both guides
        GuideRatingService.rate_guide(self.guide, self.rater_profile, 5)
        GuideRatingService.rate_guide(guide2, self.rater_profile2, 3)

        author_trust = AuthorTrust.objects.get(profile=self.author_profile)
        self.assertEqual(author_trust.total_stars_received, 8)

    def test_update_author_total_stars_only_published(self):
        """Total stars should only count published guides."""
        # Create unpublished guide
        draft_guide = GuideService.create_guide(
            profile=self.author_profile,
            game=self.game,
            title='Draft Guide',
            summary='Draft guide summary'
        )
        GuideService.add_section(draft_guide, 'Section', 'Content')

        # Rate published guide
        GuideRatingService.rate_guide(self.guide, self.rater_profile, 5)

        # Manually create rating for draft guide (bypass validation)
        GuideRating.objects.create(
            guide=draft_guide,
            profile=self.rater_profile2,
            stars=4
        )

        # Update stats
        GuideRatingService.update_author_total_stars(self.author_profile)

        author_trust = AuthorTrust.objects.get(profile=self.author_profile)
        self.assertEqual(author_trust.total_stars_received, 5)

    def test_update_author_total_stars_creates_author_trust(self):
        """update_author_total_stars should create AuthorTrust if needed."""
        # Ensure no AuthorTrust exists
        AuthorTrust.objects.filter(profile=self.author_profile).delete()

        GuideRatingService.update_author_total_stars(self.author_profile)

        self.assertTrue(
            AuthorTrust.objects.filter(profile=self.author_profile).exists()
        )


class AutoPromotionTests(GuideRatingServiceTestCase):
    """Tests for auto-promotion integration."""

    def test_rating_triggers_auto_promotion_check(self):
        """Rating should trigger auto-promotion check."""
        # Set up author near promotion threshold
        author_trust, _ = AuthorTrust.objects.get_or_create(
            profile=self.author_profile,
            defaults={
                'trust_level': 'new',
                'approved_guide_count': TRUSTED_MIN_APPROVED_GUIDES,
                'total_stars_received': 0
            }
        )
        # Update in case it already existed
        author_trust.trust_level = 'new'
        author_trust.approved_guide_count = TRUSTED_MIN_APPROVED_GUIDES
        author_trust.save()

        # Create ratings that sum to TRUSTED_MIN_TOTAL_STARS - 1 (99 stars)
        # We'll create 19 ratings of 5 stars each = 95 stars
        # Plus 1 rating of 4 stars = 99 total
        for i in range(19):
            user = User.objects.create_user(
                username=f'setup_user_{i}',
                email=f'setup{i}@example.com',
                password='testpass123'
            )
            profile = Profile.objects.create(
                psn_username=f'SetupUser{i}',
                is_linked=True,
                user=user
            )
            GuideRatingService.rate_guide(self.guide, profile, 5)

        # Add the 4-star rating to get to 99
        GuideRatingService.rate_guide(self.guide, self.rater_profile2, 4)

        # Verify we're at 99 stars
        author_trust.refresh_from_db()
        self.assertEqual(author_trust.total_stars_received, 99)

        # Rate to push over threshold (99 + 1 = 100)
        GuideRatingService.rate_guide(self.guide, self.rater_profile, 1)

        author_trust.refresh_from_db()
        self.assertEqual(author_trust.total_stars_received, 100)
        self.assertEqual(author_trust.trust_level, 'trusted')
        self.assertIsNotNone(author_trust.promoted_at)

    def test_rating_does_not_promote_without_approved_guides(self):
        """Rating alone without approved guides should not promote."""
        author_trust, _ = AuthorTrust.objects.get_or_create(
            profile=self.author_profile,
            defaults={
                'trust_level': 'new',
                'approved_guide_count': 1,
                'total_stars_received': 0
            }
        )
        # Update in case it already existed
        author_trust.trust_level = 'new'
        author_trust.approved_guide_count = 1
        author_trust.total_stars_received = 0
        author_trust.save()

        # Rate many times to exceed star threshold
        for i in range(TRUSTED_MIN_TOTAL_STARS // 5 + 1):
            user = User.objects.create_user(
                username=f'user_{i}',
                email=f'user{i}@example.com',
                password='testpass123'
            )
            profile = Profile.objects.create(
                psn_username=f'User{i}',
                is_linked=True,
                user=user
            )
            GuideRatingService.rate_guide(self.guide, profile, 5)

        author_trust.refresh_from_db()
        self.assertEqual(author_trust.trust_level, 'new')


class QueryHelperTests(GuideRatingServiceTestCase):
    """Tests for query helper methods."""

    def test_get_user_rating_returns_rating(self):
        """get_user_rating should return user's rating."""
        GuideRatingService.rate_guide(self.guide, self.rater_profile, 4)

        stars = GuideRatingService.get_user_rating(self.guide, self.rater_profile)
        self.assertEqual(stars, 4)

    def test_get_user_rating_returns_none_when_not_rated(self):
        """get_user_rating should return None when user hasn't rated."""
        stars = GuideRatingService.get_user_rating(self.guide, self.rater_profile)
        self.assertIsNone(stars)

    def test_get_rating_distribution_empty(self):
        """Distribution should have all zeros for unrated guide."""
        distribution = GuideRatingService.get_rating_distribution(self.guide)

        self.assertEqual(distribution, {1: 0, 2: 0, 3: 0, 4: 0, 5: 0})

    def test_get_rating_distribution_single_rating(self):
        """Distribution should correctly show single rating."""
        GuideRatingService.rate_guide(self.guide, self.rater_profile, 5)

        distribution = GuideRatingService.get_rating_distribution(self.guide)

        self.assertEqual(distribution, {1: 0, 2: 0, 3: 0, 4: 0, 5: 1})

    def test_get_rating_distribution_multiple_ratings(self):
        """Distribution should correctly aggregate multiple ratings."""
        # Create profiles for testing
        profiles = []
        for i in range(3, 8):
            user = User.objects.create_user(
                username=f'user_{i}',
                email=f'user{i}@example.com',
                password='testpass123'
            )
            profile = Profile.objects.create(
                psn_username=f'User{i}',
                is_linked=True,
                user=user
            )
            profiles.append(profile)

        # Submit varied ratings
        GuideRatingService.rate_guide(self.guide, self.rater_profile, 5)
        GuideRatingService.rate_guide(self.guide, self.rater_profile2, 5)
        GuideRatingService.rate_guide(self.guide, profiles[0], 4)
        GuideRatingService.rate_guide(self.guide, profiles[1], 4)
        GuideRatingService.rate_guide(self.guide, profiles[2], 3)

        distribution = GuideRatingService.get_rating_distribution(self.guide)

        self.assertEqual(distribution, {1: 0, 2: 0, 3: 1, 4: 2, 5: 2})


class DeleteRatingTests(GuideRatingServiceTestCase):
    """Tests for delete_rating functionality."""

    def test_delete_rating_success(self):
        """Successfully delete a rating."""
        GuideRatingService.rate_guide(self.guide, self.rater_profile, 5)

        result = GuideRatingService.delete_rating(self.guide, self.rater_profile)

        self.assertTrue(result)
        self.assertFalse(
            GuideRating.objects.filter(
                guide=self.guide,
                profile=self.rater_profile
            ).exists()
        )

    def test_delete_rating_returns_false_when_not_found(self):
        """delete_rating should return False when rating doesn't exist."""
        result = GuideRatingService.delete_rating(self.guide, self.rater_profile)

        self.assertFalse(result)

    def test_delete_rating_recalculates_guide_stats(self):
        """Deleting rating should update guide stats."""
        GuideRatingService.rate_guide(self.guide, self.rater_profile, 5)
        GuideRatingService.rate_guide(self.guide, self.rater_profile2, 4)

        self.guide.refresh_from_db()
        self.assertEqual(self.guide.rating_count, 2)
        self.assertEqual(self.guide.rating_sum, 9)

        GuideRatingService.delete_rating(self.guide, self.rater_profile)

        self.guide.refresh_from_db()
        self.assertEqual(self.guide.rating_count, 1)
        self.assertEqual(self.guide.rating_sum, 4)
        self.assertEqual(self.guide.average_rating, Decimal('4.00'))

    def test_delete_rating_recalculates_author_total_stars(self):
        """Deleting rating should update author total stars."""
        GuideRatingService.rate_guide(self.guide, self.rater_profile, 5)
        GuideRatingService.rate_guide(self.guide, self.rater_profile2, 4)

        author_trust = AuthorTrust.objects.get(profile=self.author_profile)
        self.assertEqual(author_trust.total_stars_received, 9)

        GuideRatingService.delete_rating(self.guide, self.rater_profile)

        author_trust.refresh_from_db()
        self.assertEqual(author_trust.total_stars_received, 4)


class EdgeCaseTests(GuideRatingServiceTestCase):
    """Tests for edge cases and boundary conditions."""

    def test_rating_with_all_ones(self):
        """Guide with all 1-star ratings should calculate correctly."""
        profiles = []
        for i in range(5):
            user = User.objects.create_user(
                username=f'user_{i}',
                email=f'user{i}@example.com',
                password='testpass123'
            )
            profile = Profile.objects.create(
                psn_username=f'User{i}',
                is_linked=True,
                user=user
            )
            profiles.append(profile)
            GuideRatingService.rate_guide(self.guide, profile, 1)

        self.guide.refresh_from_db()
        self.assertEqual(self.guide.average_rating, Decimal('1.00'))
        self.assertEqual(self.guide.rating_sum, 5)

    def test_rating_with_all_fives(self):
        """Guide with all 5-star ratings should calculate correctly."""
        profiles = []
        for i in range(5):
            user = User.objects.create_user(
                username=f'user_{i}',
                email=f'user{i}@example.com',
                password='testpass123'
            )
            profile = Profile.objects.create(
                psn_username=f'User{i}',
                is_linked=True,
                user=user
            )
            profiles.append(profile)
            GuideRatingService.rate_guide(self.guide, profile, 5)

        self.guide.refresh_from_db()
        self.assertEqual(self.guide.average_rating, Decimal('5.00'))
        self.assertEqual(self.guide.rating_sum, 25)

    def test_update_rating_from_low_to_high(self):
        """Updating rating from 1 to 5 should recalculate correctly."""
        GuideRatingService.rate_guide(self.guide, self.rater_profile, 1)
        self.guide.refresh_from_db()
        self.assertEqual(self.guide.average_rating, Decimal('1.00'))

        GuideRatingService.rate_guide(self.guide, self.rater_profile, 5)
        self.guide.refresh_from_db()
        self.assertEqual(self.guide.average_rating, Decimal('5.00'))

    def test_update_rating_from_high_to_low(self):
        """Updating rating from 5 to 1 should recalculate correctly."""
        GuideRatingService.rate_guide(self.guide, self.rater_profile, 5)
        self.guide.refresh_from_db()
        self.assertEqual(self.guide.average_rating, Decimal('5.00'))

        GuideRatingService.rate_guide(self.guide, self.rater_profile, 1)
        self.guide.refresh_from_db()
        self.assertEqual(self.guide.average_rating, Decimal('1.00'))

    def test_large_number_of_ratings(self):
        """System should handle many ratings correctly."""
        # Create 100 ratings
        for i in range(100):
            user = User.objects.create_user(
                username=f'user_{i}',
                email=f'user{i}@example.com',
                password='testpass123'
            )
            profile = Profile.objects.create(
                psn_username=f'User{i}',
                is_linked=True,
                user=user
            )
            # Alternate between 4 and 5 stars
            stars = 4 if i % 2 == 0 else 5
            GuideRatingService.rate_guide(self.guide, profile, stars)

        self.guide.refresh_from_db()
        self.assertEqual(self.guide.rating_count, 100)
        # 50 fours + 50 fives = 450, average = 4.5
        self.assertEqual(self.guide.average_rating, Decimal('4.50'))
