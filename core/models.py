from django.conf import settings
from django.db import models
from django.utils import timezone


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
        ('easter_egg', 'Easter Egg'),
        ('sync_search', 'Sync Search'),
        ('welcome_tour_complete', 'Welcome Tour Completed'),
        ('welcome_tour_skip', 'Welcome Tour Skipped'),
        ('game_detail_tour_complete', 'Game Detail Tour Completed'),
        ('game_detail_tour_skip', 'Game Detail Tour Skipped'),
        ('badge_detail_tour_complete', 'Badge Detail Tour Completed'),
        ('badge_detail_tour_skip', 'Badge Detail Tour Skipped'),
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


class EmailLog(models.Model):
    """
    Audit trail for all emails sent from the platform.

    Tracks subscription emails, account emails (verification, password reset),
    content emails (monthly recaps), and admin announcements. Used by the
    subscription admin dashboard and for general email audit purposes.
    """
    EMAIL_TYPES = [
        # Subscription lifecycle
        ('payment_failed', 'Payment Failed Warning'),
        ('payment_failed_final', 'Payment Failed Final Warning'),
        ('subscription_cancelled', 'Subscription Cancelled'),
        ('subscription_welcome', 'Subscription Welcome'),
        ('payment_succeeded', 'Payment Succeeded'),
        ('payment_action_required', 'Payment Action Required'),
        ('gift_code', 'Gift Code Issued'),
        ('gift_expired', 'Gift Access Ended'),
        # Account
        ('email_verification', 'Email Verification'),
        ('password_reset', 'Password Reset'),
        # Content
        ('monthly_recap', 'Monthly Recap'),
        ('weekly_digest', 'Weekly Digest'),
        # Achievements
        ('badge_earned', 'Badge Earned'),
        ('milestone_achieved', 'Milestone Achieved'),
        # Onboarding
        ('welcome', 'Welcome'),
        # Admin
        ('admin_announcement', 'Admin Announcement'),
        # Fundraiser
        ('donation_receipt', 'Donation Receipt'),
        ('badge_claim_confirmation', 'Badge Claim Confirmation'),
        ('artwork_complete', 'Badge Artwork Complete'),
    ]
    STATUS_CHOICES = [
        ('sent', 'Sent'),
        ('suppressed', 'Suppressed (Preference)'),
        ('failed', 'Failed'),
    ]

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        related_name='email_logs', null=True, blank=True,
    )
    recipient_email = models.EmailField()
    email_type = models.CharField(max_length=50, choices=EMAIL_TYPES, db_index=True)
    subject = models.CharField(max_length=255)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    triggered_by = models.CharField(
        max_length=30, default='system',
        help_text="Origin: system, webhook, admin_manual, management_command",
    )
    metadata = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['user', '-created_at']),
            models.Index(fields=['email_type', '-created_at']),
        ]

    def __str__(self):
        target = self.user.email if self.user else self.recipient_email
        return f"{self.get_email_type_display()} -> {target} ({self.status})"


class CommunityTrophyDay(models.Model):
    """
    Daily aggregate of trophy activity from Discord-linked profiles.

    Computed and posted to Discord at ~12:30 PM ET for the previous ET day
    by the post_community_trophy_tracker management command. Each row is
    immutable after posting; the `posted_at` field gates against double-posts.

    Eligibility: Profile.discord_id IS NOT NULL, Trophy.game.shovelware_status='clean',
    EarnedTrophy.earned_date_time within the target ET calendar day.

    PP Score = total_trophies + (5 * total_platinums) + (3 * total_ultra_rares).
    Weights are applied at compute time and stored; changing the formula does
    NOT retroactively recompute historical rows.
    """
    date = models.DateField(unique=True, help_text="The ET calendar day these stats cover.")

    total_trophies = models.PositiveIntegerField(default=0)
    total_platinums = models.PositiveIntegerField(default=0)
    total_ultra_rares = models.PositiveIntegerField(default=0)
    pp_score = models.PositiveIntegerField(default=0)

    eligible_profile_count = models.PositiveIntegerField(
        default=0,
        help_text="Snapshot of how many Discord-linked profiles existed when this day was computed. Internal diagnostic.",
    )

    posted_at = models.DateTimeField(null=True, blank=True, help_text="When the Discord webhook post succeeded. Idempotency gate.")
    computed_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-date']
        indexes = [
            models.Index(fields=['-pp_score'], name='ctd_pp_score_idx'),
            models.Index(fields=['-total_platinums'], name='ctd_plat_idx'),
            models.Index(fields=['-total_trophies'], name='ctd_trophies_idx'),
            models.Index(fields=['-total_ultra_rares'], name='ctd_ur_idx'),
        ]
        verbose_name = "Community Trophy Day"
        verbose_name_plural = "Community Trophy Days"

    def __str__(self):
        return f"{self.date}: {self.total_trophies:,}T / {self.total_platinums:,}P / PP={self.pp_score:,}"
