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
from contextlib import contextmanager
from datetime import timedelta
from io import StringIO
from unittest.mock import patch

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError
from django.utils import timezone

from trophies.management.commands.process_contracts import (
    FULL_SWEEP_INTERVAL, FULL_WATERMARK_KEY, WATERMARK_KEY, Command,
)
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

    # Through the STORE, not a patched accessor: patching `_get_watermark` here stopped meaning
    # anything once the command grew a second accessor, and a patch that no longer influences the
    # code under test reads to the next person like coverage that is not there.
    now_iso = timezone.now().isoformat()
    with _fake_redis(**{WATERMARK_KEY: now_iso, FULL_WATERMARK_KEY: now_iso}):
        out = _run('--contract', 'c-unchanged', '--all', '--incremental')

    assert 'No Contracts changed' not in out
    assert EarnedContract.objects.filter(profile=profile, contract=contract).exists(), (
        'the targeted sweep was silently swallowed by the incremental filter'
    )


@pytest.mark.parametrize('scenario', ['swept', 'quiet', 'full'])
def test_dry_run_does_not_advance_either_watermark(scenario):
    """`--dry-run` promises "write nothing", and a watermark IS a write -- a preview that advances the
    nightly's cursor past contracts it never processed is the worst kind, because the real run then
    skips them.

    THREE scenarios because a dry-run guard can be missing on either key independently. The swept and
    quiet branches both take the incremental path, which writes with `full=False` and so never touches
    `FULL_WATERMARK_KEY` at all -- assert on that key from either one and the assertion cannot fail.
    The 'full' case is the only shape where a non-dry run stamps BOTH, so it is the only one that pins
    the full stamp.

    THIS TEST WAS VACUOUS FOR A WHILE, and the way it went vacuous is the lesson. It patched
    `_get_watermark` only. When the command grew a second accessor, the unpatched one fell through to
    real Redis, returned None, forced a full sweep, and -- because the test created no Contract -- the
    run left through the "No live Contracts" early return, which writes no watermark under any
    circumstances. `assert not spy.called` then held for a reason that had nothing to do with
    `--dry-run`: both guards could be deleted with the test still green.
    """
    a_day_ago = (timezone.now() - timedelta(days=1)).isoformat()
    stale_full = (timezone.now() - FULL_SWEEP_INTERVAL - timedelta(hours=1)).isoformat()
    full_seed = stale_full if scenario == 'full' else a_day_ago

    contract, _game, _plat = _completable_contract('c-dryrun-wm')
    if scenario == 'quiet':
        Contract.objects.filter(pk=contract.pk).update(
            updated_at=timezone.now() - timedelta(days=3))     # older than the cursor: quiet branch

    with _fake_redis(**{WATERMARK_KEY: a_day_ago, FULL_WATERMARK_KEY: full_seed}) as fake:
        out = _run('--all', '--incremental', '--dry-run')

    expected = {'swept': 'incremental (1 changed)',
                'quiet': 'No Contracts changed',
                'full': 'FULL sweep'}[scenario]
    assert expected in out, f'the run did not reach the branch under test: {out!r}'
    assert _stamp(fake, WATERMARK_KEY) == a_day_ago, 'a dry run advanced the scoping cursor'
    assert _stamp(fake, FULL_WATERMARK_KEY) == full_seed, 'a dry run stamped a full pass'


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



# -- --incremental watermarks: two keys, two questions ---------------------------------------------

#: Patch target for the command module's own globals (`redis_client`, `CURSOR_GRACE`).
_CMD = 'trophies.management.commands.process_contracts'


@contextmanager
def _fake_redis(**initial):
    """Route the command's raw redis client through an in-memory fake, seeded with watermarks.

    A STORE RATHER THAN A PATCHED ACCESSOR, deliberately. Every incremental test here used to patch
    `_get_watermark`, and that is precisely why the wrong-key bug survived them: a patched accessor
    answers whatever the test asked for no matter which key the code reads, so the one thing that was
    broken was the one thing unobservable. Worse, when the command grew a SECOND accessor those
    patches silently stopped covering their own subject (see the dry-run test above). Going through a
    store makes the KEY part of what is under test, and cannot rot that way.

    `fakeredis` rather than a hand-rolled dict, for one specific reason: the real client is built
    WITHOUT `decode_responses` (`cache.get_redis_client`), so `get` returns BYTES. A dict fake hands
    back the `str` it was given, which would leave `_read_watermark`'s decode branch exercised only in
    production -- delete the `.decode()` and a dict-backed suite stays green while every real run reads
    an unusable watermark and sweeps the whole catalogue nightly. It is also the project's convention:
    `fakeredis` is in requirements-dev for exactly "tests that touch the raw redis client".
    """
    import fakeredis

    client = fakeredis.FakeStrictRedis(server=fakeredis.FakeServer())
    for key, value in initial.items():
        client.set(key, value)
    with patch(_CMD + '.redis_client', client):
        yield client


@contextmanager
def _no_grace():
    """Zero `CURSOR_GRACE` for tests that reason about the window at sub-minute resolution.

    NOT a workaround -- it is what keeps those tests honest. The margin is one minute, and a test
    publishes its waves milliseconds apart, so every wave falls inside the previous run's margin and
    the margin alone would satisfy them: they would pass on the buggy key too, and go quietly vacuous.
    Zeroing it isolates the question each test is actually asking (which key is read, and which
    timestamp is stamped) from the margin, which has its own test below at real-world spacing.
    """
    with patch(_CMD + '.CURSOR_GRACE', timedelta(0)):
        yield


def _stamp(client, key):
    """A stored watermark, decoded the way the command decodes it. None when unset."""
    raw = client.get(key)
    return raw.decode() if raw is not None else None


def _publish(slug):
    """A live Contract whose `updated_at` is NOW -- one wave of the curator's publishing."""
    contract, _game, _plat = _completable_contract(slug)
    Contract.objects.filter(pk=contract.pk).update(updated_at=timezone.now())
    return contract


def _backdate(contract, **delta):
    Contract.objects.filter(pk=contract.pk).update(updated_at=timezone.now() - timedelta(**delta))


def test_consecutive_incremental_runs_do_not_re_sweep_the_previous_wave():
    """THE BUG THIS PINS. The `updated_at__gt` narrowing read `contract_detection:last_full_run`, so
    an incremental run scoped to "changed since the last FULL pass" -- re-sweeping everything every
    incremental run since that pass had already covered. Publishing in waves reported
    `incremental (150 changed)`, then 300, then 450, then 600: each run redoing the previous run's
    work, reporting the redo as change, and looking exactly like a busy catalogue while doing it.

    `contract_detection:last_run` was written by every run and read by nobody, which is the shape of
    the defect: one stored value answering two different questions, right about only one of them.
    """
    # A baseline Contract so the first run reaches the sweep at all: a full pass that finds NOTHING
    # live returns early and stamps no watermark, which would leave run two a full sweep as well.
    _publish('c-baseline')
    with _fake_redis(), _no_grace():
        _run('--all', '--incremental')                     # first run: full sweep, stamps both keys

        _publish('c-wave-a')
        first = _run('--all', '--incremental')
        assert 'incremental (1 changed)' in first

        _publish('c-wave-b')
        second = _run('--all', '--incremental')

    assert 'incremental (1 changed)' in second, (
        f'the second wave re-swept the first: {second!r}'
    )
    # `c-wave-a` is the contract's NAME as well as its slug (see `_completable_contract`), and
    # `_process_all` prints one line per swept contract by name -- so its absence is the assertion
    # that it was not swept again. It is also not a substring of `c-wave-b`.
    assert 'c-wave-a' not in second, 'an unchanged Contract from the previous wave was swept again'


def test_an_incremental_run_advances_the_scoping_cursor_but_not_the_full_stamp():
    """The two keys move on different schedules, and the window is one RUN wide, not one full-pass
    wide. Seeded to DIFFERENT values on purpose: with both keys holding the same timestamp this test
    passes whichever key the code reads, which is exactly the blind spot that let the bug through.

    The Contract updated 4 days ago sits BETWEEN them -- already covered by the last run, not yet
    covered by the last full pass. Scoping from the full stamp sweeps it; scoping from the cursor does
    not. That single row is the discriminator.
    """
    cursor = (timezone.now() - timedelta(days=2)).isoformat()
    full = (timezone.now() - timedelta(days=6)).isoformat()
    _backdate(_publish('c-already-swept'), days=4)
    _publish('c-genuinely-new')

    with _fake_redis(**{WATERMARK_KEY: cursor, FULL_WATERMARK_KEY: full}) as fake:
        out = _run('--all', '--incremental')

    assert 'FULL sweep' not in out, 'a 6-day-old full pass should not have forced another'
    assert 'incremental (1 changed)' in out, f'the window was not one run wide: {out!r}'
    assert 'c-already-swept' not in out, 'a Contract the previous run already covered was swept again'
    assert _stamp(fake, FULL_WATERMARK_KEY) == full, 'an incremental run moved the full stamp'
    assert _stamp(fake, WATERMARK_KEY) != cursor, 'the scoping cursor did not advance'


def test_the_weekly_full_pass_still_keys_off_the_full_stamp():
    """The invariant the split has to preserve, and the reason the full stamp cannot simply be dropped.
    Membership is IGDB-derived, so a Contract's `updated_at` does NOT move when it gains a member --
    the forced full pass is the only thing that ever sees that, and a cursor advancing every night must
    not be able to postpone it. (This guards the split rather than pinning the original bug: it fails
    if the full-pass decision is ever pointed at the cursor.)"""
    with _fake_redis(**{
        WATERMARK_KEY: timezone.now().isoformat(),                                # swept minutes ago
        FULL_WATERMARK_KEY: (timezone.now() - FULL_SWEEP_INTERVAL - timedelta(hours=1)).isoformat(),
    }):
        _publish('c-weekly')
        out = _run('--all', '--incremental')

    assert 'FULL sweep' in out, 'a stale full pass was postponed by a fresh incremental cursor'


def test_a_quiet_run_still_advances_the_cursor():
    """Nothing changed is the normal nightly outcome, and it must still close the window -- leaving the
    cursor put would make the next run re-sweep whatever the last one already covered.

    Seeded to different values for the same reason as the sibling above: the 1-day-old Contract is
    inside the full-pass window but outside the cursor's, so reading the wrong key turns this from a
    quiet run into a sweep and the first assertion fails."""
    cursor = (timezone.now() - timedelta(hours=6)).isoformat()
    full = (timezone.now() - timedelta(days=3)).isoformat()
    _backdate(_publish('c-quiet'), days=1)

    with _fake_redis(**{WATERMARK_KEY: cursor, FULL_WATERMARK_KEY: full}) as fake:
        out = _run('--all', '--incremental')

    assert 'No Contracts changed' in out, f'a quiet run swept something: {out!r}'
    assert _stamp(fake, WATERMARK_KEY) != cursor, 'a quiet run left the window open'
    assert _stamp(fake, FULL_WATERMARK_KEY) == full, 'a quiet run stamped a full pass it never did'


def test_a_contract_published_mid_run_is_not_stamped_as_already_swept():
    """The watermark is stamped at the run's START, not its end. The run read the catalogue at that
    instant, so that is the only instant it can claim to have covered -- a curator publishing a wave
    while the sweep is mid-flight must fall AFTER the cursor. Stamping the END silently swallowed that
    wave until the weekly full pass forced a look.

    `_no_grace` for the reason its own docstring gives: the margin happens to rescue this case too, but
    only because the test completes in milliseconds. In production a full sweep runs for minutes while
    the margin is one minute, so `started_at` is what actually protects a real mid-run publish."""
    real_process_all = Command._process_all

    def _publish_mid_run(self, contracts, dry_run):
        result = real_process_all(self, contracts, dry_run)
        _publish('c-mid-run')          # the curator hits "Mark LIVE" while the sweep is running
        return result

    _publish('c-mid-baseline')        # else the first run finds nothing live and never sweeps at all
    with _fake_redis(), _no_grace():
        with patch.object(Command, '_process_all', _publish_mid_run):
            _run('--all', '--incremental')
        out = _run('--all', '--incremental')

    assert 'incremental (1 changed)' in out, (
        f'a Contract published during the previous run was stamped as swept: {out!r}'
    )
    assert 'c-mid-run' in out


def test_the_grace_margin_catches_a_publish_that_committed_after_the_cursor():
    """The commit-visibility race `started_at` alone cannot close. `Contract.updated_at` is stamped in
    Python (`auto_now`, and the admin's bulk actions stamp it explicitly), so a row's timestamp always
    predates the instant its transaction commits and becomes visible to the sweep. A `Mark LIVE` whose
    stamp lands just before the cursor but whose COMMIT lands just after would otherwise be recorded as
    swept, and after that only the weekly full pass would ever look again.

    Note `>=` would not help: in this race `updated_at` is strictly LESS than the watermark, which is
    why the fix is a lookback margin rather than a change of operator."""
    cursor = timezone.now()
    contract = _publish('c-raced')
    # Stamped 2 seconds BEFORE the cursor: inside the margin, where a row whose commit the previous run
    # could not yet see still lives. ANCHORED TO `cursor`, not to a fresh `now()` -- `_backdate` would
    # compute the offset after the factories have built a Contract, Concept, IGDBMatch, Game and
    # Trophy, so on any run slower than 2s the row would land AFTER the cursor, be swept with no margin
    # involved, and pass this test without exercising the thing it exists for.
    Contract.objects.filter(pk=contract.pk).update(updated_at=cursor - timedelta(seconds=2))

    with _fake_redis(**{
        WATERMARK_KEY: cursor.isoformat(),
        FULL_WATERMARK_KEY: (timezone.now() - timedelta(days=1)).isoformat(),
    }):
        out = _run('--all', '--incremental')

    # A FULL sweep would print `c-raced` too, so the positive assertion alone is satisfiable without
    # the margin ever applying -- any regression that makes the cursor unreadable would pass it.
    assert 'FULL sweep' not in out, 'a full sweep printed c-raced without exercising the margin'
    assert 'incremental (1 changed)' in out, f'the margin did not reach back over the row: {out!r}'
    assert 'c-raced' in out, f'a Contract that committed after the cursor was never swept: {out!r}'


@pytest.mark.parametrize('blind_key', [WATERMARK_KEY, FULL_WATERMARK_KEY])
def test_an_unreadable_watermark_falls_back_to_a_full_sweep(blind_key):
    """Redis half-available: one key readable, the other raising. `_read_watermark` returns None on any
    failure, and None must always degrade toward MORE work, never toward skipping some -- a watermark
    that cannot be read must not be treated as "nothing has changed". Parametrized over which key fails
    because the two reach the decision by different routes."""
    import fakeredis
    import redis

    class _BlindTo:
        """A client that raises on reads of ONE key, leaving the other readable.

        `redis.exceptions.ConnectionError`, NOT the builtin of the same name: the two are in disjoint
        hierarchies (the builtin is an `OSError`, redis's is a `RedisError`). `_read_watermark` catches
        bare `Exception` so either works today, but raising the builtin would make this test lie under
        the natural tightening -- narrow that except to `redis.RedisError` and a test raising `OSError`
        fails while production is fine, which sends someone hunting a bug that is not there.
        """

        def __init__(self, client, key):
            self._client, self._key = client, key

        def get(self, key):
            if key == self._key:
                raise redis.exceptions.ConnectionError('redis is half-down')
            return self._client.get(key)

        def set(self, key, value):
            return self._client.set(key, value)

    inner = fakeredis.FakeStrictRedis(server=fakeredis.FakeServer())
    fresh = timezone.now().isoformat()
    inner.set(WATERMARK_KEY, fresh)
    inner.set(FULL_WATERMARK_KEY, fresh)          # both fresh: a readable pair would sweep nothing
    _backdate(_publish('c-blind'), days=2)        # older than either watermark

    with patch(_CMD + '.redis_client', _BlindTo(inner, blind_key)):
        out = _run('--all', '--incremental')

    assert 'FULL sweep' in out, f'an unreadable watermark skipped work instead of sweeping: {out!r}'
    assert 'c-blind' in out


def test_a_full_sweep_advances_both_keys():
    """The positive counterpart to the tests above, which all assert what does NOT move. A full pass
    has covered the whole catalogue, so it is the one run entitled to stamp both -- and both must carry
    the SAME instant, because they are two records of one run and a drift between them would quietly
    shift the next window. Previously only implied by the first run of the consecutive-runs test."""
    _publish('c-full-both')

    with _fake_redis() as fake:                                   # empty store: full sweep
        out = _run('--all', '--incremental')

    assert 'FULL sweep' in out
    cursor, full = _stamp(fake, WATERMARK_KEY), _stamp(fake, FULL_WATERMARK_KEY)
    assert cursor is not None, 'a full sweep left the scoping cursor unset'
    assert full is not None, 'a full sweep did not record itself as a full pass'
    assert cursor == full, 'the two keys recorded different instants for one run'


def test_a_single_user_run_never_stamps_a_watermark():
    """`--user` has covered ONE account, not the catalogue, so it may not tell the nightly it swept.
    The same reasoning as the targeted `--contract` sweep above, and a documented invariant that had no
    test: the write site is skipped because the `--user` branch returns before reaching it, which is
    true by control flow today and exactly the kind of thing a later refactor moves."""
    contract, game, plat = _completable_contract('c-user-wm')
    profile = ProfileFactory(psn_username='wm-user')
    _complete(profile, game, plat)
    fresh = timezone.now().isoformat()

    with _fake_redis(**{WATERMARK_KEY: fresh, FULL_WATERMARK_KEY: fresh}) as fake:
        _run('--user', 'wm-user', '--all', '--incremental')

    # The run DID do its work -- otherwise this asserts nothing about watermarks.
    assert EarnedContract.objects.filter(profile=profile, contract=contract).exists()
    assert _stamp(fake, WATERMARK_KEY) == fresh, 'a --user run advanced the scoping cursor'
    assert _stamp(fake, FULL_WATERMARK_KEY) == fresh, 'a --user run stamped a full pass'
