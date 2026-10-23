"""The Game page shows a game flag only when EVERY trophy list for it carries that flag (owner, 2026-10-10).

A flag one list has and another does not belongs to that list, and its own Trophy List page shows it. Naming it for
the whole game would tell a hunter on the good list that their game is broken. The Game page reuses the Trophy List
page's `_game_flags.html`, fed a `SharedListFlags`, and draws no "No Known Issues" chip, because an empty shared set
does not mean no list has an issue: the row renders only when a real flag is shared (`has_chips`).
"""
import pytest
from django.template.loader import render_to_string

from tests.factories import ConceptFactory, GameFactory
from trophies.models import SHOVELWARE_FLAGGED_STATUSES, IGDBMatch
from trophies.views.game_page_views import shared_list_flags

pytestmark = pytest.mark.django_db

SHOVEL = sorted(SHOVELWARE_FLAGGED_STATUSES)[0]
CF = {'HTTP_CF_RAY': '8f0000000000abcd-LHR'}


def _page(*lists, igdb_id=777_001):
    concept = ConceptFactory()
    IGDBMatch.objects.create(concept=concept, igdb_id=igdb_id, status='accepted')
    games = [GameFactory(concept=concept, **over) for over in lists]
    return '/games/%d/' % igdb_id, games


def _header(client, url):
    """The hero's head (platforms, title, facts, flags), from its start to the end of the hero."""
    html = client.get(url, **CF).content.decode()
    start = html.index('gp-hero__head')
    return html[start:html.index('</section>', start)]


# ── the rule ─────────────────────────────────────────────────────────────────────────────────────

def test_a_flag_holds_only_when_every_list_has_it():
    a = GameFactory(is_delisted=True, has_online_trophies=True, has_buggy_trophies=True, shovelware_status=SHOVEL)
    b = GameFactory(is_delisted=True, has_online_trophies=False, has_buggy_trophies=True)
    flags = shared_list_flags([a, b])
    assert flags.is_delisted is True
    assert flags.has_buggy_trophies is True
    assert flags.has_online_trophies is False
    assert flags.is_shovelware is False


def test_unobtainable_needs_every_list_unobtainable():
    gone = GameFactory(is_obtainable=False)
    still = GameFactory(is_obtainable=True)
    assert shared_list_flags([gone, still]).is_obtainable is True
    assert shared_list_flags([gone, GameFactory(is_obtainable=False)]).is_obtainable is False


def test_nothing_shared_means_nothing_to_draw():
    flags = shared_list_flags([GameFactory(is_delisted=True), GameFactory()])
    assert flags.has_chips is False
    assert shared_list_flags([]) is None


def test_shovelware_alone_draws_no_flags_row():
    """It rides the platform row; the flags row must not render around nothing."""
    shovel = shared_list_flags([GameFactory(shovelware_status=SHOVEL), GameFactory(shovelware_status=SHOVEL)])
    assert shovel.is_shovelware is True and shovel.has_chips is False


# ── the page ─────────────────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize('field, value, label', [
    ('is_delisted', True, 'Delisted'),
    ('is_obtainable', False, 'Unobtainable'),
    ('has_online_trophies', True, 'Online Trophies'),
    ('has_buggy_trophies', True, 'Buggy Trophies'),
])
def test_each_flag_shows_when_every_list_has_it_and_not_when_one_does(client, field, value, label):
    shared, _ = _page({field: value, 'title_platform': ['PS4']}, {field: value, 'title_platform': ['PS5']},
                      igdb_id=777_100 + len(label))
    split, _ = _page({field: value, 'title_platform': ['PS4']}, {'title_platform': ['PS5']},
                     igdb_id=777_200 + len(label))
    assert label in _header(client, shared)
    assert label not in _header(client, split)


def test_every_list_delisted_shows_the_chip(client):
    url, _ = _page({'is_delisted': True, 'title_platform': ['PS4']}, {'is_delisted': True, 'title_platform': ['PS5']})
    header = _header(client, url)
    assert 'Delisted' in header and 'bd-chip bd-chip--error' in header
    assert 'No Known Issues' not in header


def test_one_list_delisted_shows_nothing_and_never_claims_no_issues(client):
    url, _ = _page({'is_delisted': True, 'title_platform': ['PS4']}, {'title_platform': ['PS5']})
    header = _header(client, url)
    assert 'Delisted' not in header
    assert 'No Known Issues' not in header


def test_every_list_shovelware_puts_the_chip_in_the_platform_row(client):
    url, _ = _page({'shovelware_status': SHOVEL, 'title_platform': ['PS4']},
                   {'shovelware_status': SHOVEL, 'title_platform': ['PS5']}, igdb_id=777_002)
    header = _header(client, url)
    chips = header[:header.index('gd-hero__title')]
    assert 'gd-plat gd-plat--warn' in chips and 'Shovelware' in chips
    # Shovelware is the only shared flag, so the flags row must not render at all: neither an empty row nor the
    # partial's "No Known Issues" beside a Shovelware chip.
    assert 'No Known Issues' not in header and 'flex flex-row flex-wrap gap-1.5' not in header


def test_the_shared_flags_render_through_the_list_pages_own_partial():
    """`SharedListFlags` stands in for a Game in `_game_flags.html`, whose behaviour for the list page is unchanged."""
    flags = shared_list_flags([GameFactory(is_delisted=True), GameFactory(is_delisted=True)])
    html = render_to_string('trophies/partials/_game_flags.html', {'game': flags, 'variant': 'full'})
    assert 'Delisted' in html and 'No Known Issues' not in html
    clean = render_to_string('trophies/partials/_game_flags.html', {'game': GameFactory(), 'variant': 'full'})
    assert 'No Known Issues' in clean
