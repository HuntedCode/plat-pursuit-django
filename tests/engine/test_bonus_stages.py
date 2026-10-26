"""Bonus stages replace stage 0 (owner, 2026-10-25).

Stage 0 held every optional game of a series in ONE stage, and a stage is cleared by any one of its games, so
finishing one Japan-only release cleared every other one for Mastery. It never showed on the badge page either,
so "Mastery: 1 of 2 stages" pointed at a stage nobody could see. A bonus stage is an ordinary numbered stage with
`Stage.is_bonus`: one per title, shown in its own section, never toward the badge (no gating, no holo, no XP),
only toward Mastery. Migration 0355 moved each stage 0 to the end of its series as one.
"""
import importlib

import pytest
from django.apps import apps as global_apps
from django.db import IntegrityError, connection, transaction

from trophies.models import Stage
from trophies.services.badge_apply import evaluate_and_apply
from trophies.services.badge_detail_service import get_badge_detail
from tests.engine.test_badge_mastery_page import _page
from tests.engine.test_badge_rewards_apply import BONUS, ULTRA, _finish, _hold, _hunter, _series
from tests.factories import ConceptFactory, GameFactory, StageFactory

pytestmark = pytest.mark.django_db
MIGRATION = importlib.import_module('trophies.migrations.0355_bonus_stages')


# -- the migration -----------------------------------------------------------------------------------------------

@pytest.fixture
def stage_zero_allowed():
    """The data as it was before 0355: lift the stage-number check for this test only (Postgres DDL is
    transactional, so the test's own rollback puts it back)."""
    constraint = next(c for c in Stage._meta.constraints if c.name == 'stage_number_from_one')
    with connection.schema_editor() as editor:
        editor.remove_constraint(Stage, constraint)


def test_a_stage_zero_moves_after_the_last_stage_as_a_bonus_with_its_games(stage_zero_allowed):
    zero = StageFactory(series_slug='jp', stage_number=0, title='Japan only')
    games = [ConceptFactory(), ConceptFactory()]
    zero.concepts.set(games)
    for n in (1, 2):
        StageFactory(series_slug='jp', stage_number=n)
    StageFactory(series_slug='other', stage_number=1)

    MIGRATION.stage_zero_to_bonus(global_apps, None)

    zero.refresh_from_db()
    assert (zero.stage_number, zero.is_bonus, zero.title) == (3, True, 'Japan only')
    assert set(zero.concepts.all()) == set(games), 'its games travel with it, for the owner to split by hand'
    assert list(Stage.objects.filter(is_bonus=True)) == [zero], 'nothing else turns bonus'


def test_a_series_with_only_a_stage_zero_starts_at_one(stage_zero_allowed):
    zero = StageFactory(series_slug='lonely', stage_number=0)
    MIGRATION.stage_zero_to_bonus(global_apps, None)
    zero.refresh_from_db()
    assert (zero.stage_number, zero.is_bonus) == (1, True)


def test_the_reverse_puts_each_series_bonus_stage_back_at_zero(stage_zero_allowed):
    bonus = StageFactory(series_slug='one', stage_number=3, is_bonus=True)
    MIGRATION.bonus_to_stage_zero(global_apps, None)
    bonus.refresh_from_db()
    assert bonus.stage_number == 0


def test_the_reverse_refuses_a_series_split_into_several_bonus_stages(stage_zero_allowed):
    """All but one would become required stages once the flag is dropped, revoking holds."""
    StageFactory(series_slug='split', stage_number=3, is_bonus=True)
    StageFactory(series_slug='split', stage_number=4, is_bonus=True)
    with pytest.raises(RuntimeError, match='split'):
        MIGRATION.bonus_to_stage_zero(global_apps, None)


def test_the_database_refuses_a_stage_zero():
    with pytest.raises(IntegrityError), transaction.atomic():
        StageFactory(series_slug='nope', stage_number=0)


# -- the engine through the database ---------------------------------------------------------------------------

def test_two_bonus_titles_are_two_stages_so_one_never_clears_the_other():
    """The stage 0 bug: both Japan-only games in one stage, so finishing either mastered the badge."""
    _, badges, games = _series((1, [ULTRA]), (BONUS, [ULTRA]), legacy=False)
    other = StageFactory(series_slug=badges['ultra-hd'].series.series_slug, stage_number=98, is_bonus=True)
    concept = ConceptFactory()
    other.concepts.add(concept)
    GameFactory(concept=concept, title_platform=ULTRA)
    hunter = _hunter()
    _finish(hunter, games[1][0])
    _finish(hunter, games[BONUS][0])

    evaluate_and_apply(hunter)
    hold = _hold(hunter, badges['ultra-hd'])
    assert hold.is_holo and hold.mastered_at is None, 'one bonus title of two is not every stage'


# -- the badge page ------------------------------------------------------------------------------------------------

def test_the_page_shows_bonus_stages_in_their_own_section_unnumbered(client):
    series, badges, games = _series((1, [ULTRA]), (2, [ULTRA]), (BONUS, [ULTRA]), legacy=False)
    hunter = _hunter()
    _finish(hunter, games[1][0])
    _finish(hunter, games[2][0], full=False)
    evaluate_and_apply(hunter)

    body = _page(client, series, hunter)
    assert '<h3 class="bd-fence__title">Bonus &middot; 1 stage</h3>' in body
    assert "These don&rsquo;t count toward the badge, only toward Mastery." in body
    assert games[BONUS][0].title_name in body
    assert 'Bonus &middot; toward Mastery' in body and 'bd-node--bonus' in body
    assert 'Stage 3' not in body, 'a bonus stage is not a step of the badge, so it has no number'
    assert 'Mastery: 1 of 3 stages at 100%' in body, 'the count names stages the hunter can see'
    bonus_at, retired_at = body.index('bd-fence--bonus'), body.find('No longer obtainable &middot;')
    assert retired_at == -1 or bonus_at < retired_at


def test_a_cleared_bonus_stage_reads_cleared(client):
    """Audit: the page read the engine's `stages`, which leave bonus stages out, so a bonus at 100% still drew
    as to-do under a hero that said Mastered."""
    series, _, games = _series((1, [ULTRA]), (BONUS, [ULTRA]), legacy=False)
    hunter = _hunter()
    _finish(hunter, games[1][0])
    _finish(hunter, games[BONUS][0])
    evaluate_and_apply(hunter)
    [g] = get_badge_detail(series, hunter).groups
    [b] = g.bonus_stages
    assert b['completion_state'] == 'complete' and b['is_mastered']
    body = _page(client, series, hunter)
    fence = body[body.index('bd-fence--bonus'):]
    assert 'bd-node--done' in fence and 'Mastered &middot;' in body


def test_my_stats_counts_its_stage_split_over_the_ladder_it_is_out_of():
    """Audit: one counting stage and a bonus, both platted, read "2 of 1" in My Stats."""
    series, _, games = _series((1, [ULTRA]), (BONUS, [ULTRA]), legacy=False)
    hunter = _hunter()
    _finish(hunter, games[1][0])
    _finish(hunter, games[BONUS][0])
    evaluate_and_apply(hunter)
    [g] = get_badge_detail(series, hunter).groups
    assert (g.user_stats['stages_platted'], g.gating_count) == (1, 1)
    assert g.user_stats['games_platted'] == 2, 'the bonus game is still counted as a game on screen'


def test_a_bonus_stage_is_not_on_the_ladder_or_priced():
    from trophies.services.badge_xp import XP_BADGE_COMPLETION_BONUS, XP_PER_STAGE

    series, _, _ = _series((1, [ULTRA]), (BONUS, [ULTRA]), legacy=False)
    [g] = get_badge_detail(series, None).groups
    assert [s['stage'].stage_number for s in g.stages] == [1]
    assert [s['stage'].stage_number for s in g.bonus_stages] == [99]
    assert g.gating_count == 1
    assert g.xp_on_offer == XP_PER_STAGE + XP_BADGE_COMPLETION_BONUS, 'a bonus stage pays nothing'


def test_a_bonus_stage_whose_game_went_unobtainable_is_still_a_bonus():
    series, _, games = _series((1, [ULTRA]), (BONUS, [ULTRA]), legacy=False)
    games[BONUS][0].is_obtainable = False
    games[BONUS][0].save()
    [g] = get_badge_detail(series, None).groups
    assert len(g.bonus_stages) == 1 and g.retired_stages == []


def test_admin_shows_and_filters_the_bonus_flag():
    from trophies.admin import StageAdmin

    assert 'is_bonus' in StageAdmin.list_display and 'is_bonus' in StageAdmin.list_filter
