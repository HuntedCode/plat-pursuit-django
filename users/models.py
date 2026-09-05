from django.db import models
from django.db.models import Q
from django.conf import settings
from django.contrib.auth.models import AbstractUser, BaseUserManager
from django.utils import timezone
from django.utils.translation import gettext_lazy as _
import pytz
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
        extra_fields.setdefault('role', 'admin')
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
    use_24hr_clock = models.BooleanField(default=False, help_text="Use 24-hour time format (23:00) instead of 12-hour AM/PM format (11:00 PM)")
    stripe_customer_id = models.CharField(max_length=255, blank=True, null=True, help_text="Stripe Customer ID for this user.")
    paypal_subscription_id = models.CharField(max_length=255, blank=True, null=True, help_text="PayPal Subscription ID for active subscription.")
    subscription_provider = models.CharField(max_length=10, blank=True, null=True, choices=[('stripe', 'Stripe'), ('paypal', 'PayPal')], help_text="Which payment provider manages the current subscription.")
    paypal_cancel_at = models.DateTimeField(blank=True, null=True, help_text="When the PayPal subscription will expire after cancellation.")
    premium_tier = models.CharField(max_length=50, blank=True, null=True, choices=PREMIUM_TIER_CHOICES, help_text="User's subscription tier.")
    email_preferences = models.JSONField(default=dict, blank=True, help_text="User's email notification preferences")
    browse_defaults = models.JSONField(default=dict, blank=True, help_text="Per-page saved filter defaults. Keys: 'games', 'trophies', 'profiles'.")
    ui_flags = models.JSONField(default=dict, blank=True, help_text="One-shot UI education flags. Presence of a key means dismissed. Keys: 'career_explainer'.")

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
                stripe_data__status__in=['active', 'trialing', 'past_due']
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

    @property
    def display_name(self):
        """Who this account is, for anywhere a person gets NAMED in the UI.

        PSN handle first, email only when there is no linked profile. Two reasons, and the privacy
        one is the bigger:

        - An email address is personal data, and putting one on a page shows it to everyone who can
          see that page. A staff surface is still a page.
        - It is also just better information. A moderator reading "Hunted47" knows who acted; an
          address they have never seen tells them almost nothing, and two colleagues with similar
          addresses tell them worse than nothing.

        `getattr(..., None)` rather than `hasattr` + attribute: Django's reverse one-to-one raises
        `RelatedObjectDoesNotExist`, which subclasses AttributeError precisely so this works, and a
        user with no linked PSN account is an ordinary case here rather than an error.

        Accessing this in a loop touches `profile`, so any queryset that renders it needs
        `select_related('<user path>__profile')` or it is one query per row.
        """
        profile = getattr(self, 'profile', None)
        return (getattr(profile, 'display_psn_username', '') or getattr(profile, 'psn_username', '')
                or self.email)

    def save(self, *args, **kwargs):
        # The lockstep enforces exactly two directions: an admin role guarantees Django-admin
        # access, and a demotion to moderator cannot leave admin access behind by accident.
        # A bare is_staff with NO role is left alone -- forcing it off would silently demote
        # every user flagged directly (tests, createsuperuser flows, the admin checkbox).
        desired = None
        extra_fields = []
        if self.role == 'admin' and not self.is_staff:
            # Either the role was just granted (drive the flag on), or an admin un-ticked
            # "staff status" on an existing Administrator -- then the CHANGED field wins and
            # the demotion cascades to the role, so the two never sit in disagreement.
            was_admin_with_staff = bool(self.pk) and type(self).objects.filter(
                pk=self.pk, role='admin', is_staff=True).exists()
            if was_admin_with_staff and not self.is_superuser:
                self.role = ''
                extra_fields.append('role')
            else:
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
            extra_fields.append('is_staff')
        update_fields = kwargs.get('update_fields')
        if extra_fields and update_fields is not None:
            kwargs['update_fields'] = list(update_fields) + [
                f for f in extra_fields if f not in update_fields]
        super().save(*args, **kwargs)
        # The service mark follows the role. Imported here to avoid an import cycle at load.
        # Skipped for narrow writes that cannot move the mark (login's last_login save was
        # costing a Profile round-trip on every sign-in).
        MARK_FIELDS = {'role', 'is_staff', 'is_superuser', 'premium_tier'}
        if update_fields is None or MARK_FIELDS & set(update_fields) or extra_fields:
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


class UserRestriction(models.Model):
    """A hunter barred from writing something, for a while or indefinitely.

    FK to `CustomUser`, NOT `Profile`. A restriction is an ACCOUNT fact: hanging it off the profile
    would make unlinking and relinking a PSN account a way to shed it, and the gates that read this
    all have a profile in hand and can reach the user through it.

    WHAT IT IS NOT. It is not `is_active=False`, which kills login and every read; this is a targeted
    write ban that leaves their trophies, badges and leaderboard positions exactly as they were. And
    it hides nothing already published -- an existing quick take stays up unless somebody hides it.
    Both of those are said on the page too, because an admin reaching for "restrict" when they meant
    "hide" is the likely mistake.

    LIFTING NEVER EDITS THIS ROW'S HISTORY. It stamps the lift fields and writes an `AdminAction`
    pointing at the entry that applied it -- the same grammar as reversing a moderation decision, so
    "who lifted this, and why" has an answer.
    """
    SCOPES = [
        ('quick_takes', 'Writing quick takes'),
        ('reports', 'Filing reports and flags'),
        ('all_ugc', 'All user-submitted content'),
    ]

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='restrictions',
        help_text='The account restricted. SET_NULL, not CASCADE -- see `profile` below.',
    )
    profile = models.ForeignKey(
        'trophies.Profile', on_delete=models.SET_NULL, null=True, blank=True,
        related_name='restrictions',
        help_text='The PSN profile behind the account, and the half that actually survives.\n\n'
                  'Both, because neither alone is durable. The FIRST cut keyed on the user only, '
                  'reasoning that a restriction is an account fact and that hanging it off the '
                  'profile would make unlinking PSN an escape hatch. True, and it missed the bigger '
                  'hatch: Settings has a self-service DELETE ACCOUNT, `Profile.user` is SET_NULL, '
                  'and `link_profile_to_user` reattaches THE SAME profile row to a new account. So '
                  'delete, re-register, re-verify the same PSN account, and a CASCADE took every '
                  'restriction with it -- while the trophies, badges, ranking and handle all came '
                  'back. The profile is what persists across that, so the profile is what a '
                  'restriction has to remember.',
    )
    scope = models.CharField(max_length=16, choices=SCOPES)
    reason = models.TextField(help_text='REQUIRED by the service, like every other audited action.')

    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True,
        related_name='restrictions_applied')
    created_by_label = models.CharField(max_length=150, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    expires_at = models.DateTimeField(
        null=True, blank=True,
        help_text='NULL means indefinite. ONE field rather than a boolean plus a duration, because '
                  'two fields describing one fact are two fields that can disagree -- and the '
                  'disagreement would be silent, since nothing reads them together.',
    )

    lifted_at = models.DateTimeField(null=True, blank=True)
    lifted_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='restrictions_lifted')
    lifted_by_label = models.CharField(max_length=150, blank=True)
    lift_reason = models.TextField(blank=True)

    class Meta:
        ordering = ['-created_at', '-id']
        indexes = [
            # The gate's query: every live restriction for one account. It runs on UGC writes, so it
            # reads off this index rather than scanning.
            models.Index(fields=['user', 'lifted_at', 'expires_at'], name='restriction_live_idx'),
            # The same shape for the profile half, because the gate asks about both in one query.
            models.Index(fields=['profile', 'lifted_at', 'expires_at'],
                         name='restriction_live_prof_idx'),
            models.Index(fields=['-created_at', '-id'], name='restriction_recent_idx'),
        ]
        # NO unique constraint on (user, scope). A partial unique on `lifted_at IS NULL` would also
        # cover EXPIRED rows -- which are not lifted, merely lapsed -- and so would refuse to
        # re-restrict somebody who had served a previous one. The service takes a row lock and
        # checks for a LIVE restriction instead, which is the same shape as `_lock_report`.
        constraints = [
            models.CheckConstraint(
                condition=Q(reason__regex=r'\S'),
                name='restriction_reason_is_not_blank',
            ),
        ]

    def __str__(self):
        until = f' until {self.expires_at:%Y-%m-%d}' if self.expires_at else ' indefinitely'
        return f'{self.get_scope_display()}{until} ({self.created_by_label or "unknown"})'

    @property
    def is_live(self):
        """Derived, never stored. A cached boolean would be wrong the moment `expires_at` passed,
        with nothing to notice: expiry happens by the clock, not by anybody writing a row."""
        if self.lifted_at is not None:
            return False
        return self.expires_at is None or self.expires_at > timezone.now()

