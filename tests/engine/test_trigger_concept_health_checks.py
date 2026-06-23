"""Tests for the trigger_concept_health_checks management command.

The command resolves a concept for every concept-less Game: it tries the IGDB
anchor first and falls back to a PP_ stub so no Game is left without a concept.
It runs inline, no PSN calls or worker required.
"""

import pytest
from django.core.management import call_command

from trophies.models import Game
from tests.factories import (
    ConceptFactory,
    GameFactory,
    ProfileFactory,
    ProfileGameFactory,
)

pytestmark = pytest.mark.django_db

ANCHOR_TARGET = "trophies.management.commands.trigger_concept_health_checks.try_anchor_new_game"


@pytest.fixture
def patched_redis(monkeypatch):
    """Point create_default_concept's redis client at an in-memory fake so the
    PP_ stub counter works without a live Redis."""
    import fakeredis

    client = fakeredis.FakeStrictRedis()
    monkeypatch.setattr("trophies.util_modules.cache.redis_client", client)
    return client


def _conceptless_game(**kwargs):
    return GameFactory(concept=None, **kwargs)


def test_anchors_game_when_igdb_matches(monkeypatch):
    game = _conceptless_game()
    target = ConceptFactory()

    def fake_anchor(g):
        # Mirror the real entry point: it assigns the concept to the Game.
        g.concept = target
        g.save(update_fields=["concept"])
        return target

    monkeypatch.setattr(ANCHOR_TARGET, fake_anchor)

    call_command("trigger_concept_health_checks")

    game.refresh_from_db()
    # Game.concept_id is the FK (Concept PK), distinct from Concept.concept_id
    # (a string field), so compare the related object directly.
    assert game.concept == target


def test_stub_fallback_when_no_igdb_match(monkeypatch, patched_redis):
    game = _conceptless_game(title_name="Stubby", title_platform=["PS5"])
    monkeypatch.setattr(ANCHOR_TARGET, lambda g: None)

    call_command("trigger_concept_health_checks")

    game.refresh_from_db()
    assert game.concept is not None
    assert game.concept.concept_id.startswith("PP_")


def test_dry_run_resolves_nothing(monkeypatch):
    game = _conceptless_game()
    calls = {"n": 0}

    def fake_anchor(g):
        calls["n"] += 1
        return None

    monkeypatch.setattr(ANCHOR_TARGET, fake_anchor)

    call_command("trigger_concept_health_checks", dry_run=True)

    game.refresh_from_db()
    assert game.concept is None
    assert calls["n"] == 0  # the anchor is never invoked in a dry run


def test_skips_games_that_already_have_a_concept(monkeypatch):
    GameFactory()  # default factory gives it a concept

    def fake_anchor(g):
        raise AssertionError("try_anchor_new_game must not run for concepted games")

    monkeypatch.setattr(ANCHOR_TARGET, fake_anchor)

    # No exception => the concepted game was excluded from the queryset.
    call_command("trigger_concept_health_checks")


def test_profile_id_scopes_to_owned_games(monkeypatch, patched_redis):
    owned = _conceptless_game(title_platform=["PS5"])
    unowned = _conceptless_game(title_platform=["PS5"])
    profile = ProfileFactory()
    ProfileGameFactory(profile=profile, game=owned)

    monkeypatch.setattr(ANCHOR_TARGET, lambda g: None)

    call_command("trigger_concept_health_checks", profile_id=profile.id)

    owned.refresh_from_db()
    unowned.refresh_from_db()
    assert owned.concept is not None  # stubbed
    assert unowned.concept is None  # out of scope, untouched


def test_one_failure_does_not_abort_the_batch(monkeypatch, patched_redis):
    failing = _conceptless_game(title_name="Boom", title_platform=["PS5"])
    surviving = _conceptless_game(title_name="Fine", title_platform=["PS5"])

    def fake_anchor(g):
        if g.pk == failing.pk:
            raise RuntimeError("IGDB exploded")
        return None  # surviving game falls through to the stub path

    monkeypatch.setattr(ANCHOR_TARGET, fake_anchor)

    # Must not raise: per-game exception isolation keeps the batch going.
    call_command("trigger_concept_health_checks")

    failing.refresh_from_db()
    surviving.refresh_from_db()
    assert failing.concept is None  # failed, left for a later run
    assert surviving.concept is not None  # still resolved despite the earlier failure


def test_concurrent_placement_skips_stub(monkeypatch):
    game = _conceptless_game(title_platform=["PS5"])
    winner = ConceptFactory()

    def fake_anchor(g):
        # Simulate another process placing the concept after the anchor returns
        # None but before our stub guard re-reads the row.
        Game.objects.filter(pk=g.pk).update(concept=winner)
        return None

    monkeypatch.setattr(ANCHOR_TARGET, fake_anchor)

    call_command("trigger_concept_health_checks")

    game.refresh_from_db()
    # The concurrent placement is kept; no PP_ stub overwrites it.
    assert game.concept == winner


def test_limit_caps_processing(monkeypatch, patched_redis):
    games = [_conceptless_game(title_platform=["PS5"]) for _ in range(3)]
    monkeypatch.setattr(ANCHOR_TARGET, lambda g: None)

    call_command("trigger_concept_health_checks", limit=1)

    resolved = sum(
        1 for g in Game.objects.filter(pk__in=[g.pk for g in games]) if g.concept_id
    )
    assert resolved == 1
