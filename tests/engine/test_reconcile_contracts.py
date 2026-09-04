"""Coverage for the ONLY subtractive path in the Contract engine: revoking credit that current
membership no longer supports (`contract_service.revoke_contract` + `manage.py reconcile_contracts`).

The bug these exist for: Contract membership is DERIVED live from the anchored IGDB id, so pulling
two wrongly-grouped games apart into their own Concepts silently drops one of them out of its
Contract -- and every write path in the engine is forward-only, so the credit, and any XP already
banked on it, outlives the qualification. The real case was Myst: the 2020 remake and the 2025 port
of the original shared one Concept, so hunters who completed the ORIGINAL were paid the REMAKE's
Contract.

The safety rule under test alongside it: revoke ONLY when neither tier detects. A per-tier revoke
would strip fairly-earned 100% XP whenever a DLC drops a hunter's ProfileGame off 100.
"""
import itertools
from io import StringIO

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db.models import Sum
from django.utils import timezone

from trophies.models import (
    Contract, ContractBundle, ContractXPGrant, EarnedContract, IGDBMatch, Job,
    ProfileCareerStanding, ProfileGame, ProfileJobXP, ProgressionMilestone,
)
from trophies.services import contract_service
from trophies.util_modules.constants import JOB_XP_PER_LEVEL
from tests.factories import (
    ConceptFactory, EarnedTrophyFactory, GameFactory, IGDBMatchFactory, ProfileFactory,
    ProfileGameFactory, TrophyFactory,
)

pytestmark = pytest.mark.django_db

_igdb_seq = itertools.count(80001)   # distinct raw igdb ids per test contract/concept


def _contract(slug, job_slugs=('gunslinger',), *, igdb_id=None, xp_total_override=None):
    c = Contract.objects.create(name=slug, slug=slug, igdb_id=igdb_id or next(_igdb_seq), is_live=True,
                                xp_total_override=xp_total_override)
    c.jobs.set(Job.objects.filter(slug__in=job_slugs))
    return c


def _member(contract):
    """An ANCHORED, trusted-matched member concept of `contract`, with a platinum on its game."""
    concept = ConceptFactory(anchor_migration_completed_at=timezone.now())
    IGDBMatchFactory(concept=concept, igdb_id=contract.igdb_id)   # factory default = auto_accepted
    game = GameFactory(concept=concept)
    plat = TrophyFactory(game=game, trophy_type='platinum')
    return concept, game, plat


def _complete(profile, game, plat):
    """Platinum + 100% on `game` -- both tiers reached."""
    EarnedTrophyFactory(profile=profile, trophy=plat, earned=True)
    ProfileGameFactory(profile=profile, game=game, progress=100, has_plat=True)


def _grouped_game(concept):
    """A SECOND game filed under an existing Concept -- the wrongly-grouped state this whole lane
    exists for. Myst: the 2020 remake and the 2025 port of the original shared one Concept, so the
    original's completions satisfied the remake's Contract."""
    game = GameFactory(concept=concept)
    plat = TrophyFactory(game=game, trophy_type='platinum')
    return game, plat


def _split_game_away(game):
    """THE FIX being modelled: this game moves to its own anchored Concept with its own IGDB id, so
    it stops satisfying the Contract its old Concept keys. The old Concept (and the Contract's
    membership) SURVIVES -- which is what makes the orphaned hunter visible rather than the whole
    contract collapsing to zero members. Returns the new Concept."""
    concept = ConceptFactory(anchor_migration_completed_at=timezone.now())
    IGDBMatchFactory(concept=concept, igdb_id=next(_igdb_seq))
    game.concept = concept
    game.save(update_fields=['concept'])
    return concept


def _orphan_concept(concept):
    """Re-key a whole Concept onto its own IGDB id, dropping it out of its Contract. Used only
    where the test is ABOUT membership collapsing; prefer `_split_game_away` for the real shape."""
    match = IGDBMatch.objects.get(concept=concept)
    match.igdb_id = next(_igdb_seq)
    match.save(update_fields=['igdb_id'])


def _reconcile(slug, *extra):
    out = StringIO()
    call_command('reconcile_contracts', '--contract', slug, *extra, stdout=out, stderr=out)
    return out.getvalue()


def _banked(profile):
    return ProfileJobXP.objects.filter(profile=profile).aggregate(t=Sum('total_xp'))['t'] or 0


# --- the core case: credit for a game that left the contract -------------------------------

def test_accepted_orphan_is_revoked_and_its_xp_removed():
    profile = ProfileFactory(psn_username='myst-orig-only')
    contract = _contract('myst-remake')
    concept, _remake_game, _remake_plat = _member(contract)
    original, original_plat = _grouped_game(concept)     # wrongly filed under the remake's concept
    _complete(profile, original, original_plat)          # this hunter played only the ORIGINAL

    contract_service.mark_contract_reached(profile, contract)
    granted = contract_service.accept_contract(profile, contract)
    assert granted > 0
    assert _banked(profile) == granted

    _split_game_away(original)           # the remake's concept still keys the contract
    _reconcile('myst-remake', '--apply')

    assert not EarnedContract.objects.filter(profile=profile, contract=contract).exists()
    assert not ContractXPGrant.objects.filter(profile=profile).exists()
    assert _banked(profile) == 0
    standing = ProfileCareerStanding.objects.get(profile=profile)
    assert standing.total_xp == 0


def test_unaccepted_orphan_clears_the_stale_claimable():
    """Reached but never claimed: no XP to remove, but the reward must stop being offered."""
    profile = ProfileFactory(psn_username='never-claimed')
    contract = _contract('c-unaccepted')
    concept, _game, _plat = _member(contract)
    original, original_plat = _grouped_game(concept)
    _complete(profile, original, original_plat)
    contract_service.mark_contract_reached(profile, contract)
    assert contract_service.claimable_contracts(profile).count() == 1

    _split_game_away(original)
    _reconcile('c-unaccepted', '--apply')

    assert contract_service.claimable_contracts(profile).count() == 0
    assert _banked(profile) == 0


# --- who must NOT be touched ---------------------------------------------------------------

def test_hunter_who_completed_a_still_member_game_keeps_everything():
    """The Myst hunter who played BOTH versions. One concept leaves, one stays; they still
    qualify through the one that stayed, so the same detector the sync uses protects them."""
    profile = ProfileFactory(psn_username='played-both')
    contract = _contract('c-both')
    leaving, leaving_game, leaving_plat = _member(contract)
    _staying, staying_game, staying_plat = _member(contract)
    _complete(profile, leaving_game, leaving_plat)
    _complete(profile, staying_game, staying_plat)

    contract_service.mark_contract_reached(profile, contract)
    granted = contract_service.accept_contract(profile, contract)

    _orphan_concept(leaving)
    _reconcile('c-both', '--apply')

    assert EarnedContract.objects.filter(profile=profile, contract=contract).exists()
    assert _banked(profile) == granted


def test_dlc_progress_regression_never_revokes():
    """THE SAFETY RULE. `detect_dlc_and_refresh` adding a DLC knocks ProfileGame off 100, so the
    100% tier stops detecting even though membership never changed. A per-tier revoke would strip
    that hunter's fairly-earned XP; the all-or-nothing rule leaves them alone because the platinum
    still detects."""
    profile = ProfileFactory(psn_username='dlc-added')
    contract = _contract('c-dlc')
    _concept, game, plat = _member(contract)
    _complete(profile, game, plat)
    contract_service.mark_contract_reached(profile, contract)
    granted = contract_service.accept_contract(profile, contract)

    ProfileGame.objects.filter(profile=profile, game=game).update(progress=99)   # DLC lands
    _reconcile('c-dlc', '--apply')

    ec = EarnedContract.objects.get(profile=profile, contract=contract)
    assert ec.full_accepted_at is not None
    assert _banked(profile) == granted


def test_revoking_one_contract_leaves_another_contracts_xp_intact():
    """THE LEDGER-INVARIANT TEST. `recompute_profile_job_xp` rebuilds the cache from what SURVIVES,
    so a hunter with grants from several Contracts must land on the survivors' total -- not 0
    (rebuilt from nothing) and not the old sum (never rebuilt). Both contracts pay the same job so
    the arithmetic actually has to be right rather than incidentally separated."""
    profile = ProfileFactory(psn_username='two-contracts')
    doomed = _contract('c-doomed')
    kept = _contract('c-kept')
    doomed_concept, _dg, _dp = _member(doomed)
    doomed_game, doomed_plat = _grouped_game(doomed_concept)
    _kept_concept, kept_game, kept_plat = _member(kept)
    _complete(profile, doomed_game, doomed_plat)
    _complete(profile, kept_game, kept_plat)

    contract_service.mark_contract_reached(profile, doomed)
    contract_service.mark_contract_reached(profile, kept)
    contract_service.accept_contract(profile, doomed)
    kept_xp = contract_service.accept_contract(profile, kept)

    _split_game_away(doomed_game)
    _reconcile('c-doomed', '--apply')

    assert _banked(profile) == kept_xp
    assert ProfileCareerStanding.objects.get(profile=profile).total_xp == kept_xp
    assert EarnedContract.objects.filter(profile=profile, contract=kept).exists()
    # The surviving contract's ledger rows are untouched -- the invariant, stated directly.
    assert _banked(profile) == (ContractXPGrant.objects.filter(profile=profile)
                                .aggregate(t=Sum('amount'))['t'])


# --- command contract ----------------------------------------------------------------------

def test_preview_is_the_default_and_writes_nothing():
    profile = ProfileFactory(psn_username='preview-only')
    contract = _contract('c-preview')
    concept, _game, _plat = _member(contract)
    original, original_plat = _grouped_game(concept)
    _complete(profile, original, original_plat)
    contract_service.mark_contract_reached(profile, contract)
    granted = contract_service.accept_contract(profile, contract)
    _split_game_away(original)

    out = _reconcile('c-preview')        # no --apply

    assert 'PREVIEW' in out and 'Would revoke 1 row' in out
    assert EarnedContract.objects.filter(profile=profile, contract=contract).exists()
    assert _banked(profile) == granted


def test_reconcile_is_idempotent():
    profile = ProfileFactory(psn_username='twice')
    contract = _contract('c-twice')
    concept, _game, _plat = _member(contract)
    original, original_plat = _grouped_game(concept)
    _complete(profile, original, original_plat)
    contract_service.mark_contract_reached(profile, contract)
    contract_service.accept_contract(profile, contract)
    _split_game_away(original)

    _reconcile('c-twice', '--apply')
    out = _reconcile('c-twice', '--apply')

    assert 'No orphaned credit' in out
    assert _banked(profile) == 0


def test_unknown_slug_is_an_error():
    with pytest.raises(CommandError):
        _reconcile('no-such-contract')


def test_revoke_contract_declines_when_there_is_no_row():
    profile = ProfileFactory()
    contract = _contract('c-norow')
    assert contract_service.revoke_contract(profile, contract) == (False, 0)


def test_revoke_contract_declines_a_hunter_who_still_qualifies():
    """The TOCTOU guard. A sweep finds candidates in one pass and revokes in a second; on a
    popular Contract those are minutes apart, long enough for a sync to land a platinum on a
    concept that IS still a member. `mark_contract_reached` leaves the existing stamp alone, so
    nothing else would notice an unconditional revoke deleting credit just earned for real."""
    profile = ProfileFactory(psn_username='requalified')
    contract = _contract('c-toctou')
    _concept, game, plat = _member(contract)
    _complete(profile, game, plat)
    contract_service.mark_contract_reached(profile, contract)
    granted = contract_service.accept_contract(profile, contract)

    # Called directly, as a shell would -- the caller believes this row is orphaned; it is not.
    assert contract_service.revoke_contract(profile, contract) == (False, 0)
    assert EarnedContract.objects.filter(profile=profile, contract=contract).exists()
    assert _banked(profile) == granted


def test_revoke_contract_returns_the_xp_it_actually_removed():
    profile = ProfileFactory(psn_username='xp-return')
    contract = _contract('c-xp-return')
    concept, _game, _plat = _member(contract)
    original, original_plat = _grouped_game(concept)
    _complete(profile, original, original_plat)
    contract_service.mark_contract_reached(profile, contract)
    granted = contract_service.accept_contract(profile, contract)
    _split_game_away(original)

    assert contract_service.revoke_contract(profile, contract) == (True, granted)


# --- the decisions this lane locked in ------------------------------------------------------

def test_milestones_survive_a_revoke():
    """Decision: ProgressionMilestone is forward-only and its unique constraints mean a deleted
    rung can never be re-logged, so a journey entry sitting ahead of current XP is the smaller
    wrong. Pinned so a future 'tidy up the reversal' does not quietly un-earn them."""
    profile = ProfileFactory(psn_username='has-milestones')
    # Sized to cross a prestige rung: the default T (6000) puts one job at level 3, and the first
    # JOB_TIERS rung above the Initiate floor is level 10. 27000 XP = level 10 exactly (Apprentice).
    contract = _contract('c-milestones', xp_total_override=JOB_XP_PER_LEVEL * 9)
    concept, _game, _plat = _member(contract)
    original, original_plat = _grouped_game(concept)
    _complete(profile, original, original_plat)
    contract_service.mark_contract_reached(profile, contract)
    contract_service.accept_contract(profile, contract)

    before = ProgressionMilestone.objects.filter(profile=profile).count()
    assert before > 0, 'test is vacuous unless the accept logged a milestone'

    _split_game_away(original)
    _reconcile('c-milestones', '--apply')

    assert ProgressionMilestone.objects.filter(profile=profile).count() == before


def test_revoked_hunter_re_earns_through_the_correct_contract():
    """Decision: revoke-and-re-earn rather than re-point. The end-to-end story -- credit is removed
    from the contract they never qualified for, and the game they DID play, now anchored to its own
    IGDB id and given its own Contract, makes them claimable again through the honest route."""
    profile = ProfileFactory(psn_username='re-earner')
    remake = _contract('myst-2020')
    concept, _remake_game, _remake_plat = _member(remake)
    original_game, original_plat = _grouped_game(concept)
    _complete(profile, original_game, original_plat)
    contract_service.mark_contract_reached(profile, remake)
    contract_service.accept_contract(profile, remake)

    split_concept = _split_game_away(original_game)   # now its own anchored concept
    _reconcile('myst-2020', '--apply')
    assert _banked(profile) == 0

    # Its own Contract, keyed on the id the split gave it.
    original = Contract.objects.create(
        name='Myst (original)', slug='myst-original', is_live=True,
        igdb_id=IGDBMatch.objects.get(concept=split_concept).igdb_id,
    )
    original.jobs.set(Job.objects.filter(slug='gunslinger'))

    call_command('process_contracts', '--user', 're-earner', stdout=StringIO(), stderr=StringIO())
    assert contract_service.claimable_contracts(profile).count() == 1

    payload = contract_service.claim(profile, all_claimable=True)
    assert payload['accepted'] == ['myst-original']
    assert payload['xp'] > 0
    assert _banked(profile) == payload['xp']


# --- the drift-net -------------------------------------------------------------------------

def test_verify_profile_sync_reports_orphaned_credit():
    """The inverse direction the drift-net used to `continue` straight past."""
    from trophies.management.commands.verify_profile_sync import Command

    profile = ProfileFactory(psn_username='drifted')
    contract = _contract('c-drift')
    concept, _game, _plat = _member(contract)
    original, original_plat = _grouped_game(concept)
    _complete(profile, original, original_plat)
    contract_service.mark_contract_reached(profile, contract)
    contract_service.accept_contract(profile, contract)

    checks = {c.name: c for c in Command()._contract_drift(profile)}
    assert checks['contracts credited but no longer qualifying'].ok

    _split_game_away(original)

    checks = {c.name: c for c in Command()._contract_drift(profile)}
    orphan_check = checks['contracts credited but no longer qualifying']
    assert not orphan_check.ok and orphan_check.stored == 1
    assert checks['contracts complete but not marked reachable'].ok


# --- the guards that stop this becoming a mass-XP-deletion tool ------------------------------

def test_zero_member_contract_refuses_to_apply():
    """THE BLAST-RADIUS GUARD. When membership resolves to nothing, EVERY earner reads as orphaned
    -- and that state is reached by a mid-rematch `pending_review`, a cleared anchor, or a mistyped
    igdb_id at least as often as by a real split. Preview and apply are separate invocations that
    each re-resolve membership, so a clean preview does not bind the apply; the refusal has to live
    in the write path."""
    profile = ProfileFactory(psn_username='mass-revoke')
    contract = _contract('c-empty')
    concept, game, plat = _member(contract)
    _complete(profile, game, plat)
    contract_service.mark_contract_reached(profile, contract)
    granted = contract_service.accept_contract(profile, contract)

    _orphan_concept(concept)             # contract now resolves to ZERO members

    with pytest.raises(CommandError):
        _reconcile('c-empty', '--apply')
    assert EarnedContract.objects.filter(profile=profile, contract=contract).exists()
    assert _banked(profile) == granted

    # The preview still runs, and warns rather than refusing.
    out = _reconcile('c-empty')
    assert 'ZERO qualifiable members' in out
    assert _banked(profile) == granted

    # ...and the genuine case has an explicit way through.
    _reconcile('c-empty', '--apply', '--force-empty')
    assert not EarnedContract.objects.filter(profile=profile, contract=contract).exists()
    assert _banked(profile) == 0


def test_bundle_satisfied_credit_is_never_revoked():
    """Episodic (bundle-only) Contracts have `igdb_id=None`, so `member_concept_ids()` is EMPTY by
    design and qualification lives entirely in `_detect_tiers`'s bundle loop. Without this test,
    'optimize credit_is_orphaned to just compare against member_ids' wipes every episodic hunter
    and the suite stays green."""
    profile = ProfileFactory(psn_username='episodic')
    contract = Contract.objects.create(name='Episodic', slug='c-episodic', is_live=True, igdb_id=None)
    contract.jobs.set(Job.objects.filter(slug='gunslinger'))
    bundle = ContractBundle.objects.create(contract=contract, label='All episodes')
    for _ in range(2):
        concept = ConceptFactory()
        game = GameFactory(concept=concept)
        plat = TrophyFactory(game=game, trophy_type='platinum')
        _complete(profile, game, plat)
        bundle.concepts.add(concept)

    contract_service.mark_contract_reached(profile, contract)
    granted = contract_service.accept_contract(profile, contract)
    assert granted > 0

    # No members and no --force-empty would refuse; the bundle is what makes membership non-empty.
    _reconcile('c-episodic', '--apply')

    assert EarnedContract.objects.filter(profile=profile, contract=contract).exists()
    assert _banked(profile) == granted


def test_user_scope_limits_the_sweep_to_one_hunter():
    """`--user` so the fix can be sanity-checked on one hunter before the whole population."""
    contract = _contract('c-scoped')
    concept, _game, _plat = _member(contract)
    original, original_plat = _grouped_game(concept)
    first = ProfileFactory(psn_username='scoped-one')
    second = ProfileFactory(psn_username='scoped-two')
    for profile in (first, second):
        _complete(profile, original, original_plat)
        contract_service.mark_contract_reached(profile, contract)
        contract_service.accept_contract(profile, contract)

    _split_game_away(original)
    _reconcile('c-scoped', '--user', 'scoped-one', '--apply')

    assert not EarnedContract.objects.filter(profile=first, contract=contract).exists()
    assert EarnedContract.objects.filter(profile=second, contract=contract).exists()
    assert _banked(first) == 0
    assert _banked(second) > 0


def test_drift_net_sees_orphaned_credit_on_an_UNPUBLISHED_contract():
    """Unpublishing a contract that is paying the wrong hunters is the obvious first staff move.
    A live-only orphan pass would then report CLEAN for everyone still holding the credit -- the
    reporter and the fixer disagreeing about scope, on the very contract being fixed."""
    from trophies.management.commands.verify_profile_sync import Command

    profile = ProfileFactory(psn_username='unpublished')
    contract = _contract('c-unpublished')
    concept, _game, _plat = _member(contract)
    original, original_plat = _grouped_game(concept)
    _complete(profile, original, original_plat)
    contract_service.mark_contract_reached(profile, contract)
    contract_service.accept_contract(profile, contract)

    _split_game_away(original)
    Contract.objects.filter(pk=contract.pk).update(is_live=False)

    checks = {c.name: c for c in Command()._contract_drift(profile)}
    assert checks['contracts credited but no longer qualifying'].stored == 1


def test_an_EMPTY_bundle_does_not_disable_the_zero_member_guard():
    """The guard asks whether qualification is POSSIBLE, not whether a bundle ROW exists.

    Testing `bundles` alone meant a bundle with no concepts in it -- one `_detect_tiers` can never
    satisfy -- switched the refusal off completely. Combined with a cleared or mistyped `igdb_id`
    (the exact state the guard exists for) that turned the guard into a no-op and the command into
    a mass-XP-deletion tool."""
    profile = ProfileFactory(psn_username='empty-bundle')
    contract = _contract('c-empty-bundle')
    concept, game, plat = _member(contract)
    _complete(profile, game, plat)
    contract_service.mark_contract_reached(profile, contract)
    granted = contract_service.accept_contract(profile, contract)

    ContractBundle.objects.create(contract=contract, label='empty on purpose')   # zero concepts
    _orphan_concept(concept)                                                     # no members left

    with pytest.raises(CommandError):
        _reconcile('c-empty-bundle', '--apply')
    assert _banked(profile) == granted


def test_the_guard_does_not_block_a_single_hunter_spot_check():
    """`--user` bounds the blast radius to one hunter, so the mass-revoke guard has nothing to
    protect. Refusing there would block the deploy checklist's own "spot-check ONE hunter first"
    step in exactly the case an operator most wants it."""
    contract = _contract('c-spotcheck')
    concept, game, plat = _member(contract)
    first = ProfileFactory(psn_username='spot-one')
    second = ProfileFactory(psn_username='spot-two')
    for profile in (first, second):
        _complete(profile, game, plat)
        contract_service.mark_contract_reached(profile, contract)
        contract_service.accept_contract(profile, contract)

    _orphan_concept(concept)             # zero members: an unscoped --apply would refuse

    _reconcile('c-spotcheck', '--user', 'spot-one', '--apply')   # no --force-empty needed

    assert _banked(first) == 0
    assert _banked(second) > 0, 'a --user run must not touch anyone else'


def test_a_job_with_no_row_yet_survives_a_concurrent_revoke():
    """The lock hole. `SELECT ... FOR UPDATE` is not a predicate lock, so locking the ProfileJobXP
    rows that EXIST leaves every job the hunter has never been paid unprotected -- which is most of
    the catalogue. `revoke_contract` therefore materializes the whole catalogue at its level-1 floor
    BEFORE locking, so a concurrent claim paying a new job blocks instead of slipping between
    `recompute_profile_job_xp`'s two reads and having its XP zeroed by the floor loop.

    Asserted structurally (every job has a locked row by the time the ledger is read) rather than by
    racing two connections, which pytest-django's single transaction cannot express."""
    profile = ProfileFactory(psn_username='lock-cover')
    contract = _contract('c-lockcover')
    concept, _game, _plat = _member(contract)
    original, original_plat = _grouped_game(concept)
    _complete(profile, original, original_plat)
    contract_service.mark_contract_reached(profile, contract)
    contract_service.accept_contract(profile, contract)

    # One contract pays one job, so the hunter has exactly one row out of the whole catalogue.
    assert ProfileJobXP.objects.filter(profile=profile).count() == 1

    _split_game_away(original)
    _reconcile('c-lockcover', '--apply')

    assert (ProfileJobXP.objects.filter(profile=profile).count()
            == contract_service.catalogue_job_count()), 'the catalogue was not materialized'
    assert _banked(profile) == 0
    # The floored rows are the hunter's true state, so Pursuer Level is unchanged by materializing.
    assert (ProfileCareerStanding.objects.get(profile=profile).pursuer_level
            == contract_service._pursuer_level(profile))
