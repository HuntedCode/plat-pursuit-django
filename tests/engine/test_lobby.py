"""The lobby at `/` (2026-08).

`/` stopped being the personal hub's Overview tab and became the page ABOVE the four hubs: where every
login lands, belonging to no hub, carrying no sub-nav rail. What needs pinning is the part that is easy to
regress silently -- that `/` is still a four-state router, that none of those states sprouts a hub rail,
and that the wordmark (its only nav affordance) is lit when you are standing on it.
"""
import pytest

from tests.factories import ProfileFactory

pytestmark = pytest.mark.django_db

# Profile-scoped pages sit behind the Cloudflare origin guard; `/` does not, but the header costs nothing
# and keeps these robust if the guarded set ever widens.
CF = {'HTTP_CF_RAY': '8f0000000000abcd-LHR'}


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
    js = (root / 'static' / 'js' / 'home-motion.js').read_text(encoding='utf-8')
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
