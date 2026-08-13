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
