"""The five billing emails on base v2.

These carry money and account status, and four of the five had no test that rendered them at
all before this file. The pins here are mostly about copy that must survive: the dunning
email's two tones, the "your access remains active" reassurance, the perk list that is the only
content in a cancellation, and the receipt rows that legitimately vanish on some send paths.
"""
import html as _html
import re
from pathlib import Path

from django.conf import settings
from django.template.loader import render_to_string
from django.utils.html import strip_tags

from users.constants import PREMIUM_PERKS

SITE = 'https://platpursuit.com'
PORTAL = 'https://billing.stripe.com/p/session/live_YWNjdF8xMjM0/abcdef123456'
INVOICE = 'https://invoice.stripe.com/i/acct_123/test_YWNjdF8x'
EMAILS = Path(settings.BASE_DIR) / 'templates' / 'emails'


def _plain(body):
    stripped = re.sub(r'(?is)<(style|script)[^>]*>.*?</\1>', ' ', body)
    return _html.unescape(strip_tags(stripped))


def _render(name, **ctx):
    ctx.setdefault('site_url', SITE)
    ctx.setdefault('username', 'TestHunter')
    return render_to_string('emails/' + name, ctx)


# --- the family contract ---

def test_every_billing_email_rides_the_new_base():
    bodies = [
        _render('payment_action_required.html', tier_name='Patron', invoice_url=INVOICE),
        _render('payment_succeeded.html', tier_name='Patron', manage_url=f'{SITE}/users/subscription/'),
        _render('subscription_welcome.html', tier_name='Patron', premium_perks=PREMIUM_PERKS,
                profile_url=f'{SITE}/hunters/t/', discord_url='https://discord.gg/platpursuit'),
        _render('subscription_cancelled.html', tier_name='Patron', premium_perks=PREMIUM_PERKS,
                subscribe_url=f'{SITE}/support/'),
        _render('payment_failed.html', tier_name='Patron', premium_perks=PREMIUM_PERKS,
                portal_url=PORTAL, is_final_warning=False),
    ]
    for body in bodies:
        assert 'role="presentation"' in body
        assert '#667eea' not in body, 'the pre-rebuild purple came back'
        assert chr(8212) not in body
        assert 'Manage your account settings' in body
        # The headline must carry its colour inline or it renders near-black on the dark band.
        assert re.search(r'<h1[^>]*style="[^"]*color: #F0F6FD', body)


# --- the perk trio: one shape, three framings ---

def test_the_perk_trio_all_render_the_constant():
    """Hand-writing this list is how the old emails ended up selling themes and checklists
    years after both were retired. The storefront guard scans the files; this scans the render."""
    welcome = _render('subscription_welcome.html', tier_name='Patron', premium_perks=PREMIUM_PERKS,
                      profile_url=f'{SITE}/hunters/t/', discord_url='')
    cancelled = _render('subscription_cancelled.html', tier_name='Patron',
                        premium_perks=PREMIUM_PERKS, subscribe_url=f'{SITE}/support/')
    final = _render('payment_failed.html', tier_name='Patron', premium_perks=PREMIUM_PERKS,
                    portal_url=PORTAL, is_final_warning=True)

    for body in (welcome, cancelled, final):
        for perk in PREMIUM_PERKS:
            assert perk['name'] in body
            assert perk['colour'] in body, 'each perk drives its own accent bar'

    # Only the welcome describes what each perk DOES; the other two are name-only by design.
    assert PREMIUM_PERKS[0]['member'] in welcome
    assert PREMIUM_PERKS[0]['member'] not in cancelled


def test_the_cancellation_promises_the_data_is_safe():
    """Without this sentence a farewell reads as an implied data-loss notice."""
    body = _render('subscription_cancelled.html', tier_name='Patron',
                   premium_perks=PREMIUM_PERKS, subscribe_url=f'{SITE}/support/')

    assert 'trophies' in body and 'stay exactly as they are' in body


def test_the_membership_welcome_keeps_its_discord_cta():
    ctx = dict(tier_name='Patron', premium_perks=PREMIUM_PERKS, profile_url=f'{SITE}/hunters/t/')
    with_discord = _render('subscription_welcome.html', discord_url='https://discord.gg/platpursuit', **ctx)
    without = _render('subscription_welcome.html', discord_url='', **ctx)

    assert 'href="https://discord.gg/platpursuit"' in with_discord
    assert 'Join the Discord' in with_discord
    assert 'Join the Discord' not in without, 'the block needs its guard'


# --- dunning: the one email whose colour means something ---

def test_the_two_dunning_tones_stay_distinct():
    ctx = dict(tier_name='Patron', premium_perks=PREMIUM_PERKS, portal_url=PORTAL)
    first = _render('payment_failed.html', is_final_warning=False, **ctx)
    final = _render('payment_failed.html', is_final_warning=True, **ctx)

    # Amber vs red, and never both in one render.
    assert '#D9903B' in first and '#D6453D' not in first
    assert '#D6453D' in final and '#D9903B' not in final

    # The reassurance that keeps a first failure from reading as a cancellation.
    assert 'premium access remains active' in first
    assert 'will be cancelled unless' in final

    # The loss list appears ONLY on the final warning.
    assert PREMIUM_PERKS[0]['name'] in final
    assert PREMIUM_PERKS[0]['name'] not in first

    # Distinct preheaders (the preview line, and the first thing in the plaintext part).
    assert 'retry' in _plain(first)[:200]
    assert 'cancelled' in _plain(final)[:200]


# --- the plaintext lifeline on every CTA ---

def test_every_billing_cta_is_reachable_in_plaintext():
    """strip_tags keeps no hrefs. A customer reading in plaintext must still be able to fix a
    failed payment or complete a verification."""
    cases = [
        (_render('payment_action_required.html', tier_name='Patron', invoice_url=INVOICE), INVOICE),
        (_render('payment_failed.html', tier_name='Patron', premium_perks=PREMIUM_PERKS,
                 portal_url=PORTAL, is_final_warning=True), PORTAL),
        (_render('payment_failed.html', tier_name='Patron', premium_perks=PREMIUM_PERKS,
                 portal_url=PORTAL, is_final_warning=False), PORTAL),
    ]
    for body, url in cases:
        assert url in _plain(body), 'the CTA vanishes for a plaintext reader'


# --- the receipt's guarded rows ---

def test_the_receipt_states_the_amount_when_it_has_one():
    body = _render('payment_succeeded.html', tier_name='Patron',
                   manage_url=f'{SITE}/users/subscription/',
                   amount='$4.99 USD', next_billing_date='September 01, 2026')

    assert 'Amount charged' in body and '$4.99 USD' in body
    assert 'Next billing date' in body and 'September 01, 2026' in body


def test_the_receipt_omits_rows_it_cannot_fill():
    """PayPal renewals and admin resends carry no invoice: an unguarded row would print
    'Next billing date: None' to a paying customer."""
    body = _render('payment_succeeded.html', tier_name='Patron',
                   manage_url=f'{SITE}/users/subscription/')

    assert 'None' not in _plain(body)
    assert 'Amount charged' not in body
    assert 'Next billing date' not in body
    assert 'Patron' in body, 'the subscription row is unconditional'


def test_the_billing_senders_dropped_the_dead_preference_token():
    """It was minted (signed, per send) for a page that is parked and a template that never
    read it."""
    src = (Path(settings.BASE_DIR) / 'users' / 'services' / 'subscription_service.py').read_text(encoding='utf-8')

    assert 'preference_url' not in src
    assert 'SITE_URL}/support/' not in src, 'literal URL should be reverse()'
