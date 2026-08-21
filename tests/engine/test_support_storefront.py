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

from users.constants import PREMIUM_PERKS, SUPPORT_TIERS
from tests.factories import UserFactory

pytestmark = pytest.mark.django_db


def _css_rules(name):
    """A component stylesheet with its comments stripped.

    Comments in these files describe the very things the tests forbid -- "no glow here", "no rule per
    level" -- so a bare substring search over raw CSS matches its own documentation and fails on
    correct code. Three tests tripped on exactly that ('drop-shadow' inside a comment saying there is
    no drop-shadow; 'ally' inside the word 'eventually').
    """
    root = pathlib.Path(__file__).resolve().parents[2]
    css = (root / 'static' / 'css' / 'components' / name).read_text(encoding='utf-8')
    return re.sub(r'/\*.*?\*/', '', css, flags=re.S)



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


def _flat(client):
    """The page with runs of whitespace collapsed.

    Prose in a template wraps across source lines, so a literal needle like 'no investors' misses on
    "no
                    investors". Asserting against raw markup would make every one of these
    tests hostage to where the copy happens to line-break.
    """
    with _member(False):
        return re.sub(r'\s+', ' ', _get(client).content.decode())


# ----------------------------------------------------------------- the three viewer states ----

def test_a_signed_out_visitor_sees_the_whole_pitch(client):
    """The load-bearing one. The old storefront was `@login_required`, so the site's only "here is
    why we exist" page could not be read by anyone who had not already signed up -- which is exactly
    backwards for the one page whose job is persuading strangers."""
    body = _flat(client)

    assert 'Support Platinum Pursuit' in body
    # The statement and the ask, not a login wall wearing the title.
    assert 'Help us build' in body
    assert 'nothing locked away' in body
    assert 'Backer' in body, 'the ladder is missing for a signed-out visitor'


def test_a_signed_in_non_member_sees_the_same_ladder(client):
    """While the ladder is placeholders there is no form to render, so signed-in and
    signed-out see the same thing. The difference only returns when the prices exist."""
    user = UserFactory()
    client.force_login(user)
    with _member(False):
        body = _get(client).content.decode()

    assert 'Cornerstone' in body
    assert 'Manage your membership' not in body, 'a non-member sees the member state'


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
    assert 'You already back this' in body
    assert 'Backer' not in body, 'a member is being sold a second subscription'


# ------------------------------------------------------------------- the checkout contract ----

@pytest.mark.parametrize('tier', ['backer', 'contributor', 'patron',
                                  'sponsor', 'benefactor', 'cornerstone'])
def test_each_ladder_level_reaches_stripe_as_itself(client, tier):
    """Asserts the level the button carries is the level the service is asked for -- a mix-up here
    charges the wrong amount, and nothing else would catch it. All six, because the storefront now
    sells the LADDER only; the legacy tiers have their own rejection test below."""
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


def test_a_legacy_tier_can_no_longer_be_bought_from_the_storefront(client):
    """GRANDFATHERED means renewable, not purchasable. The webhooks renew premium_monthly /
    premium_yearly / supporter forever; the storefront stopped admitting them, or the ladder and
    the legacy price list would both be on sale at once."""
    user = UserFactory()
    client.force_login(user)

    for legacy in ('premium_monthly', 'premium_yearly', 'supporter'):
        with _priced(), _member(False), \
                patch('users.views.SubscriptionService.create_checkout_session') as checkout:
            response = client.post(reverse('support_hub'), {'tier': legacy, 'provider': 'stripe'})

        assert not checkout.called, f'{legacy} is still purchasable from the storefront'
        assert response.status_code == 302


def test_the_cycle_radio_finally_reaches_the_service(client):
    """The radio always rode along in the payload; the server ignored it and priced everything
    monthly -- which would have BILLED a Yearly pick monthly the day the SKUs went live."""
    user = UserFactory()
    client.force_login(user)

    with _priced(), _member(False), \
            patch('users.views.SubscriptionService.create_checkout_session',
                  return_value='https://checkout.stripe.com/x') as checkout:
        client.post(reverse('support_hub'),
                    {'tier': 'patron', 'provider': 'stripe', 'sup-cycle': 'yearly'})

    assert checkout.call_args.kwargs['interval'] == 'yearly'


def test_an_unknown_cycle_is_rejected(client):
    user = UserFactory()
    client.force_login(user)

    with _priced(), _member(False), \
            patch('users.views.SubscriptionService.create_checkout_session') as checkout:
        response = client.post(reverse('support_hub'),
                               {'tier': 'patron', 'sup-cycle': 'weekly'})

    assert not checkout.called
    assert response.status_code == 302


def test_the_stripe_urls_keep_their_placeholder_and_come_back_here():
    """`{CHECKOUT_SESSION_ID}` is substituted by STRIPE, not by us, so it has to leave our process
    un-interpolated or `subscribe_success` gets no session to verify. And the success path must stay
    on `/users/subscribe/success/`, which is baked into every checkout we have ever created,
    including subscriptions bought months ago."""
    from django.test import RequestFactory
    user = UserFactory()
    request = RequestFactory().post('/support/', {'tier': 'cornerstone', 'provider': 'stripe'})
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
                               {'tier': 'benefactor', 'provider': 'paypal', 'sup-cycle': 'yearly'})

    assert paypal.call_args.kwargs['tier'] == 'benefactor'
    assert paypal.call_args.kwargs['interval'] == 'yearly'
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
        response = client.post(reverse('support_hub'), {'tier': 'patron'})

    assert response.status_code == 302
    assert '/accounts/login/' in response['Location']


def test_a_member_cannot_buy_a_second_subscription(client):
    user = UserFactory()
    client.force_login(user)

    with _priced(), _member(True), \
            patch('users.views.SubscriptionService.create_checkout_session') as checkout:
        response = client.post(reverse('support_hub'), {'tier': 'patron'})

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
    # The LADDER's prices come from the constant, so a legacy-band Stripe outage is simply
    # irrelevant to it: the buy buttons stay live. (The live-ladder-without-prices case is
    # `test_placeholders_can_never_reach_live_stripe`.)
    assert 'Not live yet' not in body, 'a legacy pricing outage knocked out the armed ladder'


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


def test_the_perk_list_is_the_decided_lineup():
    """Every perk-table test iterates PREMIUM_PERKS, so a shrunk or emptied list passes them all
    vacuously. Pinning the slugs is what gives those loops a floor."""
    assert [p['slug'] for p in PREMIUM_PERKS] == ['sync', 'discord', 'mark', 'early', 'credit']


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


# --------------------------------------------------------------------- the arc's own rules ----

def test_cold_heartbeat_drops_the_figures_instead_of_printing_zeroes(client):
    """`get_cached_heartbeat` returns None when both hourly buckets are cold, which happens on a
    fresh deploy and every time the cache is flushed.

    "Tracking 0 trophies across 0 games for 0 hunters" on the page asking you to fund the thing is
    considerably worse than saying nothing, so the sentence is omitted whole. Same gate
    `badge_how_it_works` uses, and the rest of the beat has to survive it.
    """
    with patch('core.services.site_heartbeat.get_cached_heartbeat', return_value=None), _member(False):
        body = _get(client).content.decode()

    # `sup-serve` and the EXACT rendered case. Both old needles were dead: `sup-head__figs` was
    # renamed to `sup-serve` and the assertion could never fail against a class that no longer
    # exists in any state, and 'trophies tracked' (lowercase) never matched the rendered
    # 'Trophies tracked'. The audit proved the gate was uncovered: flipping all() to any() rendered
    # 'None platinums' with this test green.
    assert 'sup-serve' not in body, 'the serve band rendered on a cold heartbeat'
    assert 'Trophies tracked' not in body, 'the page is advertising a zero'
    # The rest of the header still stands.
    assert 'Help us build' in body
    assert 'Backer' in body


def test_a_partial_heartbeat_is_treated_as_no_heartbeat(client):
    """All three figures or none. A heartbeat missing one count would otherwise render "tracking
    12,000 trophies across 0 games", which reads as broken rather than as partial."""
    # The fixture supplies the fields the view ACTUALLY reads (hunters/trophies/platinums/hours;
    # an earlier version fed `games_total`, which the serve band dropped) with one of them missing,
    # and the assertion targets the live class -- `sup-head__figs` was a rename victim that could
    # never match anything.
    half = {
        'always': {'trophies_total': {'value': 12000}, 'profiles_total': {'value': 40}},
        'expanded': {'platinums_total': {'value': 900}},   # hours_hunted missing
    }
    with patch('core.services.site_heartbeat.get_cached_heartbeat', return_value=half), _member(False):
        body = _get(client).content.decode()

    assert 'sup-serve' not in body, 'a partial heartbeat rendered the band with a hole in it'


# ------------------------------------------------------------------ involvement / the beta ----

def test_early_access_still_says_something_between_betas(client):
    """`CURRENT_BETA` is None most of the time, so the PERMANENT claim about early access cannot
    live in the live-beta callout -- it lives in the perk table, which always renders.

    Otherwise one of the two things this page sells hardest disappears for whoever visits in a quiet
    week, including somebody who subscribed FOR it.
    """
    body = _flat(client)

    assert 'New things' in body, 'early access vanishes entirely between betas'
    assert 'Before they ship' in body


def test_every_level_of_the_ladder_is_on_the_page(client):
    """Six levels, all of them visible. The model is "pick how visible your support is", which only
    works if the reader can see the range."""
    from users.constants import SUPPORT_TIERS

    body = _flat(client)
    for tier in SUPPORT_TIERS:
        assert tier['name'] in body, f"{tier['slug']} is missing from the ladder"
        # Scoped to the class, because unscoped these cannot fail: every monthly price is a string
        # prefix of its own yearly price ($4/$40 ... $30/$300), so the yearly face satisfied the
        # monthly needle even with the monthly span deleted from the template.
        assert f'sup-amt__m">${tier["monthly"]}<' in body, (
            f"{tier['slug']}'s monthly price is not on the monthly face"
        )
        assert f'sup-amt__y">${tier["yearly"]}<' in body, (
            f"{tier['slug']}'s yearly price is not on the yearly face"
        )


def test_placeholder_buttons_cannot_be_pressed(client):
    """The placeholder state is HISTORY on this branch (the flag flipped False when the 24 SKUs
    were minted, 2026-08-21) but the guard is not dead code: it is what makes the flag safe to
    flip BACK during an incident. Pinned with the flag forced on."""
    with patch('users.views.SUPPORT_TIERS_ARE_PLACEHOLDERS', True):
        body = _flat(client)

    assert 'disabled aria-disabled="true"' in body, 'placeholder buttons are pressable'
    assert 'Not live yet' in body, 'nothing tells the reader why they cannot press'


def test_the_armed_ladder_sells_live_buttons(client):
    """The flip side, the state actually shipping: flag off + ids filled = pressable buttons,
    no unavailable copy."""
    body = _flat(client)

    assert 'disabled aria-disabled="true"' not in body, 'the armed ladder rendered dead buttons'
    assert 'Not live yet' not in body


def test_placeholders_can_never_reach_live_stripe(client, settings):
    """THE GUARD, and the reason it is a runtime check rather than a checklist item.

    A payment page rendering dead buy buttons is worse than one saying it is unavailable. So the
    placeholder flag is honoured in test mode only: if `SUPPORT_TIERS_ARE_PLACEHOLDERS` is still True
    on the day this deploys, live mode falls back to the unavailable state, because the ladder's own
    slugs have no Stripe prices behind them and nothing is offered that cannot be bought.

    Checklists get skipped. This cannot be.
    """
    settings.STRIPE_MODE = 'live'
    body = _flat(client)

    assert 'disabled aria-disabled="true"' not in body, 'DEAD BUY BUTTONS IN LIVE MODE'
    assert 'Not live yet' not in body
    assert 'briefly unavailable' in body, 'live mode is offering something with no price behind it'
    # Only the ask degrades. The statement beside it is unaffected.
    assert 'Help us build' in body
    assert 'Support Platinum Pursuit' in body


def test_every_level_gets_every_perk():
    """The ladder sells RECOGNITION, never capability. If a tier ever grows its own perk list the
    model has quietly become a feature ladder and the page's central promise is false."""
    from users.constants import SUPPORT_TIERS

    for tier in SUPPORT_TIERS:
        assert set(tier) == {'slug', 'name', 'monthly', 'yearly',
                             'stars', 'outline', 'colour'}, (
            f"{tier['slug']} carries something beyond price and how it looks. A level is an AMOUNT "
            f"and a mark; the moment one carries anything else, this is a feature ladder."
        )


def test_yearly_is_ten_months_at_every_level():
    from users.constants import SUPPORT_TIERS

    for tier in SUPPORT_TIERS:
        assert tier['yearly'] == tier['monthly'] * 10, (
            f"{tier['slug']}: yearly is not the promised two months free"
        )


def test_every_level_can_actually_be_revealed(client):
    """The amount grid is CSS-only: picking an amount reveals its level through `:has(:checked)`.

    That makes a new level in `SUPPORT_TIERS` silently half-broken -- its button renders and
    highlights, but the block naming what you become never appears, because the reveal rule lives in
    CSS and nobody remembered to add it. Exactly the dead-hook class this codebase keeps getting bitten
    by (dangling keyframes, `data-*` attributes nothing reads).

    Asserted against the BUILT stylesheet, not the source, so it also catches the rule being written
    and then dropped by lightningcss.
    """
    from users.constants import SUPPORT_TIERS

    root = pathlib.Path(__file__).resolve().parents[2]
    css = (root / 'static' / 'css' / 'output.css').read_text(encoding='utf-8', errors='ignore')

    for tier in SUPPORT_TIERS:
        slug = tier['slug']
        # lightningcss strips the quotes from attribute selectors, so match unquoted.
        assert f'sup-becomes[data-for={slug}]' in css, (
            f'{slug} has no reveal rule: its amount is pickable but names nothing'
        )
        assert f'input[value={slug}]:checked' in css, f'{slug} never registers as selected'


def test_the_grid_opens_on_a_middle_amount(client):
    """Defaulting to the top reads as grabby and defaulting to the bottom anchors low, so the
    preselected amount is the second rung. Also load-bearing mechanically: with no radio checked,
    the CSS reveals no level block at all and the box opens looking broken."""
    from users.constants import SUPPORT_TIERS

    body = _flat(client)
    checked = [t for t in SUPPORT_TIERS if f'value="{t["slug"]}" class="sr-only" checked' in body]

    assert len(checked) == 1, f'{len(checked)} amounts are preselected, expected exactly one'
    assert checked[0]['slug'] == SUPPORT_TIERS[1]['slug']


def test_the_purchase_box_needs_no_javascript(client):
    """Both the cycle switch and the amount grid are real radios driving `:has()` rules, so the box
    works with JS off. The script on the page only adds the `is-active` class the shared `.pp-switch`
    look keys on -- if the markup ever moves to buttons, this stops being true silently."""
    body = _flat(client)

    # `name="tier"` deliberately: it is the field the checkout POST handler already validates on, so
    # the placeholder is shaped like the real form rather than needing renaming to become one.
    assert body.count('type="radio" name="tier"') == 6, 'the amounts are not radios any more'
    assert 'type="radio" name="sup-cycle"' in body, 'the cycle switch is not radio-backed any more'
    # ...and the cycle genuinely flips in CSS. It used to key on a data-cycle attribute only JS
    # set, which made this test's own claim false: with scripts off, Yearly showed monthly prices.
    assert 'data-cycle' not in body, 'the cycle switch depends on JS again'
    root = pathlib.Path(__file__).resolve().parents[2]
    css = (root / 'static' / 'css' / 'output.css').read_text(encoding='utf-8', errors='ignore')
    assert 'input[name=sup-cycle][value=yearly]:checked' in css, (
        'nothing in the built CSS flips the yearly face'
    )


# ------------------------------------------------------------------ the perks modal + preview ----

def test_the_perks_modal_is_reachable_whatever_you_pick(client):
    """The answer is the same whichever amount you choose -- that IS the model -- so the control
    does not move with the choice and there is exactly one of it.

    A native `<dialog>` on purpose: Escape, the focus trap and the backdrop come from the browser
    rather than from a hand-rolled modal we would then have to keep accessible.
    """
    body = _flat(client)

    assert '<dialog id="sup-perks"' in body
    assert 'data-perks-open' in body, 'nothing opens the modal'
    assert 'data-perks-close' in body, 'nothing closes it but Escape'
    # One per level, since it is the first line of every level's checklist -- and the text is
    # identical in all six, which is the point: the answer does not change with the amount.
    assert body.count('sup-gets__modal') == len(SUPPORT_TIERS)
    assert body.count('Every supporter perk, whatever you give') == len(SUPPORT_TIERS)


def test_the_modal_carries_both_sides_of_every_perk(client):
    """It is the only place the dial is stated now, so if it renders one column the page has quietly
    started claiming supporters get things free hunters cannot have."""
    body = _flat(client)
    dialog = body[body.index('<dialog id="sup-perks"'):]

    for perk in PREMIUM_PERKS:
        assert perk['everyone'] in dialog, f"{perk['slug']}: the free side is missing"
        assert perk['member'] in dialog


def test_the_preview_shows_the_viewer_their_own_name(client):
    """Their own name wearing the mark they are about to pick is far more persuasive than a stand-in,
    and it costs nothing -- it is a string already on the request, not a lookup."""
    from tests.factories import ProfileFactory

    profile = ProfileFactory(display_psn_username='TrophyDad')
    client.force_login(profile.user)

    with _member(False):
        body = _get(client).content.decode()

    # Scoped to the preview row. An unscoped `'TrophyDad' in body` cannot fail: the navbar prints the
    # signed-in hunter's name on every page, so the needle matches whatever the preview renders.
    row = body[body.index('sup-prev__name'):]
    row = row[:row.index('</span>', row.index('sup-prev__mark'))]
    assert 'TrophyDad' in row, 'the preview is not showing the viewer their own name'
    assert 'sup-prev__mark' in row, 'the mark is not shown against the name'


def test_an_anonymous_visitor_still_sees_what_the_preview_is_showing(client):
    """A blank name would make the preview meaningless for exactly the people it has to persuade."""
    body = _flat(client)

    assert 'YourName' in body
    assert 'sup-prev' in body


def test_the_prices_are_the_agreed_ladder():
    """Pinned because they have moved twice and the yearly column is derived by hand."""
    from users.constants import SUPPORT_TIERS

    assert [t['monthly'] for t in SUPPORT_TIERS] == [4, 10, 15, 20, 25, 30]
    assert [t['yearly'] for t in SUPPORT_TIERS] == [40, 100, 150, 200, 250, 300]


def test_the_header_carries_the_artwork(client):
    """The reason this page read as flat: it was the only surface on the site with no art on it.

    `visual-identity.md` calls the commissioned badge artwork the moat and says that if the chrome
    ever fights the art, the chrome loses. A Support page that describes what PlatPursuit makes but
    shows none of it is arguing for the thing while hiding it.
    """
    from tests.factories import BadgeSeriesFactory, GroupBadgeFactory, PlatformGroupFactory

    group = PlatformGroupFactory()
    series = BadgeSeriesFactory(name='Helldivers', badge_image='badges/helldivers.png')
    GroupBadgeFactory(series=series, platform_group=group, is_live=True)

    with _member(False):
        body = _get(client).content.decode()

    # Bounded at the purchase box, which always follows the header copy. The old end anchor was
    # `sup-head__figs`, a renamed class that never matched -- so the slice silently became the
    # whole document and 'in the header' was untested.
    head = body[body.index('sup-head__say'):body.index('sup-box')]
    assert 'sup-art__cell' in head, 'the artwork is not in the header'
    assert 'Helldivers badge artwork' in head


def test_the_header_leads_with_the_invitation_not_a_disclaimer(client):
    """An earlier draft opened on "PlatPursuit is free, and always will be" and then listed three more
    things we do not do to you. Four negatives before a single positive reads as a disclaimer rather
    than an ask, and it is why the header felt joyless.

    The free-forever promise still has to be ON the page -- it is the whole model -- but as
    reassurance underneath, not as the headline.
    """
    body = _flat(client)

    head = body[body.index('sup-head__h1'):]
    headline = head[:head.index('</h1>')]
    assert 'free' not in headline.lower(), 'the headline leads on what we do not charge for'
    assert 'Help us build' in headline

    # ...and the promise is still made, lower down.
    assert 'stays free for everyone' in body
    assert 'nothing locked away' in body


def test_every_level_carries_its_own_colour_from_the_constant(client):
    """Colour used to be a block of hardcoded CSS rules keyed on slug, which meant the palette lived
    in two places that could quietly disagree with `SUPPORT_TIERS`. It is inlined from the constant
    now, so this checks the value actually reaches the markup rather than that a rule exists."""
    body = _flat(client)

    for tier in SUPPORT_TIERS:
        assert f"--sup-t: {tier['colour']}" in body, (
            f"{tier['slug']} is not painting its own colour"
        )
    # ...and no two levels look alike, which is the whole reason they are individually coloured.
    assert len({t['colour'] for t in SUPPORT_TIERS}) == len(SUPPORT_TIERS)


def test_the_mark_builds_one_star_at_a_time():
    """Outline, then one filled, then two, three, four, five. ONE shape throughout: introducing a
    second shape at higher levels would risk a paid mark reading as something earned, which the flair
    guardrail forbids. A star that simply repeats cannot."""
    counts = [t['stars'] for t in SUPPORT_TIERS]
    assert counts == [1, 1, 2, 3, 4, 5]
    assert [t['outline'] for t in SUPPORT_TIERS] == [True, False, False, False, False, False], (
        'only the entry level is an outline'
    )


def test_the_marks_are_actually_drawn(client):
    """The counts above are worthless if the template draws one star regardless."""
    body = _flat(client)

    def heading_mark(slug):
        """Just the heading's star span. The preview row draws the mark too, so a slice to the end
        of the level block counts both and reports double."""
        block = body[body.index(f'data-for="{slug}"'):]
        start = block.index('sup-becomes__stars')
        return block[start:block.index('</span>', start)]

    assert heading_mark('cornerstone').count('<svg class="pp-supstar') == 5, 'the top level is not wearing five stars'
    assert heading_mark('sponsor').count('<svg class="pp-supstar') == 3
    assert heading_mark('backer').count('pp-supstar is-outline') == 1
    assert heading_mark('contributor').count('is-outline') == 0, 'the second level is still an outline'


def test_the_modal_still_centres_itself():
    """A REGRESSION GUARD for a bug whose fix looks like a no-op.

    A modal `<dialog>` centres through the UA stylesheet's `margin: auto` against `inset: 0`. Tailwind's
    preflight sets `margin: 0` on every element, which silently removes it, and the dialog pins to the
    top-left corner. Nothing in the markup looks wrong when this happens.

    So `.sup-modal` restores `margin: auto` explicitly, and the reason that line exists is invisible
    to anyone tidying the file later. Asserted against the BUILT stylesheet, since the preflight rule
    it is fighting only exists after the build.
    """
    root = pathlib.Path(__file__).resolve().parents[2]
    css = (root / 'static' / 'css' / 'output.css').read_text(encoding='utf-8', errors='ignore')

    rule = css[css.index('.sup-modal{'):]
    rule = rule[:rule.index('}')]
    assert 'margin:auto' in rule.replace(' ', ''), (
        'the perks dialog will render in the top-left corner instead of centred'
    )


def test_no_star_row_is_sized_for_a_single_star():
    """A REGRESSION GUARD for a bug the markup could not reveal.

    Every one of these holds between one and five stars. `.sup-prev__mark` used to be a single 11px
    pip with a gradient, and when the stars replaced the pip that fixed width stayed behind: five
    SVGs rendered correctly into a box the size of one, so the mark looked like it never escalated.
    The template was right the whole time, which is why counting SVGs in the HTML did not catch it.

    So none of the star containers may carry a fixed width. They size to their contents or they lie.
    """
    root = pathlib.Path(__file__).resolve().parents[2]
    css = (root / 'static' / 'css' / 'output.css').read_text(encoding='utf-8', errors='ignore')

    for cls in ('.sup-prev__mark', '.sup-stars', '.sup-becomes__stars'):
        found = re.findall(re.escape(cls) + r'[,{][^}]*}', css)
        assert found, f'{cls} not found in output.css -- renamed, so this guard checks nothing'
        for rule in found:
            assert 'width:' not in rule.replace('stroke-width', ''), (
                f'{cls} is sized for one star and will crush the rest: {rule[:90]}'
            )


# ------------------------------------------------------------------- the supporter name mark ----

def test_the_supporter_name_treatment_never_gets_louder_with_price():
    """ONE animation, six colours. The level changes the hue and NOTHING else.

    `visual-identity.md`: flair is a separate visual language from earned status, never a better one,
    and neon is earned by state rather than bought. A treatment that grew an extra glow or a longer
    sweep at the top would be buying prominence next to hunters who earned their rank, which is the
    one thing this whole model is built to avoid.

    So there must be exactly one animation and no per-level rules at all -- the colour arrives inline
    from the constant.
    """
    root = pathlib.Path(__file__).resolve().parents[2]
    css = (root / 'static' / 'css' / 'components' / 'supporter.css').read_text(encoding='utf-8')

    assert css.count('animation:') == 1, 'more than one animation on the supporter name'

    # Searched as a SELECTOR, not as a bare substring. A plain `slug in css` also matches prose --
    # "ally" is inside "eventually" -- which made this fail on a comment while passing on a real
    # rule would have been pure luck.
    rules = re.sub(r'/\*.*?\*/', '', css, flags=re.S)
    # `"` and `=` in the prefix class: an ATTRIBUTE selector ([data-tier="cornerstone"]) puts a
    # quote before the slug, which the original class missed -- the audit proved a per-level rule
    # written that way sailed through. And longhand animation-* properties dodge the animation:
    # count above, so they are forbidden outright.
    # `animation-delay` is exempt: it offsets PHASE (the neighbour stagger) and cannot make any
    # level louder. Duration, name, iteration-count and timing-function absolutely can.
    assert not re.search(r'animation-(?!delay)[a-z-]+\s*:', rules), (
        'longhand animation-* dodges the one-animation count; use the shorthand'
    )
    for tier in SUPPORT_TIERS:
        assert not re.search(r'[.\[#="\'-]' + tier['slug'] + r'\b', rules), (
            f"supporter.css has a rule for {tier['slug']} -- the levels must differ by hue only, "
            f"and that hue comes inline from SUPPORT_TIERS"
        )


def test_the_name_is_legible_without_background_clip():
    """`background-clip: text` needs `color: transparent` to show through, so a browser without it
    renders an INVISIBLE username. The flat tier colour is declared first and unguarded; everything
    that can make the name disappear sits inside an @supports."""
    root = pathlib.Path(__file__).resolve().parents[2]
    css = (root / 'static' / 'css' / 'components' / 'supporter.css').read_text(encoding='utf-8')

    base = css[css.index('.pp-supname {'):]
    base = base[:base.index('}')]
    assert 'color: var(--sup-t' in base, 'no unguarded colour, so the name can vanish'
    assert 'transparent' not in base

    guarded = css[css.index('@supports'):]
    assert 'color: transparent' in guarded, 'the transparent fill is not behind a support check'


def test_the_flow_loops_without_a_visible_seam_and_costs_almost_nothing():
    """This replaced a sweep-then-rest sheen. A single band travelling across text is mechanical --
    the eye locks onto the period -- so it is now a train of three bands at irregular widths, which
    reads as light over water instead.

    Two things have to hold for that to be affordable and seamless:

    ONLY `background-position` may animate. Not a filter, not `background-size`, nothing that
    re-rasterises the gradient every frame. This runs continuously and eventually on every supporter
    name in a leaderboard, so the per-frame cost has to stay a glyph repaint.

    AND THE WRAP MUST BE INVISIBLE BY CONSTRUCTION. The gradient's first and last thirds are flat
    tier colour, so the window at 0% and at 100% shows the same thing and the loop has no seam. That
    is what removed the need for a rest beat, and it silently breaks if a band is ever moved into
    either end.
    """
    root = pathlib.Path(__file__).resolve().parents[2]
    motion = (root / 'static' / 'css' / 'components' / 'motion.css').read_text(encoding='utf-8')

    frames = motion[motion.index('@keyframes ppSupFlow'):]
    frames = frames[:frames.index(chr(10) + '}')]
    # EVERY property in the block, not one per line. A line-based parse reads only the first
    # declaration, so `to { background-position: ...; background-size: ...; }` would look clean --
    # which it did, until a mutation test caught it.
    moved = set(re.findall(r'([a-z-]+)\s*:', frames))
    assert moved == {'background-position'}, f'the flow animates more than a position: {moved}'

    css = (root / 'static' / 'css' / 'components' / 'supporter.css').read_text(encoding='utf-8')
    grad = css[css.index('background-image: linear-gradient('):]
    grad = grad[:grad.index(');')]
    # Flat at both ends: the last stop before 52% and the first after must be the base colour.
    assert '--pp-accent)) 30%' in grad, 'the leading third is no longer flat, so the wrap will show'
    assert '--pp-accent)) 80%' in grad and '--pp-accent)) 100%' in grad, (
        'the trailing third is no longer flat, so the wrap will show'
    )


def test_the_legacy_name_treatment_is_not_used_here():
    """`.legendary-title` is legacy: built to animate but never wired (`--shimmer-size` is undefined
    anywhere), in a font the site does not use, in gold -- which on a trophy site reads as
    achievement rather than support."""
    root = pathlib.Path(__file__).resolve().parents[2]
    markup = (root / 'templates' / 'support' / 'support_hub.html').read_text(encoding='utf-8')

    assert 'legendary-title' not in markup
    assert 'pp-supname' in markup


def test_the_star_wears_the_level_colour():
    """The star and the name share a hue on purpose: together they are ONE mark, not two things that
    happen to sit beside each other.

    Two alternatives were built and rejected, and both would look like reasonable improvements to
    someone reading this file cold, so they are named here rather than rediscovered:

      - a pale tint of the level hue -> reads washed out
      - one constant colour for all six -> levels stop being tellable apart

    So the fill must be `--sup-t` itself: not mixed, not constant.
    """
    root = pathlib.Path(__file__).resolve().parents[2]
    css = (root / 'static' / 'css' / 'components' / 'supporter.css').read_text(encoding='utf-8')

    rule = css[css.index('.pp-supstar {'):]
    rule = rule[:rule.index(chr(10) + '}')]
    fill = next(l for l in rule.splitlines() if l.strip().startswith('fill:'))

    assert '--sup-t' in fill, 'the star is a constant again; levels are no longer tellable apart'
    assert 'color-mix' not in fill, 'the star is a tint again, which reads washed out'


def test_the_star_carries_no_glow():
    """Removed by request, and it is the kind of flourish that creeps back. `drop-shadow` on a mark
    that eventually renders on every supporter name in a virtualized board is also a cost nobody
    asked for."""
    rules = _css_rules('supporter.css')
    star = rules[rules.index('.pp-supstar {'):]
    assert 'drop-shadow' not in star and 'filter:' not in star, 'the glow is back'


def test_no_level_colour_is_a_warm_metal():
    """Bronze, silver, gold and platinum are the PSN trophy grades AND the badge medallion metals on
    this site. A level whose hue drifted warm would put a bought mark in the same visual family as an
    earned grade, which is the collision that drove the level NAMES off those words in the first
    place. The palette is cool and synthetic on purpose."""
    for tier in SUPPORT_TIERS:
        hex_value = tier['colour'].lstrip('#')
        r, g, b = (int(hex_value[i:i + 2], 16) for i in (0, 2, 4))
        assert b > r * 0.6, (
            f"{tier['slug']} ({tier['colour']}) has drifted warm enough to read as a metal"
        )


def test_the_mark_is_not_defined_in_the_storefront_stylesheet():
    """The star and the name are site-wide primitives headed for leaderboards, comments and the
    hunters wall. Defining either in `support.css` would make every one of those surfaces depend on
    the storefront page's stylesheet to draw a username."""
    root = pathlib.Path(__file__).resolve().parents[2] / 'static' / 'css' / 'components'
    page = (root / 'support.css').read_text(encoding='utf-8')

    # A DEFINITION is forbidden; a scoped size override is not. `.sup-becomes__stars .pp-supstar`
    # saying "in this container the mark is 13px" is a page concern and belongs here. An unscoped
    # `.pp-supstar { ... }` would be the page owning the primitive, which is the thing to prevent.
    for cls in ('.pp-supstar', '.pp-supname'):
        assert not re.search(r'^' + re.escape(cls) + r'\s*\{', page, re.M), (
            f'{cls} is DEFINED in the page stylesheet; it belongs in components/supporter.css'
        )


def test_the_preview_names_the_level_in_words(client):
    """A star count tells you there IS a hierarchy; it does not tell you what rung this is or what
    the rung is called. The level joins the leaderboard's existing title line after a dot, in words,
    so a reader can see what a supporter is -- and, ideally, wonder what "Ally" means."""
    body = _flat(client)

    for tier in SUPPORT_TIERS:
        block = body[body.index(f'data-for="{tier["slug"]}"'):]
        block = block[:block.index('</div>', block.index('sup-prev__sub'))]
        assert f'PlatPursuit {tier["name"]}' in block, (
            f'{tier["slug"]} does not name itself under the name'
        )
        assert f'{tier["name"]} Supporter' not in block, (
            'the level names are already supporter-words; appending Supporter doubles up'
        )


def test_a_worn_title_sits_before_the_level(client):
    """Leaderboard rows already put the hunter's worn title on this line. The level joins it rather
    than replacing it, because the title is something they EARNED and it goes first."""
    from tests.factories import ProfileFactory
    from trophies.models import Title, UserTitle

    profile = ProfileFactory(display_psn_username='TrophyDad')
    title = Title.objects.create(name='The Completionist')
    UserTitle.objects.create(profile=profile, title=title, is_displayed=True)
    client.force_login(profile.user)

    with _member(False):
        body = re.sub(r'\s+', ' ', _get(client).content.decode())

    sub = body[body.index('sup-prev__sub'):]
    sub = sub[:sub.index('</span>')]
    assert 'The Completionist' in sub
    assert sub.index('The Completionist') < sub.index('PlatPursuit'), 'the earned title comes first'


def test_the_checklist_demonstrates_the_mark_it_describes(client):
    """The line naming what you get wears the treatment it is naming, so the sentence shows the thing
    instead of asking the reader to imagine it. It is also the only place on the page the name
    animation appears in running text rather than on a mock row.

    Also guards the article. Django cannot pick a/an, the level names are not all consonant-initial,
    and "A Ally mark" shipped once already.
    """
    body = _flat(client)

    for tier in SUPPORT_TIERS:
        block = body[body.index(f'data-for="{tier["slug"]}"'):]
        block = block[:block.index('</ul>')]
        assert f'<span class="pp-supname">{tier["name"]}</span> mark' in block, (
            f'{tier["slug"]}: the checklist names the level without wearing its treatment'
        )
        assert f'A {tier["name"]} mark' not in block, 'the article is back and it does not agree'


def test_no_level_name_collides_with_something_earned():
    """THE GUARD THAT SHOULD HAVE EXISTED TWO LADDERS AGO.

    A bought marker must never read as "better hunter" (`visual-identity.md`), which means a supporter
    level may not share a name with anything a hunter EARNS. Two ladders shipped past review before
    this test existed, and each looked obviously fine at the time:

      Bronze / Silver / Gold / Platinum  -> the PSN trophy grades AND the badge medallion metals
      Friend / Ally / Patron / Champion / Guardian / Luminary
                                         -> "Luminary" is the 10th PURSUER RANK (~690 games) and
                                            "Champion" is a JOB in the heart discipline

    Both were caught by eye, months apart, by someone who happened to remember the other ladder. That
    is not a review process, so this checks against every earned vocabulary in the codebase at once.
    """
    from trophies.util_modules.leveling import PURSUER_RANKS
    from trophies.models import Job
    from trophies.constants import TROPHY_TYPE_BRONZE  # noqa: F401  (module presence, see below)

    earned = set()

    # Pursuer ranks: the account-wide ladder, earned by playing.
    earned |= {name.lower() for _, _, name, _ in PURSUER_RANKS}

    # The 24 Jobs. Read from the seed migration rather than the DB so this holds on an empty test
    # database -- the point is the NAME SPACE, which exists whether or not rows do.
    root = pathlib.Path(__file__).resolve().parents[2]
    seed = (root / 'trophies' / 'migrations' / '0247_seed_jobs.py').read_text(encoding='utf-8')
    earned |= {m.lower() for m in re.findall(r"'([A-Z][a-z]+)'", seed)}

    # PSN trophy grades and the badge medallion metals share these four words.
    earned |= {'bronze', 'silver', 'gold', 'platinum'}

    for tier in SUPPORT_TIERS:
        assert tier['name'].lower() not in earned, (
            f"supporter level {tier['name']!r} is also something hunters EARN. Pick from the giving "
            f"register (Friend, Backer, Patron, Sponsor, Benefactor...) rather than the standing one."
        )
        assert tier['slug'] not in earned


def test_earned_titles_are_checked_by_hand_because_they_are_data():
    """The one vocabulary the test above CANNOT cover, recorded so it is not mistaken for covered.

    `Title` rows are seeded data and users earn them, so the set is unbounded and lives in the
    database rather than in code. A future title called "Patron" would collide with a supporter level
    and nothing here would fail.

    The mitigation is the register itself: title names are achievements, and the giving register
    ("Backer", "Benefactor") is not a field anyone names an achievement from. That is why the fix was
    to change register rather than to pick safer nouns from the same one.
    """
    from trophies.models import Title

    assert hasattr(Title, 'name'), 'Title lost its name field; this note needs rewriting'


def test_no_two_levels_look_alike():
    """MEASURED, not eyeballed. Six hexes in a list can look perfectly distinct while two of them are
    indistinguishable at 11px on a star or in a name on a leaderboard row.

    That happened: Benefactor sat at hue 314 and Cornerstone at 338 -- 23 degrees apart, both
    mid-lightness, both plainly pink -- and it was only caught by looking at the real page. The ramp
    is even ~35 degree steps now, with lightness climbing toward the top so the last pair differs on
    two axes rather than one.
    """
    import colorsys

    def hls(hex_value):
        r, g, b = (int(hex_value.lstrip('#')[i:i + 2], 16) / 255 for i in (0, 2, 4))
        h, l, _ = colorsys.rgb_to_hls(r, g, b)
        return h * 360, l

    read = [(t['name'], *hls(t['colour'])) for t in SUPPORT_TIERS]

    for (a_name, a_h, a_l), (b_name, b_h, b_l) in zip(read, read[1:]):
        hue_gap = abs(b_h - a_h)
        light_gap = abs(b_l - a_l)
        assert hue_gap >= 30 or light_gap >= 0.12, (
            f'{a_name} and {b_name} are {hue_gap:.0f} degrees and {light_gap * 100:.0f}% lightness '
            f'apart -- not tellable apart on an 11px star'
        )

    # ...and no PAIR anywhere in the ladder collides, not just neighbours.
    for i, (a_name, a_h, a_l) in enumerate(read):
        for b_name, b_h, b_l in read[i + 1:]:
            assert abs(b_h - a_h) >= 30 or abs(b_l - a_l) >= 0.12, (
                f'{a_name} and {b_name} read as the same colour'
            )


# ------------------------------------------------------------------ how the site is paid for ----

def _band(body):
    """Just the support band.

    Found by its `data-sup-paid` hook rather than by its copy: the band had a heading once and does
    not now, and a slice anchored on prose breaks every time the words change while telling you
    nothing about whether the band still works.

    Bounded at its own `</section>`, because slicing to the end of the document also swallows the
    perks modal -- whose comparison column is headed "Supporters" -- so an unbounded slice reports
    the band as containing a cell it does not have.
    """
    start = body.index('data-sup-paid')
    return body[start:body.index('</section>', start)]

@pytest.fixture(autouse=True)
def _fresh_support_cache():
    """Every test in this module starts with a cold support cache.

    The view caches under `support:stats` for 300s and the run shares one LocMemCache, so without
    this the file was ORDER-DEPENDENT: a test that rendered without clearing consumed whatever the
    previous test cached (a 205-name wall, mocked prices), which passed in file order and broke
    under `pytest --ff` or random ordering. The explicit `_clear_support_cache()` calls in
    individual tests are retained -- they document which tests depend on freshness mid-test.
    """
    from django.core.cache import cache
    cache.delete('support:stats')
    yield
    cache.delete('support:stats')


def _clear_support_cache():
    from django.core.cache import cache
    cache.delete('support:stats')


def test_the_band_counts_legacy_supporters_too(client):
    """THE POINT OF THE BAND, and the thing that would quietly make it useless.

    Every real supporter today holds `premium_monthly`, `premium_yearly` or `supporter`. Nobody holds
    a ladder slug and nobody will until the twelve SKUs exist and people move across. A band that
    counted only the new ladder would read zero on a live site while looking perfectly correct in
    review.
    """
    UserFactory(premium_tier='premium_monthly')
    UserFactory(premium_tier='premium_yearly')
    UserFactory(premium_tier='supporter')
    UserFactory()  # not a supporter; must not be counted
    _clear_support_cache()

    body = _flat(client)

    band = _band(body)
    # Scoped to the Supporters cell: an unscoped countup="3" also matches the Months-running cell
    # in any month where the site is three months old, which made this assertion date-dependent.
    sup_cell = band[band.index('Supporters'):]
    sup_cell = sup_cell[:sup_cell.index('</div>', sup_cell.index('data-countup'))]
    assert 'data-countup="3"' in sup_cell, 'legacy supporters are not being counted'


def test_a_yearly_pledge_counts_as_a_twelfth_of_itself(client):
    """One figure has to mean one thing. Counting a yearly subscription whole would inflate the
    monthly number by a factor of twelve, which on a TRANSPARENCY row is the worst possible place to
    be wrong."""
    yearly = _FakePrice(12000, 'year')     # $120/yr -> $10/mo
    monthly = _FakePrice(400, 'month')     # $4/mo

    UserFactory(premium_tier='premium_yearly')
    UserFactory(premium_tier='premium_monthly')
    _clear_support_cache()

    # Deliberately NOT `_get`: that helper applies its own `_priced()` inside, which would override
    # the prices this test exists to set up -- silently, and the assertion would then be measuring
    # the helper's fixture rather than anything this test controls.
    with patch('users.views.SubscriptionService.get_prices_from_stripe',
               return_value={'premium_yearly': yearly, 'premium_monthly': monthly}), _member(False):
        body = re.sub(r'\s+', ' ', client.get(reverse('support_hub')).content.decode())

    band = _band(body)
    assert 'data-countup="14"' in band, 'the yearly pledge was not divided by twelve'


def test_unavailable_prices_drop_the_money_cell_rather_than_showing_zero(client):
    """`None` means "we cannot say"; zero would be a claim that nobody is paying. On the band whose
    whole job is transparency, publishing a wrong number is worse than publishing one fewer."""
    UserFactory(premium_tier='premium_monthly')
    _clear_support_cache()

    from djstripe.models import Price
    # Same reason as above: `_get` would re-patch prices back into existence.
    with patch('users.views.SubscriptionService.get_prices_from_stripe',
               side_effect=Price.DoesNotExist), _member(False):
        body = re.sub(r'\s+', ' ', client.get(reverse('support_hub')).content.decode())

    band = _band(body)
    assert 'Supporters' in band, 'the count went with the prices'
    assert 'Monthly support' not in band, 'a money figure is being shown with no prices behind it'


def test_the_band_hides_its_supporter_cells_when_there_are_none(client):
    """The dev case, and the launch-morning case. "$0 a month from 0 supporters" on the page asking
    you to fund the thing is worse than a shorter band -- but months running and ads served are true
    regardless, so the band itself stays."""
    _clear_support_cache()
    body = _flat(client)

    band = _band(body)
    assert 'Supporters' not in band
    assert 'Monthly support' not in band
    assert 'Months running' in band, 'the whole band vanished, not just the empty cells'
    assert 'Ads served' in band


def test_months_running_is_computed_not_typed(client):
    """A hardcoded number goes stale silently and nobody notices for a year."""
    from django.utils import timezone

    _clear_support_cache()
    body = _flat(client)
    now = timezone.now()
    expected = (now.year - 2026) * 12 + (now.month - 1)

    band = _band(body)
    assert f'data-countup="{expected}"' in band, f'months running is not {expected}'


def test_a_currency_symbol_is_never_left_beside_a_counter(client):
    """A BUG THAT ONLY EXISTS AFTER THE FIRST ANIMATION FRAME.

    `countUp` writes `el.textContent`, which replaces the element's ENTIRE contents. So a "$" typed
    into the template next to the number renders correctly on load and then vanishes the moment the
    count starts -- which reads as the currency symbol never having been implemented, and is
    invisible in the markup, in a screenshot taken early, and under reduced motion.

    The symbol has to travel through the formatter instead, via `data-countup-prefix`.
    """
    UserFactory(premium_tier='premium_monthly')
    _clear_support_cache()
    body = _flat(client)

    cells = re.findall(r'<div class="scard__value[^>]*data-countup="[^"]*"[^>]*>[^<]*</div>',
                       _band(body))
    assert cells, 'no counter cells found in the band -- the pattern drifted, guard checks nothing'
    for cell in cells:
        if '$' in cell:
            assert 'data-countup-prefix' in cell, (
                'a currency symbol sits beside a counter that will overwrite it: ' + cell[:120]
            )


def test_countup_carries_a_prefix_through_the_formatter():
    """Asserted on the helper itself, because the template fix above is worthless if the helper
    ignores the attribute. Both halves have to exist for the symbol to survive a frame."""
    root = pathlib.Path(__file__).resolve().parents[2]
    js = (root / 'static' / 'js' / 'utils.js').read_text(encoding='utf-8')

    fn = js[js.index('function countUp('):]
    fn = fn[:fn.index(chr(10) + '}')]
    assert 'countupPrefix' in fn, 'countUp ignores a prefix, so the symbol is dropped every frame'
    fmt = next(l for l in fn.splitlines() if 'const fmt' in l)
    assert 'pre' in fmt, 'the prefix is read but not used by the formatter'


def test_no_level_sells_a_link(client):
    """A perk that is easy to re-add and expensive to withdraw once names are live.

    Checked in the RENDERED copy as well as the data, because the constant losing `linked` does not
    stop a template from promising one.
    """
    body = _flat(client)

    for tier in SUPPORT_TIERS:
        block = body[body.index(f'data-for="{tier["slug"]}"'):]
        block = block[:block.index('</ul>')]
        assert 'link on the site' not in block.lower(), f'{tier["slug"]} is selling a link'
        assert 'link beside' not in block.lower()


# ---------------------------------------------------------------------------- the supporter wall ----

def _flat_headings(body):
    """Just the h1/h2 text, so a heading assertion cannot be satisfied by prose or a class name."""
    return ' '.join(re.findall(r'<h[12][^>]*>(.*?)</h[12]>', body))


def _wall(body):
    """Just the wall section, found by hook and bounded at its own section."""
    start = body.index('data-sup-wall')
    return body[start:body.index('</section>', start)]


def test_the_wall_only_ever_lists_people_who_consented(client):
    """THE LOAD-BEARING TEST ON THIS WHOLE SECTION.

    A PSN name is already public everywhere on this site. The fact that somebody PAYS is not, and
    publishing it is new information about a person. So `show_on_supporter_wall` is not a preference
    that shapes a list -- it is the gate, and a bug here exposes something a real person did not
    agree to make public.
    """
    from tests.factories import ProfileFactory

    ProfileFactory(display_psn_username='SaysYes', show_on_supporter_wall=True,
                   user__premium_tier='patron')
    ProfileFactory(display_psn_username='SaysNo', show_on_supporter_wall=False,
                   user__premium_tier='patron')
    _clear_support_cache()

    wall = _wall(_flat(client))
    assert 'SaysYes' in wall
    assert 'SaysNo' not in wall, 'the wall published somebody who opted out'


def test_the_wall_includes_the_legacy_tiers(client):
    """Nobody holds a ladder slug yet, so a wall that listed only ladder levels would be empty on a
    live site. And excluding somebody by a rule that did not exist when they subscribed would be
    arbitrary."""
    from tests.factories import ProfileFactory

    ProfileFactory(display_psn_username='OldTimer', user__premium_tier='premium_yearly')
    _clear_support_cache()

    assert 'OldTimer' in _wall(_flat(client))


def test_a_legacy_supporter_wears_their_mapped_level(client):
    """Grandfathered presentation (2026-08-21): billing stays on the legacy tier, but the credit
    card wears the price-nearest ladder level -- colour, stars, level name -- via
    LEGACY_TIER_LEVEL_MAP. The 'Supporter' fallback is reserved for unmapped strays."""
    from tests.factories import ProfileFactory
    from users.constants import LEGACY_TIER_LEVEL_MAP, SUPPORT_TIERS

    ProfileFactory(display_psn_username='OldTimer', user__premium_tier='premium_yearly')
    _clear_support_cache()

    wall = _wall(_flat(client))
    worn = next(t for t in SUPPORT_TIERS
                if t['slug'] == LEGACY_TIER_LEVEL_MAP['premium_yearly'])
    assert f"PlatPursuit {worn['name']}" in wall, 'the legacy card fell back to plain Supporter'
    assert 'is-legacy' not in wall


def test_every_legacy_tier_maps_to_a_real_level():
    """The map is presentation-load-bearing: a typo here silently demotes every grandfathered
    subscriber to the unstyled fallback."""
    from users.constants import LADDER_SLUGS, LEGACY_TIER_LEVEL_MAP
    from users.constants import PREMIUM_TIER_CHOICES

    legacy = {slug for slug, _ in PREMIUM_TIER_CHOICES} - set(LADDER_SLUGS)
    assert set(LEGACY_TIER_LEVEL_MAP) == legacy, 'a legacy tier is missing from the map'
    for target in LEGACY_TIER_LEVEL_MAP.values():
        assert target in LADDER_SLUGS


def test_legacy_supporters_rank_with_their_worn_level(client):
    """The credits run highest level first; a mapped legacy supporter sorts WITH the level they
    wear, not in a separate after-the-ladder block."""
    from tests.factories import ProfileFactory
    from users.constants import LEGACY_TIER_LEVEL_MAP

    assert LEGACY_TIER_LEVEL_MAP['supporter'] == 'sponsor', 'test premise moved with the map'
    ProfileFactory(display_psn_username='LegacyPlus', user__premium_tier='supporter')
    ProfileFactory(display_psn_username='NewBacker', user__premium_tier='backer')
    ProfileFactory(display_psn_username='NewPatron', user__premium_tier='patron')
    _clear_support_cache()

    wall = _wall(_flat(client))
    assert wall.index('LegacyPlus') < wall.index('NewPatron') < wall.index('NewBacker'),         'the worn Sponsor level did not place the legacy supporter above patron'


def test_a_non_supporter_is_never_on_the_wall(client):
    """`show_on_supporter_wall` defaults True on EVERY profile, supporter or not, because it is
    inert for anyone without a tier. If the query ever stopped filtering on the tier, that default
    would put the entire user base on the wall."""
    from tests.factories import ProfileFactory

    ProfileFactory(display_psn_username='JustAHunter')
    ProfileFactory(display_psn_username='ARealPatron', user__premium_tier='patron')
    _clear_support_cache()

    wall = _wall(_flat(client))
    assert 'ARealPatron' in wall
    assert 'JustAHunter' not in wall, 'a non-supporter is on the supporter wall'


def test_the_wall_is_capped(client):
    """It grows without bound on a public page. The cap is applied in the DATABASE, so a wall of ten
    thousand supporters is still one bounded query rather than ten thousand rows sorted in Python."""
    from users.views import SupportStorefrontView
    from tests.factories import ProfileFactory

    for i in range(SupportStorefrontView.WALL_CAP + 5):
        ProfileFactory(display_psn_username=f'Backer{i:04d}', user__premium_tier='patron')
    _clear_support_cache()

    wall = _wall(_flat(client))
    assert wall.count('sup-prev--credit') == SupportStorefrontView.WALL_CAP


def test_the_wall_is_omitted_rather_than_rendered_empty(client):
    """A heading promising names over an empty frame is worse than no section."""
    _clear_support_cache()
    body = _flat(client)

    assert 'data-sup-wall' not in body
    assert 'Credits' not in _flat_headings(body)


def test_the_opt_out_is_reachable_and_works(client):
    """Consent that cannot be withdrawn is not consent. The toggle lives on subscription management,
    which is the page somebody is already on when they think about it."""
    from tests.factories import ProfileFactory

    profile = ProfileFactory(display_psn_username='Rethinker', user__premium_tier='patron')
    client.force_login(profile.user)

    page = client.get(reverse('subscription_management')).content.decode()
    assert 'List me on the supporter wall' in page, 'no way to get off the wall'

    client.post(reverse('subscription_management'), {'wall_visibility': '1'})
    profile.refresh_from_db()
    assert profile.show_on_supporter_wall is False

    client.post(reverse('subscription_management'), {'wall_visibility': '1', 'on_the_wall': 'yes'})
    profile.refresh_from_db()
    assert profile.show_on_supporter_wall is True


def test_changing_the_toggle_clears_the_cached_wall(client):
    """The wall is cached for five minutes. Without this, somebody who takes themselves off still
    sees their name on the page afterwards -- which reads as the opt-out not working, on exactly the
    control where that matters most."""
    from django.core.cache import cache
    from tests.factories import ProfileFactory

    profile = ProfileFactory(display_psn_username='Rethinker', user__premium_tier='patron')
    _clear_support_cache()
    assert 'Rethinker' in _wall(_flat(client))          # warms the cache

    client.force_login(profile.user)
    client.post(reverse('subscription_management'), {'wall_visibility': '1'})

    assert cache.get('support:stats') is None, 'the stale wall would still show them'


def test_the_wall_shows_faces_and_names_together(client):
    """The wall's job is "look how many people care about this", so it needs faces for the crowd and
    names for the credit. An avatar-only wall thanks nobody by name; a name-only wall is a credits
    roll rather than a room full of people."""
    from tests.factories import ProfileFactory

    ProfileFactory(display_psn_username='FacedHunter', avatar_url='https://example.test/a.png',
                   user__premium_tier='patron')
    _clear_support_cache()

    wall = _wall(_flat(client))
    assert 'https://example.test/a.png' in wall, 'the credits are not showing faces'
    assert 'FacedHunter' in wall
    assert 'PlatPursuit Patron' in wall, 'the card does not name its own level'


def test_the_wall_does_not_animate_two_hundred_names_at_once(client):
    """`.pp-supname` is a CONTINUOUS animation. It belongs where a supporter appears individually --
    a leaderboard row, a comment -- not two hundred at a time on one screen, which is the
    wall-of-pulsing-names this whole treatment has been avoiding since it was a sheen.

    The tiles still arrive on scroll; that is a one-shot entrance, not a loop.
    """
    from tests.factories import ProfileFactory

    ProfileFactory(display_psn_username='Quiet', user__premium_tier='patron')
    _clear_support_cache()

    wall = _wall(_flat(client))
    assert 'pp-supname' not in wall, 'every name on the wall is running a continuous animation'


def test_a_supporter_with_no_avatar_still_gets_a_tile(client):
    """Plenty of profiles have no avatar. The shared partial falls back to a glyph, and a tile that
    collapsed without one would leave a hole in the grid for a person who is on the wall."""
    from tests.factories import ProfileFactory

    ProfileFactory(display_psn_username='NoPicture', avatar_url=None, user__premium_tier='sponsor')
    _clear_support_cache()

    wall = _wall(_flat(client))
    assert 'NoPicture' in wall
    assert 'sup-prev__av' in wall, 'the avatar slot vanished rather than falling back'


def test_nobody_is_credited_twice(client):
    """A duplicate on a credit roll is the kind of thing the person themselves notices and nobody
    else does."""
    from tests.factories import ProfileFactory

    for slug in ('patron', 'sponsor', 'benefactor', 'cornerstone', 'premium_monthly'):
        ProfileFactory(display_psn_username=f'One{slug[:6].title()}', user__premium_tier=slug)
    _clear_support_cache()

    wall = _wall(_flat(client))
    assert wall.count('sup-prev--credit') == 5, 'somebody is listed more than once'


def test_every_supporting_level_is_credited(client):
    """Backer and Contributor were held off the credits once, so that being listed was what the
    middle of the ladder bought. That went, and the reasoning matters more than the rule:

    a credits section listing only the higher levels is thin until there ARE higher levels, and the
    obvious fix -- hide the bottom rungs once enough people sit above them -- takes somebody's credit
    away after they had it. Removing recognition from a person who already had it is worse than
    never giving it.
    """
    from tests.factories import ProfileFactory

    for tier in SUPPORT_TIERS:
        ProfileFactory(display_psn_username=f'A{tier["slug"].title()}', user__premium_tier=tier['slug'])
    _clear_support_cache()

    wall = _wall(_flat(client))
    for tier in SUPPORT_TIERS:
        assert f'A{tier["slug"].title()}' in wall, f'{tier["slug"]} is not credited'


def test_the_credits_run_highest_level_first(client):
    """Flat, but not unordered. A roll that put Backer above Cornerstone would quietly teach the
    ladder backwards."""
    from tests.factories import ProfileFactory

    ProfileFactory(display_psn_username='TopDog', user__premium_tier='cornerstone')
    ProfileFactory(display_psn_username='MidOne', user__premium_tier='sponsor')
    ProfileFactory(display_psn_username='LowOne', user__premium_tier='backer')
    _clear_support_cache()

    wall = _wall(_flat(client))
    order = [wall.index('TopDog'), wall.index('MidOne'), wall.index('LowOne')]
    assert order == sorted(order), 'the credits are not in descending level order'


def test_the_credits_and_the_preview_are_the_same_object(client):
    """The purchase box promises "this is how you will appear"; the credits are that promise kept.
    They share `.sup-prev` classes OUTRIGHT rather than imitating each other, so a restyle of one is
    a restyle of both and the two can never drift apart. A credit card growing its own class family
    again is the first step of that drift."""
    from tests.factories import ProfileFactory

    ProfileFactory(display_psn_username='SameShape', user__premium_tier='patron')
    _clear_support_cache()

    wall = _wall(_flat(client))
    assert 'sup-prev sup-prev--credit' in wall, 'the credits stopped sharing the preview row'
    assert 'sup-prev__sub' in wall, 'the level line is not the preview sub-line'
    # `(?!s)` because the section WRAPPER is legitimately `.sup-credits` (heading, grid) -- the
    # thing being forbidden is the per-card family (`sup-credit__av`, `sup-credit `), not the plural.
    assert not re.search(r'sup-credit(?!s)', wall), 'a parallel credit-card class family is back'


def test_the_cached_support_payload_is_json_serializable(client):
    """THE PROD-500 GUARD, and the reason it asserts on the RAW cache payload.

    Production's Redis cache serializes with JSONSerializer; the test cache is LocMem, which pickles
    anything. So a payload that JSON cannot encode -- the wall once carried
    `star_range=range(...)` -- passes every rendering test here and then 500s every request in
    production from the moment the wall has anyone on it, because the failing `cache.set` runs on
    the request path and never warms.

    Serializing the raw payload with DjangoJSONEncoder is the closest this suite can get to the
    production serializer without Redis.
    """
    import json
    from django.core.cache import cache
    from django.core.serializers.json import DjangoJSONEncoder
    from tests.factories import ProfileFactory

    ProfileFactory(display_psn_username='CachedOne', user__premium_tier='patron')
    ProfileFactory(display_psn_username='LegacyOne', user__premium_tier='premium_monthly')
    _clear_support_cache()
    _flat(client)                                    # warms the cache through the real view

    raw = cache.get('support:stats')
    assert raw is not None, 'the view stopped caching, so this guard is checking nothing'
    json.dumps(raw, cls=DjangoJSONEncoder)           # raises on any non-primitive


def test_the_wall_survives_a_cache_round_trip(client):
    """The payload is primitives and the rich tier dicts are rebuilt on every read -- so the SECOND
    request (cache hit) must render identically to the first. A hydration bug shows up only on the
    hit path, which no other test takes deliberately."""
    from tests.factories import ProfileFactory

    ProfileFactory(display_psn_username='RoundTrip', user__premium_tier='sponsor')
    _clear_support_cache()

    first = _wall(_flat(client))
    second = _wall(_flat(client))                    # served from cache

    assert 'RoundTrip' in second
    assert 'PlatPursuit Sponsor' in second, 'the tier did not survive hydration on the cache hit'
    assert second.count('<svg class="pp-supstar') == first.count('<svg class="pp-supstar')


def test_the_cap_cannot_cut_a_higher_level_for_a_lower_one(client):
    """The cap used to slice the first 200 ALPHABETICALLY before the rank sort, so past 200
    supporters a Cornerstone named 'zed' was cut while a Backer named 'aaa' stayed. The ordering is
    rank-first in the database now, so the cap eats from the bottom of the ladder only."""
    from users.views import SupportStorefrontView
    from tests.factories import ProfileFactory

    cap = SupportStorefrontView.WALL_CAP
    for i in range(cap):
        ProfileFactory(display_psn_username=f'Aaa{i:04d}', user__premium_tier='backer')
    # Alphabetically last, highest level. The old code cut them; rank-first keeps them.
    ProfileFactory(display_psn_username='ZedCornerstone', user__premium_tier='cornerstone')
    _clear_support_cache()

    wall = _wall(_flat(client))
    assert 'ZedCornerstone' in wall, 'the cap cut the top of the ladder to keep the bottom'


def test_an_unknown_provider_is_rejected_not_defaulted(client):
    """`provider=venmo` used to FALL THROUGH to Stripe, silently charging through a processor the
    user did not pick. On a payment form that is the wrong kind of forgiving."""
    user = UserFactory()
    client.force_login(user)

    with _priced(), _member(False),             patch('users.views.SubscriptionService.create_checkout_session') as checkout:
        response = client.post(reverse('support_hub'),
                               {'tier': 'premium_monthly', 'provider': 'venmo'})

    assert not checkout.called, 'an unknown provider reached Stripe anyway'
    assert response.status_code == 302


def test_the_checkout_is_a_real_form(client):
    """THE AUDIT FINDING ALL THREE AGENTS CONVERGED ON.

    The tier radios and both submit buttons floated in plain divs -- a submit button outside a form
    is inert, so at ladder go-live the enabled buttons would have silently done nothing, and the
    missing CSRF token would have 403'd every submission once a form was added. CI stayed green
    through all of it because every checkout test POSTs via the test client, which talks straight to
    the view and never exercises the markup. This test is the tie between the two: the page must
    contain a real POST form, with the token, ENCLOSING the tier radios and both provider buttons.
    """
    body = _flat(client)

    assert '<form method="post">' in body, 'the checkout has no form; the buy buttons are inert'
    form = body[body.index('<form method="post">'):]
    form = form[:form.index('</form>')]
    assert 'csrfmiddlewaretoken' in form, 'no CSRF token; every submission would 403'
    assert form.count('type="radio" name="tier"') == 6, 'the tier radios are outside the form'
    assert form.count('name="provider"') == 2, 'a provider button is outside the form'


def test_the_cta_wears_the_selected_levels_colour(client):
    """'Picking a number is visibly picking a level' stopped one element short of the money button:
    a stylesheet block claimed to put the level's hue on the box, but --sup-t lived inline on
    SIBLING elements the button does not inherit from, so the CTA sat on the fallback forever.

    The six rules are emitted by the template from SUPPORT_TIERS -- one source -- and this checks
    each is actually on the page with its own colour.
    """
    body = _flat(client)

    for tier in SUPPORT_TIERS:
        rule = f'.sup-box:has(input[name="tier"][value="{tier["slug"]}"]:checked)'
        assert rule in body, f'{tier["slug"]} never colours the box'
        i = body.index(rule)
        assert tier['colour'] in body[i:i + len(rule) + 60], (
            f'{tier["slug"]} colours the box with the wrong hue'
        )


# --------------------------------------------------------------- POST edges the audit found bare ----

def test_a_stripe_error_lands_back_on_the_page_with_a_message(client):
    """The provider failing must degrade to a message, never a 500 -- this is the path a real
    payment outage takes."""
    import stripe as stripe_lib

    user = UserFactory()
    client.force_login(user)

    with _priced(), _member(False), \
            patch('users.views.SubscriptionService.create_checkout_session',
                  side_effect=stripe_lib.error.StripeError('boom')):
        response = client.post(reverse('support_hub'), {'tier': 'patron'})

    assert response.status_code == 302
    assert response['Location'] == reverse('support_hub')


def test_a_paypal_failure_lands_back_on_the_page_with_a_message(client):
    user = UserFactory()
    client.force_login(user)

    with _priced(), _member(False), \
            patch('users.services.paypal_service.PayPalService.create_subscription',
                  side_effect=RuntimeError('paypal down')):
        response = client.post(reverse('support_hub'),
                               {'tier': 'patron', 'provider': 'paypal'})

    assert response.status_code == 302
    assert response['Location'] == reverse('support_hub')


def test_an_anonymous_checkout_carries_next_back_to_support(client):
    """`redirect_to_login(request.get_full_path())` -- the half that was untested is the `next=`,
    which is what brings somebody back to finish what they started after signing in."""
    with _priced():
        response = client.post(reverse('support_hub'), {'tier': 'patron'})

    assert 'next=/support/' in response['Location']


def test_the_old_url_redirect_preserves_a_querystring(client):
    """`query_string=True` is set deliberately on the subscribe redirect and was never exercised.
    Old marketing links with UTM tags should survive the hop."""
    response = client.get(reverse('subscribe') + '?utm_source=discord')

    assert response.status_code == 302
    assert response['Location'] == reverse('support_hub') + '?utm_source=discord'


def test_the_wall_toggle_ignores_an_unrelated_post(client):
    """The handler only acts when `wall_visibility` is in the payload, so an unrelated POST to the
    management page must not silently flip somebody's listing."""
    from tests.factories import ProfileFactory

    profile = ProfileFactory(user__premium_tier='patron')
    assert profile.show_on_supporter_wall is True
    client.force_login(profile.user)

    client.post(reverse('subscription_management'), {'something_else': '1'})
    profile.refresh_from_db()
    assert profile.show_on_supporter_wall is True, 'an unrelated POST flipped the wall consent'


def test_the_wall_toggle_requires_login(client):
    response = client.post(reverse('subscription_management'), {'wall_visibility': '1'})

    assert response.status_code == 302
    assert '/accounts/login/' in response['Location'] or '/login/' in response['Location']


def test_the_wall_toggle_survives_a_user_with_no_profile(client):
    """Signed in, never linked PSN: `request.user.profile` does not exist. The guard must no-op
    rather than 500."""
    user = UserFactory()
    client.force_login(user)

    response = client.post(reverse('subscription_management'), {'wall_visibility': '1'})
    assert response.status_code == 302


def test_ladder_and_legacy_money_add_up_together(client):
    """The band's arithmetic has two arms -- ladder slugs price from the constant, legacy tiers from
    Stripe -- and only the legacy arm was ever exercised. A hunter on each: $15 (patron, constant)
    + $10/mo (legacy yearly $120/12, Stripe) = $25."""
    UserFactory(premium_tier='patron')
    UserFactory(premium_tier='premium_yearly')
    _clear_support_cache()

    yearly = _FakePrice(12000, 'year')
    with patch('users.views.SubscriptionService.get_prices_from_stripe',
               return_value={'premium_yearly': yearly}), _member(False):
        body = re.sub(r'\s+', ' ', client.get(reverse('support_hub')).content.decode())

    band = _band(body)
    assert 'data-countup="25"' in band, 'the two pricing arms do not add up'


def test_an_active_stripe_member_can_load_the_management_page(client):
    """PRE-EXISTING CRASHER, found by the audit outside this lane's diff and fixed with it because
    the lane links every member here.

    The next-billing line used `timezone.utc` where `timezone` is django.utils.timezone -- an alias
    REMOVED in Django 5.0. Every active Stripe member got an AttributeError on the one page that
    manages their money, and nothing noticed because no test ever exercised the active-subscription
    branch (it needs a Subscription row, a Stripe portal call, and a customer id all mocked at once,
    so nobody had).
    """
    user = UserFactory()
    user.subscription_provider = 'stripe'
    user.stripe_customer_id = 'cus_test123'
    user.premium_tier = 'premium_monthly'
    user.save()
    client.force_login(user)

    fake_sub = type('S', (), {'stripe_data': {'status': 'active', 'current_period_end': 1767225600}})()
    fake_portal = type('P', (), {'url': 'https://billing.stripe.com/x'})()

    with patch('users.views.SubscriptionService.has_active_subscription',
               return_value=(True, 'stripe')),             patch('users.views.Subscription.objects') as sub_mgr,             patch('users.views.stripe.billing_portal.Session.create', return_value=fake_portal):
        sub_mgr.filter.return_value.first.return_value = fake_sub
        response = client.get(reverse('subscription_management'))

    assert response.status_code == 200, 'an active Stripe member cannot open their own billing page'
    assert b'billing.stripe.com' in response.content

