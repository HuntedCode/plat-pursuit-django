"""Premium gifts: redeemable codes granting timed premium.

The purchase half (Stripe/PayPal checkout, webhook completion) follows the fundraiser-donation
pattern and lives alongside `redeem`/`mint_comp` here. The LIFECYCLE and what a grant does and does
not touch are documented on the `PremiumGrant` model; the short version is that redemption and
expiry both flow through `SubscriptionService.reconcile_premium`, the one premium truth-writer, so
grants and subscriptions can coexist without either clobbering the other.
"""
import logging
import secrets
import uuid

from dateutil.relativedelta import relativedelta
from django.conf import settings
from django.db import transaction
from django.utils import timezone

from users.constants import GIFT_MONTHS, SUPPORT_TIERS
from users.models import PremiumGrant
from users.services.subscription_service import SubscriptionService

logger = logging.getLogger('users.gifts')

# No 0/O/1/I: these codes get read aloud, screenshotted and retyped.
CODE_ALPHABET = '23456789ABCDEFGHJKLMNPQRSTUVWXYZ'


def _mint_code() -> str:
    """PP-XXXX-XXXX, collision-retried against the unique column.

    32^8 codes make a collision astronomically unlikely; the loop exists so that when the
    astronomically unlikely happens it costs one retry instead of one IntegrityError in a webhook.
    """
    for _ in range(10):
        body = ''.join(secrets.choice(CODE_ALPHABET) for _ in range(8))
        code = f'PP-{body[:4]}-{body[4:]}'
        if not PremiumGrant.objects.filter(code=code).exists():
            return code
    raise RuntimeError('could not mint a unique gift code in 10 attempts')


def gift_price(tier_slug: str, months: int):
    """The gift costs exactly what the subscription costs: monthly price for a month, yearly price
    (ten months) for a year. One price list, no gift-only arithmetic to drift."""
    tier = next((t for t in SUPPORT_TIERS if t['slug'] == tier_slug), None)
    if tier is None:
        raise ValueError(f'Unknown ladder tier: {tier_slug}')
    if months == 1:
        return tier['monthly']
    if months == 12:
        return tier['yearly']
    raise ValueError(f'Gifts are one month or one year, not {months} months')


class GiftService:
    @staticmethod
    def complete_grant(grant: PremiumGrant) -> PremiumGrant:
        """Payment (or comp) confirmed: mint the code, email it to the purchaser.

        IDEMPOTENT on status: the redirect handler and the webhook can both fire (they are designed
        to -- DEBUG completes inline because webhooks cannot reach localhost), and the second caller
        must find nothing to do. One code, one email.
        """
        if grant.status != 'pending':
            return grant

        grant.code = _mint_code()
        grant.status = 'issued'
        grant.completed_at = timezone.now()
        grant.save(update_fields=['code', 'status', 'completed_at'])

        GiftService._send_code_email(grant)
        logger.info("Gift grant %s issued (%s x%smo, %s)",
                    grant.id, grant.tier_slug, grant.months, grant.provider)
        return grant

    @staticmethod
    def redeem(code: str, user) -> PremiumGrant:
        """Turn a code into premium. Raises ValueError with a human-readable reason on any refusal.

        `select_for_update` is the double-redeem guard: two concurrent submissions of the same code
        serialize here, and the second finds status != 'issued'. Redeeming a code you bought
        yourself is allowed -- buying for yourself is a legitimate use of the flat-rate idea this
        grew from.
        """
        normalized = (code or '').strip().upper()
        if not normalized:
            raise ValueError('Enter a code.')

        with transaction.atomic():
            grant = PremiumGrant.objects.select_for_update().filter(code=normalized).first()
            if grant is None:
                raise ValueError('That code does not exist. Check it for typos and try again.')
            if grant.status == 'redeemed':
                raise ValueError('That code has already been redeemed.')
            if grant.status != 'issued':
                raise ValueError('That code is not redeemable.')

            grant.redeemed_by = user
            grant.redeemed_at = timezone.now()
            grant.expires_at = grant.redeemed_at + relativedelta(months=grant.months)
            grant.status = 'redeemed'
            grant.save(update_fields=['redeemed_by', 'redeemed_at', 'expires_at', 'status'])

            # The truth-writer: flips the denorm and opens a provider='gift' SubscriptionPeriod
            # (unless one is already open for another source), so milestone tenure counts gift time.
            SubscriptionService.reconcile_premium(user, provider_hint='gift')

            # The grant confers the Premium Discord role, same as every ladder level.
            profile = getattr(user, 'profile', None)
            if (profile is not None and profile.is_discord_verified and profile.discord_id
                    and settings.DISCORD_PREMIUM_ROLE):
                from trophies.services.discord_roles import notify_bot_role_earned
                transaction.on_commit(
                    lambda p=profile, r=settings.DISCORD_PREMIUM_ROLE: notify_bot_role_earned(p, r)
                )

        logger.info("Gift grant %s redeemed by user %s until %s",
                    grant.id, user.id, grant.expires_at)
        return grant

    @staticmethod
    def mint_comp(tier_slug: str, months: int, staff_user=None, note: str = '') -> PremiumGrant:
        """A code with no payment behind it: staff comps, giveaways, apologies. Identical to a paid
        grant from `issued` onward, which is the whole point of the shared primitive."""
        gift_price(tier_slug, months)   # validates slug + duration; the price itself is unused
        grant = PremiumGrant.objects.create(
            tier_slug=tier_slug,
            months=months,
            amount=0,
            provider='comp',
            provider_transaction_id=f'comp_{uuid.uuid4().hex}',
            purchaser=staff_user,
            notes=note[:255],
        )
        return GiftService.complete_grant(grant)

    @staticmethod
    def expire_due_grants(now=None, dry_run: bool = False) -> dict:
        """The daily sweep. There is no webhook for time passing, so this is the only thing that
        ever moves `redeemed -> expired` -- and the only thing that can flip the denorm back off
        for a grant-holder.

        Each grant reconciles individually so a user with a live subscription (or a second grant)
        keeps premium: reconcile refuses to flip while any source survives. Also voids week-old
        `pending` rows -- abandoned checkouts, pure hygiene.
        """
        now = now or timezone.now()
        counts = {'expired': 0, 'still_premium': 0, 'voided_pending': 0}

        due = PremiumGrant.objects.filter(status='redeemed', expires_at__lte=now)
        for grant in due.select_related('redeemed_by'):
            if dry_run:
                counts['expired'] += 1
                continue
            with transaction.atomic():
                locked = PremiumGrant.objects.select_for_update().get(id=grant.id)
                if locked.status != 'redeemed':      # a concurrent run got here first
                    continue
                locked.status = 'expired'
                locked.save(update_fields=['status'])
                counts['expired'] += 1

                user = locked.redeemed_by
                if user is None:
                    continue
                still = SubscriptionService.reconcile_premium(user)
                if still:
                    counts['still_premium'] += 1
                    continue

                # Premium genuinely ended. Strip the Discord role unless a subscription tier still
                # confers it (mirror of deactivate_subscription's grant-aware guard, other way round).
                from users.constants import PREMIUM_DISCORD_ROLE_TIERS
                profile = getattr(user, 'profile', None)
                if (profile is not None and profile.is_discord_verified and profile.discord_id
                        and settings.DISCORD_PREMIUM_ROLE
                        and user.premium_tier not in PREMIUM_DISCORD_ROLE_TIERS):
                    from trophies.services.discord_roles import notify_bot_role_removed
                    transaction.on_commit(
                        lambda p=profile, r=settings.DISCORD_PREMIUM_ROLE: notify_bot_role_removed(p, r)
                    )
                GiftService._send_expiry_email(locked, user)

        stale = PremiumGrant.objects.filter(
            status='pending', created_at__lt=now - relativedelta(days=7)
        )
        if dry_run:
            counts['voided_pending'] = stale.count()
        else:
            counts['voided_pending'] = stale.update(status='void')

        return counts

    # ------------------------------------------------------------------------------- emails ----
    @staticmethod
    def _send_code_email(grant: PremiumGrant) -> None:
        """The code goes to the PURCHASER -- they decide who gets it. Preference-gated the same as
        every subscription email, but note a suppressed send leaves the code reachable only via
        support, so the suppression is logged loudly."""
        if grant.purchaser is None or not grant.purchaser.email:
            return
        try:
            from core.services.email_service import EmailService
            from users.services.email_preference_service import EmailPreferenceService
            from fundraiser.services.donation_service import DonationService

            user = grant.purchaser
            if not EmailPreferenceService.should_send_email(user, 'subscription_notifications'):
                EmailService.log_suppressed('gift_code', user,
                                            'Your PlatPursuit gift code', metadata={'grant_id': grant.id})
                logger.warning("Gift code email SUPPRESSED by preferences for user %s; "
                               "code retrievable via admin only", user.id)
                return

            context = DonationService._build_email_base_context(user)
            context.update({
                'code': grant.code,
                'tier_name': grant.tier_slug.title(),
                'duration': 'one year' if grant.months == 12 else 'one month',
                'redeem_url': f"{context['site_url']}/support/redeem/?code={grant.code}",
            })
            EmailService.send_html_email(
                subject='Your PlatPursuit gift code',
                to_emails=[user.email],
                template_name='emails/gift_code.html',
                context=context,
                log_email_type='gift_code',
                log_user=user,
            )
        except Exception:
            logger.exception("Failed to send gift code email for grant %s", grant.id)

    @staticmethod
    def _send_expiry_email(grant: PremiumGrant, user) -> None:
        if not user.email:
            return
        try:
            from core.services.email_service import EmailService
            from users.services.email_preference_service import EmailPreferenceService
            from fundraiser.services.donation_service import DonationService

            if not EmailPreferenceService.should_send_email(user, 'subscription_notifications'):
                EmailService.log_suppressed('gift_expired', user, 'Your gift access has ended')
                return
            context = DonationService._build_email_base_context(user)
            context.update({'tier_name': grant.tier_slug.title()})
            EmailService.send_html_email(
                subject='Your PlatPursuit gift access has ended',
                to_emails=[user.email],
                template_name='emails/gift_expired.html',
                context=context,
                log_email_type='gift_expired',
                log_user=user,
            )
        except Exception:
            logger.exception("Failed to send gift expiry email for grant %s", grant.id)
