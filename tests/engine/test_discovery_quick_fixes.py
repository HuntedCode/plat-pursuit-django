"""The Discovery review's quick fixes (`fix/discovery-quick-fixes`): docs/design/reviews/discovery.md.

DS-01 no hidden platform default, DS-05 Trending first, DS-03 the franchise / company grid cannot widen the
page, DS-10 labelled rates on phones, DS-12 the phone search button's label, DS-13 a list named after its
game, DS-14 "6,000 XP".
"""
import re
from pathlib import Path

import pytest
from django.conf import settings as dj_settings
from django.urls import reverse

from tests.factories import ConceptFactory, GameFactory

pytestmark = pytest.mark.django_db

CSS = Path(dj_settings.BASE_DIR) / 'static' / 'css' / 'components'


def _top_level_css(name):
    """The sheet with comments and every @media block removed: the rules that apply at EVERY width."""
    css = re.sub(r'/\*.*?\*/', '', (CSS / name).read_text(encoding='utf-8'), flags=re.S)
    out, i = [], 0
    while i < len(css):
        if css.startswith('@media', i):
            depth, j = 0, css.index('{', i)
            while True:
                depth += {'{': 1, '}': -1}.get(css[j], 0)
                j += 1
                if depth == 0:
                    break
            i = j
        else:
            out.append(css[i]); i += 1
    return ''.join(out)


def _rule(css, selector):
    match = re.search(re.escape(selector) + r'\s*\{([^}]*)\}', css)
    assert match, f'no top-level rule for {selector}'
    return match.group(1)


def _precollapse(panel_id):
    """The drawer_precollapse script's lookup for `panel_id`, as rendered: escapejs writes '-' as a backslash +
    'u002D' (built with chr(92) so no editor or heredoc can quietly unescape it back to a hyphen)."""
    return "getElementById('" + panel_id.replace('-', chr(92) + 'u002D') + "')"


def _card_order(content, *titles):
    return sorted(titles, key=lambda t: content.index(t))


def _game(title, platforms, **kw):
    """Browse Games cards show the CONCEPT's title, so name the concept too."""
    return GameFactory(concept=ConceptFactory(unified_title=title), title_name=title, title_platform=platforms, **kw)


# ── DS-01 / DS-05: Browse Games' defaults ─────────────────────────────────────────────────────────────

def test_the_same_search_finds_the_same_games_from_a_link_and_from_the_page(client):
    """The page's own box submits the form as rendered. With a hidden PS4 + PS5 default it carried those
    platforms and a PS3 game vanished from the answer; a link (`?query=`) did not. Both now agree."""
    _game('Ratchet Old Gen', ['PS3'])
    _game('Ratchet New Gen', ['PS5'])

    from_link = client.get(reverse('games_list'), {'query': 'Ratchet'}).content.decode()
    bare = client.get(reverse('games_list')).content.decode()

    assert 'Ratchet Old Gen' in from_link and 'Ratchet New Gen' in from_link
    assert 'Ratchet Old Gen' in bare, 'the bare hub still hides older platforms'
    assert not re.search(r'name="platform" value="[^"]+"\s+checked', bare), (
        'a platform box renders ticked, so the page\'s own search submits a filter the hunter never chose'
    )


def test_browse_games_opens_on_trending(client):
    """Alphabetical put "0000" and other numeral-titled shovelware first (DS-05)."""
    _game('000 Shovel', ['PS5'], monthly_earners_count=1)
    _game('Zeta Hit', ['PS5'], monthly_earners_count=900)

    content = client.get(reverse('games_list')).content.decode()

    assert _card_order(content, '000 Shovel', 'Zeta Hit') == ['Zeta Hit', '000 Shovel']
    assert re.search(r'<option value="trending"\s+selected>', content), 'the sort control does not say Trending'


def test_a_chosen_sort_still_wins(client):
    _game('000 Shovel', ['PS5'], monthly_earners_count=1)
    _game('Zeta Hit', ['PS5'], monthly_earners_count=900)

    content = client.get(reverse('games_list'), {'sort': 'alpha'}).content.decode()

    assert _card_order(content, '000 Shovel', 'Zeta Hit') == ['000 Shovel', 'Zeta Hit']
    assert re.search(r'<option value="alpha"\s+selected>', content)
    assert not re.search(r'<option value="trending"\s+selected>', content)


# ── DS-03: the group grid cannot widen the page ───────────────────────────────────────────────────────

def test_the_group_grid_tracks_cannot_grow_past_their_column():
    """A bare `1fr` track grows to its content's min-content width, and a version title is nowrap, so one
    long title widened the franchise and company pages to 399px and 503px on a 375px phone."""
    css = re.sub(r'/\*.*?\*/', '', (CSS / 'group-card.css').read_text(encoding='utf-8'), flags=re.S)
    tracks = re.findall(r'\.fgroup-grid\s*\{[^}]*grid-template-columns:\s*([^;]+);', css)

    assert len(tracks) == 2, tracks   # the base rule and the 1280px rule
    for value in tracks:
        assert re.fullmatch(r'(minmax\(0,\s*1fr\)\s*)+', value.strip()), f'a bare track came back: {value}'


# ── DS-10: labelled rates on phones ───────────────────────────────────────────────────────────────────

def test_the_rate_labels_show_at_every_width():
    lbl = _rule(_top_level_css('game-detail.css'), '.gd-trate__lbl')

    assert 'display: none' not in lbl.replace('  ', ' '), 'phones are back to two unlabelled percentages'


# ── DS-12: the phone search button ────────────────────────────────────────────────────────────────────

def test_the_phone_search_button_names_what_it_searches(client):
    content = client.get(reverse('games_list')).content.decode()
    button = re.search(r'<button[^>]*id="navbar-sync-search-btn"[^>]*>', content).group(0)

    assert 'aria-label="Search games, badges, jobs, game lists and hunters"' in button
    assert 'PSN profiles' not in button


# ── DS-13: a list named after its game ────────────────────────────────────────────────────────────────

def _crumbs(client, game):
    resp = client.get(reverse('game_detail', kwargs={'np_communication_id': game.np_communication_id}))
    assert resp.status_code == 200
    return [c['text'] for c in resp.context['breadcrumb']]


def test_a_list_named_after_its_game_is_named_by_its_platform(client):
    game = GameFactory(concept=ConceptFactory(unified_title='Jak 3'), title_name='Jak 3', title_platform=['PS4'])

    assert _crumbs(client, game)[-2:] == ['Jak 3', 'PS4 trophy list']


def test_the_match_ignores_case_and_spacing(client):
    game = GameFactory(concept=ConceptFactory(unified_title='Jak 3'), title_name=' JAK 3 ', title_platform=['PS4'])

    assert _crumbs(client, game)[-1] == 'PS4 trophy list'


def test_a_list_with_its_own_name_keeps_it(client):
    game = GameFactory(concept=ConceptFactory(unified_title='Ratchet & Clank'),
                       title_name='Ratchet & Clank - (PS3/PSVITA)', title_platform=['PS3', 'PSVITA'])

    assert _crumbs(client, game)[-2:] == ['Ratchet & Clank', 'Ratchet & Clank - (PS3/PSVITA)']


def test_a_platform_chip_shows_but_does_not_open_the_drawer(client):
    """Saved browse defaults usually carry a platform. Its chip must show, but if it opened the filter drawer
    the drawer would open on every landing: only real content filters do."""
    platform_only = client.get(reverse('games_list'), {'platform': 'PS5'}).content.decode()
    with_filter = client.get(reverse('games_list'), {'platform': 'PS5', 'in_badge': 'on'}).content.decode()
    precollapse = _precollapse('gbrowse-advanced')

    assert 'aria-label="Remove filter: PS5"' in platform_only
    assert precollapse in platform_only, 'a platform alone opened the drawer'
    assert precollapse not in with_filter, 'a content filter no longer opens the drawer'


def test_clear_all_drops_the_platform_too():
    from django.test import RequestFactory
    from trophies.forms import GameSearchForm
    from trophies.views.browse_helpers import get_active_filter_chips

    req = RequestFactory().get('/games/', {'platform': ['PS5', 'PS3'], 'in_badge': 'on'})
    counted = get_active_filter_chips(req, GameSearchForm(req.GET))
    skipping = get_active_filter_chips(req, GameSearchForm(req.GET), drawer_skip=('platform', 'regions'))

    assert {'PS5', 'PS3', 'In a badge'} == {c['label'] for c in counted['filter_chips']}
    assert counted['filter_chips'] == skipping['filter_chips'], 'drawer_skip must not hide a chip'
    assert counted['filter_open_count'] == 3      # tag detail / trophy lists count every chip
    assert skipping['filter_open_count'] == 1     # Browse Games: only the content filter opens the drawer
    assert 'platform' not in counted['filter_clear_url']


def test_a_list_with_no_platforms_keeps_its_name(client):
    """Naming it by platform would say "Unknown trophy list" (Game.platforms_display), in the crumb and in the
    BreadcrumbList schema search engines read."""
    game = GameFactory(concept=ConceptFactory(unified_title='Jak 3'), title_name='Jak 3', title_platform=[])

    assert _crumbs(client, game)[-1] == 'Jak 3'


def test_the_empty_state_reset_clears_every_filter(client):
    """It used to link `?platform=PS5&platform=PS4`, bringing the hidden default back. `?page=1`, not a bare
    URL, so a signed-in hunter's saved-defaults redirect cannot turn "Reset" into "your saved filters"."""
    content = client.get(reverse('games_list'), {'query': 'zzqxv-no-such-game'}).content.decode()
    reset = re.search(r'<a href="([^"]+)" class="pp-gcard-empty__reset"', content)

    assert reset, 'the empty state lost its reset link'
    assert reset.group(1) == reverse('games_list') + '?page=1'


@pytest.mark.parametrize('params, opens', [
    ({'regions': 'EU'}, False),                       # Browse Games skips regions, like its script
    ({'platform': 'PS5', 'regions': 'EU'}, False),
    ({'regions': 'EU', 'in_badge': 'on'}, True),
])
def test_browse_games_drawer_agrees_with_its_script(client, params, opens):
    """The server's pre-collapse and the page's filterPanel `skip` must make the same call, or the drawer
    renders one way and flips after paint (the layout jump drawer_precollapse exists to stop)."""
    content = client.get(reverse('games_list'), params).content.decode()

    assert (_precollapse('gbrowse-advanced') not in content) is opens


def test_trophy_lists_counts_platform_like_its_script(client):
    """Trophy Lists' script counts platform (nothing pre-fills it there), so the server must open the drawer for it."""
    content = client.get(reverse('trophy_lists'), {'platform': 'PS5'}).content.decode()

    assert _precollapse('tlb-advanced') not in content, 'the server pre-collapsed what the script opens'
    assert 'aria-label="Remove filter: PS5"' in content
