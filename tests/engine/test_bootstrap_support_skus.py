"""The SKU bootstrap: idempotent against both processors, djstripe-synced, live-gated.

Everything here runs against mocks -- the command's only side effects are processor API calls,
and the properties under test are the command's OWN promises: run twice creates nothing, every
Stripe object lands in djstripe, the printed block is valid paste material, and live mode is a
hard stop without --live-ok.
"""
import ast
import re
from io import StringIO
from unittest.mock import patch, MagicMock

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError

from users.constants import SUPPORT_TIERS, LADDER_SLUGS

pytestmark = pytest.mark.django_db


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
    """Run the command with both processors fully mocked; returns (stdout, mocks)."""
    out = StringIO()

    created_products, created_prices, paypal_posts = [], [], []
    existing = env.get('existing', {})  # {'products': [...], 'prices': [...], 'paypal': {...}}

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
        else:  # plan listing
            response.status_code = 200
            response.json.return_value = {'plans': env.get('paypal_plans', [])}
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

    with patch('stripe.Product.list', return_value=_FakeList(existing.get('products', []))), \
            patch('stripe.Price.list', return_value=_FakeList(existing.get('prices', []))), \
            patch('stripe.Product.create', side_effect=product_create), \
            patch('stripe.Price.create', side_effect=price_create), \
            patch('djstripe.models.Product.sync_from_stripe_data') as sync_product, \
            patch('djstripe.models.Price.sync_from_stripe_data') as sync_price, \
            patch('users.services.paypal_service.PayPalService._api_headers', return_value={}), \
            patch('users.management.commands.bootstrap_support_skus.requests.get',
                  side_effect=paypal_get), \
            patch('users.management.commands.bootstrap_support_skus.requests.post',
                  side_effect=paypal_post):
        call_command('bootstrap_support_skus', *args, stdout=out)

    return out.getvalue(), {
        'products': created_products, 'prices': created_prices,
        'paypal_posts': paypal_posts, 'sync_product': sync_product, 'sync_price': sync_price,
    }


# ---------------------------------------------------------------------------- idempotency ----

def test_a_cold_run_creates_everything():
    output, mocks = _run()

    assert len(mocks['products']) == 6
    assert len(mocks['prices']) == 12
    # 6 PayPal products + 12 plans
    plan_posts = [p for p in mocks['paypal_posts'] if '/v1/billing/plans' in p[0]]
    product_posts = [p for p in mocks['paypal_posts'] if '/v1/catalogs/products' in p[0]]
    assert len(plan_posts) == 12
    assert len(product_posts) == 6


def test_a_second_run_creates_nothing():
    """Everything matched on its idempotency anchor: products by metadata slug, prices by
    lookup_key, PayPal products by chosen id, plans by name."""
    products = [_stripe_obj(id=f'prod_{t["slug"]}', metadata={'pp_ladder_slug': t['slug']})
                for t in SUPPORT_TIERS]
    prices = [_stripe_obj(id=f'price_{t["slug"]}_{iv}',
                          lookup_key=f'pp_ladder_{t["slug"]}_{iv}')
              for t in SUPPORT_TIERS for iv in ('monthly', 'yearly')]
    paypal_plans = [{'id': f'P-{t["slug"]}-{iv}', 'name': f'pp_ladder_{t["slug"]}_{iv}',
                     'status': 'ACTIVE'}
                    for t in SUPPORT_TIERS for iv in ('monthly', 'yearly')]

    output, mocks = _run(
        existing={'products': products, 'prices': prices},
        paypal_products={f'PP-LADDER-{t["slug"].upper()}' for t in SUPPORT_TIERS},
        paypal_plans=paypal_plans,
    )

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
    assert 'would create' in output


# -------------------------------------------------------------------------- djstripe sync ----

def test_every_stripe_price_is_synced_into_djstripe():
    """Checkout does Price.objects.get(id=...) -- an unsynced price 500s at flag-flip. All 12
    prices and all 6 products must pass through sync_from_stripe_data."""
    output, mocks = _run('--provider', 'stripe')

    assert mocks['sync_price'].call_count == 12
    assert mocks['sync_product'].call_count == 6


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


def test_plan_names_use_the_shared_pp_ladder_convention():
    """Plan names anchor idempotent matching; they share the pp_ladder_ namespace with Stripe
    lookup_keys deliberately, one convention across both processors."""
    output, mocks = _run('--provider', 'paypal')
    plan_posts = [p for p in mocks['paypal_posts'] if '/v1/billing/plans' in p[0]]
    names = {p[1]['json']['name'] for p in plan_posts}
    assert names == {f'pp_ladder_{t["slug"]}_{iv}'
                     for t in SUPPORT_TIERS for iv in ('monthly', 'yearly')}
