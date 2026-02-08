from django.db import models
from django.utils import timezone


class PageView(models.Model):
    """
    Records individual deduplicated page views for public-facing content.
    One row per unique viewer+page per 24-hour window (enforced via Redis dedup in tracking service).
    """
    PAGE_TYPE_CHOICES = [
        ('profile', 'Profile'),
        ('game', 'Game'),
        ('checklist', 'Checklist'),
        ('badge', 'Badge Series'),
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

    class Meta:
        indexes = [
            models.Index(fields=['page_type', 'object_id'], name='pv_type_object_idx'),
            models.Index(fields=['page_type', 'object_id', 'viewed_at'], name='pv_type_object_date_idx'),
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
        ('recap_share_generate', 'Recap Share Card Generate (Legacy)'),
        ('recap_intro_view', 'Recap Intro Slide View'),
        ('recap_summary_view', 'Recap Summary Slide View'),
        ('recap_image_download', 'Recap Image Download'),
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
