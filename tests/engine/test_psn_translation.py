"""Spine tests for the PSN-to-ORM translation layer (PsnApiService).

The sync pipeline's network calls live in token_keeper; PsnApiService is the
translation layer beneath it. Its classmethods take already-fetched PSN library
objects (TrophyTitle, trophy data, etc.) and write Game / ProfileGame / Trophy /
EarnedTrophy rows. That makes it the most valuable *and* most testable slice of
sync: no HTTP, no Redis, no threads, just duck-typed PSN inputs -> asserted DB.

`update_profilegame_stats` is the bridge to badge eval: it derives
ProfileGame.has_plat (and the trophy counts) from EarnedTrophy rows, which is
exactly the signal badge evaluation reads.

PSN objects are faked with SimpleNamespace shaped to the attributes each method
touches (verified against psn_api_service.py).
"""

import logging
from types import SimpleNamespace

import pytest
from django.utils import timezone

from trophies.models import EarnedTrophy, Game, ProfileGame, Trophy
from trophies.services.psn_api_service import PsnApiService
from trophies.sync_utils import sync_signal_suppressor
from tests.factories import GameFactory, ProfileFactory, ProfileGameFactory, TrophyFactory

pytestmark = pytest.mark.django_db


# --- fake PSN objects ---------------------------------------------------------


def _counts(bronze=0, silver=0, gold=0, platinum=0):
    return SimpleNamespace(bronze=bronze, silver=silver, gold=gold, platinum=platinum)


def fake_trophy_title(**overrides):
    data = dict(
        np_communication_id="NPWR00001_00",
        np_service_name="trophy2",
        trophy_set_version="01.00",
        title_name="Test Game",
        title_detail="Detail",
        title_icon_url="http://example.com/icon.png",
        title_platform=[SimpleNamespace(value="PS5")],
        has_trophy_groups=False,
        defined_trophies=_counts(bronze=10, silver=5, gold=2, platinum=1),
        # used by create_or_update_profile_game:
        progress=0,
        hidden_flag=False,
        earned_trophies=_counts(),
        last_updated_datetime=timezone.now(),
    )
    data.update(overrides)
    return SimpleNamespace(**data)


def fake_trophy_data(**overrides):
    data = dict(
        trophy_id=1,
        trophy_set_version="01.00",
        trophy_type=SimpleNamespace(value="bronze"),
        trophy_name="A Trophy",
        trophy_detail="Do the thing",
        trophy_icon_url="http://example.com/t.png",
        trophy_group_id="default",
        trophy_progress_target_value=None,
        trophy_reward_name=None,
        trophy_reward_img_url=None,
        trophy_rarity=SimpleNamespace(value=3),
        trophy_earn_rate=50.0,
        # used by create_or_update_earned_trophy_from_trophy_data:
        earned=False,
        trophy_hidden=False,
        progress=None,
        progress_rate=None,
        progressed_date_time=None,
        earned_date_time=None,
    )
    data.update(overrides)
    return SimpleNamespace(**data)


# --- Game translation ---------------------------------------------------------


def test_create_or_update_game_creates_with_mapped_fields():
    tt = fake_trophy_title()

    game, created, needs_trophy_update = PsnApiService.create_or_update_game(tt)

    assert created is True
    assert needs_trophy_update is True  # new game always needs trophies
    assert game.np_communication_id == "NPWR00001_00"
    assert game.title_name == "Test Game"
    assert game.title_platform == ["PS5"]
    assert game.defined_trophies == {"bronze": 10, "silver": 5, "gold": 2, "platinum": 1}


def test_create_or_update_game_flags_trophy_update_on_version_change():
    PsnApiService.create_or_update_game(fake_trophy_title(trophy_set_version="01.00"))

    _, created, needs_trophy_update = PsnApiService.create_or_update_game(
        fake_trophy_title(trophy_set_version="02.00")
    )

    assert created is False
    assert needs_trophy_update is True  # version bumped -> trophies must refresh


def test_create_or_update_game_respects_lock_title():
    game = GameFactory(
        np_communication_id="NPWR00001_00", title_name="Curated Name", lock_title=True
    )

    PsnApiService.create_or_update_game(fake_trophy_title(title_name="PSN Raw Name"))

    game.refresh_from_db()
    assert game.title_name == "Curated Name"  # lock_title preserved the admin value


# --- ProfileGame translation --------------------------------------------------


def test_create_or_update_profile_game_maps_fields():
    profile = ProfileFactory()
    game = GameFactory()
    tt = fake_trophy_title(
        progress=42, hidden_flag=False, earned_trophies=_counts(bronze=3, platinum=0)
    )

    pg, created, drifted = PsnApiService.create_or_update_profile_game(profile, game, tt)

    assert created is True
    assert drifted == []  # a fresh row was written by the create, not by the drift path
    assert pg.progress == 42
    assert pg.earned_trophies == {"bronze": 3, "silver": 0, "gold": 0, "platinum": 0}


def test_create_or_update_profile_game_increments_played_count_once():
    # Regression guard: played_count is maintained solely by the ProfileGame
    # post_save signal. create_or_update_profile_game must NOT also increment it
    # (that double-counted to 2 per new link before the fix).
    profile = ProfileFactory()
    game = GameFactory()

    PsnApiService.create_or_update_profile_game(profile, game, fake_trophy_title())

    game.refresh_from_db()
    assert game.played_count == 1


def test_profile_game_accepts_a_correction_with_an_unchanged_timestamp():
    # Regression guard for the frozen-row bug (The Long Dark, NPWR13317_00). PSN revised this
    # title's earned counts and progress IN PLACE without moving last_updated_datetime. The
    # update gated solely on that timestamp, so the correction could never land and the row sat
    # at an impossible 111% for five months, failing every `progress == 100` completion test.
    frozen = timezone.now()
    profile = ProfileFactory()
    game = GameFactory()
    ProfileGameFactory(
        profile=profile,
        game=game,
        progress=111,
        earned_trophies={"bronze": 67, "silver": 16, "gold": 4, "platinum": 1},
        last_updated_datetime=frozen,
    )

    # Same timestamp, corrected numbers -- exactly what PSN serves for this title today.
    _, _, drifted = PsnApiService.create_or_update_profile_game(
        profile,
        game,
        fake_trophy_title(
            progress=100,
            earned_trophies=_counts(bronze=56, silver=15, gold=4, platinum=1),
            defined_trophies=_counts(bronze=56, silver=15, gold=4, platinum=1),
            last_updated_datetime=frozen,
        ),
    )

    pg = ProfileGame.objects.get(profile=profile, game=game)
    assert pg.progress == 100  # the completion tests can see him again
    assert pg.earned_trophies == {"bronze": 56, "silver": 15, "gold": 4, "platinum": 1}
    # The caller reads this to put the row into touched_profilegame_ids, which is what gets the
    # badge/contract credit re-evaluated. Fixing the number alone would not have paid him.
    assert "progress" in drifted


@pytest.mark.parametrize("psn_progress,stored", [(111, 100), (-5, 0)])
def test_profile_game_clamps_impossible_psn_progress(psn_progress, stored):
    # A percentage outside 0-100 is Sony's arithmetic contradicting itself mid-restructure. On the
    # create path we have no EarnedTrophy rows to outrank it with, so the clamped value stands.
    profile = ProfileFactory()
    game = GameFactory()

    pg, _, _ = PsnApiService.create_or_update_profile_game(
        profile,
        game,
        fake_trophy_title(
            progress=psn_progress,
            earned_trophies=_counts(bronze=67, silver=16, gold=4, platinum=1),
            defined_trophies=_counts(bronze=56, silver=15, gold=4, platinum=1),
        ),
    )

    assert ProfileGame.objects.get(pk=pg.pk).progress == stored


def test_impossible_payload_defers_to_our_own_rows_rather_than_inventing_completion():
    # An earned count that outnumbers the defined count does NOT mean the hunter holds the whole
    # list: trophies from a group PSN dropped can outnumber base-game trophies they are still
    # missing. Clamping such a row to 100 would invent a completion and hand out badge, contract
    # and plat-card credit for an unfinished game, so our own EarnedTrophy denorms outrank the
    # payload. Floored, so it reads 100 only when nothing is unearned.
    profile = ProfileFactory()
    game = GameFactory()
    ProfileGameFactory(
        profile=profile,
        game=game,
        progress=50,
        earned_trophies_count=34,
        unearned_trophies_count=3,  # three trophies of the live list still missing
    )

    PsnApiService.create_or_update_profile_game(
        profile,
        game,
        fake_trophy_title(
            progress=127,
            earned_trophies=_counts(bronze=20, silver=5, gold=8, platinum=1),
            defined_trophies=_counts(bronze=20, silver=5, gold=4, platinum=1),
        ),
    )

    assert ProfileGame.objects.get(profile=profile, game=game).progress == 91  # 34*100//37


def test_impossible_payload_with_in_range_progress_still_defers_and_warns(monkeypatch, caplog):
    # The contradiction is earned > defined; `progress` itself happens to look plausible. The
    # payload is still untrustworthy, so the same fallback applies, and the warning names both
    # totals because the overshoot is what says the title's list moved under Sony.
    profile = ProfileFactory()
    game = GameFactory()
    ProfileGameFactory(
        profile=profile,
        game=game,
        progress=64,
        earned_trophies_count=30,
        unearned_trophies_count=0,
    )

    # The psn_api logger is configured with propagate=False, so caplog's root handler cannot see
    # it without this. Without the propagation flip the assertion below passes vacuously.
    monkeypatch.setattr(logging.getLogger("psn_api"), "propagate", True)

    with caplog.at_level(logging.WARNING, logger="psn_api"):
        PsnApiService.create_or_update_profile_game(
            profile,
            game,
            fake_trophy_title(
                progress=64,  # in range, so a range check alone would wave this through
                earned_trophies=_counts(bronze=25, silver=5, gold=3, platinum=1),
                defined_trophies=_counts(bronze=20, silver=5, gold=4, platinum=1),
            ),
        )

    assert ProfileGame.objects.get(profile=profile, game=game).progress == 100  # 30*100//30
    assert any(
        "earned=34 defined=30" in r.getMessage() for r in caplog.records
    ), "the contradiction that names the incident class was not logged"


def test_profile_game_writes_on_a_single_field_drift():
    # The gate compares four PSN-owned fields. Dropping any one of them from the comparison
    # would freeze that field forever, so each must be able to trigger the write on its own.
    profile = ProfileFactory()
    game = GameFactory()
    title = fake_trophy_title(hidden_flag=False)
    PsnApiService.create_or_update_profile_game(profile, game, title)

    moved = timezone.now()
    _, _, drifted = PsnApiService.create_or_update_profile_game(
        profile, game, fake_trophy_title(hidden_flag=True, last_updated_datetime=moved)
    )

    pg = ProfileGame.objects.get(profile=profile, game=game)
    assert sorted(drifted) == ["hidden_flag", "last_updated_datetime"]
    assert pg.hidden_flag is True
    assert pg.last_updated_datetime == moved


def test_profile_game_skips_the_write_when_nothing_moved():
    # The value comparison replaced a timestamp comparison; it must not have cost us the
    # original intent, which was to leave an unchanged row alone. last_sync is auto_now and is
    # listed in update_fields, so a save bumps it.
    profile = ProfileFactory()
    game = GameFactory()
    title = fake_trophy_title(progress=42, earned_trophies=_counts(bronze=3))
    pg, _, _ = PsnApiService.create_or_update_profile_game(profile, game, title)
    first_sync = ProfileGame.objects.get(pk=pg.pk).last_sync

    _, _, drifted = PsnApiService.create_or_update_profile_game(profile, game, title)

    assert drifted == []
    assert ProfileGame.objects.get(pk=pg.pk).last_sync == first_sync


# --- Trophy translation -------------------------------------------------------


def test_create_or_update_trophy_maps_type_value():
    game = GameFactory()
    td = fake_trophy_data(trophy_type=SimpleNamespace(value="gold"), trophy_name="Shiny")

    trophy, created = PsnApiService.create_or_update_trophy_from_trophy_data(game, td)

    assert created is True
    assert trophy.trophy_type == "gold"
    assert trophy.trophy_name == "Shiny"
    assert trophy.game_id == game.id


# --- EarnedTrophy translation -------------------------------------------------


def test_create_earned_trophy_unearned():
    profile = ProfileFactory()
    trophy = TrophyFactory()
    td = fake_trophy_data(earned=False)

    with sync_signal_suppressor():
        et, created = PsnApiService.create_or_update_earned_trophy_from_trophy_data(
            profile, trophy, td
        )

    assert created is True
    assert et.earned is False


def test_earned_trophy_flips_unearned_to_earned():
    profile = ProfileFactory()
    trophy = TrophyFactory(trophy_type="bronze")  # bronze: no platinum notification path
    earned_at = timezone.now()

    with sync_signal_suppressor():
        PsnApiService.create_or_update_earned_trophy_from_trophy_data(
            profile, trophy, fake_trophy_data(earned=False)
        )
        et, created = PsnApiService.create_or_update_earned_trophy_from_trophy_data(
            profile, trophy, fake_trophy_data(earned=True, earned_date_time=earned_at)
        )

    assert created is False
    assert et.earned is True
    assert et.earned_date_time == earned_at


# --- update_profilegame_stats (the bridge to badge eval) ----------------------


def _earned_rows(profile, game, types):
    """Create EarnedTrophy rows (signal-free) for the given trophy types."""
    trophies = [
        Trophy.objects.create(
            game=game, trophy_id=i, trophy_type=t, trophy_name=f"T{i}"
        )
        for i, t in enumerate(types)
    ]
    EarnedTrophy.objects.bulk_create(
        [
            EarnedTrophy(
                profile=profile, trophy=tr, earned=True,
                earned_date_time=timezone.now(),
            )
            for tr in trophies
        ]
    )
    return trophies


def test_update_profilegame_stats_sets_has_plat_when_platinum_earned():
    profile = ProfileFactory()
    game = GameFactory()
    pg = ProfileGame.objects.create(profile=profile, game=game, progress=100)
    _earned_rows(profile, game, ["bronze", "bronze", "platinum"])

    PsnApiService.update_profilegame_stats([pg.id])

    pg.refresh_from_db()
    assert pg.has_plat is True
    assert pg.earned_trophies_count == 3
    assert pg.most_recent_trophy_date is not None


def test_update_profilegame_stats_no_platinum_means_no_plat():
    profile = ProfileFactory()
    game = GameFactory()
    pg = ProfileGame.objects.create(profile=profile, game=game, progress=80)
    # 2 earned + 1 unearned, no platinum
    t_earned = _earned_rows(profile, game, ["bronze", "silver"])
    unearned = Trophy.objects.create(
        game=game, trophy_id=99, trophy_type="gold", trophy_name="Locked"
    )
    EarnedTrophy.objects.create(profile=profile, trophy=unearned, earned=False)

    PsnApiService.update_profilegame_stats([pg.id])

    pg.refresh_from_db()
    assert pg.has_plat is False
    assert pg.earned_trophies_count == 2
    assert pg.unearned_trophies_count == 1


# --- get_db_fingerprint -------------------------------------------------------


def test_get_db_fingerprint_counts_earned_by_type_and_visible_games():
    profile = ProfileFactory()
    game = GameFactory()
    ProfileGame.objects.create(profile=profile, game=game, user_hidden=False)
    ProfileGame.objects.create(
        profile=profile, game=GameFactory(), user_hidden=True
    )  # hidden -> excluded from visible count
    _earned_rows(profile, game, ["bronze", "bronze", "silver", "platinum"])

    bronze, silver, gold, platinum, visible = PsnApiService.get_db_fingerprint(profile)

    assert (bronze, silver, gold, platinum) == (2, 1, 0, 1)
    assert visible == 1  # only the non-hidden ProfileGame
