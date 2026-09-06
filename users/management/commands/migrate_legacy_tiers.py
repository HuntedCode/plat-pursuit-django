"""
Migrate the three grandfathered tiers onto the supporter ladder.

The legacy tiers (`premium_monthly` $3.99/mo, `premium_yearly` $39.99/yr, `supporter` $20/mo) were
never withdrawn from EXISTING subscribers when the ladder shipped in 2026-08, only from the
storefront. This moves the remaining holders onto the ladder proper, so the legacy layer can finally
be deleted rather than maintained forever.

Usage:
    python manage.py migrate_legacy_tiers                        # report only, touches nothing
    python manage.py migrate_legacy_tiers --fix --live-ok        # migrate (live mode needs the flag)
    python manage.py migrate_legacy_tiers --provider stripe      # one arm

There is deliberately no `--dry-run`: a bare run already previews every row, including the real
processor state, so a second preview mode would be a flag that could only rot.

THE TWO ARMS ARE DIFFERENT ON PURPOSE, and the asymmetry is the whole design:

- STRIPE is a real billing migration. The subscription is swapped onto the actual ladder price
  ($3.99 -> $4.00, $39.99 -> $40.00, $20 -> $20), with `proration_behavior='none'` so nothing is
  charged or credited now and the new price applies from the next renewal. The billing cycle anchor
  is left alone, so no renewal date moves.

- PAYPAL is an adoption, not a migration: no processor call is made beyond reading the plan. PayPal's
  revise endpoint requires the SUBSCRIBER to log in and re-approve whenever the billing amount
  changes, so a penny increase is not something we can do on their behalf, and cancel-and-resubscribe
  would fire the farewell email and risk losing a paying member over one cent. They keep their legacy
  plan and price; `LEGACY_PLAN_ADOPTION` makes that plan read as `backer` from here on.

WHY THIS IS QUIET (no email, no Discord embed, no interruption):

NEITHER arm calls `activate_subscription`, and neither re-derives the tier from the djstripe mirror.
Both write `premium_tier` directly and then call `reconcile_premium`, the one premium truth-writer.
Each arm has its own reason for avoiding the service's usual entry points, and both are load-bearing:

- STRIPE avoids `sync_from_stripe_data` + `update_user_subscription` because this command's SDK
  calls run at stripe-python 14's pinned API version while the mirror is otherwise written by
  webhooks at the ACCOUNT's version, and those shapes differ (a prod row checked 2026-09-06 has
  `plan` but no `current_period_end`). Writing one version's payload over another's is how a field
  silently vanishes from a row, and `update_user_subscription`'s four fall-throughs to
  `deactivate_subscription` are a risk taken for nothing when the target slug is already known.
  See `_write_tier`.
- PAYPAL avoids `activate_subscription` because it clears `paypal_cancel_at` for
  `provider='paypal'`, which would resurrect a member who is mid-cancellation.

So nothing announces, and no Discord role is pushed on either arm. That is fine: every legacy tier
and its target grant the same role, except `supporter` -> `sponsor`, whose role handling is a known
and accepted inconsistency (see users/constants.py).

`reconcile_premium` leaves an open `SubscriptionPeriod` untouched while premium stays true, so tenure
and the `premium_months` milestone survive intact. `Profile.display_mark` does not change for a
single user either: `worn_supporter_level` already collapsed the legacy slugs onto their ladder
level, so every name on the site renders identically before and after.

VERIFY, NEVER NARRATE. Both arms confirm the tier actually landed before printing success, the lesson
`audit_subscription_status._repoint` already encodes: after a Stripe modify the money has already
moved, so a green line the operator's eye slides past is the worst possible outcome.

RE-RUNNABLE, INCLUDING AFTER A CRASH. The dangerous window is between the Stripe modify returning and
the local tier write. A run that dies there leaves the member billed at the ladder price on a legacy
slug, which `audit_subscription_status` cannot see (it branches on subscription STATUS, never
comparing the tier to the product). So a subscription already sitting on the target price is NOT
skipped: it writes the tier anyway, which is what makes a re-run the real repair.
"""
import logging

import stripe
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.db.models import F
from djstripe.models import Subscription

from users.constants import (
    INTERVAL_TO_STRIPE,
    LEGACY_PLAN_ADOPTION,
    LEGACY_TIER_LEVEL_MAP,
)
from users.models import CustomUser
from users.services.subscription_service import SubscriptionService

logger = logging.getLogger('users.management.migrate_legacy_tiers')

#: The legacy tiers this command moves. Read from the PRESENTATION map, which is the codebase's
#: established answer to "what is a legacy tier" (`marks.worn_level_dict`, the supporter wall, the
#: role backfill migration all key off it). Deriving selection from anywhere else would let a map
#: edit silently stop selecting a tier.
LEGACY_TIERS = sorted(LEGACY_TIER_LEVEL_MAP)

#: Stripe statuses a subscription may be modified from. `past_due` is deliberately absent: that
#: member's payment is already failing and Stripe is mid-retry, so changing their price underneath
#: that is the worst possible moment. `canceled` cannot be modified at all (Stripe raises).
MODIFIABLE_STATUSES = ('active', 'trialing')

#: Reverse of INTERVAL_TO_STRIPE, for reading a live price's recurrence back into our own key.
STRIPE_TO_INTERVAL = {v: k for k, v in INTERVAL_TO_STRIPE.items()}


class Command(BaseCommand):
    help = 'Migrate grandfathered premium tiers onto the supporter ladder'

    def add_arguments(self, parser):
        parser.add_argument(
            '--fix',
            action='store_true',
            help='Perform the migration (without this, the command only reports)',
        )
        parser.add_argument(
            '--live-ok',
            action='store_true',
            help='Required to migrate when STRIPE_MODE is live (real subscriptions, real members)',
        )
        parser.add_argument(
            '--provider',
            choices=['stripe', 'paypal', 'all'],
            default='all',
            help='Which arm to run (default: all)',
        )

    def handle(self, *args, **options):
        fix = options['fix']
        provider = options['provider']

        # THE LIVE GATE, matching `bootstrap_support_skus`. That command gates the CREATION of SKUs;
        # this one modifies live subscriptions of paying members, which is strictly more dangerous,
        # and it is copy-pasteable straight off the deploy checklist. The api-key check is the same
        # belt bootstrap uses: an sk_live secret in a test-mode env var still trips it.
        if fix and provider in ('stripe', 'all'):
            key = getattr(stripe, 'api_key', '') or ''
            if (settings.STRIPE_MODE == 'live' or key.startswith('sk_live')) and not options['live_ok']:
                raise CommandError(
                    'Refusing to migrate live Stripe subscriptions without --live-ok. '
                    'Run without --fix first and read the report.'
                )

        users = list(
            CustomUser.objects.filter(premium_tier__in=LEGACY_TIERS)
            .select_related('profile')
            .order_by('id')
        )

        self.stdout.write(self.style.MIGRATE_HEADING('\nLegacy tier migration'))
        self.stdout.write(
            f'  Stripe mode: {settings.STRIPE_MODE} | PayPal mode: {getattr(settings, "PAYPAL_MODE", "?")}'
        )
        if not users:
            self.stdout.write(self.style.SUCCESS(
                '\n  No users hold a legacy tier. Nothing to migrate.\n'
            ))
            return

        self._report_population(users)

        results = {'migrated': 0, 'would': 0, 'skipped': 0, 'already': 0, 'errors': 0}

        stripe_users = [u for u in users if u.subscription_provider == 'stripe']
        paypal_users = [u for u in users if u.subscription_provider == 'paypal']
        stranded = [u for u in users if u.subscription_provider not in ('stripe', 'paypal')]

        # PayPal FIRST, matching the deploy checklist: it makes no billing change, so it is the
        # reversible half and the right thing to have already succeeded if the Stripe arm goes wrong.
        if provider in ('paypal', 'all'):
            self.stdout.write(self.style.MIGRATE_HEADING('\nPayPal (adoption, no billing change)'))
            if not paypal_users:
                self.stdout.write('  No legacy PayPal subscribers.')
            for user in paypal_users:
                self._migrate_paypal(user, fix, results)

        if provider in ('stripe', 'all'):
            self.stdout.write(self.style.MIGRATE_HEADING('\nStripe (price swap)'))
            if not stripe_users:
                self.stdout.write('  No legacy Stripe subscribers.')
            for user in stripe_users:
                self._migrate_stripe(user, fix, results)

        # Only under `all`: a single-arm run reporting the other arm's rows is cross-arm noise.
        if stranded and provider == 'all':
            # Never silently skipped: a legacy tier with no provider is a row that needs a human,
            # and dropping it out of both arms is how it would be missed forever.
            self.stdout.write(self.style.MIGRATE_HEADING('\nNeeds a human'))
            for user in stranded:
                self.stdout.write(self.style.ERROR(
                    f'  [NO PROVIDER] {user.email} ({self._psn(user)}) - tier={user.premium_tier}, '
                    f'subscription_provider={user.subscription_provider!r}'
                ))
                results['errors'] += 1

        self._summary(results, fix)

    # ── Reporting ────────────────────────────────────────────────────────

    @staticmethod
    def _psn(user):
        profile = getattr(user, 'profile', None)
        return getattr(profile, 'psn_username', None) or 'N/A'

    def _report_population(self, users):
        """The counts, which are the whole point of a bare run: how many, on what, where.

        No interval column: the billing cycle is read per subscriber from their live price, never
        assumed from the tier name, so printing an assumed one here would be the only place this
        command guesses at billing.
        """
        self.stdout.write(self.style.MIGRATE_HEADING('\nPopulation'))
        tally = {}
        for user in users:
            key = (user.premium_tier, user.subscription_provider or 'none')
            tally[key] = tally.get(key, 0) + 1
        for (tier, prov), count in sorted(tally.items()):
            self.stdout.write(
                f'  {count:>4}  {tier} via {prov}  ->  {LEGACY_TIER_LEVEL_MAP.get(tier, "?")}'
            )
        self.stdout.write(f'  {len(users):>4}  TOTAL')

    def _summary(self, results, fix):
        self.stdout.write(self.style.MIGRATE_HEADING('\nSummary'))
        self.stdout.write(
            f"  migrated={results['migrated']}  would-migrate={results['would']}  "
            f"already={results['already']}  skipped={results['skipped']}  "
            f"errors={results['errors']}"
        )
        if not fix:
            self.stdout.write(self.style.WARNING(
                '  Report only. Re-run with --fix to migrate.'
            ))
        elif results['errors'] or results['skipped']:
            self.stdout.write(self.style.WARNING(
                '  Done, but rows above need attention. A verify re-run will still list them.'
            ))
        else:
            self.stdout.write(self.style.SUCCESS(
                '  Done. Verify by re-running this command: it should report no legacy holders.'
            ))
        self.stdout.write('')

    # ── Stripe arm ───────────────────────────────────────────────────────

    def _find_mirror(self, customer_id):
        """The djstripe row this user pays through.

        Ordered, because djstripe's Subscription has no Meta.ordering and an unordered `.first()`
        on a customer with more than one subscription returns an arbitrary row. Prefers a live
        subscription but falls back to the most recent of any status, so a `past_due` or `canceled`
        member reaches the status guard below and is reported for what they are, instead of being
        reported as a missing subscription and sending the operator after a phantom sync problem.
        """
        subs = Subscription.objects.filter(customer__id=customer_id).order_by(
            F('created').desc(nulls_last=True), '-djstripe_created')
        return subs.filter(stripe_data__status__in=MODIFIABLE_STATUSES).first() or subs.first()

    def _migrate_stripe(self, user, fix, results):
        label = f'{user.email} ({self._psn(user)})'
        target_slug = LEGACY_TIER_LEVEL_MAP[user.premium_tier]

        if not user.stripe_customer_id:
            self.stdout.write(self.style.ERROR(
                f'  [NO CUSTOMER] {label} - tier={user.premium_tier}, no stripe_customer_id'
            ))
            results['errors'] += 1
            return

        mirrored = self._find_mirror(user.stripe_customer_id)
        if not mirrored:
            self.stdout.write(self.style.ERROR(
                f'  [NO SUB] {label} - no subscription under this customer. The likely cause is '
                f'the duplicate-customer case: run `audit_subscription_status --fix` to repoint '
                f'stripe_customer_id, then re-run this command.'
            ))
            results['errors'] += 1
            return

        # The mirror identifies the subscription; Stripe itself is asked for the authoritative
        # status, item id and price. Reading those off a stale mirror is how you modify the wrong
        # item or reprice someone mid-cancellation.
        try:
            sub = stripe.Subscription.retrieve(mirrored.id)
        except stripe.error.StripeError as exc:
            self.stdout.write(self.style.ERROR(f'  [STRIPE ERROR] {label} - retrieve failed: {exc}'))
            logger.exception('migrate_legacy_tiers: retrieve failed for %s', user.email)
            results['errors'] += 1
            return

        if sub.get('status') not in MODIFIABLE_STATUSES:
            self.stdout.write(self.style.WARNING(
                f'  [SKIP status] {label} - subscription is {sub.get("status")}; leave it alone. '
                f'(If they ARE still paying, this may be a dead row under a duplicate customer: '
                f'run `audit_subscription_status --fix` to repoint, then re-run.)'
            ))
            results['skipped'] += 1
            return

        # BOTH cancellation shapes. A portal cancel leaves the sub active with
        # `cancel_at_period_end`; a cancel scheduled for a specific date sets `cancel_at` ALONE
        # (`membership_status` documents the distinction). Checking only the first would reprice a
        # member who has already told us they are leaving.
        if sub.get('cancel_at_period_end') or sub.get('cancel_at'):
            self.stdout.write(self.style.WARNING(
                f'  [SKIP cancelling] {label} - a cancellation is scheduled; leave it alone'
            ))
            results['skipped'] += 1
            return

        items = (sub.get('items') or {}).get('data') or []
        if len(items) != 1:
            # Every subscription this site creates has exactly one item. More than one means
            # something we did not build, and guessing which to reprice could double a charge.
            self.stdout.write(self.style.ERROR(
                f'  [BLOCKED items] {label} - subscription has {len(items)} items, expected 1'
            ))
            results['errors'] += 1
            return

        item = items[0]
        price = item.get('price') or {}
        current_price = price.get('id')

        # THE INTERVAL COMES FROM THE SUBSCRIPTION, never from the tier name. Assuming it was the
        # one place this command took billing on faith: a legacy row whose tier and real price
        # disagree about the cycle would be swapped across intervals, and Stripe RESETS the billing
        # anchor on an interval change and invoices immediately. `proration_behavior='none'`
        # suppresses proration line items; it does not suppress that.
        stripe_interval = (price.get('recurring') or {}).get('interval')
        interval = STRIPE_TO_INTERVAL.get(stripe_interval)
        if not interval:
            self.stdout.write(self.style.ERROR(
                f'  [BLOCKED interval] {label} - price {current_price} recurs '
                f'{stripe_interval!r}, which is neither monthly nor yearly'
            ))
            results['errors'] += 1
            return

        # Confirm we are repricing what we think we are. Without this the arm would swap ANY active
        # subscription onto the target, so a user whose `premium_tier` has drifted from their real
        # subscription (the exact class the audit command exists for) could be downgraded.
        current_tier = (SubscriptionService.get_tier_from_product_id(price.get('product'))
                        or SubscriptionService.resolve_tier_from_ladder_price(current_price))
        if current_tier not in LEGACY_TIERS and current_tier != target_slug:
            self.stdout.write(self.style.ERROR(
                f'  [BLOCKED price] {label} - tier says {user.premium_tier} but the subscription '
                f'is on {current_price}, which resolves to {current_tier!r} (expected a legacy '
                f'tier, or {target_slug} from an interrupted run). Not repricing.'
            ))
            results['errors'] += 1
            return

        target_price = SubscriptionService.resolve_ladder_price_id(
            target_slug, interval, settings.STRIPE_MODE == 'live'
        )
        if not target_price:
            self.stdout.write(self.style.ERROR(
                f'  [NO PRICE] {label} - {target_slug}/{interval} is not configured for this mode'
            ))
            results['errors'] += 1
            return

        already = current_price == target_price
        if not fix:
            verb = 'ALREADY (tier would be re-derived)' if already else 'WOULD SWAP'
            self.stdout.write(self.style.WARNING(
                f'  [{verb}] {label} - {user.premium_tier} {current_price} '
                f'-> {target_slug}/{interval} {target_price}'
            ))
            results['already' if already else 'would'] += 1
            return

        if already:
            # NOT a skip. A run that died between the modify and the tier write leaves the member
            # billed at the ladder price on a legacy slug, and `audit_subscription_status` cannot
            # see that (it branches on subscription status, never comparing tier to product). So
            # the price already matching is exactly the state a re-run must REPAIR, not step over.
            self.stdout.write(f'  [ALREADY] {label} - on {target_price}; repairing the tier')
            self._write_tier(user, label, target_slug, target_price, results, bucket='already')
            return

        try:
            # THE ITEM ID IS LOAD-BEARING. `items=[{'price': ...}]` without the existing item's id
            # does not replace that item, it ADDS a second one, and the member is then billed for
            # both. This is the one mistake in this command that costs real money.
            stripe.Subscription.modify(
                mirrored.id,
                items=[{'id': item['id'], 'price': target_price}],
                proration_behavior='none',
            )
        except stripe.error.StripeError as exc:
            self.stdout.write(self.style.ERROR(f'  [STRIPE ERROR] {label} - modify failed: {exc}'))
            logger.exception('migrate_legacy_tiers: modify failed for %s', user.email)
            results['errors'] += 1
            return

        logger.info(
            'migrate_legacy_tiers: %s swapped %s -> %s/%s (%s)',
            user.email, current_price, target_slug, interval, target_price,
        )
        self._write_tier(user, label, target_slug, target_price, results, bucket='migrated')

    def _write_tier(self, user, label, target_slug, target_price, results, bucket):
        """Write the tier we already know, the same way the PayPal arm does. Then VERIFY.

        DELIBERATELY NOT `sync_from_stripe_data` + `update_user_subscription`. Three reasons, in
        order of how much they would cost if ignored:

        1. IT WOULD MIX API VERSIONS IN ONE TABLE. `sync_from_stripe_data` stores the response
           verbatim, and this command's SDK calls run at stripe-python 14's pinned
           `2025-12-15.clover`, while every other row in that table was written by a webhook at the
           ACCOUNT's version. Those shapes differ: a prod row inspected 2026-09-06 carries `plan` but
           has NO `current_period_end` (the period moved onto the subscription item), and four
           production call sites still read that key. Writing a differently-versioned payload over a
           mirror row is how a field silently disappears from it.
        2. `update_user_subscription` has four fall-throughs to `deactivate_subscription`. Every one
           of them would null the tier, close the period carrying the member's tenure, and pull their
           Discord role, moments after their card was moved onto the new price. Re-deriving is a
           gamble taken for no gain here, because the target slug is already known.
        3. A lost or delayed webhook would otherwise stall the migration.

        So the mirror is left alone for the `customer.subscription.updated` webhook that this
        command's own modify triggers, which arrives in the account's version and is the only writer
        that table should have.

        No Discord role is pushed on this path, matching the PayPal arm. Every legacy tier and its
        target grant the same role anyway, except `supporter` -> `sponsor`, whose role handling is a
        known and accepted inconsistency (see users/constants.py).
        """
        was = user.premium_tier
        with transaction.atomic():
            user.premium_tier = target_slug
            user.save(update_fields=['premium_tier'])
            # No provider_hint: already premium and staying premium, so reconcile deliberately
            # leaves the open SubscriptionPeriod alone and tenure carries across untouched.
            SubscriptionService.reconcile_premium(user)

        user.refresh_from_db(fields=['premium_tier'])
        if user.premium_tier != target_slug:
            # VERIFY THE OUTCOME INSTEAD OF NARRATING THE INTENT, the lesson
            # `audit_subscription_status._repoint` already encodes. The money has moved by this
            # point, so a green line the operator's eye slides past is the worst possible outcome.
            logger.error(
                'migrate_legacy_tiers: %s expected %s after write, got %r',
                user.email, target_slug, user.premium_tier,
            )
            self.stdout.write(self.style.ERROR(
                f'  [TIER WRONG] {label} - expected {target_slug}, got {user.premium_tier!r}. '
                f'The price HAS moved. Investigate before continuing.'
            ))
            results['errors'] += 1
            return

        verb = 'ALREADY' if bucket == 'already' else 'SWAPPED'
        self.stdout.write(self.style.SUCCESS(
            f'  [{verb}] {label} - {was} -> {user.premium_tier} on {target_price}'
        ))
        results[bucket] += 1

    # ── PayPal arm ───────────────────────────────────────────────────────

    def _migrate_paypal(self, user, fix, results):
        """Adoption: rewrite the tier, change nothing on PayPal.

        The plan is cross-checked against LEGACY_PLAN_ADOPTION, and a snapshot the cache cannot
        supply BLOCKS the row rather than proceeding on trust. That is a deliberate reversal: the
        design's escape hatch for an unexpected plan (a PayPal `supporter`, whose plan is not in the
        adoption map and would drift back to `supporter` on its next re-activation) only works if
        the check actually runs, and `get_cached_subscription_snapshot` returns None on any PayPal
        failure AND for 60s afterwards. A population of eight can afford to wait for PayPal.
        """
        from users.services.paypal_service import PayPalService

        label = f'{user.email} ({self._psn(user)})'
        target_slug = LEGACY_TIER_LEVEL_MAP[user.premium_tier]

        if not user.paypal_subscription_id:
            self.stdout.write(self.style.ERROR(
                f'  [NO SUB ID] {label} - tier={user.premium_tier}, no paypal_subscription_id'
            ))
            results['errors'] += 1
            return

        snapshot = PayPalService.get_cached_subscription_snapshot(user.paypal_subscription_id)
        plan_id = (snapshot or {}).get('plan_id')
        if not plan_id:
            self.stdout.write(self.style.ERROR(
                f'  [UNVERIFIED] {label} - PayPal did not return a plan (outage, or the 60s '
                f'failure marker). Not migrating on trust; re-run when PayPal answers.'
            ))
            results['errors'] += 1
            return
        if plan_id not in LEGACY_PLAN_ADOPTION:
            self.stdout.write(self.style.ERROR(
                f'  [UNKNOWN PLAN] {label} - tier={user.premium_tier} but PayPal reports plan '
                f'{plan_id}, which is not an adopted legacy plan. Left alone.'
            ))
            results['errors'] += 1
            return

        note = '  (cancelling, still in paid time)' if user.paypal_cancel_at else ''

        if not fix:
            self.stdout.write(self.style.WARNING(
                f'  [WOULD ADOPT] {label} - {user.premium_tier} -> {target_slug}{note}'
            ))
            results['would'] += 1
            return

        was = user.premium_tier
        with transaction.atomic():
            user.premium_tier = target_slug
            user.save(update_fields=['premium_tier'])
            # The one premium truth-writer. No provider_hint: the user is already premium and stays
            # premium, so reconcile deliberately leaves their open SubscriptionPeriod alone and
            # tenure carries across the migration untouched.
            SubscriptionService.reconcile_premium(user)

        user.refresh_from_db(fields=['premium_tier'])
        if user.premium_tier != target_slug:
            self.stdout.write(self.style.ERROR(
                f'  [TIER WRONG] {label} - expected {target_slug}, got {user.premium_tier!r}'
            ))
            results['errors'] += 1
            return

        # The OLD tier is logged because the adoption is lossy: `premium_monthly` and
        # `premium_yearly` both collapse to `backer`, and after this write the distinction survives
        # only on the PayPal plan id.
        self.stdout.write(self.style.SUCCESS(
            f'  [ADOPTED] {label} - {was} -> {target_slug}, still billing on plan {plan_id}{note}'
        ))
        logger.info(
            'migrate_legacy_tiers: %s adopted %s -> %s (PayPal plan %s left in place)',
            user.email, was, target_slug, plan_id,
        )
        results['migrated'] += 1
