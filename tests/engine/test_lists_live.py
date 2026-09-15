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
    import ast

    # PARSED, NOT STRIPPED. The first version regex-deleted docstrings and whole-line comments --
    # necessary, because this module's docstring legitimately explains what the gate was, so a bare
    # membership check failed on the history rather than on live code. But the regex paired `"""`
    # POSITIONALLY: one stray triple-quote anywhere shifts every later pairing by one, at which point
    # it deletes the code BETWEEN docstrings and a real `StaffRequiredMixin` in that gap is silently
    # stripped -- the guard goes green while the gate is live. It also had no floor: move the views
    # to a package with a re-export shim and both assertions pass against a two-line file.
    #
    # `ast` gives both for free, and asserts against the class BASES rather than the file's text.
    tree = ast.parse((ROOT / 'gamelists' / 'views.py').read_text(encoding='utf-8'))
    classes = {n.name: [ast.unparse(b) for b in n.bases]
               for n in ast.walk(tree) if isinstance(n, ast.ClassDef)}

    assert 'BrowseListsView' in classes, 'the views moved; this guard is reading the wrong file'
    for name, bases in classes.items():
        assert not any('StaffRequired' in b or 'DevelopmentGate' in b for b in bases), (
            f'{name} still carries a staff gate')


def test_a_public_list_carries_its_own_social_card(client):
    """Until the un-hide the detail page set a `{% block title %}` and no `seo_description`, so every
    list anybody posted anywhere previewed as the site-wide generic. It is the indexable, shareable
    page in the whole feature -- the one thing a link to Game Lists is most likely to BE."""
    from gamelists.services import game_list_service as svc

    owner = ProfileFactory(is_linked=True, psn_username='author')
    described = svc.create_list(owner, name='Hardest Platinums',
                                description='Thirty that broke me.', is_public=True)

    resp = client.get(f'/community/lists/{described.id}/')
    ctx = resp.context
    # The author's own words win: they wrote them to say what the list is for.
    assert ctx['seo_description'] == 'Thirty that broke me.'
    assert 'Hardest Platinums' in ctx['seo_title'] and 'author' in ctx['seo_title']

    # AND IT REACHES THE TAG. `base.html` renders `seo_title` through `{% firstof %}` inside
    # `{% block og_title %}` -- a block this template already overrides for `{% block title %}`, so
    # overriding one more would leave the context key set, this test green, and every share preview
    # back on the site-wide generic. That is the exact failure this test exists to prevent, one
    # layer further down than it was looking.
    body = resp.content.decode()
    assert 'Hardest Platinums' in body[:body.index('</head>')], 'seo_title never reaches the head'

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


def test_the_cover_fanout_is_bounded_on_a_public_detail_page(client):
    """The `[:N]` slice on `items` bounds the ROWS. It does not bound this, and the two are different
    numbers: a `Game` is one trophy list PER STACK, so 200 concepts fetch 400-1500 rows, each
    dragging a joined Concept and IGDBMatch.

    That is the half the whale rule is actually about -- the query shape stays O(1) while the bytes
    do not -- on a page that is anonymous, enumerable by id, and now advertised in a sitemap. The
    same shape as the 2026-08 incident where a crawler walking an index was the first domino.
    """
    from gamelists.services import covers
    import inspect

    source = inspect.getsource(covers.cover_games_for)
    assert '[:len(ids) * 4]' in source, 'the cover fan-out is unbounded again'


def test_the_anonymous_browse_search_has_a_floor(client):
    """The typeahead has a rate limit, a 60s cache and a 3-character floor; this page shared only the
    LENGTH bound with it -- and it is the one without a login in front. A one-character `%x%` against
    three columns plus a join is a guaranteed full scan that returns most of the table."""
    owner = ProfileFactory(is_linked=True, psn_username='findable')
    from gamelists.services import game_list_service as svc
    svc.create_list(owner, name='Backlog', is_public=True)

    # A ONE-CHARACTER QUERY THAT WOULD NOT MATCH. `z` appears in neither the name, the description
    # nor the owner's username -- so if the floor is gone and the term reaches the LIKE, the grid is
    # EMPTY; with the floor, the term is ignored and the full grid comes back. The first version of
    # this test searched `z` against a list called "Zebra", which returns one row either way.
    short = client.get('/community/lists/', {'q': 'z'})
    assert short.status_code == 200
    assert len(short.context['game_lists']) == 1, 'a 1-char query reached the LIKE'

    # At the floor it filters for real, which is what stops this passing on a page that ignores `q`
    # altogether.
    assert len(client.get('/community/lists/', {'q': 'ack'}).context['game_lists']) == 1
    assert len(client.get('/community/lists/', {'q': 'qqq'}).context['game_lists']) == 0


def test_game_list_share_is_still_declared_and_unwired():
    """The twin of the bug the un-hide fixed. `game_list_create` was declared in 2019 with its only
    call site in an unrouted module, so a grep found a hit and it had never once fired; that is now
    wired. `game_list_share` is in exactly that state and was left there, because there is no share
    affordance to wire it to yet.

    Recorded rather than assumed, and INVERTED the day a share button ships -- which is the point:
    without this, the next person greps, finds the declaration, and assumes it works.
    """
    from core.models import SiteEvent

    assert any(value == 'game_list_share' for value, _ in SiteEvent._meta.get_field('event_type').choices), (
        'the event type was removed; delete this guard with it')

    wired = [p.relative_to(ROOT) for p in (ROOT / 'gamelists').rglob('*.py')
             if 'game_list_share' in p.read_text(encoding='utf-8')]
    assert not wired, f'game_list_share is wired now -- invert this test and check the analytics: {wired}'


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

    # COMMENTS STRIPPED, the way this file's other source-reading guards do it. Commenting the
    # footer link out is the likeliest way it goes missing, and it leaves the string in the file.
    footer = (ROOT / 'templates' / 'partials' / 'footer.html').read_text(encoding='utf-8')
    footer = re.sub(r'\{%\s*comment\s*%\}.*?\{%\s*endcomment\s*%\}', '', footer, flags=re.S)
    assert "url 'lists_browse'" in footer, 'the footer sitemap does not reach Game Lists'


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

    from gamelists.services import game_list_service as svc
    owner = ProfileFactory(is_linked=True, psn_username='sitemapper')
    published = svc.create_list(owner, name='Out in the world', is_public=True)
    private = svc.create_list(owner, name='Not yours')
    deleted = svc.create_list(owner, name='Gone', is_public=True)
    svc.delete_list(deleted, owner)

    # THE FILTER, which nothing pinned. The model assertion above is blind to it, and
    # `GameListSitemap`'s own docstring weighs `.visible()` as a reasonable alternative -- but
    # `.visible()` is `filter(is_deleted=False)`, which admits EVERY PRIVATE LIST ON THE SITE. That
    # edit would have published them to Google with every assertion here still green.
    advertised = set(GameListSitemap().items().values_list('id', flat=True))
    assert published.id in advertised, 'a public list is not advertised'
    assert private.id not in advertised, 'the sitemap advertises a PRIVATE list'
    assert deleted.id not in advertised, 'the sitemap advertises a soft-deleted list'

    # ...and the route is the rebuilt one. `location()` never consults `items()`, so this is a
    # separate property rather than a stronger version of the above.
    assert GameListSitemap().location(published) == f'/community/lists/{published.id}/'


def test_the_game_card_offers_to_add_to_a_list():
    """The inverse of the guard that stood here, and a lesson about how it stopped working.

    While Game Lists was hidden, this asserted that no template rendered `quick-add-trigger` -- the
    LEGACY class. The entry points are now back under different names (`data-quick-add`,
    `.pp-gcard__add`), so the old assertion went on passing over the exact change it was written to
    catch: a negative over a name cannot notice a name that did not exist when it was written. It is
    the same way `gl-sections__locked` let an upsell slip past its own guard a day earlier.

    So this asserts the POSITIVE. A test that says what should be there fails when that stops being
    true; a test that lists what should not be there passes by default forever.
    """
    card = (ROOT / 'templates/trophies/partials/game_list/game_cards.html').read_text(
        encoding='utf-8')

    assert 'data-quick-add' in card, 'the shared game card offers no way onto a list'
    assert 'data-concept-id="{{ game.concept_id }}"' in card, \
        'the trigger carries no concept, so the popover has nothing to ask about'

    # OUTSIDE THE ANCHOR. The card is an `<a>`, and a `<button>` inside a link is invalid HTML that
    # swallows the link's own activation -- the bug the list detail card documents. The wrapper is
    # what makes the button a sibling instead.
    assert 'pp-gcard-wrap' in card
    assert card.index('</a>') < card.index('data-quick-add'), \
        'the add button is nested inside the card link again'

    # ...and the routes it posts to exist.
    for name in ('lists_for_concept', 'list_create_with_concept'):
        assert reverse(name, args=[1]), name


def test_the_card_offers_nothing_to_a_visitor_or_a_conceptless_row(client):
    """Two negatives that still earn their place, because both are about what the button CANNOT do.

    A signed-out visitor is the bulk of a browse grid's traffic and every other action on the site is
    hidden from them. A conceptless row -- a trophy list whose games were reassigned -- has nothing a
    list could hold, so the button would file nothing."""
    card = (ROOT / 'templates/trophies/partials/game_list/game_cards.html').read_text(
        encoding='utf-8')

    # The gate is one expression, used for the wrapper and the button alike.
    assert 'request.user.is_authenticated and request.user.profile.is_linked' in card
    assert '{% with quick_add=game.concept_id %}' in card, \
        'a conceptless row would be offered a destination it cannot reach'


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


def test_the_legacy_rows_survive_and_are_unreachable(client):
    """The point of hiding rather than removing: somebody's carefully ordered backlog is still there.

    The second half INVERTED at the un-hide. It used to assert that
    `reverse('list_detail', args=[legacy_id])` produced a URL, on the reasoning that unreachable
    templates still referenced the names. Those templates became reachable, and what the assertion
    then said out loud was "a legacy row's id maps to a rebuilt-app URL" -- precisely the sitemap
    landmine, restated as a desired property. The two tables share a class name and nothing else.
    """
    owner = ProfileFactory(is_linked=True)
    gl = GameList.objects.create(profile=owner, name='Still here', is_public=True, game_count=3)

    assert GameList.objects.filter(pk=gl.pk).exists(), 'a legacy row was destroyed'
    # A LEGACY ID MUST NOT RENDER IN THE REBUILT APP. This used to assert the opposite -- that
    # `reverse('list_detail', args=[legacy_id])` produced a URL -- on the reasoning that unreachable
    # templates still referenced the names. Those templates became reachable, and what the assertion
    # then said out loud was "a legacy row's id maps to a rebuilt-app URL": precisely the sitemap
    # landmine, restated as a desired property. The two tables share a class name and nothing else.
    assert client.get(f'/community/lists/{gl.id}/').status_code == 404, (
        'a legacy list id renders in the rebuilt app')


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
