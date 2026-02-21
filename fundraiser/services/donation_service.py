"""
Donation service: handles one-time donation lifecycle for fundraiser campaigns.

This service manages:
- Stripe one-time payment checkout sessions (mode='payment')
- PayPal Orders API v2 (create order, capture payment)
- Donation completion (status updates, reward granting)
- Badge claiming for badge_artwork campaigns
- Milestone/title/Discord role granting
- Email notifications (receipt, claim confirmation, artwork complete)
"""
import logging
import math
import requests
import stripe
import uuid

from django.conf import settings
from django.db import IntegrityError, transaction
from django.db.models import F
from django.utils import timezone

from core.services.email_service import EmailService
from fundraiser.models import Donation, DonationBadgeClaim, Fundraiser
from users.services.email_preference_service import EmailPreferenceService

logger = logging.getLogger(__name__)


class DonationService:
    """Handles one-time donation lifecycle for all payment providers."""

    # ──────────────────────────────────────────────
    # Stripe One-Time Payment
    # ──────────────────────────────────────────────

    @staticmethod
    def create_stripe_checkout(fundraiser, user, amount, is_anonymous, message,
                               success_url, cancel_url):
        """
        Create a Stripe Checkout Session in mode='payment' for a one-time donation.

        Returns the Stripe-hosted checkout URL for redirect.
        """
        profile = getattr(user, 'profile', None)

        donation = Donation.objects.create(
            fundraiser=fundraiser,
            user=user,
            profile=profile,
            amount=amount,
            provider='stripe',
            provider_transaction_id=f'pending_{uuid.uuid4().hex}',
            status='pending',
            is_anonymous=is_anonymous,
            message=message,
        )

        session = stripe.checkout.Session.create(
            payment_method_types=['card', 'us_bank_account', 'amazon_pay', 'cashapp', 'link'],
            line_items=[{
                'price_data': {
                    'currency': 'usd',
                    'product_data': {
                        'name': f'PlatPursuit: {fundraiser.name}',
                        'description': f'Donation of ${amount}',
                    },
                    'unit_amount': int(amount * 100),
                },
                'quantity': 1,
            }],
            mode='payment',
            success_url=success_url + '?session_id={CHECKOUT_SESSION_ID}',
            cancel_url=cancel_url,
            customer_email=user.email,
            metadata={
                'donation_id': str(donation.id),
                'type': 'fundraiser_donation',
            },
        )

        donation.provider_transaction_id = session.id
        donation.save(update_fields=['provider_transaction_id'])

        return session.url

    # ──────────────────────────────────────────────
    # PayPal One-Time Payment (Orders API v2)
    # ──────────────────────────────────────────────

    @staticmethod
    def create_paypal_order(fundraiser, user, amount, is_anonymous, message,
                            return_url, cancel_url):
        """
        Create a PayPal Order via Orders API v2 and return the approval URL.

        The user is redirected to this URL to approve payment on PayPal.
        """
        from users.services.paypal_service import PayPalService

        profile = getattr(user, 'profile', None)

        donation = Donation.objects.create(
            fundraiser=fundraiser,
            user=user,
            profile=profile,
            amount=amount,
            provider='paypal',
            provider_transaction_id=f'pending_{uuid.uuid4().hex}',
            status='pending',
            is_anonymous=is_anonymous,
            message=message,
        )

        payload = {
            'intent': 'CAPTURE',
            'purchase_units': [{
                'amount': {
                    'currency_code': 'USD',
                    'value': str(amount),
                },
                'description': f'PlatPursuit: {fundraiser.name}',
                'custom_id': str(donation.id),
            }],
            'application_context': {
                'brand_name': 'PlatPursuit',
                'shipping_preference': 'NO_SHIPPING',
                'user_action': 'PAY_NOW',
                'return_url': return_url,
                'cancel_url': cancel_url,
            },
        }

        response = requests.post(
            f"{settings.PAYPAL_API_BASE}/v2/checkout/orders",
            json=payload,
            headers=PayPalService._api_headers(),
            timeout=30,
        )
        response.raise_for_status()
        data = response.json()

        order_id = data['id']
        donation.provider_transaction_id = order_id
        donation.save(update_fields=['provider_transaction_id'])

        for link in data.get('links', []):
            if link['rel'] == 'approve':
                return link['href']

        logger.error(f"No approval URL in PayPal order response for donation {donation.id}")
        raise ValueError("No approval URL in PayPal order response")

    @staticmethod
    def capture_paypal_order(order_id):
        """
        Capture a PayPal order after user approval.

        Called on the success redirect page for fast UX, with webhook as backup.
        Returns the capture response JSON.
        """
        from users.services.paypal_service import PayPalService

        response = requests.post(
            f"{settings.PAYPAL_API_BASE}/v2/checkout/orders/{order_id}/capture",
            headers=PayPalService._api_headers(),
            timeout=30,
        )
        response.raise_for_status()
        return response.json()

    # ──────────────────────────────────────────────
    # Webhook Handlers
    # ──────────────────────────────────────────────

    @staticmethod
    def handle_stripe_payment_completed(session_data):
        """
        Handle checkout.session.completed for donation payments.

        Called from stripe_webhook when mode='payment' and type='fundraiser_donation'.
        """
        donation_id = session_data.get('metadata', {}).get('donation_id')
        if not donation_id:
            logger.warning("Stripe donation webhook missing donation_id in metadata")
            return

        try:
            donation = Donation.objects.select_related('fundraiser', 'profile', 'user').get(
                id=int(donation_id), status='pending',
            )
        except Donation.DoesNotExist:
            logger.info(f"Donation {donation_id} not found or already completed")
            return

        DonationService.complete_donation(donation)

    @staticmethod
    def handle_paypal_capture_completed(resource):
        """
        Handle PAYMENT.CAPTURE.COMPLETED for donation orders.

        PayPal nests the custom_id inside purchase_units -> payments -> captures.
        Returns True if a matching donation was found and processed, False otherwise.
        """
        custom_id = resource.get('custom_id')

        if not custom_id:
            # Try nested structure for capture events
            for pu in resource.get('purchase_units', []):
                for capture in pu.get('payments', {}).get('captures', []):
                    custom_id = capture.get('custom_id')
                    if custom_id:
                        break
                if custom_id:
                    break

        if not custom_id:
            return False

        try:
            donation = Donation.objects.select_related('fundraiser', 'profile', 'user').get(
                id=int(custom_id), status='pending',
            )
        except (Donation.DoesNotExist, ValueError):
            return False

        DonationService.complete_donation(donation)
        return True

    # ──────────────────────────────────────────────
    # Donation Completion
    # ──────────────────────────────────────────────

    @staticmethod
    def complete_donation(donation):
        """
        Finalize a donation after payment is confirmed.

        Updates status, calculates badge picks (for badge_artwork campaigns),
        grants the fundraiser milestone, sends receipt email, and posts
        a Discord webhook notification.
        """
        if donation.status == 'completed':
            logger.info(f"Donation {donation.id} already completed, skipping")
            return

        donation.status = 'completed'
        donation.completed_at = timezone.now()

        # Campaign-type-specific rewards
        if donation.fundraiser.campaign_type == 'badge_artwork':
            donation.badge_picks_earned = math.floor(donation.amount / Fundraiser.BADGE_PICK_DIVISOR)

        donation.save(update_fields=['status', 'completed_at', 'badge_picks_earned'])

        logger.info(
            f"Donation {donation.id} completed: ${donation.amount} by user {donation.user_id} "
            f"({donation.badge_picks_earned} badge picks)"
        )

        # Grant fundraiser milestone (idempotent: safe for repeat donations)
        DonationService._grant_fundraiser_milestone(donation)

        # Send receipt email + in-app notification
        DonationService._send_donation_receipt_email(donation)
        DonationService._send_donation_notification(donation)

        # Discord webhook notification
        DonationService._send_discord_notification(donation)

    # ──────────────────────────────────────────────
    # Badge Claiming
    # ──────────────────────────────────────────────

    @staticmethod
    def claim_badge(donation, profile, badge_id):
        """
        Claim a badge series for artwork commissioning.

        Validates all preconditions inside an atomic transaction with
        select_for_update() to prevent race conditions on concurrent claims.

        Args:
            donation: Completed Donation instance
            profile: Profile of the claiming user
            badge_id: ID of the Tier 1 badge to claim

        Returns the DonationBadgeClaim instance.
        Raises ValueError on validation failure.
        """
        from trophies.models import Badge

        try:
            with transaction.atomic():
                # Lock the badge row to prevent concurrent claims
                badge = Badge.objects.select_for_update().get(id=badge_id, tier=1)

                # Refresh donation inside transaction to get accurate picks count
                donation.refresh_from_db(fields=['badge_picks_earned', 'badge_picks_used'])

                if donation.badge_picks_remaining <= 0:
                    raise ValueError("No badge picks remaining for this donation.")

                if not profile.is_discord_verified:
                    raise ValueError(
                        "Discord verification is required to claim badges. "
                        "Link your Discord in our server to claim your picks."
                    )

                if badge.badge_image:
                    raise ValueError("This badge series already has custom artwork.")

                if hasattr(badge, 'artwork_claim'):
                    raise ValueError("This badge series has already been claimed by another donor.")

                series_name = badge.effective_display_series or badge.name

                claim = DonationBadgeClaim.objects.create(
                    donation=donation,
                    profile=profile,
                    badge=badge,
                    series_slug=badge.series_slug,
                    series_name=series_name,
                )
                Donation.objects.filter(pk=donation.pk).update(
                    badge_picks_used=F('badge_picks_used') + 1,
                )
                donation.refresh_from_db(fields=['badge_picks_used'])

        except Badge.DoesNotExist:
            raise ValueError("Badge not found.")
        except IntegrityError:
            raise ValueError("This badge series has already been claimed by another donor.")

        logger.info(
            f"Badge claim created: {series_name} ({badge.series_slug}) "
            f"by {profile.psn_username} via donation {donation.id}"
        )

        DonationService._send_badge_claim_email(claim)
        DonationService._send_badge_claim_notification(claim)
        return claim

    # ──────────────────────────────────────────────
    # Milestone / Title / Discord Role
    # ──────────────────────────────────────────────

    @staticmethod
    def _grant_fundraiser_milestone(donation):
        """
        Grant the fundraiser milestone, title, and Discord role.

        Uses the 'manual' criteria_type milestone pattern: directly creates
        UserMilestone record. Idempotent for repeat donors.
        """
        from trophies.models import Milestone, UserMilestone, UserTitle
        from trophies.services.badge_service import notify_bot_role_earned

        profile = donation.profile
        if not profile:
            return

        try:
            milestone = Milestone.objects.select_related('title').get(
                name='Badge Artwork Patron',
                criteria_type='manual',
            )
        except Milestone.DoesNotExist:
            logger.warning("Fundraiser milestone 'Badge Artwork Patron' not found. Run populate_milestones.")
            return

        _, created = UserMilestone.objects.get_or_create(
            profile=profile,
            milestone=milestone,
        )

        if created:
            # Update earned_count via F() to avoid race conditions
            Milestone.objects.filter(pk=milestone.pk).update(
                earned_count=F('earned_count') + 1,
            )

            # Grant title if milestone has one
            if milestone.title:
                UserTitle.objects.get_or_create(
                    profile=profile,
                    title=milestone.title,
                    defaults={'source_type': 'milestone', 'source_id': milestone.pk},
                )

            # Assign Discord role
            if (milestone.discord_role_id
                    and profile.is_discord_verified
                    and profile.discord_id):
                transaction.on_commit(
                    lambda p=profile, r=milestone.discord_role_id:
                        notify_bot_role_earned(p, r)
                )

            logger.info(f"Fundraiser milestone granted to {profile.psn_username}")

    # ──────────────────────────────────────────────
    # Email Notifications
    # ──────────────────────────────────────────────

    @staticmethod
    def _build_email_base_context(user):
        """Build shared context variables required by base_email.html."""
        try:
            preference_token = EmailPreferenceService.generate_preference_token(user.id)
            preference_url = f"{settings.SITE_URL}/users/email-preferences/?token={preference_token}"
        except Exception:
            logger.exception(f"Failed to generate preference_url for user {user.id}")
            preference_url = f"{settings.SITE_URL}/users/email-preferences/"

        return {
            'site_url': settings.SITE_URL,
            'preference_url': preference_url,
        }

    @staticmethod
    def _send_donation_receipt_email(donation):
        """Send donation receipt/thank-you email."""
        if not donation.user or not donation.user.email:
            return

        try:
            context = {
                **DonationService._build_email_base_context(donation.user),
                'user': donation.user,
                'donation': donation,
                'fundraiser': donation.fundraiser,
                'badge_picks_earned': donation.badge_picks_earned,
                'claim_url': f"{settings.SITE_URL}/fundraiser/{donation.fundraiser.slug}/",
            }

            EmailService.send_html_email(
                subject=f"Thank you for your donation to {donation.fundraiser.name}!",
                to_emails=donation.user.email,
                template_name='emails/donation_receipt.html',
                context=context,
                log_email_type='donation_receipt',
                log_user=donation.user,
                log_triggered_by='webhook',
                log_metadata={
                    'donation_id': donation.id,
                    'amount': str(donation.amount),
                    'fundraiser_slug': donation.fundraiser.slug,
                },
            )
        except Exception:
            logger.exception(f"Failed to send donation receipt for donation {donation.id}")

    @staticmethod
    def _send_badge_claim_email(claim):
        """Send badge claim confirmation email."""
        user = claim.profile.user if claim.profile else None
        if not user or not user.email:
            return

        try:
            context = {
                **DonationService._build_email_base_context(user),
                'user': user,
                'claim': claim,
                'badge_url': f"{settings.SITE_URL}/badges/{claim.series_slug}/",
            }

            EmailService.send_html_email(
                subject=f"Badge claimed: {claim.series_name}",
                to_emails=user.email,
                template_name='emails/badge_claim_confirmation.html',
                context=context,
                log_email_type='badge_claim_confirmation',
                log_user=user,
                log_triggered_by='system',
                log_metadata={
                    'claim_id': claim.id,
                    'series_slug': claim.series_slug,
                },
            )
        except Exception:
            logger.exception(f"Failed to send badge claim email for claim {claim.id}")

    @staticmethod
    def send_artwork_complete_email(claim):
        """Send notification that artwork has been completed for a claimed badge."""
        user = claim.profile.user if claim.profile else None
        if not user or not user.email:
            return

        try:
            context = {
                **DonationService._build_email_base_context(user),
                'user': user,
                'claim': claim,
                'badge_url': f"{settings.SITE_URL}/badges/{claim.series_slug}/",
            }

            EmailService.send_html_email(
                subject=f"New artwork is live: {claim.series_name}!",
                to_emails=user.email,
                template_name='emails/artwork_complete.html',
                context=context,
                log_email_type='artwork_complete',
                log_user=user,
                log_triggered_by='admin_manual',
                log_metadata={
                    'claim_id': claim.id,
                    'series_slug': claim.series_slug,
                },
            )
        except Exception:
            logger.exception(f"Failed to send artwork complete email for claim {claim.id}")

    @staticmethod
    def send_artwork_complete_notification(claim):
        """Create an in-app notification when artwork is completed."""
        from notifications.services.notification_service import NotificationService

        user = claim.profile.user if claim.profile else None
        if not user:
            return

        try:
            NotificationService.create_notification(
                recipient=user,
                notification_type='system_alert',
                title=f"New badge artwork: {claim.series_name}",
                icon='🎉',
                message=(
                    f"The artwork you helped commission for {claim.series_name} is now live! "
                    f"Check out the new look."
                ),
                sections=[
                    {
                        'id': 'artwork_live',
                        'header': 'New Artwork Live',
                        'icon': '🖼️',
                        'content': (
                            f"Custom artwork for the *{claim.series_name}* badge series "
                            f"has been uploaded and is now live across all tiers! "
                            f"[Check it out](/badges/{claim.series_slug}/)."
                        ),
                        'order': 1,
                    },
                    {
                        'id': 'thank_you',
                        'header': 'Thank You',
                        'icon': '❤️',
                        'content': (
                            "Your contribution made this possible. "
                            "You're now credited as the patron on the badge detail page. "
                            "Thank you for helping bring our badges to life!"
                        ),
                        'order': 2,
                    },
                ],
                action_url=f'/badges/{claim.series_slug}/',
                action_text='View Badge',
                priority='high',
                metadata={
                    'claim_id': claim.id,
                    'series_slug': claim.series_slug,
                    'series_name': claim.series_name,
                },
            )
        except Exception:
            logger.exception(f"Failed to create artwork notification for claim {claim.id}")

    @staticmethod
    def _send_donation_notification(donation):
        """Create an in-app notification when a donation is completed."""
        from notifications.services.notification_service import NotificationService

        if not donation.user:
            return

        try:
            sections = []
            if (donation.fundraiser.campaign_type == 'badge_artwork'
                    and donation.badge_picks_earned > 0):
                picks = donation.badge_picks_earned
                sections.append({
                    'id': 'rewards',
                    'header': 'Your Rewards',
                    'icon': '🎁',
                    'content': (
                        f"You earned *{picks}* badge artwork "
                        f"{'pick' if picks == 1 else 'picks'}! "
                        f"Head to the [fundraiser page](/fundraiser/{donation.fundraiser.slug}/) "
                        f"to browse available badges and claim your picks."
                    ),
                    'order': 1,
                })
            sections.append({
                'id': 'impact',
                'header': 'Your Impact',
                'icon': '📊',
                'content': (
                    f"Your *${donation.amount:.0f}* contribution to "
                    f"*{donation.fundraiser.name}* helps bring our badge artwork to life. "
                    f"Every donation makes a difference for the community!"
                ),
                'order': 2,
            })

            NotificationService.create_notification(
                recipient=donation.user,
                notification_type='system_alert',
                title='Donation received!',
                icon='💚',
                message=(
                    f"Thank you for your ${donation.amount:.0f} donation "
                    f"to {donation.fundraiser.name}!"
                ),
                sections=sections,
                action_url=f'/fundraiser/{donation.fundraiser.slug}/',
                action_text='View Fundraiser',
                priority='normal',
                metadata={
                    'donation_id': donation.id,
                    'amount': str(donation.amount),
                    'fundraiser_slug': donation.fundraiser.slug,
                    'badge_picks_earned': donation.badge_picks_earned,
                },
            )
        except Exception:
            logger.exception(f"Failed to create donation notification for donation {donation.id}")

    @staticmethod
    def _send_badge_claim_notification(claim):
        """Create an in-app notification when a badge is claimed."""
        from notifications.services.notification_service import NotificationService

        user = claim.profile.user if claim.profile else None
        if not user:
            return

        try:
            NotificationService.create_notification(
                recipient=user,
                notification_type='system_alert',
                title=f"Badge claimed: {claim.series_name}",
                icon='🎨',
                message=(
                    f"Your claim on {claim.series_name} has been confirmed. "
                    f"We'll notify you when the artwork is ready!"
                ),
                sections=[
                    {
                        'id': 'confirmation',
                        'header': 'Claim Confirmed',
                        'icon': '✅',
                        'content': (
                            f"Your pick for *{claim.series_name}* has been locked in. "
                            f"No one else can claim this badge series."
                        ),
                        'order': 1,
                    },
                    {
                        'id': 'next_steps',
                        'header': 'What Happens Next',
                        'icon': '🖌️',
                        'content': (
                            "Our artist will be commissioned to create custom artwork "
                            "for this badge series. You'll receive a notification as soon "
                            "as the artwork is uploaded and live on the site!"
                        ),
                        'order': 2,
                    },
                ],
                action_url=f'/badges/{claim.series_slug}/',
                action_text='View Badge',
                priority='normal',
                metadata={
                    'claim_id': claim.id,
                    'series_slug': claim.series_slug,
                    'series_name': claim.series_name,
                },
            )
        except Exception:
            logger.exception(f"Failed to create badge claim notification for claim {claim.id}")

    # ──────────────────────────────────────────────
    # Discord Webhook
    # ──────────────────────────────────────────────

    @staticmethod
    def _send_discord_notification(donation):
        """Send a Discord webhook notification for a new donation."""
        from trophies.discord_utils.discord_notifications import queue_webhook_send

        profile = donation.profile
        if not profile:
            return

        try:
            plat_pursuit_emoji = (
                f"<:PlatPursuit:{settings.PLAT_PURSUIT_EMOJI_ID}>"
                if settings.PLAT_PURSUIT_EMOJI_ID else ""
            )

            if donation.is_anonymous:
                donor_name = 'An Anonymous Hunter'
                mention = f"**{donor_name}**"
            elif profile.is_discord_verified and profile.discord_id:
                donor_name = profile.display_psn_username
                mention = f"<@{profile.discord_id}>"
            else:
                donor_name = profile.display_psn_username
                mention = f"**{donor_name}**"

            description = (
                f"{plat_pursuit_emoji} {mention} just donated **${donation.amount}** "
                f"to the **{donation.fundraiser.name}**!"
            )

            if donation.badge_picks_earned > 0:
                description += (
                    f"\nThey earned **{donation.badge_picks_earned}** badge artwork "
                    f"{'pick' if donation.badge_picks_earned == 1 else 'picks'}!"
                )

            description += (
                f"\n\nWant to help bring our badges to life? "
                f"Donate here: {settings.SITE_URL}/fundraiser/{donation.fundraiser.slug}/"
            )

            embed_data = {
                'title': f"New Donation from {donor_name}!",
                'description': description,
                'color': 0x2ECC71,  # Green for donations
                'footer': {'text': 'Powered by Plat Pursuit | No Trophy Can Hide From Us'},
            }
            payload = {'embeds': [embed_data]}

            if settings.STRIPE_MODE == 'live':
                webhook_url = settings.DISCORD_PLATINUM_WEBHOOK_URL
            else:
                webhook_url = getattr(settings, 'DISCORD_TEST_WEBHOOK_URL', None)

            if webhook_url:
                queue_webhook_send(payload, webhook_url)
        except Exception:
            logger.exception(f"Failed to send Discord notification for donation {donation.id}")
