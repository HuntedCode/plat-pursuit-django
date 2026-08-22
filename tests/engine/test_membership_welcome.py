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
    from users.constants import PREMIUM_PERKS
    body = render_to_string('emails/subscription_welcome.html', {
        'username': 'x', 'tier_name': 'Patron', 'site_url': 's', 'profile_url': 'p',
        'preference_url': 'u', 'premium_perks': PREMIUM_PERKS,
        'discord_url': settings.DISCORD_INVITE_URL,
    })
    assert f'href="{settings.DISCORD_INVITE_URL}"' in body
    assert 'Join the Discord' in body
    # Real perks render, so the perk-loop line (where the em dash lived) is actually exercised.
    assert 'mdash' not in body and chr(8212) not in body


def test_the_discord_invite_setting_exists(settings):
    assert settings.DISCORD_INVITE_URL.startswith('https://discord.gg/')


def test_the_webhook_announcer_actually_announces():
    """The other half of the no-announce pin: with an activation event type the welcome email
    DOES send -- so the inline-path guard cannot rot into 'no email ever'."""
    user = UserFactory()
    ProfileFactory(user=user)
    mail.outbox.clear()
    SubscriptionService.activate_subscription(user, 'patron', 'stripe',
                                              event_type='customer.subscription.created')
    assert any('Welcome' in m.subject for m in mail.outbox),         'the activation event must send the welcome email'


def test_a_grace_resubscriber_is_welcomed_as_the_new_tier(client, settings):
    """The audit's ordering bug: a cancelled Backer buying Cornerstone was congratulated on
    Backer (the active shortcut ran before the session was read). Purchase params win now."""
    settings.DEBUG = False
    user = _buyer(client)
    SubscriptionService.activate_subscription(user, 'backer', 'stripe')
    upgraded = _fake_session(metadata={'tier': 'cornerstone', 'interval': 'monthly'})
    with patch('users.views.stripe.checkout.Session.retrieve', return_value=upgraded):
        body = client.get(URL + '?session_id=cs_up').content.decode()
    assert 'PlatPursuit Cornerstone' in body, 'the NEW tier must be the one celebrated'
    assert 'settling in' in body


def test_a_user_without_a_customer_id_cannot_use_a_foreign_session(client, settings):
    """The guard lost its short-circuit: a legitimate Stripe buyer ALWAYS has a customer id by
    redirect time, so a missing local id plus any session is a foreign session."""
    settings.DEBUG = True   # the dangerous combination the audit named: guard skip + inline activation
    user = UserFactory()
    ProfileFactory(user=user)
    client.force_login(user)   # no stripe_customer_id at all
    with patch('users.views.stripe.checkout.Session.retrieve', return_value=_fake_session()):
        response = client.get(URL + '?session_id=cs_foreign')
    assert response.status_code == 302
    user.refresh_from_db()
    assert not user.premium_tier, 'a foreign session must never activate anything'


def test_the_paypal_read_goes_through_the_snapshot_cache(client):
    """A query-param-driven uncached 30s provider call was the audit find: the welcome page
    reads the snapshot, so two hits cost one fetch."""
    from users.services.paypal_service import PayPalService
    from django.core.cache import cache
    user = _buyer(client)
    sub_id = 'I-CACHEWEL'
    cache.delete(PayPalService.SUB_CACHE_KEY.format(id=sub_id))
    details = {'custom_id': str(user.id), 'plan_id': 'P-X', 'status': 'ACTIVE',
               'billing_info': {}}
    with patch.object(PayPalService, 'get_subscription_details', return_value=details) as fetch:
        client.get(URL + f'?provider=paypal&subscription_id={sub_id}')
        client.get(URL + f'?provider=paypal&subscription_id={sub_id}')
    assert fetch.call_count == 1, 'the second hit must come from the snapshot cache'

