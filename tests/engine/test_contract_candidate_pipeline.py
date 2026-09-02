"""The nightly contract-candidate pipeline (evaluate_contract_candidates + ContractCandidate):
staged auto-creation under the demand-ordered cap, the queue statuses, sticky dismissals,
snooze promotions, and done-marking. The RULE itself is pinned in
test_report_contract_candidates; this suite pins what the pipeline DOES with its verdicts."""
from io import StringIO

import pytest
from django.core.management import call_command
from django.utils import timezone

from tests.factories import ConceptFactory, ConceptGenreFactory, GameFactory, GenreFactory, IGDBMatchFactory
from trophies.models import Contract, ContractCandidate

pytestmark = pytest.mark.django_db

_REAL = {'bronze': 30, 'silver': 12, 'gold': 5, 'platinum': 1}
_STACK = {'bronze': 1, 'silver': 0, 'gold': 11, 'platinum': 1}


def _game(igdb_id, name, *, videos=0, shots=0, defined=_REAL, shovelware='', played=0, genre=None):
    concept = ConceptFactory(unified_title=name)
    concept.anchor_migration_completed_at = timezone.now()
    concept.save(update_fields=['anchor_migration_completed_at'])
    match = IGDBMatchFactory(
        concept=concept, igdb_id=igdb_id, igdb_name=name,
        igdb_video_youtube_ids=[f'v{i}' for i in range(videos)],
        igdb_screenshot_image_ids=[f's{i}' for i in range(shots)],
    )
    GameFactory(concept=concept, title_name=name, defined_trophies=defined, played_count=played,
                **({'shovelware_status': shovelware} if shovelware else {}))
    if genre is not None:
        ConceptGenreFactory(concept=concept, genre=genre)
    return concept, match


def _run(**opts):
    out = StringIO()
    call_command('evaluate_contract_candidates', stdout=out, **opts)
    return out.getvalue()


def test_tier_a_stages_a_contract_with_jobs():
    """A Tier A game gets a STAGED contract: is_live=False, igdb-keyed, jobs auto-suggested
    from its genres, candidate linked."""
    racing = GenreFactory(name='Racing', slug='racing')
    _game(95001, 'Speed Kings', videos=1, played=500, genre=racing)

    _run()

    cand = ContractCandidate.objects.get(igdb_id=95001)
    assert cand.status == ContractCandidate.STATUS_STAGED
    assert cand.tier == 'A'
    contract = cand.contract
    assert contract is not None and contract.is_live is False
    assert contract.igdb_id == 95001
    assert 'Auto-staged' in contract.notes
    assert set(contract.jobs.values_list('slug', flat=True)) == {'driver'}


def test_cap_stages_by_demand_and_rest_waits_in_review():
    """--max-stage caps per run in players order; over-cap Tier A rows wait as review (tier A)
    and stage on the NEXT run. Created LOWEST-demand first so insertion order can't fake the
    demand sort."""
    _game(95013, 'Small Hit', videos=1, played=10)
    _game(95011, 'Huge Hit', videos=1, played=9000)
    _game(95012, 'Mid Hit', videos=1, played=500)

    _run(max_stage=2)

    assert ContractCandidate.objects.get(igdb_id=95011).status == ContractCandidate.STATUS_STAGED
    assert ContractCandidate.objects.get(igdb_id=95012).status == ContractCandidate.STATUS_STAGED
    small = ContractCandidate.objects.get(igdb_id=95013)
    assert small.status == ContractCandidate.STATUS_REVIEW and small.tier == 'A'

    _run(max_stage=2)
    small.refresh_from_db()
    assert small.status == ContractCandidate.STATUS_STAGED
    # THE pre-prod audit's F1: run 2 must NOT flip run 1's staged rows to done -- a staged
    # contract (is_live=False) is not a live one, and the staged queue must survive the night.
    assert ContractCandidate.objects.get(igdb_id=95011).status == ContractCandidate.STATUS_STAGED
    assert ContractCandidate.objects.get(igdb_id=95012).status == ContractCandidate.STATUS_STAGED


def test_b_and_c_land_in_their_queues():
    _game(95021, 'Flagged Trailer', videos=1, shovelware='auto_flagged', played=50)
    _game(95022, 'Bare Page', shots=1)

    _run()

    blocked = ContractCandidate.objects.get(igdb_id=95021)
    assert blocked.status == ContractCandidate.STATUS_REVIEW
    assert blocked.tier == 'B' and blocked.reason == 'blocked'
    assert blocked.contract is None   # never auto-staged

    snoozed = ContractCandidate.objects.get(igdb_id=95022)
    assert snoozed.status == ContractCandidate.STATUS_SNOOZED and snoozed.tier == 'C'


def test_snooze_promotes_when_media_lands():
    """The re-check paying off: a snoozed page that gains a trailer promotes to review, and
    (tier A + not dismissed) stages in the same run."""
    _, match = _game(95031, 'Late Bloomer', shots=1, played=80)
    _run()
    assert ContractCandidate.objects.get(igdb_id=95031).status == ContractCandidate.STATUS_SNOOZED

    match.igdb_video_youtube_ids = ['v1']
    match.save(update_fields=['igdb_video_youtube_ids'])
    _run()

    cand = ContractCandidate.objects.get(igdb_id=95031)
    assert cand.status == ContractCandidate.STATUS_STAGED
    assert cand.contract is not None


def test_dismissed_is_sticky():
    """A staff dismissal is never overridden -- even a Tier A game stays dismissed and
    consumes no cap."""
    _game(95041, 'Staff Said No', videos=1, played=700)
    _run()
    cand = ContractCandidate.objects.get(igdb_id=95041)
    cand.status = ContractCandidate.STATUS_DISMISSED
    cand.contract.delete()
    cand.contract = None
    cand.save()

    _run()

    cand.refresh_from_db()
    assert cand.status == ContractCandidate.STATUS_DISMISSED
    assert not Contract.objects.filter(igdb_id=95041).exists()


def test_contracted_ids_leave_the_queue_as_done():
    """An id that gained a real contract (staff-created or a published staging) is marked done
    and stops being evaluated."""
    _game(95051, 'Manual Contract', shots=5)
    _run()
    assert ContractCandidate.objects.get(igdb_id=95051).status == ContractCandidate.STATUS_REVIEW

    Contract.objects.create(name='Manual', slug='manual-95051', igdb_id=95051, is_live=True)
    _run()

    assert ContractCandidate.objects.get(igdb_id=95051).status == ContractCandidate.STATUS_DONE


def test_slug_collision_gets_igdb_suffix():
    Contract.objects.create(name='Speed Kings', slug='speed-kings', igdb_id=None)
    _game(95061, 'Speed Kings', videos=1)

    _run()

    staged = ContractCandidate.objects.get(igdb_id=95061).contract
    assert staged.slug == 'speed-kings-95061'


def test_dry_run_writes_nothing():
    _game(95071, 'Dry Game', videos=1, played=42)

    out = _run(dry_run=True)

    assert 'would stage 1' in out and 'Dry Game' in out
    assert not ContractCandidate.objects.exists()
    assert not Contract.objects.exists()


def test_siblings_collapse_to_one_candidate():
    """Sibling concepts sharing the IGDB page produce ONE candidate row (the best-signal one)."""
    _game(95081, 'Sibling A', videos=1, played=10)
    b = ConceptFactory(unified_title='Sibling B')
    b.anchor_migration_completed_at = timezone.now()
    b.save(update_fields=['anchor_migration_completed_at'])
    IGDBMatchFactory(concept=b, igdb_id=95081, igdb_name='Sibling B', igdb_video_youtube_ids=['v1'])
    GameFactory(concept=b, defined_trophies=_REAL, played_count=300)

    _run()

    cand = ContractCandidate.objects.get(igdb_id=95081)
    assert ContractCandidate.objects.count() == 1
    assert cand.players == 300   # the higher-demand sibling's signal wins


def test_staged_becomes_done_only_when_the_contract_goes_live():
    """Only a LIVE contract marks done: publish the staged contract -> the next run flips the
    candidate; until then it stays staged (the F1 fix's positive half)."""
    _game(96001, 'Awaiting Publish', videos=1, played=100)
    _run()
    cand = ContractCandidate.objects.get(igdb_id=96001)
    assert cand.status == ContractCandidate.STATUS_STAGED

    cand.contract.is_live = True
    cand.contract.save(update_fields=['is_live'])
    _run()

    cand.refresh_from_db()
    assert cand.status == ContractCandidate.STATUS_DONE


def test_flagged_sibling_blocks_the_whole_group():
    """The audit's F2: the shovelware override holds at GROUP level. A clean sibling with a
    trailer must not launder a flagged game's IGDB id into Tier A -- the group lands in review
    as blocked, and no contract is staged."""
    _game(96011, 'Clean Sibling', videos=1, played=50)
    flagged = ConceptFactory(unified_title='Flagged Sibling')
    flagged.anchor_migration_completed_at = timezone.now()
    flagged.save(update_fields=['anchor_migration_completed_at'])
    IGDBMatchFactory(concept=flagged, igdb_id=96011, igdb_name='Flagged Sibling',
                     igdb_video_youtube_ids=['v1'])
    GameFactory(concept=flagged, defined_trophies=_REAL, shovelware_status='auto_flagged')

    _run()

    cand = ContractCandidate.objects.get(igdb_id=96011)
    assert cand.status == ContractCandidate.STATUS_REVIEW
    assert cand.tier == 'B' and cand.reason == 'blocked'
    assert not Contract.objects.filter(igdb_id=96011).exists()


def test_deleted_staged_contract_recovers_to_review():
    """The audit's F3: staff deleting a staged Contract (SET_NULL) must not orphan the
    candidate as staged-forever -- it recovers to review and can stage again."""
    _game(96021, 'Second Chance', videos=1, played=60)
    _run()
    cand = ContractCandidate.objects.get(igdb_id=96021)
    cand.contract.delete()
    _run()

    cand.refresh_from_db()
    # Recovered to review, and (tier A + review) it staged again in the same run.
    assert cand.status == ContractCandidate.STATUS_STAGED
    assert cand.contract is not None


def test_racing_contract_never_aborts_the_run():
    """The audit's F4: a contract appearing between the scan and the staging (a staff action
    mid-run) must not raise IntegrityError and roll the whole batch back -- _stage_contract
    detects it and skips."""
    from trophies.management.commands.evaluate_contract_candidates import Command

    _game(96031, 'Raced Game', videos=1, played=70)
    _run()
    cand = ContractCandidate.objects.get(igdb_id=96031)
    # Simulate the race: a contract already exists for the id when staging is attempted.
    assert Command()._stage_contract(cand) is None


def test_double_slug_collision_gets_a_counter():
    """The audit's F7: base AND base-igdb both taken -> the uniquifier keeps counting instead
    of raising IntegrityError (which would abort every future nightly run)."""
    Contract.objects.create(name='Speed Kings', slug='speed-kings', igdb_id=None)
    Contract.objects.create(name='Speed Kings', slug='speed-kings-96041', igdb_id=None)
    _game(96041, 'Speed Kings', videos=1)

    _run()

    staged = ContractCandidate.objects.get(igdb_id=96041).contract
    assert staged.slug == 'speed-kings-96041-2'


def test_unchanged_rows_are_not_rewritten():
    """The audit's F11: a row whose verdict did not change keeps its evaluated_at -- the
    nightly run must not churn ~16k identical rows."""
    _game(96051, 'Steady Game', shots=5, played=20)
    _run()
    first = ContractCandidate.objects.get(igdb_id=96051).evaluated_at

    _run()

    assert ContractCandidate.objects.get(igdb_id=96051).evaluated_at == first


def test_admin_dismiss_skips_staged_rows():
    """The audit's F6: the dismiss action only touches review/snoozed -- a staged row (real
    contract behind it) is skipped, keeping the ledger and the Contract table consistent."""
    from django.contrib.admin.sites import AdminSite
    from django.test import RequestFactory
    from django.contrib.messages.storage.fallback import FallbackStorage

    from trophies.admin import ContractCandidateAdmin

    _game(96061, 'Staged One', videos=1, played=10)
    _game(96062, 'Review One', videos=1, shovelware='auto_flagged', played=5)
    _run()

    admin = ContractCandidateAdmin(ContractCandidate, AdminSite())
    request = RequestFactory().post('/')
    request.session = {}
    request._messages = FallbackStorage(request)
    admin.dismiss(request, ContractCandidate.objects.all())

    assert ContractCandidate.objects.get(igdb_id=96061).status == ContractCandidate.STATUS_STAGED
    assert ContractCandidate.objects.get(igdb_id=96062).status == ContractCandidate.STATUS_DISMISSED


def test_admin_stage_action_creates_contract():
    """The stage_contracts action (cap-free staff staging) creates the staged contract and
    links it, and marks already-contracted rows done instead of crashing."""
    from django.contrib.admin.sites import AdminSite
    from django.test import RequestFactory
    from django.contrib.messages.storage.fallback import FallbackStorage

    from trophies.admin import ContractCandidateAdmin

    _game(96071, 'Stage Me', videos=1, shovelware='auto_flagged', played=30)   # review (blocked)
    _run()

    admin = ContractCandidateAdmin(ContractCandidate, AdminSite())
    request = RequestFactory().post('/')
    request.session = {}
    request._messages = FallbackStorage(request)
    admin.stage_contracts(request, ContractCandidate.objects.filter(igdb_id=96071))

    cand = ContractCandidate.objects.get(igdb_id=96071)
    assert cand.status == ContractCandidate.STATUS_STAGED
    assert cand.contract is not None and cand.contract.is_live is False
