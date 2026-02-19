from django.db import models
from django.conf import settings
from django.contrib.auth.models import AbstractUser, BaseUserManager
from django.utils import timezone
from django.utils.translation import gettext_lazy as _
import pytz
from trophies.util_modules.constants import REGIONS
from djstripe.models import Subscription
from users.constants import PREMIUM_TIER_CHOICES


class UserManager(BaseUserManager):
    """Custom user manager for email-based authentication."""
    def create_user(self, email, password=None, **extra_fields):
        """
        Create and return a regular user with email and password.

        Args:
            email: User's email address
            password: User's password
            **extra_fields: Additional fields for user model

        Returns:
            CustomUser: Created user instance

        Raises:
            ValueError: If email is not provided
        """
        if not email:
            raise ValueError('The Email field must be set')
        email = self.normalize_email(email)
        user = self.model(email=email, **extra_fields)
        user.set_password(password)
        user.save(using=self.db)
        return user

    def create_superuser(self, email, password=None, **extra_fields):
        """
        Create and return a superuser with admin privileges.

        Args:
            email: Superuser's email address
            password: Superuser's password
            **extra_fields: Additional fields for user model

        Returns:
            CustomUser: Created superuser instance

        Raises:
            ValueError: If is_staff or is_superuser are not True
        """
        extra_fields.setdefault('is_staff', True)
        extra_fields.setdefault('is_superuser', True)
        if extra_fields.get('is_staff') is not True:
            raise ValueError('Superuser must have is_staff=True.')
        if extra_fields.get('is_superuser') is not True:
            raise ValueError('Superuser must have is_superuser=True.')
        return self.create_user(email, password, **extra_fields)


class CustomUser(AbstractUser):
    """
    Custom user model with email-based authentication and premium subscriptions.

    This model extends Django's AbstractUser to use email as the primary
    authentication field and includes Stripe subscription integration.
    """
    email = models.EmailField(_("email address"), unique=True, blank=False, null=False)
    user_timezone = models.CharField(max_length=63, choices=[(tz, tz) for tz in pytz.common_timezones], default='UTC', help_text="User's preferred timezone. UTC default.")
    default_region = models.CharField(max_length=2, choices=[(r, r) for r in REGIONS], null=True, blank=True, default=None, help_text="User's preferred default region filter for games.")
    use_24hr_clock = models.BooleanField(default=False, help_text="Use 24-hour time format (23:00) instead of 12-hour AM/PM format (11:00 PM)")
    stripe_customer_id = models.CharField(max_length=255, blank=True, null=True, help_text="Stripe Customer ID for this user.")
    paypal_subscription_id = models.CharField(max_length=255, blank=True, null=True, help_text="PayPal Subscription ID for active subscription.")
    subscription_provider = models.CharField(max_length=10, blank=True, null=True, choices=[('stripe', 'Stripe'), ('paypal', 'PayPal')], help_text="Which payment provider manages the current subscription.")
    paypal_cancel_at = models.DateTimeField(blank=True, null=True, help_text="When the PayPal subscription will expire after cancellation.")
    premium_tier = models.CharField(max_length=50, blank=True, null=True, choices=PREMIUM_TIER_CHOICES, help_text="User's subscription tier.")
    email_preferences = models.JSONField(default=dict, blank=True, help_text="User's email notification preferences")

    USERNAME_FIELD = "email"
    REQUIRED_FIELDS = []

    objects = UserManager()

    def is_premium(self):
        """
        Check if user has an active premium subscription from any provider.

        Returns:
            bool: True if user has an active subscription
        """
        if self.subscription_provider == 'stripe' and self.stripe_customer_id:
            return Subscription.objects.filter(
                customer__id=self.stripe_customer_id,
                stripe_data__status__in=['active', 'past_due']
            ).exists()
        elif self.subscription_provider == 'paypal' and self.paypal_subscription_id:
            if self.paypal_cancel_at and self.paypal_cancel_at < timezone.now():
                return False
            return self.premium_tier is not None
        return False
    
    def get_premium_tier(self):
        """
        Get the human-readable display name for user's premium tier.

        Returns:
            str: Display name (e.g., 'Premium Monthly') or None
        """
        if not self.premium_tier:
            return None
        from users.services.subscription_service import SubscriptionService
        return SubscriptionService.get_tier_display_name(self.premium_tier)

    def update_subscription_status(self, event_type: str = None):
        """
        Update user's subscription status from Stripe data.

        This method delegates to SubscriptionService for the actual logic.
        Maintained for backward compatibility with existing code.

        Args:
            event_type: Optional Stripe event type for logging/notifications
        """
        from users.services.subscription_service import SubscriptionService
        SubscriptionService.update_user_subscription(self, event_type)


class SubscriptionPeriod(models.Model):
    """
    Tracks individual subscription periods for loyalty milestone calculations.

    A new period is created when a subscription activates and closed (ended_at set)
    when the subscription deactivates. Gaps between periods are expected and handled.
    Total accumulated subscription time = sum of all period durations.
    """
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='subscription_periods',
    )
    started_at = models.DateTimeField(help_text="When this subscription period began.")
    ended_at = models.DateTimeField(
        null=True, blank=True,
        help_text="When this period ended. NULL means currently active.",
    )
    provider = models.CharField(
        max_length=10,
        choices=[('stripe', 'Stripe'), ('paypal', 'PayPal')],
        help_text="Which payment provider for this period.",
    )
    notes = models.CharField(
        max_length=255, blank=True, default='',
        help_text="Admin notes (e.g., 'backfilled from launch').",
    )

    class Meta:
        ordering = ['-started_at']
        indexes = [
            models.Index(fields=['user', 'ended_at'], name='subperiod_user_active_idx'),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=['user'],
                condition=models.Q(ended_at__isnull=True),
                name='one_open_period_per_user',
            ),
        ]
        verbose_name = 'Subscription Period'
        verbose_name_plural = 'Subscription Periods'

    def __str__(self):
        end = self.ended_at.strftime('%Y-%m-%d') if self.ended_at else 'active'
        return f"{self.user.email}: {self.started_at.strftime('%Y-%m-%d')} -> {end}"

    @property
    def duration_days(self):
        """Return the number of days in this period (open periods count to now)."""
        if not self.started_at:
            return 0
        end = self.ended_at or timezone.now()
        return (end - self.started_at).days

