"""
View mixins for shared functionality across trophies app views.

This module provides reusable mixins for common patterns like breadcrumb
building and cache key generation, reducing code duplication across views.
"""
from django.urls import reverse_lazy


class BreadcrumbMixin:
    """Mixin for building breadcrumb navigation consistently across views."""

    def build_breadcrumbs(self, *breadcrumbs):
        """
        Build a breadcrumb navigation list.

        Args:
            *breadcrumbs: Variable number of breadcrumb dictionaries.
                         Each should have 'text' and optionally 'url' keys.
                         The last breadcrumb typically has no URL (current page).

        Returns:
            list: List of breadcrumb dictionaries for template rendering

        Example:
            >>> mixin = BreadcrumbMixin()
            >>> mixin.build_breadcrumbs(
            ...     {'text': 'Home', 'url': reverse_lazy('home')},
            ...     {'text': 'Games', 'url': reverse_lazy('game_list')},
            ...     {'text': 'God of War'}
            ... )
            [
                {'text': 'Home', 'url': '/'},
                {'text': 'Games', 'url': '/games/'},
                {'text': 'God of War'}
            ]
        """
        return list(breadcrumbs)

    def build_home_breadcrumb(self):
        """
        Build the standard Home breadcrumb.

        Returns:
            dict: Home breadcrumb dictionary
        """
        return {'text': 'Home', 'url': reverse_lazy('home')}

    def build_profiles_breadcrumb(self):
        """
        Build the standard Profiles breadcrumb.

        Returns:
            dict: Profiles list breadcrumb dictionary
        """
        return {'text': 'Profiles', 'url': reverse_lazy('profiles_list')}

    def build_games_breadcrumb(self):
        """
        Build the standard Games breadcrumb.

        Returns:
            dict: Games list breadcrumb dictionary
        """
        return {'text': 'Games', 'url': reverse_lazy('game_list')}

    def build_badges_breadcrumb(self):
        """
        Build the standard Badges breadcrumb.

        Returns:
            dict: Badges list breadcrumb dictionary
        """
        return {'text': 'Badges', 'url': reverse_lazy('badge_list')}

    def build_leaderboards_breadcrumb(self):
        """
        Build the standard Leaderboards breadcrumb.

        Returns:
            dict: Leaderboards breadcrumb dictionary
        """
        return {'text': 'Leaderboards', 'url': reverse_lazy('leaderboards')}


class CacheKeyMixin:
    """
    Mixin for generating consistent cache keys across views.

    Centralizes cache key patterns to ensure consistency and make it
    easier to update cache key formats in the future.
    """

    @staticmethod
    def game_images_key(np_comm_id):
        """
        Generate cache key for game image URLs.

        Args:
            np_comm_id: PSN communication ID for the game

        Returns:
            str: Cache key for game image URLs
        """
        return f"game:imageurls:{np_comm_id}"

    @staticmethod
    def game_stats_key(np_comm_id, date_str, hour):
        """
        Generate cache key for game statistics.

        Args:
            np_comm_id: PSN communication ID for the game
            date_str: Date string in YYYY-MM-DD format
            hour: Hour of the day (0-23)

        Returns:
            str: Cache key for game statistics
        """
        return f"game:stats:{np_comm_id}:{date_str}:{hour:02d}"

    @staticmethod
    def concept_averages_key(concept_id):
        """
        Generate cache key for concept community rating averages.

        Args:
            concept_id: ID of the Concept

        Returns:
            str: Cache key for concept rating averages
        """
        return f"concept:averages:{concept_id}"

    @staticmethod
    def leaderboard_key(leaderboard_type, date_str=None):
        """
        Generate cache key for leaderboard data.

        Args:
            leaderboard_type: Type of leaderboard (e.g., 'platinum', 'completion')
            date_str: Optional date string for time-based leaderboards

        Returns:
            str: Cache key for leaderboard data
        """
        if date_str:
            return f"leaderboard:{leaderboard_type}:{date_str}"
        return f"leaderboard:{leaderboard_type}"

    @staticmethod
    def profile_stats_key(profile_id):
        """
        Generate cache key for profile statistics.

        Args:
            profile_id: ID of the Profile

        Returns:
            str: Cache key for profile statistics
        """
        return f"profile:stats:{profile_id}"

    @staticmethod
    def badge_progress_key(profile_id, badge_id):
        """
        Generate cache key for badge progress data.

        Args:
            profile_id: ID of the Profile
            badge_id: ID of the Badge

        Returns:
            str: Cache key for badge progress
        """
        return f"badge:progress:{profile_id}:{badge_id}"


class PlatformFilterMixin:
    """
    Mixin for standardizing platform filtering across views.

    Provides helper methods for building platform filter queries
    consistently.
    """

    def get_platform_filter_params(self, request):
        """
        Extract platform filter parameters from request.

        Args:
            request: Django HttpRequest object

        Returns:
            list: List of platform strings to filter by, or None if no filter
        """
        platforms = request.GET.getlist('platform')
        if not platforms:
            return None
        return platforms

    def get_region_filter_params(self, request):
        """
        Extract region filter parameters from request.

        Args:
            request: Django HttpRequest object

        Returns:
            list: List of region strings to filter by, or None if no filter
        """
        regions = request.GET.getlist('region')
        if not regions:
            return None
        return regions
