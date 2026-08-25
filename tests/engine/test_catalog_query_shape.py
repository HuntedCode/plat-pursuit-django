"""How the per-profile completion reads filter, and that both shapes select the same rows.

`evaluate_with_catalog` runs once per profile -- ~300,000 times on `evaluate_badges --all`, once per
sync in `evaluate_for_touched_games`, and once per badge-page render in `get_badge_detail`. It decides
which badges every hunter has earned, so a filter that selects the wrong rows is silently wrong for
the whole userbase and nothing crashes.

TWO SHAPES, because neither wins in both regimes. Inlining the catalogue's id list sends one bound
parameter per game: fine for one series, ruinous for the whole catalogue (~440 ms per profile, over a
day of wall clock across the userbase). A subquery makes that constant-size and is ~60x faster there,
but Postgres semi-joins it rather than hashing it once, so on a small catalogue against a large
library it is ~12x SLOWER. The CALLER declares which regime it is in (`whole_catalogue=`),
because the caller already knows: only `evaluate_badges --all` sweeps everything.

These tests pin the property rather than either implementation: whichever shape is chosen the rows
must be identical, and the large-catalogue shape must not grow with the game count.
"""
import io

import pytest

from trophies.models import ConceptBundle, Game, ProfileGame, ProfileTrophyGroup, TrophyGroup
from trophies.services.badge_orchestrator import build_catalog, evaluate_with_catalog
from tests.factories import (
    BadgeSeriesFactory, ConceptFactory, GameFactory, GroupBadgeFactory, ProfileFactory,
    StageFactory, TrophyGroupFactory,
)

pytestmark = pytest.mark.django_db


def _series_with_games(n_games, *, with_dlc=False):
    """One series, one stage, `n_games` games each on their own concept.

    `with_dlc` adds a NON-default trophy group. Without one, dropping the `trophy_group_id='default'`
    filter is invisible -- and that mutation awards badges nobody earned, because base_map is keyed on
    the group's GAME id, so a finished DLC group would mark the base game complete.
    """
    series = BadgeSeriesFactory()
    stage = StageFactory(series_slug=series.series_slug, stage_number=1)
    for _ in range(n_games):
        concept = ConceptFactory()
        game = GameFactory(concept=concept, title_platform=['PS4', 'PS5'])
        TrophyGroupFactory(game=game, trophy_group_id='default')
        if with_dlc:
            TrophyGroupFactory(game=game, trophy_group_id='001')
        stage.concepts.add(concept)
    return GroupBadgeFactory(series=series)


def _add_bundle(series, stage_number=2):
    """A bundled game reaches the catalogue by a different join path than a direct stage concept."""
    stage = StageFactory(series_slug=series.series_slug, stage_number=stage_number)
    bundle = ConceptBundle.objects.create(stage=stage, label='Episodes')
    concept = ConceptFactory()
    game = GameFactory(concept=concept, title_platform=['PS4', 'PS5'])
    TrophyGroupFactory(game=game, trophy_group_id='default')
    bundle.concepts.add(concept)
    return game


def _per_profile_sql(gb, profile, *, whole_catalogue=False):
    from django.db import connection
    from django.test.utils import CaptureQueriesContext

    catalog = build_catalog([gb], whole_catalogue=whole_catalogue)
    with CaptureQueriesContext(connection) as ctx:
        evaluate_with_catalog(profile, catalog)
    return [q['sql'] for q in ctx.captured_queries]


# --- the large-catalogue shape must not grow ----------------------------------------------------

def test_the_subquery_shape_does_not_grow_with_the_game_count():
    """Compared PAIRWISE. Taking the max length across the two reads hides the failure entirely: they
    differ in length, so inlining the ids back into the shorter one leaves the max untouched."""
    profile = ProfileFactory()

    small = _per_profile_sql(_series_with_games(2), profile, whole_catalogue=True)
    large = _per_profile_sql(_series_with_games(60), profile, whole_catalogue=True)

    assert len(small) == len(large), 'the query COUNT grew with the catalogue'
    for i, (a, b) in enumerate(zip(small, large)):
        # Tight on purpose: 60 inlined ids is ~350 characters, so a generous allowance lets the
        # original bug straight back in. An earlier 200-char allowance did exactly that.
        assert len(b) < len(a) + 80, (
            f'per-profile query {i} grew from {len(a)} to {len(b)} chars for 30x the games -- the id '
            f'list is being inlined again, and this runs once per profile across the userbase'
        )


def test_the_series_slug_list_is_the_remaining_growth_axis():
    """Honest about what is NOT fixed. The slug set is still inlined, so SQL still grows with SERIES
    count even though it no longer grows with game count -- and on `--all` the slug list is the axis
    that actually scales. Recorded rather than left for the docstring to overstate."""
    from django.db import connection
    from django.test.utils import CaptureQueriesContext

    profile = ProfileFactory()
    one = _per_profile_sql(_series_with_games(2), profile, whole_catalogue=True)

    many_badges = [_series_with_games(1) for _ in range(12)]
    catalog = build_catalog(many_badges, whole_catalogue=True)
    with CaptureQueriesContext(connection) as ctx:
        evaluate_with_catalog(profile, catalog)
    many = [q['sql'] for q in ctx.captured_queries]

    assert max(len(q) for q in many) > max(len(q) for q in one), (
        'if this stops being true the slug list was hoisted server-side too, and this test should '
        'become the stronger assertion that nothing grows at all'
    )


# --- the one caller that must declare the big regime ---------------------------------------------

@pytest.mark.parametrize('args, expected, why', [
    (['--all'], True, 'the whole live catalogue across every profile is the regime the subquery exists for'),
    (['--series', 'SLUG'], False, 'one series is the small regime, same as a sync or a page render'),
])
def test_evaluate_badges_declares_its_regime(args, expected, why, monkeypatch):
    """`--all` is the only caller that sweeps everything, and nothing else knows that but it.

    Without this pin, dropping `whole_catalogue=True` from the command is invisible: every test above
    passes the flag directly, so they all still pass while the nightly run silently reverts to
    inlining the entire catalogue's ids once per profile across the userbase.
    """
    from django.core.management import call_command

    from trophies.management.commands import evaluate_badges as cmd
    from tests.factories import ProfileFactory

    gb = _series_with_games(1)
    ProfileFactory()
    seen = {}

    real = cmd.build_catalog

    def spy(group_badges, **kwargs):
        seen['whole_catalogue'] = kwargs.get('whole_catalogue', False)
        return real(group_badges, **kwargs)

    monkeypatch.setattr(cmd, 'build_catalog', spy)

    args = [a.replace('SLUG', gb.series.series_slug) for a in args]
    call_command('evaluate_badges', *args, stdout=io.StringIO(), stderr=io.StringIO())

    assert seen.get('whole_catalogue') is expected, why


# --- both shapes must select the same rows ------------------------------------------------------

@pytest.mark.parametrize('whole_catalogue', [True, False])
def test_the_two_shapes_agree_on_the_catalogue(whole_catalogue):
    gb = _series_with_games(5)
    bundled = _add_bundle(gb.series)
    catalog = build_catalog([gb], whole_catalogue=whole_catalogue)

    assert bundled.id in catalog['game_ids'], 'the fixture did not add a bundled game'

    selected = set(
        Game.objects.filter(id__in=catalog['game_filter']).values_list('id', flat=True)
    )
    assert selected == set(catalog['game_ids'])

    tg_selected = set(
        TrophyGroup.objects.filter(id__in=catalog['tg_filter']).values_list('id', flat=True)
    )
    expected = set(
        TrophyGroup.objects.filter(game_id__in=catalog['game_ids'], trophy_group_id='default')
        .values_list('id', flat=True)
    )
    assert tg_selected == expected


def test_a_bundle_game_is_reachable_through_the_subquery():
    """Bundle concepts reach games via `concept__bundles__stage`, direct ones via `concept__stages`.
    A subquery covering only the first silently drops every episodic series."""
    series = BadgeSeriesFactory()
    stage = StageFactory(series_slug=series.series_slug, stage_number=1)
    concept = ConceptFactory()
    direct = GameFactory(concept=concept, title_platform=['PS4', 'PS5'])
    TrophyGroupFactory(game=direct, trophy_group_id='default')
    stage.concepts.add(concept)
    bundled = _add_bundle(series)

    catalog = build_catalog([GroupBadgeFactory(series=series)], whole_catalogue=True)
    selected = set(
        Game.objects.filter(id__in=catalog['game_filter']).values_list('id', flat=True)
    )

    assert {direct.id, bundled.id} <= selected


# --- and the evaluation must still be right -----------------------------------------------------

@pytest.mark.parametrize('whole_catalogue', [True, False])
def test_the_base_read_is_what_decides_base_completion(whole_catalogue):
    """Pins the trophy-group read specifically.

    The first version set `progress=100` on the ProfileGame, which makes `full_complete` true -- and
    `base_complete = base_prog == 100 or full_complete`, so it passed even with the trophy-group
    filter returning nothing at all. The ProfileTrophyGroup row was dead fixture in a test named for
    reading completion state. `progress=50` forces base completion to come from the group read alone.
    """
    profile = ProfileFactory()
    gb = _series_with_games(1)
    catalog = build_catalog([gb], whole_catalogue=whole_catalogue)
    game = Game.objects.get(id=next(iter(catalog['game_ids'])))

    ProfileGame.objects.create(profile=profile, game=game, progress=50, earned_trophies_count=1)
    ProfileTrophyGroup.objects.create(
        profile=profile, trophy_group=game.trophy_groups.get(trophy_group_id='default'), progress=100,
    )

    result = evaluate_with_catalog(profile, catalog)[gb.id]

    assert result.gating_count == 1
    assert result.base_satisfied_count == 1, 'the base read did not see the completed trophy group'
    assert result.holo_satisfied_count == 0, 'the game is not at 100%, so nothing is mastered'


@pytest.mark.parametrize('whole_catalogue', [True, False])
def test_a_finished_dlc_group_does_not_complete_the_base_game(whole_catalogue):
    """`base_map` is keyed on the trophy group's GAME id, so dropping the `default` filter files a
    finished DLC group under the base game and marks it complete. Hunters would gain badges they never
    earned, with nothing to notice."""
    profile = ProfileFactory()
    gb = _series_with_games(1, with_dlc=True)
    catalog = build_catalog([gb], whole_catalogue=whole_catalogue)
    game = Game.objects.get(id=next(iter(catalog['game_ids'])))

    ProfileGame.objects.create(profile=profile, game=game, progress=40, earned_trophies_count=3)
    # The DLC group is finished; the base list is not.
    ProfileTrophyGroup.objects.create(
        profile=profile, trophy_group=game.trophy_groups.get(trophy_group_id='001'), progress=100,
    )
    ProfileTrophyGroup.objects.create(
        profile=profile, trophy_group=game.trophy_groups.get(trophy_group_id='default'), progress=40,
    )

    result = evaluate_with_catalog(profile, catalog)[gb.id]

    assert result.base_satisfied_count == 0, 'a finished DLC group completed the base game'
