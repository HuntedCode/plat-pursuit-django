"""`SubscriptionService.subscription_period_end` and the four grace paths that read it.

THE BUG THIS PINS, found 2026-09-06 and verified against a live production row: Stripe moved
`current_period_end` off the subscription and onto its ITEMS (the 2025-03-31 "basil" API version).
`stripe==14.1.0` was pinned on 2025-12-26, defaulting to `2025-12-15.clover`; the grace-period code
was written on 2026-01-14, three weeks later, reading a top-level key that production payloads no
longer carry.

It failed silently and it failed CLOSED. `.get()` returned None, the `if period_end_ts and ...` guard
short-circuited, and every caller took its not-in-grace branch. Three of them then revoke premium
from a member who has paid for time they have not used, one of those being the weekly
`audit_subscription_status --fix` cron. The symptom is indistinguishable from an ordinary expiry.

Every test here uses CLOVER-shaped payloads (period on the item, absent at the top level) because
that is what production actually stores. The old shape is covered too, since the mirror still holds
rows written at the older version and a webhook may deliver either.
"""
from datetime import timedelta
from io import StringIO
from unittest.mock import patch

import pytest
from django.core.management import call_command
from django.utils import timezone
from djstripe.models import Customer, Subscription

from users.services.subscription_service import SubscriptionService
from tests.factories import ProfileFactory

pytestmark = pytest.mark.django_db

#: Test-mode product for the tier every fixture here uses, so the payload and the tier agree.
PATRON_PRODUCT = 'prod_V735dER1GhMN4k'


def _ts(**delta):
    return int((timezone.now() + timedelta(**delta)).timestamp())


def _clover(status='canceled', period_end=None, product=PATRON_PRODUCT, **extra):
    """The post-basil shape: period AND product on the item, neither at the top level.

    Deliberately carries NO top-level `plan`. An earlier version of this fixture invented one, which
    was inert but false, and inventing the exact field production code depends on is precisely how
    the previous generation of these tests hid a live bug. Anything that needs the product must now
    read it off the item, which is what production code has to do anyway.
    """
    return {
        'status': status,
        'items': {'data': [{'id': 'si_1', 'current_period_end': period_end,
                            'price': {'id': 'price_patron', 'product': product}}]},
        **extra,
    }


def _legacy_shape(status='canceled', period_end=None, product=PATRON_PRODUCT, **extra):
    """The pre-basil payload: period and `plan` at the top level, no item period.

    Both shapes must keep working: the mirror still holds rows written before the change, and a
    webhook delivers whatever version the account's endpoint is pinned to.
    """
    return {
        'status': status,
        'plan': {'product': product, 'id': 'price_patron'},
        'current_period_end': period_end,
        'items': {'data': [{'id': 'si_1', 'price': {'id': 'price_patron', 'product': product}}]},
        **extra,
    }


def _subscriber(stripe_data, tier='patron', customer_id='cus_grace', sub_id='sub_grace'):
    profile = ProfileFactory()
    user = profile.user
    SubscriptionService.activate_subscription(user, tier, 'stripe')
    user.stripe_customer_id = customer_id
    user.save(update_fields=['stripe_customer_id'])
    customer = Customer.objects.create(id=customer_id, subscriber=user)
    Subscription.objects.create(id=sub_id, customer=customer, stripe_data=stripe_data)
    return user, profile


# ── The reader ───────────────────────────────────────────────────────────


def test_it_reads_the_period_off_the_item():
    """The shape production actually stores, confirmed on a live row."""
    assert SubscriptionService.subscription_period_end(_clover(period_end=1798940526)) == 1798940526


def test_it_still_reads_the_old_top_level_shape():
    """Rows written before the API moved are still in the mirror, and a webhook may deliver either
    shape depending on the account's configured version. Both must resolve."""
    assert SubscriptionService.subscription_period_end(
        _legacy_shape(period_end=1798940526)) == 1798940526


def test_it_prefers_the_item_when_both_are_present():
    data = _clover(period_end=2000000000)
    data['current_period_end'] = 1000000000
    assert SubscriptionService.subscription_period_end(data) == 2000000000


def test_it_takes_the_latest_end_across_items():
    """With more than one item the member is paid up until the LAST lapses, and the safe direction
    for a grace check is to keep access rather than end it early."""
    data = _clover(period_end=1000000000)
    data['items']['data'].append({'id': 'si_2', 'current_period_end': 2000000000})
    assert SubscriptionService.subscription_period_end(data) == 2000000000


@pytest.mark.parametrize('payload', [None, {}, {'items': None}, {'items': {'data': []}},
                                     {'items': {'data': [{'id': 'si_1'}]}}])
def test_it_returns_none_rather_than_raising_on_anything_missing(payload):
    """Callers guard on falsiness; a raise here would take down the membership page."""
    assert SubscriptionService.subscription_period_end(payload) is None


# ── The three paths that were revoking premium early ─────────────────────


def test_update_user_subscription_keeps_premium_during_grace():
    """THE revocation. A `canceled` subscription with paid time left must keep premium until the
    period ends. With the top-level read, `period_end_ts` was None, the grace branch never matched,
    and this fell straight through to `deactivate_subscription` -- nulling the tier, closing the
    period carrying their tenure, and pulling their Discord role, the moment they cancelled.
    """
    user, profile = _subscriber(_clover(period_end=_ts(days=20)))

    assert SubscriptionService.update_user_subscription(user) is True

    user.refresh_from_db()
    profile.refresh_from_db()
    assert user.premium_tier == 'patron', 'premium was revoked during a paid grace period'
    assert profile.user_is_premium is True


def test_update_user_subscription_still_deactivates_once_the_period_has_passed():
    """The other side of the same branch: grace that has genuinely expired must still end."""
    user, _ = _subscriber(_clover(period_end=_ts(days=-2)))

    assert SubscriptionService.update_user_subscription(user) is False

    user.refresh_from_db()
    assert user.premium_tier is None


def test_membership_status_reports_grace_with_its_end_date():
    """The page told a paying member in grace that they had no membership at all."""
    end = _ts(days=20)
    user, _ = _subscriber(_clover(period_end=end))

    ms = SubscriptionService.membership_status(user)

    assert ms.state == 'grace' and ms.provider == 'stripe'
    assert ms.grace_until is not None
    assert int(ms.grace_until.timestamp()) == end


def test_membership_status_dates_a_scheduled_cancel_from_the_item():
    """`cancels_at` on an ACTIVE subscription. This one survived the bug only because Stripe happens
    to set `cancel_at` alongside `cancel_at_period_end`; with only the flag set it read as no date."""
    end = _ts(days=12)
    user, _ = _subscriber(_clover(status='active', period_end=end, cancel_at_period_end=True))

    ms = SubscriptionService.membership_status(user)

    assert ms.state == 'active'
    assert ms.cancels_at is not None and int(ms.cancels_at.timestamp()) == end


def test_the_weekly_audit_reports_grace_instead_of_revoking():
    """`audit_subscription_status --fix` runs weekly on prod. Reading the top-level key, a member in
    paid grace was not recognised as grace and the sweep revoked their premium early."""
    user, _ = _subscriber(_clover(period_end=_ts(days=20)))

    out = StringIO()
    call_command('audit_subscription_status', '--fix', '--no-email', stdout=out)
    output = out.getvalue()

    user.refresh_from_db()
    assert '[GRACE]' in output
    assert 'NEEDS FIX' not in output
    assert user.premium_tier == 'patron', 'the weekly cron revoked a grace member'


def test_the_weekly_audit_still_revokes_once_grace_has_expired():
    user, _ = _subscriber(_clover(period_end=_ts(days=-3)))

    out = StringIO()
    call_command('audit_subscription_status', '--fix', '--no-email', stdout=out)

    user.refresh_from_db()
    assert user.premium_tier is None, 'an expired subscription kept premium'


def test_the_membership_page_shows_a_next_billing_date():
    """The display path. Cosmetic next to the revocations, but it is the same missing key: an active
    member simply saw no next-billing date."""
    end = _ts(days=25)
    user, _ = _subscriber(_clover(status='active', period_end=end))

    from django.test import Client
    client = Client()
    client.force_login(user)
    response = client.get('/support/membership/')

    assert response.status_code == 200
    next_billing = response.context.get('next_billing')
    assert next_billing is not None, 'no next billing date was resolved'
    assert int(next_billing.timestamp()) == end


# ── Backwards compatibility ──────────────────────────────────────────────


def test_an_old_shape_row_still_grants_grace():
    """Rows written before the API change are still in the mirror. The fix must not trade one
    broken shape for the other."""
    user, _ = _subscriber(_legacy_shape(period_end=_ts(days=20)))

    assert SubscriptionService.update_user_subscription(user) is True
    assert SubscriptionService.membership_status(user).state == 'grace'


# ── The Django 5 `timezone.utc` removal, same bug class ──────────────────


def test_the_renewal_receipt_computes_its_billing_date_and_sends():
    """`_send_payment_succeeded_email` built its date with `django.utils.timezone.utc`, removed in
    Django 5.0. `except (ValueError, OSError)` does not catch AttributeError, so it raised out of
    the email path on EVERY renewal; the webhook view's catch-all logged it and returned 200, so
    subscription state stayed correct while renewal receipts silently stopped sending.

    The existing template tests could never catch this: they pass `next_billing_date` in ready-made,
    so they exercise the rendering and not the line that computes it.
    """
    from core.models import EmailLog

    profile = ProfileFactory()
    user = profile.user
    SubscriptionService.activate_subscription(user, 'patron', 'stripe')

    invoice = {
        'billing_reason': 'subscription_cycle',
        'amount_paid': 1500,
        'currency': 'usd',
        'lines': {'data': [{'period': {'end': 1798940526}}]},
    }
    SubscriptionService.handle_payment_succeeded(user, invoice)

    log = EmailLog.objects.filter(user=user, email_type='payment_succeeded').first()
    assert log is not None, 'the renewal receipt did not send'
    assert log.status == 'sent'
    # The DATE ITSELF, not just the absence of a raise: without this, replacing the whole
    # computation with a hardcoded string passes. Derived from the timestamp rather than typed in,
    # so the assertion cannot drift away from the input.
    from datetime import datetime, timezone as dt_tz
    from django.core import mail

    expected = datetime.fromtimestamp(1798940526, tz=dt_tz.utc).strftime('%B %d, %Y')
    assert mail.outbox, 'nothing reached the outbox'
    # The date renders in the HTML part; `.body` is the stripped plain-text alternative.
    html = ' '.join(content for content, _mime in mail.outbox[-1].alternatives)
    assert expected in html, f'the billing date {expected!r} was not rendered'


def test_an_offset_less_iso_string_does_not_crash_the_template_filters():
    """The latent twin of the same removal. Only reachable for an ISO string with no offset (the
    `Z` form is normalised first), which is why it never fired -- but AttributeError is outside the
    filters' except clause, so it would have been a 500 on the page rather than a passthrough."""
    from core.templatetags.custom_filters import iso_datetime, iso_naturaltime

    naive = '2026-09-06T12:00:00'
    assert iso_datetime(naive).tzinfo is not None, 'the naive string was not made aware'
    assert isinstance(iso_naturaltime(naive), str)


# ── Newly load-bearing behaviour the fix exposed ─────────────────────────


def test_the_most_recent_canceled_subscription_decides_grace():
    """A repeat subscriber has MORE THAN ONE canceled row, and `.first()` on an unordered queryset
    is a heap-order coin flip. While the period read always returned None this was invisible (always
    revoke); the fix made it decide whether a paying member keeps access.

    Old sub: cancelled and long expired. New sub: cancelled with paid time left. Grace must be read
    from the new one. `audit_subscription_status` already ordered its lookup, so before this the
    audit could report GRACE for the very member the service was revoking.
    """
    user, _ = _subscriber(_clover(period_end=_ts(days=-300)), customer_id='cus_repeat',
                          sub_id='sub_old')
    old = Subscription.objects.get(id='sub_old')
    old.created = timezone.now() - timedelta(days=400)
    old.save(update_fields=['created'])
    Subscription.objects.create(
        id='sub_new', customer=Customer.objects.get(id='cus_repeat'),
        created=timezone.now() - timedelta(days=30),
        stripe_data=_clover(period_end=_ts(days=20)),
    )

    assert SubscriptionService.update_user_subscription(user) is True
    user.refresh_from_db()
    assert user.premium_tier == 'patron', 'the stale canceled row revoked a member still in grace'
    assert SubscriptionService.membership_status(user).state == 'grace'


def test_an_active_subscription_resolves_its_tier_without_a_top_level_plan():
    """`update_user_subscription` resolved the tier through the deprecated top-level `plan`, whose
    miss arm is `deactivate_subscription`. Stripe only ever populates `plan` for SINGLE-item
    subscriptions and stripe-python 14 no longer models it, so the day it stops arriving every
    ACTIVE member's next webhook would revoke premium they are paying for. Reading the item's
    `price.product` first makes that unreachable.

    A live prod row still carried `plan` on 2026-09-06, so this is defensive rather than a fix for
    something currently firing -- which is exactly why it needs a test rather than a comment.
    """
    no_plan = {
        'status': 'active',
        'items': {'data': [{'id': 'si_1', 'current_period_end': _ts(days=25),
                            'price': {'id': 'price_x', 'product': 'prod_ThqpPjDyERnoaF'}}]},
    }
    user, profile = _subscriber(no_plan, tier='premium_yearly')

    assert SubscriptionService.update_user_subscription(user) is True

    user.refresh_from_db()
    profile.refresh_from_db()
    assert user.premium_tier == 'premium_yearly', 'an active paying member was revoked'
    assert profile.user_is_premium is True


def test_a_ladder_price_still_rescues_a_tier_when_the_product_map_misses():
    """The existing belt: an active subscription on a known ladder PRICE is a paying supporter even
    if the product map has a gap. It read `plan['id']`, so it needed the same item-first treatment."""
    unknown_product = {
        'status': 'active',
        'items': {'data': [{'id': 'si_1', 'current_period_end': _ts(days=25),
                            'price': {'id': 'price_1U6ozjR5jhcbjB32vStaZGFu',
                                      'product': 'prod_NOT_IN_THE_MAP'}}]},
    }
    user, _ = _subscriber(unknown_product, tier='backer')

    assert SubscriptionService.update_user_subscription(user) is True
    user.refresh_from_db()
    assert user.premium_tier == 'backer'


@pytest.mark.parametrize('payload,expected', [
    ({'items': [{'current_period_end': 111}], 'current_period_end': 222}, 111),
    ({'items': {'data': [None, {'current_period_end': 333}]}}, 333),
    ({'items': {'data': [{'current_period_end': '900000000'},
                         {'current_period_end': '1798940526'}]}}, None),
    ({'items': {'data': [{'current_period_end': True}]}, 'current_period_end': 444}, 444),
])
def test_malformed_payloads_never_raise_and_never_return_a_wrong_value(payload, expected):
    """None of these are shapes Stripe emits, but this reader sits on the membership page and inside
    the weekly cron, so a raise is a 500 or a dead run with no report email. The string case is the
    sharp one: `max()` over strings compares LEXICALLY, so '900000000' beats '1798940526' and the
    helper would hand back the EARLIER date -- a wrong answer, which revokes premium."""
    assert SubscriptionService.subscription_period_end(payload) == expected


def test_an_unmigrated_paypal_holder_keeps_their_cycle_when_paypal_is_unreachable():
    """The `describe_billing` legacy fallback, which had no positive test after the adoption path
    was added: replacing the whole branch with `cycle = None` was caught by nothing.

    Its whole purpose is that the membership page's Billing row must not VANISH for an un-migrated
    legacy holder during a PayPal snapshot miss (an outage, or the 60s failure marker). A migrated
    member correctly still falls through to omission, because `backer` cannot say which cycle.
    """
    from users.services.subscription_service import MembershipStatus

    profile = ProfileFactory()
    user = profile.user
    SubscriptionService.activate_subscription(user, 'premium_yearly', 'paypal')
    user.paypal_subscription_id = 'I-UNMIGRATED'
    user.save(update_fields=['paypal_subscription_id'])

    with patch('users.services.paypal_service.PayPalService.get_cached_subscription_snapshot',
               return_value=None):
        billing = SubscriptionService.describe_billing(user, MembershipStatus('active', 'paypal'))

    assert billing == {'amount': None, 'cycle': 'year'}


def test_the_audit_repoints_a_duplicate_customer_instead_of_calling_it_grace():
    """The regression the grace fix itself introduced.

    In the duplicate-customer case the OLD customer keeps the old canceled sub while the member pays
    under a new one. Granting grace `continue`d past `_resolve_stripe_row`, where the repoint lives,
    so a PAYING member was filed as GRACE and their stale pointer survived for a whole period, with
    the membership page telling them they had cancelled.
    """
    user, _ = _subscriber(_clover(period_end=_ts(days=20)), customer_id='cus_stale',
                          sub_id='sub_stale')
    live_customer = Customer.objects.create(id='cus_live', subscriber=user)
    Subscription.objects.create(
        id='sub_live', customer=live_customer,
        stripe_data={'status': 'active',
                     'items': {'data': [{'id': 'si_1', 'current_period_end': _ts(days=25),
                                         'price': {'id': 'price_patron',
                                                   'product': PATRON_PRODUCT}}]}},
    )

    out = StringIO()
    call_command('audit_subscription_status', '--fix', '--no-email', stdout=out)
    output = out.getvalue()

    user.refresh_from_db()
    assert 'GRACE+MISMATCH' in output or '[MISMATCH]' in output
    assert user.stripe_customer_id == 'cus_live', 'the stale customer pointer was not repointed'
    assert user.premium_tier == 'patron', 'a paying member lost premium'


@pytest.mark.parametrize('lines', [
    {'data': [{'period': {'end': 'not-a-timestamp'}}]},   # TypeError
    {'data': [{'period': {'end': 1e20}}]},                # OverflowError
    {'data': [{'period': None}]},                         # AttributeError
    None,                                                 # AttributeError on .get('data')
])
def test_a_malformed_invoice_still_sends_the_receipt_without_a_date(lines):
    """The receipt must degrade to "no date", never to "no receipt".

    The original guard was `except (ValueError, OSError)`, which catches none of these. Each one
    would raise out of the email path into the webhook's catch-all, which logs and returns 200 --
    the member is charged and hears nothing. That is the exact failure mode this file already fixed
    once, so the guard has to cover the shapes that reach it rather than the one that was noticed.
    """
    from core.models import EmailLog

    profile = ProfileFactory()
    user = profile.user
    SubscriptionService.activate_subscription(user, 'patron', 'stripe')

    SubscriptionService.handle_payment_succeeded(user, {
        'billing_reason': 'subscription_cycle',
        'amount_paid': 1500,
        'currency': 'usd',
        'lines': lines,
    })

    assert EmailLog.objects.filter(user=user, email_type='payment_succeeded',
                                   status='sent').exists(), 'the receipt was dropped entirely'
