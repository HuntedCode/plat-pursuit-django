"""`GroupBadge.required_stages` -- the medallion's "X / Y" stage count on Browse Badges.

The column's help_text promised a recompute and nothing ever performed one, so every row sat at its
`default=0` from the day the model was created. `badge_list_service` reads it as `stages_total`, and
`components/badge_medallion.html` renders the count behind `{% if total %}` -- so a zero does not show
as "0 / 0", it removes the count from every card. It read as a design choice, which is how it survived
a whole rebuild. Badge DETAIL was unaffected: it takes `result.gating_count` from a live evaluation.

These pins are about the two halves that were missing: a writer, and a browse card that renders a count.
"""
import pytest

from trophies.services.badge_orchestrator import build_catalog, recompute_required_stages
from tests.factories import (
    BadgeSeriesFactory, ConceptFactory, GameFactory, GroupBadgeFactory, PlatformGroupFactory,
    StageFactory,
)

pytestmark = pytest.mark.django_db


def _series_with_stages(n_stages, *, platforms=('PS4', 'PS5'), obtainable=True, delisted=False):
    """A series with `n_stages` gating stages, one concept and one game each."""
    series = BadgeSeriesFactory()
    for i in range(1, n_stages + 1):
        stage = StageFactory(series_slug=series.series_slug, stage_number=i)
        concept = ConceptFactory()
        GameFactory(concept=concept, title_platform=list(platforms),
                    is_obtainable=obtainable, is_delisted=delisted)
        stage.concepts.add(concept)
    return series


def _recompute(group_badges):
    return recompute_required_stages(build_catalog(list(group_badges)))


def test_the_count_is_written_at_all():
    """The whole bug: every row was 0 because nothing ever wrote this."""
    series = _series_with_stages(3)
    gb = GroupBadgeFactory(series=series)
    assert gb.required_stages == 0, 'the model default, which is what shipped'

    changed = _recompute([gb])

    gb.refresh_from_db()
    assert changed == 1
    assert gb.required_stages == 3


def test_stage_zero_does_not_gate():
    """Stage 0 is tangential by design -- the engine skips it, so the count must too."""
    series = _series_with_stages(2)
    # It needs a qualifying, obtainable game: an empty stage cannot gate whatever the filter does, so
    # a bare StageFactory here would pass even with the stage_number check deleted.
    tangential = StageFactory(series_slug=series.series_slug, stage_number=0)
    concept = ConceptFactory()
    GameFactory(concept=concept, title_platform=['PS4', 'PS5'], is_obtainable=True)
    tangential.concepts.add(concept)
    gb = GroupBadgeFactory(series=series)

    _recompute([gb])

    gb.refresh_from_db()
    assert gb.required_stages == 2


def test_an_unobtainable_stage_does_not_gate():
    series = _series_with_stages(2)
    dead_stage = StageFactory(series_slug=series.series_slug, stage_number=3)
    concept = ConceptFactory()
    GameFactory(concept=concept, title_platform=['PS4', 'PS5'], is_obtainable=False)
    dead_stage.concepts.add(concept)
    gb = GroupBadgeFactory(series=series)

    _recompute([gb])

    gb.refresh_from_db()
    assert gb.required_stages == 2, 'an unobtainable stage cannot gate'


def test_the_count_is_per_edition_not_per_series():
    """THE reason this is not just a `Stage` row count. A stage stops gating on an edition that
    excludes delisted games, so two editions of one series legitimately differ."""
    series = _series_with_stages(2)
    delisted_stage = StageFactory(series_slug=series.series_slug, stage_number=3)
    concept = ConceptFactory()
    GameFactory(concept=concept, title_platform=['PS4', 'PS5'], is_delisted=True)
    delisted_stage.concepts.add(concept)

    keeps = GroupBadgeFactory(series=series,
                              platform_group=PlatformGroupFactory(exclude_delisted=False))
    excludes = GroupBadgeFactory(series=series,
                                 platform_group=PlatformGroupFactory(exclude_delisted=True))

    _recompute([keeps, excludes])

    keeps.refresh_from_db()
    excludes.refresh_from_db()
    assert keeps.required_stages == 3
    assert excludes.required_stages == 2, 'the delisted stage does not gate this edition'


def test_a_stage_off_the_edition_platform_does_not_gate():
    series = _series_with_stages(2)
    other = StageFactory(series_slug=series.series_slug, stage_number=3)
    concept = ConceptFactory()
    GameFactory(concept=concept, title_platform=['PS3'])
    other.concepts.add(concept)
    gb = GroupBadgeFactory(series=series, platform_group=PlatformGroupFactory(platforms=['PS4', 'PS5']))

    _recompute([gb])

    gb.refresh_from_db()
    assert gb.required_stages == 2


def test_recompute_only_writes_rows_that_changed():
    """It runs over the whole live catalogue in `nightly`; a no-op run must not rewrite every row."""
    series = _series_with_stages(2)
    gb = GroupBadgeFactory(series=series)

    assert _recompute([gb]) == 1
    gb.refresh_from_db()
    assert _recompute([gb]) == 0, 'second pass rewrote rows that had not changed'


def test_the_browse_card_renders_a_stage_count():
    """The user-visible half. `build_list_cards` feeds `frame.stages_total`, and the medallion hides
    its count entirely when that is falsy -- so this asserts the value the template gates on."""
    from trophies.services.badge_list_service import build_list_cards

    series = _series_with_stages(4)
    gb = GroupBadgeFactory(series=series)
    _recompute([gb])
    gb.refresh_from_db()

    cards = build_list_cards([gb], None)

    assert cards[0]['frame']['stages_total'] == 4, 'the medallion count would be hidden'
