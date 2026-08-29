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
