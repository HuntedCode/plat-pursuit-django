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
    progress, _plats = view._viewer_maps(viewer)

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
    progress, _plats = view._viewer_maps(viewer)

    assert view._default_list(progress).pk == ps5.pk


def test_anonymous_default_is_platform_priority_with_zero_viewer_queries():
    from django.db import connection
    from django.test.utils import CaptureQueriesContext

    view = _view_for('/')
    view.list_set = [None]  # unused by the anon branch

    with CaptureQueriesContext(connection) as ctx:
        progress, plats = view._viewer_maps(None)

    assert (progress, plats) == ({}, set())
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
    assert 'profilegame' not in sql.replace('trophies_profilegame', 'PG').lower() or 'trophies_profilegame' not in sql, \
        'anonymous render must not touch per-user tables'


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
