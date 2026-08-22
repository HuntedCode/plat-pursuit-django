"""
Subscription service: handles subscription lifecycle for all payment providers.

This service manages:
- Provider-agnostic subscription activation and deactivation
- Stripe-specific product/price mapping and checkout sessions
- Processing Stripe webhook events
- Discord role assignments for premium users
- Double-subscribe guard (only one active sub across providers)
"""
import logging
import stripe
from typing import NamedTuple, Optional, Dict, Tuple
from datetime import datetime, timedelta, timezone as dt_timezone
from django.urls import reverse
from django.utils import timezone
from django.conf import settings
from django.db import IntegrityError, transaction
from djstripe.models import Subscription, Customer, Price
from users.constants import (
    STRIPE_PRODUCTS,
    STRIPE_PRICES,
    PREMIUM_TIER_DISPLAY,
    PREMIUM_DISCORD_ROLE_TIERS,
    SUPPORTER_DISCORD_ROLE_TIERS,
    ACTIVE_PREMIUM_TIERS,
)
from trophies.discord_utils.discord_notifications import send_subscription_notification
from trophies.services.discord_roles import notify_bot_role_earned

logger = logging.getLogger('users.services.subscription')


class MembershipStatus(NamedTuple):
    """The membership page's read of a user's standing -- richer than the boolean
    `has_active_subscription` (which deliberately says (False, None) during Stripe grace so the
    double-subscribe guard lets a cancelled member re-subscribe).

    state: 'active' | 'past_due' | 'grace' | 'none'
    """
    state: str
    provider: Optional[str] = None
    grace_until: Optional[datetime] = None   # set when state == 'grace'
    cancels_at: Optional[datetime] = None    # set when active but a cancel is scheduled
    stripe_sub: Optional[Subscription] = None  # the djstripe row the state was read from


class SubscriptionService:
    """Handles subscription lifecycle for all payment providers."""

    @staticmethod
    def get_tier_from_product_id(product_id: str, is_live: bool = None) -> Optional[str]:
        """
        Map a Stripe product ID to a premium tier.

        Args:
            product_id: Stripe product ID from subscription
            is_live: Whether to check live or test products. If None, checks both.

        Returns:
            str: Premium tier name ('premium_monthly', 'supporter', etc.) or None if not found
        """
        if is_live is None:
            # Check both modes if not specified
            for mode in ['test', 'live']:
                tier = SubscriptionService._find_tier_in_mode(product_id, mode)
                if tier:
                    return tier
            return None

        mode = 'live' if is_live else 'test'
        return SubscriptionService._find_tier_in_mode(product_id, mode)

    @staticmethod
    def _find_tier_in_mode(product_id: str, mode: str) -> Optional[str]:
        """Helper to find tier in a specific mode."""
        products = STRIPE_PRODUCTS.get(mode, {})
        for tier, pid in products.items():
            if pid and pid == product_id:
                return tier
        return None

    @staticmethod
    def get_tier_display_name(tier: str) -> str:
        """
        Get the display name for a premium tier.

        Args:
            tier: Internal tier name (e.g., 'premium_monthly')

        Returns:
            str: Human-readable tier name (e.g., 'Premium Monthly')
        """
        return PREMIUM_TIER_DISPLAY.get(tier, 'Unknown')

    @staticmethod
    def is_tier_premium(tier: str) -> bool:
        """
        Check if a tier grants premium features.

        Every live tier currently does, but this stays a distinct check rather than a truthiness
        test on ``premium_tier``: the retired 'ad_free' tier was a paid tier that granted none, and
        a future non-feature tier would be too.

        Args:
            tier: Premium tier name

        Returns:
            bool: True if tier grants premium features
        """
        return tier in ACTIVE_PREMIUM_TIERS

    # ── Provider-agnostic subscription lifecycle ──────────────────────────

    @staticmethod
    def reconcile_premium(user, *, provider_hint: str = None) -> bool:
        """THE one premium truth-writer. Activation and deactivation both converge here instead
        of each carrying its own copy of "what makes this user premium" -- so a second premium
        source, if one ever exists again, gets added to the truth expression below and nowhere
        else. (Gift grants briefly were that second source; they were cut in Aug 2026 because a
        giftable supporter mark dilutes what every paying supporter's mark means.)

        Truth: a feature-granting `premium_tier`.

        Periods: with a `provider_hint`, a source just activated for that provider -- ensure an open
        period exists, reopening one closed within 14 days for the SAME provider (Stripe's retry
        window; the reopen keeps milestone tenure honest across payment recovery). With no hint and
        no premium, close everything open. With no hint and premium still true, TOUCH NOTHING --
        that is what protects the surviving source's period.

        Must run inside the caller's transaction. Returns the computed truth.

        NOTE the one deliberate exception: the `past_due` path in `update_user_subscription` keeps
        premium while closing the period (tenure pauses during failed payment). Reconcile would
        refuse to close a premium user's period, so that path stays direct -- see the comment there.
        """
        is_premium = (
            user.premium_tier is not None and SubscriptionService.is_tier_premium(user.premium_tier)
        )

        if hasattr(user, 'profile'):
            user.profile.update_profile_premium(is_premium)
            # update_profile_premium refreshes the worn mark itself (users/services/marks.py);
            # nothing further to write here -- the denorm has exactly two writers, this path
            # (through the profile) and CustomUser.save on role changes.

        from users.models import SubscriptionPeriod
        if provider_hint is not None and is_premium:
            open_period = SubscriptionPeriod.objects.filter(
                user=user, ended_at__isnull=True
            ).exists()
            if not open_period:
                recent_threshold = timezone.now() - timedelta(days=14)
                recent_closed = SubscriptionPeriod.objects.filter(
                    user=user, provider=provider_hint, ended_at__isnull=False,
                    ended_at__gte=recent_threshold,
                ).order_by('-ended_at').first()
                if recent_closed:
                    recent_closed.ended_at = None
                    recent_closed.save(update_fields=['ended_at'])
                else:
                    try:
                        SubscriptionPeriod.objects.create(
                            user=user,
                            started_at=timezone.now(),
                            provider=provider_hint,
                        )
                    except IntegrityError:
                        # Concurrent webhook won the race against `one_open_period_per_user`
                        # between our exists() and this insert. A period is open, which is all
                        # this branch wanted -- not worth a 500 and a provider retry.
                        logger.info(f"Open period already created concurrently for {user.email}")
        elif provider_hint is None and not is_premium:
            SubscriptionPeriod.objects.filter(
                user=user, ended_at__isnull=True
            ).update(ended_at=timezone.now())
        # provider_hint None + still premium: deliberately nothing.

        return is_premium

    @staticmethod
    def activate_subscription(user, tier: str, provider: str, event_type: str = None) -> bool:
        """
        Activate a subscription for a user, regardless of payment provider.

        Called by both Stripe and PayPal webhook handlers when a subscription
        becomes active. Sets premium_tier, subscription_provider, updates
        profile premium status, and handles Discord notifications/roles.

        Args:
            user: CustomUser instance
            tier: Subscription tier name ('premium_monthly', 'supporter', etc.)
            provider: 'stripe' or 'paypal'
            event_type: Original webhook event type (for Discord notification logic)

        Returns:
            bool: True if tier grants premium features
        """
        user.premium_tier = tier
        user.subscription_provider = provider
        is_premium = SubscriptionService.is_tier_premium(tier)

        update_fields = ['premium_tier', 'subscription_provider']
        if provider == 'paypal':
            user.paypal_cancel_at = None  # Clear any previous cancellation
            update_fields += ['paypal_cancel_at', 'paypal_subscription_id']

        with transaction.atomic():
            user.save(update_fields=update_fields)
            # Denorm + period management converge on the one truth-writer. The hint is only passed
            # when this tier actually grants features -- a non-feature tier activating must not
            # open a period, and reconcile's no-hint path handles the denorm either way.
            SubscriptionService.reconcile_premium(
                user, provider_hint=provider if is_premium else None
            )

        # Discord notification embed for new subscriptions only (side effects after commit)
        activation_events = [
            'customer.subscription.created',       # Stripe
            'BILLING.SUBSCRIPTION.ACTIVATED',       # PayPal
        ]
        if hasattr(user, 'profile') and event_type in activation_events and is_premium:
            send_subscription_notification(user)

        # Idempotent role assignment: re-apply on every activation (including renewals)
        # so roles self-heal if the user rejoined the server or the bot had an outage.
        # Deferred via on_commit to avoid blocking the webhook response with HTTP calls.
        if hasattr(user, 'profile') and is_premium and user.profile.is_discord_verified and user.profile.discord_id:
            profile = user.profile
            if user.premium_tier in PREMIUM_DISCORD_ROLE_TIERS and settings.DISCORD_PREMIUM_ROLE:
                role_id = settings.DISCORD_PREMIUM_ROLE
                transaction.on_commit(lambda p=profile, r=role_id: notify_bot_role_earned(p, r))
            elif user.premium_tier in SUPPORTER_DISCORD_ROLE_TIERS and settings.DISCORD_PREMIUM_PLUS_ROLE:
                role_id = settings.DISCORD_PREMIUM_PLUS_ROLE
                transaction.on_commit(lambda p=profile, r=role_id: notify_bot_role_earned(p, r))

        # Welcome email for new subscriptions (not upgrades/recoveries)
        if hasattr(user, 'profile') and event_type in activation_events and is_premium:
            tier_name = SubscriptionService.get_tier_display_name(tier)
            SubscriptionService._send_subscription_welcome_email(user, tier_name)

        # (Premium tenure recognition now lives in the milestones app's premium_months ladder,
        # recomputed by its nightly sweep -- the legacy is_premium/subscription_months milestones
        # retired with the legacy engine.)

        return is_premium

    @staticmethod
    def deactivate_subscription(user, provider: str, event_type: str = None) -> None:
        """
        Deactivate a subscription for a user.

        Called by both Stripe and PayPal when a subscription actually ends
        (Stripe deleted, PayPal EXPIRED/SUSPENDED).

        Args:
            user: CustomUser instance
            provider: 'stripe' or 'paypal'
            event_type: Original webhook event type for logging
        """
        # Capture tier before clearing for cancellation email
        original_tier = user.premium_tier

        user.premium_tier = None
        user.subscription_provider = None
        update_fields = ['premium_tier', 'subscription_provider']
        if provider == 'paypal':
            user.paypal_subscription_id = None
            user.paypal_cancel_at = None
            update_fields += ['paypal_subscription_id', 'paypal_cancel_at']

        with transaction.atomic():
            user.save(update_fields=update_fields)
            # The truth-writer, not an unconditional flip. (A post_save signal on Profile handles
            # cascading side effects of a real premium transition, e.g. deactivating premium-only
            # showcases.)
            SubscriptionService.reconcile_premium(user)

        logger.info(f"Deactivated {provider} subscription for user {user.email} ({event_type})")

        # Side effects after commit: Discord role removal (only the role matching the user's tier)
        # Deferred via on_commit to avoid blocking the webhook response with HTTP calls.
        if hasattr(user, 'profile') and user.profile.is_discord_verified and user.profile.discord_id:
            from trophies.services.discord_roles import notify_bot_role_removed
            profile = user.profile
            if original_tier in PREMIUM_DISCORD_ROLE_TIERS and settings.DISCORD_PREMIUM_ROLE:
                role_id = settings.DISCORD_PREMIUM_ROLE
                transaction.on_commit(lambda p=profile, r=role_id: notify_bot_role_removed(p, r))
            elif original_tier in SUPPORTER_DISCORD_ROLE_TIERS and settings.DISCORD_PREMIUM_PLUS_ROLE:
                role_id = settings.DISCORD_PREMIUM_PLUS_ROLE
                transaction.on_commit(lambda p=profile, r=role_id: notify_bot_role_removed(p, r))

        # Send cancellation email and notification for voluntary cancellations.
        # Payment failures (SUSPENDED) are handled separately by handle_payment_failed
        # and the PayPal SUSPENDED handler in paypal_service.py.
        cancellation_events = [
            'customer.subscription.deleted',          # Stripe
            'BILLING.SUBSCRIPTION.EXPIRED',           # PayPal
        ]
        if event_type in cancellation_events:
            tier_name = SubscriptionService.get_tier_display_name(original_tier) if original_tier else 'Premium'
            SubscriptionService._send_subscription_cancelled_email(user, tier_name)

            # In-app notification
            try:
                from notifications.services.notification_service import NotificationService
                NotificationService.create_notification(
                    recipient=user,
                    notification_type='subscription_updated',
                    title="Your subscription has ended",
                    message="Your premium subscription has expired. Thank you for your support! You can resubscribe anytime.",
                    action_url='/support/',
                    action_text='Resubscribe',
                    priority='normal',
                    metadata={'previous_tier': original_tier},
                )
            except Exception:
                logger.exception(f"Failed to create cancellation notification for {user.email}")

    @staticmethod
    def mark_subscription_cancelling(user, cancel_at: Optional[datetime] = None) -> None:
        """
        Mark a PayPal subscription as cancelling (user cancelled but still has paid time).

        Premium is NOT removed here. The EXPIRED webhook will handle that.

        Args:
            user: CustomUser instance
            cancel_at: When the subscription will actually expire
        """
        user.paypal_cancel_at = cancel_at
        with transaction.atomic():
            user.save(update_fields=['paypal_cancel_at'])

    @staticmethod
    def has_active_subscription(user) -> Tuple[bool, Optional[str]]:
        """
        Check if user has an active subscription from ANY provider.

        Used as a double-subscribe guard to prevent users from subscribing
        through multiple providers simultaneously.

        Returns:
            tuple: (has_active, provider_name) e.g. (True, 'stripe') or (False, None)
        """
        # Check Stripe (include past_due to prevent double-subscribe during retry)
        if user.stripe_customer_id:
            active_stripe = Subscription.objects.filter(
                customer__id=user.stripe_customer_id,
                stripe_data__status__in=['active', 'trialing', 'past_due']
            ).exists()
            if active_stripe:
                return (True, 'stripe')

        # Check PayPal (trust our stored state, set by webhooks).
        # Must mirror is_premium() logic: respect paypal_cancel_at expiry.
        if user.paypal_subscription_id and user.premium_tier and user.subscription_provider == 'paypal':
            if user.paypal_cancel_at and user.paypal_cancel_at < timezone.now():
                return (False, None)
            return (True, 'paypal')

        return (False, None)

    @staticmethod
    def membership_status(user) -> MembershipStatus:
        """The membership page's state read: active / past_due / grace / none, read-only.

        Mirrors `update_user_subscription`'s truth without writing anything. The crucial extra
        over `has_active_subscription` is GRACE: a cancelled Stripe sub with paid time left keeps
        premium (see the canceled branch there), but the boolean helper reports (False, None) --
        correct for the double-subscribe guard, wrong for a page that would tell a paying member
        they have "no active subscription".
        """
        if user.stripe_customer_id:
            sub = Subscription.objects.filter(
                customer__id=user.stripe_customer_id,
                stripe_data__status__in=['active', 'trialing'],
            ).first()
            if sub:
                data = sub.stripe_data or {}
                cancels_at = None
                if data.get('cancel_at_period_end') or data.get('cancel_at'):
                    # Portal cancels leave the sub 'active' with cancel_at_period_end; a cancel
                    # scheduled for a specific date sets cancel_at ALONE. Either way the end date
                    # is real information. `dt_timezone.utc`, never django.utils.timezone.utc
                    # (removed in Django 5.0).
                    end_ts = data.get('cancel_at') or data.get('current_period_end')
                    if end_ts:
                        cancels_at = datetime.fromtimestamp(end_ts, tz=dt_timezone.utc)
                return MembershipStatus('active', 'stripe', cancels_at=cancels_at, stripe_sub=sub)

        # The PayPal-subscriber guard, same reasoning as update_user_subscription's: for a
        # provider='paypal' user, STALE Stripe rows (the past_due/canceled sub they left behind
        # before re-subscribing via PayPal) must never claim the page. Only an ACTIVE Stripe sub
        # (checked above) outranks the PayPal read.
        if user.paypal_subscription_id and user.premium_tier and user.subscription_provider == 'paypal':
            if user.paypal_cancel_at:
                if user.paypal_cancel_at > timezone.now():
                    return MembershipStatus('grace', 'paypal', grace_until=user.paypal_cancel_at)
                return MembershipStatus('none')
            return MembershipStatus('active', 'paypal')

        if user.stripe_customer_id:
            past_due = Subscription.objects.filter(
                customer__id=user.stripe_customer_id, stripe_data__status='past_due'
            ).first()
            if past_due:
                return MembershipStatus('past_due', 'stripe', stripe_sub=past_due)

            if user.premium_tier and SubscriptionService.is_tier_premium(user.premium_tier):
                canceled = Subscription.objects.filter(
                    customer__id=user.stripe_customer_id, stripe_data__status='canceled'
                ).first()
                if canceled:
                    end_ts = (canceled.stripe_data or {}).get('current_period_end')
                    if end_ts:
                        until = datetime.fromtimestamp(end_ts, tz=dt_timezone.utc)
                        if until > timezone.now():
                            return MembershipStatus('grace', 'stripe', grace_until=until,
                                                    stripe_sub=canceled)

        return MembershipStatus('none')

    @staticmethod
    def premium_tenure(user) -> Dict:
        """Member-since and total supported time, from SubscriptionPeriod (the only tenure data on
        the site). One values_list pass; bounded per user (a handful of periods). The milestones
        metric `premium_months` delegates here -- one implementation, pinned by a parity test."""
        from users.models import SubscriptionPeriod

        now = timezone.now()
        member_since = None
        current_started = None
        total_days = 0
        for started, ended in SubscriptionPeriod.objects.filter(user=user).values_list(
                'started_at', 'ended_at'):
            if not started:
                continue
            total_days += max(((ended or now) - started).days, 0)
            if member_since is None or started < member_since:
                member_since = started
            if ended is None:
                current_started = started
        return {
            'member_since': member_since,
            'current_started': current_started,
            'total_days': total_days,
            'total_months': int(total_days // 30),
        }

    @staticmethod
    def describe_billing(user, membership: MembershipStatus) -> Dict:
        """What the member pays and how often: {'amount': int|None dollars, 'cycle':
        'month'|'year'|None}. Best-effort display data -- never guessed, omitted when unknown.
        """
        amount = None
        cycle = None

        if membership.provider == 'stripe' and membership.stripe_sub is not None:
            plan = (membership.stripe_sub.stripe_data or {}).get('plan') or {}
            if plan.get('amount'):
                # Legacy Stripe prices are not whole-dollar-guaranteed; never floor a member's
                # real price ($4.99 must not read as $4).
                cents = plan['amount']
                amount = cents // 100 if cents % 100 == 0 else f"{cents / 100:.2f}"
            cycle = plan.get('interval') or None

        elif membership.provider == 'paypal':
            from users.services.paypal_service import PayPalService
            from users.constants import PAYPAL_LADDER_PLANS, SUPPORT_TIERS

            snapshot = PayPalService.get_cached_subscription_snapshot(user.paypal_subscription_id)
            plan_id = (snapshot or {}).get('plan_id')
            if plan_id:
                mode = 'live' if settings.PAYPAL_MODE == 'live' else 'sandbox'
                for slug, intervals in PAYPAL_LADDER_PLANS.get(mode, {}).items():
                    for interval, pid in intervals.items():
                        if pid == plan_id:
                            cycle = 'month' if interval == 'monthly' else 'year'
                            tier = next((t for t in SUPPORT_TIERS if t['slug'] == slug), None)
                            if tier:
                                amount = tier['monthly'] if cycle == 'month' else tier['yearly']
                            break
                    if cycle:
                        break
            if cycle is None:
                # Legacy PayPal tiers: the cycle is knowable from the tier, the dollar figure
                # lives only on the processor -- never guess it.
                cycle = {'premium_monthly': 'month', 'premium_yearly': 'year',
                         'supporter': 'month'}.get(user.premium_tier)

        return {'amount': amount, 'cycle': cycle}

    # ── Stripe-specific methods ──────────────────────────────────────────

    @staticmethod
    def update_user_subscription(user, event_type: str = None) -> bool:
        """
        Update user's subscription status based on Stripe data.

        This Stripe-specific method:
        1. Checks for active Stripe subscriptions via djstripe
        2. Maps product ID to premium tier
        3. Delegates to activate_subscription() or deactivate_subscription()
        4. Handles Stripe grace period for cancelled subscriptions

        Args:
            user: CustomUser instance to update
            event_type: Optional Stripe event type (e.g., 'customer.subscription.created')

        Returns:
            bool: True if user has active premium subscription
        """
        # A Stripe event must never end a PAYPAL subscriber's premium. `stripe_customer_id` is
        # kept forever, so somebody who once paid via Stripe and now pays via PayPal still routes
        # here on a late event for the long-dead Stripe subscription -- and every fall-through
        # below is a deactivation. Only proceed for such a user when an ACTIVE Stripe sub exists
        # (a genuine provider switch); otherwise the event is stale by definition.
        if user.subscription_provider == 'paypal' and user.paypal_subscription_id:
            stripe_active = user.stripe_customer_id and Subscription.objects.filter(
                customer__id=user.stripe_customer_id, stripe_data__status='active'
            ).exists()
            if not stripe_active:
                logger.info(f"Stripe event {event_type} ignored for PayPal subscriber {user.email}")
                return SubscriptionService.is_tier_premium(user.premium_tier) if user.premium_tier else False

        if not user.stripe_customer_id:
            SubscriptionService.deactivate_subscription(user, 'stripe', event_type)
            return False

        # Find a premium-granting subscription. `trialing` counts: Stripe grants access during a
        # trial, and before this it fell through EVERY branch below into deactivate_subscription
        # -- the audit command's repoint arm handed trialing rescues straight to that cliff.
        active_sub = Subscription.objects.filter(
            customer__id=user.stripe_customer_id,
            stripe_data__status__in=['active', 'trialing']
        ).first()

        if active_sub:
            # Map product ID to tier via stripe_data JSON
            stripe_data = active_sub.stripe_data or {}
            plan = stripe_data.get('plan', {})
            product_id = plan.get('product')
            tier = SubscriptionService.get_tier_from_product_id(product_id)

            if not tier:
                # Belt for the product-map gap: the subscription is demonstrably live, so before
                # the revoke arm, try recovering the tier from the PRICE id against the ladder
                # maps. This is what saves a paying subscriber when a bootstrap paste missed the
                # STRIPE_PRODUCTS block (it happened: the first paste block only printed prices).
                tier = SubscriptionService.resolve_tier_from_ladder_price(plan.get('id'))

            if tier:
                return SubscriptionService.activate_subscription(user, tier, 'stripe', event_type)
            else:
                logger.warning(f"Unknown product ID {product_id} for user {user.email}")
                SubscriptionService.deactivate_subscription(user, 'stripe', event_type)
                return False
        else:
            # Check for past_due (payment failing, Stripe still retrying).
            # Keep premium features active but close SubscriptionPeriod
            # to stop milestone time accumulation during unpaid window.
            # DELIBERATELY DIRECT, not through reconcile_premium: this is the one state where
            # premium stays TRUE while the period closes (tenure pauses during failed payment),
            # and reconcile refuses to close a premium user's period by design.
            past_due_sub = Subscription.objects.filter(
                customer__id=user.stripe_customer_id,
                stripe_data__status='past_due'
            ).first()

            if past_due_sub:
                with transaction.atomic():
                    from users.models import SubscriptionPeriod
                    SubscriptionPeriod.objects.filter(
                        user=user, ended_at__isnull=True
                    ).update(ended_at=timezone.now())
                logger.info(f"Subscription past_due for {user.email}: period paused, premium retained")
                return SubscriptionService.is_tier_premium(user.premium_tier) if user.premium_tier else False

            # Check for unpaid (Stripe exhausted retries, configured to leave as unpaid)
            unpaid_sub = Subscription.objects.filter(
                customer__id=user.stripe_customer_id,
                stripe_data__status='unpaid'
            ).first()

            if unpaid_sub:
                SubscriptionService.deactivate_subscription(user, 'stripe', event_type)
                return False

            # Check if subscription is canceled but still in grace period
            canceled_sub = Subscription.objects.filter(
                customer__id=user.stripe_customer_id,
                stripe_data__status='canceled'
            ).first()

            if canceled_sub:
                canceled_data = canceled_sub.stripe_data or {}
                period_end_ts = canceled_data.get('current_period_end')
                # dt_timezone.utc, NOT timezone.utc: `timezone` is django.utils.timezone, whose
                # `utc` alias was removed in Django 5.0 -- this line raised AttributeError for
                # every grace-period check since the 5.x upgrade (same bug class as the one fixed
                # on the management page view).
                if period_end_ts and datetime.fromtimestamp(period_end_ts, tz=dt_timezone.utc) > timezone.now():
                    # Still in grace period, keep premium active
                    return SubscriptionService.is_tier_premium(user.premium_tier) if user.premium_tier else False

            SubscriptionService.deactivate_subscription(user, 'stripe', event_type)
            return False

    @staticmethod
    def resolve_tier_from_ladder_price(price_id):
        """Reverse of `resolve_ladder_price_id`: a Stripe PRICE id back to its ladder slug, both
        modes. The webhook fallback when product-id recovery misses -- an active subscription on a
        ladder price is a paying supporter regardless of what the product map knows."""
        if not price_id:
            return None
        from users.constants import STRIPE_LADDER_PRICES
        for mode_map in STRIPE_LADDER_PRICES.values():
            for slug, intervals in mode_map.items():
                if price_id in (intervals.get('monthly'), intervals.get('yearly')):
                    return slug
        return None

    @staticmethod
    def resolve_ladder_price_id(tier: str, interval: str, is_live: bool):
        """Ladder (slug, interval) -> Stripe price id, or None when unconfigured.

        Deliberately SEPARATE from `get_price_ids`/`get_prices_from_stripe`: those raise on one
        missing id and their caller degrades EVERYTHING on a miss, which is the right shape for the
        three legacy tiers that must all exist together and the wrong shape for a ladder that fills
        in one bootstrap run at a time.
        """
        from users.constants import STRIPE_LADDER_PRICES
        mode = 'live' if is_live else 'test'
        return (STRIPE_LADDER_PRICES.get(mode, {}).get(tier) or {}).get(interval) or None

    @staticmethod
    def get_price_ids(is_live: bool) -> Dict[str, str]:
        """
        Get Stripe price IDs for the current mode.

        Args:
            is_live: True for live mode, False for test mode

        Returns:
            dict: Mapping of tier names to price IDs
        """
        mode = 'live' if is_live else 'test'
        return STRIPE_PRICES.get(mode, {})

    @staticmethod
    def get_prices_from_stripe(is_live: bool) -> Dict[str, Price]:
        """
        Fetch Price objects from djstripe for all tiers.

        Args:
            is_live: True for live mode, False for test mode

        Returns:
            dict: Mapping of tier names to djstripe Price objects

        Raises:
            Price.DoesNotExist: If any price is not found
        """
        price_ids = SubscriptionService.get_price_ids(is_live)
        prices = {}

        for tier, price_id in price_ids.items():
            prices[tier] = Price.objects.get(id=price_id)

        return prices

    @staticmethod
    def create_checkout_session(user, tier: str, success_url: str, cancel_url: str,
                                interval: str = 'monthly') -> str:
        """
        Create a Stripe checkout session for a subscription.

        Args:
            user: CustomUser instance
            tier: Subscription tier ('premium_monthly', 'supporter', etc.)
            success_url: URL to redirect to after successful payment
            cancel_url: URL to redirect to if payment is canceled

        Returns:
            str: Stripe checkout session URL

        Raises:
            ValueError: If tier is invalid or price not found
            stripe.error.StripeError: If Stripe API call fails
        """
        is_live = settings.STRIPE_MODE == 'live'

        from users.constants import LADDER_SLUGS
        if tier in LADDER_SLUGS:
            # Ladder branch: (slug, interval) -> its own price. The legacy branch below is untouched
            # so the three grandfathered tiers keep renewing forever; they simply are not offered.
            price_id = SubscriptionService.resolve_ladder_price_id(tier, interval, is_live)
            if not price_id:
                raise ValueError(f"Ladder tier not configured: {tier}/{interval}")
            price = Price.objects.get(id=price_id)
        else:
            prices = SubscriptionService.get_prices_from_stripe(is_live)
            if tier not in prices:
                raise ValueError(f"Invalid tier: {tier}")
            price = prices[tier]

        # Get or create Stripe customer
        customer, created = Customer.get_or_create(subscriber=user)
        if created:
            customer.email = user.email
            customer.save()

        # Update user's stored customer ID
        user.stripe_customer_id = customer.id
        user.save(update_fields=['stripe_customer_id'])

        # Create checkout session
        session = stripe.checkout.Session.create(
            customer=customer.id,
            payment_method_types=['card', 'us_bank_account', 'amazon_pay', 'cashapp', 'link'],
            line_items=[{'price': price.id, 'quantity': 1}],
            mode='subscription',
            success_url=success_url,
            cancel_url=cancel_url,
            metadata={'tier': tier, 'interval': interval},
        )

        return session.url

    # ── Payment failure and cancellation notifications ───────────────────

    @staticmethod
    def _send_payment_failed_email(user, is_final_warning: bool, triggered_by: str = 'webhook') -> bool:
        """
        Send payment failure email via EmailService.

        Args:
            user: CustomUser instance
            is_final_warning: True for final attempt (premium at risk), False for first warning
            triggered_by: Origin of the email ('webhook', 'admin_manual', etc.)

        Returns:
            bool: True if email was sent successfully
        """
        from users.services.email_preference_service import EmailPreferenceService
        from core.services.email_service import EmailService

        email_type = 'payment_failed_final' if is_final_warning else 'payment_failed'
        subject = (
            "Action Required: Your PlatPursuit subscription is at risk"
            if is_final_warning
            else "Heads up: We couldn't process your payment"
        )

        if not EmailPreferenceService.should_send_email(user, 'subscription_notifications'):
            logger.info(f"Skipping payment failed email for {user.email}: preference disabled")
            EmailService.log_suppressed(email_type, user, subject, triggered_by)
            return False

        # Generate billing portal URL for Stripe users, fallback to management page
        portal_url = f"{settings.SITE_URL}{reverse('subscription_management')}"
        if user.stripe_customer_id:
            try:
                portal_session = stripe.billing_portal.Session.create(
                    customer=user.stripe_customer_id,
                    return_url=f"{settings.SITE_URL}{reverse('subscription_management')}",
                )
                portal_url = portal_session.url
            except stripe.error.StripeError:
                logger.exception("Failed to create billing portal session for payment failed email")

        tier_name = SubscriptionService.get_tier_display_name(user.premium_tier) if user.premium_tier else 'Premium'
        username = user.profile.psn_username if hasattr(user, 'profile') else user.email.split('@')[0]

        preference_token = EmailPreferenceService.generate_preference_token(user.id)

        from users.constants import PREMIUM_PERKS
        context = {
            'username': username,
            'is_final_warning': is_final_warning,
            'portal_url': portal_url,
            'tier_name': tier_name,
            'site_url': settings.SITE_URL,
            'preference_url': f"{settings.SITE_URL}/users/email-preferences/?token={preference_token}",
            # From the shared constant: the hand-written what-you-lose list threatened retired
            # perks (themes, premium checklists) on a dunning email.
            'premium_perks': PREMIUM_PERKS,
        }

        try:
            sent = EmailService.send_html_email(
                subject=subject,
                to_emails=[user.email],
                template_name='emails/payment_failed.html',
                context=context,
                log_email_type=email_type,
                log_user=user,
                log_triggered_by=triggered_by,
                log_metadata={'is_final_warning': is_final_warning},
            )
            if sent:
                logger.info(f"Sent payment failed email to {user.email} (final={is_final_warning})")
            return sent > 0
        except Exception:
            logger.exception(f"Failed to send payment failed email to {user.email}")
            return False

    @staticmethod
    def _send_payment_failed_notification(
        user, attempt_count: int, is_final: bool,
        next_retry_at=None, triggered_by: str = 'webhook',
    ) -> None:
        """
        Send in-app notification for payment failure.

        Args:
            user: CustomUser instance
            attempt_count: Which payment attempt failed
            is_final: True if Stripe has given up retrying
            next_retry_at: Unix timestamp of next Stripe retry (or None)
            triggered_by: Origin ('webhook', 'admin_manual', etc.)
        """
        from notifications.services.notification_service import NotificationService

        if is_final:
            title = "Payment failed: subscription at risk"
            message = (
                "We were unable to process your payment after multiple attempts. "
                "Please update your payment method to keep your premium features."
            )
            priority = 'urgent'
        else:
            title = "Payment issue with your subscription"
            message = (
                f"We couldn't process your latest payment (attempt {attempt_count}). "
                "We'll retry automatically, but you may want to check your payment method."
            )
            priority = 'high'

        try:
            NotificationService.create_notification(
                recipient=user,
                notification_type='payment_failed',
                title=title,
                message=message,
                action_url=reverse('subscription_management'),
                action_text='Manage Subscription',
                icon='💳',
                priority=priority,
                metadata={
                    'attempt_count': attempt_count,
                    'is_final': is_final,
                    'next_retry_at': next_retry_at,
                    'triggered_by': triggered_by,
                },
            )
        except Exception:
            logger.exception(f"Failed to create payment failed notification for {user.email}")

    @staticmethod
    def _send_subscription_cancelled_email(user, tier_name: str, triggered_by: str = 'webhook') -> bool:
        """
        Send farewell email when a subscription ends.

        Args:
            user: CustomUser instance
            tier_name: Display name of the tier that just ended
            triggered_by: Origin of the email ('webhook', 'admin_manual', etc.)

        Returns:
            bool: True if email was sent successfully
        """
        from users.services.email_preference_service import EmailPreferenceService
        from core.services.email_service import EmailService

        subject = "We're sorry to see you go"

        if not EmailPreferenceService.should_send_email(user, 'subscription_notifications'):
            logger.info(f"Skipping cancellation email for {user.email}: preference disabled")
            EmailService.log_suppressed('subscription_cancelled', user, subject, triggered_by)
            return False

        username = user.profile.psn_username if hasattr(user, 'profile') else user.email.split('@')[0]
        preference_token = EmailPreferenceService.generate_preference_token(user.id)

        from users.constants import PREMIUM_PERKS
        context = {
            'username': username,
            'tier_name': tier_name,
            'subscribe_url': f"{settings.SITE_URL}/support/",
            'site_url': settings.SITE_URL,
            'preference_url': f"{settings.SITE_URL}/users/email-preferences/?token={preference_token}",
            'premium_perks': PREMIUM_PERKS,
        }

        try:
            sent = EmailService.send_html_email(
                subject=subject,
                to_emails=[user.email],
                template_name='emails/subscription_cancelled.html',
                context=context,
                log_email_type='subscription_cancelled',
                log_user=user,
                log_triggered_by=triggered_by,
            )
            if sent:
                logger.info(f"Sent subscription cancelled email to {user.email}")
            return sent > 0
        except Exception:
            logger.exception(f"Failed to send cancellation email to {user.email}")
            return False

    @staticmethod
    def handle_payment_failed(user, invoice_data: dict) -> None:
        """
        Handle a Stripe invoice.payment_failed event.

        Sends in-app notifications on every attempt and emails on first
        failure and final warning only. Stores Stripe's next retry timestamp
        in notification metadata for the admin dashboard.

        Args:
            user: CustomUser instance
            invoice_data: Stripe Invoice object data
        """
        attempt_count = invoice_data.get('attempt_count', 1)
        next_attempt = invoice_data.get('next_payment_attempt')
        is_first = (attempt_count == 1)
        is_final = (next_attempt is None and attempt_count > 1)

        logger.info(
            f"Payment failed for {user.email}: attempt {attempt_count}, "
            f"next_attempt={'none' if next_attempt is None else 'scheduled'}"
        )

        # In-app notification on every attempt (includes next retry timestamp for dashboard)
        SubscriptionService._send_payment_failed_notification(
            user, attempt_count, is_final, next_retry_at=next_attempt,
        )

        # Email only on first failure or final warning
        if is_first or is_final:
            SubscriptionService._send_payment_failed_email(user, is_final)

    # ── Payment action required (3D Secure / SCA) ───────────────────────

    @staticmethod
    def handle_payment_action_required(user, invoice_data: dict) -> None:
        """
        Handle a Stripe invoice.payment_action_required event.

        Fires when a payment needs customer authentication (3D Secure / SCA).
        Sends a single notification and email directing the user to complete
        verification. No subscription status change: premium stays active.

        Args:
            user: CustomUser instance
            invoice_data: Stripe Invoice object data
        """
        invoice_url = invoice_data.get('hosted_invoice_url', '')
        if not invoice_url:
            logger.warning(f"No hosted_invoice_url in payment_action_required event for {user.email}")
            invoice_url = f"{settings.SITE_URL}{reverse('subscription_management')}"

        logger.info(f"Payment action required for {user.email}: invoice_url={invoice_url}")

        SubscriptionService._send_payment_action_required_notification(user, invoice_url)
        SubscriptionService._send_payment_action_required_email(user, invoice_url)

    @staticmethod
    def _send_payment_action_required_notification(
        user, invoice_url: str, triggered_by: str = 'webhook',
    ) -> None:
        """
        Send in-app notification when a payment requires customer authentication.

        Args:
            user: CustomUser instance
            invoice_url: Stripe hosted invoice URL for completing 3D Secure
            triggered_by: Origin ('webhook', 'admin_manual', etc.)
        """
        from notifications.services.notification_service import NotificationService

        try:
            NotificationService.create_notification(
                recipient=user,
                notification_type='payment_action_required',
                title="Payment verification needed",
                message=(
                    "Your bank requires an extra verification step to process "
                    "your latest subscription payment. This usually takes less "
                    "than a minute."
                ),
                action_url=reverse('subscription_management'),
                action_text='Manage Subscription',
                icon='\U0001f510',
                priority='normal',
                metadata={
                    'invoice_url': invoice_url,
                    'triggered_by': triggered_by,
                },
            )
        except Exception:
            logger.exception(f"Failed to create payment action required notification for {user.email}")

    @staticmethod
    def _send_payment_action_required_email(
        user, invoice_url: str, triggered_by: str = 'webhook',
    ) -> bool:
        """
        Send email when a payment requires customer authentication (3D Secure / SCA).

        Args:
            user: CustomUser instance
            invoice_url: Stripe hosted invoice URL where the customer completes authentication
            triggered_by: Origin of the email ('webhook', 'admin_manual', etc.)

        Returns:
            bool: True if email was sent successfully
        """
        from users.services.email_preference_service import EmailPreferenceService
        from core.services.email_service import EmailService

        subject = "Quick step needed to complete your payment"

        if not EmailPreferenceService.should_send_email(user, 'subscription_notifications'):
            logger.info(f"Skipping payment action required email for {user.email}: preference disabled")
            EmailService.log_suppressed('payment_action_required', user, subject, triggered_by)
            return False

        tier_name = SubscriptionService.get_tier_display_name(user.premium_tier) if user.premium_tier else 'Premium'
        username = user.profile.psn_username if hasattr(user, 'profile') else user.email.split('@')[0]
        preference_token = EmailPreferenceService.generate_preference_token(user.id)

        context = {
            'username': username,
            'invoice_url': invoice_url,
            'tier_name': tier_name,
            'site_url': settings.SITE_URL,
            'preference_url': f"{settings.SITE_URL}/users/email-preferences/?token={preference_token}",
        }

        try:
            sent = EmailService.send_html_email(
                subject=subject,
                to_emails=[user.email],
                template_name='emails/payment_action_required.html',
                context=context,
                log_email_type='payment_action_required',
                log_user=user,
                log_triggered_by=triggered_by,
                log_metadata={'invoice_url': invoice_url},
            )
            if sent:
                logger.info(f"Sent payment action required email to {user.email}")
            return sent > 0
        except Exception:
            logger.exception(f"Failed to send payment action required email to {user.email}")
            return False

    # ── Positive lifecycle emails ─────────────────────────────────────────

    @staticmethod
    def _send_subscription_welcome_email(user, tier_name: str, triggered_by: str = 'webhook') -> bool:
        """
        Send welcome email when a user first subscribes.

        Args:
            user: CustomUser instance
            tier_name: Display name of the tier they subscribed to
            triggered_by: Origin of the send (webhook, admin_manual, etc.)

        Returns:
            bool: True if email was sent successfully
        """
        from users.services.email_preference_service import EmailPreferenceService
        from core.services.email_service import EmailService

        subject = "Welcome to PlatPursuit Premium!"

        if not EmailPreferenceService.should_send_email(user, 'subscription_notifications'):
            logger.info(f"Skipping welcome email for {user.email}: preference disabled")
            EmailService.log_suppressed('subscription_welcome', user, subject, triggered_by)
            return False

        username = user.profile.psn_username if hasattr(user, 'profile') else user.email.split('@')[0]
        preference_token = EmailPreferenceService.generate_preference_token(user.id)

        from users.constants import PREMIUM_PERKS
        context = {
            'username': username,
            'tier_name': tier_name,
            'site_url': settings.SITE_URL,
            'profile_url': f"{settings.SITE_URL}/profiles/{user.profile.psn_username}/" if hasattr(user, 'profile') else settings.SITE_URL,
            'preference_url': f"{settings.SITE_URL}/users/email-preferences/?token={preference_token}",
            # The perk list renders from the shared constant (the hand-written copy sold four
            # retired perks on the first email a member ever read).
            'premium_perks': PREMIUM_PERKS,
        }

        try:
            sent = EmailService.send_html_email(
                subject=subject,
                to_emails=[user.email],
                template_name='emails/subscription_welcome.html',
                context=context,
                log_email_type='subscription_welcome',
                log_user=user,
                log_triggered_by=triggered_by,
            )
            if sent:
                logger.info(f"Sent welcome email to {user.email}")
            return sent > 0
        except Exception:
            logger.exception(f"Failed to send welcome email to {user.email}")
            return False

    @staticmethod
    def _send_payment_succeeded_email(user, tier_name: str, next_billing_date=None, triggered_by: str = 'webhook') -> bool:
        """
        Send payment confirmation email on successful renewal.

        Args:
            user: CustomUser instance
            tier_name: Display name of the subscription tier
            next_billing_date: Formatted date string for next billing (or None)
            triggered_by: Origin of the send (webhook, admin_manual, etc.)

        Returns:
            bool: True if email was sent successfully
        """
        from users.services.email_preference_service import EmailPreferenceService
        from core.services.email_service import EmailService

        subject = "Payment confirmed for your PlatPursuit subscription"

        if not EmailPreferenceService.should_send_email(user, 'subscription_notifications'):
            logger.info(f"Skipping payment succeeded email for {user.email}: preference disabled")
            EmailService.log_suppressed('payment_succeeded', user, subject, triggered_by)
            return False

        username = user.profile.psn_username if hasattr(user, 'profile') else user.email.split('@')[0]
        preference_token = EmailPreferenceService.generate_preference_token(user.id)

        context = {
            'username': username,
            'tier_name': tier_name,
            'next_billing_date': next_billing_date,
            'manage_url': f"{settings.SITE_URL}{reverse('subscription_management')}",
            'site_url': settings.SITE_URL,
            'preference_url': f"{settings.SITE_URL}/users/email-preferences/?token={preference_token}",
        }

        try:
            sent = EmailService.send_html_email(
                subject=subject,
                to_emails=[user.email],
                template_name='emails/payment_succeeded.html',
                context=context,
                log_email_type='payment_succeeded',
                log_user=user,
                log_triggered_by=triggered_by,
            )
            if sent:
                logger.info(f"Sent payment succeeded email to {user.email}")
            return sent > 0
        except Exception:
            logger.exception(f"Failed to send payment succeeded email to {user.email}")
            return False

    @staticmethod
    def handle_payment_succeeded(user, invoice_data: dict) -> None:
        """
        Handle a successful payment (Stripe invoice.paid or PayPal PAYMENT.SALE.COMPLETED).

        Only sends the payment succeeded email for renewal payments, not the
        initial subscription charge (which is handled by the welcome email).

        Args:
            user: CustomUser instance
            invoice_data: Stripe Invoice object data (or empty dict for PayPal)
        """
        # Skip initial subscription invoices (welcome email handles that)
        billing_reason = invoice_data.get('billing_reason', '')
        if billing_reason == 'subscription_create':
            logger.info(f"Skipping payment succeeded email for {user.email}: initial subscription (welcome email sent instead)")
            return

        # Skip $0 invoices (setup, prorations, trials)
        amount_paid = invoice_data.get('amount_paid', 0)
        if amount_paid is not None and amount_paid <= 0:
            return

        tier_name = SubscriptionService.get_tier_display_name(user.premium_tier) if user.premium_tier else 'Premium'

        # Extract next billing date from invoice line items
        next_billing_date = None
        lines = invoice_data.get('lines', {}).get('data', [])
        if lines:
            period_end = lines[0].get('period', {}).get('end')
            if period_end:
                try:
                    next_billing_date = datetime.fromtimestamp(period_end, tz=timezone.utc).strftime('%B %d, %Y')
                except (ValueError, OSError):
                    pass

        SubscriptionService._send_payment_succeeded_email(user, tier_name, next_billing_date)

    # ── Stripe webhook handling ───────────────────────────────────────────

    @staticmethod
    def handle_webhook_event(event_type: str, event_data: dict) -> None:
        """
        Process Stripe webhook events.

        Handles:
        - checkout.session.completed
        - customer.subscription.created
        - customer.subscription.updated
        - customer.subscription.deleted
        - invoice.paid
        - invoice.payment_failed
        - invoice.payment_action_required

        Args:
            event_type: Stripe event type
            event_data: Event data from Stripe
        """
        # Payment failure events use Invoice object (different shape than Subscription)
        if event_type == 'invoice.payment_failed':
            customer_id = event_data.get('customer')
            if not customer_id:
                logger.warning(f"No customer_id in webhook event {event_type}")
                return

            try:
                from users.models import CustomUser
                user = CustomUser.objects.get(stripe_customer_id=customer_id)
                SubscriptionService.handle_payment_failed(user, event_data)
            except CustomUser.DoesNotExist:
                logger.warning(f"No user found with stripe_customer_id {customer_id}")
            return

        # Payment action required (3D Secure / SCA authentication needed)
        if event_type == 'invoice.payment_action_required':
            customer_id = event_data.get('customer')
            if not customer_id:
                logger.warning(f"No customer_id in webhook event {event_type}")
                return

            try:
                from users.models import CustomUser
                user = CustomUser.objects.get(stripe_customer_id=customer_id)
                SubscriptionService.handle_payment_action_required(user, event_data)
            except CustomUser.DoesNotExist:
                logger.warning(f"No user found with stripe_customer_id {customer_id}")
            return

        # invoice.paid: update subscription status AND send payment succeeded email
        if event_type == 'invoice.paid':
            customer_id = event_data.get('customer')
            if not customer_id:
                logger.warning(f"No customer_id in webhook event {event_type}")
                return

            try:
                from users.models import CustomUser
                user = CustomUser.objects.get(stripe_customer_id=customer_id)
                SubscriptionService.update_user_subscription(user, event_type)
                SubscriptionService.handle_payment_succeeded(user, event_data)
                logger.info(f"Updated subscription for user {user.email} from webhook {event_type}")
            except CustomUser.DoesNotExist:
                logger.warning(f"No user found with stripe_customer_id {customer_id}")
            return

        if event_type in [
            'checkout.session.completed',
            'customer.subscription.created',
            'customer.subscription.updated',
            'customer.subscription.deleted',
        ]:
            customer_id = event_data.get('customer')
            if not customer_id:
                logger.warning(f"No customer_id in webhook event {event_type}")
                return

            try:
                from users.models import CustomUser
                user = CustomUser.objects.get(stripe_customer_id=customer_id)
                SubscriptionService.update_user_subscription(user, event_type)
                logger.info(f"Updated subscription for user {user.email} from webhook {event_type}")
            except CustomUser.DoesNotExist:
                logger.warning(f"No user found with stripe_customer_id {customer_id}")
