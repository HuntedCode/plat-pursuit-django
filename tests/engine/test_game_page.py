"""The concept-level Game page: /games/<igdb_id>/ wrapping every list that resolves to it.

The resolution rule is the page's soul: identity is the IGDB id, so deliberately-split concepts
(lists split when trophy counts diverge) SHARE one page -- the owner's binding call. These tests
mirror the _build_other_versions semantics tests the rule was lifted from.
"""
import pytest
from django.test import RequestFactory

from tests.factories import ConceptFactory, GameFactory, ProfileFactory, ProfileGameFactory, TrophyFactory
from trophies.models import IGDBMatch
from trophies.views.game_page_views import GamePageView

pytestmark = pytest.mark.django_db


def _match(concept, igdb_id, status='accepted'):
    return IGDBMatch.objects.create(concept=concept, igdb_id=igdb_id, status=status)


def _game(igdb_id=None, concept=None, status='accepted', **over):
    concept = concept or ConceptFactory()
    if igdb_id is not None and not hasattr(concept, 'igdb_match'):
        _match(concept, igdb_id, status)
    over.setdefault('title_platform', ['PS4'])
    return GameFactory(concept=concept, **over)


def _resolve(kwargs):
    view = GamePageView()
    view.request = RequestFactory().get('/')
    return view._resolve(kwargs)


# --- resolution ----------------------------------------------------------------------------------

def test_split_concepts_sharing_an_igdb_id_share_one_page():
    """THE rule. Two separate Concepts (a deliberate trophy-count split), one IGDB id -> one page
    holding both lists."""
    a = _game(igdb_id=555, title_platform=['PS4'])
    b = _game(igdb_id=None, title_platform=['PS5'])
    _match(b.concept, 555)

    games = _resolve({'igdb_id': 555})

    assert {g.pk for g in games} == {a.pk, b.pk}


def test_platform_priority_orders_the_set_ps5_first():
    ps4 = _game(igdb_id=777, title_platform=['PS4'])
    ps5 = _game(igdb_id=None, title_platform=['PS5'])
    _match(ps5.concept, 777)

    games = _resolve({'igdb_id': 777})

    assert [g.pk for g in games] == [ps5.pk, ps4.pk]


def test_unmatched_concept_resolves_by_concept_key():
    concept = ConceptFactory(concept_id='PP_STUB1')
    game = GameFactory(concept=concept, title_platform=['PS3'])

    games = _resolve({'concept_id': 'PP_STUB1'})

    assert [g.pk for g in games] == [game.pk]


def test_a_graduated_concept_301s_to_its_igdb_page():
    """The concept URL is transitional: the moment a trusted match exists, the igdb URL is the
    page, and old links must consolidate rather than duplicate."""
    concept = ConceptFactory(concept_id='PSN_123')
    GameFactory(concept=concept)
    _match(concept, 909)

    view = GamePageView()
    view.request = RequestFactory().get('/games/c/PSN_123/?list=NPWR1_00')
    response = view._resolve({'concept_id': 'PSN_123'})

    assert response.status_code == 301
    assert response.url == '/games/909/?list=NPWR1_00'


def test_an_untrusted_match_does_not_graduate():
    concept = ConceptFactory(concept_id='PSN_456')
    game = GameFactory(concept=concept)
    _match(concept, 909, status='pending_review')

    games = _resolve({'concept_id': 'PSN_456'})

    assert [g.pk for g in games] == [game.pk]


def test_an_empty_set_404s(client):
    assert client.get('/games/999999999/').status_code == 404


# --- the default-list rule -----------------------------------------------------------------------

def _view_for(url, user=None):
    view = GamePageView()
    rf = RequestFactory()
    view.request = rf.get(url)
    if user is not None:
        view.request.user = user
    return view


def test_default_list_is_the_viewers_single_started_stack():
    ps5 = _game(igdb_id=888, title_platform=['PS5'])
    ps4 = _game(igdb_id=None, title_platform=['PS4'])
    _match(ps4.concept, 888)
    viewer = ProfileFactory()
    ProfileGameFactory(profile=viewer, game=ps4, progress=40)

    view = _view_for('/')
    view.list_set = _resolve({'igdb_id': 888})
    progress, _plats, _hours = view._viewer_maps(viewer)

    assert view._default_list(progress).pk == ps4.pk, 'their one started stack wins over PS5-first'


def test_default_list_with_two_started_stacks_is_platform_priority():
    """THREE lists, and the platform-priority winner (PS5) is deliberately UNSTARTED: with two
    started stacks the rule falls back to platform priority, NOT to the first started one. A
    two-list shape cannot discriminate those (list order IS platform order) -- found as an
    unfalsifiable mutant."""
    ps5 = _game(igdb_id=889, title_platform=['PS5'])
    ps4 = _game(igdb_id=None, title_platform=['PS4'])
    ps3 = _game(igdb_id=None, title_platform=['PS3'])
    _match(ps4.concept, 889)
    _match(ps3.concept, 889)
    viewer = ProfileFactory()
    ProfileGameFactory(profile=viewer, game=ps4, progress=40)
    ProfileGameFactory(profile=viewer, game=ps3, progress=10)

    view = _view_for('/')
    view.list_set = _resolve({'igdb_id': 889})
    progress, _plats, _hours = view._viewer_maps(viewer)

    assert view._default_list(progress).pk == ps5.pk


def test_anonymous_default_is_platform_priority_with_zero_viewer_queries():
    from django.db import connection
    from django.test.utils import CaptureQueriesContext

    view = _view_for('/')
    view.list_set = [None]  # unused by the anon branch

    with CaptureQueriesContext(connection) as ctx:
        progress, plats, hours = view._viewer_maps(None)

    assert (progress, plats, hours) == ({}, set(), None)
    assert len(ctx) == 0, 'anonymous must pay zero per-user queries'


def test_unknown_list_param_falls_back_without_redirecting():
    game = _game(igdb_id=890)

    view = _view_for('/games/890/?list=NPWR99999_00')
    view.list_set = _resolve({'igdb_id': 890})

    assert view._selected_list({}).pk == game.pk


# --- rendering -----------------------------------------------------------------------------------

def test_the_page_renders_anonymously_with_a_switcher_even_for_one_list(client):
    """NO conditional IA: a one-list game renders the same page shape with a one-entry switcher."""
    game = _game(igdb_id=901)
    TrophyFactory(game=game, trophy_id=1)

    response = client.get('/games/901/')
    content = response.content.decode()

    assert response.status_code == 200
    assert 'gp-lswitch' in content, 'the switcher element must exist even for one list'
    assert 'gp-lswitch--solo' in content
    assert 'id="gp-viewport"' in content
    assert 'gp-trophy-group-' in content, 'the shared grid must render with the gp- prefix'
    assert 'gd-trophies' in content


def test_list_param_selects_the_named_list(client):
    a = _game(igdb_id=902, title_platform=['PS5'], title_name='Game PS5')
    b = _game(igdb_id=None, title_platform=['PS4'], title_name='Game PS4')
    _match(b.concept, 902)
    TrophyFactory(game=a, trophy_id=1)
    TrophyFactory(game=b, trophy_id=1)

    content = client.get(f'/games/902/?list={b.np_communication_id}').content.decode()

    assert f'href="/games/{b.np_communication_id}/"' in content, 'identity chip must link the selected list'


def test_htmx_viewport_swap_returns_only_the_partial(client):
    game = _game(igdb_id=903)
    TrophyFactory(game=game, trophy_id=1)

    response = client.get(
        f'/games/903/?list={game.np_communication_id}',
        HTTP_HX_REQUEST='true', HTTP_HX_TARGET='gp-viewport',
    )
    content = response.content.decode()

    assert 'gp-idchip' in content and 'gd-trophies' in content
    assert '<html' not in content and 'gp-lswitch' not in content, 'the swap must not return the page'


def test_concept_fallback_url_renders(client):
    concept = ConceptFactory(concept_id='PP_RENDER')
    game = GameFactory(concept=concept)
    TrophyFactory(game=game, trophy_id=1)

    assert client.get('/games/c/PP_RENDER/', HTTP_CF_RAY='test').status_code == 200


def test_anonymous_page_load_pays_no_per_user_queries(client):
    from django.db import connection
    from django.test.utils import CaptureQueriesContext

    game = _game(igdb_id=904)
    TrophyFactory(game=game, trophy_id=1)

    with CaptureQueriesContext(connection) as ctx:
        client.get('/games/904/')

    sql = ' '.join(q['sql'] for q in ctx.captured_queries).lower()
    # The first version of this assertion was a malformed boolean the audit PROVED tautological
    # (the replace() stripped the very substring the left operand then searched for). Plain and
    # conjunctive now, over the per-user tables the viewer path touches.
    assert 'trophies_profilegame' not in sql, 'anonymous render queried ProfileGame'
    assert 'trophies_earnedtrophy' not in sql, 'anonymous render queried EarnedTrophy'
    assert 'trophies_userconceptrating' not in sql, 'anonymous render queried ratings rows'


def test_bots_are_not_canonical_redirected_off_the_concept_fallback(client):
    """BotCanonicalRedirectMiddleware 301s bot hits on /games/<np>/<user>/ to /games/<np>/. Its
    regex read /games/c/<concept_id>/ as that shape, so Googlebot would have been 301'd to the
    nonexistent /games/c/ and indexed a 404 -- the (?!c/) exclusion is what this pins."""
    concept = ConceptFactory(concept_id='PP_BOTPIN')
    game = GameFactory(concept=concept)
    TrophyFactory(game=game, trophy_id=1)

    response = client.get('/games/c/PP_BOTPIN/', HTTP_CF_RAY='test',
                          HTTP_USER_AGENT='Mozilla/5.0 (compatible; Googlebot/2.1)')

    assert response.status_code == 200, f'bot was redirected: {getattr(response, "url", "")}'


# --- SEO: canonicals both directions -------------------------------------------------------------

def test_param_states_canonicalize_to_the_bare_page_url(client):
    """?list= and ?view= are views of ONE document; six stack-states must collapse to one indexed
    page. The canonical, og:url and the jsonld VideoGame url all read one context value."""
    game = _game(igdb_id=905)
    TrophyFactory(game=game, trophy_id=1)

    head = client.get(f'/games/905/?list={game.np_communication_id}&view=about')\
        .content.decode().split('</head>')[0]

    assert 'rel="canonical" href="http://testserver/games/905/"' in head
    assert 'og:url" content="http://testserver/games/905/"' in head
    assert '"url": "http://testserver/games/905/"' in head, 'jsonld must agree with the canonical'


def test_concept_fallback_page_self_canonicalizes(client):
    concept = ConceptFactory(concept_id='PP_CANON')
    game = GameFactory(concept=concept)
    TrophyFactory(game=game, trophy_id=1)

    head = client.get('/games/c/PP_CANON/', HTTP_CF_RAY='test').content.decode().split('</head>')[0]

    assert 'rel="canonical" href="http://testserver/games/c/PP_CANON/"' in head


def test_conceptless_list_page_stays_self_canonical(client):
    """A game with no concept has no Game page to consolidate onto; block.super keeps it
    self-canonical rather than emitting a link to nowhere."""
    game = GameFactory(concept=None)
    TrophyFactory(game=game, trophy_id=1)

    head = client.get(f'/games/{game.np_communication_id}/').content.decode().split('</head>')[0]

    assert f'rel="canonical" href="http://testserver/games/{game.np_communication_id}/"' in head


def test_list_page_canonicalizes_up_to_its_igdb_game_page(client):
    game = _game(igdb_id=906)
    TrophyFactory(game=game, trophy_id=1)

    head = client.get(f'/games/{game.np_communication_id}/').content.decode().split('</head>')[0]

    assert 'rel="canonical" href="http://testserver/games/906/"' in head


# --- sitemap: the page-identity election ---------------------------------------------------------

def test_sitemap_advertises_a_split_concept_page_exactly_once():
    """Two concepts, one trusted igdb id -> ONE page, so the sitemap must emit /games/<id>/ once.
    Under the old per-concept partition it would have advertised the same URL twice."""
    from core.sitemaps import GameSitemap

    a = _game(igdb_id=950, title_platform=['PS4'], defined_trophies={'bronze': 2})
    b = _game(igdb_id=None, title_platform=['PS5'], defined_trophies={'bronze': 2})
    _match(b.concept, 950)

    sm = GameSitemap()
    urls = [sm.location(o) for o in sm.items()]

    assert urls.count('/games/950/') == 1


def test_sitemap_routes_unmatched_and_conceptless_correctly():
    from core.sitemaps import GameSitemap

    stub_concept = ConceptFactory(concept_id='PP_SMAP')
    stub_game = GameFactory(concept=stub_concept, defined_trophies={'bronze': 1})
    lone = GameFactory(concept=None, defined_trophies={'bronze': 1})

    sm = GameSitemap()
    urls = {sm.location(o) for o in sm.items()}

    assert '/games/c/PP_SMAP/' in urls
    assert f'/games/{lone.np_communication_id}/' in urls


def test_sitemap_page_is_bounded_queries():
    """The window over the Case partition key is heavier SQL than the old concept partition; this
    pins that a sitemap page still resolves in a fixed number of queries (no per-row igdb walks --
    location() reads the select_related'd match)."""
    from django.db import connection
    from django.test.utils import CaptureQueriesContext
    from core.sitemaps import GameSitemap

    for i in range(6):
        _game(igdb_id=960 + i, defined_trophies={'bronze': 1})

    sm = GameSitemap()
    with CaptureQueriesContext(connection) as ctx:
        urls = [sm.location(o) for o in sm.items()]

    assert len(urls) >= 6
    assert len(ctx) == 1, f'sitemap page must be one query, used {len(ctx)}'


def test_untrusted_matches_do_not_merge_sitemap_partitions():
    """Two concepts with UNTRUSTED matches sharing an igdb id are still two c/ pages (trust gates
    the URL, so it must gate the partition identically) -- merging them would elect one row for
    two pages and silently drop the other from the sitemap."""
    from core.sitemaps import GameSitemap

    a = ConceptFactory(concept_id='PSN_UT_A')
    b = ConceptFactory(concept_id='PSN_UT_B')
    _match(a, 970, status='pending_review')
    _match(b, 970, status='pending_review')
    GameFactory(concept=a, defined_trophies={'bronze': 1})
    GameFactory(concept=b, defined_trophies={'bronze': 1})

    sm = GameSitemap()
    urls = {sm.location(o) for o in sm.items()}

    assert '/games/c/PSN_UT_A/' in urls and '/games/c/PSN_UT_B/' in urls


# --- nav + search wiring -------------------------------------------------------------------------

def test_the_subnav_rail_lights_on_both_new_urls():
    """The documented job_detail failure: a detail page's url_name never equals its rail item's,
    so without an override line the strip renders with nothing lit -- silently."""
    from core.hub_subnav import _URL_NAME_TO_SLUG_OVERRIDES

    assert _URL_NAME_TO_SLUG_OVERRIDES['game_page'] == ('browse', 'games')
    assert _URL_NAME_TO_SLUG_OVERRIDES['game_page_concept'] == ('browse', 'games')


def test_search_suggests_the_concept_game_page(client):
    """Search already dedupes to concepts; its destination is now the concept's own page --
    igdb URL when trusted, c/ URL for the unmatched tail."""
    from trophies.views.sync_views import SiteSuggestView

    matched = _game(igdb_id=980, title_name='Searchable Matched')
    matched.concept.unified_title = 'Searchable Matched'
    matched.concept.save()
    stub = ConceptFactory(concept_id='PP_SRCH', unified_title='Searchable Stubbed')
    GameFactory(concept=stub)

    view = SiteSuggestView()
    group = view._games('searchable')
    urls = {i['label']: i['url'] for i in group['items']}

    assert urls['Searchable Matched'] == '/games/980/'
    assert urls['Searchable Stubbed'] == '/games/c/PP_SRCH/'


# --- final-audit fixes, pinned ------------------------------------------------------------------

PAGE_TPL = None  # populated lazily; Path computed in the test to match the repo layout


def test_the_page_actually_loads_its_javascript():
    """The audit's H1: {% block scripts %} does not exist in base.html, so Django silently
    discarded the tag and the ENTIRE client layer -- tabs, switcher sync, URL state, bar fills --
    was dead in production while every test passed. Source-pinned so a block rename can never be
    silent again."""
    from pathlib import Path

    src = (Path(__file__).resolve().parents[2] / 'templates' / 'trophies' / 'game_page.html').read_text(encoding='utf-8')

    assert '{% block js_scripts %}' in src, 'base.html has no `scripts` block; only js_scripts renders'
    assert "js/game-page.js" in src
    assert "js/trophy-grid.js" in src, 'the shared grid driver (jump-to-pack menu) must load here too'


def test_template_js_and_view_agree_on_the_swap_target():
    """Three independent encodings of 'gp-viewport' (template id, JS constant, view's htmx.target
    check). A rename in one silently degrades the page to full reloads with zero test failures --
    this parity guard is the repo's answer to that class."""
    from pathlib import Path

    root = Path(__file__).resolve().parents[2]
    tpl = (root / 'templates' / 'trophies' / 'game_page.html').read_text(encoding='utf-8')
    js = (root / 'static' / 'js' / 'game-page.js').read_text(encoding='utf-8')
    view = (root / 'trophies' / 'views' / 'game_page_views.py').read_text(encoding='utf-8')

    assert 'id="gp-viewport"' in tpl
    assert "getElementById('gp-viewport')" in js
    assert "self.request.htmx.target == 'gp-viewport'" in view
    # And the concept tabs: VIEW_ORDER must match the chips' data-view attributes in order.
    import re
    chips = re.findall(r'data-view="(\w+)"(?=[^>]*role="tab")', tpl)
    assert "VIEW_ORDER = ['lists', 'ratings', 'about']" in js
    assert chips == ['lists', 'ratings', 'about'], chips


def test_the_host_is_the_first_trusted_list_not_an_untrusted_platform_winner(client):
    """The audit's H2: membership is trust-ungated by decision, but the HOST (title, canonical,
    concept furniture) must not be -- an untrusted or admin-rejected match whose PS5 list wins
    platform priority would title the page and point its canonical at a subset c/ page while the
    sitemap advertises the igdb URL."""
    trusted_ps4 = _game(igdb_id=991, title_platform=['PS4'])
    trusted_ps4.concept.unified_title = 'The Real Name'
    trusted_ps4.concept.save()
    untrusted_ps5 = _game(igdb_id=None, title_platform=['PS5'])
    _match(untrusted_ps5.concept, 991, status='rejected')
    TrophyFactory(game=trusted_ps4, trophy_id=1)
    TrophyFactory(game=untrusted_ps5, trophy_id=1)

    head = client.get('/games/991/').content.decode().split('</head>')[0]

    assert 'rel="canonical" href="http://testserver/games/991/"' in head, (
        'the untrusted host pointed the canonical at its c/ subset page'
    )
    assert 'The Real Name' in head


def test_rows_without_an_np_communication_id_are_excluded(client):
    """The audit's H4: np_communication_id is nullable/blankable; a blank row NoReverseMatch-500s
    the identity chip and a null one mints an unactivatable ?list=None chip. Same floor as the
    sitemap."""
    game = _game(igdb_id=992)
    broken = GameFactory(concept=game.concept, np_communication_id=None)
    TrophyFactory(game=game, trophy_id=1)

    content = client.get('/games/992/').content.decode()

    assert content.count('gp-lchip') >= 1
    assert '?list=None' not in content


def test_community_band_aggregates_across_the_list_set(client):
    """The audit's M3: 'Players' under a header that just said '2 trophy lists' must describe the
    game, not the host stack."""
    a = _game(igdb_id=993, title_platform=['PS5'], played_count=100, plats_earned_count=10)
    b = _game(igdb_id=None, title_platform=['PS4'], played_count=900, plats_earned_count=90)
    _match(b.concept, 993)
    TrophyFactory(game=a, trophy_id=1)

    content = client.get('/games/993/').content.decode()

    assert '1,000' in content, 'the header/Community numbers must sum every list'


def test_the_ratings_tab_is_active_here(client):
    """Phase 2 (slim-down): this page is the ONE active ratings host, replacing the slice-1
    read-only gate this test used to pin. An ELIGIBLE linked viewer (platinum earned on the host
    concept, the can_rate rule) gets the quick-rate CTA with its API-contract attrs, the compose
    dialog + shared controller ship, the tracked playtime reaches the modal's hint, and the
    About empty-state's flag CTA has its modal + JS on the page (the D7 machinery)."""
    import datetime

    from tests.factories import ConceptTrophyGroupFactory, EarnedTrophyFactory

    game = _game(igdb_id=994)
    plat = TrophyFactory(game=game, trophy_id=1, trophy_type='platinum')
    ConceptTrophyGroupFactory(concept=game.concept)
    viewer = ProfileFactory(is_linked=True)
    EarnedTrophyFactory(profile=viewer, trophy=plat, earned=True)
    ProfileGameFactory(profile=viewer, game=game, progress=100,
                       play_duration=datetime.timedelta(hours=42))
    client.force_login(viewer.user)

    content = client.get('/games/994/').content.decode()

    assert 'quick-rate-btn' in content, 'the eligible viewer must get the rate CTA'
    assert f'data-concept-id="{game.concept.id}"' in content
    assert 'id="gd-qr-modal"' in content
    assert 'js/quick-rate.js' in content and 'js/ratings-tab.js' in content
    assert 'Playtime: about <b>42</b>' in content, 'the playtime hint must reach the modal'
    assert 'data-flag-open' in content and 'id="game-flag-modal"' in content
    assert 'js/game-flag.js' in content
    assert 'concept_tabs_readonly' not in content

    # An anonymous viewer gets none of the write surfaces (can_rate gates them), and no flag JS.
    client.logout()
    anon = client.get('/games/994/').content.decode()
    assert 'quick-rate-btn' not in anon
    assert 'js/game-flag.js' not in anon


def test_the_breadcrumb_is_visible_not_just_structured_data(client):
    game = _game(igdb_id=995)
    game.concept.unified_title = 'Crumbed Game'
    game.concept.save()
    TrophyFactory(game=game, trophy_id=1)

    content = client.get('/games/995/').content.decode()

    assert 'aria-label="Breadcrumb"' in content or 'breadcrumbs' in content, (
        'the breadcrumb partial must render visibly, not only as jsonld'
    )


def test_the_hero_is_concept_level_with_no_list_furniture(client):
    """The hero adoption (Jeffrey's call): the List-detail hero's concept-level portion, WITHOUT
    the per-list pieces or modal-bound CTAs -- their JS doesn't ship here. ONE exception: the
    screenshot lightbox, which was extracted into a shared module both pages load, so its openers
    and dialog are expected. The second audit round
    proved the anonymous version of this test vacuous (4 of 5 banned markers could not render for
    an anonymous viewer of an unenriched game, while the REAL leak -- an inert blurb-report button
    plus three orphaned modals -- sailed through unbanned). So the fixture makes every ban
    reachable: a logged-in linked viewer, ANOTHER profile's public blurb (the report affordance's
    exact render condition on List detail), and PSN screenshots."""
    from tests.factories import (
        BadgeSeriesFactory, ConceptTrophyGroupFactory, GroupBadgeFactory, PlatformGroupFactory,
        ProfileTrophyGroupFactory, StageFactory, TrophyGroupFactory, UserConceptRatingFactory,
    )

    a = _game(igdb_id=996, title_platform=['PS4'])
    b = _game(igdb_id=None, title_platform=['PS5'])
    _match(b.concept, 996)
    TrophyFactory(game=a, trophy_id=1)
    # The enrichment goes on B's concept: both lists are trusted, so host election picks the
    # platform-priority winner (PS5 = b), and image_urls / community_tabs read the HOST concept.
    b.concept.media = [{'type': 'SCREENSHOT', 'url': 'https://img.example/shot1.jpg'}]
    b.concept.save()
    ConceptTrophyGroupFactory(concept=b.concept)  # blurbs render per-CTG; base group required
    UserConceptRatingFactory(
        profile=ProfileFactory(is_linked=True), concept=b.concept,
        blurb='A reachable quick take from someone else.',
    )
    # TWO badge series on the host concept: the band must link each medallion to ITS series
    # (a single series could not distinguish the audit-fixed markup from the old badges.0 row
    # link), and badges being present is what makes List detail's data-spine-open reachable.
    pg = PlatformGroupFactory(key='hero-ultra', platforms=['PS4', 'PS5'])
    for slug in ('hero-crash', 'hero-spyro'):
        GroupBadgeFactory(series=BadgeSeriesFactory(series_slug=slug), platform_group=pg)
        StageFactory(series_slug=slug).concepts.add(b.concept)
    viewer = ProfileFactory(is_linked=True)
    # A finished host list: makes the plat-card CTA, My Stats opener and progress readout all
    # reachable on List detail, so their bans below cannot be vacuous.
    ProfileGameFactory(profile=viewer, game=b, progress=100)
    ProfileTrophyGroupFactory(
        profile=viewer, trophy_group=TrophyGroupFactory(game=b), progress=100,
    )
    client.force_login(viewer.user)

    content = client.get('/games/996/').content.decode()

    assert 'gd-hero' in content and 'gd-hero__title' in content
    # Platform chips: the UNION in priority order, one per platform -- scoped to the hero's own
    # chip row so a stray gd-plat elsewhere on the page can't satisfy (or pollute) the check.
    import re
    chips_html = content.split('gd-hero__chips', 1)[1].split('</div>', 1)[0]
    chips = re.findall(r'<span class="gd-plat">(\w+)</span>', chips_html)
    assert chips == ['PS5', 'PS4'], chips
    # Screenshots open the SHARED lightbox (extracted from List detail at Jeffrey's ask): opener
    # buttons + the dialog + the shared script must all ship, or the thumbs are dead buttons.
    assert '<button type="button" class="gd-shots__thumb" data-shot="0"' in content
    assert 'id="gd-shot-modal"' in content
    assert 'js/shot-lightbox.js' in content
    assert 'A reachable quick take from someone else.' in content
    # Each badge medallion links its OWN series page (audit M3): the second series' href is the
    # one a badges.0-style row link would lose.
    assert '/hero-crash/' in content and '/hero-spyro/' in content
    # Since the slim-down made this page the ACTIVE ratings host, the ratings dialogs + the blurb
    # report affordance legitimately ship here -- they are positive pins now, not bans.
    for marker in [
        'data-blurb-report',        # blurb report button (another profile's blurb IS present)
        'id="gd-qr-modal"',
        'id="gd-blurb-report-modal"',
        'id="gd-guidelines-modal"',
    ]:
        assert marker in content, f'active-ratings piece missing: {marker}'
    # Every one of these WOULD render on List detail for this exact viewer + data; here each is a
    # dead CTA or an orphaned dialog -- genuinely list-level furniture.
    for marker in [
        'id="gd-versions-modal"',   # List detail's versions dialog
        'data-versions-open',
        'data-stats-open',          # My Stats modal opener
        'gd-btn--card',             # plat-card CTA (the viewer's finished list earns one THERE)
        'gd-prog',                  # per-list progress readout
        'data-spine-open',          # badge spine modal opener (badges ARE present)
        'game-flag-btn',            # hero flag/report button (the About CTA's modal ships, this id doesn't)
    ]:
        assert marker not in content, f'list-level hero piece leaked: {marker}'

    # And every banned marker DOES render on the HOST concept's List detail for this same viewer
    # + data: proves each ban is reachable, so the list above cannot rot into vacuousness again
    # (the first rewrite of this test dropped three bans without noticing -- audit finding). The
    # extraction must not have cost List detail its own lightbox either.
    list_content = client.get(f'/games/{b.np_communication_id}/').content.decode()
    for marker in [
        'data-blurb-report', 'id="gd-qr-modal"', 'id="gd-blurb-report-modal"',
        'id="gd-guidelines-modal"', 'id="gd-versions-modal"', 'data-versions-open',
        'data-stats-open', 'gd-btn--card', 'gd-prog', 'data-spine-open', 'game-flag-btn',
        'id="gd-shot-modal"', 'js/shot-lightbox.js',
    ]:
        assert marker in list_content, f'ban not proven reachable on List detail: {marker}'


def test_hero_facts_and_jumps_are_concept_true(client):
    """Audit pins for the un-guarded hero fixes: Released is the WORK's earliest date
    (concept.release_date), never the host list's platform date; and with a non-default ?list=
    selected, both hero jump anchors carry it so the no-JS path lands on the right stack."""
    from django.utils import timezone as djtz
    import datetime

    from trophies.services.igdb_service import IGDB_RELEASE_STATUS_RELEASE, PLAT_TO_IGDB_ID

    a = _game(igdb_id=1010, title_platform=['PS4'])
    b = _game(igdb_id=None, title_platform=['PS5'])
    _match(b.concept, 1010)
    TrophyFactory(game=a, trophy_id=1)
    TrophyFactory(game=b, trophy_id=1)
    # The work released in 2018; the HOST (PS5 = b) list's platform date is 2020. The hero
    # describes the work, so 2018 must win -- reverting to host_game.platform_release_date
    # renders 2020 and fails both assertions.
    b.concept.release_date = djtz.make_aware(datetime.datetime(2018, 4, 20))
    b.concept.save()
    match = b.concept.igdb_match
    match.igdb_ps_release_dates = [
        {'platform': PLAT_TO_IGDB_ID['PS5'], 'date': '2020-11-12',
         'status': IGDB_RELEASE_STATUS_RELEASE},
    ]
    match.igdb_summary = 'A summary, so the about teaser (and its jump link) renders.'
    match.save()
    # Non-zero players so the ratings jump headline renders.
    for g in (a, b):
        g.played_count = 10
        g.save(update_fields=['played_count'])

    content = client.get('/games/1010/').content.decode()

    assert 'datetime="2018-04-20"' in content
    assert '2020-11-12' not in content
    # Default list selected -> bare jump hrefs (no redundant list param).
    assert 'href="?view=about"' in content
    assert 'href="?view=ratings"' in content

    # Non-default list selected -> both jumps preserve it (audit L8).
    sel = client.get(f'/games/1010/?list={a.np_communication_id}').content.decode()
    assert f'href="?list={a.np_communication_id}&amp;view=about"' in sel
    assert f'href="?list={a.np_communication_id}&amp;view=ratings"' in sel


def test_old_ratings_and_about_deep_links_redirect_up(client):
    """Slim-down decision 4: List detail's old tabs WROTE ?view= into the address bar, so
    bookmarked deep links exist in the wild. After the tabs leave, /games/<np>/?view=ratings|about
    must land where the content went -- a 302 to the concept Game page's same view. The username
    segment and other params are dropped (ratings are concept-level); conceptless games have no
    Game page and fall through to Trophies."""
    a = _game(igdb_id=1014)
    TrophyFactory(game=a, trophy_id=1)

    resp = client.get(f'/games/{a.np_communication_id}/?view=ratings')
    assert resp.status_code == 302 and resp.url == '/games/1014/?view=ratings'
    resp = client.get(f'/games/{a.np_communication_id}/?view=about')
    assert resp.status_code == 302 and resp.url == '/games/1014/?view=about'

    # The authed profile-scoped variant redirects up too, shedding the username segment.
    viewer = ProfileFactory(is_linked=True)
    client.force_login(viewer.user)
    resp = client.get(f'/games/{a.np_communication_id}/{viewer.psn_username}/?view=ratings',
                      HTTP_CF_RAY='test')
    assert resp.status_code == 302 and resp.url == '/games/1014/?view=ratings'
    client.logout()

    # Views that still live here (or no view at all) render normally, with no extra hop.
    assert client.get(f'/games/{a.np_communication_id}/?view=trophies').status_code == 200
    assert client.get(f'/games/{a.np_communication_id}/').status_code == 200

    # A conceptless game has nowhere to send the bookmark -- it falls through to the page.
    lone = GameFactory(concept=None)
    TrophyFactory(game=lone, trophy_id=1)
    assert client.get(f'/games/{lone.np_communication_id}/?view=ratings').status_code == 200


def test_the_sticky_minibar_is_wired_like_list_details(client):
    """Jeffrey's call: the Game page carries List detail's sticky minibar (identity icon per tab +
    jump-to-pack select) instead of pinning the grid's jump menu. Pins the template contract (bar,
    sentinel pair, select, server-seeded data-mb-active) and the JS-side agreement -- a rename on
    either side silently leaves a bar that never pins or a select that never fills."""
    from pathlib import Path

    game = _game(igdb_id=1012)
    TrophyFactory(game=game, trophy_id=1)

    content = client.get('/games/1012/').content.decode()
    assert 'pp-minibar gd-minibar' in content
    assert 'data-sticky-sentinel="#gp-minibar-sentinel"' in content
    assert 'id="gp-minibar-sentinel"' in content
    assert 'data-minibar-groupjump' in content
    assert 'data-mb-active="lists"' in content

    about = client.get('/games/1012/?view=about').content.decode()
    assert 'data-mb-active="about"' in about, 'the bar must seed from the server-honored ?view='

    js = (Path(__file__).resolve().parents[2] / 'static' / 'js' / 'game-page.js').read_text(encoding='utf-8')
    assert 'StickyReveal.init()' in js, 'nothing pins the bar without the page-side init'
    assert 'data-minibar-groupjump' in js, 'nothing fills the jump select without the sync'
    assert 'mbActive' in js, 'nothing follows the active tab without the dataset write'
    assert 'data-minibar-listswitch' in js, 'nothing proxies the list switch without the wiring'

    # Single-list game: a one-option list switch is dead chrome, so it must not render at all.
    assert 'data-minibar-listswitch' not in content


def test_the_minibar_list_switch_mirrors_the_real_switcher(client):
    """Jeffrey's call: switch lists from the pinned bar. The select is server-rendered from
    switcher_entries -- the same source as the real chips -- with the SELECTED list following
    ?list=, so bar and switcher can never disagree at render time (JS proxies a pick to a real
    chip click from there)."""
    a = _game(igdb_id=1013, title_platform=['PS4'])
    b = _game(igdb_id=None, title_platform=['PS5'])
    _match(b.concept, 1013)
    TrophyFactory(game=a, trophy_id=1)
    TrophyFactory(game=b, trophy_id=1)

    content = client.get('/games/1013/').content.decode()
    assert content.count('data-minibar-listswitch') == 1
    assert f'<option value="{a.np_communication_id}"' in content
    assert f'<option value="{b.np_communication_id}" selected' in content, (
        'the platform-priority default (PS5 = b) must render selected'
    )
    assert '(PS4)' in content and '(PS5)' in content

    # A non-default ?list= flips which option is selected.
    sel = client.get(f'/games/1013/?list={a.np_communication_id}').content.decode()
    assert f'<option value="{a.np_communication_id}" selected' in sel
    assert f'<option value="{b.np_communication_id}" selected' not in sel


def test_view_param_is_honored_server_side(client):
    """The hero jump anchors are only real no-JS links if the server renders the named panel
    visible -- the audit found ?view=about landing on a page whose About panel shipped with the
    `hidden` attribute (game-page.js was the only thing that ever revealed it)."""
    import re

    game = _game(igdb_id=1011)
    TrophyFactory(game=game, trophy_id=1)

    def panel_tag(content, view):
        return re.search(r'<div[^>]*id="gp-view-' + view + r'"[^>]*>', content).group(0)

    def tab_tag(content, view):
        return re.search(r'<button[^>]*id="gp-tab-' + view + r'"[^>]*>', content).group(0)

    about = client.get('/games/1011/?view=about').content.decode()
    assert 'hidden' not in panel_tag(about, 'about')
    assert 'hidden' in panel_tag(about, 'lists')
    assert 'aria-selected="true"' in tab_tag(about, 'about')
    assert 'is-active' in tab_tag(about, 'about')
    assert 'aria-selected="false"' in tab_tag(about, 'lists')

    # Default and unknown values both fall back to Lists.
    for url in ('/games/1011/', '/games/1011/?view=bogus'):
        content = client.get(url).content.decode()
        assert 'hidden' not in panel_tag(content, 'lists')
        assert 'hidden' in panel_tag(content, 'about')
        assert 'aria-selected="true"' in tab_tag(content, 'lists')


def test_the_family_band_links_siblings_to_their_own_game_pages(client):
    """Family band (hero): siblings -- same GameFamily, DIFFERENT igdb id -- link to THEIR Game
    pages via game_page_url, so family hopping stays at the concept level. Progressive: a game
    with no family renders no band."""
    from trophies.models import GameFamily

    family = GameFamily.objects.create(igdb_id=424242, canonical_name='Crash Family')
    original = _game(igdb_id=997, title_platform=['PS4'])
    original.concept.family = family
    original.concept.unified_title = 'Crash Original'
    original.concept.save()
    remake_concept = ConceptFactory(unified_title='Crash Remade', family=family)
    _match(remake_concept, 998)
    GameFactory(concept=remake_concept, title_platform=['PS5'])
    stub_sibling = ConceptFactory(concept_id='PP_FAMSTUB', unified_title='Crash Stub', family=family)
    GameFactory(concept=stub_sibling, title_platform=['PS3'])
    TrophyFactory(game=original, trophy_id=1)

    content = client.get('/games/997/').content.decode()

    # A family member whose concept matches the SAME igdb id is 'other platforms' (the switcher's
    # territory), never family -- the band must exclude it or the page lists itself as its own
    # sibling on every split concept.
    same_igdb = ConceptFactory(unified_title='Crash Same-IGDB', family=family)
    # Untrusted on purpose: a trusted PS5 match would win host election and retitle the page,
    # which is correct page behavior but not what this test is about. Membership and the family
    # exclusion both key on the igdb id regardless of trust.
    _match(same_igdb, 997, status='pending')
    GameFactory(concept=same_igdb, title_platform=['PS5'])

    content = client.get('/games/997/').content.decode()

    assert 'gp-family' in content
    assert content.count('In the same family') == 1, (
        'the family label must appear exactly once: the hero band. A second occurrence means the '
        'About versions card is rendering its family section again (the about_hide_versions gate)'
    )
    assert 'href="/games/998/"' in content, 'a matched sibling must link its igdb page'
    assert 'href="/games/c/PP_FAMSTUB/"' in content, 'an unmatched sibling links its c/ page'
    assert 'Crash Remade' in content and 'Crash Stub' in content
    # Scoped to the band's own name spans: the sibling's game is legitimately elsewhere on the
    # page (it IS a member of the list set), it just must not appear as family.
    assert 'gp-family__name">Crash Same-IGDB' not in content

    # No family -> no band.
    lone = _game(igdb_id=999)
    TrophyFactory(game=lone, trophy_id=1)
    assert 'gp-family' not in client.get('/games/999/').content.decode()


def test_the_family_band_is_bounded_at_six_with_an_overflow_tally(client):
    """The band is a hero PEEK, not a directory (audit M5): a mega-family (LEGO, Ratalaika shovel
    stacks) must not stretch the header into a wall of covers. Six render; the rest collapse to
    the same '+N more' idiom the badge peek uses."""
    from trophies.models import GameFamily

    family = GameFamily.objects.create(igdb_id=525252, canonical_name='Mega Family')
    host = _game(igdb_id=1005, title_platform=['PS5'])
    host.concept.family = family
    host.concept.save()
    TrophyFactory(game=host, trophy_id=1)
    for i in range(8):
        sib = ConceptFactory(unified_title=f'Mega Sibling {i}', family=family)
        _match(sib, 2000 + i)
        GameFactory(concept=sib, title_platform=['PS4'])

    content = client.get('/games/1005/').content.decode()

    assert content.count('gp-family__item') == 6, 'the band must cap at six siblings'
    assert '+2 more' in content


def test_the_about_tab_hides_the_versions_card_here_but_not_on_list_detail(client):
    """Jeffrey's call: on THIS page "Other platforms" is the switcher's own set restated relative
    to the host, and "In the same family" is the hero's family band -- redundant twice over. On
    List detail the card stays: that page IS one version, so its relatives belong there. The gate
    covers the whole card, heading included (an empty "Versions & editions" shell is the classic
    half-gate bug)."""
    a = _game(igdb_id=1001, title_platform=['PS5'])
    b = _game(igdb_id=None, title_platform=['PS4'])
    _match(b.concept, 1001)
    TrophyFactory(game=a, trophy_id=1)
    TrophyFactory(game=b, trophy_id=1)

    game_page = client.get('/games/1001/').content.decode()
    assert 'Versions &amp; editions' not in game_page
    assert 'gd-vsec' not in game_page
    # BOTH audits caught the companion bug: with the card gated off, the About tab's empty state
    # was ALSO suppressed (its old condition required no other_versions, which a split-concept
    # page has by construction), leaving a zero-length panel. Unenriched fixture -> gd-empty.
    assert 'gd-empty' in game_page, (
        'an unenriched game page must show the About empty state, not a blank panel'
    )

    list_page = client.get(f'/games/{a.np_communication_id}/').content.decode()
    assert 'Versions &amp; editions' in list_page, 'List detail must keep its versions card'


def test_the_concept_fallback_page_keeps_the_versions_card(client):
    """The flag is igdb-pages-only (audit M1): on a /games/c/ page the About versions card is the
    ONLY surface linking untrusted same-igdb sibling concepts (the switcher can't reach them --
    membership groups by concept there). Blanket-hiding it orphaned those siblings entirely."""
    a = _game(title_platform=['PS5'])
    b = _game(igdb_id=None, title_platform=['PS4'])
    # Same UNTRUSTED igdb id on both: neither graduates, each keeps its own c/ page, and the only
    # bridge between them is _build_other_versions' same-igdb grouping inside the versions card.
    _match(a.concept, 7007, status='pending')
    _match(b.concept, 7007, status='pending')
    TrophyFactory(game=a, trophy_id=1)

    content = client.get(f'/games/c/{a.concept.concept_id}/', HTTP_CF_RAY='test').content.decode()

    assert 'Versions &amp; editions' in content, 'c/ pages must keep the versions card'
    assert f'/games/{b.np_communication_id}/' in content, (
        'the untrusted same-igdb sibling must stay reachable through it'
    )
