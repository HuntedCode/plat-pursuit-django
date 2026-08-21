"""
Bootstrap the supporter ladder's SKUs on the payment processors.

Creates (idempotently) the six Stripe products + twelve prices and the six PayPal catalog
products + twelve plans that back SUPPORT_TIERS, then prints ready-to-paste constants blocks.
It NEVER edits source: the paste into users/constants.py is a deliberate human step, because
the ids differ per environment and the diff is the review.

Usage:
    python manage.py bootstrap_support_skus                     # both providers, current mode
    python manage.py bootstrap_support_skus --provider stripe
    python manage.py bootstrap_support_skus --dry-run           # report, create nothing
    python manage.py bootstrap_support_skus --live-ok           # required when a mode is live

⚠ LIVE MODE IS GATED ON PURPOSE. Stripe/PayPal webhooks fan out to every registered endpoint,
and the production `main` build deactivates a subscriber whose product id it does not recognise.
Live ladder SKUs may therefore only be created at the rebuild cutover, never before -- the
--live-ok flag exists so that cannot happen by muscle memory. Full runbook:
docs/guides/support-skus.md.

Idempotency anchors (safe to re-run any number of times):
- Stripe products:  metadata.pp_ladder_slug = {slug}
- Stripe prices:    lookup_key = pp_ladder_{slug}_{interval}   (Stripe-unique)
- PayPal products:  caller-chosen id PP-LADDER-{SLUG}          (GET 404 -> create)
- PayPal plans:     name = pp_ladder_{slug}_{interval}, listed per product and matched,
                    plus a deterministic PayPal-Request-Id belt on create

Every Stripe object touched is synced into djstripe immediately: checkout does
`Price.objects.get(id=...)`, so an unsynced price 500s the moment SUPPORT_TIERS_ARE_PLACEHOLDERS
flips False.
"""
import logging

import requests
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from users.constants import SUPPORT_TIERS

logger = logging.getLogger('users.management.bootstrap_skus')

# (constants-key interval, Stripe recurring interval, PayPal interval_unit)
INTERVALS = [('monthly', 'month', 'MONTH'), ('yearly', 'year', 'YEAR')]


def _lookup_key(slug: str, interval: str) -> str:
    return f'pp_ladder_{slug}_{interval}'


def _paypal_product_id(slug: str) -> str:
    return f'PP-LADDER-{slug.upper()}'


class Command(BaseCommand):
    help = 'Idempotently create the supporter ladder SKUs on Stripe and/or PayPal'

    def add_arguments(self, parser):
        parser.add_argument(
            '--provider',
            choices=['stripe', 'paypal', 'all'],
            default='all',
            help='Which processor to bootstrap (default: all)',
        )
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Report what would be created without creating anything',
        )
        parser.add_argument(
            '--live-ok',
            action='store_true',
            help='Required to run against a live processor mode (see the module docstring)',
        )

    def handle(self, *args, **options):
        provider = options['provider']
        dry_run = options['dry_run']

        do_stripe = provider in ('stripe', 'all')
        do_paypal = provider in ('paypal', 'all')

        # The live gate. Checked per provider actually being touched, so a sandbox PayPal
        # bootstrap is not blocked by Stripe happening to be in live mode on the same box.
        if not options['live_ok'] and not dry_run:
            # Belt on the label: a live key pasted into the test env var sails past STRIPE_MODE,
            # and the blast radius here is prod deactivating paying subscribers. Check both.
            import stripe as stripe_mod
            stripe_is_live = (settings.STRIPE_MODE == 'live'
                              or str(stripe_mod.api_key or '').startswith('sk_live'))
            if do_stripe and stripe_is_live:
                raise CommandError(
                    'Stripe is in live mode (by STRIPE_MODE or a live api key). Live ladder SKUs '
                    'must not exist before the rebuild '
                    'cutover (prod would deactivate their subscribers on webhook fan-out). '
                    'If this IS the cutover, re-run with --live-ok.'
                )
            if do_paypal and settings.PAYPAL_MODE == 'live':
                raise CommandError(
                    'PAYPAL_MODE is live. Live ladder plans must not exist before the rebuild '
                    'cutover. If this IS the cutover, re-run with --live-ok.'
                )

        if dry_run:
            self.stdout.write(self.style.WARNING('DRY RUN -- nothing will be created or synced'))

        # Each provider failing must not swallow the other's paste block: by the time PayPal
        # 500s, the Stripe objects already exist, and the operator needs those ids. Catch per
        # provider, print what succeeded, then re-raise so the failure is still loud.
        stripe_result = paypal_result = None
        failure = None
        try:
            stripe_result = self._bootstrap_stripe(dry_run) if do_stripe else None
        except Exception as exc:
            failure = ('stripe', exc)
        if failure is None or not do_stripe:
            try:
                paypal_result = self._bootstrap_paypal(dry_run) if do_paypal else None
            except Exception as exc:
                failure = ('paypal', exc)
        elif do_paypal:
            self.stdout.write(self.style.WARNING(
                'Skipping PayPal after the Stripe failure; re-run with --provider paypal.'))

        self._print_paste_block(stripe_result, paypal_result)
        if failure is not None:
            provider_name, exc = failure
            self.stdout.write(self.style.ERROR(
                f'{provider_name} bootstrap INCOMPLETE -- objects created so far are listed '
                f'above; the run is idempotent, so fix the cause and re-run with '
                f'--provider {provider_name}.'))
            raise exc

    # ------------------------------------------------------------------------------- Stripe ----

    def _bootstrap_stripe(self, dry_run: bool) -> dict:
        import stripe
        from djstripe.models import Price as DJPrice, Product as DJProduct

        mode = settings.STRIPE_MODE if settings.STRIPE_MODE == 'live' else 'test'
        self.stdout.write(f'\n== Stripe ({mode}) ==')

        # One pass over existing objects, matched on the idempotency anchors. Duplicate anchors
        # (two products claiming a slug, which nothing on Stripe's side prevents) are surfaced
        # loudly: silently letting the last listing win would paste a different id per run.
        products_by_slug = {}
        for product in stripe.Product.list(limit=100, active=True).auto_paging_iter():
            slug = (product.get('metadata') or {}).get('pp_ladder_slug')
            if slug:
                if slug in products_by_slug:
                    self.stdout.write(self.style.WARNING(
                        f'  DUPLICATE product anchor {slug}: {products_by_slug[slug].id} and '
                        f'{product.id} both carry it; using the latter. Archive one on Stripe.'))
                products_by_slug[slug] = product

        # lookup_keys accepts AT MOST 10 keys per request (the ladder needs 12), so the lookup is
        # chunked. active=True matters too: an archived price still holds its lookup key, and
        # matching it would paste an id checkout cannot charge against.
        all_keys = [_lookup_key(t['slug'], iv) for t in SUPPORT_TIERS for iv, _, _ in INTERVALS]
        prices_by_key = {}
        for i in range(0, len(all_keys), 10):
            for price in stripe.Price.list(lookup_keys=all_keys[i:i + 10], active=True,
                                           limit=100).auto_paging_iter():
                prices_by_key[price.lookup_key] = price

        result = {}
        for tier in SUPPORT_TIERS:
            slug = tier['slug']
            result[slug] = {}

            product = products_by_slug.get(slug)
            if product is None:
                if dry_run:
                    self.stdout.write(f'  would create product: PlatPursuit {tier["name"]}')
                else:
                    product = stripe.Product.create(
                        name=f'PlatPursuit {tier["name"]}',
                        metadata={'pp_ladder_slug': slug},
                        idempotency_key=f'pp_ladder_product_{slug}',
                    )
                    self.stdout.write(self.style.SUCCESS(
                        f'  created product {product.id} ({slug})'))
            else:
                self.stdout.write(f'  product exists: {product.id} ({slug})')
            if product is not None and not dry_run:
                DJProduct.sync_from_stripe_data(product)

            for interval, stripe_interval, _ in INTERVALS:
                key = _lookup_key(slug, interval)
                price = prices_by_key.get(key)
                if price is None:
                    if dry_run or product is None:
                        self.stdout.write(
                            f'  would create price:  {key} (${tier[interval]}/{stripe_interval})')
                        result[slug][interval] = ''
                        continue
                    price = stripe.Price.create(
                        product=product.id,
                        unit_amount=tier[interval] * 100,
                        currency='usd',
                        recurring={'interval': stripe_interval},
                        lookup_key=key,
                        # An ARCHIVED price still holds its lookup key (and the active=True match
                        # above rightly ignored it); transfer_lookup_key atomically moves the key
                        # to this new price instead of erroring on the collision.
                        transfer_lookup_key=True,
                        idempotency_key=f'pp_ladder_price_{key}',
                    )
                    logger.info("bootstrap_support_skus created Stripe price %s (%s)", price.id, key)
                    self.stdout.write(self.style.SUCCESS(f'  created price   {price.id} ({key})'))
                else:
                    self.stdout.write(f'  price exists:   {price.id} ({key})')
                if not dry_run:
                    DJPrice.sync_from_stripe_data(price)
                result[slug][interval] = price.id

        return {'mode': mode, 'ids': result}

    # ------------------------------------------------------------------------------- PayPal ----

    def _bootstrap_paypal(self, dry_run: bool) -> dict:
        from users.services.paypal_service import PayPalService

        # Normalised exactly like paypal_service/views do: anything that is not 'live' is
        # sandbox, so a typo'd env var cannot print a paste key that exists nowhere.
        mode = 'live' if settings.PAYPAL_MODE == 'live' else 'sandbox'
        base = settings.PAYPAL_API_BASE
        self.stdout.write(f'\n== PayPal ({mode}) ==')
        headers = PayPalService._api_headers()

        result = {}
        for tier in SUPPORT_TIERS:
            slug = tier['slug']
            result[slug] = {}
            product_id = _paypal_product_id(slug)

            # Product: our own id makes existence a plain GET.
            response = requests.get(
                f'{base}/v1/catalogs/products/{product_id}', headers=headers, timeout=30)
            if response.status_code == 404:
                if dry_run:
                    self.stdout.write(f'  would create product: {product_id}')
                else:
                    create = requests.post(
                        f'{base}/v1/catalogs/products',
                        json={
                            'id': product_id,
                            'name': f'PlatPursuit {tier["name"]}',
                            'description': 'PlatPursuit supporter membership',
                            'type': 'SERVICE',
                            'category': 'SOFTWARE',
                        },
                        headers={**headers, 'PayPal-Request-Id': product_id},
                        timeout=30,
                    )
                    create.raise_for_status()
                    self.stdout.write(self.style.SUCCESS(f'  created product {product_id}'))
            else:
                response.raise_for_status()
                self.stdout.write(f'  product exists: {product_id}')

            # Plans: listed per product and matched on our name convention. Plan names are not
            # unique on PayPal's side, so the deterministic PayPal-Request-Id on create is the
            # belt against a double-create racing the list.
            # raise_for_status is load-bearing: a swallowed 401/429/500 here would read as "no
            # plans exist" and the create below would DUPLICATE live billing plans. Paged, because
            # INACTIVE plans accumulate (deactivation never frees a name) and the ACTIVE one must
            # not fall off page 1.
            existing_plans = {}
            page = 1
            while True:
                plans_response = requests.get(
                    f'{base}/v1/billing/plans',
                    params={'product_id': product_id, 'page_size': 20, 'page': page},
                    headers={**headers, 'Prefer': 'return=representation'},
                    timeout=30,
                )
                plans_response.raise_for_status()
                page_plans = plans_response.json().get('plans', [])
                for plan in page_plans:
                    if plan.get('status') != 'INACTIVE':
                        if plan['name'] in existing_plans:
                            self.stdout.write(self.style.WARNING(
                                f'  DUPLICATE plan anchor {plan["name"]}: '
                                f'{existing_plans[plan["name"]]["id"]} and {plan["id"]} are both '
                                f'ACTIVE; using the latter. Deactivate one on PayPal.'))
                        existing_plans[plan['name']] = plan
                if len(page_plans) < 20:
                    break
                page += 1

            for interval, _, paypal_unit in INTERVALS:
                plan_name = _lookup_key(slug, interval)
                plan = existing_plans.get(plan_name)
                if plan is None:
                    if dry_run:
                        self.stdout.write(
                            f'  would create plan:   {plan_name} (${tier[interval]}/{paypal_unit})')
                        result[slug][interval] = ''
                        continue
                    create = requests.post(
                        f'{base}/v1/billing/plans',
                        json={
                            'product_id': product_id,
                            'name': plan_name,
                            'description': f'PlatPursuit {tier["name"]} ({interval})',
                            'status': 'ACTIVE',  # PayPal's default today, pinned explicitly
                            'billing_cycles': [{
                                'frequency': {'interval_unit': paypal_unit, 'interval_count': 1},
                                'tenure_type': 'REGULAR',
                                'sequence': 1,
                                'total_cycles': 0,  # renews until cancelled
                                'pricing_scheme': {'fixed_price': {
                                    'value': f'{tier[interval]}.00', 'currency_code': 'USD'}},
                            }],
                            'payment_preferences': {
                                'auto_bill_outstanding': True,
                                'payment_failure_threshold': 3,
                            },
                        },
                        headers={**headers, 'PayPal-Request-Id': f'{plan_name}-v1'},
                        timeout=30,
                    )
                    create.raise_for_status()
                    plan = create.json()
                    logger.info("bootstrap_support_skus created PayPal plan %s (%s)",
                                plan['id'], plan_name)
                    self.stdout.write(self.style.SUCCESS(
                        f'  created plan    {plan["id"]} ({plan_name})'))
                else:
                    self.stdout.write(f'  plan exists:    {plan["id"]} ({plan_name})')
                result[slug][interval] = plan['id']

        return {'mode': mode, 'ids': result}

    # ------------------------------------------------------------------------------- output ----

    def _print_paste_block(self, stripe_result, paypal_result):
        """The whole point: a block whose literal dicts drop straight into users/constants.py,
        replacing the empty-string comprehension for the mode that was bootstrapped."""
        self.stdout.write('\n' + '=' * 78)
        self.stdout.write('PASTE INTO users/constants.py (then flip SUPPORT_TIERS_ARE_PLACEHOLDERS')
        self.stdout.write('once BOTH providers are filled for the mode you sell in):')
        self.stdout.write('=' * 78)

        for label, result in (('STRIPE_LADDER_PRICES', stripe_result),
                              ('PAYPAL_LADDER_PLANS', paypal_result)):
            if result is None:
                continue
            self.stdout.write(f"\n# {label}['{result['mode']}'] =")
            self.stdout.write('{')
            for slug, intervals in result['ids'].items():
                self.stdout.write(
                    f"    '{slug}': {{'monthly': '{intervals.get('monthly', '')}', "
                    f"'yearly': '{intervals.get('yearly', '')}'}},")
            self.stdout.write('}')
