"""
Custom model managers and querysets for trophies app.

This module provides reusable query patterns that were previously
scattered throughout views. Using custom managers improves code
reusability, testability, and maintainability.
"""
from django.db import models
from django.db.models import Q, Exists, OuterRef, Value


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
        Filter to premium user profiles using denormalized field.

        Returns:
            QuerySet: Profiles with active premium status
        """
        return self.filter(user_is_premium=True)

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


# The concept-canonical ELECTION ORDER (SEO Lane 1, the "light version" -- his call: no FK, no
# cron, no absorb() branch). One deterministic ordering names the SKU that represents a concept
# in search: has trophy data first, then the SKU most of our hunters actually own
# (played_count), then id for stability. Self-healing: the election is computed, never stored.
def canonical_election_order():
    """The ordering expressions; kept as one function so the sitemap's window and the
    per-concept pick can never disagree about who the canonical sibling is."""
    from django.db.models import Case, IntegerField, Value, When
    return [
        # NOTE: emptiness, not counts -- a malformed {'bronze': 0} blob scores as having
    # trophies. Accepted: played_count dominates in practice, and JSON-sum ordering is not
    # worth the SQL. Revisit only if a real mis-election shows up.
    Case(When(defined_trophies={}, then=Value(1)), default=Value(0),
             output_field=IntegerField()),
        models.F('played_count').desc(),
        models.F('id').asc(),
    ]


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

    @staticmethod
    def _game_page_key():
        """The GAME PAGE a row belongs to. Mirrors Concept.game_page_url exactly (trusted igdb id, else
        concept_id, else the row itself), so everything keyed on it agrees on what one game is."""
        from django.db.models import Case, CharField, Value, When
        from django.db.models.functions import Cast, Concat

        from trophies.models import IGDBMatch

        return Case(
            When(
                concept__igdb_match__status__in=IGDBMatch.TRUSTED_STATUSES,
                concept__igdb_match__igdb_id__isnull=False,
                then=Concat(Value('igdb:'), Cast('concept__igdb_match__igdb_id', CharField())),
            ),
            When(concept__isnull=False,
                 then=Concat(Value('concept:'), models.F('concept__concept_id'))),
            default=Concat(Value('game:'), Cast('id', CharField())),
            output_field=CharField(),
        )

    def game_page_canonicals(self):
        """One Game per GAME PAGE -- the Games/Trophy Lists IA identity, which is broader than
        the retired per-concept election: deliberately-split concepts sharing a trusted igdb_id are ONE page, so
        they elect ONE representative between them. Partitioned on `_game_page_key`, so the sitemap can never
        advertise two rows for one page or a page for zero rows. Same election ordering inside the
        partition; concept-less rows partition on their own id and stand for themselves."""
        from django.db.models import Window
        from django.db.models.functions import RowNumber

        return (
            self.annotate(
                _election_rank=Window(
                    expression=RowNumber(),
                    partition_by=[self._game_page_key()],
                    order_by=canonical_election_order(),
                )
            )
            .filter(_election_rank=1)
        )

    def game_page_counts(self, since):
        """How many GAME PAGES these rows make, and how many of those first appeared at or after `since`.

        The count Browse Games shows (one card per page; the same np floor its grid applies), for figures that
        must agree with it: the landing's "Games in catalog" counted trophy lists, so a game on PS4 and PS5
        counted twice (review FV-21). Grouped in the database, two aggregates over one grouping; meant for the
        hourly cron, never a request path."""
        from django.db.models import Min

        pages = (
            self.filter(np_communication_id__isnull=False).exclude(np_communication_id='')
            .annotate(_page=self._game_page_key()).values('_page')
            .annotate(_first=Min('created_at'))
        )
        return {'total': pages.count(), 'weekly': pages.filter(_first__gte=since).count()}

    def exclude_shovelware(self):
        """
        Exclude shovelware games: any `SHOVELWARE_FLAGGED_STATUSES` status (auto or manual).
        `manually_cleared` counts as clean. Who gets flagged, and why, is
        docs/reference/shovelware-detection.md.

        Returns:
            QuerySet: Non-shovelware games
        """
        from trophies.models import SHOVELWARE_FLAGGED_STATUSES
        return self.exclude(shovelware_status__in=SHOVELWARE_FLAGGED_STATUSES)

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

    def game_page_canonicals(self):
        """Proxy to GameQuerySet.game_page_canonicals -- one implementation, manager-style."""
        return self.get_queryset().game_page_canonicals()

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

    def live(self):
        """Filter to only badges visible to regular users."""
        return self.filter(is_live=True)

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
            badge_type: Type of badge ('series', 'collection', 'megamix', 'developer', 'user', 'genre', 'misc')

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


class BadgeSeriesQuerySet(models.QuerySet):
    """Custom queryset for BadgeSeries (the grouping-badge system)."""

    def live(self):
        """Series with at least one live edition: the set the badge pages serve (a series with none 404s).

        An EXISTS rather than a join on `group_badges`, so a series with several live editions is still one
        row and callers need no `.distinct()`."""
        from trophies.models import GroupBadge
        return self.filter(Exists(GroupBadge.objects.filter(series=OuterRef('pk'), is_live=True)))


class BadgeManager(models.Manager):
    """Custom manager for Badge model."""

    def get_queryset(self):
        """Return custom queryset."""
        return BadgeQuerySet(self.model, using=self._db)

    def live(self):
        """Proxy to queryset method."""
        return self.get_queryset().live()

    def by_series(self, series_slug):
        """Proxy to queryset method."""
        return self.get_queryset().by_series(series_slug)

    def by_type(self, badge_type):
        """Proxy to queryset method."""
        return self.get_queryset().by_type(badge_type)

    def with_most_recent_concept(self):
        """Proxy to queryset method."""
        return self.get_queryset().with_most_recent_concept()


class CommentQuerySet(models.QuerySet):
    """Custom queryset for Comment model."""

    def active(self):
        """
        Filter to non-deleted comments.

        Returns:
            QuerySet: Comments that are not soft-deleted
        """
        return self.filter(is_deleted=False)

    def replies_to(self, comment):
        """
        Get direct replies to a comment.

        Args:
            comment: Comment instance

        Returns:
            QuerySet: Direct child comments
        """
        return self.filter(parent=comment)

    def by_new(self):
        """
        Order by creation date descending.

        Returns:
            QuerySet: Comments ordered by newest first
        """
        return self.order_by('-created_at')


class CommentManager(models.Manager):
    """Custom manager for Comment model."""

    def get_queryset(self):
        """Return custom queryset."""
        return CommentQuerySet(self.model, using=self._db)

    def active(self):
        """Proxy to queryset method."""
        return self.get_queryset().active()

    def by_new(self):
        """Proxy to queryset method."""
        return self.get_queryset().by_new()


class ChecklistQuerySet(models.QuerySet):
    """Custom queryset for Checklist model."""

    def active(self):
        """
        Filter to non-deleted checklists.

        Returns:
            QuerySet: Checklists that are not soft-deleted
        """
        return self.filter(is_deleted=False)

    def published(self):
        """
        Filter to published checklists only.

        Returns:
            QuerySet: Published, non-deleted checklists
        """
        return self.filter(status='published', is_deleted=False)

    def drafts(self):
        """
        Filter to draft checklists only.

        Returns:
            QuerySet: Draft, non-deleted checklists
        """
        return self.filter(status='draft', is_deleted=False)

    def for_concept(self, concept):
        """
        Get all checklists for a concept.

        Args:
            concept: Concept instance

        Returns:
            QuerySet: Checklists for the concept
        """
        return self.filter(concept=concept)

    def by_author(self, profile):
        """
        Get all checklists by an author.

        Args:
            profile: Profile instance

        Returns:
            QuerySet: Checklists created by the profile
        """
        return self.filter(profile=profile)

    def by_top(self):
        """
        Order by upvote count (default sort).

        Returns:
            QuerySet: Checklists ordered by most upvotes first
        """
        return self.order_by('-upvote_count', '-created_at')

    def by_new(self):
        """
        Order by creation date descending.

        Returns:
            QuerySet: Checklists ordered by newest first
        """
        return self.order_by('-created_at')

    def by_popular(self):
        """
        Order by usage count (most users tracking).

        Returns:
            QuerySet: Checklists ordered by most tracked first
        """
        return self.order_by('-progress_save_count', '-upvote_count')

    def with_author_data(self):
        """
        Optimize with profile and user data.

        Returns:
            QuerySet: Checklists with prefetched author data
        """
        return self.select_related('profile', 'profile__user')

    def with_sections(self):
        """
        Prefetch sections and items for detail view.

        Returns:
            QuerySet: Checklists with prefetched sections and items
        """
        from django.db.models import Prefetch
        from trophies.models import ChecklistSection

        return self.prefetch_related(
            Prefetch(
                'sections',
                queryset=ChecklistSection.objects.prefetch_related('items').order_by('order')
            )
        )

    def with_vote_check(self, profile):
        """
        Annotate whether the given profile has voted on each checklist.

        Args:
            profile: Profile instance to check votes for

        Returns:
            QuerySet: Checklists with 'user_has_voted' annotation
        """
        if not profile:
            return self.annotate(user_has_voted=Value(False))

        from trophies.models import ChecklistVote

        return self.annotate(
            user_has_voted=Exists(
                ChecklistVote.objects.filter(
                    checklist=OuterRef('pk'),
                    profile=profile
                )
            )
        )

    def with_progress_check(self, profile):
        """
        Annotate user's progress percentage on each checklist.

        Args:
            profile: Profile instance to check progress for

        Returns:
            QuerySet: Checklists with 'user_progress_percentage' annotation
        """
        from django.db.models import FloatField, Subquery
        from django.db.models.functions import Coalesce

        if not profile:
            return self.annotate(
                user_progress_percentage=Value(0.0, output_field=FloatField())
            )

        from trophies.models import UserChecklistProgress

        return self.annotate(
            user_progress_percentage=Coalesce(
                Subquery(
                    UserChecklistProgress.objects.filter(
                        checklist=OuterRef('pk'),
                        profile=profile
                    ).values('progress_percentage')[:1]
                ),
                Value(0.0),
                output_field=FloatField()
            )
        )


class ChecklistManager(models.Manager):
    """Custom manager for Checklist model."""

    def get_queryset(self):
        """Return custom queryset."""
        return ChecklistQuerySet(self.model, using=self._db)

    def active(self):
        """Proxy to queryset method."""
        return self.get_queryset().active()

    def published(self):
        """Proxy to queryset method."""
        return self.get_queryset().published()

    def drafts(self):
        """Proxy to queryset method."""
        return self.get_queryset().drafts()

    def for_concept(self, concept):
        """Proxy to queryset method."""
        return self.get_queryset().for_concept(concept)

    def by_author(self, profile):
        """Proxy to queryset method."""
        return self.get_queryset().by_author(profile)

    def by_top(self):
        """Proxy to queryset method."""
        return self.get_queryset().by_top()

    def by_new(self):
        """Proxy to queryset method."""
        return self.get_queryset().by_new()

    def by_popular(self):
        """Proxy to queryset method."""
        return self.get_queryset().by_popular()

    def get_checklists_for_concept(self, concept, profile=None, sort='top'):
        """
        Get published checklists for a concept with optimizations.

        Args:
            concept: Concept instance
            profile: Optional viewing profile (for vote/progress status)
            sort: 'top', 'new', or 'popular'

        Returns:
            QuerySet: Checklists optimized for display
        """
        qs = self.published().for_concept(concept).with_author_data()

        if profile:
            qs = qs.with_vote_check(profile).with_progress_check(profile)

        if sort == 'new':
            qs = qs.by_new()
        elif sort == 'popular':
            qs = qs.by_popular()
        else:
            qs = qs.by_top()

        return qs


