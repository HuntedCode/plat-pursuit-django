"""Game Lists is LIVE (2026-09).

This was `test_lists_hidden.py`, which pinned the 2026-08 teardown: nothing on the site led INTO the
system, because the ways a parked feature leaks back are all quiet ones -- a footer link nobody
re-checked, a sub-nav tab, a sitemap entry still inviting crawlers, an API still accepting writes into
a system with no door.

Turning it on inverted about half of that and left the rest exactly as it was, which is why this is
one file rather than a deletion and a rewrite. WHAT FLIPPED: the browse page answers for anybody, the
sitemap advertises it, the chrome leads to it, and a hunter can create a list.

WHAT DID NOT, and is arguably more important now: the LEGACY system stays dead. Its API is still
unrouted, its rows are still untouched, `?tab=lists` still leads nowhere, `/community/lists/1/edit/`
-- an address the rebuild deliberately does not have -- still redirects, and the shared game card
still offers no quick-add. A live feature is exactly when somebody is most likely to wire a new page
to an old view by reaching for a familiar name.
"""
import re
from pathlib import Path

import inspect

import pytest
from django.urls import reverse

from tests.factories import ProfileFactory, UserFactory
from trophies.models import GameList

pytestmark = pytest.mark.django_db

ROOT = Path(__file__).resolve().parents[2]

#: Public as of 2026-09. `/community/lists/` renders for anyone.
PUBLIC_PAGES = ['/community/lists/']
#: Login-only, which is a DIFFERENT thing from gated: it redirects to login rather than refusing a
#: hunter who is signed in.
PERSONAL_PAGES = ['/my-lists/']

PAGES = [
    '/community/lists/1/edit/',
    '/lists/',                # the pre-2026 paths, which used to 301 into the ones above
    '/lists/1/',
]


@pytest.mark.parametrize('url', PAGES)
def test_every_unbuilt_list_page_sends_you_home(client, url):
    resp = client.get(url)

    assert resp.status_code in (301, 302), f'{url} still renders (got {resp.status_code})'
    assert resp['Location'] == '/', f'{url} redirects to {resp["Location"]}, not the homepage'


def test_the_redirect_is_temporary_so_it_can_be_taken_back():
    """A 301 is cached by the browser indefinitely. Using one here would keep sending people to the
    homepage long after the rebuilt system ships -- and specifically the people who used lists most,
    because they are the ones holding the bookmarks."""
    assert client_status('/community/lists/1/edit/') == 302


@pytest.mark.parametrize('url', PUBLIC_PAGES)
def test_the_browse_page_answers_for_anybody(client, url):
    """INVERTED 2026-09. It asserted an anonymous visitor and an ordinary hunter were both turned
    away and only staff got in; all three now get the page.

    The anonymous case is the one that matters, and not only as a formality: it is the reason the
    browse queryset bounds its free-text `q` and its numeric filters at all. An unbounded LIKE on a
    page anybody can reach is a different risk from one behind a staff gate.
    """
    assert client.get(url).status_code == 200, f'{url} is not public'

    hunter = UserFactory()
    ProfileFactory(user=hunter, is_linked=True, psn_username=f'hunter{hunter.pk}')
    client.force_login(hunter)
    assert client.get(url).status_code == 200, f'{url} does not render for an ordinary hunter'


@pytest.mark.parametrize('url', PERSONAL_PAGES)
def test_my_lists_is_personal_rather_than_gated(client, url):
    """A DIFFERENT property, and it did not change. My Lists has always been login-only, and the
    un-hide must not have made it public by removing a mixin next to the ones it meant to remove."""
    assert client.get(url).status_code == 302, f'{url} is open to anonymous visitors'

    hunter = UserFactory()
    ProfileFactory(user=hunter, is_linked=True, psn_username=f'mine{hunter.pk}')
    client.force_login(hunter)
    assert client.get(url).status_code == 200, f'{url} does not render for its owner'


def test_no_view_still_carries_a_staff_gate():
    """The switch was six mixin references plus the class. Leaving one behind would gate a single
    surface while the rest opened -- the hardest half-shipped state to notice, because the feature
    demonstrably works."""
    source = (ROOT / 'gamelists' / 'views.py').read_text(encoding='utf-8')
    # PROSE STRIPPED FIRST. The module docstring legitimately explains what the gate was and why it
    # went, so a bare membership check failed on the history rather than on any live code -- the
    # mirror of a comment SATISFYING an assertion, and just as misleading.
    code = re.sub(r'"""(?:.|\n)*?"""', '', source)
    code = re.sub(r'^\s*#.*$', '', code, flags=re.M)

    assert '_DevelopmentGate' not in code, 'a development gate survived the un-hide'
    assert 'StaffRequiredMixin' not in code, 'a staff mixin survived the un-hide'


def test_a_public_list_carries_its_own_social_card(client):
    """Until the un-hide the detail page set a `{% block title %}` and no `seo_description`, so every
    list anybody posted anywhere previewed as the site-wide generic. It is the indexable, shareable
    page in the whole feature -- the one thing a link to Game Lists is most likely to BE."""
    from gamelists.services import game_list_service as svc

    owner = ProfileFactory(is_linked=True, psn_username='author')
    described = svc.create_list(owner, name='Hardest Platinums',
                                description='Thirty that broke me.', is_public=True)

    ctx = client.get(f'/community/lists/{described.id}/').context
    # The author's own words win: they wrote them to say what the list is for.
    assert ctx['seo_description'] == 'Thirty that broke me.'
    assert 'Hardest Platinums' in ctx['seo_title'] and 'author' in ctx['seo_title']

    # Without a description it is BUILT rather than left blank -- a bare "a game list" tells a reader
    # deciding whether to click precisely nothing.
    bare = svc.create_list(owner, name='No words', is_public=True)
    bare_ctx = client.get(f'/community/lists/{bare.id}/').context
    assert 'No words' in bare_ctx['seo_description']
    assert 'author' in bare_ctx['seo_description']


def test_creating_a_list_records_the_event_that_was_never_reachable(client):
    """`game_list_create` has been declared in `core/models.py` since 2019 and had exactly one call
    site -- in `api/game_list_views.py`, which is unrouted. So a grep found a hit and the event had
    never once been recorded. Wired to the live create in 2026-09."""
    from core.models import SiteEvent

    hunter = UserFactory()
    ProfileFactory(user=hunter, is_linked=True, psn_username='eventful')
    client.force_login(hunter)

    # A REAL User-Agent, or nothing is recorded and the test looks like a wiring bug.
    # `track_site_event` drops bot traffic, and `is_bot_user_agent('')` is True -- an empty UA is
    # classified as a bot, which is what the Django test client sends by default. Worth knowing
    # before writing any other SiteEvent test.
    client.post('/community/lists/create/', {'name': 'Tracked'},
                HTTP_USER_AGENT='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36')

    assert SiteEvent.objects.filter(event_type='game_list_create').exists(), (
        'creating a list recorded nothing')


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


def test_the_chrome_leads_into_lists_from_both_places_that_matter():
    """INVERTED 2026-09. This asserted NO rail tab and NO footer link, because the two places a link
    survives a teardown are the two places one goes missing on a launch: neither is exercised by the
    page you were actually working on.

    Where each one lands is the part worth pinning. Game Lists is public, so it goes in the Community
    rail. My Lists is personal and login-gated, so it goes in My Pursuit -> Tools -- the same split
    that keeps *Collection* in My Pursuit while *Badges* sits in Browse. Putting My Lists in Community
    would be the easy mistake, and the un-hide checklist originally said to.
    """
    from core.hub_subnav import HUB_SUBNAV_CONFIG

    by_key = {hub.key: [i.slug for i in hub.items] for hub in HUB_SUBNAV_CONFIG}

    assert 'lists' in by_key['community'], 'the public browse is not in the Community rail'
    assert 'my_lists' in by_key['my_pursuit'], 'My Lists is not in My Pursuit'
    assert 'my_lists' not in by_key['community'], 'a personal page is in the public hub'
    assert 'lists' not in by_key['browse'], 'the community browse leaked into the catalogue hub'

    # A detail page's URL name never matches its rail item's, and an item shipping without an
    # override is SILENT -- the strip renders with nothing lit.
    from core.hub_subnav import _URL_NAME_TO_SLUG_OVERRIDES
    assert _URL_NAME_TO_SLUG_OVERRIDES['list_detail'] == ('community', 'lists')

    footer = (ROOT / 'templates' / 'partials' / 'footer.html').read_text(encoding='utf-8')
    assert 'lists_browse' in footer, 'the footer sitemap does not reach Game Lists'


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


def test_the_sitemap_advertises_lists_and_reads_the_rebuilt_model():
    """INVERTED 2026-09, and the second half was a live landmine rather than a tidy-up.

    `GameListSitemap` read the LEGACY `trophies.GameList` while `reverse('list_detail')` resolves to
    the REBUILT app. The two tables share nothing but a class name, so uncommenting it unchanged
    would have published several thousand legacy ids against new-app routes -- a sitemap of 404s,
    handed to Google on the day lists turned on. Being commented out of the index is the only reason
    that never shipped, which is exactly the kind of safety you cannot rely on twice.
    """
    from core.sitemaps import GameListSitemap, StaticViewSitemap
    from plat_pursuit.urls import sitemaps

    assert 'lists' in sitemaps, 'the per-list sitemap is still disabled'
    assert 'lists_browse' in StaticViewSitemap().items(), 'the browse index is not advertised'

    # THE MODEL IT READS, asserted directly: `items()` returning nothing on an empty database would
    # prove nothing either way.
    from gamelists.models import GameList as RebuiltGameList
    assert GameListSitemap().items().model is RebuiltGameList, (
        'the sitemap still reads the legacy model, so every URL it emits is a 404')

    # ...and it resolves through the rebuilt route for a real row.
    from gamelists.services import game_list_service as svc
    owner = ProfileFactory(is_linked=True, psn_username='sitemapper')
    published = svc.create_list(owner, name='Out in the world', is_public=True)
    assert GameListSitemap().location(published) == f'/community/lists/{published.id}/'


def test_no_game_card_offers_to_add_to_a_list():
    """The least obvious entry point and the most numerous: the quick-add button rode on the shared game
    card, so it appeared on Browse Games, Recently Added, tag/franchise/company grids and game detail.
    A button that files a game somewhere unreachable is worse than no button."""
    for rel in ('templates/trophies/partials/game_list/game_cards.html',
                'templates/trophies/partials/game_detail/hero.html',
                'templates/trophies/game_detail.html',
                'templates/trophies/game_list.html',
                'templates/trophies/recently_added.html',
                'templates/trophies/tag_detail.html',
                'templates/trophies/trophy_lists.html'):
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
                'templates/trophies/recently_added.html', 'templates/trophies/tag_detail.html',
                'templates/trophies/trophy_lists.html'):
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

    Hidden, not deleted -- but that was scoped too narrowly. Removing the CHIP left the tab still
    RENDERING for anyone who typed `?tab=lists`, with cards whose links bounce home. The builder and
    the template map entry are gone as of 2026-09; the rebuilt system brings its own tab.
    """
    owner = ProfileFactory(is_linked=True)
    GameList.objects.create(profile=owner, name='Public list', is_public=True, game_count=2)

    body = client.get(f'/hunters/{owner.psn_username}/', HTTP_CF_RAY='8f0000000000abcd-LHR').content.decode()

    assert 'data-tab="lists"' not in body, 'the profile still offers a Lists tab'
    assert '?tab=lists' not in body, 'something on the profile still links into lists'


def test_typing_the_lists_tab_does_not_render_it(client):
    """The chip was removed and the tab still answered. Asserted on the RESPONSE, not the chrome:
    the previous guard passed the whole time this was live, because it only looked for a way in."""
    owner = ProfileFactory(is_linked=True)
    GameList.objects.create(profile=owner, name='Bounced list', is_public=True, game_count=2)

    url = f'/hunters/{owner.psn_username}/?tab=lists'
    full = client.get(url, HTTP_CF_RAY='8f0000000000abcd-LHR')
    htmx = client.get(url, HTTP_HX_REQUEST='true', HTTP_HX_TARGET='tab-content',
                      HTTP_CF_RAY='8f0000000000abcd-LHR')

    for label, resp in (('full page', full), ('htmx swap', htmx)):
        # The status check is not ceremony. Without it a 404 -- a renamed route, a changed factory --
        # satisfies "the list is absent" while proving nothing, which is how the guard this replaced
        # managed to pass for the whole time the tab was live.
        assert resp.status_code == 200, f'{label} answered {resp.status_code}, so this proves nothing'
        assert 'Bounced list' not in resp.content.decode(), f'{label} still renders the lists tab'


def test_an_unknown_tab_gets_a_fragment_not_the_whole_site(client):
    """`get_template_names` had no default, so a tab that was not in either template map fell all the
    way through to the full page -- which htmx then swapped INTO the tab panel. `?tab=lists` walked
    into that when the map entry was removed, but it was never specific to lists: any junk value did
    it, and the InfiniteScroller rebuilds the query string from the address bar, so a stale
    `?tab=lists` link plus one scroll appended a second copy of the document into the grid.
    """
    owner = ProfileFactory(is_linked=True)

    for junk in ('lists', 'nonsense'):
        resp = client.get(
            f'/hunters/{owner.psn_username}/?tab={junk}',
            HTTP_HX_REQUEST='true', HTTP_HX_TARGET='tab-content',
            HTTP_CF_RAY='8f0000000000abcd-LHR',
        )
        body = resp.content.decode()

        assert resp.status_code == 200
        assert '<!doctype html' not in body.lower(), (
            f'?tab={junk} answered an htmx swap with the entire page'
        )
        assert '<nav' not in body.lower(), f'?tab={junk} nested the site chrome inside the tab panel'


def test_no_profile_render_counts_a_parked_systems_rows(client):
    """It ran `GameList.objects.filter(...).count()` on EVERY hunter profile render, feeding a
    context key no template read. A query for a hidden system, for nobody."""
    from django.db import connection
    from django.test.utils import CaptureQueriesContext

    owner = ProfileFactory(is_linked=True)
    GameList.objects.create(profile=owner, name='Public list', is_public=True, game_count=2)

    with CaptureQueriesContext(connection) as captured:
        resp = client.get(f'/hunters/{owner.psn_username}/', HTTP_CF_RAY='8f0000000000abcd-LHR')

    # A 404 runs no queries at all, so "no query mentions gamelist" would be trivially true.
    assert resp.status_code == 200, 'the profile did not render; the query assertion is vacuous'

    listy = [q['sql'] for q in captured.captured_queries if 'gamelist' in q['sql'].lower()]
    assert not listy, f'the profile still queries the parked list tables: {listy}'


def test_the_create_endpoint_takes_a_hunters_post_but_not_an_anonymous_one(client):
    """The only route that WRITES, and HALF of this inverted.

    An ordinary hunter can create a list now; an anonymous visitor still cannot. The second half is
    worth more after the un-hide than before it: while the whole feature was staff-only, an
    anonymous POST was refused twice over, and now it is refused once.
    """
    from gamelists.models import GameList as RebuiltGameList

    url = '/community/lists/create/'

    assert client.post(url, {'name': 'Should not exist'}).status_code == 302, \
        'anonymous POST was not refused'
    assert RebuiltGameList.objects.count() == 0, 'a refused POST still created a list'

    hunter = UserFactory()
    ProfileFactory(user=hunter, is_linked=True, psn_username='ordinary')
    client.force_login(hunter)

    assert client.post(url, {'name': 'Mine now'}).status_code == 302, 'a hunter cannot create'
    assert RebuiltGameList.objects.filter(name='Mine now').exists(), 'the list was not created'


def test_the_old_list_search_endpoint_is_gone():
    """`/api/v1/games/search/` was the last routed survivor of the old list system, and it leaked.

    `?exclude_list=<id>` read `GameListItem` for ANY list id with no ownership and no `is_public`
    check, so any authenticated user could infer a private list's contents from which games came
    back excluded. Its only caller was `static/js/game-lists.js`, loaded solely by
    `game_list_detail.html` and `game_list_edit.html` -- both unreachable once `list_detail` began
    resolving to the rebuilt app and `list_edit` became a redirect stub.

    `NoReverseMatch` alone would be a weak pin here (it also fires for a name that merely needs
    arguments), so the view class is asserted gone as well.

    AND THE NAME IS NAMESPACED. `api/urls.py` sets `app_name = 'api'`, so `reverse('game-search')`
    raises `NoReverseMatch` whether or not the route exists -- the bare name never resolves under a
    namespaced include. The first version asserted exactly that, two lines below a docstring warning
    about weak reverse pins, and passed with the route restored. Mutation caught it; `api:` is what
    makes the assertion mean anything. A positive control below proves the namespace itself works.
    """
    from django.urls import NoReverseMatch, reverse

    import api.game_list_views as old_views

    # Control: the namespace resolves, so the failure below is about THIS name, not the prefix.
    assert reverse('api:generate-code').startswith('/api/v1/')

    with pytest.raises(NoReverseMatch):
        reverse('api:game-search')

    assert not hasattr(old_views, 'GameSearchView'), 'the leaking view is still importable'


def test_the_rebuilt_search_is_scoped_where_the_old_one_was_not():
    """The replacement resolves the list through `readable_by` BEFORE reading its items, which is
    the check the deleted endpoint never had. Asserted as a positive control, so the test above
    cannot pass merely because search stopped existing altogether."""
    from gamelists.views import ListGameSearchView

    source = inspect.getsource(ListGameSearchView)
    assert 'readable_by' in source
