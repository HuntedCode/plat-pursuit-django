"""The diagnostic that answers "is capture actually storing what we think it is?".

It exists because the PSN response shapes were inferred from how the sync code reads them, never
from a recorded fixture -- there are no cassettes in this repo. That is how `media` shipped reading
a list when PSN sends a dict: every test agreed with the fixture, and the fixture agreed with the
bug. The signature of that class of defect is a field empty on 100% of rows, so these tests pin that
the report actually flags it, and that the report survives the broken states it exists to describe.
"""
import io

import pytest
from django.core.management import call_command

from tests.factories import ConceptFactory
from trophies.models import PSNConceptData, PSNRawPayload
from trophies.services.psn_metadata_service import capture_psn_concept_data

pytestmark = pytest.mark.django_db


def _details(psn_id='12345', **over):
    payload = {
        'id': psn_id,
        'name': 'ゴースト・オブ・ツシマ',
        'nameEn': 'Ghost of Tsushima',
        'publisherName': 'Sony Interactive Entertainment',
        'genres': ['Action'],
        'subGenres': ['Open World'],
        'descriptions': [{'type': 'SHORT', 'desc': 'A short one.'}],
        'contentRating': {'rating': 'M'},
        'media': {'images': [{'type': 'MASTER', 'url': 'https://psn/m.png'}]},
        'defaultProduct': {'media': {'images': []}},
    }
    payload.update(over)
    return payload


def _run(*args):
    out = io.StringIO()
    call_command('audit_psn_capture', *args, stdout=out)
    return out.getvalue()


def test_a_field_empty_on_every_row_is_flagged():
    """THE point of the command. A key we read wrong yields an always-empty column, which is
    indistinguishable from a healthy one unless something counts it."""
    capture_psn_concept_data(ConceptFactory(), _details(publisherName=''), country='US')

    printed = _run()

    assert 'publisher_name' in printed
    assert 'empty on every row' in printed
    assert 'publisher_name' in printed.split('empty on every row')[1]


def test_a_healthy_capture_reports_no_suspect_field():
    capture_psn_concept_data(ConceptFactory(), _details(), country='US')

    printed = _run()

    assert 'No field is empty across the board' in printed
    assert 'empty on every row' not in printed


def test_a_blank_country_is_called_out():
    """Blank country means the answering region never reached capture, so rows cannot be
    interpreted and two regions of one concept collide on the unique key."""
    capture_psn_concept_data(ConceptFactory(), _details())

    printed = _run()

    assert 'Blank country' in printed


def test_regions_are_reported_per_storefront():
    concept = ConceptFactory()
    capture_psn_concept_data(concept, _details(psn_id='1'), country='US', language='en-US')
    capture_psn_concept_data(concept, _details(psn_id='2'), country='JP', language='ja')

    printed = _run()

    assert 'US/en-US' in printed and 'JP/ja' in printed


def test_an_empty_table_says_so_and_names_the_worker_env():
    """The likeliest cause of zero rows is the kill switch being off in the WORKER's environment,
    which is read per-service -- the web service having it on proves nothing."""
    printed = _run()

    assert 'Nothing captured yet' in printed
    assert 'WORKER' in printed


def test_a_row_with_no_raw_payload_is_reported_rather_than_crashing():
    """`row.raw` raises RelatedObjectDoesNotExist rather than returning None. A diagnostic that
    crashes on the broken state it exists to describe is worse than none."""
    capture_psn_concept_data(ConceptFactory(), _details(), country='US')
    PSNRawPayload.objects.all().delete()

    printed = _run()

    assert 'MISSING' in printed
    assert 'have no raw payload' in printed


def test_the_sample_prints_the_real_response_keys():
    """The raw key list is the thing we could never answer before: what does PSN actually send?"""
    capture_psn_concept_data(ConceptFactory(), _details(), country='US')

    printed = _run()

    assert 'defaultProduct' in printed and 'contentRating' in printed


def test_sample_zero_skips_the_detail_block():
    capture_psn_concept_data(ConceptFactory(), _details(), country='US')

    printed = _run('--sample', '0')

    assert 'raw keys' not in printed
    assert 'PSNConceptData rows: 1' in printed


def test_the_report_never_walks_the_table():
    """Runs against prod, where this table reaches catalogue size.

    Asserting the SHAPE of every query, not the count of them. The failure that matters is someone
    replacing a `.count()` aggregate with a Python walk over `.all()` -- and that costs the SAME
    number of queries while pulling every row and its multi-KB JSON into memory, so a query-count
    assertion sails straight past it. What separates the two is that an aggregate says COUNT and a
    walk is an unbounded SELECT, so every query here must be one or the other: a COUNT, or bounded
    by a LIMIT.
    """
    from django.test.utils import CaptureQueriesContext
    from django.db import connection

    concept = ConceptFactory()
    for i in range(12):
        capture_psn_concept_data(concept, _details(psn_id=str(i)), country='US')

    with CaptureQueriesContext(connection) as ctx:
        call_command('audit_psn_capture', stdout=io.StringIO())

    unbounded = [
        q['sql'] for q in ctx.captured_queries
        if 'count(' not in q['sql'].lower() and 'limit' not in q['sql'].lower()
    ]
    assert not unbounded, (
        'these queries neither aggregate nor bound their rows, so their cost grows with the '
        'table: ' + ' | '.join(unbounded)
    )


# --- --gap: classifying the concepts a sweep could not capture -----------------------------------

def _gap(*args):
    out = io.StringIO()
    call_command('audit_psn_capture', '--gap', *args, stdout=out)
    return out.getvalue()


def test_the_gap_buckets_account_for_every_uncaptured_concept():
    """The three buckets are computed two different ways -- no_games and reachable by query,
    no_title_ids by subtraction -- so a wrong filter shows up as a bucket that does not add up
    rather than as a plausible-looking number."""
    from tests.factories import GameFactory

    captured = ConceptFactory()
    capture_psn_concept_data(captured, _details(psn_id='c1'), country='US')
    GameFactory(concept=captured, title_ids=['CUSA00001_00'], title_platform=['PS4'])

    ConceptFactory()                                                   # no games
    orphan_titleless = ConceptFactory()
    GameFactory(concept=orphan_titleless, title_ids=[], title_platform=['PS3'])
    reachable = ConceptFactory()
    GameFactory(concept=reachable, title_ids=['CUSA00009_00'], title_platform=['PS3'])

    printed = _gap()

    assert 'with a PSN row:    1' in printed
    assert 'without:           3' in printed
    assert 'no games at all:                 1' in printed
    assert 'games, but no title_id:          1' in printed
    assert 'reachable, still uncaptured:     1' in printed


def test_a_concept_with_one_titleless_game_and_one_real_game_counts_as_reachable():
    """The subtlety the comment in the command names: `games__title_ids=[]` on its own also matches
    a concept whose OTHER game does have a title_id, which would double-count it into two buckets."""
    from tests.factories import GameFactory

    concept = ConceptFactory()
    GameFactory(concept=concept, title_ids=[], title_platform=['PS3'])
    GameFactory(concept=concept, title_ids=['CUSA00002_00'], title_platform=['PS4'])

    printed = _gap()

    assert 'reachable, still uncaptured:     1' in printed
    assert 'games, but no title_id:          0' in printed


def test_the_platform_breakdown_separates_ps3_from_modern():
    """The whole question behind --gap: is the shortfall concentrated in platforms whose storefront
    is retired, in which case re-running the sweep cannot win it back."""
    from tests.factories import GameFactory

    ps3 = ConceptFactory()
    GameFactory(concept=ps3, title_ids=['NPUB30001_00'], title_platform=['PS3'])
    # A MIXED concept: a titleless PS3 entry beside a sweepable PS5 one. This shape is what makes
    # the breakdown honest -- under a multi-valued exclude() the titleless game suppresses the whole
    # concept and PS5 silently reads 0, which is how a reachable platform looks unreachable.
    ps5 = ConceptFactory()
    GameFactory(concept=ps5, title_ids=[], title_platform=['PS3'])
    GameFactory(concept=ps5, title_ids=['PPSA00001_00'], title_platform=['PS5'])

    printed = _gap()
    tail = printed.split('by platform:')[1]

    assert 'PS3' in tail and 'PS5' in tail
    ps3_line = [l for l in tail.splitlines() if l.strip().startswith('PS3')][0]
    ps5_line = [l for l in tail.splitlines() if l.strip().startswith('PS5')][0]
    assert ps3_line.split()[-1] == '1' and ps5_line.split()[-1] == '1'


def test_stub_concepts_are_surfaced_as_evidence_of_a_sparse_answer():
    """A PP_* concept exists because PSN returned nothing usable, so it is the closest thing we have
    to a recorded failed attempt -- which we otherwise do not store at all."""
    ConceptFactory(concept_id='PP_something')

    printed = _gap()

    assert 'PP_* stubs: 1' in printed


def test_the_gap_report_costs_no_psn_calls_and_never_walks_the_concepts():
    """Runs against 18k concepts on prod. Every number must be a DB-side aggregate."""
    from django.test.utils import CaptureQueriesContext
    from django.db import connection
    from tests.factories import GameFactory

    for i in range(15):
        c = ConceptFactory()
        GameFactory(concept=c, title_ids=[f'CUSA{i:05}_00'], title_platform=['PS4'])

    with CaptureQueriesContext(connection) as ctx:
        call_command('audit_psn_capture', '--gap', stdout=io.StringIO())

    unbounded = [
        q['sql'] for q in ctx.captured_queries
        if 'count(' not in q['sql'].lower() and 'limit' not in q['sql'].lower()
    ]
    assert not unbounded, 'the gap report walks rows: ' + ' | '.join(unbounded)
