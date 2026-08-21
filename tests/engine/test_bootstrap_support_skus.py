"""The SKU bootstrap: idempotent against both processors, djstripe-synced, live-gated.

Everything here runs against mocks -- the command's only side effects are processor API calls --
but the fakes VALIDATE the requests they receive rather than ignoring kwargs. That distinction is
why this file looks fussy: the original return_value mocks discarded every kwarg and thereby hid a
real cold-run breaker (Stripe caps `lookup_keys` at 10 per request; the ladder needs 12). The
fakes now enforce the API's contract, so the suite fails the way Stripe would.
"""
import ast
import re
from io import StringIO
from unittest.mock import patch, MagicMock

import pytest
import requests as requests_lib
from django.core.management import call_command
from django.core.management.base import CommandError

from users.constants import SUPPORT_TIERS, LADDER_SLUGS

pytestmark = pytest.mark.django_db

ALL_PLAN_NAMES = {f'pp_ladder_{t["slug"]}_{iv}' for t in SUPPORT_TIERS
                  for iv in ('monthly', 'yearly')}


def _stripe_obj(**kwargs):
    """A dict that also does attribute access, like stripe's own objects."""
    obj = MagicMock()
    obj.get = lambda key, default=None: kwargs.get(key, default)
    for key, value in kwargs.items():
        setattr(obj, key, value)
    return obj


class _FakeList:
    def __init__(self, items):
        self._items = items

    def auto_paging_iter(self):
        return iter(self._items)


def _run(*args, **env):
    """Run the command with both processors mocked; returns (stdout, mocks).

    The Stripe list fakes assert the request contract (lookup_keys cap, active filter) and filter
    fixtures BY the passed kwargs, so a convention drift between create and lookup fails here
    instead of only against the real API. Pass catch=True to capture an expected exception in
    mocks['error'] instead of letting it propagate.
    """
    out = StringIO()

    created_products, created_prices, paypal_posts = [], [], []
    existing = env.get('existing', {})  # {'products': [...], 'prices': [...]}

    def product_list(**kwargs):
        assert kwargs.get('active') is True, 'product listing must exclude archived products'
        return _FakeList(existing.get('products', []))

    def price_list(**kwargs):
        keys = kwargs.get('lookup_keys') or []
        assert keys, 'the price lookup must be anchored on lookup_keys'
        assert len(keys) <= 10, 'STRIPE CAPS lookup_keys AT 10 PER REQUEST (the ladder needs 12)'
        assert kwargs.get('active') is True, \
            'archived prices still hold their lookup keys; the listing must filter them out'
        return _FakeList([p for p in existing.get('prices', []) if p.lookup_key in keys])

    def product_create(**kwargs):
        product = _stripe_obj(id=f"prod_new_{kwargs['metadata']['pp_ladder_slug']}", **kwargs)
        created_products.append(product)
        return product

    def price_create(**kwargs):
        price = _stripe_obj(id=f"price_new_{kwargs['lookup_key']}", **kwargs)
        created_prices.append(price)
        return price

    def paypal_get(url, **kwargs):
        response = MagicMock()
        if '/v1/catalogs/products/' in url:
            product_id = url.rsplit('/', 1)[1]
            known = env.get('paypal_products', set())
            response.status_code = 200 if product_id in known else 404
        else:  # plan listing, paged
            status = env.get('plan_list_status', 200)
            response.status_code = status
            if status != 200:
                response.raise_for_status.side_effect = requests_lib.HTTPError(f'{status} error')
                return response
            response.raise_for_status.return_value = None
            page = kwargs.get('params', {}).get('page', 1)
            plans = env.get('paypal_plans', [])
            response.json.return_value = {'plans': plans[(page - 1) * 20:page * 20]}
        return response

    def paypal_post(url, **kwargs):
        paypal_posts.append((url, kwargs))
        response = MagicMock()
        response.status_code = 201
        if '/v1/billing/plans' in url:
            response.json.return_value = {'id': f"P-NEW-{kwargs['json']['name']}",
                                          'name': kwargs['json']['name']}
        else:
            response.json.return_value = {'id': kwargs['json']['id']}
        return response

    error = None
    with patch('stripe.Product.list', side_effect=product_list), \
            patch('stripe.Price.list', side_effect=price_list), \
            patch('stripe.Product.create', side_effect=product_create), \
            patch('stripe.Price.create', side_effect=price_create), \
            patch('djstripe.models.Product.sync_from_stripe_data') as sync_product, \
            patch('djstripe.models.Price.sync_from_stripe_data') as sync_price, \
            patch('users.services.paypal_service.PayPalService._api_headers', return_value={}), \
            patch('users.management.commands.bootstrap_support_skus.requests.get',
                  side_effect=paypal_get), \
            patch('users.management.commands.bootstrap_support_skus.requests.post',
                  side_effect=paypal_post):
        if env.get('catch'):
            try:
                call_command('bootstrap_support_skus', *args, stdout=out)
            except Exception as exc:
                error = exc
        else:
            call_command('bootstrap_support_skus', *args, stdout=out)

    return out.getvalue(), {
        'products': created_products, 'prices': created_prices,
        'paypal_posts': paypal_posts, 'sync_product': sync_product, 'sync_price': sync_price,
        'error': error,
    }


def _second_run_fixtures(mocks):
    """Feed run 1's created objects back as run 2's existing state, so the create and lookup
    conventions are checked against EACH OTHER rather than against hand-written strings."""
    plan_posts = [p for p in mocks['paypal_posts'] if '/v1/billing/plans' in p[0]]
    return dict(
        existing={'products': mocks['products'], 'prices': mocks['prices']},
        paypal_products={f'PP-LADDER-{t["slug"].upper()}' for t in SUPPORT_TIERS},
        paypal_plans=[{'id': f"P-NEW-{p[1]['json']['name']}", 'name': p[1]['json']['name'],
                       'status': 'ACTIVE'} for p in plan_posts],
    )


# ---------------------------------------------------------------------------- idempotency ----

def test_a_cold_run_creates_everything():
    output, mocks = _run()

    assert len(mocks['products']) == 6
    assert len(mocks['prices']) == 12
    plan_posts = [p for p in mocks['paypal_posts'] if '/v1/billing/plans' in p[0]]
    product_posts = [p for p in mocks['paypal_posts'] if '/v1/catalogs/products' in p[0]]
    assert len(plan_posts) == 12
    assert len(product_posts) == 6


def test_a_second_run_creates_nothing():
    """Round-trip: run 2's fixtures ARE run 1's created objects, so a drift between the create
    payloads and the lookup anchors fails here without any hand-kept strings."""
    _, first = _run()

    output, mocks = _run(**_second_run_fixtures(first))

    assert mocks['products'] == []
    assert mocks['prices'] == []
    assert mocks['paypal_posts'] == []
    assert 'exists' in output


def test_dry_run_creates_and_syncs_nothing():
    output, mocks = _run('--dry-run')

    assert mocks['products'] == []
    assert mocks['prices'] == []
    assert mocks['paypal_posts'] == []
    assert not mocks['sync_price'].called
    assert not mocks['sync_product'].called
    assert 'would create' in output


def test_an_inactive_paypal_plan_is_skipped_and_replaced():
    """Deactivating a plan on PayPal's dashboard does not free its name; the bootstrap must not
    resurrect the corpse, it must create a fresh plan (the documented gotcha)."""
    dead = [{'id': 'P-DEAD', 'name': 'pp_ladder_backer_monthly', 'status': 'INACTIVE'}]

    output, mocks = _run('--provider', 'paypal',
                         paypal_products={f'PP-LADDER-{t["slug"].upper()}' for t in SUPPORT_TIERS},
                         paypal_plans=dead)

    plan_posts = [p[1]['json']['name'] for p in mocks['paypal_posts']
                  if '/v1/billing/plans' in p[0]]
    assert 'pp_ladder_backer_monthly' in plan_posts, 'the INACTIVE plan was treated as alive'
    assert 'P-DEAD' not in output


# --------------------------------------------------------------------------- request shape ----

def test_the_money_is_right_on_both_processors():
    """Dollars-to-cents for Stripe, decimal-string dollars for PayPal. Nothing else in the suite
    would notice the two being swapped, and this is the billing surface."""
    _, mocks = _run()

    stripe_amounts = {p.lookup_key: (p.unit_amount, p.currency) for p in mocks['prices']}
    plan_values = {p[1]['json']['name']: p[1]['json']['billing_cycles'][0]
                   ['pricing_scheme']['fixed_price']
                   for p in mocks['paypal_posts'] if '/v1/billing/plans' in p[0]}
    for tier in SUPPORT_TIERS:
        for interval in ('monthly', 'yearly'):
            key = f'pp_ladder_{tier["slug"]}_{interval}'
            assert stripe_amounts[key] == (tier[interval] * 100, 'usd')
            assert plan_values[key] == {'value': f'{tier[interval]}.00', 'currency_code': 'USD'}


def test_the_interval_mapping_is_right_on_both_processors():
    _, mocks = _run()

    for price in mocks['prices']:
        expected = 'month' if price.lookup_key.endswith('_monthly') else 'year'
        assert price.recurring == {'interval': expected}, price.lookup_key
    for url, kwargs in mocks['paypal_posts']:
        if '/v1/billing/plans' not in url:
            continue
        payload = kwargs['json']
        expected = 'MONTH' if payload['name'].endswith('_monthly') else 'YEAR'
        assert payload['billing_cycles'][0]['frequency']['interval_unit'] == expected


def test_every_paypal_create_carries_its_deterministic_request_id():
    """The PayPal-Request-Id belt is claimed by the docstring, the runbook and the deploy
    checklist; deleting the headers used to pass every test."""
    _, mocks = _run('--provider', 'paypal')

    for url, kwargs in mocks['paypal_posts']:
        headers = kwargs['headers']
        if '/v1/billing/plans' in url:
            assert headers['PayPal-Request-Id'] == f"{kwargs['json']['name']}-v1"
        else:
            assert headers['PayPal-Request-Id'] == kwargs['json']['id']


def test_plan_names_use_the_shared_pp_ladder_convention():
    """Plan names anchor idempotent matching; they share the pp_ladder_ namespace with Stripe
    lookup_keys deliberately, one convention across both processors."""
    output, mocks = _run('--provider', 'paypal')
    plan_posts = [p for p in mocks['paypal_posts'] if '/v1/billing/plans' in p[0]]
    names = {p[1]['json']['name'] for p in plan_posts}
    assert names == ALL_PLAN_NAMES


# -------------------------------------------------------------------------- failure modes ----

def test_a_failed_plan_listing_must_not_cause_duplicate_plans():
    """THE silent-duplicate hazard: a swallowed 401/429/500 on the plan list would read as 'no
    plans exist' and the command would create duplicate live billing plans. It must raise
    instead, before any plan POST happens."""
    output, mocks = _run('--provider', 'paypal',
                         paypal_products={f'PP-LADDER-{t["slug"].upper()}' for t in SUPPORT_TIERS},
                         plan_list_status=500, catch=True)

    assert isinstance(mocks['error'], requests_lib.HTTPError)
    plan_posts = [p for p in mocks['paypal_posts'] if '/v1/billing/plans' in p[0]]
    assert plan_posts == [], 'plans were created on top of an unreadable listing'


def test_a_paypal_failure_still_prints_the_stripe_paste_block():
    """By the time PayPal fails, the Stripe objects exist; the operator needs those ids even
    though the run errors. The failure itself stays loud (re-raised after printing)."""
    output, mocks = _run(plan_list_status=500, catch=True)

    assert isinstance(mocks['error'], requests_lib.HTTPError)
    assert len(mocks['prices']) == 12, 'stripe half should have completed first'
    assert "# STRIPE_LADDER_PRICES['test'] =" in output
    assert 'INCOMPLETE' in output


def test_the_paypal_plan_listing_walks_pages():
    """INACTIVE plans accumulate (a deactivated name is never freed), so the ACTIVE plan can sit
    past page 1. Single-page listing would silently duplicate it."""
    graveyard = [{'id': f'P-DEAD-{i}', 'name': f'pp_ladder_dead_{i}', 'status': 'INACTIVE'}
                 for i in range(20)]
    alive = [{'id': f'P-{name}', 'name': name, 'status': 'ACTIVE'} for name in ALL_PLAN_NAMES]

    output, mocks = _run('--provider', 'paypal',
                         paypal_products={f'PP-LADDER-{t["slug"].upper()}' for t in SUPPORT_TIERS},
                         paypal_plans=graveyard + alive)

    assert mocks['paypal_posts'] == [], 'page-2 ACTIVE plans were not seen and got duplicated'


# ---------------------------------------------------------------------------- paste block ----

def test_the_printed_block_parses_and_matches_the_constant_shapes():
    output, mocks = _run()

    blocks = re.findall(r"# (STRIPE_LADDER_PRICES|PAYPAL_LADDER_PLANS)\['(\w+)'\] =\n(\{.*?\n\})",
                        output, re.DOTALL)
    assert len(blocks) == 2, 'expected one paste block per provider'
    for constant_name, mode, literal in blocks:
        parsed = ast.literal_eval(literal)
        assert sorted(parsed.keys()) == sorted(LADDER_SLUGS)
        for slug, intervals in parsed.items():
            assert sorted(intervals.keys()) == ['monthly', 'yearly']
            assert all(intervals.values()), f'{constant_name}.{slug} printed an empty id'


def test_paypal_plan_ids_land_under_the_paypal_mode_key():
    """PAYPAL_LADDER_PLANS is keyed 'sandbox'/'live' (PAYPAL_MODE), not 'test'."""
    output, mocks = _run('--provider', 'paypal')
    assert "# PAYPAL_LADDER_PLANS['sandbox'] =" in output


# -------------------------------------------------------------------------- djstripe sync ----

def test_every_stripe_price_is_synced_into_djstripe():
    """Checkout does Price.objects.get(id=...) -- an unsynced price 500s at flag-flip. All 12
    prices and all 6 products must pass through sync_from_stripe_data."""
    output, mocks = _run('--provider', 'stripe')

    assert mocks['sync_price'].call_count == 12
    assert mocks['sync_product'].call_count == 6


# ------------------------------------------------------------------------------ live gate ----

def test_live_stripe_mode_is_a_hard_stop_without_the_flag(settings):
    """THE prod-safety rule: live SKUs before cutover would have prod's webhook fan-out
    deactivate ladder subscribers (unknown product id). The gate must hold."""
    settings.STRIPE_MODE = 'live'

    with pytest.raises(CommandError, match='cutover'):
        _run('--provider', 'stripe')


def test_live_paypal_mode_is_gated_independently(settings):
    settings.PAYPAL_MODE = 'live'

    with pytest.raises(CommandError, match='cutover'):
        _run('--provider', 'paypal')


def test_a_live_looking_api_key_trips_the_gate_even_in_test_mode(settings):
    """Belt on the label: a live secret pasted into the test env var must not sail through on the
    strength of STRIPE_MODE alone."""
    import stripe
    settings.STRIPE_MODE = 'test'

    with patch.object(stripe, 'api_key', 'sk_live_looks_real'), \
            pytest.raises(CommandError, match='cutover'):
        _run('--provider', 'stripe')


def test_live_ok_actually_permits_a_live_run(settings):
    """The gate must be passable at cutover, or the flag is theatre."""
    settings.STRIPE_MODE = 'live'

    output, mocks = _run('--provider', 'stripe', '--live-ok')

    assert mocks['error'] is None
    assert len(mocks['prices']) == 12
    assert "# STRIPE_LADDER_PRICES['live'] =" in output


def test_dry_run_bypasses_the_live_gate_by_design(settings):
    """Deliberate: a read-only report against live is how the operator checks state before
    cutover. This test pins the bypass so it cannot silently widen into creates."""
    settings.STRIPE_MODE = 'live'

    output, mocks = _run('--provider', 'stripe', '--dry-run')

    assert mocks['error'] is None
    assert mocks['products'] == [] and mocks['prices'] == []
    assert 'would create' in output
