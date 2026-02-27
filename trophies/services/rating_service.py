"""
Game rating aggregation service.

This module handles the calculation and caching of community rating averages
for game concepts, including difficulty, grindiness, fun, and time estimates.
Supports both base game ratings (concept_trophy_group=NULL) and DLC group ratings.
"""
from django.db.models import Avg, Count
from django.core.cache import cache
from trophies.util_modules.language import calculate_trimmed_mean


class RatingService:
    """Handles game rating aggregation and statistics."""

    # Cache timeout for rating averages (1 hour)
    RATING_CACHE_TIMEOUT = 3600

    @staticmethod
    def _compute_averages(ratings_qs):
        """Shared aggregation logic for a filtered ratings queryset.

        Args:
            ratings_qs: QuerySet of UserConceptRating

        Returns:
            dict or None
        """
        if not ratings_qs.exists():
            return None

        aggregates = ratings_qs.aggregate(
            avg_difficulty=Avg('difficulty'),
            avg_grindiness=Avg('grindiness'),
            avg_fun=Avg('fun_ranking'),
            avg_rating=Avg('overall_rating'),
            count=Count('id')
        )

        hours_list = list(ratings_qs.values_list('hours_to_platinum', flat=True))
        aggregates['avg_hours'] = (
            calculate_trimmed_mean(hours_list, trim_percent=0.1)
            if hours_list
            else None
        )

        return aggregates

    @staticmethod
    def get_community_averages(concept):
        """
        Calculate community rating averages for a game concept (base game only).

        Filters to base game ratings (concept_trophy_group=NULL) so DLC ratings
        do not skew the base game averages.

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
        """
        ratings = concept.user_ratings.filter(concept_trophy_group__isnull=True)
        return RatingService._compute_averages(ratings)

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
        ratings = concept.user_ratings.filter(concept_trophy_group__isnull=True)
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

    # ------------------------------------------------------------------ #
    #  DLC / Trophy Group rating methods
    # ------------------------------------------------------------------ #

    @staticmethod
    def get_community_averages_for_group(concept, concept_trophy_group):
        """Calculate community rating averages for a specific trophy group.

        For base game (trophy_group_id='default'): filters where
        concept_trophy_group IS NULL (backward compat with existing rows).
        For DLC: filters by the specific ConceptTrophyGroup FK.

        Args:
            concept: Concept instance
            concept_trophy_group: ConceptTrophyGroup instance

        Returns:
            dict or None: Rating averages dictionary
        """
        if concept_trophy_group.trophy_group_id == 'default':
            # Base game ratings have concept_trophy_group=NULL
            ratings = concept.user_ratings.filter(concept_trophy_group__isnull=True)
        else:
            ratings = concept.user_ratings.filter(
                concept_trophy_group=concept_trophy_group,
            )
        return RatingService._compute_averages(ratings)

    @staticmethod
    def get_cached_community_averages_for_group(concept, concept_trophy_group):
        """Get community averages for a trophy group with caching.

        Args:
            concept: Concept instance
            concept_trophy_group: ConceptTrophyGroup instance

        Returns:
            dict or None
        """
        cache_key = (
            f"concept:averages:{concept.id}:group:{concept_trophy_group.id}"
        )
        averages = cache.get(cache_key)
        if averages is None:
            averages = RatingService.get_community_averages_for_group(
                concept, concept_trophy_group,
            )
            if averages:
                cache.set(cache_key, averages, RatingService.RATING_CACHE_TIMEOUT)
        return averages

    @staticmethod
    def invalidate_group_cache(concept, concept_trophy_group):
        """Invalidate cached rating averages for a specific trophy group.

        Args:
            concept: Concept instance
            concept_trophy_group: ConceptTrophyGroup instance
        """
        cache_key = (
            f"concept:averages:{concept.id}:group:{concept_trophy_group.id}"
        )
        cache.delete(cache_key)
