"""The membership welcome page, rendered AT the frozen success URL.

The design center is the webhook race: on arrival the buyer's premium fields are usually not
yet written, so the page celebrates OPTIMISTICALLY from what the processor can prove about the
purchase (session metadata / PayPal details, ownership-guarded) while the webhook does the
writing. These tests pin every state plus the two guards that keep foreign ids from leaking a
tier.
"""
from unittest.mock import patch

import pytest
from django.core import mail
from django.urls import reverse

from users.services.subscription_service import SubscriptionService
from tests.factories import ProfileFactory, UserFactory

pytestmark = pytest.mark.django_db

URL = '/users/subscribe/success/'


def _fake_session(**over):
    defaults = dict(payment_status='paid', customer='cus_wel_1',
                    metadata={'tier': 'patron', 'interval': 'monthly'})
    defaults.update(over)
    return type('S', (), defaults)()


def _buyer(client, customer_id='cus_wel_1'):
    user = UserFactory()
    ProfileFactory(user=user, display_psn_username='NewPatron')
    user.stripe_customer_id = customer_id
    user.save()
    client.force_login(user)
    return user


def test_the_frozen_url_still_resolves():
    assert reverse('subscribe_success') == URL


def test_an_activated_member_gets_the_hero(client):
    user = _buyer(client)
    SubscriptionService.activate_subscription(user, 'patron', 'stripe')
    body = client.get(URL).content.decode()
    assert 'NewPatron' in body
    assert 'PlatPursuit Patron' in body
    assert 'pp-supname' in body
    assert 'settling in' not in body
    assert 'Join the Discord' in body and 'discord.gg' in body


def test_the_stripe_race_renders_the_purchased_tier(client, settings):
    """The headline: webhook not landed, premium untouched, but the session metadata knows the
    tier -- the hero celebrates from it, display-only."""
    settings.DEBUG = False
    user = _buyer(client)
    with patch('users.views.stripe.checkout.Session.retrieve', return_value=_fake_session()):
        body = client.get(URL + '?session_id=cs_x').content.decode()
    assert 'PlatPursuit Patron' in body
    assert 'settling in' in body
    user.refresh_from_db()
    assert not user.premium_tier, 'outside DEBUG the view must never write premium'


def test_debug_inline_activation_never_announces(client, settings):
    """The fundraiser model: in DEBUG the view activates so dev sees the real thing -- with NO
    event_type, so the welcome email cannot fire from the inline path (the webhook stays the
    only announcer)."""
    settings.DEBUG = True
    user = _buyer(client)
    mail.outbox.clear()
    with patch('users.views.stripe.checkout.Session.retrieve', return_value=_fake_session()):
        body = client.get(URL + '?session_id=cs_x').content.decode()
    user.refresh_from_db()
    assert user.premium_tier == 'patron'
    assert 'settling in' not in body, 'DEBUG activation should render the active state'
    assert not any('Welcome' in m.subject for m in mail.outbox), \
        'the inline activation must not send the welcome email'


def test_a_foreign_session_id_never_leaks_a_tier(client, settings):
    settings.DEBUG = False
    _buyer(client, customer_id='cus_mine')
    foreign = _fake_session(customer='cus_theirs')
    with patch('users.views.stripe.checkout.Session.retrieve', return_value=foreign):
        response = client.get(URL + '?session_id=cs_stolen')
    assert response.status_code == 302
    assert response['Location'] == reverse('support_hub')


def test_an_async_payment_still_settles_not_errors(client, settings):
    settings.DEBUG = False
    _buyer(client)
    pending = _fake_session(payment_status='unpaid')
    with patch('users.views.stripe.checkout.Session.retrieve', return_value=pending):
        body = client.get(URL + '?session_id=cs_x').content.decode()
    assert 'settling in' in body and 'PlatPursuit Patron' in body


def test_paypal_verifies_ownership_and_recovers_the_tier(client):
    user = _buyer(client)
    from users.constants import PAYPAL_LADDER_PLANS
    plan_id = PAYPAL_LADDER_PLANS['sandbox']['patron']['monthly']
    details = {'custom_id': str(user.id), 'plan_id': plan_id, 'status': 'ACTIVE'}
    with patch('users.services.paypal_service.PayPalService.get_subscription_details',
               return_value=details):
        body = client.get(URL + '?provider=paypal&subscription_id=I-NEW').content.decode()
    assert 'PlatPursuit Patron' in body
    assert 'settling in' in body


def test_a_foreign_paypal_subscription_goes_blind_not_leaked(client):
    _buyer(client)
    details = {'custom_id': '999999', 'plan_id': 'P-X', 'status': 'ACTIVE'}
    with patch('users.services.paypal_service.PayPalService.get_subscription_details',
               return_value=details):
        body = client.get(URL + '?provider=paypal&subscription_id=I-THEIRS').content.decode()
    assert 'PlatPursuit member' in body, 'ownership mismatch renders the blind hero'
    assert 'PlatPursuit Patron' not in body


def test_a_bare_paypal_return_settles_blind(client):
    _buyer(client)
    body = client.get(URL + '?provider=paypal').content.decode()
    assert 'PlatPursuit member' in body and 'settling in' in body


def test_a_bare_hit_lands_on_the_storefront(client):
    _buyer(client)
    response = client.get(URL)
    assert response.status_code == 302
    assert response['Location'] == reverse('support_hub')


def test_a_stripe_error_lands_on_the_storefront_with_a_message(client):
    import stripe as stripe_mod
    _buyer(client)
    with patch('users.views.stripe.checkout.Session.retrieve',
               side_effect=stripe_mod.error.StripeError('down')):
        response = client.get(URL + '?session_id=cs_x', follow=True)
    assert response.status_code == 200
    assert b'will activate shortly' in response.content


def test_a_legacy_tier_wears_the_mapped_level_with_its_real_name(client):
    user = _buyer(client)
    SubscriptionService.activate_subscription(user, 'premium_yearly', 'stripe')
    body = client.get(URL).content.decode()
    assert 'Premium Yearly' in body
    assert 'wearing the Backer mark' in body


def test_the_preview_harness_is_gated_and_fabricated(client, settings):
    settings.DEBUG = False
    staff = UserFactory(is_staff=True)
    ProfileFactory(user=staff)
    client.force_login(staff)
    body = client.get(URL + '?preview=settling').content.decode()
    assert 'Previewing state:' in body and 'settling in' in body

    plain = UserFactory()
    ProfileFactory(user=plain)
    client.force_login(plain)
    response = client.get(URL + '?preview=settling')
    assert response.status_code == 302, 'a regular user gets their real (bare) state'


def test_the_welcome_email_carries_the_discord_cta(settings):
    from django.template.loader import render_to_string
    body = render_to_string('emails/subscription_welcome.html', {
        'username': 'x', 'tier_name': 'Patron', 'site_url': 's', 'profile_url': 'p',
        'preference_url': 'u', 'premium_perks': [],
        'discord_url': settings.DISCORD_INVITE_URL,
    })
    assert 'Join the Discord' in body and settings.DISCORD_INVITE_URL in body
    assert 'mdash' not in body


def test_the_discord_invite_setting_exists(settings):
    assert settings.DISCORD_INVITE_URL.startswith('https://discord.gg/')
