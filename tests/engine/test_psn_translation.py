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

from types import SimpleNamespace

import pytest
from django.utils import timezone

from trophies.models import EarnedTrophy, Game, ProfileGame, Trophy
from trophies.services.psn_api_service import PsnApiService
from trophies.sync_utils import sync_signal_suppressor
from tests.factories import GameFactory, ProfileFactory, TrophyFactory

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

    pg, created = PsnApiService.create_or_update_profile_game(profile, game, tt)

    assert created is True
    assert pg.progress == 42
    assert pg.earned_trophies == {"bronze": 3, "silver": 0, "gold": 0, "platinum": 0}


@pytest.mark.xfail(
    reason=(
        "BUG: Game.played_count is double-incremented on first link. Both the "
        "post_save signal update_game_played_count_on_save (signals.py) AND "
        "create_or_update_profile_game (psn_api_service.py:434) increment it, so "
        "a single new ProfileGame bumps played_count by 2. The signal is the "
        "canonical maintainer; the service-side increment is the redundant one. "
        "Remove this marker once the duplicate increment is removed."
    ),
    strict=True,
)
def test_create_or_update_profile_game_increments_played_count_once():
    profile = ProfileFactory()
    game = GameFactory()

    PsnApiService.create_or_update_profile_game(profile, game, fake_trophy_title())

    game.refresh_from_db()
    assert game.played_count == 1


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
