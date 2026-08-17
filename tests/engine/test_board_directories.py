"""The board DIRECTORIES -- Badge Boards and Game Boards (leaderboards rebuild, step 6).

A directory is a catalogue of BOARDS: entity identity, a top slice, a link to the full board. The load-
bearing constraint is the preview query. The naive shape is one board read per card, which compounds
under infinite scroll -- a 24-card page becomes 24 reads, and the next scroll page 24 more. A window
function collapses that to one, and the tests here pin that it stays one.

The other constraint is the THIN-DIRECTORY rule: search plus exactly two sorts, no filter panel. Without
it these converge into second copies of `/games/` and `/badges/`, and then there are two walls to
maintain and a drift risk -- the failure this whole rebuild exists to remove.
"""
import datetime as dt

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext

from trophies.models import ProfileGame, SeriesBadgeStanding
from trophies.services import badge_leaderboards as lb
from tests.factories import ProfileFactory, ConceptFactory, GameFactory

pytestmark = pytest.mark.django_db


def _standing(slug, *, bp, on, name=None):
    p = ProfileFactory(display_psn_username=name or None)
    SeriesBadgeStanding.objects.create(
        profile=p, series_slug=slug, xp=10, progress_bp=bp,
        stages_cleared=bp // 2500, stages_total=4, advanced_at=on,
    )
    return p


def test_the_preview_reads_once_per_entity_on_the_page():
    """One index-limited read per entity requested -- NOT one query total.

    The single-query version (a `ROW_NUMBER()` window) was measured 1,000x slower in the 2026-08 audit,
    because a window's LIMIT is applied after every row of every partition has been read and sorted. The
    property worth pinning is that the reads track the entities ASKED FOR (a page of cards), so nothing
    grows with the size of the standing table.
    """
    few = ['s0', 's1', 's2']
    for slug in few:
        _standing(slug, bp=5000, on=dt.date(2024, 1, 1))
    with CaptureQueriesContext(connection) as small:
        lb.series_board_previews(few)

    many = [f'm{i}' for i in range(20)]
    for slug in many:
        _standing(slug, bp=5000, on=dt.date(2024, 1, 1))
    with CaptureQueriesContext(connection) as large:
        lb.series_board_previews(many)

    assert len(small.captured_queries) == len(few)
    assert len(large.captured_queries) == len(many), (
        'the preview should read once per requested entity; a different count means the shape changed'
    )


def test_the_preview_is_capped_per_entity_not_overall():
    """`LIMIT n` on the whole set would give the top n across ALL series -- one popular series would fill
    the page and every other card would render empty. The cap is PER PARTITION."""
    for i in range(8):
        _standing('busy', bp=9000 - i * 100, on=dt.date(2024, 1, 1 + i))
    for i in range(8):
        _standing('quiet', bp=8000 - i * 100, on=dt.date(2024, 1, 1 + i))

    previews = lb.series_board_previews(['busy', 'quiet'], n=5)

    assert len(previews['busy']) == 5
    assert len(previews['quiet']) == 5, 'a second series got no preview -- the cap is global, not per series'


def test_the_preview_matches_the_full_board_order():
    """A preview that disagrees with the board it previews is worse than no preview. Same ordering
    expression, including the `advanced_at` tiebreak that stops a rung of chasers sorting by profile id.
    """
    later = _standing('ord', bp=5000, on=dt.date(2024, 6, 1), name='SecondThere')
    earlier = _standing('ord', bp=5000, on=dt.date(2021, 1, 1), name='FirstThere')
    done = _standing('ord', bp=10000, on=dt.date(2025, 1, 1), name='Finisher')

    preview = [r[0] for r in lb.series_board_previews(['ord'])['ord']]
    full = [r[0] for r in lb.series_board_rows('ord')]

    assert preview == full[:len(preview)], 'the preview and the board disagree about order'
    assert preview[0] == done.id and preview[1] == earlier.id


def test_counts_feed_both_the_sort_and_the_gate():
    """"Most entrants" is free precisely because the minimum-participants gate needs the same numbers --
    a directory full of one-entrant boards reads as broken and drowns the boards worth looking at."""
    for _ in range(3):
        _standing('popular', bp=5000, on=dt.date(2024, 1, 1))
    _standing('lonely', bp=5000, on=dt.date(2024, 1, 1))

    counts = lb.series_board_counts(['popular', 'lonely', 'nobody'])
    assert counts['popular'] == 3 and counts['lonely'] == 1
    assert 'nobody' not in counts, 'a series with no entrants should be absent, not zero-filled'


def test_game_previews_ride_the_shipped_leaderboard_ordering():
    """Ordered to match `pg_game_leaderboard_idx` (game, -progress, most_recent_trophy_date, profile), so
    the window uses the index the shipped game board already uses rather than forcing its own sort."""
    game = GameFactory(concept=ConceptFactory(), title_platform=['PS5'])
    top = ProfileFactory()
    mid = ProfileFactory()
    ProfileGame.objects.create(profile=top, game=game, progress=100,
                               most_recent_trophy_date=dt.datetime(2024, 1, 1, tzinfo=dt.timezone.utc))
    ProfileGame.objects.create(profile=mid, game=game, progress=60,
                               most_recent_trophy_date=dt.datetime(2024, 1, 1, tzinfo=dt.timezone.utc))

    preview = lb.game_board_previews([game.id])[game.id]
    assert [r[0] for r in preview] == [top.id, mid.id]


def test_a_game_nobody_has_touched_gets_no_preview():
    """`progress > 0` -- a wall of zeroes is not a board, and an owner who has earned nothing is not an
    entrant. It also keeps the count and the rows agreeing."""
    game = GameFactory(concept=ConceptFactory(), title_platform=['PS5'])
    ProfileGame.objects.create(profile=ProfileFactory(), game=game, progress=0)

    assert lb.game_board_previews([game.id]) == {}


def test_empty_input_touches_the_database_at_all():
    """An empty page of entities must not issue a query with an empty IN clause."""
    with CaptureQueriesContext(connection) as ctx:
        assert lb.series_board_previews([]) == {}
        assert lb.series_board_counts([]) == {}
        assert lb.game_board_previews([]) == {}
    assert len(ctx.captured_queries) == 0


# ------------------------------------------------------------------ the pages ----------------------------

def _series_with_board(slug, name, n_hunters, *, live=True):
    from tests.factories import BadgeSeriesFactory, PlatformGroupFactory, GroupBadgeFactory
    series = BadgeSeriesFactory(series_slug=slug, name=name)
    if live:
        pg = PlatformGroupFactory(key=f'{slug}-pg', name='Ultra HD', platforms=['PS5'])
        GroupBadgeFactory(series=series, platform_group=pg, is_live=True)
    for i in range(n_hunters):
        _standing(slug, bp=9000 - i * 100, on=dt.date(2024, 1, 1 + i))
    return series


def test_badge_boards_lists_series_that_have_a_board(client):
    from django.urls import reverse
    _series_with_board('alpha', 'Alpha Series', 3)
    body = client.get(reverse('badge_boards')).content.decode()
    assert 'Alpha Series' in body
    assert 'bdir-card' in body


def test_a_dormant_series_is_not_listed(client, settings):
    """No live GroupBadge means no board to preview. Listing it would offer a race nobody can enter.

    Gate pinned so this stays a test of DORMANCY. It is a negative assertion, so a raised
    `BOARD_MIN_ENTRANTS_BADGES` would make it pass vacuously -- filtered out by the threshold rather than
    by the rule it names, and still green.
    """
    from django.urls import reverse

    settings.BOARD_MIN_ENTRANTS = {'games': 3, 'badges': 1, 'jobs': 1}
    _series_with_board('dorm', 'Dormant Series', 3, live=False)
    body = client.get(reverse('badge_boards')).content.decode()
    assert 'Dormant Series' not in body


def test_the_directory_previews_scale_with_the_PAGE_not_the_catalogue(client):
    """The invariant, restated after a 2026-08 performance audit measured the old one backwards.

    This used to assert a CONSTANT query count, because the previews were one
    `ROW_NUMBER() OVER (PARTITION BY ...)` query and "one query beats N" is normally the right instinct.
    It is wrong here, and badly: Postgres cannot push a LIMIT into a window partition, so `WHERE rn <= 5`
    runs AFTER the window has read and sorted EVERY row of EVERY partition. Measured on realistic data
    the single query took 1,279 ms with a 30 MB external sort spilling to disk, against 1.6 ms for the
    per-partition reads -- and the "most entrants" sort puts the biggest partitions on page one, so that
    is the typical case rather than the tail.

    So the property that actually matters is not "one query"; it is that the work is bounded by what is
    ON THE PAGE. A page of 24 cards issues ~24 index-limited reads whether the catalogue holds 30 series
    or 3,000, and each read stops after 5 rows instead of scanning a table.
    """
    from django.urls import reverse
    for i in range(2):
        _series_with_board(f'few{i}', f'Few {i}', 3)
    with CaptureQueriesContext(connection) as small:
        client.get(reverse('badge_boards'))

    for i in range(14):
        _series_with_board(f'many{i}', f'Many {i}', 3)
    with CaptureQueriesContext(connection) as large:
        client.get(reverse('badge_boards'))

    # Both pages fit inside one page of cards, so both do the same bounded work.
    assert len(large.captured_queries) <= len(small.captured_queries) + 14, (
        f'{len(small.captured_queries)} queries for 2 boards but {len(large.captured_queries)} for 16 -- '
        f'that is more than one read per additional card, so something scales with the catalogue'
    )


def test_a_preview_read_is_limited_not_a_partition_scan():
    """Each per-partition read must carry its own LIMIT. Without it the "N small seeks" reasoning above
    collapses into N table scans, which is worse than the window function it replaced."""
    from trophies.services import badge_leaderboards as lb

    for i in range(3):
        _series_with_board(f'lim{i}', f'Lim {i}', 3)
    slugs = [f'lim{i}' for i in range(3)]

    with CaptureQueriesContext(connection) as ctx:
        lb.series_board_previews(slugs, n=5)

    preview_sql = [q['sql'] for q in ctx.captured_queries if 'seriesbadgestanding' in q['sql'].lower()]
    assert preview_sql, 'no preview query ran at all'
    assert all('LIMIT 5' in sql for sql in preview_sql), (
        'a preview read has no LIMIT, so it scans the whole partition'
    )


def test_the_directory_has_search_and_exactly_two_sorts_and_no_filter_panel(client):
    """The THIN-DIRECTORY rule, as a test. This page catalogues the same entities `/badges/` already
    catalogues; the differentiator is that it stays a board catalogue rather than growing into a second
    Browse Games. A filter drawer here is the signal that it has."""
    from django.urls import reverse
    _series_with_board('thin', 'Thin', 3)
    body = client.get(reverse('badge_boards')).content.decode()

    assert 'name="q"' in body, 'search is missing'
    assert body.count('<option value="') == 2, 'the directory should offer exactly two sorts'
    for drawer in ('data-browse-form', 'filterPanel', 'pp-bgal__advanced', 'data-minibar-filters'):
        assert drawer not in body, f'{drawer} -- the directory grew a filter panel'


def test_sorting_by_entrants_puts_the_busiest_board_first(client, settings):
    from django.urls import reverse

    # Pinned: the badges gate is env-overridable (`BOARD_MIN_ENTRANTS_BADGES`) and 'Quiet Series' has ONE
    # entrant, so on a box that raised it this test goes red for behaviour that has not changed. The games
    # path was pinned after exactly that bite; this one was missed.
    settings.BOARD_MIN_ENTRANTS = {'games': 3, 'badges': 1, 'jobs': 1}
    _series_with_board('quiet', 'Quiet Series', 1)
    _series_with_board('busy', 'Busy Series', 6)
    body = client.get(reverse('badge_boards'), {'sort': 'entrants'}).content.decode()
    assert body.index('Busy Series') < body.index('Quiet Series')


def test_search_narrows_the_catalogue(client):
    from django.urls import reverse
    _series_with_board('keep', 'Findable', 2)
    _series_with_board('drop', 'Unrelated', 2)
    body = client.get(reverse('badge_boards'), {'q': 'Find'}).content.decode()
    assert 'Findable' in body and 'Unrelated' not in body


def test_game_boards_gates_out_boards_nobody_is_on(client, settings):
    """Games are numerous; a 1-2 name board is noise in a catalogue this size. `played_count` is a
    denormalized column, so the gate costs nothing.

    The threshold is PINNED here rather than left to the default. It became env-overridable
    (`BOARD_MIN_ENTRANTS_GAMES`) when a dev database needed a lower one, which quietly made this test
    depend on the machine running it -- green on CI, red on the developer box that set the variable, for a
    behaviour that had not changed.
    """
    from django.urls import reverse
    from trophies.models import Game

    settings.BOARD_MIN_ENTRANTS = {'games': 3, 'badges': 1, 'jobs': 1}
    busy = GameFactory(concept=ConceptFactory(), title_name='Busy Game', title_platform=['PS5'])
    lonely = GameFactory(concept=ConceptFactory(), title_name='Lonely Game', title_platform=['PS5'])
    Game.objects.filter(pk=busy.pk).update(played_count=9)
    Game.objects.filter(pk=lonely.pk).update(played_count=1)
    for i in range(3):
        ProfileGame.objects.create(profile=ProfileFactory(), game=busy, progress=90 - i)

    body = client.get(reverse('game_boards')).content.decode()
    assert 'Busy Game' in body
    assert 'Lonely Game' not in body, 'a board below the participants gate was listed'


# ------------------------------------------------------------------ the gate ------------------------------

def test_the_gate_is_configurable_per_kind(client, settings):
    """The prod-correct threshold silently empties the page on any smaller dataset.

    Reported for real: a dev database with a handful of linked profiles has almost no game owned by 3
    people, so the default hid the entire catalogue behind "no board has enough hunters on it yet" -- a
    confident, specific, wrong explanation. The number now comes from settings so a smaller dataset can
    lower it without a code edit.
    """
    from django.urls import reverse
    from trophies.models import Game

    game = GameFactory(concept=ConceptFactory(), title_name='Two Player Game', title_platform=['PS5'])
    for i in range(2):
        ProfileGame.objects.create(profile=ProfileFactory(), game=game, progress=80 - i)
    # AFTER the creates: `ProfileGame.objects.create` fires the post_save signal that increments
    # played_count, so setting it first just gets overwritten -- which is how this fixture failed the
    # first time, reporting a gate bug that was not there.
    Game.objects.filter(pk=game.pk).update(played_count=2)

    settings.BOARD_MIN_ENTRANTS = {'games': 3, 'badges': 1, 'jobs': 1}
    assert 'Two Player Game' not in client.get(reverse('game_boards')).content.decode()

    settings.BOARD_MIN_ENTRANTS = {'games': 1, 'badges': 1, 'jobs': 1}
    assert 'Two Player Game' in client.get(reverse('game_boards')).content.decode(), (
        'lowering the gate did not surface the board -- the threshold is still hardcoded somewhere'
    )


def test_a_stale_played_count_hides_a_real_board(client, settings):
    """Documents the coupling that caused the report, so the next reader of `played_count__gte` knows what
    it depends on.

    `Game.played_count` is incremented by a post_save signal on ProfileGame CREATION. bulk_create,
    fixtures and database restores bypass signals entirely, so the counter reads 0 while the rows sit
    there intact. `backfill_played_count` (or the nightly `recalc_earn_rates`) repairs it.
    """
    from django.urls import reverse
    from trophies.models import Game

    settings.BOARD_MIN_ENTRANTS = {'games': 3, 'badges': 1, 'jobs': 1}
    game = GameFactory(concept=ConceptFactory(), title_name='Bulk Loaded', title_platform=['PS5'])
    ProfileGame.objects.bulk_create([
        ProfileGame(profile=ProfileFactory(), game=game, progress=90 - i) for i in range(6)
    ])

    assert Game.objects.get(pk=game.pk).played_count == 0, 'bulk_create fired the signal after all'
    assert 'Bulk Loaded' not in client.get(reverse('game_boards')).content.decode()

    # The repair the backfill command performs.
    Game.objects.filter(pk=game.pk).update(played_count=6)
    assert 'Bulk Loaded' in client.get(reverse('game_boards')).content.decode()
