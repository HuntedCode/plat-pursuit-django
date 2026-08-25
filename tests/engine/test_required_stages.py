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
from trophies.models import SeriesBadgeStanding, UserGroupBadge
from tests.factories import (
    BadgeSeriesFactory, ConceptFactory, GameFactory, GroupBadgeFactory, PlatformGroupFactory,
    ProfileFactory, StageFactory,
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


def _medallion(card):
    """Render the real medallion for a card. The first version of these tests asserted on the frame
    dict and never rendered anything, which is why it could not see that the card was about to show
    "0 / 5" to a hunter who had finished the badge."""
    from django.template.loader import render_to_string

    return render_to_string('components/badge_medallion.html',
                            {'frame': card['frame'], 'no_id': True})


def _card(gb, profile):
    from trophies.services.badge_list_service import build_list_cards

    return build_list_cards([gb], profile)[0]


def test_an_anonymous_viewer_gets_no_stage_count():
    """A catalogue wall has no progress to show someone with no account. "0 / 5" there is noise, and
    it is what a total-without-a-done produces."""
    series = _series_with_stages(4)
    gb = GroupBadgeFactory(series=series)
    _recompute([gb])
    gb.refresh_from_db()

    assert 'pp-med__count' not in _medallion(_card(gb, None))


def test_a_held_badge_reads_as_complete_not_zero():
    """THE regression giving the column a writer introduced. `_list_frame` set `stages_total` and
    never `stages_done`, and the medallion renders `{{ stages_done|default:0 }} / {{ total }}` -- so
    the moment the total stopped being 0, every card said "0 / 4", including badges you own."""
    series = _series_with_stages(4)
    gb = GroupBadgeFactory(series=series)
    _recompute([gb])
    gb.refresh_from_db()

    profile = ProfileFactory()
    UserGroupBadge.objects.create(profile=profile, group_badge=gb)

    html = _medallion(_card(gb, profile))

    assert '4 / 4' in html
    assert '0 / 4' not in html


def test_a_partial_standing_shows_real_progress():
    series = _series_with_stages(4)
    gb = GroupBadgeFactory(series=series)
    _recompute([gb])
    gb.refresh_from_db()

    profile = ProfileFactory()
    SeriesBadgeStanding.objects.create(
        profile=profile, series_slug=series.series_slug, stages_cleared=2, stages_total=4,
    )

    assert '2 / 4' in _medallion(_card(gb, profile))


def test_the_browse_card_carries_both_halves_of_the_count():
    """Either both or neither: a total without a done is the bug above."""
    series = _series_with_stages(4)
    gb = GroupBadgeFactory(series=series)
    _recompute([gb])
    gb.refresh_from_db()

    frame = _card(gb, ProfileFactory())['frame']

    assert frame['stages_total'] == 4
    assert 'stages_done' in frame, 'the medallion defaults this to 0 and renders "0 / N"'
