"""Concept-less Game placement: the shared anchor-or-stub service, the concept_lock rule, and the
sync wiring that runs it.

Regression context (Nov 2026): 722 Games were left with no Concept. A slow-path walk SAVES new
Games before placing them, and placement was sync_complete's job alone. A walk that aborted after
saving them (a private profile, a PSN NotFound, an outage) never reached sync_complete, and neither
recovery path ran it, so the Games stayed concept-less until the hunter next synced cleanly.

token_keeper cannot be imported under test (the module instantiates its singleton, which needs live
TOKEN_GROUPS), so its wiring is pinned by AST, the convention test_psn_title_observations set.
"""
import ast
import pathlib

import pytest

from tests.factories import ConceptFactory, GameFactory, ProfileFactory, ProfileGameFactory
from trophies.models import Concept, Game
from trophies.services.concept_anchor_service import (
    PLACED_ANCHORED,
    PLACED_ELSEWHERE,
    PLACED_STUBBED,
    place_concept_less_game,
    place_profile_orphan_games,
)

pytestmark = pytest.mark.django_db

ANCHOR_TARGET = 'trophies.services.concept_anchor_service.try_anchor_new_game'


@pytest.fixture
def stub_redis(monkeypatch):
    """create_default_concept's PP_ counter lives in Redis."""
    import fakeredis

    monkeypatch.setattr('trophies.util_modules.cache.redis_client', fakeredis.FakeStrictRedis())


def _orphan(**kwargs):
    kwargs.setdefault('title_platform', ['PS4'])
    return GameFactory(concept=None, **kwargs)


def _owned_orphan(profile, **kwargs):
    game = _orphan(**kwargs)
    ProfileGameFactory(profile=profile, game=game)
    return game


# --- place_concept_less_game ------------------------------------------------


def test_a_clean_igdb_match_anchors_the_game(monkeypatch):
    game = _orphan()
    target = ConceptFactory()

    def fake_anchor(g):
        g.add_concept(target)
        return target

    monkeypatch.setattr(ANCHOR_TARGET, fake_anchor)

    outcome, concept = place_concept_less_game(game)

    game.refresh_from_db()
    assert (outcome, concept, game.concept) == (PLACED_ANCHORED, target, target)


def test_no_match_falls_back_to_a_stub_named_from_the_game(monkeypatch, stub_redis):
    game = _orphan(title_name='Rising Dusk')
    monkeypatch.setattr(ANCHOR_TARGET, lambda g: None)

    outcome, concept = place_concept_less_game(game)

    game.refresh_from_db()
    assert outcome == PLACED_STUBBED
    assert game.concept == concept
    assert concept.concept_id.startswith('PP_')
    assert concept.unified_title == 'Rising Dusk (PS4)'


def test_a_concurrent_placement_is_kept_and_no_stub_is_minted(monkeypatch):
    game = _orphan()
    winner = ConceptFactory()

    def fake_anchor(g):
        Game.objects.filter(pk=g.pk).update(concept=winner)  # another worker placed it
        return None

    monkeypatch.setattr(ANCHOR_TARGET, fake_anchor)
    stubs_before = Concept.objects.filter(concept_id__startswith='PP_').count()

    outcome, concept = place_concept_less_game(game)

    game.refresh_from_db()
    assert (outcome, concept, game.concept) == (PLACED_ELSEWHERE, winner, winner)
    assert Concept.objects.filter(concept_id__startswith='PP_').count() == stubs_before


def test_a_placement_that_lands_while_the_stub_is_minted_wins_and_the_stub_is_dropped(
    monkeypatch, stub_redis,
):
    # The abort handler can run while sync_title_id jobs place these same Games. A job that
    # attaches between the FK re-read and the attach must keep its concept.
    game = _orphan()
    winner = ConceptFactory()
    monkeypatch.setattr(ANCHOR_TARGET, lambda g: None)
    real_mint = Concept.create_default_concept

    def mint_then_lose_the_race(g):
        stub = real_mint(g)
        Game.objects.filter(pk=g.pk).update(concept=winner)  # a sync_title_id job lands here
        return stub

    monkeypatch.setattr(Concept, 'create_default_concept', mint_then_lose_the_race)

    outcome, concept = place_concept_less_game(game)

    game.refresh_from_db()
    assert (outcome, concept, game.concept) == (PLACED_ELSEWHERE, winner, winner)
    assert not Concept.objects.filter(concept_id__startswith='PP_').exists(), 'the losing stub was left behind'


# --- concept_lock ------------------------------------------------------------


def test_a_locked_game_with_no_concept_still_accepts_one(monkeypatch, stub_redis):
    # The lock guards an existing assignment. Before the fix, add_concept no-oped here, so every
    # reconcile minted a stub that never attached and the game stayed concept-less forever.
    game = _orphan(concept_lock=True)
    monkeypatch.setattr(ANCHOR_TARGET, lambda g: None)

    outcome, concept = place_concept_less_game(game)

    game.refresh_from_db()
    assert outcome == PLACED_STUBBED
    assert game.concept == concept


def test_the_lock_still_protects_an_existing_assignment():
    original = ConceptFactory()
    game = GameFactory(concept=original, concept_lock=True)

    game.add_concept(ConceptFactory())

    game.refresh_from_db()
    assert game.concept == original


def test_force_still_overrides_the_lock():
    game = GameFactory(concept=ConceptFactory(), concept_lock=True)
    target = ConceptFactory()

    game.add_concept(target, force=True)

    game.refresh_from_db()
    assert game.concept == target


# --- place_profile_orphan_games -----------------------------------------------


def test_places_only_the_profiles_own_orphans(monkeypatch, stub_redis):
    profile = ProfileFactory()
    owned = _owned_orphan(profile)
    someone_elses = _owned_orphan(ProfileFactory())
    monkeypatch.setattr(ANCHOR_TARGET, lambda g: None)

    assert place_profile_orphan_games(profile) == 1

    owned.refresh_from_db()
    someone_elses.refresh_from_db()
    assert owned.concept is not None
    assert someone_elses.concept is None


def test_on_stub_runs_for_stubs_only(monkeypatch, stub_redis):
    profile = ProfileFactory()
    anchored_game = _owned_orphan(profile)
    _owned_orphan(profile)
    target = ConceptFactory()

    def fake_anchor(g):
        if g.pk != anchored_game.pk:
            return None
        g.add_concept(target)
        return target

    monkeypatch.setattr(ANCHOR_TARGET, fake_anchor)
    stubs = []

    assert place_profile_orphan_games(profile, on_stub=stubs.append) == 2
    assert len(stubs) == 1 and stubs[0].concept_id.startswith('PP_')


def test_one_failing_game_costs_only_itself(monkeypatch, stub_redis):
    profile = ProfileFactory()
    failing = _owned_orphan(profile)
    surviving = _owned_orphan(profile)

    def fake_anchor(g):
        if g.pk == failing.pk:
            raise RuntimeError('IGDB exploded')
        return None

    monkeypatch.setattr(ANCHOR_TARGET, fake_anchor)

    assert place_profile_orphan_games(profile) == 1

    failing.refresh_from_db()
    surviving.refresh_from_db()
    assert failing.concept is None
    assert surviving.concept is not None


def test_limit_places_the_oldest_orphans_first(monkeypatch, stub_redis):
    profile = ProfileFactory()
    first, second = _owned_orphan(profile), _owned_orphan(profile)
    monkeypatch.setattr(ANCHOR_TARGET, lambda g: None)

    assert place_profile_orphan_games(profile, limit=1) == 1

    first.refresh_from_db()
    second.refresh_from_db()
    assert first.concept is not None
    assert second.concept is None


def test_a_profile_with_no_orphans_is_a_no_op(monkeypatch):
    profile = ProfileFactory()
    ProfileGameFactory(profile=profile, game=GameFactory())

    def fake_anchor(g):
        raise AssertionError('a placed game must never be re-placed')

    monkeypatch.setattr(ANCHOR_TARGET, fake_anchor)

    assert place_profile_orphan_games(profile) == 0


# --- token_keeper wiring (AST pins) ---------------------------------------------


def _function(name):
    tree = ast.parse(pathlib.Path('trophies/token_keeper.py').read_text(encoding='utf-8'))
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return node
    raise AssertionError(f'{name} not found in token_keeper.py -- renamed or moved; retarget this test')


def _calls(node, name):
    """Calls to `name(...)` or `<anything>.name(...)` anywhere under node."""
    return [
        c for c in ast.walk(node)
        if isinstance(c, ast.Call) and (
            (isinstance(c.func, ast.Name) and c.func.id == name)
            or (isinstance(c.func, ast.Attribute) and c.func.attr == name)
        )
    ]


def _catches_exception(handler):
    return isinstance(handler.type, ast.Name) and handler.type.id == 'Exception'


def test_a_slow_path_abort_places_stranded_games_then_re_raises():
    refresh = _function('_job_profile_refresh')
    guards = [
        t for t in ast.walk(refresh)
        if isinstance(t, ast.Try) and any(_calls(s, '_profile_refresh_slow_path') for s in t.body)
    ]
    assert guards, '_profile_refresh_slow_path is no longer wrapped: an abort strands its new Games again'
    handler = next((h for h in guards[0].handlers if _catches_exception(h)), None)
    assert handler is not None, 'the slow-path guard must catch Exception (outages included)'
    assert _calls(handler, '_place_stranded_games'), 'the abort handler no longer places stranded Games'
    assert isinstance(handler.body[-1], ast.Raise) and handler.body[-1].exc is None, (
        'the abort handler must re-raise the original error for the worker loop'
    )


def test_stranded_placement_never_raises():
    outer = _function('_place_stranded_games').body[-1]
    handler = next((h for h in getattr(outer, 'handlers', []) if _catches_exception(h)), None)
    assert isinstance(outer, ast.Try) and handler is not None, (
        '_place_stranded_games runs inside an except block; an escaping error would replace the original'
    )
    assert not any(isinstance(n, ast.Raise) for n in ast.walk(handler)), 'its handler must swallow, not re-raise'


def test_stranded_placement_is_capped():
    calls = _calls(_function('_place_stranded_games'), 'place_profile_orphan_games')
    assert calls and any(k.arg == 'limit' for k in calls[0].keywords), (
        'an initial sync that dies strands its whole library; uncapped, one worker runs IGDB for all of it'
    )


def test_stranded_placement_drains_only_when_no_jobs_are_in_flight():
    # In-flight sync_title_id jobs defer concepts too; draining them early enriches before their
    # sibling Games attach. The drain must sit under a pending-jobs check.
    place = _function('_place_stranded_games')
    gated = [
        n for n in ast.walk(place)
        if isinstance(n, ast.If) and _calls(n.test, '_get_current_jobs_for_profile')
        and any(_calls(s, '_drain_deferred_igdb_enrich') for s in n.body)
    ]
    assert gated, 'the drain is no longer gated on the profile having no jobs in flight'
    assert len(_calls(place, '_drain_deferred_igdb_enrich')) == 1, 'an ungated drain was added beside the gated one'


def test_sync_complete_still_reconciles_orphans():
    assert _calls(_function('_job_sync_complete'), 'place_profile_orphan_games')


# --- Game.concept is PROTECT ------------------------------------------------------


def test_deleting_a_concept_that_still_has_games_raises():
    # Under SET_NULL this silently orphaned the Games (an admin delete skipped absorb() entirely).
    from django.db.models import ProtectedError

    concept = ConceptFactory()
    game = GameFactory(concept=concept)

    with pytest.raises(ProtectedError):
        concept.delete()
    game.refresh_from_db()
    assert game.concept == concept


def test_an_empty_concept_still_deletes():
    concept = ConceptFactory()

    concept.delete()

    assert not Concept.objects.filter(pk=concept.pk).exists()


def test_reassigning_a_games_last_concept_still_absorbs_and_deletes_it():
    # add_concept moves the Game off BEFORE deleting, so PROTECT never fires on the normal path.
    old, new = ConceptFactory(), ConceptFactory()
    game = GameFactory(concept=old)

    game.add_concept(new)

    assert not Concept.objects.filter(pk=old.pk).exists()


# --- token_keeper placement goes through the shared function ----------------------


def test_token_keeper_has_no_inline_anchor_or_stub_copies():
    """Every anchor-or-stub site uses place_concept_less_game. What may remain: the PSN-details branch
    (anchors or falls back to a PSN concept, never a stub) and sync_title_id's exception recovery,
    which mints a bare stub on purpose because the exception it recovers from may be IGDB's."""
    tree = ast.parse(pathlib.Path('trophies/token_keeper.py').read_text(encoding='utf-8'))
    assert len(_calls(tree, 'try_anchor_new_game')) == 1, 'an inline anchor-or-stub copy came back'
    assert len(_calls(tree, 'create_default_concept')) == 1, 'an inline stub mint came back'
    assert len(_calls(tree, 'place_concept_less_game')) >= 2


def test_merge_in_keeps_a_concept_that_gained_a_game_instead_of_raising(monkeypatch):
    # The race PROTECT turns loud: a Game attaches between the caller's empty-check and the delete.
    # absorb() has already run and cannot roll back, so the concept is kept (it is not orphaned) and
    # the sync that triggered the merge carries on instead of failing after its own save.
    survivor, doomed = ConceptFactory(), ConceptFactory()
    late = GameFactory(concept=None)
    real_absorb = Concept.absorb

    def absorb_then_race(self, other):
        real_absorb(self, other)
        Game.objects.filter(pk=late.pk).update(concept=other)

    monkeypatch.setattr(Concept, 'absorb', absorb_then_race)

    assert survivor.merge_in(doomed) is False
    assert Concept.objects.filter(pk=doomed.pk).exists()


def test_merge_in_deletes_an_empty_concept():
    survivor, doomed = ConceptFactory(), ConceptFactory()

    assert survivor.merge_in(doomed) is True
    assert not Concept.objects.filter(pk=doomed.pk).exists()
