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
    """The footer and the sub-nav rails are the two places a link survives a teardown, because neither
    is exercised by the page you were actually working on. (The Community rail this used to check was
    itself retired in 2026-08, so the check now sweeps every configured hub.)"""
    from core.hub_subnav import HUB_SUBNAV_CONFIG

    for hub in HUB_SUBNAV_CONFIG:
        slugs = [i.slug for i in hub.items]
        assert 'lists' not in slugs, f'the Lists tab is back in the {hub.key} sub-nav'

    footer = (ROOT / 'templates' / 'partials' / 'footer.html').read_text(encoding='utf-8')
    assert 'lists_browse' not in footer and 'my_lists' not in footer, 'a footer link survived'


def test_nothing_advertises_lists_or_pays_to_build_a_spotlight():
    """The hub that carried the lists spotlight (and the query behind it) was retired wholesale in
    2026-08, so the surface this used to guard no longer exists. What still needs guarding is that the
    spotlight does not reappear somewhere else -- a query per page load for a system with no door."""
    assert not (ROOT / 'core' / 'services' / 'community_hub_service.py').exists()

    offenders = [
        path.relative_to(ROOT)
        for path in list((ROOT / 'core').rglob('*.py')) + list((ROOT / 'templates').rglob('*.html'))
        if '_get_recent_lists_spotlight' in path.read_text(encoding='utf-8')
        or 'recent_lists' in path.read_text(encoding='utf-8')
    ]
    assert not offenders, f'the lists spotlight came back in {offenders}'


def test_the_sitemap_stops_inviting_crawlers_in():
    """Both halves. Dropping the per-list ListSitemap was the obvious one; the browse page also sat in
    the STATIC sitemap, still advertising `/community/lists/` -- which redirects to the homepage. A
    sitemap entry that resolves to a redirect spends crawl budget to arrive somewhere it did not ask
    for, and keeps signalling that a hidden system is live."""
    from core.sitemaps import StaticViewSitemap
    from plat_pursuit.urls import sitemaps

    assert 'lists' not in sitemaps
    assert 'lists_browse' not in StaticViewSitemap().items()


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


def test_the_data_is_untouched():
    """The point of hiding rather than removing. Somebody's carefully ordered backlog is still there."""
    owner = ProfileFactory(is_linked=True)
    gl = GameList.objects.create(profile=owner, name='Still here', is_public=True, game_count=3)

    assert GameList.objects.filter(pk=gl.pk).exists()
    assert reverse('list_detail', args=[gl.id]) == f'/community/lists/{gl.id}/', (
        'the URL names still resolve -- unreachable templates reference them, and the revamp needs them'
    )


def test_no_profile_tab_leads_into_lists(client):
    """The door this guard did not cover. A profile carried a Lists tab whose cards linked to
    /lists/<id>/ -- routes that redirect home -- so following one from a profile bounced the reader to
    the homepage. Chrome, ads, the sitemap and game cards were all checked; a per-profile tab was not.

    Hidden, not deleted: `_build_lists_tab_context` and `lists_tab.html` are intact for the revamp. What
    must not come back before the system does is the way IN.
    """
    from trophies.views.profile_views import ProfileDetailView

    owner = ProfileFactory(is_linked=True)
    GameList.objects.create(profile=owner, name='Public list', is_public=True, game_count=2)

    body = client.get(f'/hunters/{owner.psn_username}/', HTTP_CF_RAY='8f0000000000abcd-LHR').content.decode()

    assert 'data-tab="lists"' not in body, 'the profile still offers a Lists tab'
    assert '?tab=lists' not in body, 'something on the profile still links into lists'
