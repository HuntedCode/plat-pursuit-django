"""
Audit subscription status: finds users who are marked as premium in the DB
but whose Stripe/PayPal subscription is not actually active.

Usage:
    python manage.py audit_subscription_status              # Report only
    python manage.py audit_subscription_status --fix        # Revoke for unpaid/no-sub users
    python manage.py audit_subscription_status --dry-run    # Preview what --fix would do
"""
import logging
from django.core.management.base import BaseCommand
from django.utils import timezone
from djstripe.models import Subscription

from users.models import CustomUser
from users.services.subscription_service import SubscriptionService

logger = logging.getLogger('users.management.audit')


class Command(BaseCommand):
    help = 'Audit users with premium_tier set against actual Stripe/PayPal subscription status'

    def add_arguments(self, parser):
        parser.add_argument(
            '--fix',
            action='store_true',
            help='Revoke premium for users with unpaid or missing subscriptions',
        )
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Preview what --fix would do without making changes',
        )

    def handle(self, *args, **options):
        fix = options['fix']
        dry_run = options['dry_run']

        if dry_run and not fix:
            self.stdout.write(self.style.WARNING('--dry-run has no effect without --fix'))

        self.stdout.write(self.style.MIGRATE_HEADING('\nAuditing Stripe subscribers...'))
        stripe_results = self._audit_stripe(fix=fix, dry_run=dry_run)

        self.stdout.write(self.style.MIGRATE_HEADING('\nAuditing PayPal subscribers...'))
        paypal_results = self._audit_paypal(fix=fix, dry_run=dry_run)

        # Summary
        self.stdout.write(self.style.MIGRATE_HEADING('\n=== Summary ==='))
        total_ok = stripe_results['ok'] + paypal_results['ok']
        total_grace = stripe_results['grace'] + paypal_results['grace']
        total_needs_fix = stripe_results['needs_fix'] + paypal_results['needs_fix']
        total_fixed = stripe_results['fixed'] + paypal_results['fixed']

        self.stdout.write(f'  OK (active):     {total_ok}')
        self.stdout.write(f'  Grace period:    {total_grace}')
        self.stdout.write(f'  Needs fix:       {total_needs_fix}')
        if fix:
            action = 'Would fix' if dry_run else 'Fixed'
            self.stdout.write(f'  {action}:          {total_fixed}')

    def _audit_stripe(self, fix=False, dry_run=False):
        results = {'ok': 0, 'grace': 0, 'needs_fix': 0, 'fixed': 0}

        stripe_users = CustomUser.objects.filter(
            premium_tier__isnull=False,
            subscription_provider='stripe',
        ).select_related('profile')

        found_any = False
        for user in stripe_users:
            found_any = True
            psn = user.profile.psn_username if hasattr(user, 'profile') else 'N/A'

            if not user.stripe_customer_id:
                self.stdout.write(self.style.ERROR(
                    f'  [NO CUSTOMER] {user.email} ({psn}) - tier={user.premium_tier}, no stripe_customer_id'
                ))
                results['needs_fix'] += 1
                if fix:
                    if self._deactivate(user, 'stripe', dry_run):
                        results['fixed'] += 1
                continue

            # Check subscription status: prefer active/past_due/trialing, fall back to most recent
            sub = Subscription.objects.filter(
                customer__id=user.stripe_customer_id,
                stripe_data__status__in=['active', 'past_due', 'trialing'],
            ).first()
            if not sub:
                sub = Subscription.objects.filter(
                    customer__id=user.stripe_customer_id
                ).order_by('-created').first()

            if not sub:
                self.stdout.write(self.style.ERROR(
                    f'  [NO SUB] {user.email} ({psn}) - tier={user.premium_tier}, no subscription found in djstripe'
                ))
                results['needs_fix'] += 1
                if fix:
                    if self._deactivate(user, 'stripe', dry_run):
                        results['fixed'] += 1
                continue

            status = (sub.stripe_data or {}).get('status', 'unknown')

            if status == 'active':
                self.stdout.write(self.style.SUCCESS(f'  [OK] {user.email} ({psn}) - {status}'))
                results['ok'] += 1
            elif status == 'past_due':
                self.stdout.write(self.style.WARNING(f'  [GRACE] {user.email} ({psn}) - {status} (Stripe retrying)'))
                results['grace'] += 1
            elif status == 'canceled':
                # Check grace period
                canceled_data = sub.stripe_data or {}
                period_end_ts = canceled_data.get('current_period_end')
                if period_end_ts:
                    from datetime import datetime
                    period_end = datetime.fromtimestamp(period_end_ts, tz=timezone.utc)
                    if period_end > timezone.now():
                        self.stdout.write(self.style.WARNING(
                            f'  [GRACE] {user.email} ({psn}) - canceled, grace until {period_end}'
                        ))
                        results['grace'] += 1
                        continue

                self.stdout.write(self.style.ERROR(
                    f'  [NEEDS FIX] {user.email} ({psn}) - {status}, grace period expired'
                ))
                results['needs_fix'] += 1
                if fix:
                    if self._deactivate(user, 'stripe', dry_run):
                        results['fixed'] += 1
            elif status in ('unpaid', 'incomplete', 'incomplete_expired'):
                self.stdout.write(self.style.ERROR(
                    f'  [NEEDS FIX] {user.email} ({psn}) - {status}'
                ))
                results['needs_fix'] += 1
                if fix:
                    if self._deactivate(user, 'stripe', dry_run):
                        results['fixed'] += 1
            else:
                self.stdout.write(self.style.WARNING(
                    f'  [UNKNOWN] {user.email} ({psn}) - status={status}'
                ))
                results['needs_fix'] += 1

        if not found_any:
            self.stdout.write('  No Stripe premium users found.')

        return results

    def _audit_paypal(self, fix=False, dry_run=False):
        results = {'ok': 0, 'grace': 0, 'needs_fix': 0, 'fixed': 0}

        paypal_users = CustomUser.objects.filter(
            premium_tier__isnull=False,
            subscription_provider='paypal',
        ).select_related('profile')

        found_any = False
        for user in paypal_users:
            found_any = True
            psn = user.profile.psn_username if hasattr(user, 'profile') else 'N/A'

            if not user.paypal_subscription_id:
                self.stdout.write(self.style.ERROR(
                    f'  [NO SUB ID] {user.email} ({psn}) - tier={user.premium_tier}, no paypal_subscription_id'
                ))
                results['needs_fix'] += 1
                if fix:
                    if self._deactivate(user, 'paypal', dry_run):
                        results['fixed'] += 1
                continue

            if user.paypal_cancel_at and user.paypal_cancel_at < timezone.now():
                self.stdout.write(self.style.ERROR(
                    f'  [EXPIRED] {user.email} ({psn}) - cancel_at={user.paypal_cancel_at} (past)'
                ))
                results['needs_fix'] += 1
                if fix:
                    if self._deactivate(user, 'paypal', dry_run):
                        results['fixed'] += 1
            elif user.paypal_cancel_at:
                self.stdout.write(self.style.WARNING(
                    f'  [GRACE] {user.email} ({psn}) - cancelling, expires {user.paypal_cancel_at}'
                ))
                results['grace'] += 1
            else:
                self.stdout.write(self.style.SUCCESS(f'  [OK] {user.email} ({psn}) - active'))
                results['ok'] += 1

        if not found_any:
            self.stdout.write('  No PayPal premium users found.')

        return results

    def _deactivate(self, user, provider, dry_run):
        """
        Deactivate a user's subscription. Returns True on success (or dry-run).
        """
        if dry_run:
            self.stdout.write(self.style.WARNING(f'    [DRY RUN] Would deactivate {user.email}'))
            return True
        try:
            SubscriptionService.deactivate_subscription(user, provider, 'audit_subscription_status')
            self.stdout.write(self.style.SUCCESS(f'    [FIXED] Deactivated {user.email}'))
            return True
        except Exception:
            logger.exception(f"Failed to deactivate {user.email} during audit")
            self.stdout.write(self.style.ERROR(f'    [ERROR] Failed to deactivate {user.email}, skipping'))
            return False
