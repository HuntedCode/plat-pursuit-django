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
