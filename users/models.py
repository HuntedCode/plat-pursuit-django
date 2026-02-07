from django.db import models
from django.conf import settings
from django.contrib.auth.models import AbstractUser, BaseUserManager
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
    premium_tier = models.CharField(max_length=50, blank=True, null=True, choices=PREMIUM_TIER_CHOICES, help_text="User's subscription tier.")

    USERNAME_FIELD = "email"
    REQUIRED_FIELDS = []

    objects = UserManager()

    class Meta:
        indexes = [
            models.Index(fields=["email"]),
        ]
    
    def is_premium(self):
        """
        Check if user has an active premium subscription.

        Returns:
            bool: True if user has an active subscription in Stripe
        """
        if not self.stripe_customer_id:
            return False
        return Subscription.objects.filter(
            customer__id=self.stripe_customer_id,
            status='active'
        ).exists()
    
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

