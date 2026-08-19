"""The one-time seed that puts existing hunters on the per-edition board at deploy.

The store is written by `badge_xp.apply_changes`, so it fills itself as profiles sync. That is too slow
to deploy behind: until a hunter next synced they would simply be absent from a board they were on the
day before. This command reads the JSON maps the old board read and lands the same answer in one pass.
"""
import datetime as dt

import pytest
from django.core.management import call_command

from trophies.models import SeriesBadgeStanding, SeriesEditionStanding
from tests.factories import ProfileFactory

pytestmark = pytest.mark.django_db


def _standing(slug='dual', *, progress, group_xp=None, on=dt.date(2025, 5, 5), code='', linked=True):
    prof = ProfileFactory(country_code=code, is_linked=linked)
    SeriesBadgeStanding.objects.create(
        profile=prof, series_slug=slug, xp=sum((group_xp or {}).values()),
        group_progress=progress, group_xp=group_xp or {},
        progress_bp=5000, stages_cleared=1, stages_total=2,
        advanced_at=on, country_code=code, is_linked=linked,
    )
    return prof


def test_it_seeds_one_row_per_started_edition_with_that_editions_points():
    """Points, stages and the denominator come across EXACTLY -- `group_progress` and `group_xp` are
    already per edition, which is the whole reason a backfill is possible at all."""
    prof = _standing(progress={'ultra-hd': [3, 5], 'legacy-hd': [1, 4]},
                     group_xp={'ultra-hd': 1500, 'legacy-hd': 500})

    call_command('backfill_series_edition_standings')

    rows = {r.platform_group_key: r for r in SeriesEditionStanding.objects.filter(profile=prof)}
    assert set(rows) == {'ultra-hd', 'legacy-hd'}
    assert (rows['ultra-hd'].xp, rows['ultra-hd'].stages_cleared, rows['ultra-hd'].gating_count) == (1500, 3, 5)
    assert (rows['legacy-hd'].xp, rows['legacy-hd'].stages_cleared, rows['legacy-hd'].gating_count) == (500, 1, 4)


def test_an_untouched_edition_is_not_seeded():
    """`group_progress` keeps `[0, gating]` so the Collection has a denominator for a chase not begun. The
    board's membership rule is stricter, and the backfill has to apply the STORE's rule rather than copy
    the map it is reading -- or every chaser of one edition lands on the other's board at zero."""
    prof = _standing(progress={'ultra-hd': [2, 5], 'legacy-hd': [0, 4]},
                     group_xp={'ultra-hd': 1000})

    call_command('backfill_series_edition_standings')

    assert list(SeriesEditionStanding.objects.filter(profile=prof)
                .values_list('platform_group_key', flat=True)) == ['ultra-hd']


def test_an_edition_that_has_paid_no_points_is_still_seeded():
    """MEMBERSHIP is "started"; POINTS are the ordering. They are different keys on purpose -- a hunter can
    clear a gating stage that pays nothing, and `group_xp` omits an edition at zero. Reading points as
    membership would drop somebody visibly chasing, which is the failure the board was rebuilt to fix."""
    prof = _standing(progress={'ultra-hd': [1, 5]}, group_xp={})

    call_command('backfill_series_edition_standings')

    row = SeriesEditionStanding.objects.get(profile=prof)
    assert row.stages_cleared == 1 and row.xp == 0


def test_advanced_at_is_seeded_from_the_series_wide_value():
    """The one thing that CANNOT be recovered: the source carries one date for the whole series. Seeding it
    makes the board behave exactly as it did before the store existed -- no rank moves at deploy -- and the
    real per-edition dates arrive with the next evaluation. Pinned so the compromise is deliberate rather
    than discovered later as a bug."""
    prof = _standing(progress={'ultra-hd': [1, 5], 'legacy-hd': [2, 5]},
                     group_xp={'ultra-hd': 500, 'legacy-hd': 1000},
                     on=dt.date(2026, 2, 2))

    call_command('backfill_series_edition_standings')

    dates = set(SeriesEditionStanding.objects.filter(profile=prof).values_list('advanced_at', flat=True))
    assert dates == {dt.date(2026, 2, 2)}


def test_the_mirrored_board_predicates_come_across():
    """`country_code` and `is_linked` are what the board filters and indexes on. A seed that left them at
    their defaults would put every backfilled hunter behind `is_linked=False` -- an empty board, on a
    table that is visibly full."""
    prof = _standing(progress={'ultra-hd': [1, 5]}, group_xp={'ultra-hd': 500}, code='JP')

    call_command('backfill_series_edition_standings')

    row = SeriesEditionStanding.objects.get(profile=prof)
    assert (row.country_code, row.is_linked) == ('JP', True)


def test_rerunning_does_not_duplicate_or_clobber_real_dates():
    """Idempotent, and specifically SKIPS a series that already has rows. Once evaluation has written real
    per-edition dates, a second run must not re-seed them from the series-wide value and undo the fix."""
    prof = _standing(progress={'ultra-hd': [1, 5]}, group_xp={'ultra-hd': 500})
    call_command('backfill_series_edition_standings')
    SeriesEditionStanding.objects.filter(profile=prof).update(advanced_at=dt.date(2019, 9, 9))

    call_command('backfill_series_edition_standings')

    rows = SeriesEditionStanding.objects.filter(profile=prof)
    assert rows.count() == 1, 'a re-run duplicated the row'
    assert rows.first().advanced_at == dt.date(2019, 9, 9), 'a re-run clobbered a real per-edition date'


def test_force_rewrites_the_rows_it_would_otherwise_skip():
    """The escape hatch, for a first run that went in wrong. Off by default because the safe failure is
    "seeded twice, unchanged" and the unsafe one is "overwrote data the engine had corrected"."""
    prof = _standing(progress={'ultra-hd': [1, 5]}, group_xp={'ultra-hd': 500})
    call_command('backfill_series_edition_standings')
    SeriesEditionStanding.objects.filter(profile=prof).update(xp=1)

    call_command('backfill_series_edition_standings', '--force')

    assert SeriesEditionStanding.objects.get(profile=prof).xp == 500


def test_dry_run_writes_nothing():
    _standing(progress={'ultra-hd': [1, 5]}, group_xp={'ultra-hd': 500})
    call_command('backfill_series_edition_standings', '--dry-run')
    assert not SeriesEditionStanding.objects.exists()


def test_a_corrupt_progress_entry_is_skipped_rather_than_crashing_the_run():
    """`group_progress` is a JSONB blob, so its shape is a convention rather than a constraint -- and one
    bad row must not abort a whole-table backfill halfway through. The Collection's reader makes the same
    allowance for the same reason."""
    good = _standing(progress={'ultra-hd': [1, 5]}, group_xp={'ultra-hd': 500})
    _standing(slug='bent', progress={'ultra-hd': 'nonsense'}, group_xp={})

    call_command('backfill_series_edition_standings')

    assert list(SeriesEditionStanding.objects.values_list('profile_id', flat=True)) == [good.id]


def test_series_scopes_the_run():
    """For spot-checking one badge before committing to the whole table."""
    a = _standing(slug='aaa', progress={'ultra-hd': [1, 5]}, group_xp={'ultra-hd': 500})
    _standing(slug='bbb', progress={'ultra-hd': [1, 5]}, group_xp={'ultra-hd': 500})

    call_command('backfill_series_edition_standings', '--series', 'aaa')

    assert list(SeriesEditionStanding.objects.values_list('profile_id', flat=True)) == [a.id]
