from decimal import Decimal

from django.conf import settings
from django.core.validators import MinValueValidator
from django.db import models
from django.utils import timezone


class Fundraiser(models.Model):
    """
    A fundraising campaign. Each campaign has its own page, donor wall,
    and optional reward mechanics determined by campaign_type.

    Created and managed via Django admin. The staff page at /staff/fundraiser/
    is for monitoring donations and managing claims, not campaign CRUD.
    """
    CAMPAIGN_TYPES = [
        ('badge_artwork', 'Badge Artwork'),
    ]

    # Amount in USD per badge artwork pick. Referenced in donation_service.py,
    # fundraiser.js, fundraiser.html, and donation_receipt.html.
    BADGE_PICK_DIVISOR = Decimal('10')

    name = models.CharField(max_length=200, help_text='Display name shown on the fundraiser page.')
    slug = models.SlugField(max_length=100, unique=True, help_text='URL identifier, e.g. "badge-artwork"')
    description = models.TextField(help_text='Rich description shown on the fundraiser page.')
    campaign_type = models.CharField(
        max_length=30, choices=CAMPAIGN_TYPES, default='badge_artwork',
        help_text='Determines reward mechanics. badge_artwork enables badge claiming.',
    )
    banner_active = models.BooleanField(
        default=False,
        help_text='Show site-wide banner linking to this fundraiser (only while campaign is live).',
    )
    banner_text = models.CharField(
        max_length=200, blank=True,
        default='Help support our community.',
        help_text='Custom message shown in the site-wide banner between the campaign name and the CTA link.',
    )
    banner_dismiss_days = models.PositiveIntegerField(
        default=7,
        help_text='Days the banner stays dismissed after a user closes it. 0 means it reappears every session.',
    )
    start_date = models.DateTimeField(help_text='When the fundraiser opens to the public.')
    end_date = models.DateTimeField(
        null=True, blank=True,
        help_text='When the fundraiser closes. Leave blank for perpetual campaigns that never end.',
    )
    minimum_donation = models.DecimalField(
        max_digits=6, decimal_places=2, default=Decimal('1.00'),
        validators=[MinValueValidator(Decimal('0.01'))],
        help_text='Minimum donation amount in USD.',
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return self.name

    def is_upcoming(self):
        """Campaign exists but hasn't started yet (staff preview only)."""
        return timezone.now() < self.start_date

    def is_live(self):
        """Campaign is actively accepting donations."""
        now = timezone.now()
        if self.end_date is None:
            return now >= self.start_date
        return self.start_date <= now <= self.end_date

    def is_ended(self):
        """Campaign has closed (read-only archive)."""
        if self.end_date is None:
            return False
        return timezone.now() > self.end_date

    def show_banner(self):
        """Whether the site-wide banner should be displayed."""
        return self.banner_active and self.is_live()


class Donation(models.Model):
    """A one-time donation to a fundraiser campaign."""
    PROVIDER_CHOICES = [
        ('stripe', 'Stripe'),
        ('paypal', 'PayPal'),
    ]
    STATUS_CHOICES = [
        ('pending', 'Pending'),
        ('completed', 'Completed'),
        ('failed', 'Failed'),
        ('refunded', 'Refunded'),
    ]

    fundraiser = models.ForeignKey(
        Fundraiser, on_delete=models.CASCADE, related_name='donations',
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, related_name='donations',
    )
    profile = models.ForeignKey(
        'trophies.Profile', on_delete=models.SET_NULL,
        null=True, related_name='donations',
        help_text='Denormalized for donor wall display.',
    )
    amount = models.DecimalField(
        max_digits=8, decimal_places=2,
        validators=[MinValueValidator(Decimal('1.00'))],
    )
    provider = models.CharField(max_length=10, choices=PROVIDER_CHOICES)
    provider_transaction_id = models.CharField(
        max_length=255, unique=True,
        help_text='Stripe Checkout Session ID or PayPal Order ID.',
    )
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default='pending')
    badge_picks_earned = models.PositiveIntegerField(
        default=0,
        help_text='floor(amount / 10), calculated at completion for badge_artwork campaigns.',
    )
    badge_picks_used = models.PositiveIntegerField(default=0)
    is_anonymous = models.BooleanField(
        default=False,
        help_text='If True, donor is hidden from the public donor wall.',
    )
    message = models.CharField(
        max_length=200, blank=True,
        help_text='Optional public message from the donor.',
    )
    created_at = models.DateTimeField(auto_now_add=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    metadata = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ['-completed_at', '-created_at']
        indexes = [
            models.Index(fields=['fundraiser', 'status', '-completed_at']),
            models.Index(fields=['user', '-created_at']),
            models.Index(fields=['provider_transaction_id']),
        ]

    def __str__(self):
        username = self.profile.display_psn_username if self.profile else 'Anonymous'
        return f"${self.amount} by {username} ({self.status})"

    @property
    def badge_picks_remaining(self):
        return max(0, self.badge_picks_earned - self.badge_picks_used)


class DonationBadgeClaim(models.Model):
    """
    A donor's claim on a specific badge series for artwork commissioning.

    The badge field is a OneToOneField to enforce that each badge series
    can only be claimed by one donor (DB-level constraint).
    """
    STATUS_CHOICES = [
        ('claimed', 'Claimed'),
        ('in_progress', 'Artwork In Progress'),
        ('completed', 'Artwork Complete'),
    ]

    donation = models.ForeignKey(
        Donation, on_delete=models.CASCADE, related_name='badge_claims',
    )
    profile = models.ForeignKey(
        'trophies.Profile', on_delete=models.SET_NULL,
        null=True, related_name='badge_claims',
    )
    badge = models.OneToOneField(
        'trophies.Badge', on_delete=models.CASCADE, related_name='artwork_claim',
        help_text='Tier 1 badge of the series. OneToOne enforces one claim per badge.',
    )
    series_slug = models.CharField(max_length=100, db_index=True)
    series_name = models.CharField(
        max_length=255, blank=True,
        help_text='Denormalized display name at time of claim.',
    )
    status = models.CharField(max_length=15, choices=STATUS_CHOICES, default='claimed')
    claimed_at = models.DateTimeField(auto_now_add=True)
    completed_at = models.DateTimeField(
        null=True, blank=True,
        help_text='When the artwork was uploaded for this badge series.',
    )

    class Meta:
        ordering = ['-claimed_at']
        indexes = [
            models.Index(fields=['status']),
        ]

    def __str__(self):
        username = self.profile.display_psn_username if self.profile else 'Unknown'
        return f"{self.series_name} claimed by {username} ({self.status})"
