"""Game Lists is hidden pending a revamp (2026-08).

Hidden, not deleted: the views, models, templates and every row of user data are intact, and the rebuilt
browse page is sitting there waiting. What this pins is that nothing on the site leads INTO it, because
the ways a parked system leaks back are all quiet ones -- a footer link nobody re-checked, a sub-nav tab,
a sitemap entry that keeps inviting crawlers, an API still accepting writes into a system with no door.

The paired `test_lists_browse.py` holds the rebuild's own assertions and is skipped alongside the system.
"""
import re
from pathlib import Path

import pytest
from django.urls import reverse

from tests.factories import ProfileFactory
from trophies.models import GameList

pytestmark = pytest.mark.django_db

ROOT = Path(__file__).resolve().parents[2]

PAGES = [
    '/community/lists/',
    '/community/lists/create/',
    '/community/lists/1/',
    '/community/lists/1/edit/',
    '/my-lists/',
    '/lists/',                # the pre-2026 paths, which used to 301 into the ones above
    '/lists/1/',
]


@pytest.mark.parametrize('url', PAGES)
def test_every_list_page_sends_you_home(client, url):
    resp = client.get(url)

    assert resp.status_code in (301, 302), f'{url} still renders (got {resp.status_code})'
    assert resp['Location'] == '/', f'{url} redirects to {resp["Location"]}, not the homepage'


def test_the_redirect_is_temporary_so_it_can_be_taken_back():
    """A 301 is cached by the browser indefinitely. Using one here would keep sending people to the
    homepage long after the rebuilt system ships -- and specifically the people who used lists most,
    because they are the ones holding the bookmarks."""
    assert client_status('/community/lists/') == 302
    assert client_status('/my-lists/') == 302


def client_status(url):
    from django.test import Client
    return Client().get(url).status_code


def test_the_api_no_longer_accepts_writes_into_a_system_with_no_door(client):
    """An endpoint left answering would let anything still holding a reference file games into a system
    nobody can open, which the revamp then has to reconcile. Checked before withdrawing them: PlatBot
    does not call /api/v1/lists/."""
    owner = ProfileFactory(is_linked=True)
    client.force_login(owner.user)

    # A POST to an unrouted path answers 405 rather than 404 on this site: the custom `handler404` is a
    # view that only allows GET/HEAD/OPTIONS, so it rejects the METHOD before it ever reports the missing
    # route. Confirmed against a control path that has never existed, so this asserts "answers like an
    # unrouted path" rather than a specific code -- pinning 404 here would be pinning a quirk.
    control = client.post('/api/v1/definitely-not-a-route/', {}).status_code

    for url in ('/api/v1/lists/', '/api/v1/lists/my/', '/api/v1/lists/quick-add/',
                '/api/v1/lists/1/', '/api/v1/lists/1/items/', '/api/v1/lists/1/like/'):
        assert client.get(url).status_code == 404, f'{url} is still routed'
        assert client.post(url, {}).status_code == control, f'{url} still accepts writes'


def test_nothing_in_the_chrome_points_at_lists():
    """The footer and the community sub-nav are the two places a link survives a teardown, because
    neither is exercised by the page you were actually working on."""
    from django.contrib.auth.models import AnonymousUser
    from django.test import RequestFactory

    from plat_pursuit.context_processors import hub_subnav

    req = RequestFactory().get('/community/')
    req.user = AnonymousUser()
    slugs = [i.slug for i in hub_subnav(req)['hub_subnav_items']]
    assert 'lists' not in slugs, 'the Lists tab is back in the community sub-nav'

    footer = (ROOT / 'templates' / 'partials' / 'footer.html').read_text(encoding='utf-8')
    assert 'lists_browse' not in footer and 'my_lists' not in footer, 'a footer link survived'


def test_the_community_hub_does_not_advertise_lists_or_pay_to_build_them():
    """The hub card is gone, and so is the query behind it -- a spotlight nobody renders is a query per
    page load on the busiest community page."""
    hub = (ROOT / 'templates' / 'community' / 'hub.html').read_text(encoding='utf-8')
    assert 'recent_lists' not in hub, 'the hub still renders the lists spotlight'

    service = (ROOT / 'core' / 'services' / 'community_hub_service.py').read_text(encoding='utf-8')
    body = service[service.index('def build_community_hub_context'):]
    assert '_get_recent_lists_spotlight()' not in body, 'the hub still computes the lists spotlight'


def test_the_sitemap_stops_inviting_crawlers_in():
    from plat_pursuit.urls import sitemaps
    assert 'lists' not in sitemaps


def test_no_game_card_offers_to_add_to_a_list():
    """The least obvious entry point and the most numerous: the quick-add button rode on the shared game
    card, so it appeared on Browse Games, Recently Added, tag/franchise/company grids and game detail.
    A button that files a game somewhere unreachable is worse than no button."""
    for rel in ('templates/trophies/partials/game_list/game_cards.html',
                'templates/trophies/partials/game_detail/hero.html',
                'templates/trophies/game_detail.html',
                'templates/trophies/game_list.html',
                'templates/trophies/recently_added.html',
                'templates/trophies/tag_detail.html'):
        src = (ROOT / rel).read_text(encoding='utf-8')
        # Strip the notes explaining the removal, which naturally name the thing they removed.
        src = re.sub(r'\{%\s*comment\s*%\}.*?\{%\s*endcomment\s*%\}', '', src, flags=re.S)
        assert 'quick-add-trigger' not in src, f'{rel} still renders the add-to-list button'
        assert 'GameListQuickAdd' not in src, f'{rel} still boots the add-to-list widget'


def test_live_pages_do_not_ship_the_dead_list_controller():
    """`game-lists.js` is ~1,200 lines whose only entry point on these pages was the add-to-list button.
    Left in place it downloads, parses and binds nothing -- on game detail and Browse Games, which are
    two of the busiest pages on the site. It stays on the two list pages themselves, which are hidden."""
    for rel in ('templates/trophies/game_detail.html', 'templates/trophies/game_list.html',
                'templates/trophies/recently_added.html', 'templates/trophies/tag_detail.html'):
        src = (ROOT / rel).read_text(encoding='utf-8')
        src = re.sub(r'\{%\s*comment\s*%\}.*?\{%\s*endcomment\s*%\}', '', src, flags=re.S)
        assert 'game-lists.js' not in src, f'{rel} still ships the list controller'


def test_no_url_conf_imports_a_view_it_no_longer_routes():
    """A name imported with nothing using it is the residue a teardown leaves, and it is what makes the
    next person think the routes are still there."""
    import ast

    for rel in ('plat_pursuit/urls.py', 'api/urls.py'):
        tree = ast.parse((ROOT / rel).read_text(encoding='utf-8'))
        imported = {a.asname or a.name for n in ast.walk(tree)
                    if isinstance(n, ast.ImportFrom) for a in n.names}
        used = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)}
        dead = sorted(i for i in imported - used if 'GameList' in i or i in {'BrowseListsView', 'MyListsView'})
        assert not dead, f'{rel} imports {dead} but routes none of them'


def test_the_community_hub_grid_fits_what_is_left_in_it():
    """The Game Lists card was the third of three in a `lg:grid-cols-3` row. Removing it without the
    column count left two cards and a hole on a main navigation surface."""
    hub = (ROOT / 'templates' / 'community' / 'hub.html').read_text(encoding='utf-8')
    i = hub.index('mt-3 grid grid-cols-1')
    grid_open = hub[i:hub.index('>', i)]

    depth, k, children = 1, hub.index('>', i) + 1, 0
    while depth > 0:
        m = re.compile(r'</?(?:div|section)\b').search(hub, k)
        if m.group(0).startswith('</'):
            depth -= 1
        else:
            if depth == 1:
                children += 1
            depth += 1
        k = m.end()

    cols = int(re.search(r'lg:grid-cols-(\d)', grid_open).group(1))
    assert cols == children, f'{children} cards in a {cols}-column row leaves {cols - children} empty'


def test_the_data_is_untouched():
    """The point of hiding rather than removing. Somebody's carefully ordered backlog is still there."""
    owner = ProfileFactory(is_linked=True)
    gl = GameList.objects.create(profile=owner, name='Still here', is_public=True, game_count=3)

    assert GameList.objects.filter(pk=gl.pk).exists()
    assert reverse('list_detail', args=[gl.id]) == f'/community/lists/{gl.id}/', (
        'the URL names still resolve -- unreachable templates reference them, and the revamp needs them'
    )
