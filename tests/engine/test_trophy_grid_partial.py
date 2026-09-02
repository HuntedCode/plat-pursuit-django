"""The shared trophy grid: one component, two hosts (List detail + the concept Game page).

The contract is the point. The predecessor partials read the viewer's numbers from PAGE GLOBALS
(profile_group_pct, profile_earned...), which meant a second grid rendered in the same document
would silently show the first grid's percentages -- the exact bug a list switcher would have
shipped. These tests pin that every read comes through the include params, and that id_prefix
actually namespaces the DOM.
"""
from pathlib import Path

import pytest
from django.template.loader import render_to_string

pytestmark = pytest.mark.django_db

GRID_DIR = Path(__file__).resolve().parents[2] / 'templates' / 'trophies' / 'partials' / 'trophy_grid'


def _ctx(**over):
    trophy = {
        'trophy_id': 7, 'trophy_type': 'gold', 'trophy_name': 'Master of the Wind',
        'trophy_detail': 'Do the thing.', 'trophy_icon_url': 'https://psn/t7.png',
        'earn_rate': 0.123, 'trophy_earn_rate': '31.0', 'progress_target_value': None,
    }
    ctx = {
        'trophies': {'default': [trophy]},
        'groups': {'default': {'trophy_group_name': 'Base Game',
                               'trophy_group_icon_url': 'https://psn/g.png',
                               'defined_trophies': {'bronze': 0, 'silver': 0, 'gold': 1, 'platinum': 0}}},
        'earned': {7: {'earned': True, 'earned_date_time': None, 'progress': None}},
        'group_pct': {'default': 61},
        'group_totals': {'default': {'bronze': 0, 'silver': 0, 'gold': 1, 'platinum': 0}},
        'profile': object(),   # truthy gates viewer chrome; templates only truth-test it
        'show_group_nav': True,
        'id_prefix': '',
    }
    ctx.update(over)
    return ctx


def _render(**over):
    return render_to_string('trophies/partials/trophy_grid/grid.html', _ctx(**over))


def test_the_grid_renders_from_the_contract_alone():
    html = _render()

    assert 'Master of the Wind' in html
    assert 'is-earned' in html
    assert 'Base Game' in html
    assert '61%' in html
    assert 'gd-groupnav' in html


def test_viewer_numbers_come_from_the_params_not_page_globals():
    """THE regression this file exists for: render with poisoned page-global names alongside the
    contract params -- the globals must be invisible to the partial."""
    html = render_to_string('trophies/partials/trophy_grid/grid.html', {
        **_ctx(group_pct={'default': 61}),
        'profile_group_pct': {'default': 99},       # the old global name, poisoned
        'profile_earned': {},                        # poisoned: would un-earn the trophy
        'profile_group_totals': {'default': {'gold': 0}},
    })

    assert '61%' in html and '99%' not in html
    assert 'is-earned' in html, 'earned state must come from the `earned` param, not profile_earned'


def test_id_prefix_namespaces_every_dom_id():
    html = _render(id_prefix='gp-')

    assert 'id="gp-trophy-group-default"' in html
    assert 'data-gd-groupjump="gp-trophy-group-default"' in html
    assert 'href="#gp-trophy-group-default"' in html
    assert 'id="gp-trophy-7"' in html
    assert 'id="trophy-group-default"' not in html.replace('gp-trophy-group-default', '')


def test_empty_prefix_keeps_list_details_historical_group_ids():
    html = _render()

    assert 'id="trophy-group-default"' in html
    assert 'id="trophy-7"' in html


def test_anonymous_render_shows_no_viewer_chrome():
    html = _render(profile=None, earned={})

    assert '61%' not in html
    assert 'is-earned' not in html
    assert 'gd-trophy__got' not in html


def test_group_nav_respects_the_host_flag():
    assert 'gd-groupnav' not in _render(show_group_nav=False)
    assert 'gd-jumpmenu' not in _render(show_group_nav=False, **_many_groups(12))


def _many_groups(n):
    """Contract overrides for an n-group list (group ids g00, g01, ...)."""
    trophy = dict(_ctx()['trophies']['default'][0])
    ids = [f'g{i:02d}' for i in range(n)]
    return {
        'trophies': {g: [dict(trophy, trophy_id=i)] for i, g in enumerate(ids)},
        'groups': {g: {'trophy_group_name': f'Pack {g}', 'trophy_group_icon_url': f'https://psn/{g}.png',
                       'defined_trophies': {'bronze': 1, 'silver': 0, 'gold': 0, 'platinum': 0}} for g in ids},
        'earned': {}, 'group_totals': {g: {'bronze': 0, 'silver': 0, 'gold': 0, 'platinum': 0} for g in ids},
        'group_pct': {g: 5 * i for i, g in enumerate(ids)},
    }


def test_group_nav_stays_chips_at_the_threshold():
    """Eight groups still get the chip cloud -- the jump menu is for the many-DLC tail only."""
    html = _render(**_many_groups(8))

    assert html.count('gd-groupnav__chip') == 8
    assert 'gd-jumpmenu' not in html


def test_many_groups_collapse_to_the_jump_menu():
    """Nine-plus groups (Sea of Thieves class): the chip cloud becomes ONE compact <details>
    control -- filter input + a row per pack, each row a real anchor carrying data-gd-groupjump
    so List detail's smooth-jump delegate and minibar sync keep working unchanged."""
    html = _render(**_many_groups(9), id_prefix='gp-')

    assert 'data-gd-jumpmenu' in html
    assert 'gd-groupnav__chip' not in html
    assert html.count('gd-jumpmenu__row') == 9
    assert html.count('data-gd-groupjump') == 9
    assert 'href="#gp-trophy-group-g03"' in html          # rows are id_prefix-namespaced anchors
    assert 'data-gd-groupjump="gp-trophy-group-g03"' in html
    assert 'data-gd-jumpfilter' in html
    assert 'data-gd-jumpnone' in html and 'No packs match' in html   # the filter's empty state
    assert '9 packs' in html
    assert 'Pack g03' in html
    assert '15%' in html                                   # viewer % from the group_pct param

    # Anonymous render: rows stay, the viewer % column goes. (The first cut of this asserted
    # the row count against the PROFILED render above -- vacuous; the final audit caught it.)
    anon = _render(**_many_groups(9), profile=None)
    assert anon.count('gd-jumpmenu__row') == 9
    assert 'gd-jumpmenu__pct' not in anon


def test_empty_grid_renders_the_empty_state():
    html = _render(trophies={})

    assert 'No trophies match these filters' in html


def test_the_partials_read_no_page_global_names():
    """Source guard (the house shape): the shared component must never mention the old page-global
    names -- reintroducing one silently re-couples the grid to whichever page renders it first."""
    banned = ['profile_earned', 'profile_group_pct', 'profile_group_totals',
              'grouped_trophies', 'trophy_groups|', 'game.has_trophy_groups']
    for path in GRID_DIR.glob('*.html'):
        src = path.read_text(encoding='utf-8')
        # Strip template comments -- the contract DOCUMENTATION names the old globals on purpose.
        while '{% comment %}' in src:
            head, _, rest = src.partition('{% comment %}')
            _, _, tail = rest.partition('{% endcomment %}')
            src = head + tail
        for name in banned:
            assert name not in src, f'{path.name} reads page-global {name!r}'


def test_the_real_game_page_still_renders_the_grid_through_the_wrapper(client):
    """Wrapper wiring: List detail's #browse-results body now reaches the shared component; the
    page must render the same grid classes and its historical group anchors (id_prefix='')."""
    from tests.factories import GameFactory, TrophyFactory

    game = GameFactory(has_trophy_groups=True)
    TrophyFactory(game=game, trophy_id=1, trophy_group_id='default')

    response = client.get(f'/games/{game.np_communication_id}/')
    content = response.content.decode()

    assert response.status_code == 200
    assert 'gd-trophies' in content and 'gd-group' in content
    assert 'id="trophy-group-default"' in content, "List detail's historical anchor ids must survive"
    assert 'id="gd-count-data"' in content
    assert 'js/trophy-grid.js' in content, 'List detail must load the shared grid driver'
