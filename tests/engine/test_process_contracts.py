"""Coverage for the `process_contracts` command: the igdb-keyed rework, and the `--contract` mode.

The original regression: the command used to `.prefetch_related('memberships', ...)` -- but
ContractMembership was removed and members are now igdb-derived, so evaluating that queryset crashed
with `AttributeError: Cannot find 'memberships' on Contract object`. Those pins remain below.

The rest covers `--contract <slug>`, the targeted additive sweep -- the counterpart to
`reconcile_contracts --contract`, added so credit could be handed back for one named Contract as
promptly as it can be taken away. Its sharp edges are the interaction with `--incremental` (which
narrows the same queryset) and the nightly watermark, which a targeted run must never write.
"""
import itertools
from datetime import timedelta
from io import StringIO
from unittest.mock import patch

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError
from django.utils import timezone

from trophies.management.commands.process_contracts import Command
from trophies.models import Contract, EarnedContract
from tests.factories import (
    ConceptFactory, EarnedTrophyFactory, GameFactory, IGDBMatchFactory, ProfileFactory,
    ProfileGameFactory, TrophyFactory,
)

pytestmark = pytest.mark.django_db

_igdb_seq = itertools.count(90001)


def _live_contract_with_member():
    """A live Contract keyed on an igdb id + one anchored, trusted-matched member concept/game."""
    igdb_id = next(_igdb_seq)
    contract = Contract.objects.create(name='C', slug=f'c-{igdb_id}', is_live=True, igdb_id=igdb_id)
    concept = ConceptFactory(anchor_migration_completed_at=timezone.now())
    IGDBMatchFactory(concept=concept, igdb_id=igdb_id)   # factory default status = auto_accepted
    GameFactory(concept=concept)
    return contract


def _completable_contract(slug=None, *, is_live=True, with_platinum=True):
    """A Contract whose single member game has a platinum, so a hunter can actually reach it.

    `with_platinum=False` builds one whose member game has NO platinum trophy -- the shape where
    `platinum_reached_at` can never be stamped, which is its own case for the candidate exclusion.
    """
    igdb_id = next(_igdb_seq)
    contract = Contract.objects.create(name=slug or f'C{igdb_id}', slug=slug or f'c-{igdb_id}',
                                       is_live=is_live, igdb_id=igdb_id)
    concept = ConceptFactory(anchor_migration_completed_at=timezone.now())
    IGDBMatchFactory(concept=concept, igdb_id=igdb_id)
    game = GameFactory(concept=concept)
    plat = TrophyFactory(game=game, trophy_type='platinum') if with_platinum else None
    return contract, game, plat


def _complete(profile, game, plat):
    if plat is not None:
        EarnedTrophyFactory(profile=profile, trophy=plat, earned=True)
    ProfileGameFactory(profile=profile, game=game, progress=100, has_plat=plat is not None)


def _run(*args):
    out = StringIO()
    call_command('process_contracts', *args, stdout=out, stderr=out)
    return out.getvalue()


def test_process_contracts_all_runs_without_memberships_prefetch():
    ProfileFactory()
    _live_contract_with_member()
    _run('--all', '--dry-run')   # must not raise on the live-contracts queryset evaluation


def test_process_contracts_user_runs_without_memberships_prefetch():
    ProfileFactory(psn_username='pc-user')
    _live_contract_with_member()
    _run('--user', 'pc-user', '--dry-run')


# --- --contract: the targeted additive sweep --------------------------------------------------

def test_contract_scope_sweeps_every_candidate_for_that_contract():
    """The counterpart to `reconcile_contracts --contract`. Without it the engine was asymmetric:
    credit could be REMOVED for one named Contract immediately, but handing it back waited for the
    nightly sweep -- so hunters revoked during a re-key sat with their XP dipped for up to a day."""
    contract, game, plat = _completable_contract('c-targeted')
    hunters = [ProfileFactory(psn_username=f'target-{i}') for i in range(3)]
    for profile in hunters:
        _complete(profile, game, plat)

    _run('--contract', 'c-targeted')

    for profile in hunters:
        ec = EarnedContract.objects.get(profile=profile, contract=contract)
        assert ec.platinum_reached_at is not None and ec.full_reached_at is not None
        assert ec.platinum_accepted_at is None, 'detection must never auto-accept'


def test_contract_scope_leaves_other_contracts_alone():
    """The whole point of naming one: a targeted run must not stamp the rest of the catalogue."""
    target, target_game, target_plat = _completable_contract('c-in-scope')
    other, other_game, other_plat = _completable_contract('c-out-of-scope')
    profile = ProfileFactory(psn_username='both-done')
    _complete(profile, target_game, target_plat)
    _complete(profile, other_game, other_plat)

    _run('--contract', 'c-in-scope')

    assert EarnedContract.objects.filter(profile=profile, contract=target).exists()
    assert not EarnedContract.objects.filter(profile=profile, contract=other).exists()


def test_contract_scope_combines_with_user_for_a_spot_check():
    """BOTH axes must narrow. A second live contract that `spot-a` has also completed is what makes
    this test about `--contract` at all -- with only one contract in the world, `--user` alone
    satisfies every assertion and the flag could be deleted with the suite still green."""
    contract, game, plat = _completable_contract('c-spot')
    other, other_game, other_plat = _completable_contract('c-spot-other')
    one = ProfileFactory(psn_username='spot-a')
    two = ProfileFactory(psn_username='spot-b')
    _complete(one, game, plat)
    _complete(one, other_game, other_plat)
    _complete(two, game, plat)

    _run('--contract', 'c-spot', '--user', 'spot-a')

    assert EarnedContract.objects.filter(profile=one, contract=contract).exists()
    assert not EarnedContract.objects.filter(profile=one, contract=other).exists(), (
        'the --contract scope did not narrow the --user path'
    )
    assert not EarnedContract.objects.filter(profile=two, contract=contract).exists()


def test_contract_scope_honours_dry_run():
    contract, game, plat = _completable_contract('c-dry')
    profile = ProfileFactory(psn_username='dry-hunter')
    _complete(profile, game, plat)

    _run('--contract', 'c-dry', '--dry-run')

    assert not EarnedContract.objects.filter(profile=profile, contract=contract).exists()


def test_contract_scope_refuses_a_draft_contract():
    """Detection is deliberately live-only, and the reached stamp is not something a later
    un-publish takes back -- so stamping a draft would make it claimable before curation is done.
    Note this is the OPPOSITE of reconcile_contracts, which ignores is_live on purpose."""
    contract, game, plat = _completable_contract('c-draft', is_live=False)
    profile = ProfileFactory(psn_username='draft-hunter')
    _complete(profile, game, plat)

    with pytest.raises(CommandError, match='not live'):
        _run('--contract', 'c-draft')
    assert not EarnedContract.objects.filter(profile=profile, contract=contract).exists()


def test_contract_scope_distinguishes_missing_from_draft():
    """Two different mistakes with two different fixes; one message for both sends a curator
    hunting for a typo that is not there."""
    _completable_contract('c-real-draft', is_live=False)
    with pytest.raises(CommandError, match='No Contract with slug'):
        _run('--contract', 'no-such-slug')
    with pytest.raises(CommandError, match='not live'):
        _run('--contract', 'c-real-draft')


@pytest.mark.parametrize('extra', [(), ('--all',)])
def test_targeted_sweep_never_stamps_the_nightly_watermark(extra):
    """A targeted run has covered ONE Contract. Letting it write the incremental watermark would
    tell the nightly it had swept the whole catalogue, so every Contract published since the last
    real full pass would be skipped until FULL_SWEEP_INTERVAL forced one -- silently, because a
    too-recent watermark looks exactly like a clean run.

    PARAMETRIZED over `--all` because the version without it pinned the wrong guard: the only
    invocation that could actually reach a watermark write on a targeted path was
    `--contract X --all --incremental`, and that combination went untested while it was broken."""
    _completable_contract('c-watermark')
    with patch.object(Command, '_set_watermark') as spy:
        _run('--contract', 'c-watermark', '--incremental', *extra)
    assert not spy.called, 'a targeted sweep wrote the nightly watermark'


def test_targeted_sweep_ignores_the_incremental_changed_since_filter():
    """THE BUG THIS PINS. `--contract` and `--incremental` narrowed the SAME queryset in sequence,
    and only the slug narrowing was validated -- so a Contract that had not changed since the last
    run was filtered out AFTER the "is it live?" guard passed, and the command reported the
    nightly's "No Contracts changed since the last run" with exit 0 and did nothing.

    That is this flag's headline case, not an edge: a Contract's `updated_at` does NOT move when its
    MEMBERSHIP changes (members are igdb-derived), so a concept anchored today joins an untouched
    Contract and the incremental filter is exactly what hides it. Naming a Contract must OVERRIDE
    "has it changed since last night"."""
    contract, game, plat = _completable_contract('c-unchanged')
    profile = ProfileFactory(psn_username='unchanged-hunter')
    _complete(profile, game, plat)
    # A watermark NEWER than the contract: the incremental filter would exclude it.
    Contract.objects.filter(pk=contract.pk).update(
        updated_at=timezone.now() - timedelta(days=1))

    with patch.object(Command, '_get_watermark', return_value=timezone.now()):
        out = _run('--contract', 'c-unchanged', '--all', '--incremental')

    assert 'No Contracts changed' not in out
    assert EarnedContract.objects.filter(profile=profile, contract=contract).exists(), (
        'the targeted sweep was silently swallowed by the incremental filter'
    )


def test_dry_run_does_not_advance_the_nightly_watermark():
    """`--dry-run` promises "write nothing", and a watermark IS a write -- a preview that advances
    the nightly's cursor past contracts it never processed is the worst kind, because the real run
    then skips them. (Pre-existing; the empty-queryset branch was missing the guard its sibling
    already had.)"""
    with patch.object(Command, '_get_watermark', return_value=timezone.now()):
        with patch.object(Command, '_set_watermark') as spy:
            _run('--all', '--incremental', '--dry-run')
    assert not spy.called, 'a dry run advanced the nightly watermark'


def test_no_scope_flag_is_an_error_that_names_all_three_modes():
    _completable_contract('c-nomode')   # else the "no live Contracts" branch answers first
    out = _run()
    assert '--contract' in out and '--all' in out and '--user' in out


# -- the sweep only looks at what it can still change -----------------------------------------------

def test_a_fully_stamped_hunter_is_not_a_candidate():
    """WHERE THE COST WAS. A Contract can only ever write `platinum_reached_at` and
    `full_reached_at`, and only when they are None -- so a hunter with both already set cannot
    produce a mark tonight or any night, yet every sweep re-ran full tier detection on them. In
    production that was 461 of every 462 candidates."""
    contract, game, plat = _completable_contract('c-settled')
    profile = ProfileFactory(psn_username='settled-hunter')
    _complete(profile, game, plat)

    out = _run('--all')
    assert '1 candidate(s) -> 2 new tier mark(s)' in out or '1 candidate(s)' in out

    out = _run('--all')
    # AND THE SETTLED COUNT SAYS SO. `0 candidate(s)` alone is what a broken candidate query
    # prints too, which is why the line reports the skipped total.
    assert '0 candidate(s), 1 settled' in out, (
        'a fully stamped hunter is still being re-evaluated nightly'
    )


def test_a_half_stamped_hunter_is_still_a_candidate():
    """Only BOTH applicable stamps settle a hunter. Excluding on the 100% tier alone would strand
    every platinum that had not been reached yet."""
    contract, game, plat = _completable_contract('c-half')
    profile = ProfileFactory(psn_username='half-hunter')
    _complete(profile, game, plat)
    _run('--all')

    # Give the platinum tier back: there is something left to stamp again.
    EarnedContract.objects.filter(profile=profile, contract=contract).update(platinum_reached_at=None)

    out = _run('--all')
    assert '1 candidate(s), 0 settled -> 1 new tier mark(s)' in out, (
        'a hunter with an unstamped tier was excluded'
    )


def test_a_hunter_who_has_only_the_platinum_is_still_a_candidate():
    """The mirror of the case above, and the one that catches a `settled` set built without the 100%
    condition: platinum reached, 100% not. Filtering on the platinum stamp alone would call that
    hunter settled and never stamp their full tier."""
    contract, game, plat = _completable_contract('c-plat-only')
    profile = ProfileFactory(psn_username='plat-only-hunter')
    _complete(profile, game, plat)
    _run('--all')

    EarnedContract.objects.filter(profile=profile, contract=contract).update(full_reached_at=None)

    out = _run('--all')
    assert '1 candidate(s), 0 settled -> 1 new tier mark(s)' in out, (
        'a hunter with an unstamped 100% tier was treated as settled'
    )


def test_a_contract_with_no_platinum_settles_on_the_full_tier_alone():
    """`platinum_reached_at` can never be stamped where no member game HAS a platinum, so requiring
    it would make those hunters permanent candidates -- the exact re-confirmation this removes, on
    the contracts least able to escape it."""
    contract, game, _plat = _completable_contract('c-no-plat', with_platinum=False)
    profile = ProfileFactory(psn_username='no-plat-hunter')
    _complete(profile, game, None)

    _run('--all')
    out = _run('--all')

    assert '0 candidate(s), 1 settled' in out


def test_the_platinum_question_is_asked_fresh_not_read_off_the_row():
    """`EarnedContract.has_platinum` is frozen when the row is created and never updated, while
    membership is IGDB-derived and can gain a platinum-bearing game later. Excluding on the frozen
    value would strand that hunter's platinum tier for good, silently."""
    contract, game, plat = _completable_contract('c-frozen')
    profile = ProfileFactory(psn_username='frozen-hunter')
    _complete(profile, game, plat)
    _run('--all')

    # The row says "no platinum here" while the contract now has one, and the platinum tier is
    # unstamped. Reading the row would exclude this hunter; asking the contract does not.
    EarnedContract.objects.filter(profile=profile, contract=contract).update(
        has_platinum=False, platinum_reached_at=None)

    out = _run('--all')
    assert '1 candidate(s)' in out, 'the sweep trusted the frozen flag and skipped a reachable tier'


def test_the_platinum_answer_cannot_be_forgotten_at_a_call_site():
    """A falsy `has_plat` degrades the exclusion to its WEAK form -- skipping everyone with the 100%
    tier stamped whether or not their platinum is reachable and unstamped, which is the exact silent
    miss the fresh question exists to prevent. It carried a `None` default, so one forgetful caller
    selected that failure with no error and no sign beyond a smaller candidate count."""
    import inspect

    sig = inspect.signature(Command._candidate_profiles)
    param = sig.parameters['has_plat']

    assert param.kind is inspect.Parameter.KEYWORD_ONLY, 'has_plat can be passed positionally again'
    assert param.default is inspect.Parameter.empty, 'has_plat has a default that can only be wrong'


def test_a_hunter_with_no_row_yet_is_always_a_candidate():
    """The exclusion is over rows that exist, so a first-time completion is never filtered out."""
    contract, game, plat = _completable_contract('c-first-time')
    profile = ProfileFactory(psn_username='first-timer')
    _complete(profile, game, plat)

    assert not EarnedContract.objects.filter(profile=profile).exists()
    out = _run('--all')

    assert '1 candidate(s)' in out
    assert EarnedContract.objects.filter(profile=profile, contract=contract).exists()
