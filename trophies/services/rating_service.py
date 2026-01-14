"""
Game rating aggregation service.

This module handles the calculation and caching of community rating averages
for game concepts, including difficulty, grindiness, fun, and time estimates.
"""
from django.db.models import Avg, Count
from django.core.cache import cache
from trophies.util_modules.language import calculate_trimmed_mean


class RatingService:
    """Handles game rating aggregation and statistics."""

    # Cache timeout for rating averages (1 hour)
    RATING_CACHE_TIMEOUT = 3600

    @staticmethod
    def get_community_averages(concept):
        """
        Calculate community rating averages for a game concept.

        Aggregates user ratings to calculate:
        - Average difficulty (1-10 scale)
        - Average grindiness (1-10 scale)
        - Average fun ranking (1-10 scale)
        - Average overall rating (1-10 scale)
        - Average hours to platinum (trimmed mean with 10% trim)
        - Total rating count

        Uses trimmed mean for hours to reduce impact of outliers.

        Args:
            concept: Concept instance to calculate averages for

        Returns:
            dict or None: Dictionary with rating averages, or None if no ratings exist
                {
                    'avg_difficulty': float,
                    'avg_grindiness': float,
                    'avg_fun': float,
                    'avg_rating': float,
                    'avg_hours': float,
                    'count': int
                }

        Example:
            >>> averages = RatingService.get_community_averages(concept)
            >>> if averages:
            ...     print(f"Average difficulty: {averages['avg_difficulty']}")
            ...     print(f"Based on {averages['count']} ratings")
        """
        ratings = concept.user_ratings.all()
        if not ratings.exists():
            return None

        # Calculate standard aggregates
        aggregates = ratings.aggregate(
            avg_difficulty=Avg('difficulty'),
            avg_grindiness=Avg('grindiness'),
            avg_fun=Avg('fun_ranking'),
            avg_rating=Avg('overall_rating'),
            count=Count('id')
        )

        # Calculate trimmed mean for hours (removes outliers)
        hours_list = list(ratings.values_list('hours_to_platinum', flat=True))
        aggregates['avg_hours'] = (
            calculate_trimmed_mean(hours_list, trim_percent=0.1)
            if hours_list
            else None
        )

        return aggregates

    @staticmethod
    def get_cached_community_averages(concept):
        """
        Get community rating averages with caching.

        Checks cache first, calculates and caches if not found.

        Args:
            concept: Concept instance

        Returns:
            dict or None: Rating averages dictionary, or None if no ratings

        Example:
            >>> averages = RatingService.get_cached_community_averages(concept)
        """
        cache_key = f"concept:averages:{concept.id}"
        averages = cache.get(cache_key)

        if averages is None:
            averages = RatingService.get_community_averages(concept)
            if averages:
                cache.set(cache_key, averages, RatingService.RATING_CACHE_TIMEOUT)

        return averages

    @staticmethod
    def invalidate_cache(concept):
        """
        Invalidate cached rating averages for a concept.

        Call this when a new rating is added or an existing rating is updated.

        Args:
            concept: Concept instance to invalidate cache for

        Example:
            >>> # After user submits a rating
            >>> RatingService.invalidate_cache(concept)
        """
        cache_key = f"concept:averages:{concept.id}"
        cache.delete(cache_key)

    @staticmethod
    def update_concept_ratings(concept):
        """
        Recalculate and cache concept ratings.

        Useful for batch updates or when you want to ensure cache is fresh.

        Args:
            concept: Concept instance to update

        Returns:
            dict or None: Updated rating averages

        Example:
            >>> RatingService.update_concept_ratings(concept)
        """
        averages = RatingService.get_community_averages(concept)
        if averages:
            cache_key = f"concept:averages:{concept.id}"
            cache.set(cache_key, averages, RatingService.RATING_CACHE_TIMEOUT)
        return averages

    @staticmethod
    def get_rating_statistics(concept):
        """
        Get detailed rating statistics including distribution.

        Args:
            concept: Concept instance

        Returns:
            dict: Detailed statistics including rating distribution

        Example:
            >>> stats = RatingService.get_rating_statistics(concept)
            >>> print(f"Median difficulty: {stats.get('median_difficulty')}")
        """
        ratings = concept.user_ratings.all()
        if not ratings.exists():
            return None

        # Get basic averages
        stats = RatingService.get_community_averages(concept)

        # Could add more detailed statistics here:
        # - Median values
        # - Standard deviations
        # - Rating distributions
        # - Recent trends

        return stats
