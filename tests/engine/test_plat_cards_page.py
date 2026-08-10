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
    """The old page offered ~105 site gradients while the endpoint accepted a handful and silently
    fell back, so most of them previewed one card and downloaded another.

    Asserts the RENDERED radios, not just the context: a context-only check passes even if the
    template drops its `is_game_art` guard (offering an art ground with no image) or stops marking one
    checked."""
    import re
    from trophies.themes import PLAT_CARD_THEME_KEYS

    profile = _hunter()
    _completed_game(profile, with_platinum=True)

    resp = _get(client, profile)
    content = resp.content.decode()
    rendered = re.findall(r'name="pc-theme" value="([^"]+)"', content)
    fixed = [k for k, t in resp.context['card_themes'] if not t.get('is_game_art')]

    assert rendered == fixed, 'the picker must render exactly the FIXED grounds; art is added per card'
    assert 'ppArt' not in rendered, 'the art ground has no image until a card is opened'
    assert content.count('checked') >= 1, 'one ground must start selected'
    assert set(resp.context['card_theme_js']) == set(PLAT_CARD_THEME_KEYS)


def test_the_share_page_uses_the_rebuilt_quick_rate_modal(client):
    """The page shipped with `rate_before_download_modal.html` -- a pre-rebuild DaisyUI form with NO
    blurb field, so the card rendered a quick take the only form that could set it never offered. It now
    composes the SAME modal as the Game Detail Ratings tab, which means the two rating surfaces cannot
    drift, and the guidelines sheet comes with it because the notice links there."""
    profile = _hunter()
    _completed_game(profile, with_platinum=True)

    content = _get(client, profile).content.decode()

    assert 'id="gd-qr-modal"' in content, 'the rebuilt quick-rate modal must be composed here'
    assert 'id="gd-guidelines-modal"' in content, 'its notice links to the sheet'
    assert 'rate-before-download-modal' not in content, 'the legacy modal must be gone'
    # The whole point of the swap: a blurb field, on the site's own classes.
    assert 'data-gd-qr-blurb' in content and 'gd-qr__area' in content
    assert 'range-warning' not in content, 'no legacy DaisyUI rating controls'


def test_the_old_browse_path_redirects_here(client):
    """Platinum-earned notifications already in the wild deep-link /shareables/platinums/?et=<id>."""
    profile = _hunter()
    client.force_login(profile.user)

    resp = client.get('/shareables/platinums/?et=123')

    assert resp.status_code == 302
    assert resp['Location'].startswith(URL) and 'et=123' in resp['Location']


def test_query_count_does_not_grow_with_the_page(client):
    """Two sizes, same count -- a single size against a fixed budget says nothing about growth.

    One query for the grid however many cards it draws: the cover comes through the select_related
    concept/igdb_match and the variant through the `plat_defined` annotation."""
    from django.db import connection
    from django.test.utils import CaptureQueriesContext

    def _measure(rows):
        profile = _hunter()
        for _ in range(rows):
            _completed_game(profile, with_platinum=True)
        client.force_login(profile.user)
        client.get(URL)                                   # warm session/auth
        with CaptureQueriesContext(connection) as ctx:
            assert client.get(URL).status_code == 200
        return len(ctx)

    small, full = _measure(3), _measure(24)

    assert small == full, f'query count grew with the page: {small} -> {full}'
    assert full <= 14, f'{full} queries to draw one page'


def test_share_modal_hosts_its_own_toast_container(client):
    """A toast fired while the modal is open renders BEHIND its backdrop without this host.

    ToastManager only escapes to the top layer when it finds a `.modal-toast-container` inside the open
    dialog and can showPopover() it, so both the class and the popover attribute are load-bearing."""
    profile = _hunter()
    _completed_game(profile, with_platinum=True)
    client.force_login(profile.user)

    html = client.get(URL).content.decode()

    assert 'modal-toast-container' in html
    assert 'popover="manual"' in html


def test_download_button_carries_all_three_states(client):
    """Idle, busy and done glyphs all ship in the markup; CSS picks one.

    The busy state is the load-bearing one: the PNG render is slow, so a button that only goes
    `disabled` reads as a dead click."""
    profile = _hunter()
    _completed_game(profile, with_platinum=True)
    client.force_login(profile.user)

    html = client.get(URL).content.decode()

    for glyph in ('pc-btn__i--idle', 'pc-btn__i--busy', 'pc-btn__i--done'):
        assert glyph in html, f'{glyph} missing from the download button'
