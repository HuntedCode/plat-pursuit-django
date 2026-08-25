"""The per-profile reads in `evaluate_with_catalog` must have CONSTANT-SIZE SQL.

This is an invariant test, not a regression test: it is written from the requirement rather than from
any particular bug, which is the thing that was missing when this subsystem was fixed three times.

THE REQUIREMENT. `evaluate_badges --all` runs `evaluate_with_catalog` once per profile with a PSN
username, ~300,000 of them. Anything those two queries carry per-element is multiplied by 300,000.
They passed the catalogue's game-id collection into `__in`, and Django renders that as one bound
parameter per element -- so each profile re-sent a statement carrying every game in the live badge
catalogue. Django sets `prepare_threshold=None`, so prepared statements are off and every one of
those was parsed and planned from scratch.

So the invariant is: **the number of bound parameters in a per-profile read must not grow with the
catalogue.** Not "should be small" -- must not GROW. That is a property a test can hold onto, and a
count-based assertion would rot the moment the catalogue did.
"""
import pytest

from trophies.services.badge_orchestrator import build_catalog, evaluate_with_catalog
from tests.factories import (
    BadgeSeriesFactory, ConceptFactory, GameFactory, GroupBadgeFactory, ProfileFactory,
    StageFactory, TrophyGroupFactory,
)

pytestmark = pytest.mark.django_db


def _series_with_games(n_games):
    """One series whose single stage carries `n_games` games, each on its own concept."""
    series = BadgeSeriesFactory()
    stage = StageFactory(series_slug=series.series_slug, stage_number=1)
    for _ in range(n_games):
        concept = ConceptFactory()
        game = GameFactory(concept=concept, title_platform=['PS4', 'PS5'])
        TrophyGroupFactory(game=game, trophy_group_id='default')
        stage.concepts.add(concept)
    return GroupBadgeFactory(series=series)


def _per_profile_sql(gb, profile):
    """The SQL the two per-profile reads actually issue."""
    from django.db import connection
    from django.test.utils import CaptureQueriesContext

    catalog = build_catalog([gb])
    with CaptureQueriesContext(connection) as ctx:
        evaluate_with_catalog(profile, catalog)
    return [q['sql'] for q in ctx.captured_queries]


def test_the_per_profile_sql_does_not_grow_with_the_catalogue():
    """THE invariant. A catalogue ten times the size must not produce a longer statement.

    Measured as a comparison rather than a threshold: any fixed number would either be wrong for a
    real catalogue or stop meaning anything as the catalogue grew.
    """
    profile = ProfileFactory()

    small = _per_profile_sql(_series_with_games(2), profile)
    large = _per_profile_sql(_series_with_games(60), profile)

    assert len(small) == len(large), 'the query COUNT grew with the catalogue'

    # PAIRWISE, not max. Comparing the largest query in each set hides the failure entirely: these two
    # reads differ in length, so inlining the ids back into the SHORTER one leaves the max untouched
    # and the assertion green. That is what the first version of this test did.
    for i, (a, b) in enumerate(zip(small, large)):
        # Slack is deliberately tight. 60 inlined ids is ~350 characters, so a generous allowance
        # would let the original bug straight back in.
        assert len(b) < len(a) + 80, (
            f'per-profile query {i} grew from {len(a)} to {len(b)} chars for 30x the games -- the id '
            f'list is being inlined again, and this runs once per profile across the whole userbase'
        )


def test_the_evaluation_still_reads_the_right_completion_state():
    """The invariant above is worthless if the subquery selects a different set than the Python one
    did. This pins the OUTCOME, so a subquery whose joins are subtly wrong shows up as a wrong
    evaluation rather than as a fast wrong answer."""
    from trophies.models import ProfileGame, ProfileTrophyGroup

    profile = ProfileFactory()
    gb = _series_with_games(3)
    catalog = build_catalog([gb])

    game = ProfileGame.objects.none()
    from trophies.models import Game
    games = list(Game.objects.filter(id__in=catalog['game_ids']).order_by('id'))
    assert len(games) == 3, 'the catalogue itself is wrong, before any subquery is involved'

    # Complete the base list on one game.
    ProfileGame.objects.create(profile=profile, game=games[0], progress=100,
                               earned_trophies_count=1)
    tg = games[0].trophy_groups.get(trophy_group_id='default')
    ProfileTrophyGroup.objects.create(profile=profile, trophy_group=tg, progress=100)

    results = evaluate_with_catalog(profile, catalog)

    result = results[gb.id]
    assert result.gating_count == 1, 'one stage gates this edition'
    assert result.base_satisfied_count == 1, 'the completed game was not seen through the subquery'


def test_a_bundle_game_is_reachable_through_the_subquery():
    """Bundle concepts reach games by a different path (`concept__bundles__stage`) than direct stage
    concepts (`concept__stages`). The Python walk covered both; a subquery that covers only the first
    silently drops every episodic series, and nothing else in the suite would notice."""
    from trophies.models import ConceptBundle, Game

    series = BadgeSeriesFactory()
    stage = StageFactory(series_slug=series.series_slug, stage_number=1)
    bundle = ConceptBundle.objects.create(stage=stage, label='Episodes')
    concept = ConceptFactory()
    bundled = GameFactory(concept=concept, title_platform=['PS4', 'PS5'])
    TrophyGroupFactory(game=bundled, trophy_group_id='default')
    bundle.concepts.add(concept)

    gb = GroupBadgeFactory(series=series)
    catalog = build_catalog([gb])

    assert bundled.id in catalog['game_ids'], 'the Python walk missed the bundle'
    assert bundled.id in set(
        Game.objects.filter(id__in=catalog['game_ids_qs']).values_list('id', flat=True)
    ), 'the subquery missed the bundle, so every episodic series evaluates as empty'


def test_both_id_sets_agree():
    """The Python collection and its subquery twin must select the same rows. They are built by
    different traversals of the same graph, so nothing but a test keeps them honest.

    The fixture carries a BUNDLE as well as direct stage concepts. Without one the two traversals
    cannot disagree, and this passed with the bundle arm of the subquery deleted.
    """
    from trophies.models import ConceptBundle, Game, TrophyGroup

    gb = _series_with_games(5)
    stage = StageFactory(series_slug=gb.series.series_slug, stage_number=2)
    bundle = ConceptBundle.objects.create(stage=stage, label='Episodes')
    concept = ConceptFactory()
    bundled = GameFactory(concept=concept, title_platform=['PS4', 'PS5'])
    TrophyGroupFactory(game=bundled, trophy_group_id='default')
    bundle.concepts.add(concept)

    catalog = build_catalog([gb])
    assert bundled.id in catalog['game_ids'], 'the fixture did not actually add a bundled game'

    from_qs = set(Game.objects.filter(id__in=catalog['game_ids_qs']).values_list('id', flat=True))
    assert from_qs == set(catalog['game_ids'])

    tg_from_qs = set(
        TrophyGroup.objects.filter(id__in=catalog['default_tg_qs']).values_list('id', flat=True)
    )
    assert tg_from_qs == set(catalog['default_tg_ids'])
