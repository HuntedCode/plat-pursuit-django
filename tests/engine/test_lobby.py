"""The lobby at `/` (2026-08).

`/` stopped being the personal hub's Overview tab and became the page ABOVE the four hubs: where every
login lands, belonging to no hub, carrying no sub-nav rail. What needs pinning is the part that is easy to
regress silently -- that `/` is still a four-state router, that none of those states sprouts a hub rail,
and that the wordmark (its only nav affordance) is lit when you are standing on it.
"""
import pytest
from allauth.account.models import EmailAddress
from django.urls import reverse

from tests.factories import ProfileFactory

pytestmark = pytest.mark.django_db

# Profile-scoped pages sit behind the Cloudflare origin guard; `/` does not, but the header costs nothing
# and keeps these robust if the guarded set ever widens.
CF = {'HTTP_CF_RAY': '8f0000000000abcd-LHR'}


def _code(src):
    """Source with comments stripped.

    Load-bearing for every assertion below that forbids a construct: the comment explaining WHY a rule
    is forbidden inevitably names it, so a bare substring check is satisfied by the prose documenting
    the fix rather than by the code. That has bitten four separate guards in this file's history.
    """
    import re

    src = re.sub(r'/\*.*?\*/', '', src, flags=re.S)      # /* block */ (CSS + JS)
    return re.sub(r'(?m)^\s*//.*$', '', src)              # // line (JS)


@pytest.mark.parametrize('state,setup', [
    ('no_psn', dict(is_linked=False)),
    ('syncing', dict(is_linked=True, sync_status='syncing')),
    ('synced', dict(is_linked=True, sync_status='synced')),
])
def test_every_router_state_still_renders_without_a_hub_rail(client, state, setup):
    """`/` routes four ways (anon / no-PSN / syncing / synced) and only the synced branch became the
    lobby -- but the hub-less resolution applies to ALL of them, so none may render a rail. Before the
    change, the authenticated in-between states carried the personal strip; losing it is intended (a
    Pursuer mid-first-sync has nothing behind those tabs yet)."""
    profile = ProfileFactory(**setup)
    client.force_login(profile.user)

    resp = client.get('/', **CF)

    assert resp.status_code == 200, f'{state} -> {resp.status_code}'
    assert b'data-subnav-rail' not in resp.content, f'{state} rendered a hub rail on the lobby'


def test_the_anonymous_landing_still_renders(client):
    resp = client.get('/', **CF)

    assert resp.status_code == 200
    assert b'data-subnav-rail' not in resp.content


def test_the_wordmark_is_lit_on_the_lobby(client):
    """It is the only nav route to `/`, so it takes a hub-style active state there -- and must NOT take
    one anywhere else, or every page would look like the lobby."""
    profile = ProfileFactory(is_linked=True, sync_status='synced')
    client.force_login(profile.user)

    assert b'pp-nav__brand is-active' in client.get('/', **CF).content
    assert b'pp-nav__brand is-active' not in client.get('/career/', **CF).content


def test_the_lobby_leads_with_the_trophy_floor(client):
    """Order is the argument: the trophy block is what everyone arrives for and the only block already
    full on the day someone finishes their first sync. If the moat CTAs ever climb above it, the page has
    quietly become a gamification landing again -- which is the cold-start case this ordering exists for.
    """
    profile = ProfileFactory(is_linked=True, sync_status='synced')
    client.force_login(profile.user)

    body = client.get('/', **CF).content

    assert body.index(b'Trophy collection') < body.index(b'home-moats'), (
        'the moat CTAs moved above the trophy block'
    )


# --- Premium pass: header, landing numbers, hover responses, mobile fit ---

def test_the_lobby_leads_with_an_accented_header_card(client):
    """Every rebuilt page opens with one (playbook §2). The lobby used to start on an 11px uppercase
    label, which gave the site's front door no identity and no sense of place."""
    profile = ProfileFactory(is_linked=True, sync_status='synced')
    client.force_login(profile.user)

    body = client.get('/', **CF).content

    assert b'border-l-primary' in body, 'no accented header card'
    assert b'Welcome back' in body
    assert b'pp-head-cascade' in body, 'the header has no opening beat'
    # Freshness leads: it is the lobby's reason to exist, so it belongs in the header.
    assert body.index(b'home-hi__sync') < body.index(b'Trophy collection')


def test_sync_now_delegates_rather_than_duplicating_the_control(client):
    """navsync.js owns a state machine for that button (disabled while syncing, progress, queue
    position) and binds it with a SINGLE querySelector scoped to the navbar. A second real button would
    neither bind nor stay in step, so the header's affordance presses the real one."""
    from pathlib import Path

    js = (Path(__file__).resolve().parents[2] / 'static' / 'js' / 'home-motion.js').read_text(encoding='utf-8')

    assert 'data-home-syncnow' in js
    assert "querySelector('[data-nav-syncnow]')" in js, 'the header no longer delegates to the real control'


def test_numbers_land_without_ever_rendering_a_wrong_value():
    """The count-up gets a spring, but on the TRANSFORM -- overshooting the value would mean painting a
    figure that is briefly untrue, which reads as a glitch rather than as physics."""
    from pathlib import Path

    js = (Path(__file__).resolve().parents[2] / 'static' / 'js' / 'home-motion.js').read_text(encoding='utf-8')
    tick = js[js.index('function tickUp'):js.index('// --- 2. Horizon fill')]

    assert 'scale(1.08)' in tick, 'the landing pop is gone'
    assert '1 - Math.pow(1 - p, 3)' in tick, 'the value easing is no longer monotonic ease-out'


def test_the_premium_beats_are_reduced_motion_gated():
    from pathlib import Path

    root = Path(__file__).resolve().parents[2]
    js = _code(( root / 'static' / 'js' / 'home-motion.js').read_text(encoding='utf-8'))
    css = (root / 'static' / 'css' / 'components' / 'home.css').read_text(encoding='utf-8')

    assert 'prefers-reduced-motion: reduce' in js, 'count-ups run regardless of the setting'
    # The hover responses and the breathing dot are motion, so both sit behind no-preference.
    assert 'hover: hover) and (prefers-reduced-motion: no-preference)' in css
    assert css.count('prefers-reduced-motion: no-preference') >= 2


def test_the_horizon_fill_reads_the_root_and_resets_without_animating():
    """Two bugs this pins, both of which shipped in the first cut of the premium pass and neither of
    which a check of the END state would catch -- the bar was correct at rest either way.

    1. `--horizon-progress` lives on the `.pp-horizon` ROOT. Reading it off `.pp-horizon__fill` returns
       '', so the animation silently never ran at all.
    2. The reset has to suppress the fill's `width` transition. Without that the bar SLIDES BACKWARDS
       from its server-rendered width down to zero before filling, which reads as a bug rather than a
       beat -- worse than no animation.
    """
    from pathlib import Path

    js = (Path(__file__).resolve().parents[2] / 'static' / 'js' / 'home-motion.js').read_text(encoding='utf-8')
    prime = js[js.index('function primeHorizons'):js.index('function releaseHorizons')]

    assert "querySelectorAll('main .pp-horizon')" in prime, 'the fill is being read instead of the root'
    assert "fill.style.transition = 'none'" in prime, 'the reset will animate backwards'
    assert 'void fill.offsetWidth' in prime, 'the reset is not flushed, so suppressing the transition is a no-op'


def test_the_ring_speeds_up_by_RATE_not_by_duration():
    """The ring picks up pace while the CTA is hovered -- but CSS cannot express that without a jump.
    Changing `animation-duration` re-maps elapsed time onto the new duration, so labDnaSpin
    (0 -> 360 -> 720deg with a creep and a whoosh) snaps to a different angle the moment you hover; that
    was the flip. updatePlaybackRate() changes SPEED while preserving current time.

    The primitive's own pause-on-hover is deliberately untouched: play state and playback rate are
    independent, so "faster over the card, stopped over the ring" composes instead of conflicting.
    """
    import re
    from pathlib import Path

    root = Path(__file__).resolve().parents[2]
    js = _code((root / 'static' / 'js' / 'home-motion.js').read_text(encoding='utf-8'))
    css = (root / 'static' / 'css' / 'components' / 'home.css').read_text(encoding='utf-8')
    hover = css[css.index('/* ---- D. The moats respond'):css.index('@media (prefers-reduced-motion: reduce)')]
    # Strip comments: the rule's own explanation NAMES the property it avoids, so a bare substring check
    # is satisfied by the prose documenting the fix.
    rules = _code(hover)

    assert 'animation-duration' not in rules, 'the CTA is re-timing the ring again, which makes it flip'
    assert 'updatePlaybackRate' in js, 'the ring no longer speeds up at all'
    # The pause belongs to the primitive; this file must not start driving play state as well.
    assert 'animationPlayState' not in js and 'play-state' not in js, (
        "the lobby is overriding the ring's own pause-on-hover instead of composing with it"
    )


def test_only_the_hovered_medallion_moves():
    """An earlier cut fanned all three whenever the CARD was hovered, so badges you were not pointing at
    moved and the tilt pulled the eye off the one you were."""
    import re
    from pathlib import Path

    css = (Path(__file__).resolve().parents[2] / 'static' / 'css' / 'components' / 'home.css').read_text(encoding='utf-8')
    rules = _code(css)

    assert '.home-moat__med:hover' in rules, 'the medallions no longer respond at all'
    assert '.home-moat--collection:hover .home-moat__med' not in rules, 'the card-level fan is back'
    assert 'rotate(' not in rules[rules.index('.home-moat__med'):rules.index('.home-moat__med') + 400], (
        'the medallions are tilting again -- a straight rise was the ask'
    )


# ── Django messages on `/` (2026-08-23 bug) ───────────────────────────────────────────────────
# The home templates rendered messages NOWHERE (the breadcrumb partial is the site's default
# renderer and the lobby deliberately carries no breadcrumb), so a login's "signed in" message
# queued silently and surfaced on whatever page the user visited NEXT. All four router states
# now include partials/_messages.html.

def _login(client, password='lobby-msg-pass-1', **profile_kwargs):
    profile = ProfileFactory(**profile_kwargs)
    user = profile.user
    user.set_password(password)
    user.save()
    EmailAddress.objects.create(user=user, email=user.email, verified=True, primary=True)
    return client.post(reverse('account_login'),
                       {'login': user.email, 'password': password}, follow=True, **CF)


@pytest.mark.parametrize('state,setup', [
    ('no_psn', dict(is_linked=False)),
    ('syncing', dict(is_linked=True, sync_status='syncing')),
    ('synced', dict(is_linked=True, sync_status='synced')),
])
def test_the_login_message_shows_on_the_lobby_exactly_once(client, state, setup):
    """The real reported flow, in every authed router state: sign in, land on `/`, and the
    'signed in' message renders THERE rather than queuing for the next breadcrumb page.
    EXACTLY once: the syncing state carries the breadcrumb partial (a second messages
    renderer), and its first cut double-rendered every message in two visual languages --
    the count is the assertion that catches that whole class."""
    resp = _login(client, **setup)

    assert resp.status_code == 200
    assert resp.request['PATH_INFO'] == '/', f'{state}: login should land on the lobby'
    n = resp.content.decode().count('Successfully signed in')
    assert n == 1, (
        f'{state}: rendered {n} times -- zero means the message queued for the next page, '
        f'two means a breadcrumb page also includes _messages.html'
    )


def test_every_router_template_consumes_messages_through_exactly_one_renderer():
    """The anon landing cannot receive a login message, so the source pin covers it with the
    rest: each state of `/` consumes the framework through ONE renderer -- three include the
    shared partial, and syncing gets it via the breadcrumb it already carries (including both
    was the audit-caught double-render)."""
    from pathlib import Path

    root = Path(__file__).resolve().parents[2] / 'templates'
    for name in ('trophies/home.html', 'home/landing.html', 'home/link_psn.html'):
        src = (root / name).read_text(encoding='utf-8')
        assert "partials/_messages.html" in src, f'{name} renders messages nowhere'
        assert 'partials/breadcrumb.html' not in src, f'{name} would double-render'
    syncing = (root / 'home/syncing.html').read_text(encoding='utf-8')
    assert 'partials/breadcrumb.html' in syncing, 'syncing lost its renderer'
    assert "partials/_messages.html" not in syncing, 'syncing would double-render'


def test_the_lobby_marks_its_names_like_everywhere_else(client):
    """The two name renders (welcome header + Career moat) wear the display mark, consistent
    with every other surface. A supporter's stars must show in both spots."""
    profile = ProfileFactory(is_linked=True, sync_status='synced')
    user = profile.user
    user.premium_tier = 'backer'
    user.save(update_fields=['premium_tier'])
    profile.user_is_premium = True
    profile.display_mark = 'backer'
    profile.save(update_fields=['user_is_premium', 'display_mark'])
    client.force_login(user)

    body = client.get('/', **CF).content.decode()

    assert body.count('pp-markname') >= 2, 'both the header and the moat must carry the mark'
