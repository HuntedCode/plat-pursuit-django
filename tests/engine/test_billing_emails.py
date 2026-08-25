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
from users.services.marks import worn_level_dict
from users.services.subscription_service import format_charge

SITE = 'https://platpursuit.com'
PORTAL = 'https://billing.stripe.com/p/session/live_YWNjdF8xMjM0/abcdef123456'
INVOICE = 'https://invoice.stripe.com/i/acct_123/test_YWNjdF8x'
EMAILS = Path(settings.BASE_DIR) / 'templates' / 'emails'


def _plain(body):
    stripped = re.sub(r'(?is)<(style|script)[^>]*>.*?</\1>', ' ', body)
    return _html.unescape(strip_tags(stripped))


def _preheader(body):
    """The hidden inbox preview line, pulled out of the base's mso-hide div.

    Slicing the first N characters of the plaintext gets this too, but only until a longer
    headline pushes the sentence past N and breaks a passing test for the wrong reason.
    """
    match = re.search(r'mso-hide: all[^>]*>(.*?)&zwnj;', body, re.S)
    return _html.unescape(match.group(1)).strip() if match else ''


def _content(body):
    """Everything the reader actually sees, with the hidden preheader removed.

    The preheader paraphrases the body, so a copy assertion run against the whole render can be
    satisfied by the preview line alone while the sentence it names is gone from the page.
    Everything before <body> goes with it: the <title> repeats the headline.
    """
    visible = body.split('<body', 1)[-1]
    return re.sub(r'<div style="display: none;.*?</div>', '', visible, count=1, flags=re.S)


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
        # Only v2 emits this. Everything above is satisfied by the child's OWN markup, so without
        # it the whole test stays green after a revert to the legacy base.
        assert 'mso-hide: all' in body, 'no preheader block: this is not riding v2'


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

    # Body, not preheader: the preview line also says "trophies are untouched".
    assert 'trophies' in _content(body) and 'stay exactly as they are' in _content(body)


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

    # The reassurance that keeps a first failure from reading as a cancellation. Checked against
    # the visible body: the preheader paraphrases both of these and would satisfy the pin alone.
    assert 'premium access remains active' in _content(first)
    assert 'will be cancelled unless' in _content(final)

    # The loss list appears ONLY on the final warning.
    assert PREMIUM_PERKS[0]['name'] in final
    assert PREMIUM_PERKS[0]['name'] not in first

    # Distinct preheaders (the preview line, and the first thing in the plaintext part).
    assert 'retry' in _preheader(first)
    assert 'cancelled' in _preheader(final)
    assert _preheader(first) and _preheader(first) != _preheader(final)


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
    'Next billing date: None' to a paying customer.

    The keys are passed as None rather than left out, because that is what the sender does --
    omitting them renders '' and the 'None' pin below cannot fail.
    """
    body = _render('payment_succeeded.html', tier_name='Patron',
                   manage_url=f'{SITE}/users/subscription/',
                   amount=None, next_billing_date=None)

    assert 'None' not in _plain(body)
    assert 'Amount charged' not in body
    assert 'Next billing date' not in body
    assert 'Patron' in body, 'the subscription row is unconditional'


# --- the money itself ---

def test_the_charge_is_formatted_from_the_invoice_minor_unit():
    assert format_charge(499, 'usd') == '$4.99 USD'
    assert format_charge(2500, 'USD') == '$25.00 USD'
    assert format_charge(499) == '$4.99 USD', 'currency defaults to the only one we mint'


def test_the_charge_does_not_divide_a_zero_decimal_currency():
    """Stripe sends JPY and friends in MAJOR units. Dividing by 100 understates the charge on a
    receipt by 100x, which is the worst direction for that error to run."""
    assert format_charge(500, 'jpy') == '500 JPY'
    assert format_charge(50000, 'KRW') == '50000 KRW'


def test_the_charge_never_puts_a_dollar_sign_on_another_currency():
    """"$4.99 EUR" gives the reader two contradictory currencies to choose between."""
    assert format_charge(499, 'eur') == '4.99 EUR'
    assert '$' not in format_charge(499, 'gbp')


def test_the_charge_is_omitted_rather_than_guessed():
    """No invoice in hand (PayPal, admin resend) must leave the receipt row out entirely."""
    assert format_charge(None, 'usd') is None
    assert format_charge('oops', 'usd') is None


def test_the_billing_senders_dropped_the_dead_preference_token():
    """It was minted (signed, per send) for a page that is parked and a template that never
    read it. The preview command minted its own copy for the same five templates."""
    root = Path(settings.BASE_DIR)
    src = (root / 'users' / 'services' / 'subscription_service.py').read_text(encoding='utf-8')

    assert 'preference_url' not in src
    assert 'SITE_URL}/support/' not in src, 'literal URL should be reverse()'

    preview = (root / 'core' / 'management' / 'commands' / 'test_email_system.py').read_text(encoding='utf-8')
    for name in ('payment_failed', 'subscription_cancelled', 'subscription_welcome',
                 'payment_succeeded', 'payment_action_required'):
        start = preview.index("template_name='emails/" + name + ".html'")
        context = preview.rindex('context = {', 0, start)
        assert 'preference_url' not in preview[context:start], f'{name} preview still mints it'


def test_the_perk_previews_are_fed_the_constant():
    """The three perk-list previews rendered an empty loop, so the operator eyeballing a final
    warning saw a dangling colon where the loss list belongs."""
    preview = (Path(settings.BASE_DIR) / 'core' / 'management' / 'commands'
               / 'test_email_system.py').read_text(encoding='utf-8')

    for name in ('payment_failed', 'subscription_cancelled', 'subscription_welcome'):
        start = preview.index("template_name='emails/" + name + ".html'")
        context = preview.rindex('context = {', 0, start)
        assert 'premium_perks' in preview[context:start], f'{name} preview renders an empty list'


# --- the mark preview: the one personal thing in the welcome ---

def _welcome(mark):
    return _render('subscription_welcome.html', tier_name='Patron', premium_perks=PREMIUM_PERKS,
                   profile_url=f'{SITE}/hunters/t/', discord_url='', mark=mark)


def _mark_panel(body):
    """Just the mark panel's cell.

    Scoped deliberately: the ladder colours are ALSO the perk-list accent colours (Patron's
    #6875ee is the "Site-wide marker" perk's own bar), so a colour assertion against the whole
    body is satisfied by the perk list even with the panel rendering in flat black.
    """
    match = re.search(r'background-color: #0F1720.*?</table>', body, re.S)
    return match.group(0) if match else ''


def test_the_welcome_shows_the_mark_it_just_granted():
    """The "Site-wide marker" perk, shown rather than described."""
    body = _content(_welcome(worn_level_dict('patron')))
    panel = _mark_panel(body)

    assert 'Your mark' in panel
    assert '#6875ee' in panel, "the level's own colour has to drive the name"
    assert 'TestHunter' in panel, 'the mark is the NAME wearing a glyph, not a glyph alone'
    assert panel.count('&#9733;') == 2, 'Patron wears two filled stars'


def test_the_mark_panel_draws_stars_as_text_not_svg():
    """Gmail strips <svg> outright and Outlook's Word engine cannot draw it, so the site's real
    glyph would render as NOTHING for most of the people this email is for."""
    body = _welcome(worn_level_dict('patron'))

    assert '<svg' not in body
    assert chr(9733) in _plain(body), 'the stars must survive into the plaintext part too'


def test_the_first_ladder_level_wears_a_hollow_star():
    """Backer is `outline: True` -- the site strokes the SVG instead of filling it, and the
    email needs the same distinction or level 1 and level 2 look identical."""
    backer = _mark_panel(_welcome(worn_level_dict('backer')))
    contributor = _mark_panel(_welcome(worn_level_dict('contributor')))

    assert '&#9734;' in backer and '&#9733;' not in backer
    assert '&#9733;' in contributor and '&#9734;' not in contributor


def test_the_mark_panel_stays_on_a_dark_ground():
    """Not a style preference: every ladder colour fails AA on the white content body (2.15:1 to
    3.91:1) and passes on this panel (4.61:1 to 8.38:1)."""
    assert _mark_panel(_welcome(worn_level_dict('backer'))), 'the mark panel lost its dark ground'


def test_the_welcome_skips_the_panel_when_there_is_no_supporter_mark():
    """A staff or mod subscriber keeps wearing the wrench or the shield (service marks outrank
    the ladder), so showing them stars would promise a mark no page will draw."""
    body = _welcome(None)

    assert 'Your mark' not in body
    assert PREMIUM_PERKS[0]['name'] in body, 'the rest of the email is unaffected'


class _User:
    """Only what the helper reads: the worn-mark denorm and the tier that was bought."""
    def __init__(self, display_mark, premium_tier):
        self.premium_tier = premium_tier
        self.profile = type('P', (), {'display_mark': display_mark})()


def test_the_sender_only_shows_a_mark_the_site_will_actually_draw():
    from users.services.subscription_service import SubscriptionService as S

    assert S._welcome_supporter_mark(_User('patron', 'patron'))['slug'] == 'patron'

    assert S._welcome_supporter_mark(_User('staff', 'patron')) is None, 'staff wear the wrench'
    assert S._welcome_supporter_mark(_User('mod', 'patron')) is None, 'mods wear the shield'
    assert S._welcome_supporter_mark(_User('', None)) is None
    assert S._welcome_supporter_mark(type('U', (), {'premium_tier': 'patron'})()) is None, 'no profile'


def test_a_grandfathered_member_keeps_the_tier_name_they_bought():
    """The welcome page's rule (templates/support/welcome.html): a legacy tier wears the
    price-nearest level's colour and stars but displays its REAL name. Naming the panel from the
    worn slug instead would call a `supporter` subscriber a "Sponsor", which they never bought."""
    from users.services.subscription_service import SubscriptionService as S

    mark = S._welcome_supporter_mark(_User('sponsor', 'supporter'))
    assert mark['display_name'] == 'Supporter' and mark['name'] == 'Sponsor'
    assert mark['is_legacy'] is True

    panel = _mark_panel(_welcome(mark))
    assert 'Supporter' in panel
    assert 'PlatPursuit Sponsor' not in panel, 'a tier they never bought'
    assert 'wearing the Sponsor mark' in panel, 'but the mark they wear is still named'


def test_a_ladder_member_is_named_for_their_level():
    from users.services.subscription_service import SubscriptionService as S

    panel = _mark_panel(_welcome(S._welcome_supporter_mark(_User('patron', 'patron'))))

    assert 'PlatPursuit Patron' in panel
    assert 'founding tier' not in panel
