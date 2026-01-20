"""
Subscription service - Handles all Stripe subscription-related business logic.

This service manages:
- Mapping Stripe product IDs to premium tiers
- Creating checkout sessions
- Processing subscription webhooks
- Updating user subscription status
- Discord role assignments for premium users
"""
import time
import logging
import stripe
from typing import Optional, Dict, Tuple
from django.conf import settings
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
    """Handles all Stripe subscription-related business logic."""

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

    @staticmethod
    def update_user_subscription(user, event_type: str = None) -> bool:
        """
        Update user's subscription status based on Stripe data.

        This function:
        1. Checks for active Stripe subscriptions
        2. Maps product ID to premium tier
        3. Updates user's premium_tier field
        4. Updates linked profile's premium status
        5. Sends Discord notifications and assigns roles if applicable

        Args:
            user: CustomUser instance to update
            event_type: Optional Stripe event type (e.g., 'customer.subscription.created')

        Returns:
            bool: True if user has active premium subscription
        """
        if not user.stripe_customer_id:
            user.premium_tier = None
            user.save()
            return False

        # Find active subscription
        subs = Subscription.objects.filter(customer__id=user.stripe_customer_id)
        active_sub = next((sub for sub in subs if sub.status == 'active'), None)

        is_premium = False
        if active_sub:
            # Map product ID to tier
            product_id = active_sub.plan['product']
            tier = SubscriptionService.get_tier_from_product_id(product_id)

            if tier:
                user.premium_tier = tier
                is_premium = SubscriptionService.is_tier_premium(tier)
            else:
                logger.warning(f"Unknown product ID {product_id} for user {user.email}")
                user.premium_tier = None
                is_premium = False
        else:
            # Check if subscription is canceled but still in grace period
            canceled_sub = Subscription.objects.filter(
                customer__id=user.stripe_customer_id,
                status='canceled'
            ).first()

            if canceled_sub and canceled_sub.current_period_end > int(time.time()):
                # Still in grace period, don't update
                return SubscriptionService.is_tier_premium(user.premium_tier) if user.premium_tier else False

            user.premium_tier = None

        # Update linked profile
        if hasattr(user, 'profile'):
            user.profile.update_profile_premium(is_premium)

            # Handle Discord notifications and role assignments for new subscriptions
            if event_type == 'customer.subscription.created' and is_premium:
                send_subscription_notification(user)

                # Assign Discord roles based on tier
                if user.premium_tier in PREMIUM_DISCORD_ROLE_TIERS:
                    notify_bot_role_earned(user.profile, settings.DISCORD_PREMIUM_ROLE)
                elif user.premium_tier in SUPPORTER_DISCORD_ROLE_TIERS:
                    notify_bot_role_earned(user.profile, settings.DISCORD_PREMIUM_PLUS_ROLE)

        user.save()
        return is_premium

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
        user.save()

        # Create checkout session
        session = stripe.checkout.Session.create(
            customer=customer.id,
            payment_method_types=['card', 'us_bank_account', 'amazon_pay', 'cashapp', 'link', 'paypal'],
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

        Currently handles:
        - customer.subscription.created
        - customer.subscription.updated
        - customer.subscription.deleted

        Args:
            event_type: Stripe event type
            event_data: Event data from Stripe
        """
        if event_type in [
            'customer.subscription.created',
            'customer.subscription.updated',
            'customer.subscription.deleted'
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
