"""
PayPal Subscription Service: handles all PayPal subscription-related business logic.

This service manages:
- OAuth2 access token acquisition (cached in Redis)
- Creating PayPal subscriptions (redirecting to PayPal checkout)
- Processing PayPal webhook events
- Verifying webhook signatures
- Looking up and cancelling subscriptions
"""
import json
import logging
import requests
from datetime import datetime, timedelta
from typing import Optional
from django.conf import settings
from django.core.cache import cache
from django.utils import timezone
from users.constants import PAYPAL_PLANS, PAYPAL_PLAN_TO_TIER

logger = logging.getLogger('psn_api')

PAYPAL_TOKEN_CACHE_KEY = f'paypal_access_token:{settings.PAYPAL_MODE}'


class PayPalService:
    """Handles all PayPal subscription-related business logic."""

    @staticmethod
    def _get_access_token() -> str:
        """
        Get PayPal OAuth2 access token, cached in Redis.

        PayPal tokens are valid for ~9 hours. We cache for 8 hours
        to avoid edge cases near expiration.
        """
        cached = cache.get(PAYPAL_TOKEN_CACHE_KEY)
        if cached:
            return cached

        response = requests.post(
            f"{settings.PAYPAL_API_BASE}/v1/oauth2/token",
            auth=(settings.PAYPAL_CLIENT_ID, settings.PAYPAL_CLIENT_SECRET),
            data={'grant_type': 'client_credentials'},
            headers={'Accept': 'application/json'},
            timeout=30,
        )
        response.raise_for_status()
        data = response.json()

        token = data['access_token']
        expires_in = data.get('expires_in', 32400)  # Default ~9 hours
        cache.set(PAYPAL_TOKEN_CACHE_KEY, token, timeout=min(expires_in - 300, 28800))
        return token

    @staticmethod
    def _api_headers() -> dict:
        """Build headers for PayPal API calls."""
        return {
            'Authorization': f'Bearer {PayPalService._get_access_token()}',
            'Content-Type': 'application/json',
            'Accept': 'application/json',
        }

    @staticmethod
    def get_tier_from_plan_id(plan_id: str) -> Optional[str]:
        """Map a PayPal plan ID to a premium tier name."""
        return PAYPAL_PLAN_TO_TIER.get(plan_id)

    @staticmethod
    def create_subscription(user, tier: str, return_url: str, cancel_url: str) -> str:
        """
        Create a PayPal subscription and return the approval URL.

        The user is redirected to this URL to complete payment on PayPal.

        Args:
            user: CustomUser instance
            tier: Subscription tier ('ad_free', 'premium_monthly', etc.)
            return_url: URL PayPal redirects to after approval
            cancel_url: URL PayPal redirects to if user cancels

        Returns:
            str: PayPal approval URL for redirect

        Raises:
            ValueError: If tier is invalid or no approval URL returned
            requests.HTTPError: If PayPal API call fails
        """
        mode = 'live' if settings.PAYPAL_MODE == 'live' else 'sandbox'
        plans = PAYPAL_PLANS.get(mode, {})

        if tier not in plans or not plans[tier]:
            raise ValueError(f"Invalid or unconfigured PayPal tier: {tier}")

        plan_id = plans[tier]

        payload = {
            'plan_id': plan_id,
            'subscriber': {
                'email_address': user.email,
            },
            'application_context': {
                'brand_name': 'PlatPursuit',
                'locale': 'en-US',
                'shipping_preference': 'NO_SHIPPING',
                'user_action': 'SUBSCRIBE_NOW',
                'return_url': return_url,
                'cancel_url': cancel_url,
            },
            'custom_id': str(user.id),
        }

        response = requests.post(
            f"{settings.PAYPAL_API_BASE}/v1/billing/subscriptions",
            json=payload,
            headers=PayPalService._api_headers(),
            timeout=30,
        )
        response.raise_for_status()
        data = response.json()

        for link in data.get('links', []):
            if link['rel'] == 'approve':
                return link['href']

        raise ValueError("No approval URL in PayPal subscription response")

    @staticmethod
    def get_subscription_details(subscription_id: str) -> dict:
        """
        Fetch current subscription details from PayPal.

        Returns the full subscription object including status, billing_info,
        plan_id, and next billing time.
        """
        response = requests.get(
            f"{settings.PAYPAL_API_BASE}/v1/billing/subscriptions/{subscription_id}",
            headers=PayPalService._api_headers(),
            timeout=30,
        )
        response.raise_for_status()
        return response.json()

    @staticmethod
    def cancel_subscription(subscription_id: str, reason: str = "User requested cancellation") -> bool:
        """
        Cancel a PayPal subscription.

        Returns True if cancellation succeeded (204 No Content).
        """
        response = requests.post(
            f"{settings.PAYPAL_API_BASE}/v1/billing/subscriptions/{subscription_id}/cancel",
            json={'reason': reason},
            headers=PayPalService._api_headers(),
            timeout=30,
        )
        return response.status_code == 204

    @staticmethod
    def verify_webhook_signature(meta: dict, raw_body: bytes) -> bool:
        """
        Verify PayPal webhook signature by posting back to PayPal's verification endpoint.

        Args:
            meta: request.META dict containing PayPal signature headers
            raw_body: the raw request body bytes (parsed from original, not re-serialized)
        """
        verify_payload = {
            'auth_algo': meta.get('HTTP_PAYPAL_AUTH_ALGO', ''),
            'cert_url': meta.get('HTTP_PAYPAL_CERT_URL', ''),
            'transmission_id': meta.get('HTTP_PAYPAL_TRANSMISSION_ID', ''),
            'transmission_sig': meta.get('HTTP_PAYPAL_TRANSMISSION_SIG', ''),
            'transmission_time': meta.get('HTTP_PAYPAL_TRANSMISSION_TIME', ''),
            'webhook_id': settings.PAYPAL_WEBHOOK_ID,
            'webhook_event': json.loads(raw_body),
        }

        try:
            response = requests.post(
                f"{settings.PAYPAL_API_BASE}/v1/notifications/verify-webhook-signature",
                json=verify_payload,
                headers=PayPalService._api_headers(),
                timeout=30,
            )
            if response.status_code == 200:
                return response.json().get('verification_status') == 'SUCCESS'
        except Exception:
            logger.exception("PayPal webhook signature verification failed")
        return False

    @staticmethod
    def handle_webhook_event(event_type: str, resource: dict) -> None:
        """
        Process PayPal webhook events related to subscriptions.

        Lifecycle differences vs Stripe:
        - ACTIVATED: subscription became active (equivalent to Stripe subscription.created)
        - CANCELLED: user cancelled, but still has paid time remaining. Do NOT remove premium.
        - SUSPENDED: payment failed. Remove premium immediately.
        - EXPIRED: subscription period ended. Remove premium.
        - PAYMENT.SALE.COMPLETED: recurring payment succeeded. Log only.
        """
        from users.models import CustomUser
        from users.services.subscription_service import SubscriptionService

        subscription_id = resource.get('id')

        # For PAYMENT.SALE.COMPLETED, the subscription ID is in billing_agreement_id
        if event_type == 'PAYMENT.SALE.COMPLETED':
            subscription_id = resource.get('billing_agreement_id')

        if not subscription_id:
            logger.warning(f"No subscription_id in PayPal webhook event {event_type}")
            return

        # Look up user by paypal_subscription_id
        try:
            user = CustomUser.objects.get(paypal_subscription_id=subscription_id)
        except CustomUser.DoesNotExist:
            # For ACTIVATED events, look up by custom_id (user.id set during creation)
            if event_type == 'BILLING.SUBSCRIPTION.ACTIVATED':
                custom_id = resource.get('custom_id')
                if custom_id:
                    try:
                        user = CustomUser.objects.get(id=int(custom_id))
                        # Set PayPal fields on the user object without saving yet.
                        # activate_subscription() will save all fields atomically.
                        user.paypal_subscription_id = subscription_id
                    except (CustomUser.DoesNotExist, ValueError):
                        logger.warning(f"No user found with custom_id {custom_id} for PayPal sub {subscription_id}")
                        return
                else:
                    logger.warning(f"No user found for PayPal subscription {subscription_id}")
                    return
            else:
                logger.warning(f"No user found with paypal_subscription_id {subscription_id}")
                return

        if event_type == 'BILLING.SUBSCRIPTION.ACTIVATED':
            plan_id = resource.get('plan_id')
            tier = PayPalService.get_tier_from_plan_id(plan_id)
            if tier:
                SubscriptionService.activate_subscription(user, tier, 'paypal', event_type)
                logger.info(f"PayPal subscription activated for user {user.email}, tier={tier}")
            else:
                logger.warning(f"Unknown PayPal plan_id {plan_id}")

        elif event_type == 'BILLING.SUBSCRIPTION.CANCELLED':
            # User cancelled but still has paid time. Mark as cancelling, keep premium.
            billing_info = resource.get('billing_info', {})
            next_billing = billing_info.get('next_billing_time')
            cancel_at = None
            if next_billing:
                try:
                    cancel_at = datetime.fromisoformat(next_billing.replace('Z', '+00:00'))
                except (ValueError, AttributeError):
                    logger.warning(f"Failed to parse next_billing_time '{next_billing}' for user {user.email}")
            else:
                # No expiry date from PayPal: use a 30-day safety fallback so the user
                # doesn't stay premium forever if the EXPIRED webhook is never received.
                cancel_at = timezone.now() + timedelta(days=30)
                logger.warning(
                    f"PayPal CANCELLED event for user {user.email} has no next_billing_time, "
                    f"using 30-day fallback expiry: {cancel_at}"
                )
            SubscriptionService.mark_subscription_cancelling(user, cancel_at)
            logger.info(f"PayPal subscription cancelling for user {user.email}, expires at {cancel_at}")

        elif event_type == 'BILLING.SUBSCRIPTION.SUSPENDED':
            # Payment failed: send notification before deactivating (no retry cycle for PayPal).
            # Always uses is_final_warning=True so the email shows the urgent/final tone,
            # not the first-warning copy that mentions Stripe-specific retry behavior.
            SubscriptionService._send_payment_failed_email(user, is_final_warning=True)
            SubscriptionService._send_payment_failed_notification(user, attempt_count=1, is_final=True)
            SubscriptionService.deactivate_subscription(user, 'paypal', event_type)
            logger.info(f"PayPal subscription suspended (payment failed) for user {user.email}")

        elif event_type == 'BILLING.SUBSCRIPTION.EXPIRED':
            SubscriptionService.deactivate_subscription(user, 'paypal', event_type)
            logger.info(f"PayPal subscription expired for user {user.email}")

        elif event_type == 'PAYMENT.SALE.COMPLETED':
            # Send payment succeeded email for renewals (skip if just activated).
            # PayPal fires ACTIVATED then SALE.COMPLETED in quick succession on
            # first payment, so check if a welcome email was logged in the last
            # 5 minutes to avoid double-emailing.
            from core.models import EmailLog
            recent_welcome = EmailLog.objects.filter(
                user=user,
                email_type='subscription_welcome',
                created_at__gte=timezone.now() - timedelta(minutes=5),
            ).exists()
            if not recent_welcome:
                tier_name = SubscriptionService.get_tier_display_name(user.premium_tier) if user.premium_tier else 'Premium'
                SubscriptionService._send_payment_succeeded_email(user, tier_name)
            logger.info(f"PayPal renewal payment for user {user.email}")
