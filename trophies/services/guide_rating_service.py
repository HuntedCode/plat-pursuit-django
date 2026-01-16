"""
Guide rating service - Handles rating submission and stats aggregation.

This service manages:
- Submitting and updating guide ratings
- Calculating denormalized rating stats on Guide model
- Tracking total stars for author trust progression
"""
import logging
from decimal import Decimal
from django.db import transaction
from django.db.models import Count, Sum, Avg
from django.core.exceptions import ValidationError

from trophies.models import Guide, GuideRating, AuthorTrust
from trophies.constants import MAX_STARS, MIN_STARS, RATING_CACHE_TIMEOUT

logger = logging.getLogger(__name__)


class GuideRatingService:
    """Handles guide rating operations and statistics."""


    @staticmethod
    @transaction.atomic
    def rate_guide(guide, profile, stars):
        """
        Submit or update a rating for a guide.

        Args:
            guide: Guide instance to rate
            profile: Profile instance of the rater
            stars: Integer 1-5

        Returns:
            GuideRating: The created or updated rating

        Raises:
            ValidationError: If validation fails
        """
        # Validate stars
        if not (MIN_STARS <= stars <= MAX_STARS):
            raise ValidationError(f"Stars must be between {MIN_STARS} and {MAX_STARS}")

        # Can't rate own guide
        if profile.id == guide.author_id:
            raise ValidationError("You cannot rate your own guide")

        # Guide must be published
        if guide.status != 'published':
            raise ValidationError("You can only rate published guides")

        # Create or update rating
        rating, created = GuideRating.objects.update_or_create(
            guide=guide,
            profile=profile,
            defaults={'stars': stars}
        )

        # Update stats
        GuideRatingService.update_guide_stats(guide)
        GuideRatingService.update_author_total_stars(guide.author)

        # Check for auto-promotion
        from trophies.services.guide_service import GuideService
        author_trust = GuideService.get_or_create_author_trust(guide.author)
        GuideService._check_and_promote(author_trust)

        logger.info(
            f"{'Created' if created else 'Updated'} rating: "
            f"{profile.psn_username} rated {guide.title} {stars} stars"
        )

        return rating

    @staticmethod
    def update_guide_stats(guide):
        """Recalculate denormalized rating stats on Guide."""
        stats = guide.ratings.aggregate(
            count=Count('id'),
            total=Sum('stars'),
            avg=Avg('stars')
        )

        guide.rating_count = stats['count'] or 0
        guide.rating_sum = stats['total'] or 0
        guide.average_rating = Decimal(str(round(stats['avg'] or 0, 2)))
        guide.save(update_fields=['rating_count', 'rating_sum', 'average_rating'])

    @staticmethod
    def update_author_total_stars(profile):
        """Recalculate total stars received for an author."""
        total = GuideRating.objects.filter(
            guide__author=profile,
            guide__status='published'
        ).aggregate(total=Sum('stars'))['total'] or 0

        author_trust, _ = AuthorTrust.objects.get_or_create(profile=profile)
        author_trust.total_stars_received = total
        author_trust.save(update_fields=['total_stars_received'])

    @staticmethod
    def get_user_rating(guide, profile):
        """
        Get a user's existing rating for a guide.

        Returns:
            int or None: The star rating, or None if not rated
        """
        try:
            rating = GuideRating.objects.get(guide=guide, profile=profile)
            return rating.stars
        except GuideRating.DoesNotExist:
            return None

    @staticmethod
    def get_rating_distribution(guide):
        """
        Get rating distribution for a guide.

        Returns:
            dict: {1: count, 2: count, 3: count, 4: count, 5: count}
        """
        distribution = guide.ratings.values('stars').annotate(
            count=Count('id')
        ).order_by('stars')

        result = {i: 0 for i in range(1, 6)}
        for item in distribution:
            result[item['stars']] = item['count']

        return result

    @staticmethod
    @transaction.atomic
    def delete_rating(guide, profile):
        """
        Delete a user's rating.

        Returns:
            bool: True if deleted, False if not found
        """
        try:
            rating = GuideRating.objects.get(guide=guide, profile=profile)
            rating.delete()

            GuideRatingService.update_guide_stats(guide)
            GuideRatingService.update_author_total_stars(guide.author)

            logger.info(f"Deleted rating: {profile.psn_username} on {guide.title}")
            return True
        except GuideRating.DoesNotExist:
            return False
