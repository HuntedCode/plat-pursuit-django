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
    assert 'Friend' in body, 'the ladder is missing for a signed-out visitor'


def test_a_signed_in_non_member_sees_the_same_ladder(client):
    """While the ladder is placeholders there is no form to render, so signed-in and
    signed-out see the same thing. The difference only returns when the prices exist."""
    user = UserFactory()
    client.force_login(user)
    with _member(False):
        body = _get(client).content.decode()

    assert 'Luminary' in body
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
    assert 'Friend' not in body, 'a member is being sold a second subscription'


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

    assert 'trophies tracked' not in body, 'the page is advertising a zero'
    assert 'sup-head__figs' not in body
    # The rest of the header still stands.
    assert 'Help us build' in body
    assert 'Friend' in body


def test_a_partial_heartbeat_is_treated_as_no_heartbeat(client):
    """All three figures or none. A heartbeat missing one count would otherwise render "tracking
    12,000 trophies across 0 games", which reads as broken rather than as partial."""
    half = {'always': {'trophies_total': {'value': 12000}, 'games_total': {'value': 0},
                       'profiles_total': {'value': 40}}}
    with patch('core.services.site_heartbeat.get_cached_heartbeat', return_value=half), _member(False):
        body = _get(client).content.decode()

    assert 'sup-head__figs' not in body


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
        assert f"${tier['monthly']}" in body
        assert f"${tier['yearly']}" in body, 'the yearly face is not in the markup for the switch'


def test_placeholder_buttons_cannot_be_pressed(client):
    """The ladder is design-only until its twelve Stripe prices and twelve PayPal plans exist. A
    button that looks live and does nothing is worse than one that admits it is not ready."""
    body = _flat(client)

    assert 'disabled aria-disabled="true"' in body, 'placeholder buttons are pressable'
    assert 'Not live yet' in body, 'nothing tells the reader why they cannot press'


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
        assert set(tier) == {'slug', 'name', 'monthly', 'yearly', 'recognition',
                             'stars', 'outline', 'colour'}, (
            f"{tier['slug']} carries something beyond price, recognition and how it looks"
        )
        assert tier['recognition'] in ('none', 'named', 'linked')


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

    assert body.count('type="radio" name="sup-amt"') == 6, 'the amounts are not radios any more'
    assert 'type="radio" name="sup-cycle"' in body, 'the cycle switch is not radio-backed any more'


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


def test_the_preview_says_it_is_a_placeholder(client):
    """It is a mock of a leaderboard row with a stand-in mark, and the marks are not designed yet.
    Shipping an approximation that does not admit to being one is how it quietly becomes the design.
    """
    body = _flat(client)

    assert 'not designed the marks yet' in body


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

    head = body[body.index('sup-head__say'):body.index('sup-head__figs')]         if 'sup-head__figs' in body else body[body.index('sup-head__say'):]
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

    assert heading_mark('luminary').count('<svg class="pp-supstar') == 5, 'the top level is not wearing five stars'
    assert heading_mark('champion').count('<svg class="pp-supstar') == 3
    assert heading_mark('friend').count('pp-supstar is-outline') == 1
    assert heading_mark('ally').count('is-outline') == 0, 'the second level is still an outline'


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
        for rule in re.findall(re.escape(cls) + r'[,{][^}]*}', css):
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
    for tier in SUPPORT_TIERS:
        assert tier['slug'] not in css, (
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


def test_the_star_is_the_level_hue_lifted_toward_white():
    """The star is the same hue as the name at a much lighter value: two tones, one hue, so the pair
    cannot clash.

    Three things were tried and rejected getting here, and each would look like a reasonable tidy-up
    to someone reading this file cold:

      - the star at exactly the name's colour  -> one flat block, the mark says nothing
      - one constant pale star for every level -> levels stop being tellable apart
      - a level-coloured glow behind it        -> rejected on sight

    So the colour must be DERIVED from `--sup-t` (not constant, not raw) and mixed toward white.
    """
    root = pathlib.Path(__file__).resolve().parents[2]
    css = (root / 'static' / 'css' / 'components' / 'supporter.css').read_text(encoding='utf-8')

    rule = css[css.index('.pp-supstar {'):]
    rule = rule[:rule.index(chr(10) + '}')]

    assert '--sup-t' in rule, 'the star is a constant again; levels are no longer tellable apart'
    assert 'color-mix' in rule and 'white' in rule, (
        'the star is the raw level hue, which makes the mark one flat block of colour'
    )


def test_the_star_carries_no_glow():
    """Removed by request, and it is the kind of flourish that creeps back. `drop-shadow` on a mark
    that eventually renders on every supporter name in a virtualized board is also a cost nobody
    asked for."""
    root = pathlib.Path(__file__).resolve().parents[2]
    css = (root / 'static' / 'css' / 'components' / 'supporter.css').read_text(encoding='utf-8')

    star = css[css.index('/* --------------------------------------------------------------------'
                         '---------- the star ---- */'):]
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
        assert f'{tier["name"]} Supporter' in block, (
            f'{tier["slug"]} does not name itself under the name'
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
    assert sub.index('The Completionist') < sub.index('Supporter'), 'the earned title comes first'

