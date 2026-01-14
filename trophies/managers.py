"""
Custom model managers and querysets for trophies app.

This module provides reusable query patterns that were previously
scattered throughout views. Using custom managers improves code
reusability, testability, and maintainability.
"""
from django.db import models
from django.db.models import Q


class ProfileQuerySet(models.QuerySet):
    """Custom queryset for Profile model with common query patterns."""

    def synced(self):
        """
        Filter to successfully synced profiles.

        Returns:
            QuerySet: Profiles with sync_status='synced'
        """
        return self.filter(sync_status='synced')

    def needs_sync(self):
        """
        Filter to profiles that need syncing.

        Profiles need sync if they have never been synced or if their
        sync status indicates an error state.

        Returns:
            QuerySet: Profiles that need syncing
        """
        return self.filter(
            Q(sync_status__isnull=True) | Q(sync_status='error')
        )

    def premium_users(self):
        """
        Filter to premium user profiles.

        Returns:
            QuerySet: Profiles belonging to users with active premium tiers
        """
        return self.filter(
            user__premium_tier__in=['premium_monthly', 'premium_yearly', 'supporter']
        )

    def verified(self):
        """
        Filter to PSN-verified profiles.

        Returns:
            QuerySet: Profiles that have completed PSN verification
        """
        return self.filter(verification_status='verified')


class ProfileManager(models.Manager):
    """Custom manager for Profile model."""

    def get_queryset(self):
        """Return custom queryset."""
        return ProfileQuerySet(self.model, using=self._db)

    def synced(self):
        """Proxy to queryset method."""
        return self.get_queryset().synced()

    def needs_sync(self):
        """Proxy to queryset method."""
        return self.get_queryset().needs_sync()

    def premium_users(self):
        """Proxy to queryset method."""
        return self.get_queryset().premium_users()

    def verified(self):
        """Proxy to queryset method."""
        return self.get_queryset().verified()


class GameQuerySet(models.QuerySet):
    """Custom queryset for Game model with filtering utilities."""

    def obtainable(self):
        """
        Filter to obtainable games only.

        Excludes games marked as unobtainable (delisted, region-locked, etc.)

        Returns:
            QuerySet: Games that can still be obtained
        """
        return self.filter(is_obtainable=True)

    def exclude_shovelware(self):
        """
        Exclude shovelware games.

        Shovelware is identified by low-quality indicators like
        extremely high platinum rates or very short completion times.

        Returns:
            QuerySet: Non-shovelware games
        """
        return self.filter(is_shovelware=False)

    def for_platform(self, platforms):
        """
        Filter games by platform(s).

        Args:
            platforms: Single platform string or list of platforms
                      (e.g., 'PS5' or ['PS4', 'PS5'])

        Returns:
            QuerySet: Games available on specified platform(s)

        Example:
            Game.objects.for_platform(['PS5', 'PS4'])
        """
        # Normalize to list
        if isinstance(platforms, str):
            platforms = [platforms]

        # Build OR query for each platform
        platform_filter = Q()
        for plat in platforms:
            platform_filter |= Q(title_platform__contains=plat)

        return self.filter(platform_filter)

    def for_region(self, regions):
        """
        Filter games by region(s).

        Args:
            regions: Single region string or list of regions
                    (e.g., 'NA' or ['NA', 'EU', 'global'])

        Returns:
            QuerySet: Games available in specified region(s)

        Example:
            Game.objects.for_region(['NA', 'EU'])

        Note:
            'global' is a special region indicating non-regional games
        """
        # Normalize to list
        if isinstance(regions, str):
            regions = [regions]

        # Build OR query for each region
        region_filter = Q()
        for r in regions:
            if r == 'global':
                # Global games are marked as non-regional
                region_filter |= Q(is_regional=False)
            else:
                # Regional games have region code in their region field
                region_filter |= Q(is_regional=True, region__contains=r)

        return self.filter(region_filter)

    def with_trophy_counts(self):
        """
        Optimize query by selecting related trophy count data.

        Returns:
            QuerySet: Games with prefetched trophy group data
        """
        return self.prefetch_related('trophy_groups')


class GameManager(models.Manager):
    """Custom manager for Game model."""

    def get_queryset(self):
        """Return custom queryset."""
        return GameQuerySet(self.model, using=self._db)

    def obtainable(self):
        """Proxy to queryset method."""
        return self.get_queryset().obtainable()

    def exclude_shovelware(self):
        """Proxy to queryset method."""
        return self.get_queryset().exclude_shovelware()

    def for_platform(self, platforms):
        """Proxy to queryset method."""
        return self.get_queryset().for_platform(platforms)

    def for_region(self, regions):
        """Proxy to queryset method."""
        return self.get_queryset().for_region(regions)


class ProfileGameQuerySet(models.QuerySet):
    """Custom queryset for ProfileGame model."""

    def for_profile(self, profile):
        """
        Filter to games for a specific profile.

        Args:
            profile: Profile instance

        Returns:
            QuerySet: ProfileGames belonging to the profile
        """
        return self.filter(profile=profile)

    def with_progress(self):
        """
        Optimize query with related game and concept data.

        Use this when displaying game lists with progress information
        to avoid N+1 query problems.

        Returns:
            QuerySet: ProfileGames with optimized select_related
        """
        return self.select_related('game', 'game__concept')

    def excluding_hidden(self, profile):
        """
        Exclude hidden games if profile setting is enabled.

        Args:
            profile: Profile instance to check settings

        Returns:
            QuerySet: ProfileGames excluding hidden games (if applicable)
        """
        if profile.hide_hiddens:
            return self.exclude(user_hidden=True)
        return self

    def platinum_earned(self):
        """
        Filter to games where platinum trophy was earned.

        Returns:
            QuerySet: ProfileGames with has_plat=True
        """
        return self.filter(has_plat=True)

    def completed(self):
        """
        Filter to 100% completed games.

        Returns:
            QuerySet: ProfileGames with progress=100
        """
        return self.filter(progress=100)

    def in_progress(self):
        """
        Filter to games with some progress but not completed.

        Returns:
            QuerySet: ProfileGames with 0 < progress < 100
        """
        return self.filter(
            progress__gt=0,
            progress__lt=100
        )

    def not_started(self):
        """
        Filter to games with no progress.

        Returns:
            QuerySet: ProfileGames with progress=0
        """
        return self.filter(progress=0)


class ProfileGameManager(models.Manager):
    """Custom manager for ProfileGame model."""

    def get_queryset(self):
        """Return custom queryset."""
        return ProfileGameQuerySet(self.model, using=self._db)

    def for_profile(self, profile):
        """Proxy to queryset method."""
        return self.get_queryset().for_profile(profile)

    def with_progress(self):
        """Proxy to queryset method."""
        return self.get_queryset().with_progress()

    def platinum_earned(self):
        """Proxy to queryset method."""
        return self.get_queryset().platinum_earned()


class BadgeQuerySet(models.QuerySet):
    """Custom queryset for Badge model."""

    def by_series(self, series_slug):
        """
        Filter badges by series, ordered by tier.

        Args:
            series_slug: Slug identifier for the badge series

        Returns:
            QuerySet: Badges in the series, ordered by tier

        Example:
            Badge.objects.by_series('final-fantasy')
        """
        return self.filter(series_slug=series_slug).order_by('tier')

    def by_type(self, badge_type):
        """
        Filter badges by type.

        Args:
            badge_type: Type of badge ('series', 'collection', 'misc')

        Returns:
            QuerySet: Badges of the specified type
        """
        return self.filter(badge_type=badge_type)

    def with_most_recent_concept(self):
        """
        Optimize query by selecting related most_recent_concept data.

        Returns:
            QuerySet: Badges with select_related('most_recent_concept')
        """
        return self.select_related('most_recent_concept')


class BadgeManager(models.Manager):
    """Custom manager for Badge model."""

    def get_queryset(self):
        """Return custom queryset."""
        return BadgeQuerySet(self.model, using=self._db)

    def by_series(self, series_slug):
        """Proxy to queryset method."""
        return self.get_queryset().by_series(series_slug)

    def by_type(self, badge_type):
        """Proxy to queryset method."""
        return self.get_queryset().by_type(badge_type)

    def with_most_recent_concept(self):
        """Proxy to queryset method."""
        return self.get_queryset().with_most_recent_concept()


class MilestoneQuerySet(models.QuerySet):
    """Custom queryset for Milestone model."""

    def by_criteria_type(self, criteria_type):
        """
        Filter milestones by criteria type.

        Args:
            criteria_type: Type of milestone criteria (e.g., 'plat_count', 'manual')

        Returns:
            QuerySet: Milestones of the specified criteria type
        """
        return self.filter(criteria_type=criteria_type)

    def ordered_by_value(self):
        """
        Order milestones by their required value.

        Returns:
            QuerySet: Milestones ordered by required_value ascending
        """
        return self.order_by('required_value')

    def premium_only(self):
        """
        Filter to premium-only milestones.

        Returns:
            QuerySet: Milestones that require premium subscription
        """
        return self.filter(premium_only=True)

    def manual_award(self):
        """
        Filter to manually awarded milestones.

        Returns:
            QuerySet: Milestones that are manually awarded by admins
        """
        return self.filter(manual_award=True)


class MilestoneManager(models.Manager):
    """Custom manager for Milestone model."""

    def get_queryset(self):
        """Return custom queryset."""
        return MilestoneQuerySet(self.model, using=self._db)

    def by_criteria_type(self, criteria_type):
        """Proxy to queryset method."""
        return self.get_queryset().by_criteria_type(criteria_type)

    def ordered_by_value(self):
        """Proxy to queryset method."""
        return self.get_queryset().ordered_by_value()

    def get_for_user(self, profile, criteria_type=None):
        """
        Fetch milestones for user, optionally filtered by criteria, with progress ratio.

        This method preserves the existing functionality from the old MilestoneManager.

        Args:
            profile: Profile instance
            criteria_type: Optional criteria type filter

        Returns:
            QuerySet: Milestones with progress annotations
        """
        from django.db.models import F
        qs = self.prefetch_related('user_milestones', 'user_milestone_progress').filter(user_milestones__profile=profile)
        if criteria_type:
            qs = qs.filter(criteria_type=criteria_type)
        return qs.annotate(
            progress_ratio=F('user_milestone_progress__progress_value') / F('required_value')
        ).order_by('-progress_ratio', 'name')
