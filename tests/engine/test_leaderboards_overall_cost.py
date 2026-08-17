"""Cost defects on `/leaderboards/` (2026-08 backend audit).

Three findings, all about work the page did that nothing rendered. None of them changed a pixel, which is
why they survived: the page looked correct the whole time and simply cost more than it should.

1. The Series directory joined `IGDBMatch` twice per row without deferring `raw_response` -- the ~30 KB
   API blob CLAUDE.md names as the trigger for the May 2026 web-server OOM. Unpaginated, on a public page.
2. It then called Redis once per series inside a Python loop, so the round-trip count grew with the
   catalogue.
3. Every tab's board was assembled on every request, so `?tab=country` paid for the XP and global-progress
   pages it would never render.

These are pinned by BEHAVIOUR (the SQL that runs, the calls that are made), not by reading the source --
each one is a line that is easy to delete while leaving something that still renders correctly.
"""
from unittest.mock import patch

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from tests.factories import BadgeFactory, ConceptFactory, IGDBMatchFactory

pytestmark = pytest.mark.django_db

URL = reverse('overall_badge_leaderboards')


def _live_series(slug, name):
    """A live tier-1 badge with a concept carrying an IGDBMatch -- i.e. a directory row whose
    `raw_response` is reachable through the select_related the view does."""
    concept = ConceptFactory()
    # `raw_response` populated on purpose: an empty column would let the deferral test pass even if the
    # join were reintroduced, since the assertion is about the SQL, but a realistic row keeps the fixture
    # honest about what the page would actually be dragging across.
    IGDBMatchFactory(concept=concept, raw_response={'id': 1, 'name': 'x' * 2000})
    return BadgeFactory(series_slug=slug, display_series=name, tier=1, is_live=True,
                        most_recent_concept=concept)


def test_the_series_directory_never_selects_the_igdb_blob(client):
    """`raw_response` is ~30 KB of IGDB JSON per row that no directory row reads, and this queryset joins
    `igdb_match` through TWO paths (the badge's own concept and its base badge's). Undeferred and
    unpaginated, that is the whole catalogue's worth of unread blob in one response.

    Asserted against the SQL actually issued rather than against a `.defer(...)` call in the source: the
    point is that the column does not travel, and a future refactor could achieve or lose that any number
    of ways. `.only()` on the wrong field list would silently reintroduce it while the source still reads
    like it defers.
    """
    for i in range(3):
        _live_series(f'srs-{i}', f'Series {i}')

    with CaptureQueriesContext(connection) as ctx:
        assert client.get(URL, {'tab': 'series'}).status_code == 200

    offending = [q['sql'] for q in ctx.captured_queries if 'raw_response' in q['sql']]
    assert not offending, (
        'the Series directory is selecting igdb_match.raw_response -- the ~30 KB blob per joined row that '
        'CLAUDE.md flags as the OOM trigger. Defer it on BOTH concept paths.'
    )


def test_the_series_directory_count_does_not_scale_with_the_catalogue(client):
    """Was a Redis N+1 (one ZCARD per series in a Python loop), now one grouped query over the standing
    store. Either way the property that matters is the same and is asserted directly: the number of
    queries must not grow with the number of series.

    Pinned as "does not scale" rather than "calls X once" deliberately -- the first version of this test
    patched a specific Redis helper by name and broke the moment the backend changed, even though the
    behaviour it guarded was still correct. A scaling assertion outlives the implementation.
    """
    for i in range(3):
        _live_series(f'few-{i}', f'Few {i}')
    with CaptureQueriesContext(connection) as small:
        assert client.get(URL, {'tab': 'series'}).status_code == 200

    for i in range(12):
        _live_series(f'many-{i}', f'Many {i}')
    with CaptureQueriesContext(connection) as large:
        assert client.get(URL, {'tab': 'series'}).status_code == 200

    assert len(large.captured_queries) == len(small.captured_queries), (
        f'{len(small.captured_queries)} queries for 3 series but {len(large.captured_queries)} for 15 -- '
        f'the directory is querying per row again'
    )


def test_a_series_with_no_participants_still_renders_a_count(client):
    """A grouped query returns no ROW for a series nobody is chasing, so the lookup must default rather
    than KeyError -- and it must show 0, not blank. Every new series starts here."""
    _live_series('nobody', 'Nobody Home')
    body = client.get(URL, {'tab': 'series'}).content.decode()
    assert 'Nobody Home' in body, 'a series with no participants vanished from the directory'


@pytest.mark.parametrize('tab, must_not_fetch', [
    ('progress', ['xp_rows', 'career_xp_rows']),
    ('points',   ['progress_rows', 'career_xp_rows']),
    ('career',   ['progress_rows', 'xp_rows']),
    ('series',   ['progress_rows', 'xp_rows', 'career_xp_rows']),
])
def test_a_tab_does_not_pay_for_the_boards_it_does_not_render(client, tab, must_not_fetch):
    """Every board used to be assembled on every request. Only the ACTIVE one is built now.

    The RANK lookups deliberately stay unconditional and are not asserted against here -- they are single
    indexed COUNTs feeding the header's "your standing" strip, which shows all three whichever board is
    open. Cutting those would change what renders, not just what it costs.
    """
    _live_series('paid', 'Paid')

    patches = {name: patch(f'trophies.services.badge_leaderboards.{name}') for name in must_not_fetch}
    started = {name: p.start() for name, p in patches.items()}
    try:
        assert client.get(URL, {'tab': tab}).status_code == 200
    finally:
        for p in patches.values():
            p.stop()

    for name, mock in started.items():
        assert mock.call_count == 0, (
            f'?tab={tab} still built the {name} board it never renders ({mock.call_count} calls)'
        )


@pytest.mark.parametrize('legacy, lands_on', [('xp', 'points'), ('country', 'points')])
def test_the_old_tab_keys_still_land_somewhere_sensible(client, legacy, lands_on):
    """`?tab=xp` was the Badge Points board and `?tab=country` was a TAB before country became a filter.
    Both are in the wild in links and bookmarks, and an unrecognised key silently falling back to the
    default would drop a reader somewhere they did not ask for with no sign anything happened."""
    body = client.get(URL, {'tab': legacy}).content.decode()
    assert f'data-board="{lands_on}"' in body
    assert f'<a href="?tab={lands_on}"' in body or f'data-board="{lands_on}"' in body


def test_an_unknown_country_does_not_silently_empty_the_board(client):
    """An unvalidated code would return zero rows, which reads as "nobody from there plays" rather than
    "that is not a country we rank". The filter falls back to Everywhere instead."""
    from trophies.models import ProfileBadgeStanding
    from tests.factories import ProfileFactory

    p = ProfileFactory(display_psn_username='Somebody', country_code='CA', country='Canada')
    ProfileBadgeStanding.objects.create(profile=p, total_xp=100, country_code='CA',
                                        trophies_platinum=2, trophies_total=30)

    body = client.get(URL, {'tab': 'progress', 'country': 'ZZ'}).content.decode()
    assert 'Somebody' in body, 'an unknown country code emptied the board instead of falling back'
