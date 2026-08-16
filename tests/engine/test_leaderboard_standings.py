"""The materialized leaderboard stores (leaderboards rebuild, step 1).

Three things get denormalized so every board is an indexed `ORDER BY` instead of an aggregate per read:

- **Badge-game trophy counts** on `ProfileBadgeStanding` — the Global Progress board. Replaces a
  full-population aggregate over `EarnedTrophy` that ran every 6 hours.
- **`ProfileCareerStanding`** — the Career XP board and Pursuer Level, rolled up from `ProfileJobXP`.
- **`country_code`** on every standing store — so a country slice is a range scan, not a join-then-filter.

All three are *factual* read-models: recompute-from-scratch in a single write seam, never relative (rank
and rarity stay live). What these tests hold down is that property — recomputing must be idempotent, and
the seam must be the only thing that writes them.

See docs/design/rebuild/leaderboards-rebuild.md.
"""
import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext

from trophies.models import (
    EarnedTrophy, ProfileBadgeStanding, ProfileCareerStanding, ProfileJobXP, SeriesBadgeStanding, Trophy,
)
from trophies.services.badge_xp import badge_trophy_counts
from trophies.services.contract_service import recompute_career_standing
from tests.factories import (
    ProfileFactory, ConceptFactory, GameFactory, StageFactory, TrophyFactory, EarnedTrophyFactory,
)

pytestmark = pytest.mark.django_db


def _job(n=[0]):
    """A Job. There is no factory for these (the catalogue is seeded, not generated), and `slug` is the
    primary key, so tests mint their own distinct ones."""
    from trophies.models import Job
    n[0] += 1
    return Job.objects.create(slug=f'test-job-{n[0]}', name=f'Test Job {n[0]}', discipline='combat')


def _badge_game(slug='stg', stage_number=1):
    """A game inside a badge stage -- i.e. one whose trophies count toward Global Progress."""
    concept = ConceptFactory()
    stage = StageFactory(series_slug=slug, stage_number=stage_number)
    stage.concepts.add(concept)
    return GameFactory(concept=concept, title_platform=['PS5'])


def _earn(profile, game, tier, n=1):
    for i in range(n):
        trophy = TrophyFactory(game=game, trophy_type=tier)
        EarnedTrophyFactory(profile=profile, trophy=trophy, earned=True)


# ---------------------------------------------------------------- global progress counts ----------------

def test_trophy_counts_are_tallied_by_tier_and_totalled():
    profile = ProfileFactory(is_linked=True)
    game = _badge_game()
    _earn(profile, game, 'bronze', 4)
    _earn(profile, game, 'silver', 2)
    _earn(profile, game, 'gold', 1)
    _earn(profile, game, 'platinum', 1)

    counts = badge_trophy_counts(profile.id)

    assert counts == {
        'trophies_bronze': 4, 'trophies_silver': 2, 'trophies_gold': 1, 'trophies_platinum': 1,
        'trophies_total': 8,
    }


def test_a_game_in_many_badges_counts_its_trophies_once():
    """The load-bearing one. The game set is an `IN (subquery)`, which dedupes by construction, so a game
    sitting in five badges contributes its trophies exactly once.

    Rewriting this as a join through Stage would multiply every trophy by the number of badges containing
    its game -- a number that inflates with catalogue growth rather than with play, and looks entirely
    plausible while doing it. That is the single most likely way for this figure to silently break, which
    is why the fixture puts one game in THREE stages of three different series rather than one.
    """
    profile = ProfileFactory(is_linked=True)
    concept = ConceptFactory()
    for i, slug in enumerate(('alpha', 'beta', 'gamma'), start=1):
        stage = StageFactory(series_slug=slug, stage_number=i)
        stage.concepts.add(concept)
    game = GameFactory(concept=concept, title_platform=['PS5'])

    _earn(profile, game, 'bronze', 3)

    assert badge_trophy_counts(profile.id)['trophies_bronze'] == 3, (
        'trophies were multiplied by the number of badges containing their game'
    )


def test_trophies_outside_badge_games_do_not_count():
    """The board is "trophies across BADGE games", not "trophies". A game in no stage is not in the set."""
    profile = ProfileFactory(is_linked=True)
    _earn(profile, _badge_game(), 'gold', 2)

    unbadged = GameFactory(concept=ConceptFactory(), title_platform=['PS5'])
    _earn(profile, unbadged, 'gold', 5)

    assert badge_trophy_counts(profile.id)['trophies_gold'] == 2


def test_unearned_rows_do_not_count():
    """`EarnedTrophy` rows exist for trophies a profile has NOT earned; `earned=True` is the filter that
    makes this a score rather than a catalogue size."""
    profile = ProfileFactory(is_linked=True)
    game = _badge_game()
    trophy = TrophyFactory(game=game, trophy_type='gold')
    EarnedTrophyFactory(profile=profile, trophy=trophy, earned=False)

    assert badge_trophy_counts(profile.id)['trophies_total'] == 0


def test_the_count_is_one_grouped_query_not_a_python_tally():
    """A whale holds 250k+ EarnedTrophy rows. Iterating them in Python is the documented OOM/timeout
    pattern this codebase has been bitten by repeatedly, and it is invisible in review because the code
    reads fine at test scale. Pinned as a query COUNT so a future `for et in qs:` rewrite fails here."""
    profile = ProfileFactory(is_linked=True)
    game = _badge_game()
    _earn(profile, game, 'bronze', 12)

    with CaptureQueriesContext(connection) as ctx:
        badge_trophy_counts(profile.id)

    assert len(ctx.captured_queries) == 1, (
        f'{len(ctx.captured_queries)} queries to count one profile\'s trophies -- this must be a single '
        f'grouped aggregate'
    )


# ---------------------------------------------------------------- career standing -----------------------

def test_career_standing_rolls_up_job_xp_and_levels():
    profile = ProfileFactory(is_linked=True)
    ProfileJobXP.objects.create(profile=profile, job=_job(), total_xp=300, level=3)
    ProfileJobXP.objects.create(profile=profile, job=_job(), total_xp=700, level=5)

    recompute_career_standing(profile)

    standing = ProfileCareerStanding.objects.get(profile=profile)
    assert standing.total_xp == 1000
    assert standing.pursuer_level == 8, 'Pursuer Level is the SUM of per-job levels'


def test_career_standing_recompute_is_idempotent_and_self_healing():
    """Recompute-from-scratch is what makes materializing this safe: a re-run must not double, and a row
    corrupted by anything else must be corrected rather than added to. Both properties in one test because
    they are the same property viewed twice."""
    profile = ProfileFactory(is_linked=True)
    ProfileJobXP.objects.create(profile=profile, job=_job(), total_xp=250, level=2)

    recompute_career_standing(profile)
    recompute_career_standing(profile)
    assert ProfileCareerStanding.objects.get(profile=profile).total_xp == 250, 'the roll-up doubled'

    ProfileCareerStanding.objects.filter(profile=profile).update(total_xp=999999, pursuer_level=42)
    recompute_career_standing(profile)

    standing = ProfileCareerStanding.objects.get(profile=profile)
    assert standing.total_xp == 250 and standing.pursuer_level == 2, 'a drifted row was not corrected'


def test_a_profile_with_no_job_xp_gets_a_zeroed_standing_not_a_crash():
    profile = ProfileFactory(is_linked=True)
    recompute_career_standing(profile)

    standing = ProfileCareerStanding.objects.get(profile=profile)
    assert standing.total_xp == 0 and standing.pursuer_level == 0


def test_the_job_xp_seam_writes_the_roll_up():
    """The roll-up rides `recompute_profile_job_xp` rather than getting its own trigger, so that anything
    changing a profile's job XP necessarily updates the total. If the call is dropped, the board silently
    freezes at its last value -- no error, just a stale leaderboard."""
    import inspect
    from trophies.services import contract_service

    source = inspect.getsource(contract_service.recompute_profile_job_xp)
    assert 'recompute_career_standing(' in source, (
        'the per-job recompute no longer rolls up into ProfileCareerStanding; the Career XP board will '
        'freeze at whatever it last held'
    )


# ---------------------------------------------------------------- country denorm ------------------------

def test_changing_country_propagates_to_every_standing_store():
    """The recompute seams stamp `country_code` on rows they write, which covers a syncing profile. This
    covers the path that bypasses them: the country changing with no recompute behind it, which would
    otherwise leave a hunter ranked in the country they left until their next badge evaluation."""
    profile = ProfileFactory(is_linked=True, country_code='CA')
    ProfileBadgeStanding.objects.create(profile=profile, total_xp=10, country_code='CA')
    ProfileCareerStanding.objects.create(profile=profile, total_xp=10, country_code='CA')
    SeriesBadgeStanding.objects.create(profile=profile, series_slug='s', xp=5, country_code='CA')
    ProfileJobXP.objects.create(profile=profile, job=_job(), total_xp=5, country_code='CA')

    profile.country_code = 'GB'
    profile.save()

    assert ProfileBadgeStanding.objects.get(profile=profile).country_code == 'GB'
    assert ProfileCareerStanding.objects.get(profile=profile).country_code == 'GB'
    assert SeriesBadgeStanding.objects.get(profile=profile).country_code == 'GB'
    assert ProfileJobXP.objects.get(profile=profile).country_code == 'GB'


def test_saving_a_profile_without_changing_country_touches_no_standings():
    """Gated on the EDGE, not fired on every save. Country comes from PSN and almost never moves; four
    blind UPDATEs on every `Profile.save()` would be a real cost across sync, which writes profiles
    constantly."""
    profile = ProfileFactory(is_linked=True, country_code='CA')
    ProfileBadgeStanding.objects.create(profile=profile, total_xp=10, country_code='CA')

    with CaptureQueriesContext(connection) as ctx:
        profile.display_psn_username = 'renamed'
        profile.save()

    touched = [q['sql'] for q in ctx.captured_queries
               if 'UPDATE' in q['sql'] and 'standing' in q['sql'].lower()]
    assert not touched, f'a non-country save still rewrote the standings: {touched}'
