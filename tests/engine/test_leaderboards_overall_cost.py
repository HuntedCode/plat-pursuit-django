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


def test_the_series_directory_reads_its_counts_in_one_round_trip(client):
    """Redis N+1. ZCARD is O(1), so the per-call latency WAS the cost, and the directory is unpaginated --
    the number of round-trips grew with the catalogue on a page anyone can open.

    Pinned as "not per-row" rather than "exactly one call", so batching stays free to change shape (a
    single MGET, a cached map) without failing for the wrong reason. What must never come back is a call
    count that scales with the number of series.
    """
    for i in range(5):
        _live_series(f'nplus-{i}', f'N Plus {i}')

    with patch('trophies.views.badge_views.get_progress_count') as per_row:
        assert client.get(URL, {'tab': 'series'}).status_code == 200

    assert per_row.call_count == 0, (
        f'the directory called get_progress_count {per_row.call_count} times -- one Redis round-trip per '
        f'series. Use the batched get_progress_counts().'
    )


def test_the_batched_count_helper_returns_a_count_per_series():
    """The batching is only safe if the pipeline's results still line up with the slugs that produced
    them. `zip(slugs, pipe.execute())` is order-dependent, and a silently short result would map counts to
    the WRONG series rather than failing -- a wrong number reads as real data.
    """
    from trophies.services.redis_leaderboard_service import get_progress_counts

    slugs = ['aaa', 'bbb', 'ccc']
    counts = get_progress_counts(slugs)

    assert set(counts) == set(slugs), f'batched counts lost or invented a series: {sorted(counts)}'
    assert all(isinstance(v, int) for v in counts.values()), 'a count came back non-numeric'
    assert get_progress_counts([]) == {}, 'the empty case should not touch Redis at all'


@pytest.mark.parametrize('tab, must_not_fetch', [
    ('country', ['get_xp_page', 'get_progress_page']),
    ('series', ['get_xp_page', 'get_progress_page']),
    ('progress', ['get_xp_page']),
    ('xp', ['get_progress_page']),
])
def test_a_tab_does_not_pay_for_the_boards_it_does_not_render(client, tab, must_not_fetch):
    """Every board used to be assembled on every request. A page fetch is a ZREVRANGE plus an HMGET of 50
    JSON blobs; two of those were built and discarded on three of the four tabs.

    The RANK lookups deliberately stay unconditional and are not asserted against here -- they are single
    O(log n) ZREVRANKs, and the user-stats strip sits above the tab row, so it shows them whichever tab is
    open. Cutting those would change what renders, not just what it costs.
    """
    _live_series('paid', 'Paid')

    patches = {name: patch(f'trophies.views.badge_views.{name}') for name in must_not_fetch}
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
