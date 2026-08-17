"""The materialized leaderboard stores (leaderboards rebuild, step 1).

Three things get denormalized so every board is an indexed `ORDER BY` instead of an aggregate per read:

- **Badge-game trophy counts** on `ProfileBadgeStanding` — the Badge Trophies board. Replaces a
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
    """A game inside a badge stage -- i.e. one whose trophies count toward Badge Trophies."""
    concept = ConceptFactory()
    stage = StageFactory(series_slug=slug, stage_number=stage_number)
    stage.concepts.add(concept)
    return GameFactory(concept=concept, title_platform=['PS5'])


def _earn(profile, game, tier, n=1):
    for i in range(n):
        trophy = TrophyFactory(game=game, trophy_type=tier)
        EarnedTrophyFactory(profile=profile, trophy=trophy, earned=True)


# ---------------------------------------------------------------- badge trophy counts ------------------

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


def test_granting_job_xp_updates_the_career_board_immediately():
    """The LIVE path, end to end. Accepting a contract calls `grant_job_xp`, which is the single primitive
    every job-XP payout flows through -- contracts, quests, events, manual awards.

    The roll-up was originally hooked to `recompute_profile_job_xp` (the ledger REBUILD), which only
    management commands call. That looked right and was not: a real accept bumped ProfileJobXP and left
    ProfileCareerStanding frozen, so the Career board silently stopped at whatever the last backfill
    produced and every accept after it was invisible. Nothing errored.

    The original test asserted the call existed inside the rebuild function -- true, and no evidence at
    all about the seam that actually fires. This one grants XP the way the product does and reads the
    board's own column.
    """
    from trophies.services.contract_service import grant_job_xp

    profile = ProfileFactory(is_linked=True)
    job = _job()

    granted = grant_job_xp(profile, job, 250, source='manual')
    assert granted == 250

    standing = ProfileCareerStanding.objects.get(profile=profile)
    assert standing.total_xp == 250, 'the Career board did not move when XP was granted'
    assert standing.pursuer_level >= 1

    # A second grant must accumulate, not replace -- the board reads a running total, not the last award.
    grant_job_xp(profile, _job(), 100, source='quest')
    assert ProfileCareerStanding.objects.get(profile=profile).total_xp == 350


def test_the_ledger_rebuild_repairs_a_drifted_career_standing():
    """The backfill path, exercised rather than read. `recompute_job_xp --all` must be able to REPAIR a
    standing, which is the whole reason it is recompute-from-scratch.

    Previously this asserted `'recompute_career_standing(' in inspect.getsource(...)` -- true, and no
    evidence the call does anything. Corrupting the row and watching the rebuild fix it tests the
    property the command actually promises.
    """
    from trophies.services.contract_service import grant_job_xp, recompute_profile_job_xp

    profile = ProfileFactory(is_linked=True)
    # XP must arrive through the LEDGER: `recompute_profile_job_xp` rebuilds ProfileJobXP from
    # ContractXPGrant, so a row written directly with no grants behind it is correctly zeroed. Writing the
    # cache row by hand and expecting the rebuild to preserve it tests the opposite of the contract.
    grant_job_xp(profile, _job(), 400, source='manual')

    ProfileCareerStanding.objects.filter(profile=profile).update(total_xp=999999, pursuer_level=42)
    recompute_profile_job_xp(profile)

    standing = ProfileCareerStanding.objects.get(profile=profile)
    assert standing.total_xp == 400, (
        'the ledger rebuild did not repair the standing; the backfill command cannot fix drift'
    )


# ---------------------------------------------------------------- country denorm ------------------------

def test_changing_country_propagates_to_every_standing_store():
    """The recompute seams stamp `country_code` on rows they write, which covers a syncing profile. This
    covers the path that bypasses them: the country changing with no recompute behind it, which would
    otherwise leave a hunter ranked in the country they left until their next badge evaluation."""
    from trophies.models import ProfileEditionStanding

    profile = ProfileFactory(is_linked=True, country_code='CA')
    ProfileBadgeStanding.objects.create(profile=profile, total_xp=10, country_code='CA')
    ProfileCareerStanding.objects.create(profile=profile, total_xp=10, country_code='CA')
    ProfileEditionStanding.objects.create(
        profile=profile, platform_group_key='ultra-hd', total_xp=10, country_code='CA')
    SeriesBadgeStanding.objects.create(profile=profile, series_slug='s', xp=5, country_code='CA')
    ProfileJobXP.objects.create(profile=profile, job=_job(), total_xp=5, country_code='CA')

    profile.country_code = 'GB'
    profile.save()

    assert ProfileBadgeStanding.objects.get(profile=profile).country_code == 'GB'
    assert ProfileCareerStanding.objects.get(profile=profile).country_code == 'GB'
    assert ProfileEditionStanding.objects.get(profile=profile).country_code == 'GB'
    assert SeriesBadgeStanding.objects.get(profile=profile).country_code == 'GB'
    assert ProfileJobXP.objects.get(profile=profile).country_code == 'GB'


def test_every_store_with_a_country_mirror_is_in_the_propagation_list():
    """The test above can only check the stores somebody remembered to add to it. This one asks the MODELS
    which stores carry a mirror, and fails if the handler's list has fallen behind.

    ProfileEditionStanding shipped with a `country_code` column and was missing from the handler, so an
    edition-sliced board would have kept ranking a relocated hunter under their old flag while the
    all-editions board had already moved them. Nothing errors; the two boards just quietly disagree.
    """
    from django.apps import apps
    from trophies.signals import country_mirrored_standings

    declared = {
        model for model in apps.get_app_config('trophies').get_models()
        if 'country_code' in {f.name for f in model._meta.get_fields()}
        and 'profile' in {f.name for f in model._meta.get_fields()}
    }
    handled = set(country_mirrored_standings())

    assert declared <= handled, (
        f'these stores mirror Profile.country_code but the signal never updates them: '
        f'{sorted(m.__name__ for m in declared - handled)}'
    )


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


def test_badge_trophies_board_ranks_platinums_first_then_total():
    """A trophy-hunting board leads with the trophy that takes a whole game to earn. Total trophies is the
    tiebreak, not the lead: 400 bronzes should not outrank a hunter with more platinums.

    Asserted as an actual ORDER BY over real rows rather than by reading the index definition, because the
    two can disagree -- an index is a performance fact and the ordering is a correctness one.
    """
    few_plats_many_trophies = ProfileFactory(is_linked=True)
    many_plats = ProfileFactory(is_linked=True)
    tie_breaker_loser = ProfileFactory(is_linked=True)

    ProfileBadgeStanding.objects.create(
        profile=few_plats_many_trophies, trophies_platinum=2, trophies_total=400)
    ProfileBadgeStanding.objects.create(
        profile=many_plats, trophies_platinum=9, trophies_total=50)
    ProfileBadgeStanding.objects.create(
        profile=tie_breaker_loser, trophies_platinum=9, trophies_total=20)

    ordered = list(
        ProfileBadgeStanding.objects
        .order_by('-trophies_platinum', '-trophies_total', 'profile_id')
        .values_list('profile_id', flat=True)
    )

    assert ordered == [many_plats.id, tie_breaker_loser.id, few_plats_many_trophies.id], (
        'the board is not platinum-leading with total as the tiebreak'
    )


def test_the_progress_index_matches_the_board_order():
    """A composite index only range-scans when its columns match the ORDER BY exactly. `(-platinum,
    -total)` against `ORDER BY -platinum, -total` is a scan; any divergence silently becomes a sort of the
    whole table, which is invisible until the table is large.
    """
    meta_indexes = {idx.name: idx.fields for idx in ProfileBadgeStanding._meta.indexes}

    assert meta_indexes.get('pbs_progress_idx') == ['-trophies_platinum', '-trophies_total'], (
        f'the Badge Trophies index no longer matches the board order: {meta_indexes.get("pbs_progress_idx")}'
    )
    assert meta_indexes.get('pbs_country_prog_idx') == [
        'country_code', '-trophies_platinum', '-trophies_total'
    ], 'the country-sliced Badge Trophies index must be (country, ...board order)'


# ---------------------------------------------------------------- the per-series board tiebreak ---------

def _stage_result(n, *, gates=True, satisfied=True, on=None):
    from datetime import date
    from trophies.services.badge_engine import StageResult
    return StageResult(stage_number=n, gates=gates, base_satisfied=satisfied,
                       holo_satisfied=False, base_date=on)


def _group_result(stages, *, earned=False, earned_date=None):
    from trophies.services.badge_engine import GroupBadgeResult
    gating = [s for s in stages if s.gates]
    return GroupBadgeResult(
        base_earned=earned, holo=False, gating_count=len(gating),
        base_satisfied_count=sum(1 for s in gating if s.base_satisfied),
        holo_satisfied_count=0, earned_date=earned_date, stages=stages,
    )


def test_a_chaser_advances_on_their_latest_cleared_stage():
    from datetime import date
    from trophies.services.badge_xp import _advanced_at

    result = _group_result([
        _stage_result(1, on=date(2026, 1, 5)),
        _stage_result(2, on=date(2026, 3, 9)),
        _stage_result(3, satisfied=False),
    ])
    assert _advanced_at(result) == date(2026, 3, 9)


def test_an_earner_advances_on_their_earn_date_not_their_last_stage():
    """The megamix case, and the reason these are two different dates.

    Under `min_count`, `earned_date` is the date the NEED-th stage fell. A hunter who keeps clearing
    optional extra stages afterwards would, if we used "latest cleared stage" for everyone, have their
    completion date pushed later and LOSE rank for doing more. Under the 'all' policy the two coincide,
    so this only ever shows up on megamix series -- which is exactly why it would survive casual testing.
    """
    from datetime import date
    from trophies.services.badge_xp import _advanced_at

    result = _group_result(
        [
            _stage_result(1, on=date(2026, 1, 5)),
            _stage_result(2, on=date(2026, 2, 2)),
            _stage_result(3, on=date(2026, 8, 30)),   # cleared LATER, after the badge was already earned
        ],
        earned=True, earned_date=date(2026, 2, 2),    # min_count: earned when the 2nd stage fell
    )

    assert _advanced_at(result) == date(2026, 2, 2), (
        'an earner is being ranked by their last extra stage instead of when they earned the badge'
    )


def test_a_profile_with_nothing_cleared_has_no_advance_date():
    from trophies.services.badge_xp import _advanced_at
    assert _advanced_at(_group_result([_stage_result(1, satisfied=False)])) is None


def test_the_series_board_is_earners_then_chasers_with_dates_breaking_ties():
    """The whole point of merging the two boards into one.

    A 3-stage series stacks most hunters on 1/3 or 2/3. `progress_bp` alone leaves those large ties
    ordered by profile id, which is arbitrary and reads as unranked. Ordering by
    `(-progress_bp, advanced_at)` gives earners on top by completion date, then each rung of chasers with
    whoever arrived first ahead -- the same rule the earners board always used, applied the whole way down.
    """
    from datetime import date

    def standing(bp, on):
        return SeriesBadgeStanding.objects.create(
            profile=ProfileFactory(is_linked=True), series_slug='board', xp=1,
            progress_bp=bp, advanced_at=on,
        )

    earner_late = standing(10000, date(2026, 5, 1))
    earner_first = standing(10000, date(2026, 1, 1))
    two_thirds_late = standing(6667, date(2026, 6, 1))
    two_thirds_first = standing(6667, date(2026, 2, 1))
    one_third = standing(3333, date(2026, 1, 15))

    ordered = list(
        SeriesBadgeStanding.objects.filter(series_slug='board')
        .order_by('-progress_bp', 'advanced_at', 'profile_id')
        .values_list('id', flat=True)
    )

    assert ordered == [earner_first.id, earner_late.id,
                       two_thirds_first.id, two_thirds_late.id,
                       one_third.id], 'the combined board is not (progress desc, earliest-there first)'


def test_the_board_index_matches_the_board_order():
    """A composite index only range-scans when it matches the ORDER BY. It also has to be a SUPERSET of the
    old two-column (series_slug, -progress_bp), which it replaces -- keeping both would be dead write cost
    on every standing write."""
    idx = {i.name: i.fields for i in SeriesBadgeStanding._meta.indexes}

    assert idx.get('sbs_series_board_idx') == ['series_slug', '-progress_bp', 'advanced_at'], (
        f'the combined board index no longer matches the board order: {idx.get("sbs_series_board_idx")}'
    )
    assert 'sbs_series_prog_idx' not in idx, (
        'the superseded two-column progress index is back; the board index already covers that ordering'
    )


def test_the_evaluation_seam_actually_writes_the_advance_date():
    """End-to-end through `evaluate_and_apply`, because the pure-function tests above cannot see whether
    the value ever reaches the row.

    Caught by mutation testing: deleting `advanced_at` from the upsert in `recompute_standing` left every
    other test in this file green. The column would simply stay NULL, every standing would tie, and the
    board would silently fall back to ordering by profile id -- the exact failure the tiebreak exists to
    prevent, shipped invisibly.
    """
    import datetime as dt
    from django.utils import timezone
    from trophies.models import ProfileGame, ProfileTrophyGroup, TrophyGroup
    from trophies.services.badge_apply import evaluate_and_apply
    from tests.factories import PlatformGroupFactory, BadgeSeriesFactory, GroupBadgeFactory

    series = BadgeSeriesFactory(series_slug='advance')
    pg = PlatformGroupFactory(key='ultra-hd', name='Ultra', platforms=['PS5'], exclude_delisted=True)
    gb = GroupBadgeFactory(series=series, platform_group=pg, is_live=True)
    games = []
    for i in (1, 2):
        stage = StageFactory(series_slug='advance', stage_number=i)
        concept = ConceptFactory()
        stage.concepts.add(concept)
        games.append(GameFactory(concept=concept, title_platform=['PS5']))

    profile = ProfileFactory(is_linked=True)
    cleared_on = timezone.now() - dt.timedelta(days=30)
    ProfileGame.objects.update_or_create(profile=profile, game=games[0], defaults={'progress': 50})
    tg, _ = TrophyGroup.objects.get_or_create(
        game=games[0], trophy_group_id='default', defaults={'trophy_group_name': 'B'})
    ProfileTrophyGroup.objects.update_or_create(
        profile=profile, trophy_group=tg, defaults={'progress': 100, 'last_trophy_at': cleared_on})

    evaluate_and_apply(profile, [gb])

    standing = SeriesBadgeStanding.objects.get(profile=profile, series_slug='advance')
    assert standing.progress_bp == 5000, 'fixture is wrong -- expected 1 of 2 stages'
    assert standing.advanced_at is not None, (
        'the recompute seam did not persist advanced_at; every standing will tie and the board loses its '
        'tiebreak'
    )
    assert standing.advanced_at == cleared_on.date()


# ---------------------------------------------------------------- audit findings ------------------------

@pytest.mark.parametrize('model_path', [
    'ProfileBadgeStanding', 'ProfileCareerStanding', 'ProfileEditionStanding', 'SeriesBadgeStanding',
    'ProfileJobXP',
])
def test_the_denormalized_country_column_is_no_narrower_than_its_source(model_path):
    """`Profile.country_code` is max_length=5. A denormalized copy narrower than its source turns any
    over-long value into a DataError on the propagating UPDATE -- a 500 on profile save, for data the
    source column accepts without complaint.

    ISO alpha-2 is two characters, which is why these were declared as 2 and why the mismatch looked
    harmless. The source column is the contract, not the standard it nominally holds.
    """
    from trophies import models as m
    from trophies.models import Profile

    source = Profile._meta.get_field('country_code').max_length
    mirror = getattr(m, model_path)._meta.get_field('country_code').max_length

    assert mirror >= source, (
        f'{model_path}.country_code holds {mirror} chars but Profile.country_code allows {source} -- '
        f'propagating a longer value raises DataError'
    )


def test_a_five_character_country_code_propagates_without_error():
    """The behavioural half of the test above: exercise the signal with a value the source permits."""
    from trophies.models import ProfileBadgeStanding

    profile = ProfileFactory(is_linked=True, country_code='CA')
    ProfileBadgeStanding.objects.create(profile=profile, total_xp=10, country_code='CA')

    profile.country_code = 'GB-NI'      # 5 chars, legal for Profile
    profile.save()

    assert ProfileBadgeStanding.objects.get(profile=profile).country_code == 'GB-NI'


def test_a_null_country_propagates_as_empty_not_none():
    """`Profile.country_code` is nullable; the standing columns are NOT (blank default ''). Propagating a
    None straight through would violate the column, so the handler coerces it."""
    from trophies.models import ProfileBadgeStanding

    profile = ProfileFactory(is_linked=True, country_code='CA')
    ProfileBadgeStanding.objects.create(profile=profile, total_xp=10, country_code='CA')

    profile.country_code = None
    profile.save()

    assert ProfileBadgeStanding.objects.get(profile=profile).country_code == ''
