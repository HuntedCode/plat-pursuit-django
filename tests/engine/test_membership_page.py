"""The membership page's backend: the grace-aware status read, tenure, billing display, and the
PayPal snapshot cache.

The headline fix is GRACE: a cancelled Stripe member with paid time left keeps premium, but
`has_active_subscription` deliberately reports (False, None) for the double-subscribe guard --
so the page needs its own read (`membership_status`) or it tells a paying member they have no
subscription. Phase 2 adds the page itself; these tests pin the state machinery.
"""
from datetime import datetime, timedelta, timezone as dt_timezone
from unittest.mock import patch

import pytest
from django.core.cache import cache
from django.urls import reverse
from django.utils import timezone

from users.services.subscription_service import MembershipStatus, SubscriptionService
from tests.factories import ProfileFactory, UserFactory

pytestmark = pytest.mark.django_db


def _fake_sub(**stripe_data):
    return type('S', (), {'stripe_data': stripe_data})()


def _stripe_user(tier='patron'):
    user = UserFactory()
    user.subscription_provider = 'stripe'
    user.stripe_customer_id = 'cus_ms_test'
    user.premium_tier = tier
    user.save()
    return user


def _with_subs(*subs):
    """Patch the djstripe Subscription manager so filter(...).first() answers per status."""
    by_status = {}
    for sub in subs:
        by_status[sub.stripe_data['status']] = sub

    class FakeQS:
        def __init__(self, statuses):
            self.statuses = statuses

        def first(self):
            for status in self.statuses:
                if status in by_status:
                    return by_status[status]
            return None

        def exists(self):
            return self.first() is not None

    class FakeManager:
        def filter(self, **kw):
            statuses = kw.get('stripe_data__status__in') or [kw.get('stripe_data__status')]
            return FakeQS([s for s in statuses if s])

    return patch('users.services.subscription_service.Subscription.objects', FakeManager())


# ------------------------------------------------------------------ membership_status ----

def test_an_active_stripe_sub_reads_active_with_no_end_date():
    user = _stripe_user()
    with _with_subs(_fake_sub(status='active', current_period_end=4102444800)):
        ms = SubscriptionService.membership_status(user)
    assert ms.state == 'active' and ms.provider == 'stripe'
    assert ms.cancels_at is None and ms.grace_until is None


def test_a_portal_cancel_surfaces_the_end_date_while_still_active():
    """cancel_at_period_end leaves the sub 'active'; the end date is real information the old
    page presented as an ordinary next-billing date."""
    user = _stripe_user()
    end_ts = 4102444800
    with _with_subs(_fake_sub(status='active', current_period_end=end_ts,
                              cancel_at_period_end=True)):
        ms = SubscriptionService.membership_status(user)
    assert ms.state == 'active'
    assert ms.cancels_at == datetime.fromtimestamp(end_ts, tz=dt_timezone.utc)


def test_past_due_reads_past_due():
    user = _stripe_user()
    with _with_subs(_fake_sub(status='past_due')):
        ms = SubscriptionService.membership_status(user)
    assert ms.state == 'past_due' and ms.provider == 'stripe'


def test_the_grace_hole_is_closed_and_the_guard_is_untouched():
    """THE fix: canceled sub + future period end + premium tier = grace, with a date. The
    companion assertion pins that `has_active_subscription` still says (False, None) -- that
    boolean is the double-subscribe guard, and (False, None) during grace is what lets a
    cancelled member re-subscribe (the one path v1 supports)."""
    user = _stripe_user()
    future_ts = int((timezone.now() + timedelta(days=20)).timestamp())
    with _with_subs(_fake_sub(status='canceled', current_period_end=future_ts)):
        ms = SubscriptionService.membership_status(user)
        guard = SubscriptionService.has_active_subscription(user)
    assert ms.state == 'grace' and ms.provider == 'stripe'
    assert ms.grace_until > timezone.now()
    assert guard == (False, None), 'the guard semantics must not change'


def test_a_stale_stripe_cancel_never_claims_a_paypal_member():
    """The reachable double-provider edge: cancel Stripe, re-subscribe via PayPal inside the
    old paid period (the guard permits exactly this). The stale canceled Stripe row must not
    tell a paying PayPal member their membership is ending."""
    user = _stripe_user(tier='patron')
    user.subscription_provider = 'paypal'
    user.paypal_subscription_id = 'I-REJOINED'
    user.paypal_cancel_at = None
    user.save()
    future_ts = int((timezone.now() + timedelta(days=20)).timestamp())
    with _with_subs(_fake_sub(status='canceled', current_period_end=future_ts)):
        ms = SubscriptionService.membership_status(user)
    assert ms.state == 'active' and ms.provider == 'paypal'


def test_a_scheduled_cancel_at_shows_without_the_period_end_flag():
    """cancel_at can be set alone (a cancel scheduled for a specific date); the end date is
    real information either way."""
    user = _stripe_user()
    end_ts = 4102444800
    with _with_subs(_fake_sub(status='active', current_period_end=1, cancel_at=end_ts)):
        ms = SubscriptionService.membership_status(user)
    assert ms.cancels_at == datetime.fromtimestamp(end_ts, tz=dt_timezone.utc)


def test_an_expired_cancel_is_none_not_grace():
    user = _stripe_user()
    past_ts = int((timezone.now() - timedelta(days=2)).timestamp())
    with _with_subs(_fake_sub(status='canceled', current_period_end=past_ts)):
        ms = SubscriptionService.membership_status(user)
    assert ms.state == 'none'


def test_paypal_grace_both_directions():
    user = UserFactory()
    user.subscription_provider = 'paypal'
    user.paypal_subscription_id = 'I-MSTEST'
    user.premium_tier = 'patron'
    user.paypal_cancel_at = timezone.now() + timedelta(days=9)
    user.save()
    ms = SubscriptionService.membership_status(user)
    assert ms.state == 'grace' and ms.provider == 'paypal'
    assert ms.grace_until == user.paypal_cancel_at

    user.paypal_cancel_at = timezone.now() - timedelta(days=1)
    user.save()
    assert SubscriptionService.membership_status(user).state == 'none'

    user.paypal_cancel_at = None
    user.save()
    assert SubscriptionService.membership_status(user).state == 'active'


def test_the_grace_branch_no_longer_crashes_on_django5():
    """update_user_subscription's canceled-with-time-left branch used django.utils.timezone.utc,
    removed in Django 5.0 -- it raised AttributeError every time it actually ran. The call now
    completes and keeps premium."""
    user = _stripe_user()
    future_ts = int((timezone.now() + timedelta(days=20)).timestamp())
    with _with_subs(_fake_sub(status='canceled', current_period_end=future_ts)):
        assert SubscriptionService.update_user_subscription(user) is True


# ---------------------------------------------------------------------------- tenure ----

def test_tenure_math_and_the_milestones_parity():
    from milestones.metrics import MILESTONE_METRICS
    from users.models import SubscriptionPeriod

    profile = ProfileFactory()
    user = profile.user
    now = timezone.now()
    SubscriptionPeriod.objects.create(user=user, started_at=now - timedelta(days=400),
                                      ended_at=now - timedelta(days=300), provider='stripe')
    SubscriptionPeriod.objects.create(user=user, started_at=now - timedelta(days=100),
                                      provider='stripe')

    tenure = SubscriptionService.premium_tenure(user)
    assert tenure['total_days'] == 200
    assert tenure['total_months'] == 6
    assert tenure['member_since'] == now - timedelta(days=400)
    assert tenure['current_started'] == now - timedelta(days=100)

    assert MILESTONE_METRICS['premium_months'](profile) == tenure['total_months'], \
        'milestones and the membership page must agree on tenure'

    # And it agrees BY DELEGATION, not by parallel implementation.
    with patch.object(SubscriptionService, 'premium_tenure',
                      return_value={'total_months': 99}) as delegate:
        assert MILESTONE_METRICS['premium_months'](profile) == 99
    delegate.assert_called_once_with(user)


# --------------------------------------------------------------------------- billing ----

def test_stripe_billing_reads_the_plan_from_the_sub():
    user = _stripe_user()
    sub = _fake_sub(status='active', plan={'amount': 1500, 'interval': 'month'})
    ms = MembershipStatus('active', 'stripe', stripe_sub=sub)
    assert SubscriptionService.describe_billing(user, ms) == {'amount': 15, 'cycle': 'month'}


def test_billing_is_omitted_not_guessed_when_unknown():
    user = _stripe_user()
    ms = MembershipStatus('active', 'stripe', stripe_sub=_fake_sub(status='active'))
    assert SubscriptionService.describe_billing(user, ms) == {'amount': None, 'cycle': None}


def test_paypal_ladder_billing_resolves_from_the_plan_id():
    """A real sandbox plan id walks PAYPAL_LADDER_PLANS to slug + interval; the amount comes
    from SUPPORT_TIERS. This was the untested path a wrong mode key would silently blank."""
    from users.constants import PAYPAL_LADDER_PLANS
    user = UserFactory()
    user.subscription_provider = 'paypal'
    user.paypal_subscription_id = 'I-LADDER'
    user.premium_tier = 'patron'
    user.save()
    plan_id = PAYPAL_LADDER_PLANS['sandbox']['patron']['monthly']
    ms = MembershipStatus('active', 'paypal')
    with patch('users.services.paypal_service.PayPalService.get_cached_subscription_snapshot',
               return_value={'status': 'ACTIVE', 'next_billing_time': None, 'plan_id': plan_id}):
        billing = SubscriptionService.describe_billing(user, ms)
    assert billing == {'amount': 15, 'cycle': 'month'}


def test_paypal_legacy_billing_gives_cycle_only():
    """Legacy PayPal prices live only on the processor -- the cycle is knowable from the tier,
    the dollar figure is never guessed."""
    user = UserFactory()
    user.subscription_provider = 'paypal'
    user.paypal_subscription_id = 'I-LEGACY'
    user.premium_tier = 'premium_yearly'
    user.save()
    ms = MembershipStatus('active', 'paypal')
    with patch('users.services.paypal_service.PayPalService.get_cached_subscription_snapshot',
               return_value={'status': 'ACTIVE', 'next_billing_time': None, 'plan_id': 'P-UNKNOWN'}):
        billing = SubscriptionService.describe_billing(user, ms)
    assert billing == {'amount': None, 'cycle': 'year'}


# -------------------------------------------------------------- the PayPal snapshot ----

def test_the_paypal_snapshot_is_cached_and_busted():
    from users.services.paypal_service import PayPalService

    sub_id = 'I-CACHETEST'
    cache.delete(PayPalService.SUB_CACHE_KEY.format(id=sub_id))
    details = {'status': 'ACTIVE', 'plan_id': 'P-1',
               'billing_info': {'next_billing_time': '2026-09-01T00:00:00Z'}}

    with patch.object(PayPalService, 'get_subscription_details',
                      return_value=details) as fetch:
        first = PayPalService.get_cached_subscription_snapshot(sub_id)
        second = PayPalService.get_cached_subscription_snapshot(sub_id)
    assert fetch.call_count == 1, 'the second read must come from cache'
    assert first == second == {'status': 'ACTIVE',
                               'next_billing_time': '2026-09-01T00:00:00Z', 'plan_id': 'P-1'}

    # A webhook for the subscription outdates the snapshot.
    with patch.object(PayPalService, 'bust_subscription_snapshot') as bust:
        PayPalService.handle_webhook_event('BILLING.SUBSCRIPTION.CANCELLED', {'id': sub_id})
    bust.assert_called_once_with(sub_id)

    PayPalService.bust_subscription_snapshot(sub_id)
    assert cache.get(PayPalService.SUB_CACHE_KEY.format(id=sub_id)) is None


def test_a_snapshot_fetch_failure_negative_caches_briefly():
    """A failure is marked for 60s -- an outage costs one timeout per minute, not one 30s hang
    per page GET -- and after the marker clears the fetch retries."""
    from users.services.paypal_service import PayPalService

    sub_id = 'I-FAILTEST'
    PayPalService.bust_subscription_snapshot(sub_id)
    with patch.object(PayPalService, 'get_subscription_details',
                      side_effect=RuntimeError('paypal down')) as fetch:
        assert PayPalService.get_cached_subscription_snapshot(sub_id) is None
        assert PayPalService.get_cached_subscription_snapshot(sub_id) is None
        assert fetch.call_count == 1, 'the second read must hit the 60s failure marker'

        PayPalService.bust_subscription_snapshot(sub_id)
        assert PayPalService.get_cached_subscription_snapshot(sub_id) is None
        assert fetch.call_count == 2, 'a cleared marker must allow the retry'


def test_a_malformed_paypal_response_is_not_pinned():
    """A 200 with no status is not a snapshot worth serving for 8 hours."""
    from users.services.paypal_service import PayPalService

    sub_id = 'I-MALFORMED'
    PayPalService.bust_subscription_snapshot(sub_id)
    with patch.object(PayPalService, 'get_subscription_details', return_value={}):
        assert PayPalService.get_cached_subscription_snapshot(sub_id) is None
    assert cache.get(PayPalService.SUB_CACHE_KEY.format(id=sub_id)) is None


# ------------------------------------------------------------------ email URL repoint ----

def test_lifecycle_links_ride_the_route_name():
    """Every literal '/users/subscription-management/' became a reverse() call, so the route
    move reached every email and notification automatically. Repo-wide: the first sweep missed
    a seventh literal in an admin resend action that a module-scoped check was blind to."""
    import pathlib as _pathlib

    root = _pathlib.Path(__file__).resolve().parents[2]
    offenders = []
    for app in ('users', 'api', 'core', 'trophies', 'milestones', 'templates'):
        for path in (root / app).rglob('*'):
            if path.suffix not in ('.py', '.html') or 'test' in path.name:
                continue
            if '/users/subscription-management/' in path.read_text(encoding='utf-8', errors='ignore'):
                offenders.append(str(path.relative_to(root)))
    assert not offenders, f'literal old-path links survive in: {offenders}'


# ---------------------------------------------------------------- the page (Phase 2) ----

def test_the_old_url_is_a_302_that_keeps_the_query_string(client):
    """302 EXACTLY, never 301: the old path is baked into every sent email and stored
    notification, and a 301 on a payment-adjacent URL is cached by the browser forever."""
    response = client.get('/users/subscription-management/?src=email')
    assert response.status_code == 302
    assert response['Location'] == '/support/membership/?src=email'


def test_the_page_lives_under_support_now():
    assert reverse('subscription_management') == '/support/membership/'


def _page(client, user, *subs):
    client.force_login(user)
    with _with_subs(*subs):
        return client.get(reverse('subscription_management'))


def test_a_grace_member_sees_grace_not_no_membership(client):
    """The headline: a cancelled-but-still-paid Stripe member is a MEMBER on this page."""
    user = _stripe_user()
    ProfileFactory(user=user)
    future_ts = int((timezone.now() + timedelta(days=20)).timestamp())
    response = _page(client, user, _fake_sub(status='canceled', current_period_end=future_ts))
    body = response.content.decode()
    assert response.status_code == 200
    assert 'No active membership' not in body
    assert "You&#x27;ve cancelled" in body or "You've cancelled" in body
    assert 'Re-subscribe' in body


def test_an_active_ladder_member_wears_their_level(client):
    user = _stripe_user(tier='patron')
    ProfileFactory(user=user, display_psn_username='PatronPal')
    response = _page(client, user, _fake_sub(
        status='active', current_period_end=4102444800,
        plan={'amount': 1500, 'interval': 'month'},
    ))
    body = response.content.decode()
    assert response.status_code == 200
    from users.constants import SUPPORT_TIERS
    patron_colour = next(t['colour'] for t in SUPPORT_TIERS if t['slug'] == 'patron')
    # Two legitimate hosts: the status card (the whole tinted region inherits from it) and the
    # mark perk's rendered example, which hosts its own --sup-t exactly as the storefront does.
    assert body.count(f'--sup-t: {patron_colour}') == 2, 'unexpected extra --sup-t hosts'
    assert 'supm-status' in body
    assert 'PlatPursuit Patron' in body
    assert '$15 / month' in body
    assert 'pp-supname' in body


def test_a_legacy_member_keeps_their_real_name_but_wears_the_mapped_level(client):
    user = _stripe_user(tier='premium_yearly')
    ProfileFactory(user=user)
    response = _page(client, user, _fake_sub(status='active', current_period_end=4102444800))
    body = response.content.decode()
    from users.constants import SUPPORT_TIERS
    backer_colour = next(t['colour'] for t in SUPPORT_TIERS if t['slug'] == 'backer')
    assert f'--sup-t: {backer_colour}' in body
    assert 'Premium Yearly' in body, 'a legacy tier displays its REAL name'


def test_a_non_member_sees_the_dial_not_a_door(client):
    user = UserFactory()
    ProfileFactory(user=user)
    client.force_login(user)
    response = client.get(reverse('subscription_management'))
    body = response.content.decode()
    assert 'No active membership' in body
    assert 'Nothing on the site is locked' in body


def test_the_paypal_cancel_is_a_dialog_not_a_confirm(client):
    user = UserFactory()
    ProfileFactory(user=user)
    user.subscription_provider = 'paypal'
    user.paypal_subscription_id = 'I-DLGTEST'
    user.premium_tier = 'patron'
    user.save()
    client.force_login(user)
    with patch('users.services.paypal_service.PayPalService.get_cached_subscription_snapshot',
               return_value={'status': 'ACTIVE', 'next_billing_time': None, 'plan_id': None}):
        response = client.get(reverse('subscription_management'))
    body = response.content.decode()
    assert '<dialog id="supm-cancel"' in body
    assert 'confirm(' not in body
    assert reverse('paypal_cancel_subscription') in body


def test_messages_render_without_the_breadcrumb(client):
    """The breadcrumb partial was the site's only messages renderer and this page dropped it --
    the wall-toggle confirmation must still be visible after the redirect."""
    user = UserFactory()
    profile = ProfileFactory(user=user)
    client.force_login(user)
    response = client.post(reverse('subscription_management'),
                           {'wall_visibility': '1', 'on_the_wall': 'yes'}, follow=True)
    assert b'You are on the supporter wall.' in response.content

