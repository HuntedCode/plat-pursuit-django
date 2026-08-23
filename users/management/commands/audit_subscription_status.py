"""
Audit subscription status: finds users who are marked as premium in the DB
but whose Stripe/PayPal subscription is not actually active.

Usage:
    python manage.py audit_subscription_status                    # Report only
    python manage.py audit_subscription_status --fix              # Repair (see below)
    python manage.py audit_subscription_status --fix --dry-run    # Preview each repair per row

What --fix does depends on WHY the row failed, and the distinction is the whole point:

- [MISMATCH]: an active subscription EXISTS in djstripe, under a customer linked to this user
  (djstripe's Customer.subscriber, set at checkout) but a DIFFERENT customer id than the user row
  stores. This is the duplicate-customer case: Stripe cannot merge customers, and a checkout that
  minted a second customer leaves our pointer stale. Fix = REPOINT stripe_customer_id and resync
  the tier. Premium is kept. Deactivating here would revoke a paying subscriber, which a 2026-08
  prod audit nearly did to a yearly customer.
- [NO SUB] / [NO CUSTOMER] / expired / unpaid with NO subscription anywhere: fix = revoke premium
  (quietly: 'audit_subscription_status' is not a cancellation event, so no email is sent).

Run `djstripe_sync_models Subscription` first so the local mirror is fresh -- this command only
reads djstripe, never the Stripe API. The weekly cron pairs the two in that order.

After every run the full report is EMAILED to settings.AUDIT_REPORT_EMAIL (an operator email --
outside the email parking, which covered user-facing sends; note IS_BETA forces the dummy mail
backend, so beta runs still send nothing) so the weekly cron confirms itself instead of relying
on someone remembering to read the logs. Empty setting = no email; --no-email
skips it for manual runs. The subject line carries the topline counts, so the inbox row alone
says whether anything needs attention.
"""
import logging
import re

from django.conf import settings
from django.core.mail import send_mail
from django.core.management.base import BaseCommand
from django.utils import timezone
from djstripe.models import Subscription

from users.models import CustomUser
from users.services.subscription_service import SubscriptionService

logger = logging.getLogger('users.management.audit')

_ANSI = re.compile(r'\x1b\[[0-9;]*m')


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
        parser.add_argument(
            '--no-email',
            action='store_true',
            help='Skip the run-report email to settings.AUDIT_REPORT_EMAIL',
        )

    def handle(self, *args, **options):
        fix = options['fix']
        dry_run = options['dry_run']

        # Tee everything written to stdout into the report the email ships. Styled writes
        # carry ANSI codes on a tty; the email strips them.
        self._report_lines = []
        original_write = self.stdout.write

        def tee(msg='', *args_, **kwargs_):
            self._report_lines.append(_ANSI.sub('', str(msg)))
            return original_write(msg, *args_, **kwargs_)

        self.stdout.write = tee

        if dry_run and not fix:
            self.stdout.write(self.style.WARNING('--dry-run has no effect without --fix'))

        self.stdout.write(self.style.MIGRATE_HEADING('\nAuditing Stripe subscribers...'))
        stripe_results = self._audit_stripe(fix=fix, dry_run=dry_run)

        self.stdout.write(self.style.MIGRATE_HEADING('\nAuditing PayPal subscribers...'))
        paypal_results = self._audit_paypal(fix=fix, dry_run=dry_run)

        self.stdout.write(self.style.MIGRATE_HEADING('\nSweeping for orphaned subscriptions...'))
        try:
            orphans = self._audit_orphans()
        except Exception:
            # The sweep failing must not swallow the report email -- a broken run is
            # precisely the one the operator most needs to hear about.
            logger.exception('Orphan sweep failed')
            self.stdout.write(self.style.ERROR('  Orphan sweep FAILED (see logs).'))
            orphans = 0

        # Summary
        self.stdout.write(self.style.MIGRATE_HEADING('\n=== Summary ==='))
        total_ok = stripe_results['ok'] + paypal_results['ok']
        total_grace = stripe_results['grace'] + paypal_results['grace']
        total_needs_fix = stripe_results['needs_fix'] + paypal_results['needs_fix']
        total_fixed = stripe_results['fixed'] + paypal_results['fixed']

        total_mismatch = stripe_results['mismatch'] + paypal_results['mismatch']
        self.stdout.write(f'  OK (active):     {total_ok}')
        self.stdout.write(f'  Grace period:    {total_grace}')
        self.stdout.write(f'  Needs fix:       {total_needs_fix}')
        if total_mismatch:
            self.stdout.write(f'  ...of which customer mismatches: {total_mismatch} '
                              f'(fix REPOINTS these, never deactivates)')
        if orphans:
            self.stdout.write(self.style.ERROR(
                f'  ORPHANS:         {orphans} live subscription(s) with NO user -- cancel by '
                f'hand in the Stripe dashboard (the webhook self-heal prevents new ones)'
            ))
        if fix:
            action = 'Would fix' if dry_run else 'Fixed'
            self.stdout.write(f'  {action}:          {total_fixed}')

        if not options['no_email']:
            self._email_report(
                ok=total_ok, grace=total_grace, needs_fix=total_needs_fix,
                fixed=total_fixed, orphans=orphans, fix=fix, dry_run=dry_run,
            )

    def _audit_orphans(self):
        """Live Stripe subscriptions that belong to NOBODY: their customer is stored on no user
        row AND djstripe links it to no subscriber. The account-deletion race leaves exactly
        this shape behind (both identifiers were webhook-written after the user died), and a
        billing relationship with no user has no site-side cancel path -- it has to be caught
        here and cancelled by hand. Deliberately report-only: cancelling money objects from an
        audit loop is how a mirror hiccup becomes a mass cancellation.

        The subscriber check is what keeps this safe from the DUPLICATE-CUSTOMER case: a paying
        user's live sub can sit under a customer id no user row stores, but djstripe's
        Customer.subscriber still points at them -- that row is a [MISMATCH] to repoint, never
        an orphan.
        """
        # JSONB status filter = sequential scan of the subscription mirror; fine at this
        # site's scale (the table is subscribers, not users), flagged if that ever changes.
        # No select_related: the loop reads only customer_id (the FK column) and stripe_data.
        live = (
            Subscription.objects.filter(
                stripe_data__status__in=['active', 'trialing', 'past_due', 'unpaid', 'incomplete'],
                customer__subscriber__isnull=True,
            )
            .order_by('-created')
        )
        count = 0
        stored_ids = set(
            CustomUser.objects.filter(stripe_customer_id__isnull=False)
            .values_list('stripe_customer_id', flat=True)
        )
        for sub in live:
            if sub.customer_id in stored_ids:
                continue  # a user row still points here; not an orphan
            status = (sub.stripe_data or {}).get('status', 'unknown')
            self.stdout.write(self.style.ERROR(
                f'  [ORPHAN] sub {sub.id} ({status}) under customer {sub.customer_id} -- '
                f'no user row, no djstripe subscriber'
            ))
            count += 1
        if not count:
            self.stdout.write('  No orphaned subscriptions.')
        return count

    def _email_report(self, ok, grace, needs_fix, fixed, orphans, fix, dry_run):
        """Mail the run to the operator so the weekly cron confirms itself. Failures log and
        never fail the audit -- the report is a convenience, the audit is the job."""
        recipient = getattr(settings, 'AUDIT_REPORT_EMAIL', '')
        if not recipient:
            return
        topline = f'OK {ok} / grace {grace} / needs-fix {needs_fix}'
        if fix:
            topline += f' / {"would fix" if dry_run else "fixed"} {fixed}'
        if orphans:
            topline = f'ORPHANS {orphans} / ' + topline
        subject = f'PlatPursuit subscription audit: {topline}'
        body = (
            f'Weekly subscription audit run at {timezone.now():%Y-%m-%d %H:%M} UTC.\n\n'
            + '\n'.join(self._report_lines)
        )
        try:
            send_mail(subject, body, None, [recipient], fail_silently=False)
            self.stdout.write(f'  Report emailed to {recipient}.')
        except Exception:
            logger.exception('Failed to email the audit report')
            self.stdout.write(self.style.ERROR('  Report email FAILED (see logs); audit itself completed.'))

    def _audit_stripe(self, fix=False, dry_run=False):
        results = {'ok': 0, 'grace': 0, 'needs_fix': 0, 'fixed': 0, 'mismatch': 0}

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
                self._resolve_stripe_row(user, results, fix, dry_run)
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
                self._resolve_stripe_row(user, results, fix, dry_run)
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
                self._resolve_stripe_row(user, results, fix, dry_run)
            elif status in ('unpaid', 'incomplete', 'incomplete_expired'):
                self.stdout.write(self.style.ERROR(
                    f'  [NEEDS FIX] {user.email} ({psn}) - {status}'
                ))
                results['needs_fix'] += 1
                self._resolve_stripe_row(user, results, fix, dry_run)
            else:
                self.stdout.write(self.style.WARNING(
                    f'  [UNKNOWN] {user.email} ({psn}) - status={status}'
                ))
                results['needs_fix'] += 1
                # Route through the resolver like every other failing row (it was the one arm
                # that skipped it): a live sub under a sibling customer still gets repointed. The
                # resolver only deactivates when nothing live exists anywhere, and an UNKNOWN
                # status under the stored customer is exactly the "maybe elsewhere" case.
                self._resolve_stripe_row(user, results, fix, dry_run)

        if not found_any:
            self.stdout.write('  No Stripe premium users found.')

        return results

    def _audit_paypal(self, fix=False, dry_run=False):
        # 'mismatch' exists only so the summary can sum both dicts; the duplicate-customer
        # problem is Stripe-specific (PayPal subscription ids live directly on the user row).
        results = {'ok': 0, 'grace': 0, 'needs_fix': 0, 'fixed': 0, 'mismatch': 0}

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

    def _resolve_stripe_row(self, user, results, fix, dry_run):
        """Resolution for a Stripe row whose STORED customer pointer shows no live subscription.

        The stored pointer being dead does not mean the user stopped paying: Stripe happily mints
        duplicate customers, and the live subscription may sit under a sibling customer djstripe
        has linked to this same user. Repointing is the fix there; deactivation is only for rows
        with genuinely no live subscription anywhere. Always REPORTS which one applies, so a
        --fix --dry-run shows the exact action per row before anything runs.
        """
        elsewhere = self._find_subscription_elsewhere(user)
        if elsewhere is not None:
            status = (elsewhere.stripe_data or {}).get('status', 'unknown')
            self.stdout.write(self.style.WARNING(
                f'    [MISMATCH] live sub {elsewhere.id} ({status}) exists under customer '
                f'{elsewhere.customer.id}; user row stores '
                f'{user.stripe_customer_id or "no customer id"}'
            ))
            results['mismatch'] += 1
            if fix:
                if self._repoint(user, elsewhere, dry_run):
                    results['fixed'] += 1
            return
        if fix:
            if self._deactivate(user, 'stripe', dry_run):
                results['fixed'] += 1

    def _find_subscription_elsewhere(self, user):
        """A live subscription under a djstripe customer LINKED to this user (Customer.subscriber,
        set by Customer.get_or_create at checkout) but not the customer id the user row stores."""
        return (
            Subscription.objects.filter(
                customer__subscriber=user,
                stripe_data__status__in=['active', 'past_due', 'trialing'],
            )
            .exclude(customer__id=user.stripe_customer_id or '')
            .select_related('customer')
            .order_by('-created')
            .first()
        )

    def _repoint(self, user, sub, dry_run):
        """Point the user row at the customer that actually holds their subscription, then resync
        tier/denorm through the normal path. Quiet: 'audit_subscription_status' is not an
        activation event, so no welcome email or Discord embed fires (the role re-apply is
        idempotent by design)."""
        old_id = user.stripe_customer_id or 'none'
        new_id = sub.customer.id
        if dry_run:
            self.stdout.write(self.style.WARNING(
                f'    [DRY RUN] Would REPOINT {user.email}: stripe_customer_id {old_id} -> '
                f'{new_id} and resync tier from sub {sub.id}. Premium kept; nothing revoked.'
            ))
            return True
        try:
            user.stripe_customer_id = new_id
            user.save(update_fields=['stripe_customer_id'])
            SubscriptionService.update_user_subscription(user, 'audit_subscription_status')
            # VERIFY the outcome instead of narrating the intent: if the resync path declined to
            # activate (an unhandled status, an unmapped product), premium is now revoked and a
            # '[FIXED] Repointed' line would be a lie in the operator's log.
            user.refresh_from_db()
            if user.premium_tier is None:
                logger.error(f"Repoint of {user.email} resynced to NO premium; investigate")
                self.stdout.write(self.style.ERROR(
                    f'    [ERROR] Repointed {user.email} to {new_id} but the resync REVOKED '
                    f'premium -- not counting as fixed, investigate the subscription status'
                ))
                return False
            self.stdout.write(self.style.SUCCESS(
                f'    [FIXED] Repointed {user.email} to {new_id} and resynced'
            ))
            return True
        except Exception:
            logger.exception(f"Failed to repoint {user.email} during audit")
            self.stdout.write(self.style.ERROR(f'    [ERROR] Failed to repoint {user.email}, skipping'))
            return False

    def _deactivate(self, user, provider, dry_run):
        """
        Deactivate a user's subscription. Returns True on success (or dry-run).
        """
        if dry_run:
            self.stdout.write(self.style.WARNING(
                f'    [DRY RUN] Would DEACTIVATE {user.email}: revoke premium + Discord role '
                f'(quiet -- no cancellation email). No live subscription found anywhere.'
            ))
            return True
        try:
            SubscriptionService.deactivate_subscription(user, provider, 'audit_subscription_status')
            self.stdout.write(self.style.SUCCESS(f'    [FIXED] Deactivated {user.email}'))
            return True
        except Exception:
            logger.exception(f"Failed to deactivate {user.email} during audit")
            self.stdout.write(self.style.ERROR(f'    [ERROR] Failed to deactivate {user.email}, skipping'))
            return False
