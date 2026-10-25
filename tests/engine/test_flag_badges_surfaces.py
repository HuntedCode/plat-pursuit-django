"""Each one-list surface wears its list's own flag badges (owner, 2026-10-24), rendered from its real partial with a
flagged game. The rules themselves are pinned in test_flag_badges.py; the contract surfaces in
test_flag_badges_contracts.py."""
from types import SimpleNamespace

import pytest
from django.template.loader import render_to_string
from django.utils import timezone

from tests.factories import GameFactory, ProfileFactory
from trophies.models import TrophyGroup

pytestmark = pytest.mark.django_db

ONLINE = '<use href="#pp-gflag-online"/>'


def _flagged(**extra):
    return GameFactory(has_online_trophies=True, **extra)


def test_a_day_session_row():
    game = _flagged()
    session = SimpleNamespace(game=game, has_platinum=False, trophies=3, span_label='', tiers=[], trophy_list=[])
    html = render_to_string('trophies/partials/profile_detail/_activity_session.html',
                            {'s': session, 'profile': ProfileFactory(), 'sid': 1})
    assert ONLINE in html and 'pp-gflags--row' in html


def test_the_versions_list_other_platforms_row():
    game = _flagged()
    html = render_to_string('trophies/partials/game_detail/_version_sections.html',
                            {'other_versions': [game], 'family_versions': []})
    assert ONLINE in html


def test_a_family_row_reads_its_whole_games_keys_not_its_lists():
    """On the About tab a family row links the sibling's whole game, so it draws the keys `_build_family_versions`
    computed."""
    game = _flagged()
    row = {'concept': game.concept, 'game': game, 'flag_keys': ()}
    ctx = {'other_versions': [], 'family_versions': [row], 'family_game_pages': True}
    html = render_to_string('trophies/partials/game_detail/_version_sections.html', ctx)
    assert ONLINE not in html, "the row's own list is online, but its game is not"
    row['flag_keys'] = ('online',)
    assert ONLINE in render_to_string('trophies/partials/game_detail/_version_sections.html', ctx)


def test_a_recently_added_dlc_card_carries_its_lists_flags_but_not_buggy():
    game = _flagged(has_buggy_trophies=True)
    group = TrophyGroup.objects.create(game=game, trophy_group_id='001', trophy_group_name='Pack')
    html = render_to_string('trophies/partials/recently_added/dlc_card.html', {'trophy_group': group})
    assert ONLINE in html and '<use href="#pp-gflag-buggy"/>' not in html


def test_a_shareables_card_says_its_flags_in_its_label():
    game = _flagged()
    card = SimpleNamespace(trophy_group=SimpleNamespace(game=game), trophy_group_id=1, plat_defined=True)
    html = render_to_string('shareables/partials/plat_card_results.html', {'completions': [card], 'page_obj': None})
    assert ONLINE in html
    assert f'aria-label="Make a card for {game.title_name}, online trophies"' in html


def test_the_roadmap_header():
    game = _flagged()
    roadmap = SimpleNamespace(concept_trophy_group=SimpleNamespace(trophy_group_id='default', display_name='Base'),
                              contributors=lambda: [])
    html = render_to_string('trophies/partials/roadmap/detail_header.html', {'game': game, 'roadmap': roadmap})
    assert ONLINE in html


def test_a_calendar_day_card_keeps_its_link_name_and_says_the_flags_after_the_title():
    game = _flagged()
    plat = SimpleNamespace(game=game, clean=True, on=timezone.now())
    square = SimpleNamespace(day=plat.on.day, month=plat.on.month)
    html = render_to_string('challenges/partials/_calendar_day.html', {'plats': [plat], 'square': square})
    assert f'<span aria-hidden="true"><span class="pp-gflags' in html, 'hidden inside the link'
    assert ', online trophies</span>' in html, 'and said after the title'


def test_a_family_row_off_the_about_tab_links_one_list_and_wears_its_own():
    """On the Trophy List page a family row links the sibling's most-played LIST, so it wears that list's flags."""
    game = _flagged()
    row = {'concept': game.concept, 'game': game, 'flag_keys': ()}
    html = render_to_string('trophies/partials/game_detail/_version_sections.html',
                            {'other_versions': [], 'family_versions': [row], 'family_game_pages': False})
    assert ONLINE in html
