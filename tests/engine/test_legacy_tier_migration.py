"""The legacy tier migration: grandfathered subscribers moving onto the supporter ladder.

Two arms with deliberately different mechanics (see `migrate_legacy_tiers` for the why): Stripe is a
real price swap on the processor, PayPal is an adoption that changes no billing at all because PayPal
cannot change a billing amount without the subscriber re-approving.

THE djstripe MIRROR AND ITS SYNC ARE REAL IN THESE TESTS; only `stripe.Subscription` (the API) is
patched. Two earlier versions were weaker and both hid real bugs: the first patched the djstripe
model wholesale in the command module, which stubbed out `.filter().first()` and left the customer
scoping and the status pre-filter unpinned (deleting `customer__id=...`, i.e. repricing a stranger's
subscription, passed 19/19); the second patched `sync_from_stripe_data` with a stub that FABRICATED
a top-level `plan` key the real API version does not return, which hid a bug that would have revoked
premium from every migrated Stripe member. Follow `test_audit_subscription_status._djstripe_sub`
here, not the mock.

The four tests that matter most:

- `test_the_stripe_swap_passes_the_existing_item_id` -- omitting the item id does not REPLACE the
  subscription item, it ADDS one, and the member is billed for both.
- `test_the_swap_is_scoped_to_this_users_customer` -- the other money bug: repricing somebody else.
- `test_the_swap_never_syncs_the_mirror_or_re_derives_the_tier` -- the API-version trap.
- `test_an_activated_replay_does_not_restore_the_legacy_slug` -- the entire justification for
  LEGACY_PLAN_ADOPTION existing.

The billing-line regression this migration causes (`describe_billing` keyed on the tier, which stops
resolving once those users hold `backer`) is pinned where that contract lives, in
test_membership_page.py, rather than duplicated here.
"""
from datetime import timedelta
from io import StringIO
from unittest.mock import patch

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError
from django.utils import timezone
from djstripe.models import Customer, Subscription

from users.constants import (
    LEGACY_PLAN_ADOPTION,
    LEGACY_TIER_LEVEL_MAP,
    PAYPAL_PLANS,
    PAYPAL_PLAN_TO_TIER,
)
from users.models import SubscriptionPeriod
from users.services.subscription_service import SubscriptionService
from tests.factories import ProfileFactory

pytestmark = pytest.mark.django_db

LEGACY_MONTHLY_PLAN = PAYPAL_PLANS['live']['premium_monthly']
LEGACY_YEARLY_PLAN = PAYPAL_PLANS['live']['premium_yearly']
LEGACY_SUPPORTER_PLAN = PAYPAL_PLANS['live']['supporter']

# Test-mode ids, which is the mode the suite runs in.
LEGACY_MONTHLY_PRODUCT = 'prod_ThqljWr4cvnFFF'
LEGACY_YEARLY_PRODUCT = 'prod_ThqpPjDyERnoaF'
BACKER_PRODUCT = 'prod_V735ZqzcE781QL'
BACKER_YEARLY_PRICE = 'price_1U6ozjR5jhcbjB32OrSMYweY'
BACKER_MONTHLY_PRICE = 'price_1U6ozjR5jhcbjB32vStaZGFu'


def _legacy_paypal(tier='premium_yearly', sub_id='I-LEGACYSUB'):
    profile = ProfileFactory()
    user = profile.user
    SubscriptionService.activate_subscription(user, tier, 'paypal')
    user.paypal_subscription_id = sub_id
    user.save(update_fields=['paypal_subscription_id'])
    return user, profile


def _legacy_stripe(tier='premium_yearly', customer_id='cus_legacy', sub_id='sub_legacy',
                   status='active', product=LEGACY_YEARLY_PRODUCT):
    """A legacy Stripe subscriber with a REAL djstripe mirror row carrying the LEGACY product.

    The mirror deliberately keeps the legacy product throughout: the command must NOT touch it (the
    webhook owns that), so a row still holding the old product after a run is the correct outcome.
    """
    profile = ProfileFactory()
    user = profile.user
    SubscriptionService.activate_subscription(user, tier, 'stripe')
    user.stripe_customer_id = customer_id
    user.save(update_fields=['stripe_customer_id'])
    customer = Customer.objects.create(id=customer_id, subscriber=user)
    Subscription.objects.create(
        id=sub_id, customer=customer,
        stripe_data={'status': status, 'plan': {'product': product, 'id': 'price_legacy'}},
    )
    return user, profile


def _api_sub(sub_id='sub_legacy', price='price_legacy', product=LEGACY_YEARLY_PRODUCT,
             interval='year', status='active', cancel_at_period_end=False, cancel_at=None,
             item_id='si_existing', items=None):
    """What `stripe.Subscription.retrieve`/`modify` returns."""
    if items is None:
        items = [{'id': item_id,
                  'price': {'id': price, 'product': product, 'recurring': {'interval': interval}}}]
    return {'id': sub_id, 'status': status, 'cancel_at_period_end': cancel_at_period_end,
            'cancel_at': cancel_at, 'items': {'data': items}}


def _run(*args):
    out = StringIO()
    call_command('migrate_legacy_tiers', *args, stdout=out, stderr=out)
    return out.getvalue()


def _stripe_api():
    """Patch ONLY the Stripe API, leaving the djstripe ORM and its sync entirely real.

    An earlier version also patched `sync_from_stripe_data` with a side effect that FABRICATED a
    top-level `plan` key from the subscription item. Real dj-stripe stores the API response verbatim
    (`base.py`: `result = {"stripe_data": data}`), so a mock that BUILDS a payload is asserting its
    own author's idea of the wire format rather than Stripe's. The command no longer syncs the mirror
    or re-derives the tier, and
    `test_the_swap_never_syncs_the_mirror_or_re_derives_the_tier` pins that.
    """
    return patch('users.management.commands.migrate_legacy_tiers.stripe.Subscription')


# ── The maps ─────────────────────────────────────────────────────────────


def test_adopted_paypal_plans_resolve_to_backer():
    """The adoption itself: the two legacy plans now name a ladder level, so a member paying $3.99
    on a plan we no longer sell is read as the Backer they already wear."""
    assert PAYPAL_PLAN_TO_TIER[LEGACY_MONTHLY_PLAN] == 'backer'
    assert PAYPAL_PLAN_TO_TIER[LEGACY_YEARLY_PLAN] == 'backer'


def test_the_supporter_plan_is_deliberately_not_adopted():
    """Confirmed by hand on 2026-09-06 that every `supporter` holder is on Stripe, so they migrate
    through the price swap. Because that is an operator observation and not a fact the code can
    check, the command refuses to adopt any plan it cannot read back -- see
    `test_paypal_adoption_refuses_to_migrate_on_an_unverified_plan`."""
    assert LEGACY_SUPPORTER_PLAN not in LEGACY_PLAN_ADOPTION
    assert PAYPAL_PLAN_TO_TIER[LEGACY_SUPPORTER_PLAN] == 'supporter'


def test_each_adopted_plan_carries_the_interval_its_legacy_tier_billed_at():
    """Joins the two maps rather than checking each against a literal. An earlier version asserted
    only `interval in ('monthly','yearly')`, which passed even with the yearly plan mislabelled
    monthly -- and that value is what the membership page renders as the billing cycle."""
    expected = {PAYPAL_PLANS['live']['premium_monthly']: ('backer', 'monthly'),
                PAYPAL_PLANS['live']['premium_yearly']: ('backer', 'yearly')}
    assert LEGACY_PLAN_ADOPTION == expected
    for _plan, (slug, _iv) in LEGACY_PLAN_ADOPTION.items():
        assert slug in LEGACY_TIER_LEVEL_MAP.values()


def test_the_legacy_targets_are_exactly_these():
    """Pins the PAIRS, not just that each target resolves to something.

    Asserting only "a price exists for whatever the map says" cannot detect a wrong target:
    repointing `supporter` at `backer` would move a $20/mo member onto the $4 price and passed the
    whole suite. These three pairs are the migration's entire contract with a paying member.
    """
    assert LEGACY_TIER_LEVEL_MAP == {
        'premium_monthly': 'backer',    # $3.99/mo -> $4/mo
        'premium_yearly': 'backer',     # $39.99/yr -> $40/yr
        'supporter': 'sponsor',         # $20/mo -> $20/mo, an exact match
    }


def test_every_legacy_tier_has_a_reachable_ladder_price():
    """Both intervals must exist for every target, because the command picks the interval from each
    subscriber's live price rather than from the tier name."""
    for legacy, target in LEGACY_TIER_LEVEL_MAP.items():
        for interval in ('monthly', 'yearly'):
            assert SubscriptionService.resolve_ladder_price_id(target, interval, False), (
                f'{legacy} -> {target}/{interval} has no configured test-mode price'
            )


# ── The drift guard ──────────────────────────────────────────────────────


def test_an_activated_replay_does_not_restore_the_legacy_slug():
    """THE reason LEGACY_PLAN_ADOPTION exists.

    Nothing in the PayPal path re-derives a tier on its own: PAYMENT.SALE.COMPLETED only emails, and
    the audit command's PayPal arm checks presence and expiry but never the plan. The one exception
    is BILLING.SUBSCRIPTION.ACTIVATED, which fires on re-activation after a suspension -- and before
    adoption it would have handed a migrated member their `premium_yearly` slug straight back.
    """
    from users.services.paypal_service import PayPalService

    user, _profile = _legacy_paypal(tier='premium_yearly')
    user.premium_tier = 'backer'   # already migrated
    user.save(update_fields=['premium_tier'])

    with patch('users.services.subscription_service.send_subscription_notification'), \
            patch.object(SubscriptionService, '_send_subscription_welcome_email'):
        PayPalService.handle_webhook_event(
            'BILLING.SUBSCRIPTION.ACTIVATED',
            {'id': 'I-LEGACYSUB', 'plan_id': LEGACY_YEARLY_PLAN, 'custom_id': str(user.id)},
        )

    user.refresh_from_db()
    assert user.premium_tier == 'backer', 'a re-activation handed back the legacy slug'


# ── Arm routing ──────────────────────────────────────────────────────────


def test_provider_paypal_leaves_stripe_subscribers_untouched():
    """`--provider` was entirely unpinned: replacing both arm guards with `if True` passed every
    test, because each one created a single user of a single provider."""
    pp_user, _ = _legacy_paypal(tier='premium_monthly')
    st_user, _ = _legacy_stripe(tier='premium_yearly')

    with patch('users.services.paypal_service.PayPalService.get_cached_subscription_snapshot',
               return_value={'plan_id': LEGACY_MONTHLY_PLAN}), _stripe_api() as stripe_api:
        _run('--fix', '--provider', 'paypal')

    pp_user.refresh_from_db()
    st_user.refresh_from_db()
    assert pp_user.premium_tier == 'backer'
    assert st_user.premium_tier == 'premium_yearly', 'the Stripe arm ran under --provider paypal'
    assert not stripe_api.retrieve.called and not stripe_api.modify.called


def test_provider_stripe_leaves_paypal_subscribers_untouched():
    pp_user, _ = _legacy_paypal(tier='premium_monthly')
    st_user, _ = _legacy_stripe(tier='premium_yearly')

    # The snapshot is patched to SUCCEED on purpose: leaving it unpatched made the PayPal arm
    # refuse on an unverified plan, so this test passed even with the arm guard removed entirely.
    with _stripe_api() as stripe_api, \
            patch('users.services.paypal_service.PayPalService.get_cached_subscription_snapshot',
                  return_value={'plan_id': LEGACY_MONTHLY_PLAN}):
        stripe_api.retrieve.return_value = _api_sub()
        stripe_api.modify.return_value = _api_sub(price=BACKER_YEARLY_PRICE, product=BACKER_PRODUCT)
        _run('--fix', '--provider', 'stripe')

    pp_user.refresh_from_db()
    st_user.refresh_from_db()
    assert st_user.premium_tier == 'backer'
    assert pp_user.premium_tier == 'premium_monthly', 'the PayPal arm ran under --provider stripe'


# ── The Stripe arm ───────────────────────────────────────────────────────


def test_the_stripe_swap_passes_the_existing_item_id():
    """THE money test.

    `items=[{'price': ...}]` without the existing item's id does not replace that item, it ADDS a
    second one, and Stripe then bills the member for both.
    """
    _legacy_stripe(tier='premium_yearly')

    with _stripe_api() as stripe_api:
        stripe_api.retrieve.return_value = _api_sub()
        stripe_api.modify.return_value = _api_sub(price=BACKER_YEARLY_PRICE, product=BACKER_PRODUCT)
        _run('--fix', '--provider', 'stripe')

    assert stripe_api.modify.called, 'the subscription was never modified'
    args, kwargs = stripe_api.modify.call_args
    # WHICH subscription, not just which item: hardcoding a different id in either call survived the
    # whole suite, and that is the same class of mistake as dropping the item id.
    assert args[0] == 'sub_legacy', 'a different subscription was modified'
    assert stripe_api.retrieve.call_args[0][0] == 'sub_legacy', 'a different subscription was read'
    assert kwargs['items'] == [{'id': 'si_existing', 'price': BACKER_YEARLY_PRICE}]
    assert kwargs['proration_behavior'] == 'none', 'a proration would charge for the penny now'


def test_the_swap_is_scoped_to_this_users_customer():
    """The second money bug, previously unpinned: deleting the `customer__id` filter from the mirror
    lookup passed every test, because the ORM was mocked.

    Deliberately built so ROW ORDER cannot decide the outcome (a first attempt at this test created
    both rows and asserted on which was picked, which the unscoped query passed by luck): this
    user's own customer has NO subscription at all, and only a stranger's does. Scoped, that is a
    clean [NO SUB]. Unscoped, the command reaches for the stranger's subscription and reprices it.
    """
    profile = ProfileFactory()
    user = profile.user
    SubscriptionService.activate_subscription(user, 'premium_yearly', 'stripe')
    user.stripe_customer_id = 'cus_mine'
    user.save(update_fields=['stripe_customer_id'])
    Customer.objects.create(id='cus_mine', subscriber=user)

    stranger = Customer.objects.create(id='cus_stranger', subscriber=None)
    Subscription.objects.create(
        id='sub_stranger', customer=stranger,
        stripe_data={'status': 'active',
                     'plan': {'product': LEGACY_YEARLY_PRODUCT, 'id': 'price_legacy'}},
    )

    with _stripe_api() as stripe_api:
        stripe_api.retrieve.return_value = _api_sub(sub_id='sub_stranger')
        stripe_api.modify.return_value = _api_sub(sub_id='sub_stranger', price=BACKER_YEARLY_PRICE,
                                                  product=BACKER_PRODUCT)
        output = _run('--fix', '--provider', 'stripe')

    assert not stripe_api.retrieve.called, "a stranger's subscription was read"
    assert not stripe_api.modify.called, "a stranger's subscription was repriced"
    assert 'NO SUB' in output
    user.refresh_from_db()
    assert user.premium_tier == 'premium_yearly'


def test_the_swap_never_syncs_the_mirror_or_re_derives_the_tier():
    """The API-version trap, pinned.

    `sync_from_stripe_data` stores the API response verbatim, and this command's SDK calls run at
    stripe-python 14's pinned version while every other row in that table is written by webhooks at
    the ACCOUNT's version. The shapes differ -- a prod row checked 2026-09-06 carries `plan` but no
    `current_period_end` -- so writing one over the other is how a field disappears from a row that
    four production call sites still read.

    Re-deriving is a separate risk with no upside: `update_user_subscription` has four fall-throughs
    to `deactivate_subscription`, and the target slug is already known. So the command writes the
    tier and leaves the mirror to the webhook, which is the only writer that table should have.
    """
    user, _ = _legacy_stripe(tier='premium_yearly')
    before = Subscription.objects.get(id='sub_legacy').stripe_data

    with _stripe_api() as stripe_api, \
            patch.object(Subscription, 'sync_from_stripe_data') as sync, \
            patch.object(SubscriptionService, 'update_user_subscription') as rederive:
        stripe_api.retrieve.return_value = _api_sub()
        stripe_api.modify.return_value = _api_sub(price=BACKER_YEARLY_PRICE, product=BACKER_PRODUCT)
        _run('--fix', '--provider', 'stripe')

    assert not sync.called, 'the clover-shaped response was written into the mirror'
    assert not rederive.called, 'the tier was re-derived instead of written'
    assert Subscription.objects.get(id='sub_legacy').stripe_data == before
    user.refresh_from_db()
    assert user.premium_tier == 'backer'


def test_a_subscription_on_another_ladder_level_is_not_downgraded():
    """The guard accepted ANY ladder slug, so a member whose tier said `supporter` but whose real
    subscription was `benefactor` (the drift class the guard exists for) would have been repriced
    DOWN to Sponsor. Only the target slug is acceptable, which is the crash-recovery case."""
    user, _ = _legacy_stripe(tier='supporter')
    benefactor = 'prod_V735rQrsUjmWux'

    with _stripe_api() as stripe_api:
        stripe_api.retrieve.return_value = _api_sub(price='price_benefactor', product=benefactor,
                                                    interval='month')
        output = _run('--fix', '--provider', 'stripe')

    user.refresh_from_db()
    assert not stripe_api.modify.called, 'a higher ladder level was repriced downward'
    assert user.premium_tier == 'supporter'
    assert 'BLOCKED price' in output


def test_the_stripe_swap_writes_the_new_tier_and_counts_it():
    user, profile = _legacy_stripe(tier='premium_monthly', product=LEGACY_MONTHLY_PRODUCT)

    with _stripe_api() as stripe_api:
        stripe_api.retrieve.return_value = _api_sub(product=LEGACY_MONTHLY_PRODUCT, interval='month')
        stripe_api.modify.return_value = _api_sub(price=BACKER_MONTHLY_PRICE, product=BACKER_PRODUCT,
                                                  interval='month')
        output = _run('--fix', '--provider', 'stripe')

    user.refresh_from_db()
    profile.refresh_from_db()
    assert user.premium_tier == 'backer'
    assert profile.user_is_premium is True
    assert 'migrated=1' in output and 'errors=0' in output


def test_a_swap_whose_tier_write_does_not_stick_is_reported_as_an_error():
    """The money has already moved by the time the tier is written, so the one thing the command
    must never do is narrate a success it did not achieve.

    The original hazard was `update_user_subscription`'s four fall-throughs to
    `deactivate_subscription`, which the command no longer goes anywhere near (see
    `test_the_swap_never_syncs_the_mirror_or_re_derives_the_tier`). The verify stays regardless,
    because "the price moved and the tier did not" is the state that needs a red line however it
    arises. Here `reconcile_premium` stands in for anything that clears the tier mid-write.
    """
    user, _ = _legacy_stripe(tier='premium_yearly')

    def _clobber(u, **kwargs):
        u.premium_tier = None
        u.save(update_fields=['premium_tier'])

    with _stripe_api() as stripe_api, \
            patch.object(SubscriptionService, 'reconcile_premium', side_effect=_clobber):
        stripe_api.retrieve.return_value = _api_sub()
        stripe_api.modify.return_value = _api_sub(price=BACKER_YEARLY_PRICE, product=BACKER_PRODUCT)
        output = _run('--fix', '--provider', 'stripe')

    assert stripe_api.modify.called, 'the price never moved, so the verify was not exercised'
    user.refresh_from_db()
    assert user.premium_tier is None
    assert 'TIER WRONG' in output and 'errors=1' in output
    assert 'SWAPPED' not in output, 'a failed tier write was narrated as a successful swap'


def test_a_price_already_on_target_still_re_derives_the_tier():
    """The crash-recovery path. A run that died between the modify and the tier write leaves the
    member billed at the ladder price on a legacy slug, which `audit_subscription_status` cannot
    see. Skipping a matching price would make the documented re-run a permanent no-op."""
    user, _ = _legacy_stripe(tier='premium_yearly')

    with _stripe_api() as stripe_api:
        stripe_api.retrieve.return_value = _api_sub(price=BACKER_YEARLY_PRICE, product=BACKER_PRODUCT)
        output = _run('--fix', '--provider', 'stripe')

    user.refresh_from_db()
    assert not stripe_api.modify.called, 'a matching price was needlessly re-modified'
    assert user.premium_tier == 'backer', 'the interrupted tier write was never repaired'
    assert 'already=1' in output


def test_the_interval_comes_from_the_price_not_the_tier_name():
    """A legacy row whose tier and real price disagree about the cycle must NOT be swapped across
    intervals: Stripe resets the billing anchor on an interval change and invoices immediately,
    which `proration_behavior='none'` does not prevent. Here the tier says monthly, the price is
    yearly, and the yearly ladder price is what must be chosen."""
    _legacy_stripe(tier='premium_monthly', product=LEGACY_MONTHLY_PRODUCT)

    with _stripe_api() as stripe_api:
        stripe_api.retrieve.return_value = _api_sub(product=LEGACY_MONTHLY_PRODUCT, interval='year')
        stripe_api.modify.return_value = _api_sub(price=BACKER_YEARLY_PRICE, product=BACKER_PRODUCT)
        _run('--fix', '--provider', 'stripe')

    assert stripe_api.modify.call_args[1]['items'][0]['price'] == BACKER_YEARLY_PRICE


def test_a_subscription_on_an_unrecognised_price_is_not_repriced():
    """Without this the arm would swap ANY active subscription onto the target, so a user whose
    `premium_tier` had drifted from their real subscription could be downgraded."""
    user, _ = _legacy_stripe(tier='premium_yearly')

    with _stripe_api() as stripe_api:
        stripe_api.retrieve.return_value = _api_sub(price='price_mystery', product='prod_mystery')
        output = _run('--fix', '--provider', 'stripe')

    user.refresh_from_db()
    assert not stripe_api.modify.called
    assert user.premium_tier == 'premium_yearly'
    assert 'BLOCKED price' in output


@pytest.mark.parametrize('sub_kwargs,marker', [
    ({'status': 'past_due'}, 'SKIP status'),
    ({'status': 'canceled'}, 'SKIP status'),
    ({'cancel_at_period_end': True}, 'SKIP cancelling'),
    ({'cancel_at': 1800000000}, 'SKIP cancelling'),
])
def test_fragile_subscriptions_are_left_alone(sub_kwargs, marker):
    """Repricing a member mid-retry, or one who has already said they are leaving, is the worst
    possible moment to touch their billing. `cancel_at` alone is the scheduled-date cancellation
    shape that `membership_status` documents and this arm originally missed."""
    user, _ = _legacy_stripe(tier='premium_yearly', status=sub_kwargs.get('status', 'active'))

    with _stripe_api() as stripe_api:
        stripe_api.retrieve.return_value = _api_sub(**sub_kwargs)
        output = _run('--fix', '--provider', 'stripe')

    user.refresh_from_db()
    assert not stripe_api.modify.called, 'a fragile subscription was modified'
    assert user.premium_tier == 'premium_yearly'
    assert marker in output and 'skipped=1' in output


def test_a_multi_item_subscription_is_blocked_rather_than_guessed():
    _legacy_stripe(tier='premium_yearly')
    two = [{'id': 'si_a', 'price': {'id': 'p1', 'product': LEGACY_YEARLY_PRODUCT,
                                    'recurring': {'interval': 'year'}}},
           {'id': 'si_b', 'price': {'id': 'p2', 'product': BACKER_PRODUCT,
                                    'recurring': {'interval': 'year'}}}]

    with _stripe_api() as stripe_api:
        stripe_api.retrieve.return_value = _api_sub(items=two)
        output = _run('--fix', '--provider', 'stripe')

    assert not stripe_api.modify.called
    assert 'BLOCKED items' in output


def test_a_stripe_error_on_modify_is_reported_not_swallowed():
    import stripe as stripe_lib

    user, _ = _legacy_stripe(tier='premium_yearly')

    with _stripe_api() as stripe_api:
        stripe_api.retrieve.return_value = _api_sub()
        stripe_api.modify.side_effect = stripe_lib.error.APIConnectionError('network went away')
        output = _run('--fix', '--provider', 'stripe')

    user.refresh_from_db()
    assert user.premium_tier == 'premium_yearly'
    assert 'STRIPE ERROR' in output and 'errors=1' in output


def test_a_user_with_no_mirror_row_is_pointed_at_the_duplicate_customer_fix():
    """The likely real cause of a missing subscription is the duplicate-customer case the audit
    command exists to repoint, not a stale mirror."""
    profile = ProfileFactory()
    user = profile.user
    SubscriptionService.activate_subscription(user, 'premium_yearly', 'stripe')
    user.stripe_customer_id = 'cus_nothing_here'
    user.save(update_fields=['stripe_customer_id'])

    output = _run('--fix', '--provider', 'stripe')

    assert 'NO SUB' in output and 'audit_subscription_status --fix' in output


def test_live_mode_requires_the_live_ok_flag():
    """`bootstrap_support_skus` gates the CREATION of SKUs behind --live-ok; this command modifies
    live subscriptions of paying members and is copy-pasteable off the deploy checklist."""
    _legacy_stripe(tier='premium_yearly')

    with patch('users.management.commands.migrate_legacy_tiers.settings.STRIPE_MODE', 'live'):
        with pytest.raises(CommandError, match='--live-ok'):
            _run('--fix', '--provider', 'stripe')


def test_a_report_only_run_never_calls_the_processor_and_changes_nothing():
    user, _ = _legacy_stripe(tier='premium_yearly')

    with _stripe_api() as stripe_api:
        stripe_api.retrieve.return_value = _api_sub()
        output = _run('--provider', 'stripe')

    user.refresh_from_db()
    assert not stripe_api.modify.called
    assert user.premium_tier == 'premium_yearly'
    assert 'WOULD SWAP' in output and 'Report only' in output


# ── The PayPal arm ───────────────────────────────────────────────────────


def test_paypal_adoption_rewrites_the_tier_without_changing_billing():
    user, profile = _legacy_paypal(tier='premium_monthly')
    profile.user_is_premium = False
    profile.save(update_fields=['user_is_premium'])

    with patch('users.services.paypal_service.PayPalService.get_cached_subscription_snapshot',
               return_value={'plan_id': LEGACY_MONTHLY_PLAN}) as snapshot, \
            patch('users.services.paypal_service.requests') as http:
        output = _run('--fix', '--provider', 'paypal')

    user.refresh_from_db()
    profile.refresh_from_db()
    assert user.premium_tier == 'backer'
    # Started FALSE on purpose: asserting True on an already-premium user was vacuous, and deleting
    # the arm's `reconcile_premium` call survived the whole suite.
    assert profile.user_is_premium is True, 'the premium denorm was never reconciled'
    assert snapshot.called, 'the plan cross-check was skipped'
    assert not http.post.called, 'PayPal was asked to change something'
    assert 'premium_monthly -> backer' in output, 'the old tier was not recorded in the log line'
    assert 'migrated=1' in output


def test_paypal_adoption_keeps_the_open_period_so_tenure_survives():
    """Tenure is the thing a migration is most likely to quietly destroy: `premium_months` sums
    SubscriptionPeriod, and closing and reopening one would reset a founding member to zero."""
    user, _profile = _legacy_paypal()
    period = SubscriptionPeriod.objects.get(user=user, ended_at__isnull=True)
    period.started_at = timezone.now() - timedelta(days=400)
    period.save(update_fields=['started_at'])

    with patch('users.services.paypal_service.PayPalService.get_cached_subscription_snapshot',
               return_value={'plan_id': LEGACY_YEARLY_PLAN}):
        _run('--fix', '--provider', 'paypal')

    assert SubscriptionPeriod.objects.filter(user=user).count() == 1
    period.refresh_from_db()
    assert period.ended_at is None, 'the open period was closed by the migration'
    assert SubscriptionService.premium_tenure(user)['total_months'] == 13


def test_paypal_adoption_refuses_a_plan_it_does_not_recognise():
    user, _profile = _legacy_paypal()

    with patch('users.services.paypal_service.PayPalService.get_cached_subscription_snapshot',
               return_value={'plan_id': 'P-SOMETHINGELSE'}):
        output = _run('--fix', '--provider', 'paypal')

    user.refresh_from_db()
    assert user.premium_tier == 'premium_yearly', 'an unrecognised plan was migrated anyway'
    assert 'UNKNOWN PLAN' in output


def test_paypal_adoption_refuses_to_migrate_on_an_unverified_plan():
    """The escape hatch for an unexpected plan (a PayPal `supporter`, which is not in the adoption
    map and would drift back on its next re-activation) only works if the check actually runs. The
    snapshot returns None on any PayPal failure AND for 60s afterwards, so proceeding unverified
    would silently skip the guard exactly when it matters."""
    user, _profile = _legacy_paypal()

    with patch('users.services.paypal_service.PayPalService.get_cached_subscription_snapshot',
               return_value=None):
        output = _run('--fix', '--provider', 'paypal')

    user.refresh_from_db()
    assert user.premium_tier == 'premium_yearly', 'migrated without verifying the plan'
    assert 'UNVERIFIED' in output and 'errors=1' in output


def test_a_cancelling_paypal_member_is_migrated_but_flagged():
    """Adoption changes no billing, so there is nothing to spare them from -- but the operator
    should see it, and `activate_subscription` is avoided precisely because it would clear
    `paypal_cancel_at` and resurrect them."""
    user, _profile = _legacy_paypal()
    user.paypal_cancel_at = timezone.now() + timedelta(days=10)
    user.save(update_fields=['paypal_cancel_at'])

    with patch('users.services.paypal_service.PayPalService.get_cached_subscription_snapshot',
               return_value={'plan_id': LEGACY_YEARLY_PLAN}):
        output = _run('--fix', '--provider', 'paypal')

    user.refresh_from_db()
    assert user.premium_tier == 'backer'
    assert user.paypal_cancel_at is not None, 'the cancellation was wiped'
    assert 'cancelling, still in paid time' in output


# ── Selection ────────────────────────────────────────────────────────────


def test_a_user_already_on_a_ladder_slug_is_never_selected():
    profile = ProfileFactory()
    SubscriptionService.activate_subscription(profile.user, 'patron', 'stripe')

    output = _run('--fix')

    assert 'No users hold a legacy tier' in output


def test_a_user_with_no_provider_is_reported_rather_than_skipped():
    """A legacy tier with no provider falls into neither arm, and silently dropping it is how it
    would be missed forever."""
    profile = ProfileFactory()
    user = profile.user
    SubscriptionService.activate_subscription(user, 'premium_monthly', 'stripe')
    user.subscription_provider = None
    user.save(update_fields=['subscription_provider'])

    output = _run('--fix')

    user.refresh_from_db()
    assert 'NO PROVIDER' in output and 'errors=1' in output
    assert user.premium_tier == 'premium_monthly'


def test_migration_does_not_move_the_worn_mark():
    """`worn_supporter_level` already collapsed the legacy slugs onto their ladder level, so the
    denorm every name on the site renders from is identical before and after. If this fails, the
    migration has become visible on the supporter wall and the leaderboards."""
    user, profile = _legacy_paypal(tier='premium_monthly')
    profile.refresh_from_db()
    before = profile.display_mark
    assert before == 'backer', 'the legacy holder was not already wearing Backer'

    with patch('users.services.paypal_service.PayPalService.get_cached_subscription_snapshot',
               return_value={'plan_id': LEGACY_MONTHLY_PLAN}):
        _run('--fix', '--provider', 'paypal')

    profile.refresh_from_db()
    assert profile.display_mark == before


def test_the_stripe_swap_sends_no_welcome_email():
    """A migration is not a purchase. `activate_subscription` announces only for its
    `activation_events`, and the migration reaches it with no event type at all."""
    from core.models import EmailLog

    user, _ = _legacy_stripe(tier='premium_monthly', product=LEGACY_MONTHLY_PRODUCT)

    with _stripe_api() as stripe_api, \
            patch('users.services.subscription_service.send_subscription_notification') as discord:
        stripe_api.retrieve.return_value = _api_sub(product=LEGACY_MONTHLY_PRODUCT, interval='month')
        stripe_api.modify.return_value = _api_sub(price=BACKER_MONTHLY_PRICE, product=BACKER_PRODUCT,
                                                  interval='month')
        _run('--fix', '--provider', 'stripe')

    assert not discord.called, 'the migration announced itself in Discord'
    assert not EmailLog.objects.filter(user=user, email_type='subscription_welcome').exists()


def test_live_ok_actually_unlocks_the_live_run():
    """The gate's escape hatch, which the deploy checklist tells the operator to use. Dropping
    `and not options['live_ok']` from the guard passed the whole suite: nothing proved the flag
    did anything except that its absence raised."""
    _legacy_stripe(tier='premium_yearly')

    with patch('users.management.commands.migrate_legacy_tiers.settings.STRIPE_MODE', 'live'),             _stripe_api() as stripe_api:
        stripe_api.retrieve.return_value = _api_sub()
        stripe_api.modify.return_value = _api_sub(price=BACKER_YEARLY_PRICE, product=BACKER_PRODUCT)
        _run('--fix', '--provider', 'stripe', '--live-ok')

    assert stripe_api.modify.called, '--live-ok did not unlock the run'


def test_the_paypal_arm_needs_no_live_ok():
    """Adoption changes no billing, so gating it behind the live flag would be friction with nothing
    behind it. Broadening the guard to every provider passed the suite."""
    user, _ = _legacy_paypal(tier='premium_monthly')

    with patch('users.management.commands.migrate_legacy_tiers.settings.STRIPE_MODE', 'live'),             patch('users.services.paypal_service.PayPalService.get_cached_subscription_snapshot',
                  return_value={'plan_id': LEGACY_MONTHLY_PLAN}):
        _run('--fix', '--provider', 'paypal')

    user.refresh_from_db()
    assert user.premium_tier == 'backer'


def test_the_live_subscription_is_preferred_over_a_dead_one():
    """A customer can carry an old canceled subscription alongside the live one. Both the status
    preference and the ordering in `_find_mirror` survived deletion because no test ever gave one
    customer two subscriptions."""
    user, _ = _legacy_stripe(tier='premium_yearly', customer_id='cus_two', sub_id='sub_live')
    Subscription.objects.create(
        id='sub_dead', customer=Customer.objects.get(id='cus_two'),
        stripe_data={'status': 'canceled',
                     'plan': {'product': LEGACY_YEARLY_PRODUCT, 'id': 'price_legacy'}},
    )

    with _stripe_api() as stripe_api:
        stripe_api.retrieve.return_value = _api_sub(sub_id='sub_live')
        stripe_api.modify.return_value = _api_sub(sub_id='sub_live', price=BACKER_YEARLY_PRICE,
                                                  product=BACKER_PRODUCT)
        _run('--fix', '--provider', 'stripe')

    assert stripe_api.retrieve.call_args[0][0] == 'sub_live', 'the dead subscription was chosen'
    user.refresh_from_db()
    assert user.premium_tier == 'backer'


def test_a_paypal_tier_write_that_does_not_stick_is_reported():
    """The PayPal arm's verify, which mirrored the Stripe one and was equally unpinned."""
    user, _ = _legacy_paypal(tier='premium_monthly')

    def _clobber(u, **kwargs):
        u.premium_tier = None
        u.save(update_fields=['premium_tier'])

    with patch('users.services.paypal_service.PayPalService.get_cached_subscription_snapshot',
               return_value={'plan_id': LEGACY_MONTHLY_PLAN}),             patch.object(SubscriptionService, 'reconcile_premium', side_effect=_clobber):
        output = _run('--fix', '--provider', 'paypal')

    assert 'TIER WRONG' in output and 'errors=1' in output
    assert 'ADOPTED' not in output
