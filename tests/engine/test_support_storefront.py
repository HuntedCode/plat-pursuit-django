"""`/support/` -- the Support landing, which IS the membership storefront.

Two things make this file worth more than its size.

**The checkout POST had NO coverage at all.** There was no subscription test module; the only tests
that touched the old `/users/subscribe/` did so sideways, with `follow=True`, asserting a string was
absent. Nothing asserted that pressing a tier button reaches Stripe with that tier. That gap is what
made the rebuild dangerous: the form carries no `action`, so it self-POSTs, and moving the page
without moving the handler would have turned every checkout into a redirect-to-GET with the body
dropped -- silently, and with a green suite.

**The page was untestable by construction.** `get_prices_from_stripe` raises `Price.DoesNotExist`
when djstripe has no rows, and the old view answered that by redirecting the whole page to home.
There are no Price fixtures in the test DB, so `/users/subscribe/` ALWAYS redirected under test and
no assertion about its content could ever have run. Degrading the pricing block instead of the page
is what makes everything below possible.
"""
import pathlib
import re
from unittest.mock import patch

import pytest
from django.urls import reverse

from users.constants import PREMIUM_PERKS
from tests.factories import UserFactory

pytestmark = pytest.mark.django_db


class _FakePrice:
    """Stands in for a djstripe Price. Only `stripe_data` is ever read by the view."""

    def __init__(self, cents, interval):
        self.stripe_data = {'unit_amount': cents, 'recurring': {'interval': interval}}


_PRICES = {
    'premium_monthly': _FakePrice(300, 'month'),
    'premium_yearly': _FakePrice(3000, 'year'),
    'supporter': _FakePrice(1000, 'month'),
}


def _priced():
    return patch('users.views.SubscriptionService.get_prices_from_stripe', return_value=_PRICES)


def _member(is_member):
    return patch(
        'users.views.SubscriptionService.has_active_subscription',
        return_value=(is_member, 'stripe' if is_member else None),
    )


def _get(client):
    with _priced():
        return client.get(reverse('support_hub'))


# ----------------------------------------------------------------- the three viewer states ----

def test_a_signed_out_visitor_sees_the_whole_pitch(client):
    """The load-bearing one. The old storefront was `@login_required`, so the site's only "here is
    why we exist" page could not be read by anyone who had not already signed up -- which is exactly
    backwards for the one page whose job is persuading strangers."""
    with _member(False):
        body = _get(client).content.decode()

    assert 'Support Platinum Pursuit' in body
    # The pitch itself, not just a login wall wearing its title.
    assert 'no ads' in body.lower()
    assert 'Raised so far' in body
    # ...and the ask degrades to a way IN rather than a dead end.
    assert 'Sign in to continue' in body
    assert 'name="tier"' not in body, 'anonymous visitors are shown a buy button they cannot use'


def test_a_signed_in_non_member_gets_the_real_buttons(client):
    user = UserFactory()
    client.force_login(user)
    with _member(False):
        body = _get(client).content.decode()

    assert 'Sign in to continue' not in body
    assert 'name="tier"' in body
    assert 'csrfmiddlewaretoken' in body, 'the checkout form would be rejected by CSRF'


def test_a_member_is_not_bounced_off_the_page(client):
    """The old view redirected anyone with an active subscription to `subscription_management`. That
    was defensible when this URL sold one thing; it is not now that the same URL is the hub landing
    (and is about to carry the roadmap and the fundraiser). Redirecting members makes those
    unreachable for precisely the people who paid."""
    user = UserFactory()
    client.force_login(user)
    with _member(True):
        response = _get(client)
    body = response.content.decode()

    assert response.status_code == 200, 'a member cannot reach the Support hub at all'
    assert 'PlatPursuit Supporter' in body
    assert 'name="tier"' not in body, 'a member is being sold a second subscription'


# ------------------------------------------------------------------- the checkout contract ----

@pytest.mark.parametrize('tier', ['premium_monthly', 'premium_yearly', 'supporter'])
def test_each_tier_reaches_stripe_as_itself(client, tier):
    """THE test this codebase never had. Asserts the tier the button carries is the tier the service
    is asked for -- a mix-up here charges the wrong amount, and nothing else would catch it.

    Parametrized over all three deliberately: `supporter` was live and purchasable for months with
    no button anywhere, so it is the one most likely to be wired up wrong now that it has one.
    """
    user = UserFactory()
    client.force_login(user)

    with _priced(), _member(False), \
            patch('users.views.SubscriptionService.create_checkout_session',
                  return_value='https://checkout.stripe.com/x') as checkout:
        response = client.post(reverse('support_hub'), {'tier': tier, 'provider': 'stripe'})

    assert checkout.called, f'{tier} never reached the checkout service'
    assert checkout.call_args.kwargs['tier'] == tier
    # 303 specifically: a POST answered with 302 can be replayed as a POST by some clients.
    assert response.status_code == 303


def test_the_stripe_urls_keep_their_placeholder_and_come_back_here():
    """`{CHECKOUT_SESSION_ID}` is substituted by STRIPE, not by us, so it has to leave our process
    un-interpolated or `subscribe_success` gets no session to verify. And the success path must stay
    on `/users/subscribe/success/`, which is baked into every checkout we have ever created,
    including subscriptions bought months ago."""
    from django.test import RequestFactory
    user = UserFactory()
    request = RequestFactory().post('/support/', {'tier': 'supporter', 'provider': 'stripe'})
    request.user = user

    from users.views import SupportStorefrontView
    with _priced(), _member(False), \
            patch('users.views.SubscriptionService.create_checkout_session',
                  return_value='https://checkout.stripe.com/x') as checkout:
        SupportStorefrontView.as_view()(request)

    kwargs = checkout.call_args.kwargs
    assert kwargs['success_url'].endswith('/users/subscribe/success/?session_id={CHECKOUT_SESSION_ID}')
    assert kwargs['cancel_url'].endswith('/support/'), 'cancelling strands the user on the old URL'


def test_paypal_goes_to_paypal(client):
    user = UserFactory()
    client.force_login(user)

    with _priced(), _member(False), \
            patch('users.services.paypal_service.PayPalService.create_subscription',
                  return_value='https://paypal.com/approve') as paypal:
        response = client.post(reverse('support_hub'),
                               {'tier': 'premium_yearly', 'provider': 'paypal'})

    assert paypal.call_args.kwargs['tier'] == 'premium_yearly'
    assert response.status_code == 302
    assert response['Location'] == 'https://paypal.com/approve'


def test_an_invalid_tier_never_reaches_a_payment_provider(client):
    user = UserFactory()
    client.force_login(user)

    with _priced(), _member(False), \
            patch('users.views.SubscriptionService.create_checkout_session') as checkout:
        response = client.post(reverse('support_hub'), {'tier': 'free_forever_please'})

    assert not checkout.called
    assert response.status_code == 302


def test_an_anonymous_post_is_sent_to_log_in_rather_than_crashing(client):
    """The page is public now, so a POST can arrive with AnonymousUser attached. Every path below
    this guard touches `user.stripe_customer_id`, which AnonymousUser has not got."""
    with _priced():
        response = client.post(reverse('support_hub'), {'tier': 'premium_monthly'})

    assert response.status_code == 302
    assert '/accounts/login/' in response['Location']


def test_a_member_cannot_buy_a_second_subscription(client):
    user = UserFactory()
    client.force_login(user)

    with _priced(), _member(True), \
            patch('users.views.SubscriptionService.create_checkout_session') as checkout:
        response = client.post(reverse('support_hub'), {'tier': 'premium_monthly'})

    assert not checkout.called, 'double-subscribe guard is gone'
    assert response.status_code == 302


# --------------------------------------------------------------------------- degradation ----

def test_missing_pricing_degrades_the_block_not_the_page(client):
    """One missing Stripe Price used to redirect this ENTIRE page to the homepage, taking the
    fundraiser and the whole pitch with it. The pricing block is the only part that should care."""
    from djstripe.models import Price

    with patch('users.views.SubscriptionService.get_prices_from_stripe',
               side_effect=Price.DoesNotExist), _member(False):
        response = client.get(reverse('support_hub'))
    body = response.content.decode()

    assert response.status_code == 200, 'a pricing outage takes down the whole Support hub'
    assert 'Support Platinum Pursuit' in body, 'the pitch went with the prices'
    assert 'name="tier"' not in body, 'buy buttons are rendered with no prices behind them'


# ------------------------------------------------------------------------------- the URLs ----

def test_the_old_storefront_url_still_lands_somewhere_useful(client):
    """Seven templates plus notification and email copy reverse `subscribe`, and real Stripe/PayPal
    records point at it. It redirects rather than 404s -- and TEMPORARILY, because a cached
    permanent redirect on a payment URL cannot be taken back."""
    response = client.get(reverse('subscribe'))

    assert response.status_code == 302, 'a 301 here is cached by the browser forever'
    assert response['Location'] == reverse('support_hub')


def test_the_success_url_did_not_move():
    assert reverse('subscribe_success') == '/users/subscribe/success/'


# ------------------------------------------------------------ the perk audit, as invariants ----

def test_no_perk_promises_something_that_no_longer_exists():
    """The reason this page had to be rebuilt: the live storefront advertised THIRTEEN perks, seven
    of which were retired, redirected, or had quietly become free for everyone. People were paying
    against a list that was mostly fiction.

    Each needle below is a surface that is gone. If one comes back, this test should be updated at
    the same time the perk is re-added -- which is the point of failing here rather than silently
    shipping the claim.
    """
    text = ' '.join(
        f"{p['name']} {p['everyone']} {p['member']} {p.get('note', '')}" for p in PREMIUM_PERKS
    ).lower()

    for gone in ('dashboard', 'theme', 'game list', 'showcase', 'profile customization',
                 'recap', 'platinum grid', 'premium module'):
        assert gone not in text, f'the storefront is advertising {gone!r}, which no longer exists'


def test_every_perk_names_what_a_free_user_gets():
    """The dial invariant, enforced on the data rather than trusted to review.

    Premium is a dial, not a door: members get MORE of something everyone already has. A perk with
    nothing in its `everyone` column is a wall, and the whole page is built on the promise that
    there are none. This is the cheapest possible place to catch one being added.
    """
    for perk in PREMIUM_PERKS:
        assert perk['everyone'].strip(), (
            f"{perk['slug']!r} gives free users nothing -- that is a gate, not a dial"
        )
        assert perk['member'].strip()
        assert perk['everyone'].strip().lower() not in ('none', 'no', 'nothing', '-')


def test_the_page_renders_both_sides_of_every_perk(client):
    """The constant having two columns is worthless if the template only prints one of them."""
    with _member(False):
        body = _get(client).content.decode()

    for perk in PREMIUM_PERKS:
        assert perk['everyone'] in body, f"{perk['slug']}: the free side is not on the page"
        assert perk['member'] in body


def test_neither_page_hand_writes_its_own_perk_list_again():
    """The constant is only a source of truth while both pages actually read it.

    `test_no_perk_promises_something_that_no_longer_exists` guards the DATA, but it would happily
    pass while a template quietly went back to hardcoded rows -- which is exactly how the storefront
    and the management page drifted apart in the first place, and how one of them ended up thanking
    members with a list of things they did not have. So this checks the templates themselves.
    """
    root = pathlib.Path(__file__).resolve().parents[2]
    for name in ('support/support_hub.html', 'users/subscription_management.html'):
        markup = (root / 'templates' / name).read_text(encoding='utf-8')
        assert 'premium_perks' in markup, f'{name} is no longer reading the shared perk list'
        # Strip {% comment %} blocks first. Both templates EXPLAIN which dead perks they stopped
        # advertising, so scanning raw source flags the documentation of the fix as the bug.
        low = re.sub(r'\{%\s*comment\s*%\}.*?\{%\s*endcomment\s*%\}', '', markup,
                     flags=re.DOTALL).lower()
        for gone in ('premium modules', '105+ site themes', 'unlimited game lists',
                     'dashboard customization', 'profile customization'):
            assert gone not in low, f'{name} hand-wrote {gone!r}, which no longer exists'

