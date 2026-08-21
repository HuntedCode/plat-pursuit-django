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
    # THE ROLE SPLIT (2026-08-22): 'admin' is what is_staff used to mean loosely; 'moderator' is
    # the community team, split out so is_staff can go back to meaning exactly "Django admin
    # access". save() keeps is_staff in lockstep (role=='admin' or superuser). Mods currently
    # unlock ONE extra thing (unpublished badge preview) -- the wider mod toolset is a planned
    # rebuild, so no other gate reads this yet. Both roles wear a service mark site-wide.
    role = models.CharField(
        max_length=10, blank=True, default='',
        choices=[('admin', 'Admin'), ('moderator', 'Moderator')],
        help_text="Service role. Admins keep Django-admin access (is_staff syncs to this); "
                  "moderators get the mod mark and mod-level access only.",
    )
    user_timezone = models.CharField(max_length=63, choices=[(tz, tz) for tz in pytz.common_timezones], default='UTC', help_text="User's preferred timezone. UTC default.")
    # The field above cannot answer "did they choose this?" -- it defaults to UTC and is non-null, so a
    # London hunter who never touched it is indistinguishable from one who deliberately picked UTC. That
    # is exactly the population the recap's timezone prompt exists for, hence a separate stamp: null means
    # never confirmed, and only an explicit save sets it.
    timezone_confirmed_at = models.DateTimeField(
        null=True, blank=True,
        help_text="When the user explicitly confirmed or changed their timezone. Null = never asked/answered.",
    )
    default_region = models.CharField(max_length=2, choices=[(r, r) for r in REGIONS], null=True, blank=True, default=None, help_text="User's preferred default region filter for games.")
    use_24hr_clock = models.BooleanField(default=False, help_text="Use 24-hour time format (23:00) instead of 12-hour AM/PM format (11:00 PM)")
    stripe_customer_id = models.CharField(max_length=255, blank=True, null=True, help_text="Stripe Customer ID for this user.")
    paypal_subscription_id = models.CharField(max_length=255, blank=True, null=True, help_text="PayPal Subscription ID for active subscription.")
    subscription_provider = models.CharField(max_length=10, blank=True, null=True, choices=[('stripe', 'Stripe'), ('paypal', 'PayPal')], help_text="Which payment provider manages the current subscription.")
    paypal_cancel_at = models.DateTimeField(blank=True, null=True, help_text="When the PayPal subscription will expire after cancellation.")
    premium_tier = models.CharField(max_length=50, blank=True, null=True, choices=PREMIUM_TIER_CHOICES, help_text="User's subscription tier.")
    email_preferences = models.JSONField(default=dict, blank=True, help_text="User's email notification preferences")
    browse_defaults = models.JSONField(default=dict, blank=True, help_text="Per-page saved filter defaults. Keys: 'games', 'trophies', 'profiles'.")

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
            # The feature-tier check, not bare truthiness: a paid-but-non-feature tier (the
            # retired 'ad_free' was one) must not report premium here when nothing else does.
            from users.services.subscription_service import SubscriptionService
            return SubscriptionService.is_tier_premium(self.premium_tier) if self.premium_tier else False
        return False
    
    @property
    def is_moderator(self):
        return self.role == 'moderator'

    def save(self, *args, **kwargs):
        # The lockstep enforces exactly two directions: an admin role guarantees Django-admin
        # access, and a demotion to moderator cannot leave admin access behind by accident.
        # A bare is_staff with NO role is left alone -- forcing it off would silently demote
        # every user flagged directly (tests, createsuperuser flows, the admin checkbox).
        desired = None
        if self.role == 'admin' and not self.is_staff:
            desired = True
        elif self.role == 'moderator' and self.is_staff and not self.is_superuser:
            desired = False
        elif self.role == '' and self.is_staff and not self.is_superuser and self.pk:
            # Clearing an ADMIN role takes the admin access it granted (one cheap lookup, only
            # for staff-flagged users saving with no role). A user who was never role-admin
            # keeps their directly-set flag.
            old_role = type(self).objects.filter(pk=self.pk).values_list('role', flat=True).first()
            if old_role == 'admin':
                desired = False
        if desired is not None:
            self.is_staff = desired
            update_fields = kwargs.get('update_fields')
            if update_fields is not None and 'is_staff' not in update_fields:
                kwargs['update_fields'] = list(update_fields) + ['is_staff']
        super().save(*args, **kwargs)
        # The service mark follows the role. Imported here to avoid an import cycle at load.
        from users.services.marks import refresh_display_mark
        refresh_display_mark(self)

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
