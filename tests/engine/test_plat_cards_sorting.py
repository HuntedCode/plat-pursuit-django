"""Ordering, pagination coupling, and the `?art=` parameter on the Plat Cards page.

Split out from `test_plat_cards_page.py` because these all guard mechanisms whose failure is invisible
in a single request: a lost tiebreak only shows up as infinite scroll skipping a card two pages down, a
`paginate_by` change only shows up as duplicated rows, and a bad `?art=` index only shows up as the
wrong image inside a PNG.
"""
import re

import pytest
from django.utils import timezone

from tests.engine.test_plat_cards import _completed_game
from tests.factories import ProfileFactory

pytestmark = pytest.mark.django_db

URL = '/shareables/'


def _hunter():
    return ProfileFactory(is_linked=True)


def _names(client, profile, **params):
    client.force_login(profile.user)
    resp = client.get(URL, params)
    assert resp.status_code == 200
    return [c.trophy_group.game.title_name for c in resp.context['completions']]


def _at(profile, name, when):
    """A completion whose group was finished at `when` (None = PSN gave no timestamp)."""
    _, _, standing = _completed_game(profile, with_platinum=True, name=name)
    standing.last_trophy_at = when
    standing.save(update_fields=['last_trophy_at'])
    return standing


# ── Ordering ──────────────────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize('sort,expected', [
    ('recent', ['Newest', 'Oldest', 'Undated']),
    ('oldest', ['Oldest', 'Newest', 'Undated']),
])
def test_sort_order_including_nulls(client, sort, expected):
    """`last_trophy_at` is nullable and the grid renders "Date unknown" for those, so NULLs are a real
    population. Postgres defaults DESC to NULLS FIRST, which parked every undated completion at the TOP
    of "Most recent" -- while ASC put them at the bottom, i.e. the newest position. Both directions were
    wrong at once. Undated sorts last either way now, matching the ordinal ladders."""
    profile = _hunter()
    now = timezone.now()
    _at(profile, 'Newest', now)
    _at(profile, 'Oldest', now - timezone.timedelta(days=100))
    _at(profile, 'Undated', None)

    assert _names(client, profile, sort=sort) == expected


@pytest.mark.parametrize('sort,expected', [
    ('name', ['Alpha', 'beta', 'Gamma']),
    ('name_desc', ['Gamma', 'beta', 'Alpha']),
])
def test_name_sort_is_case_insensitive(client, sort, expected):
    """Lower() on front-facing name fields -- otherwise 'beta' sorts after every capitalised title."""
    profile = _hunter()
    for name in ('Gamma', 'Alpha', 'beta'):
        _completed_game(profile, with_platinum=True, name=name)

    assert _names(client, profile, sort=sort) == expected


def test_ties_break_on_a_stable_key(client):
    """Rows sharing a timestamp must have a TOTAL order. Without the trophy_group_id tiebreak Postgres
    may order them differently per query, so infinite scroll can skip or repeat a card between pages."""
    profile = _hunter()
    same = timezone.now()
    for name in ('Tie A', 'Tie B', 'Tie C'):
        _at(profile, name, same)

    runs = [_names(client, profile) for _ in range(4)]

    assert len(set(map(tuple, runs))) == 1, f'order was not stable across identical queries: {runs}'


def test_a_junk_sort_falls_back_instead_of_erroring(client):
    profile = _hunter()
    _completed_game(profile, with_platinum=True, name='Bloodborne')

    assert _names(client, profile, sort='; DROP TABLE') == ['Bloodborne']


def test_a_junk_variant_falls_back_to_all(client):
    profile = _hunter()
    _completed_game(profile, with_platinum=True, name='Bloodborne')
    _completed_game(profile, with_platinum=False, name='Firewatch')

    assert sorted(_names(client, profile, variant='nonsense')) == ['Bloodborne', 'Firewatch']


# ── Pagination is coupled to the JS ───────────────────────────────────────────────────────────────

def test_paginate_by_matches_the_scroller_constant():
    """`plat-cards.js` hardcodes `paginateBy: 24`, and InfiniteScroller uses it both to decide whether
    to observe at all and for its `ceil(loadedCards / paginateBy)` resume maths. Changing one without
    the other silently breaks scroll resume and duplicates rows on a history restore."""
    import io
    from trophies.views.shareables_views import PlatCardsView

    js = io.open('static/js/plat-cards.js', encoding='utf-8').read()
    declared = re.search(r'paginateBy:\s*(\d+)', js)

    assert declared, 'plat-cards.js no longer declares paginateBy'
    assert int(declared.group(1)) == PlatCardsView.paginate_by


def test_page_two_returns_the_rows_partial(client):
    profile = _hunter()
    for i in range(30):
        _completed_game(profile, with_platinum=True, name=f'Game {i:02d}')
    client.force_login(profile.user)

    resp = client.get(URL, {'page': 2}, HTTP_X_REQUESTED_WITH='XMLHttpRequest')

    assert resp.status_code == 200
    content = resp.content.decode()
    assert 'items-grid' in content and '<html' not in content
    assert content.count('data-card-open') == 6        # 30 - 24


# ── ?art= ─────────────────────────────────────────────────────────────────────────────────────────

def _game_with_art(profile, shots=3):
    from trophies.models import IGDBMatch

    game, group, _ = _completed_game(profile, with_platinum=True, name='Arted')
    IGDBMatch.objects.create(
        concept=game.concept, igdb_id=4242, status=IGDBMatch.TRUSTED_STATUSES[0],
        igdb_screenshot_image_ids=[f'shot{i}' for i in range(shots)],
    )
    return group


def test_the_html_endpoint_lists_every_art_option(client):
    profile = _hunter()
    group = _game_with_art(profile, shots=3)
    client.force_login(profile.user)

    options = client.get(f'/api/v1/shareables/completion/{group.id}/html/').json()['art_options']

    assert len(options) == 3


@pytest.mark.parametrize('art', ['0', '2', '99', '-1', 'abc', ''])
def test_any_art_index_renders_rather_than_erroring(client, art):
    """`?art=` indexes the card's OWN list, so out-of-range and junk clamp to the first image instead
    of erroring or reaching for an arbitrary URL."""
    from unittest.mock import patch

    profile = _hunter()
    group = _game_with_art(profile, shots=3)
    client.force_login(profile.user)

    with patch('core.services.playwright_renderer.render_png', return_value=b'PNG'):
        resp = client.get(f'/api/v1/shareables/completion/{group.id}/png/', {'art': art})

    assert resp.status_code == 200
    assert resp['Content-Type'] == 'image/png'


def test_a_game_with_no_art_still_downloads_on_the_art_ground(client):
    """Selecting an art ground for a game with none must fall back, not 500."""
    from unittest.mock import patch

    profile = _hunter()
    _, group, _ = _completed_game(profile, with_platinum=True)
    client.force_login(profile.user)

    with patch('core.services.playwright_renderer.render_png', return_value=b'PNG') as render:
        resp = client.get(f'/api/v1/shareables/completion/{group.id}/png/', {'theme': 'ppArt', 'art': '0'})

    assert resp.status_code == 200
    assert render.call_args.kwargs['concept_bg_path'] is None
