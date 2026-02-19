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
from datetime import datetime, timedelta
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

logger = logging.getLogger('users.services.subscription')


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

            # Ensure a SubscriptionPeriod is open (inside transaction to prevent
            # duplicate periods from concurrent webhooks; DB partial unique
            # constraint is the ultimate guard).
            # On payment recovery (past_due -> active), re-open the most recently
            # closed period rather than creating a new one.
            if is_premium:
                from users.models import SubscriptionPeriod
                open_period = SubscriptionPeriod.objects.filter(
                    user=user, ended_at__isnull=True
                ).exists()
                if not open_period:
                    # Try to re-open the most recently closed period (payment recovery).
                    # Only reopen if closed within last 14 days (covers Stripe's retry
                    # window). Older periods get a fresh start to keep milestone
                    # calculations accurate.
                    recent_threshold = timezone.now() - timedelta(days=14)
                    recent_closed = SubscriptionPeriod.objects.filter(
                        user=user, provider=provider, ended_at__isnull=False,
                        ended_at__gte=recent_threshold,
                    ).order_by('-ended_at').first()
                    if recent_closed:
                        recent_closed.ended_at = None
                        recent_closed.save(update_fields=['ended_at'])
                    else:
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
            if hasattr(user, 'profile'):
                user.profile.update_profile_premium(False)

            # Close any open SubscriptionPeriod (inside transaction so
            # deactivation and period close are atomic)
            from users.models import SubscriptionPeriod
            SubscriptionPeriod.objects.filter(
                user=user, ended_at__isnull=True
            ).update(ended_at=timezone.now())

        logger.info(f"Deactivated {provider} subscription for user {user.email} ({event_type})")

        # Side effects after commit: Discord role removal (only the role matching the user's tier)
        if hasattr(user, 'profile') and user.profile.is_discord_verified and user.profile.discord_id:
            from trophies.services.badge_service import notify_bot_role_removed
            if original_tier in PREMIUM_DISCORD_ROLE_TIERS and settings.DISCORD_PREMIUM_ROLE:
                notify_bot_role_removed(user.profile, settings.DISCORD_PREMIUM_ROLE)
            elif original_tier in SUPPORTER_DISCORD_ROLE_TIERS and settings.DISCORD_PREMIUM_PLUS_ROLE:
                notify_bot_role_removed(user.profile, settings.DISCORD_PREMIUM_PLUS_ROLE)

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
                    action_url='/users/subscribe/',
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
                stripe_data__status__in=['active', 'past_due']
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
            # Check for past_due (payment failing, Stripe still retrying).
            # Keep premium features active but close SubscriptionPeriod
            # to stop milestone time accumulation during unpaid window.
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

    # ── Payment failure and cancellation notifications ───────────────────

    @staticmethod
    def _send_payment_failed_email(user, is_final_warning: bool) -> bool:
        """
        Send payment failure email via EmailService.

        Args:
            user: CustomUser instance
            is_final_warning: True for final attempt (premium at risk), False for first warning

        Returns:
            bool: True if email was sent successfully
        """
        from users.services.email_preference_service import EmailPreferenceService
        from core.services.email_service import EmailService

        if not EmailPreferenceService.should_send_email(user, 'subscription_notifications'):
            logger.info(f"Skipping payment failed email for {user.email}: preference disabled")
            return False

        # Generate billing portal URL for Stripe users, fallback to management page
        portal_url = f"{settings.SITE_URL}/users/subscription-management/"
        if user.stripe_customer_id:
            try:
                portal_session = stripe.billing_portal.Session.create(
                    customer=user.stripe_customer_id,
                    return_url=f"{settings.SITE_URL}/users/subscription-management/",
                )
                portal_url = portal_session.url
            except stripe.error.StripeError:
                logger.exception("Failed to create billing portal session for payment failed email")

        tier_name = SubscriptionService.get_tier_display_name(user.premium_tier) if user.premium_tier else 'Premium'
        username = user.profile.psn_username if hasattr(user, 'profile') else user.email.split('@')[0]

        preference_token = EmailPreferenceService.generate_preference_token(user.id)

        context = {
            'username': username,
            'is_final_warning': is_final_warning,
            'portal_url': portal_url,
            'tier_name': tier_name,
            'site_url': settings.SITE_URL,
            'preference_url': f"{settings.SITE_URL}/users/email-preferences/?token={preference_token}",
        }

        subject = (
            "Action Required: Your PlatPursuit subscription is at risk"
            if is_final_warning
            else "Heads up: We couldn't process your payment"
        )

        try:
            sent = EmailService.send_html_email(
                subject=subject,
                to_emails=[user.email],
                template_name='emails/payment_failed.html',
                context=context,
            )
            if sent:
                logger.info(f"Sent payment failed email to {user.email} (final={is_final_warning})")
            return sent > 0
        except Exception:
            logger.exception(f"Failed to send payment failed email to {user.email}")
            return False

    @staticmethod
    def _send_payment_failed_notification(user, attempt_count: int, is_final: bool) -> None:
        """
        Send in-app notification for payment failure.

        Args:
            user: CustomUser instance
            attempt_count: Which payment attempt failed
            is_final: True if Stripe has given up retrying
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
                action_url='/users/subscription-management/',
                action_text='Manage Subscription',
                icon='💳',
                priority=priority,
                metadata={'attempt_count': attempt_count, 'is_final': is_final},
            )
        except Exception:
            logger.exception(f"Failed to create payment failed notification for {user.email}")

    @staticmethod
    def _send_subscription_cancelled_email(user, tier_name: str) -> bool:
        """
        Send farewell email when a subscription ends.

        Args:
            user: CustomUser instance
            tier_name: Display name of the tier that just ended

        Returns:
            bool: True if email was sent successfully
        """
        from users.services.email_preference_service import EmailPreferenceService
        from core.services.email_service import EmailService

        if not EmailPreferenceService.should_send_email(user, 'subscription_notifications'):
            logger.info(f"Skipping cancellation email for {user.email}: preference disabled")
            return False

        username = user.profile.psn_username if hasattr(user, 'profile') else user.email.split('@')[0]
        preference_token = EmailPreferenceService.generate_preference_token(user.id)

        context = {
            'username': username,
            'tier_name': tier_name,
            'subscribe_url': f"{settings.SITE_URL}/users/subscribe/",
            'site_url': settings.SITE_URL,
            'preference_url': f"{settings.SITE_URL}/users/email-preferences/?token={preference_token}",
        }

        try:
            sent = EmailService.send_html_email(
                subject="We're sorry to see you go",
                to_emails=[user.email],
                template_name='emails/subscription_cancelled.html',
                context=context,
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
        failure and final warning only.

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

        # In-app notification on every attempt
        SubscriptionService._send_payment_failed_notification(user, attempt_count, is_final)

        # Email only on first failure or final warning
        if is_first or is_final:
            SubscriptionService._send_payment_failed_email(user, is_final)

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
