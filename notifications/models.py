from django.db import models
from django.contrib.auth import get_user_model
from django.utils import timezone

CustomUser = get_user_model()


class NotificationTemplate(models.Model):
    """
    Reusable notification templates with variable substitution.
    Templates can be automatically triggered by events or used manually by admins.
    """
    NOTIFICATION_TYPES = [
        ('platinum_earned', 'Platinum Trophy Earned'),
        ('badge_awarded', 'Badge Awarded'),
        ('milestone_achieved', 'Milestone Achieved'),
        ('monthly_recap', 'Monthly Recap Available'),
        ('subscription_created', 'Subscription Created'),
        ('subscription_updated', 'Subscription Updated'),
        ('discord_verified', 'Discord Verified'),
        ('challenge_completed', 'Challenge Completed'),
        ('admin_announcement', 'Admin Announcement'),
        ('system_alert', 'System Alert'),
        ('payment_failed', 'Payment Failed'),
        ('payment_action_required', 'Payment Action Required'),
    ]

    PRIORITY_CHOICES = [
        ('low', 'Low'),
        ('normal', 'Normal'),
        ('high', 'High'),
        ('urgent', 'Urgent'),
    ]

    name = models.CharField(max_length=100, unique=True, help_text="Unique template name")
    notification_type = models.CharField(max_length=50, choices=NOTIFICATION_TYPES)

    # Template content with {variable} placeholders
    title_template = models.CharField(
        max_length=255,
        help_text="Use {variable} for substitution, e.g. 'New {trophy_type} earned!'"
    )
    message_template = models.TextField(
        max_length=1000,
        help_text="Use {variable} for substitution"
    )
    icon = models.CharField(max_length=50, default='🔔', help_text="Emoji or icon name")

    # Action configuration
    action_url_template = models.CharField(
        max_length=500,
        blank=True,
        help_text="URL template, e.g. '/game/{game_id}/'"
    )
    action_text = models.CharField(
        max_length=100,
        blank=True,
        help_text="Button text, e.g. 'View Game'"
    )

    # Automation settings
    auto_trigger_enabled = models.BooleanField(
        default=False,
        help_text="Automatically create notifications when event occurs"
    )
    trigger_event = models.CharField(
        max_length=50,
        blank=True,
        help_text="Event name that triggers this template"
    )

    # Defaults
    priority = models.CharField(max_length=20, choices=PRIORITY_CHOICES, default='normal')

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['name']
        verbose_name = 'Notification Template'
        verbose_name_plural = 'Notification Templates'

    def __str__(self):
        return f"{self.name} ({self.get_notification_type_display()})"


class Notification(models.Model):
    """
    Individual notification for a user.
    Stores the rendered content and tracks read status.
    """
    NOTIFICATION_TYPES = NotificationTemplate.NOTIFICATION_TYPES
    PRIORITY_CHOICES = NotificationTemplate.PRIORITY_CHOICES

    recipient = models.ForeignKey(
        CustomUser,
        on_delete=models.CASCADE,
        related_name='notifications',
        help_text="User who receives this notification"
    )
    notification_type = models.CharField(
        max_length=50,
        choices=NOTIFICATION_TYPES,
        db_index=True
    )
    template = models.ForeignKey(
        NotificationTemplate,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        help_text="Template used to create this notification"
    )

    # Rendered content (already substituted)
    title = models.CharField(max_length=255)
    message = models.TextField(max_length=1000)
    detail = models.TextField(
        max_length=2500,
        blank=True,
        help_text="Rich text detail content (supports markdown)"
    )
    icon = models.CharField(max_length=50, blank=True, default='🔔')

    # Action (optional)
    action_url = models.URLField(blank=True, null=True, max_length=500)
    action_text = models.CharField(max_length=100, blank=True)

    # Banner image (optional)
    banner_image = models.ImageField(
        upload_to='notification-banners/%Y/%m/',
        blank=True,
        null=True,
        help_text="Banner image displayed at top of notification detail view"
    )

    # Structured sections (alternative to markdown detail)
    sections = models.JSONField(
        default=list,
        blank=True,
        help_text='Structured sections as JSON array. Each section has: id, header, icon, content, order'
    )

    # Metadata
    priority = models.CharField(
        max_length=20,
        choices=PRIORITY_CHOICES,
        default='normal',
        db_index=True
    )
    metadata = models.JSONField(
        default=dict,
        blank=True,
        help_text="Additional data (trophy_id, badge_id, etc.)"
    )

    # Status tracking
    is_read = models.BooleanField(default=False, db_index=True)
    read_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ['-created_at']
        verbose_name = 'Notification'
        verbose_name_plural = 'Notifications'
        indexes = [
            models.Index(fields=['recipient', '-created_at']),
            models.Index(fields=['recipient', 'is_read', '-created_at']),
            models.Index(fields=['notification_type', '-created_at']),
        ]

    def __str__(self):
        status = "Read" if self.is_read else "Unread"
        return f"{self.recipient.email} - {self.title} ({status})"

    def mark_as_read(self):
        """Mark this notification as read."""
        if not self.is_read:
            self.is_read = True
            self.read_at = timezone.now()
            self.save(update_fields=['is_read', 'read_at'])


class PlatinumShareImage(models.Model):
    """
    Stores generated share images for platinum notifications.
    Images are stored in S3 with lifecycle management for cleanup.
    """
    FORMAT_CHOICES = [
        ('landscape', 'Landscape (1200x630)'),
        ('portrait', 'Portrait (1080x1350)'),
    ]

    notification = models.ForeignKey(
        Notification,
        on_delete=models.CASCADE,
        related_name='share_images',
        help_text="The platinum notification this image was generated for"
    )
    format = models.CharField(
        max_length=20,
        choices=FORMAT_CHOICES,
        help_text="Image format/dimensions"
    )
    image = models.ImageField(
        upload_to='platinum-share-images/%Y/%m/',
        help_text="Generated share image stored in S3"
    )
    created_at = models.DateTimeField(auto_now_add=True)
    download_count = models.PositiveIntegerField(
        default=0,
        help_text="Number of times this image has been downloaded"
    )

    class Meta:
        unique_together = ['notification', 'format']
        verbose_name = 'Platinum Share Image'
        verbose_name_plural = 'Platinum Share Images'
        indexes = [
            models.Index(fields=['notification', 'format']),
            models.Index(fields=['created_at']),
        ]

    def __str__(self):
        return f"Share image ({self.format}) for notification {self.notification_id}"


class ScheduledNotification(models.Model):
    """
    Stores scheduled notifications for future sending.
    Processed by a management command run via Render cron (hourly).
    """
    STATUS_CHOICES = [
        ('pending', 'Pending'),
        ('processing', 'Processing'),
        ('sent', 'Sent'),
        ('cancelled', 'Cancelled'),
        ('failed', 'Failed'),
    ]

    TARGET_TYPE_CHOICES = [
        ('all', 'All Active Users'),
        ('premium_monthly', 'Premium Monthly'),
        ('premium_yearly', 'Premium Yearly'),
        ('premium_supporter', 'Premium Supporter'),
        ('premium_all', 'All Premium'),
        ('discord_verified', 'Discord Verified'),
        ('individual', 'Individual Users'),
    ]

    # Content
    notification_type = models.CharField(
        max_length=50,
        choices=NotificationTemplate.NOTIFICATION_TYPES,
        default='admin_announcement'
    )
    title = models.CharField(max_length=255)
    message = models.TextField(max_length=1000)
    detail = models.TextField(
        max_length=2500,
        blank=True,
        help_text="Rich text detail content (supports markdown)"
    )
    icon = models.CharField(max_length=50, default='📢')
    action_url = models.URLField(blank=True, null=True, max_length=500)
    action_text = models.CharField(max_length=100, blank=True)
    priority = models.CharField(
        max_length=20,
        choices=NotificationTemplate.PRIORITY_CHOICES,
        default='normal'
    )

    # Banner image (optional)
    banner_image = models.ImageField(
        upload_to='notification-banners/%Y/%m/',
        blank=True,
        null=True,
        help_text="Banner image for scheduled notification"
    )

    # Structured sections (alternative to markdown detail)
    sections = models.JSONField(
        default=list,
        blank=True,
        help_text='Structured sections as JSON array. Each section has: id, header, icon, content, order'
    )

    # Targeting
    target_type = models.CharField(max_length=50, choices=TARGET_TYPE_CHOICES)
    target_criteria = models.JSONField(
        default=dict,
        blank=True,
        help_text='Criteria like {"user_ids": [1,2,3]} for individual targeting'
    )

    # Scheduling
    scheduled_at = models.DateTimeField(db_index=True)
    status = models.CharField(
        max_length=20,
        choices=STATUS_CHOICES,
        default='pending',
        db_index=True
    )

    # Tracking
    created_by = models.ForeignKey(
        CustomUser,
        on_delete=models.SET_NULL,
        null=True,
        related_name='scheduled_notifications'
    )
    created_at = models.DateTimeField(auto_now_add=True)
    sent_at = models.DateTimeField(null=True, blank=True)
    recipient_count = models.PositiveIntegerField(
        default=0,
        help_text='Estimated or actual recipient count'
    )
    error_message = models.TextField(blank=True)

    class Meta:
        ordering = ['-scheduled_at']
        verbose_name = 'Scheduled Notification'
        verbose_name_plural = 'Scheduled Notifications'
        indexes = [
            models.Index(fields=['status', 'scheduled_at']),
            models.Index(fields=['created_by', '-created_at']),
        ]

    def __str__(self):
        return f"{self.title} ({self.get_status_display()}) - {self.scheduled_at}"

    def validate_sections(self):
        """Validate sections structure and constraints."""
        if not self.sections:
            return True, None

        if not isinstance(self.sections, list):
            return False, "Sections must be a list"

        if len(self.sections) > 5:
            return False, "Maximum 5 sections allowed"

        for idx, section in enumerate(self.sections):
            required = {'id', 'header', 'icon', 'content', 'order'}
            if not required.issubset(section.keys()):
                return False, f"Section {idx+1} missing required fields"

            if len(section.get('header', '')) > 100:
                return False, f"Section {idx+1} header exceeds 100 characters"

            if len(section.get('content', '')) > 800:
                return False, f"Section {idx+1} content exceeds 800 characters"

        return True, None


class NotificationLog(models.Model):
    """
    Audit log for sent bulk notifications.
    Created after each successful bulk send (immediate or scheduled).
    """
    scheduled_notification = models.ForeignKey(
        ScheduledNotification,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='logs',
        help_text='Link to scheduled notification if applicable'
    )

    # Snapshot of what was sent
    notification_type = models.CharField(max_length=50)
    title = models.CharField(max_length=255)
    message = models.TextField(max_length=1000)
    detail = models.TextField(max_length=2500, blank=True)
    target_type = models.CharField(max_length=50)
    target_criteria = models.JSONField(default=dict)

    # Results
    recipient_count = models.PositiveIntegerField()
    sent_at = models.DateTimeField(auto_now_add=True)
    sent_by = models.ForeignKey(
        CustomUser,
        on_delete=models.SET_NULL,
        null=True,
        related_name='notification_logs'
    )

    # Was it immediate or scheduled?
    was_scheduled = models.BooleanField(default=False)

    class Meta:
        ordering = ['-sent_at']
        verbose_name = 'Notification Log'
        verbose_name_plural = 'Notification Logs'

    def __str__(self):
        scheduled_text = " (scheduled)" if self.was_scheduled else ""
        return f"{self.title} - {self.recipient_count} recipients{scheduled_text}"
