"""Tests for the job-drift scanner (Contract jobs vs current IGDB detection).

Three things are worth pinning here:

1. The bucket rule, exhaustively, as pure functions -- it is the thing a future repair
   pipeline would act on, so every branch gets a case.
2. THE EQUIVALENCE: the batched `JobDriftScanner` must produce exactly what the per-contract
   `job_detection.suggest_jobs_for_contract` produces. The scanner exists only to avoid ~5
   queries per contract; the moment the two answers differ, the report describes a rule the
   admin action and the staging pipeline do not follow, and every number it prints is fiction.
   Nothing in the types catches that, so it is a test.
3. The XP exposure figures, including the banked/pending split -- the column the whole report
   is read by.
"""
import itertools

import pytest
from django.core.management import call_command
from django.utils import timezone
from io import StringIO

from trophies.models import (
    Concept, ConceptGenre, ConceptTheme, Contract, ContractBundle, Genre, Job, Theme,
)
from trophies.services import contract_service, job_drift
from trophies.services.job_detection import suggest_jobs_for_contract
from tests.factories import (
    ConceptFactory, EarnedTrophyFactory, GameFactory, IGDBMatchFactory, ProfileFactory,
    ProfileGameFactory, TrophyFactory,
)

pytestmark = pytest.mark.django_db

_igdb_seq = itertools.count(91001)
_tag_seq = itertools.count(91001)

#: A SENTINEL, not None. `igdb_id=None` is the real episodic shape (members come from bundles
#: instead), so it has to be passable -- with None as the default-and-"unset" marker the helper
#: silently coalesced it back to a fresh id and every "episodic" fixture got a real igdb_id,
#: leaving the `igdb_id is None` branches of the scanner with no coverage at all.
_AUTO = object()


# --- helpers ----------------------------------------------------------------------------

def _contract(slug, job_slugs, *, igdb_id=_AUTO, is_live=True):
    c = Contract.objects.create(name=slug, slug=slug, is_live=is_live,
                                igdb_id=next(_igdb_seq) if igdb_id is _AUTO else igdb_id)
    c.jobs.set(Job.objects.filter(slug__in=job_slugs))
    return c


def _tagged_member(contract, genres=(), themes=()):
    """An ANCHORED, trusted-matched member concept carrying the given IGDB genres/themes."""
    concept = ConceptFactory(anchor_migration_completed_at=timezone.now())
    IGDBMatchFactory(concept=concept, igdb_id=contract.igdb_id)   # default status auto_accepted
    _tag(concept, genres, themes)
    return concept


def _tag(concept, genres=(), themes=()):
    """Attach IGDB genres/themes by NAME, creating the master rows on first use.

    Ids and slugs come from a SEQUENCE, never `hash(name)`: `Genre.igdb_id`/`slug` (and Theme's)
    are both unique, and str hashing is salted per interpreter process, so a hash-derived id
    narrowed into a 100k range is a fresh collision draw every test session -- a real
    IntegrityError flake, and non-reproducible fixture data between runs.
    """
    for name in genres:
        n = next(_tag_seq)
        genre, _ = Genre.objects.get_or_create(name=name, defaults={'igdb_id': n, 'slug': f'g-{n}'})
        ConceptGenre.objects.get_or_create(concept=concept, genre=genre)
    for name in themes:
        n = next(_tag_seq)
        theme, _ = Theme.objects.get_or_create(name=name, defaults={'igdb_id': n, 'slug': f't-{n}'})
        ConceptTheme.objects.get_or_create(concept=concept, theme=theme)


def _bucket_of(contract):
    rows = {r['contract_id']: r for r in job_drift.scan()}
    return rows[contract.pk]['bucket']


# --- the rule (pure) --------------------------------------------------------------------

def test_aligned_when_sets_match():
    assert job_drift.classify({'champion'}, {'champion'})[0] == job_drift.ALIGNED


def test_unjobbed_beats_everything_including_empty_detection():
    # An unjobbed contract banks zero XP on claim, so it is reported as unjobbed whatever
    # detection says -- including when detection is also empty.
    assert job_drift.classify(set(), {'champion'})[0] == job_drift.UNJOBBED
    assert job_drift.classify(set(), set())[0] == job_drift.UNJOBBED


def test_no_signal_when_igdb_said_nothing():
    # The fallback makes a tagless game LOOK like a real {freelancer} detection; has_signal is
    # what keeps those out of freelancer_regression.
    assert job_drift.classify({'champion'}, {'freelancer'}, has_signal=False)[0] == job_drift.NO_SIGNAL
    assert job_drift.classify({'champion'}, set())[0] == job_drift.NO_SIGNAL


def test_freelancer_repair_and_its_mirror():
    assert job_drift.classify({'freelancer'}, {'champion', 'mage'})[0] == job_drift.FREELANCER_REPAIR
    assert job_drift.classify({'champion'}, {'freelancer'})[0] == job_drift.FREELANCER_REGRESSION


def test_freelancer_on_both_sides_is_aligned_not_repair():
    assert job_drift.classify({'freelancer'}, {'freelancer'})[0] == job_drift.ALIGNED


def test_combo_shift_is_named_in_both_directions():
    assert job_drift.classify({'champion'}, {'mage'})[0] == job_drift.COMBO_UPGRADE
    assert job_drift.classify({'mage'}, {'champion'})[0] == job_drift.COMBO_DOWNGRADE
    assert job_drift.classify({'gunslinger'}, {'vanguard'})[0] == job_drift.COMBO_UPGRADE


def test_combo_shift_requires_an_exact_correspondence():
    # An unrelated job riding along with the flip must NOT be hidden inside a bucket whose
    # label claims the whole change is explained by the override.
    bucket, added, removed = job_drift.classify({'champion'}, {'mage', 'infiltrator'})
    assert bucket == job_drift.MIXED
    assert added == {'mage', 'infiltrator'} and removed == {'champion'}


def test_narrower_wider_and_mixed():
    assert job_drift.classify({'champion'}, {'champion', 'infiltrator'})[0] == job_drift.NARROWER
    assert job_drift.classify({'champion', 'infiltrator'}, {'champion'})[0] == job_drift.WIDER
    assert job_drift.classify({'champion'}, {'driver'})[0] == job_drift.MIXED


def test_classify_always_returns_the_diff():
    _bucket, added, removed = job_drift.classify({'champion', 'driver'}, {'champion', 'athlete'})
    assert added == {'athlete'} and removed == {'driver'}


# --- the equivalence (the load-bearing one) ---------------------------------------------

def test_batched_scanner_matches_the_per_contract_helper():
    """Every contract, every shape: the scanner and `suggest_jobs_for_contract` must agree.

    Covers the cases where a batched implementation could plausibly diverge -- multiple member
    concepts pooling their tags, bundle-only episodic contracts, an untrusted match that must
    NOT contribute, a tagless concept (the {freelancer} fallback), and a contract with no
    concepts at all.
    """
    solo = _contract('eq-solo', ['champion'])
    _tagged_member(solo, genres=['Role-playing (RPG)'], themes=['Fantasy'])

    pooled = _contract('eq-pooled', ['gunslinger'])
    _tagged_member(pooled, genres=['Shooter'])
    _tagged_member(pooled, themes=['Stealth'])          # sibling concept, same igdb id

    tagless = _contract('eq-tagless', ['champion'])
    _tagged_member(tagless)                              # anchored + trusted, no genres/themes

    empty = _contract('eq-empty', ['champion'])          # no member concepts at all

    untrusted = _contract('eq-untrusted', ['champion'])
    stranger = ConceptFactory(anchor_migration_completed_at=timezone.now())
    IGDBMatchFactory(concept=stranger, igdb_id=untrusted.igdb_id, status='pending_review')
    _tag(stranger, genres=['Racing'])                    # must not reach the suggestion

    episodic = _contract('eq-episodic', ['librarian'], igdb_id=None)
    ep_concept = ConceptFactory()
    _tag(ep_concept, genres=['Point-and-click'], themes=['Comedy'])
    ContractBundle.objects.create(contract=episodic, label='ep').concepts.set([ep_concept])

    capped = _contract('eq-capped', ['champion'])
    _tagged_member(
        capped,
        genres=['Shooter', 'Platform', 'Puzzle', 'Racing', 'Fighting', 'Sport', 'Music'],
        themes=['Stealth', 'Horror'],
    )   # more than MAX_CONTRACT_JOBS detections -> top_jobs must trim identically

    # The fixture that kept lying: assert the episodic contract really has NO igdb_id, so the
    # scanner's `igdb_id is None` branches are genuinely exercised below.
    episodic.refresh_from_db()
    assert episodic.igdb_id is None

    scanner = job_drift.JobDriftScanner()
    assert {c.slug for c in scanner.contracts} >= {
        'eq-solo', 'eq-pooled', 'eq-tagless', 'eq-empty', 'eq-untrusted', 'eq-episodic',
        'eq-capped',
    }
    for contract in scanner.contracts:
        batched, _has_signal = scanner.suggested_for(contract)
        # LIST equality, not set: `top_jobs` orders strongest-first and the report shows that
        # order, so the ordering is part of what must not diverge.
        assert batched == suggest_jobs_for_contract(contract), (
            f'{contract.slug}: batched scanner disagrees with suggest_jobs_for_contract'
        )


def test_untrusted_and_unanchored_members_do_not_contribute():
    contract = _contract('drift-untrusted', ['champion'])
    stranger = ConceptFactory(anchor_migration_completed_at=timezone.now())
    IGDBMatchFactory(concept=stranger, igdb_id=contract.igdb_id, status='pending_review')
    _tag(stranger, genres=['Racing'])
    unanchored = ConceptFactory(anchor_migration_completed_at=None)
    IGDBMatchFactory(concept=unanchored, igdb_id=contract.igdb_id)
    _tag(unanchored, genres=['Racing'])

    # Neither concept qualifies, so there is no signal -- NOT a 'wider' finding against driver.
    assert _bucket_of(contract) == job_drift.NO_SIGNAL


# --- end to end over real rows ----------------------------------------------------------

def test_scan_buckets_a_freelancer_repair():
    contract = _contract('drift-repair', ['freelancer'])
    _tagged_member(contract, genres=['Role-playing (RPG)'], themes=['Fantasy'])

    row = next(r for r in job_drift.scan() if r['contract_id'] == contract.pk)
    assert row['bucket'] == job_drift.FREELANCER_REPAIR
    assert row['current'] == ['freelancer'] and row['suggested'] == ['mage']
    assert row['added'] == ['mage'] and row['removed'] == ['freelancer']


def test_scan_buckets_an_aligned_contract():
    contract = _contract('drift-aligned', ['gunslinger'])
    _tagged_member(contract, genres=['Shooter'])
    assert _bucket_of(contract) == job_drift.ALIGNED


def test_episodic_bundle_contract_is_judged_on_its_bundle_concepts():
    contract = _contract('drift-episodic', ['freelancer'], igdb_id=None)
    concept = ConceptFactory()
    _tag(concept, genres=['Point-and-click'])
    ContractBundle.objects.create(contract=contract, label='season').concepts.set([concept])

    row = next(r for r in job_drift.scan() if r['contract_id'] == contract.pk)
    assert row['bucket'] == job_drift.FREELANCER_REPAIR
    assert row['suggested'] == ['librarian']


def test_live_only_excludes_staged_contracts():
    staged = _contract('drift-staged', ['freelancer'], is_live=False)
    _tagged_member(staged, genres=['Racing'])

    assert staged.pk in {r['contract_id'] for r in job_drift.scan()}
    assert staged.pk not in {r['contract_id'] for r in job_drift.scan(live_only=True)}


# --- XP exposure ------------------------------------------------------------------------

def _earner_who_banked(contract):
    """A profile who has platinum'd + 100%'d the contract's game AND claimed the XP."""
    profile = ProfileFactory()
    concept = Concept.objects.filter(igdb_match__igdb_id=contract.igdb_id).first()
    game = GameFactory(concept=concept)
    plat = TrophyFactory(game=game, trophy_type='platinum')
    EarnedTrophyFactory(profile=profile, trophy=plat, earned=True)
    ProfileGameFactory(profile=profile, game=game, progress=100, has_plat=True)
    contract_service.mark_contract_reached(profile, contract)
    return profile, game, plat


def test_stakes_split_banked_from_reached_but_unclaimed():
    contract = _contract('drift-stakes', ['freelancer'])
    _tagged_member(contract, genres=['Racing'])

    banker, game, plat = _earner_who_banked(contract)
    contract_service.accept_contract(banker, contract)

    waiter = ProfileFactory()          # reached, never claimed -> re-splits for free
    EarnedTrophyFactory(profile=waiter, trophy=plat, earned=True)
    ProfileGameFactory(profile=waiter, game=game, progress=100, has_plat=True)
    contract_service.mark_contract_reached(waiter, contract)

    row = next(r for r in job_drift.scan() if r['contract_id'] == contract.pk)
    assert row['bucket'] == job_drift.FREELANCER_REPAIR
    assert row['banked_hunters'] == 1
    assert row['pending_hunters'] == 1
    from trophies.util_modules.constants import CONTRACT_XP_TOTAL
    assert row['banked_xp'] == CONTRACT_XP_TOTAL     # full T, one job, both tiers claimed


def test_stakes_are_not_inflated_by_the_grant_join():
    """ONE hunter, four grant rows (2 jobs x 2 tiers). The hunter count must still read 1.

    The trap this guards: counting earners and summing grants in a single annotated query
    joins ContractXPGrant onto EarnedContract and multiplies the earner rows by the grant
    count, so a hunter with a 2-job contract would be counted twice (or four times) and the
    report's headline "hunters affected" would silently scale with job count. The assertion on
    the grant row count below is what keeps this test honest -- without it a future change
    that collapses grants to one row per contract would make it pass for the wrong reason.
    """
    from trophies.models import ContractXPGrant
    from trophies.util_modules.constants import CONTRACT_XP_TOTAL

    contract = _contract('drift-join', ['gunslinger', 'infiltrator'])
    _tagged_member(contract, genres=['Shooter'], themes=['Stealth'])

    banker, _game, _plat = _earner_who_banked(contract)
    contract_service.accept_contract(banker, contract)
    assert ContractXPGrant.objects.filter(profile=banker).count() == 4   # 2 jobs x 2 tiers

    # Re-point the jobs so the row is DRIFTED and therefore costed at all.
    contract.jobs.set(Job.objects.filter(slug__in=['driver']))
    row = next(r for r in job_drift.scan() if r['contract_id'] == contract.pk)

    assert row['bucket'] == job_drift.MIXED
    assert row['banked_hunters'] == 1                 # not 2, and not 4
    assert row['pending_hunters'] == 0                # the one earner banked; none left over
    assert row['banked_xp'] == CONTRACT_XP_TOTAL      # summed once across all four rows


def test_aligned_rows_are_never_costed():
    """The one bucket left unmeasured: nothing to do about it, and it is most of the catalogue."""
    contract = _contract('drift-settled', ['driver'])
    _tagged_member(contract, genres=['Racing'])
    banker, _game, _plat = _earner_who_banked(contract)
    contract_service.accept_contract(banker, contract)

    row = next(r for r in job_drift.scan() if r['contract_id'] == contract.pk)
    assert row['bucket'] == job_drift.ALIGNED
    assert row['banked_xp'] == 0 and row['banked_hunters'] == 0   # not measured, by design


def test_no_signal_rows_ARE_costed_despite_being_settled():
    """A contract hunters are actively claiming whose IGDB data says nothing is worth costing.

    `no_signal` is settled (it is not drift and gets no section by default) but it is reachable
    via `--bucket no_signal`, and a drill-down reporting "no earners" for rows nobody measured
    would be stating a fact that was never checked.
    """
    from trophies.util_modules.constants import CONTRACT_XP_TOTAL

    contract = _contract('drift-nosignal-costed', ['driver'])
    _tagged_member(contract)                     # anchored + trusted, but no genres or themes
    banker, _game, _plat = _earner_who_banked(contract)
    contract_service.accept_contract(banker, contract)

    row = next(r for r in job_drift.scan() if r['contract_id'] == contract.pk)
    assert row['bucket'] == job_drift.NO_SIGNAL
    assert row['banked_hunters'] == 1
    assert row['banked_xp'] == CONTRACT_XP_TOTAL


# --- the command ------------------------------------------------------------------------

def test_command_runs_and_reports_the_buckets():
    repair = _contract('cmd-repair', ['freelancer'])
    _tagged_member(repair, genres=['Racing'])
    unjobbed = _contract('cmd-unjobbed', [])
    _tagged_member(unjobbed, genres=['Puzzle'])

    out = StringIO()
    call_command('report_job_drift', stdout=out)
    text = out.getvalue()

    # The SECTION heading, not the bare slug: `_verdict` prints 'freelancer_repair' on every run
    # regardless of whether anything is in the bucket, so the bare slug passes vacuously.
    assert f'freelancer_repair -- {job_drift.BUCKET_LABELS[job_drift.FREELANCER_REPAIR]}' in text
    assert 'cmd-repair' in text
    assert 'NO jobs set' in text        # the unjobbed warning
    assert 'cmd-unjobbed' in text


def test_command_bucket_filter_lists_only_that_bucket():
    repair = _contract('cmd-only-repair', ['freelancer'])
    _tagged_member(repair, genres=['Racing'])
    wider = _contract('cmd-only-wider', ['champion', 'driver'])
    _tagged_member(wider, genres=['Role-playing (RPG)'])

    out = StringIO()
    call_command('report_job_drift', bucket=job_drift.FREELANCER_REPAIR, stdout=out)
    text = out.getvalue()

    assert 'cmd-only-repair' in text
    assert 'cmd-only-wider' not in text


def test_bucket_filter_on_an_empty_bucket_says_so():
    """A drill-down that printed nothing at all would read as a broken command.

    Seeds a contract in a DIFFERENT bucket first: with no contracts at all the command
    short-circuits on "No contracts found." and never reaches the per-bucket branch, which is
    a different message for a different situation.
    """
    other = _contract('empty-bucket-neighbour', ['driver'])
    _tagged_member(other, genres=['Racing'])

    out = StringIO()
    call_command('report_job_drift', bucket=job_drift.COMBO_DOWNGRADE, stdout=out)
    assert 'No contracts in this bucket.' in out.getvalue()


def test_bucket_aligned_does_not_claim_rows_have_no_earners():
    """`aligned` is never costed, so its zeros mean "never asked", not "asked and found none".

    Reachable only through `--bucket aligned`, which is exactly the drill-down where a wrong
    "no earners" would be most misleading: on prod that bucket is most of the catalogue.
    """
    from trophies.models import ContractXPGrant

    contract = _contract('cmd-aligned-earners', ['driver'])
    _tagged_member(contract, genres=['Racing'])
    banker, _game, _plat = _earner_who_banked(contract)
    contract_service.accept_contract(banker, contract)
    # Without this the test passes identically with no earner at all, since aligned rows are
    # never costed either way -- so the claim "a contract that HAS earners" would be intent,
    # not verification.
    assert ContractXPGrant.objects.filter(earned_contract__contract=contract).exists()

    out = StringIO()
    call_command('report_job_drift', bucket=job_drift.ALIGNED, stdout=out)
    text = out.getvalue()

    assert 'cmd-aligned-earners' in text
    assert 'stakes not measured' in text
    assert 'no earners' not in text


def test_suggested_keeps_strength_order_in_the_row():
    """`top_jobs` ranks combo > genre > theme; the report shows that order, not the alphabet."""
    contract = _contract('order-check', ['freelancer'])
    _tagged_member(contract, genres=['Role-playing (RPG)'], themes=['Fantasy', 'Stealth'])

    row = next(r for r in job_drift.scan() if r['contract_id'] == contract.pk)
    # mage is the combo (strongest); infiltrator is a theme job. Alphabetically it is the reverse.
    assert row['suggested'] == ['mage', 'infiltrator']


def test_sample_truncates_and_reports_the_remainder():
    """The one piece of output arithmetic in the command: `[:sample_n]` plus the "N more" line."""
    for i in range(4):
        c = _contract(f'sample-repair-{i}', ['freelancer'])
        _tagged_member(c, genres=['Racing'])

    out = StringIO()
    call_command('report_job_drift', sample=1, stdout=out)
    text = out.getvalue()

    listed = sum(1 for i in range(4) if f'sample-repair-{i}' in text)
    assert listed == 1
    assert '... and 3 more (--bucket freelancer_repair to list them all).' in text


def test_bucket_filter_overrides_the_sample_cap():
    """A drill-down lists the whole bucket -- that is what it is for."""
    for i in range(4):
        c = _contract(f'full-repair-{i}', ['freelancer'])
        _tagged_member(c, genres=['Racing'])

    out = StringIO()
    call_command('report_job_drift', bucket=job_drift.FREELANCER_REPAIR, sample=1, stdout=out)
    text = out.getvalue()

    assert all(f'full-repair-{i}' in text for i in range(4))
    assert 'more (--bucket' not in text
