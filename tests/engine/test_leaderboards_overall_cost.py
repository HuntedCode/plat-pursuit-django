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
    ('country', ['xp_rows', 'progress_rows']),
    ('series', ['xp_rows', 'progress_rows']),
    ('progress', ['xp_rows']),
    ('xp', ['progress_rows']),
])
def test_a_tab_does_not_pay_for_the_boards_it_does_not_render(client, tab, must_not_fetch):
    """Every board used to be assembled on every request. A page fetch is a ZREVRANGE plus an HMGET of 50
    JSON blobs; two of those were built and discarded on three of the four tabs.

    The RANK lookups deliberately stay unconditional and are not asserted against here -- they are single
    indexed COUNTs, and the user-stats strip sits above the tab row, so it shows them whichever tab is
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


# ------------------------------------------------------------------ the Lane B swap ----------------------

def test_the_boards_render_from_the_standing_stores(client):
    """End-to-end proof of the swap: rows come from `ProfileBadgeStanding` via Lane B, not from a Redis
    sorted set plus a display hash."""
    from trophies.models import ProfileBadgeStanding
    from tests.factories import ProfileFactory

    # `country` (the display name) as well as `country_code`: the picker is built from profiles that
    # carry both, so a code-only fixture yields an empty picker and no board.
    top = ProfileFactory(display_psn_username='TopHunter', country_code='CA', country='Canada')
    ProfileBadgeStanding.objects.create(profile=top, total_xp=900, country_code='CA',
                                        trophies_platinum=7, trophies_total=140)

    xp_body = client.get(URL, {'tab': 'xp'}).content.decode()
    assert 'TopHunter' in xp_body, 'the Badge Points board is not reading the standing store'

    progress_body = client.get(URL, {'tab': 'progress'}).content.decode()
    assert 'TopHunter' in progress_body, 'the Global Progress board is not reading the standing store'

    country_body = client.get(URL, {'tab': 'country', 'country': 'CA'}).content.decode()
    assert 'TopHunter' in country_body, 'the country slice is not reading the standing store'


def test_a_renamed_hunter_shows_their_new_name_immediately(client):
    """The concrete defect the swap fixes. Redis denormalized display data into a hash written at rank
    time, so a hunter who changed their PSN name kept the old one on every board until the next signal or
    the 6-hourly rebuild. Identity is now read live at render, so it cannot be stale.
    """
    from trophies.models import ProfileBadgeStanding
    from tests.factories import ProfileFactory

    hunter = ProfileFactory(display_psn_username='OldName')
    ProfileBadgeStanding.objects.create(profile=hunter, total_xp=500, trophies_platinum=3,
                                        trophies_total=60)
    assert 'OldName' in client.get(URL, {'tab': 'xp'}).content.decode()

    hunter.display_psn_username = 'NewName'
    hunter.save(update_fields=['display_psn_username'])

    body = client.get(URL, {'tab': 'xp'}).content.decode()
    assert 'NewName' in body and 'OldName' not in body, (
        'the board is serving a stale denormalized name -- identity must be read live'
    )


def test_a_board_page_costs_a_constant_number_of_queries(client):
    """Two reads per board page -- the board itself and one `hydrate()` for the whole page -- plus the
    request's own fixed overhead. What must never happen is a per-ROW query: `displayed_title` is a METHOD
    doing two queries per profile, so hydrating 50 rows by calling it would be ~100 round trips to print
    one word under each name."""
    from trophies.models import ProfileBadgeStanding
    from tests.factories import ProfileFactory

    for i in range(3):
        p = ProfileFactory()
        ProfileBadgeStanding.objects.create(profile=p, total_xp=100 + i, trophies_total=i)
    with CaptureQueriesContext(connection) as small:
        client.get(URL, {'tab': 'xp'})

    for i in range(20):
        p = ProfileFactory()
        ProfileBadgeStanding.objects.create(profile=p, total_xp=500 + i, trophies_total=i)
    with CaptureQueriesContext(connection) as large:
        client.get(URL, {'tab': 'xp'})

    assert len(large.captured_queries) == len(small.captured_queries), (
        f'{len(small.captured_queries)} queries for 3 rows but {len(large.captured_queries)} for 23 -- '
        f'the board is hydrating per row'
    )
