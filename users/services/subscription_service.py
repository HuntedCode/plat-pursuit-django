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
from typing import Optional, Dict, Tuple
from datetime import datetime
from django.utils import timezone
from django.conf import settings
from django.db import transaction
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
from trophies.services.badge_service import notify_bot_role_earned

logger = logging.getLogger('psn_api')


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
            str: Premium tier name ('ad_free', 'premium_monthly', etc.) or None if not found
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
            if pid == product_id:
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

        Note: 'ad_free' tier exists but doesn't grant premium features,
        only removes ads.

        Args:
            tier: Premium tier name

        Returns:
            bool: True if tier grants premium features
        """
        return tier in ACTIVE_PREMIUM_TIERS

    # ── Provider-agnostic subscription lifecycle ──────────────────────────

    @staticmethod
    def activate_subscription(user, tier: str, provider: str, event_type: str = None) -> bool:
        """
        Activate a subscription for a user, regardless of payment provider.

        Called by both Stripe and PayPal webhook handlers when a subscription
        becomes active. Sets premium_tier, subscription_provider, updates
        profile premium status, and handles Discord notifications/roles.

        Args:
            user: CustomUser instance
            tier: Subscription tier name ('ad_free', 'premium_monthly', etc.)
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
            if hasattr(user, 'profile'):
                user.profile.update_profile_premium(is_premium)

            # Open a new SubscriptionPeriod if one isn't already open (inside
            # transaction to prevent duplicate periods from concurrent webhooks;
            # DB partial unique constraint is the ultimate guard)
            if is_premium:
                from users.models import SubscriptionPeriod
                open_period = SubscriptionPeriod.objects.filter(
                    user=user, ended_at__isnull=True
                ).exists()
                if not open_period:
                    SubscriptionPeriod.objects.create(
                        user=user,
                        started_at=timezone.now(),
                        provider=provider,
                    )

        # Discord notifications for new subscriptions (side effects after commit)
        activation_events = [
            'customer.subscription.created',       # Stripe
            'BILLING.SUBSCRIPTION.ACTIVATED',       # PayPal
        ]
        if hasattr(user, 'profile') and event_type in activation_events and is_premium:
            send_subscription_notification(user)
            if user.premium_tier in PREMIUM_DISCORD_ROLE_TIERS:
                notify_bot_role_earned(user.profile, settings.DISCORD_PREMIUM_ROLE)
            elif user.premium_tier in SUPPORTER_DISCORD_ROLE_TIERS:
                notify_bot_role_earned(user.profile, settings.DISCORD_PREMIUM_PLUS_ROLE)

        # Check is_premium and subscription_months milestones
        if is_premium and hasattr(user, 'profile'):
            from trophies.services.milestone_service import check_all_milestones_for_user
            check_all_milestones_for_user(
                user.profile,
                criteria_types=['is_premium', 'subscription_months'],
            )

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
        user.premium_tier = None
        user.subscription_provider = None
        update_fields = ['premium_tier', 'subscription_provider']
        if provider == 'paypal':
            user.paypal_subscription_id = None
            user.paypal_cancel_at = None
            update_fields += ['paypal_subscription_id', 'paypal_cancel_at']

        with transaction.atomic():
            user.save(update_fields=update_fields)
            if hasattr(user, 'profile'):
                user.profile.update_profile_premium(False)

            # Close any open SubscriptionPeriod (inside transaction so
            # deactivation and period close are atomic)
            from users.models import SubscriptionPeriod
            SubscriptionPeriod.objects.filter(
                user=user, ended_at__isnull=True
            ).update(ended_at=timezone.now())

        logger.info(f"Deactivated {provider} subscription for user {user.email} ({event_type})")

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
        # Check Stripe
        if user.stripe_customer_id:
            active_stripe = Subscription.objects.filter(
                customer__id=user.stripe_customer_id,
                stripe_data__status='active'
            ).exists()
            if active_stripe:
                return (True, 'stripe')

        # Check PayPal (trust our stored state, set by webhooks)
        if user.paypal_subscription_id and user.premium_tier and user.subscription_provider == 'paypal':
            return (True, 'paypal')

        return (False, None)

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
        if not user.stripe_customer_id:
            SubscriptionService.deactivate_subscription(user, 'stripe', event_type)
            return False

        # Find active subscription
        active_sub = Subscription.objects.filter(
            customer__id=user.stripe_customer_id,
            stripe_data__status='active'
        ).first()

        if active_sub:
            # Map product ID to tier via stripe_data JSON
            stripe_data = active_sub.stripe_data or {}
            plan = stripe_data.get('plan', {})
            product_id = plan.get('product')
            tier = SubscriptionService.get_tier_from_product_id(product_id)

            if tier:
                return SubscriptionService.activate_subscription(user, tier, 'stripe', event_type)
            else:
                logger.warning(f"Unknown product ID {product_id} for user {user.email}")
                SubscriptionService.deactivate_subscription(user, 'stripe', event_type)
                return False
        else:
            # Check if subscription is canceled but still in grace period
            canceled_sub = Subscription.objects.filter(
                customer__id=user.stripe_customer_id,
                stripe_data__status='canceled'
            ).first()

            if canceled_sub:
                canceled_data = canceled_sub.stripe_data or {}
                period_end_ts = canceled_data.get('current_period_end')
                if period_end_ts and datetime.fromtimestamp(period_end_ts, tz=timezone.utc) > timezone.now():
                    # Still in grace period, keep premium active
                    return SubscriptionService.is_tier_premium(user.premium_tier) if user.premium_tier else False

            SubscriptionService.deactivate_subscription(user, 'stripe', event_type)
            return False

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
    def create_checkout_session(user, tier: str, success_url: str, cancel_url: str) -> str:
        """
        Create a Stripe checkout session for a subscription.

        Args:
            user: CustomUser instance
            tier: Subscription tier ('ad_free', 'premium_monthly', etc.)
            success_url: URL to redirect to after successful payment
            cancel_url: URL to redirect to if payment is canceled

        Returns:
            str: Stripe checkout session URL

        Raises:
            ValueError: If tier is invalid or price not found
            stripe.error.StripeError: If Stripe API call fails
        """
        is_live = settings.STRIPE_MODE == 'live'
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
            metadata={'tier': tier},
        )

        return session.url

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

        Args:
            event_type: Stripe event type
            event_data: Event data from Stripe
        """
        if event_type in [
            'checkout.session.completed',
            'customer.subscription.created',
            'customer.subscription.updated',
            'customer.subscription.deleted',
            'invoice.paid',
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
