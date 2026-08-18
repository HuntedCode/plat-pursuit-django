"""The materialized leaderboard stores (leaderboards rebuild, step 1).

Three things get denormalized so every board is an indexed `ORDER BY` instead of an aggregate per read:

- **`ProfileBadgeStanding.total_xp` / `badges_held`** — the Badge Points board.
  (Badge-game trophy counts also lived here and were REMOVED in 2026-08: maintaining them meant a
  full-library `EarnedTrophy` aggregate per profile in the badge write seam, which became a per-sync cost
  once the engine was wired into `sync_complete`. The board they fed now reads Profile's own counters.)
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


@pytest.mark.parametrize('mirror', ['country_code', 'is_linked'])
def test_every_store_with_a_profile_mirror_is_in_the_propagation_list(mirror):
    """The test above can only check the stores somebody remembered to add to it. This one asks the MODELS
    which stores carry a mirror, and fails if the handler's list has fallen behind.

    ProfileEditionStanding shipped with a `country_code` column and was missing from the handler, so an
    edition-sliced board would have kept ranking a relocated hunter under their old flag while the
    all-editions board had already moved them. Nothing errors; the two boards just quietly disagree.

    Parametrized over BOTH mirrors, because `is_linked` fails worse than `country_code` does: country is a
    filter the reader opted into, so a stale one misfiles a hunter on one slice. `is_linked` is the whole
    board's population rule, so a store left out keeps an unverified account ranked after they verify, or
    keeps a verified one off.
    """
    from django.apps import apps
    from trophies.signals import profile_mirrored_standings

    declared = {
        model for model in apps.get_app_config('trophies').get_models()
        if mirror in {f.name for f in model._meta.get_fields()}
        and 'profile' in {f.name for f in model._meta.get_fields()}
    }
    handled = set(profile_mirrored_standings())

    assert declared <= handled, (
        f'these stores mirror Profile.{mirror} but the signal never updates them: '
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
    """A composite index only range-scans when it matches the ORDER BY, and this one must express the
    board's FULL ordering -- tiebreak and unique tail included.

    The `profile` tail is what makes a rank COUNT index-only: `badge_leaderboards` numbers a page by SLOT
    and computes a rank by counting everyone ahead, and those two agree only because the ordering ends in
    a unique key. It is also a superset of the old two-column (series_slug, -progress_bp) it replaced --
    keeping both would be dead write cost on every standing write.
    """
    idx = {i.name: i.fields for i in SeriesBadgeStanding._meta.indexes}

    assert idx.get('sbs_series_board_idx') == ['series_slug', '-progress_bp', 'advanced_at', 'profile'], (
        f'the combined board index no longer matches the board order: {idx.get("sbs_series_board_idx")}'
    )
    assert 'sbs_series_prog_idx' not in idx, (
        'the superseded two-column progress index is back; the board index already covers that ordering'
    )
    # The `-xp` pair served `series_xp_rows`, deleted in the 2026-08 audit for having no caller. They were
    # pure write cost on a table every badge evaluation writes.
    assert 'sbs_series_xp_idx' not in idx and 'sbs_series_cc_xp_idx' not in idx, (
        'the per-series XP indexes are back; the board they served no longer exists'
    )


def test_the_scrolled_board_indexes_are_partial_on_the_population():
    """0309 made the whole-table boards partial and left the per-entity ones plain, on the grounds that a
    leading key already narrows them. That held for PAGINATION; these boards virtual-scroll now, so a
    reader deep into a popular series walks the index fetching `is_linked` per candidate -- the shape 0307
    measured at 49.7 ms. 0311 closed it."""
    from trophies.models import ProfileEditionStanding, UserGroupBadge

    for model, names in (
        (SeriesBadgeStanding, ('sbs_series_board_idx', 'sbs_series_cc_board_idx')),
        (ProfileEditionStanding, ('pes_ed_xp_idx', 'pes_ed_cc_xp_idx')),
        (UserGroupBadge, ('ugb_badge_earned_idx', 'ugb_badge_cc_earned_idx')),
    ):
        by_name = {i.name: i for i in model._meta.indexes}
        for name in names:
            assert name in by_name, f'{model.__name__}.{name} is missing'
            assert by_name[name].condition is not None, (
                f'{name} is not partial -- a deep scroll will evaluate is_linked from the heap'
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
    from django.utils import timezone
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


# ------------------------------------------------------------------ the write seam's integrity ----------

def _desired_for(profile, gb, cleared, gating):
    """Hand-built engine output for one group badge, which is what `recompute_standing` consumes. Building
    it directly (rather than driving the whole engine) keeps these tests about the WRITE seam."""
    from types import SimpleNamespace

    return {gb.id: SimpleNamespace(
        base_earned=cleared >= gating, holo=False, earned_date=None,
        base_satisfied_count=cleared, gating_count=gating, stages=[],
    )}


def test_the_recompute_is_ATOMIC_across_all_four_tables():
    """This seam writes across four tables and deletes from two, and its own comments already reason about
    pairs of rows that must agree -- "one of them holding a hunter the other has dropped is the kind of
    disagreement nobody would think to check". Without a transaction, a failure partway left exactly that
    disagreement durably on disk.

    The failure is injected at the LAST write, so everything before it has already been issued: if the
    block is not atomic, the earlier rows survive the exception.
    """
    from unittest import mock

    from tests.factories import BadgeSeriesFactory, GroupBadgeFactory, PlatformGroupFactory
    from trophies.models import ProfileBadgeStanding, ProfileEditionStanding, SeriesBadgeStanding
    from trophies.services import badge_xp

    profile = ProfileFactory()
    gb = GroupBadgeFactory(series=BadgeSeriesFactory(series_slug='atom'),
                           platform_group=PlatformGroupFactory(key='atom-grp'), is_live=True)
    desired = _desired_for(profile, gb, cleared=2, gating=2)

    real = badge_xp._write_edition_standings

    def boom(*a, **kw):
        real(*a, **kw)                 # do the work...
        raise RuntimeError('timeout')  # ...then fail, exactly as a statement timeout would

    with mock.patch.object(badge_xp, '_write_edition_standings', boom):
        with pytest.raises(RuntimeError):
            badge_xp.recompute_standing(profile.id, desired, [gb])

    assert not SeriesBadgeStanding.objects.filter(profile=profile).exists(), (
        'a series standing survived a failed recompute'
    )
    assert not ProfileBadgeStanding.objects.filter(profile=profile).exists()
    assert not ProfileEditionStanding.objects.filter(profile=profile).exists()


def test_the_recompute_takes_a_per_profile_lock():
    """`_upsert` skips `update_or_create`'s savepoint + SELECT FOR UPDATE on the grounds that "a profile's
    recompute is never concurrent with itself". That was asserted as a fact before anything made it one:
    the nightly `evaluate_badges --all` and a hunter's own `sync_complete` are separate processes with no
    interlock, so a hunter syncing while the batch reached them ran two recomputes at once, and both would
    find no row to UPDATE and both would INSERT.

    An ADVISORY lock, not a row lock, and the distinction is the point: `Profile.add_to_sync_target`
    locks the Profile row with `nowait=True` behind a ~0.8s tenacity retry, so a recompute holding that
    row longer than that turns into a `RetryError` inside a hunter's sync -- which is exactly the overlap
    the nightly orchestrator says to expect. An advisory lock takes no row, so Profile writers are
    untouched.

    Asserted on the emitted SQL rather than by racing two threads: a genuine race is timing-dependent and
    would be the flakiest test in the suite. What must hold is that the lock is TAKEN, and that it is not
    taken on the Profile row.
    """
    from tests.factories import BadgeSeriesFactory, GroupBadgeFactory, PlatformGroupFactory
    from trophies.services import badge_xp

    profile = ProfileFactory()
    gb = GroupBadgeFactory(series=BadgeSeriesFactory(series_slug='lock'),
                           platform_group=PlatformGroupFactory(key='lock-grp'), is_live=True)

    with CaptureQueriesContext(connection) as ctx:
        badge_xp.recompute_standing(profile.id, _desired_for(profile, gb, 1, 2), [gb])

    sql = [q['sql'] for q in ctx.captured_queries]
    assert any('pg_advisory_xact_lock' in q for q in sql), 'the recompute took no lock'
    assert not any('FOR UPDATE' in q and 'trophies_profile' in q for q in sql), (
        'the recompute locked the Profile ROW, which collides with add_to_sync_target(nowait=True)'
    )


# ------------------------------------------------------------------ the is_linked mirror ----------------

def test_every_production_path_stamps_the_mirror_at_birth():
    """The trap this denorm introduces, closed behaviourally rather than by everyone remembering.

    `is_linked` is now read off the STORE, not off a join to Profile (migration 0308), so a row created
    without it silently vanishes from its board. The propagation signal cannot save that row: it fires
    only when a Profile's value CHANGES, so a row born after a hunter linked keeps the False default
    forever.

    This is not hypothetical -- `country_code` had exactly this bug on `ProfileJobXP`, at both creation
    sites, from the day the column landed until 0308 repaired it. What follows walks each store's real
    creation path and asserts the mirror arrived, so a new path that forgets fails here rather than by a
    hunter reporting they are missing from a board.
    """
    from trophies.models import (
        Job, ProfileBadgeStanding, ProfileCareerStanding, ProfileEditionStanding, ProfileJobXP,
        SeriesBadgeStanding, UserGroupBadge,
    )
    from trophies.services.badge_apply import evaluate_and_apply
    from trophies.services.contract_service import grant_job_xp, recompute_career_standing
    from tests.factories import (
        BadgeSeriesFactory, ConceptFactory, GameFactory, GroupBadgeFactory, PlatformGroupFactory,
        StageFactory,
    )
    from trophies.models import ProfileGame, ProfileTrophyGroup, TrophyGroup
    from django.utils import timezone

    profile = ProfileFactory(is_linked=True)

    # --- the badge write seam: SeriesBadgeStanding + ProfileBadgeStanding + ProfileEditionStanding ---
    series = BadgeSeriesFactory(series_slug='mirror')
    stage = StageFactory(series_slug='mirror', stage_number=1)
    concept = ConceptFactory()
    stage.concepts.add(concept)
    game = GameFactory(concept=concept, title_platform=['PS5'])
    gb = GroupBadgeFactory(series=series,
                           platform_group=PlatformGroupFactory(key='mirror-grp', platforms=['PS5']),
                           is_live=True)
    ProfileGame.objects.create(profile=profile, game=game, progress=100)
    tg, _ = TrophyGroup.objects.get_or_create(game=game, trophy_group_id='default',
                                              defaults={'trophy_group_name': 'Base'})
    ProfileTrophyGroup.objects.create(profile=profile, trophy_group=tg, progress=100,
                                      last_trophy_at=timezone.now())
    evaluate_and_apply(profile, [gb])

    assert SeriesBadgeStanding.objects.get(profile=profile).is_linked is True
    assert ProfileBadgeStanding.objects.get(profile=profile).is_linked is True
    assert ProfileEditionStanding.objects.get(profile=profile).is_linked is True
    # ...and the earners board's own store, which is written by `apply_changes`, not by the recompute.
    assert UserGroupBadge.objects.get(profile=profile).is_linked is True

    # --- the jobs economy: ProfileJobXP (get_or_create) + ProfileCareerStanding ---
    job = Job.objects.create(slug='mirror-job', name='Mirror', discipline='combat')
    grant_job_xp(profile, job, 500, source='test', source_id=1)
    recompute_career_standing(profile)

    # ProfileJobXP is covered TWICE and this asserts the outcome, not one mechanism: `grant_job_xp`
    # stamps at birth, and `recompute_career_standing` -- which always follows it -- sweeps the profile's
    # rows. Removing either one alone leaves this green, deliberately. The end state is what a board
    # reads, and the belt-and-braces is on purpose given this is the store the country mirror was
    # silently wrong on for its whole life.
    pjx = ProfileJobXP.objects.get(profile=profile, job=job)
    assert pjx.is_linked is True, 'a job XP row reached the board with no mirror'
    assert pjx.country_code == (profile.country_code or ''), (
        'the country mirror is missing -- the bug 0308 repaired, reintroduced'
    )
    assert ProfileCareerStanding.objects.get(profile=profile).is_linked is True


def test_verifying_an_account_propagates_to_every_store():
    """The edge the recompute seams cannot cover. A hunter VERIFIES, which changes `is_linked` with no
    badge evaluation behind it -- so without this handler they would stay off every board until their
    next sync, having just done the one thing that is supposed to put them on."""
    from trophies.models import (
        ProfileBadgeStanding, ProfileCareerStanding, ProfileEditionStanding, ProfileJobXP,
        SeriesBadgeStanding, UserGroupBadge,
    )

    profile = ProfileFactory(is_linked=False)
    rows = [
        ProfileBadgeStanding.objects.create(profile=profile, total_xp=100, is_linked=False),
        ProfileCareerStanding.objects.create(profile=profile, total_xp=100, pursuer_level=1, is_linked=False),
        ProfileEditionStanding.objects.create(profile=profile, platform_group_key='e', total_xp=100, is_linked=False),
        SeriesBadgeStanding.objects.create(profile=profile, series_slug='s', xp=1, progress_bp=1,
                                           stages_cleared=1, stages_total=1, is_linked=False),
    ]

    profile.is_linked = True
    profile.save(update_fields=['is_linked'])

    for row in rows:
        row.refresh_from_db()
        assert row.is_linked is True, f'{type(row).__name__} was not reached by the propagation'
