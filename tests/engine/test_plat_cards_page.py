"""The Plat Cards page (`/shareables/`).

Rebuilt 2026-08 from a 4-card wayfinder + a browse that listed platinum EarnedTrophy rows and rendered
every one of them in a single response. These pin the three things that rebuild was FOR: non-platinum
completions can be reached, the list is paginated, and the filters compose instead of resetting.
"""
import pytest

from tests.engine.test_plat_cards import _completed_game
from tests.factories import ProfileFactory

pytestmark = pytest.mark.django_db

URL = '/shareables/'


def _hunter():
    """A LINKED profile -- _RequireLinkedProfileMixin bounces anyone else to the PSN linking flow, so
    an unlinked fixture makes every assertion below run against an empty redirect body."""
    return ProfileFactory(is_linked=True)


def _get(client, profile, **params):
    client.force_login(profile.user)
    resp = client.get(URL, params)
    assert resp.status_code == 200, f'expected the page, got {resp.status_code} -> {resp.get("Location", "")}'
    return resp


def test_a_100_percent_game_is_reachable_on_the_page(client):
    """The whole point. The old page listed platinum EarnedTrophy rows, so a game with no platinum had
    no row to list and its card was unreachable no matter what the API supported."""
    profile = _hunter()
    game, _, _ = _completed_game(profile, with_platinum=False, name='Firewatch')

    content = _get(client, profile).content.decode()

    assert 'Firewatch' in content
    assert 'pcard--full' in content, 'it should be marked as the 100% variant'


def test_both_variants_share_the_grid(client):
    profile = _hunter()
    _completed_game(profile, with_platinum=True, name='Bloodborne')
    _completed_game(profile, with_platinum=False, name='Firewatch')

    content = _get(client, profile).content.decode()

    assert 'Bloodborne' in content and 'Firewatch' in content
    assert content.count('data-card-open') == 2


@pytest.mark.parametrize('variant,shown,hidden', [
    ('platinum', 'Bloodborne', 'Firewatch'),
    ('full', 'Firewatch', 'Bloodborne'),
])
def test_the_variant_filter_narrows_the_grid(client, variant, shown, hidden):
    profile = _hunter()
    _completed_game(profile, with_platinum=True, name='Bloodborne')
    _completed_game(profile, with_platinum=False, name='Firewatch')

    content = _get(client, profile, variant=variant).content.decode()

    assert shown in content and hidden not in content


def test_the_variant_filter_composes_with_search(client):
    """It's a segmented FILTER, not a view island -- switching type must not drop the active search."""
    profile = _hunter()
    _completed_game(profile, with_platinum=True, name='Blood Omen')
    _completed_game(profile, with_platinum=True, name='Bloodborne')
    _completed_game(profile, with_platinum=False, name='Blood Money')

    content = _get(client, profile, variant='platinum', query='Blood').content.decode()

    assert 'Blood Omen' in content and 'Bloodborne' in content
    assert 'Blood Money' not in content          # right search, wrong variant


def test_shovelware_is_hidden_until_asked_for(client):
    """These are the hunter's OWN completions; the asset-flip platinums are the ones they least want
    to scroll past looking for a game they care about."""
    from trophies.models import Game

    profile = _hunter()
    game, _, _ = _completed_game(profile, with_platinum=True, name='Sausage Sports Club')
    Game.objects.filter(pk=game.pk).update(shovelware_status='auto_flagged')

    assert 'Sausage Sports Club' not in _get(client, profile).content.decode()
    assert 'Sausage Sports Club' in _get(client, profile, show_shovelware='1').content.decode()


def test_the_list_is_paginated(client):
    """The old page rendered EVERY platinum in one response -- 800 rows on load for a serious hunter,
    and adding non-platinum completions only grows that."""
    profile = _hunter()
    for i in range(30):
        _completed_game(profile, with_platinum=True, name=f'Game {i:02d}')

    resp = _get(client, profile)

    assert resp.context['paginator'].count == 30
    assert len(resp.context['completions']) == 24        # PlatCardsView.paginate_by
    assert resp.context['page_obj'].has_next()


def test_htmx_and_xhr_get_the_grid_only(client):
    """HTMX filter swaps and the InfiniteScroller's ?page fetches both need the rows partial -- the
    scroller sends X-Requested-With, not HX-Request, and would otherwise append a whole page."""
    profile = _hunter()
    _completed_game(profile, with_platinum=True, name='Bloodborne')
    client.force_login(profile.user)

    for header in ({'HTTP_HX_REQUEST': 'true'}, {'HTTP_X_REQUESTED_WITH': 'XMLHttpRequest'}):
        content = client.get(URL, **header).content.decode()
        assert 'items-grid' in content
        assert '<html' not in content, f'{header} got the full page'


def test_header_stats_describe_the_career_not_the_filter(client):
    profile = _hunter()
    for _ in range(3):
        _completed_game(profile, with_platinum=True)
    _completed_game(profile, with_platinum=False)

    ctx = _get(client, profile, variant='full').context

    assert ctx['total_platinums'] == 3          # unchanged by the active filter
    assert ctx['total_completions'] == 1


def test_the_page_renders_for_a_hunter_with_nothing(client):
    profile = _hunter()

    resp = _get(client, profile)

    assert resp.status_code == 200
    assert 'No completions yet' in resp.content.decode()


def test_only_the_curated_grounds_reach_the_picker(client):
    """The old page offered ~105 site gradients while the endpoint accepted 6 and silently fell back,
    so 99 of them previewed one card and downloaded another."""
    from trophies.themes import PLAT_CARD_THEME_KEYS

    profile = _hunter()
    _completed_game(profile, with_platinum=True)

    ctx = _get(client, profile).context

    assert [key for key, _ in ctx['card_themes']] == PLAT_CARD_THEME_KEYS
    assert set(ctx['card_theme_js']) == set(PLAT_CARD_THEME_KEYS)


def test_the_old_browse_path_redirects_here(client):
    """Platinum-earned notifications already in the wild deep-link /shareables/platinums/?et=<id>."""
    profile = _hunter()
    client.force_login(profile.user)

    resp = client.get('/shareables/platinums/?et=123')

    assert resp.status_code == 302
    assert resp['Location'].startswith(URL) and 'et=123' in resp['Location']


def test_query_count_does_not_grow_with_the_page(django_assert_max_num_queries, client):
    """One query for the grid however many cards it draws: the cover comes through the select_related
    concept/igdb_match and the variant through the `plat_defined` annotation."""
    profile = _hunter()
    for _ in range(24):
        _completed_game(profile, with_platinum=True)
    client.force_login(profile.user)
    client.get(URL)                                       # warm session/auth

    with django_assert_max_num_queries(14):
        assert client.get(URL).status_code == 200
