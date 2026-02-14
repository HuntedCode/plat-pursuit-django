from django.db import models
from django.utils import timezone


class AnalyticsSession(models.Model):
    """
    Analytics session tracking with 30-minute inactivity timeout.

    Separate from Django sessions to decouple analytics from auth. Tracks user
    engagement across pageviews within a session window (30min = GA standard).
    """
    session_id = models.UUIDField(primary_key=True, editable=False)

    # User identification
    user_id = models.IntegerField(null=True, blank=True, db_index=True,
                                   help_text="User ID if authenticated during session creation")
    ip_address = models.GenericIPAddressField(null=True, blank=True,
                                               help_text="IP address for anonymous users")

    # Session lifecycle
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    last_activity = models.DateTimeField(auto_now=True, db_index=True)
    ended_at = models.DateTimeField(null=True, blank=True, help_text="Session end time (optional)")

    # Engagement metrics (for future analytics)
    page_count = models.PositiveIntegerField(default=0, help_text="Number of pages viewed in this session")
    page_sequence = models.JSONField(default=list, blank=True,
                                       help_text="Sequence of pages visited: [{'page_type': ..., 'object_id': ..., 'timestamp': ...}]")

    # Traffic source (for future analysis)
    referrer = models.URLField(max_length=500, blank=True, null=True, help_text="HTTP Referer header")
    user_agent = models.CharField(max_length=500, blank=True, null=True, help_text="User-Agent string")

    class Meta:
        indexes = [
            models.Index(fields=['user_id', 'created_at'], name='as_user_created_idx'),
            models.Index(fields=['last_activity'], name='as_last_activity_idx'),
        ]
        ordering = ['-created_at']
        verbose_name = "Analytics Session"
        verbose_name_plural = "Analytics Sessions"

    def __str__(self):
        user_str = f"User {self.user_id}" if self.user_id else f"IP {self.ip_address}"
        return f"Session {str(self.session_id)[:8]} - {user_str} - {self.page_count} pages"


class PageView(models.Model):
    """
    Records individual deduplicated page views across the site.
    One row per unique session+page per 30-minute window (enforced via Redis dedup in tracking service).
    """
    PAGE_TYPE_CHOICES = [
        # Detail pages (have per-object view_count)
        ('profile', 'Profile'),
        ('game', 'Game'),
        ('guide', 'Guide'),
        ('badge', 'Badge Series'),
        ('index', 'Index Page'),
        # List pages
        ('profiles_list', 'Profiles List'),
        ('games_list', 'Games List'),
        ('trophies_list', 'Trophies List'),
        ('badges_list', 'Badges List'),
        ('guides_list', 'Guides List'),
        ('milestones_list', 'Milestones List'),
        # Leaderboard / showcase pages
        ('badge_leaderboard', 'Badge Leaderboard'),
        ('overall_leaderboard', 'Overall Badge Leaderboard'),
        ('trophy_case', 'Trophy Case'),
        # User-specific pages
        ('my_guides', 'My Guides'),
        ('my_shareables', 'My Shareables'),
        ('guide_edit', 'Guide Edit'),
        ('settings', 'Settings'),
        ('subscription', 'Subscription Management'),
        ('email_prefs', 'Email Preferences'),
        # Utility pages
        ('notifications', 'Notifications'),
        ('recap', 'Monthly Recap'),
        # Game lists
        ('game_list', 'Game List'),
        ('game_lists_browse', 'Browse Game Lists'),
        ('my_lists', 'My Lists'),
        ('game_list_edit', 'Game List Edit'),
        # Challenges
        ('challenges_browse', 'Challenge Hub'),
        ('my_challenges', 'My Challenges'),
        ('az_challenge', 'A-Z Challenge'),
        ('az_challenge_setup', 'A-Z Challenge Setup'),
        ('az_challenge_edit', 'A-Z Challenge Edit'),
    ]

    page_type = models.CharField(max_length=20, choices=PAGE_TYPE_CHOICES, db_index=True)
    object_id = models.CharField(
        max_length=100,
        db_index=True,
        help_text="PK or slug for the object. Profile/Game/Checklist use integer PK as string; Badge uses series_slug."
    )
    viewed_at = models.DateTimeField(default=timezone.now, db_index=True)
    user_id = models.IntegerField(null=True, blank=True, db_index=True)
    ip_address = models.GenericIPAddressField(
        null=True,
        blank=True,
        help_text="Stored for anonymous viewers only. Consider scrubbing after 90 days for GDPR compliance."
    )
    session_id = models.UUIDField(
        null=True,
        blank=True,
        db_index=True,
        help_text="Analytics session ID. Null for pre-migration records (before session-based tracking)."
    )

    class Meta:
        indexes = [
            models.Index(fields=['page_type', 'object_id'], name='pv_type_object_idx'),
            models.Index(fields=['page_type', 'object_id', 'viewed_at'], name='pv_type_object_date_idx'),
            models.Index(fields=['session_id', 'viewed_at'], name='pv_session_date_idx'),
            models.Index(fields=['page_type', 'object_id', 'session_id'], name='pv_type_obj_session_idx'),
        ]
        ordering = ['-viewed_at']

    def __str__(self):
        return f"{self.page_type}:{self.object_id} at {self.viewed_at:%Y-%m-%d %H:%M}"


class SiteEvent(models.Model):
    """
    Internal-only event tracking for admin analytics. Not shown publicly.
    Tracks guide visits, platinum share card downloads, recap interactions, and other site events.
    """
    EVENT_TYPE_CHOICES = [
        ('guide_visit', 'Guide Visit'),
        ('share_card_download', 'Platinum Share Card Download'),
        ('recap_page_view', 'Monthly Recap Page View'),
        ('recap_share_generate', 'Monthly Recap Share Card View'),
        ('recap_image_download', 'Recap Image Download'),
        ('game_list_create', 'Game List Created'),
        ('game_list_share', 'Game List Shared'),
        ('challenge_create', 'Challenge Created'),
        ('challenge_complete', 'Challenge Completed'),
    ]

    event_type = models.CharField(max_length=30, choices=EVENT_TYPE_CHOICES, db_index=True)
    object_id = models.CharField(
        max_length=100,
        blank=True,
        db_index=True,
        help_text="Related object ID: guide slug, earned_trophy_id, or recap 'YYYY-MM' string."
    )
    occurred_at = models.DateTimeField(default=timezone.now, db_index=True)
    user_id = models.IntegerField(null=True, blank=True, db_index=True)

    class Meta:
        indexes = [
            models.Index(fields=['event_type', 'object_id'], name='se_type_object_idx'),
        ]
        ordering = ['-occurred_at']

    def __str__(self):
        return f"{self.event_type}:{self.object_id} at {self.occurred_at:%Y-%m-%d %H:%M}"


class SiteSettings(models.Model):
    """
    Singleton model for site-wide settings and metrics.
    Should only ever have one row (id=1).
    """
    id = models.IntegerField(primary_key=True, default=1, editable=False)

    # Denormalized metrics
    index_page_view_count = models.PositiveIntegerField(
        default=0,
        help_text="Total view count for the index/home page (deduplicated via PageView tracking)."
    )

    # Session tracking metadata
    session_tracking_enabled_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="Date when session-based tracking was enabled. Used for UI display '(since X date)' label."
    )

    class Meta:
        verbose_name = "Site Settings"
        verbose_name_plural = "Site Settings"

    def __str__(self):
        return "Site Settings"

    def save(self, *args, **kwargs):
        # Enforce singleton - always use id=1
        self.id = 1
        super().save(*args, **kwargs)

    @classmethod
    def get_settings(cls):
        """Get or create the singleton settings instance."""
        obj, created = cls.objects.get_or_create(id=1)
        return obj
