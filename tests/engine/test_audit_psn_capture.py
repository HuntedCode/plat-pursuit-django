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


def test_both_gap_buckets_are_broken_down_by_platform():
    """The blind spot on the first prod run: only the REACHABLE bucket was classified, so 3147 of a
    3813 gap went unclassified while the platform table showed a modern-looking remainder. Reading
    that table alone pointed at the opposite conclusion to the one the data supported."""
    from tests.factories import GameFactory

    no_id = ConceptFactory()
    GameFactory(concept=no_id, title_ids=[], title_platform=['PS3'])
    # A MIXED concept: a titleless PS3 entry beside a sweepable PS5 one. Under a multi-valued
    # exclude() the titleless game suppresses the whole concept and PS5 silently reads 0, which is
    # how a reachable platform comes to look unreachable.
    mixed = ConceptFactory()
    GameFactory(concept=mixed, title_ids=[], title_platform=['PS3'])
    GameFactory(concept=mixed, title_ids=['PPSA00001_00'], title_platform=['PS5'])

    printed = _gap()
    rows = {
        l.split()[0]: l.split()[1:]
        for l in printed.split('counts under each):')[1].splitlines()
        if l.strip().startswith(('PS5', 'PS4', 'PS3', 'PSVITA', 'PSPC'))
    }

    assert rows['PS3'][0] == '1', 'the no-title_id bucket must be classified, not just reachable'
    assert rows['PS5'][1] == '1', 'a mixed concept must still count as reachable on PS5'


def test_the_conclusion_follows_the_data_rather_than_a_canned_narrative():
    """The first version asserted a PS3/Vita story unconditionally, and on the first real prod run
    that text contradicted the numbers printed directly above it."""
    from tests.factories import GameFactory

    for _ in range(3):
        c = ConceptFactory()
        GameFactory(concept=c, title_ids=[], title_platform=['PS3'])

    assert "Largest bucket is 'no title_id'" in _gap()


def test_the_conclusion_flips_when_the_reachable_bucket_dominates():
    from tests.factories import GameFactory

    for i in range(3):
        c = ConceptFactory()
        GameFactory(concept=c, title_ids=[f'CUSA{i:05}_00'], title_platform=['PS4'])

    assert "Largest bucket is 'reachable'" in _gap()


def test_stub_concepts_are_surfaced_as_evidence_of_a_sparse_answer():
    """A PP_* concept exists because PSN returned nothing usable, so it is the closest thing we have
    to a recorded failed attempt -- which we otherwise do not store at all."""
    ConceptFactory(concept_id='PP_something')

    printed = _gap()

    assert 'PP_* stubs: 1' in printed


def test_the_gap_report_costs_no_psn_calls_and_never_walks_the_concepts():
    """Runs against 18k concepts on prod. Every number must be a DB-side aggregate.

    TWO checks, because each misses what the other catches:

      * SHAPE alone is fooled by an annotated queryset -- `annotate(Count(...))` puts COUNT in the
        SQL of a query that still fetches every row, so a walk over it reads as an aggregate.
      * SCALING alone is fooled by a walk over a plain `.all()` -- that is ONE query no matter how
        many rows come back, so the count never moves.

    Together they cover both: an unbounded fetch trips the shape check, and an N+1 trips scaling.
    """
    from django.test.utils import CaptureQueriesContext
    from django.db import connection
    from tests.factories import GameFactory

    def sql_for_run():
        with CaptureQueriesContext(connection) as ctx:
            call_command('audit_psn_capture', '--gap', stdout=io.StringIO())
        return [q['sql'] for q in ctx.captured_queries]

    def add(n, start):
        for i in range(n):
            c = ConceptFactory()
            GameFactory(concept=c, title_ids=[f'CUSA{start + i:05}_00'], title_platform=['PS4'])
            d = ConceptFactory()
            GameFactory(concept=d, title_ids=[], title_platform=['PS3'])

    add(4, 0)
    small = sql_for_run()
    add(30, 100)
    big = sql_for_run()

    unbounded = [
        q for q in big
        if 'count(' not in q.lower() and 'limit' not in q.lower()
    ]
    assert not unbounded, 'these queries neither aggregate nor bound their rows: ' + ' | '.join(unbounded)

    assert len(small) == len(big), (
        f'query count grew from {len(small)} to {len(big)} as the table went 8 -> 68 concepts; '
        f'something is issuing one query per row'
    )


# --- title observations in the report: the block that was unreachable when it mattered ----------

def test_observations_are_reported_even_with_zero_concept_rows():
    """CRITICAL from the audit round: the observation block originally sat BELOW the
    `if total == 0: return` early-out -- unreachable in precisely the default post-deploy state
    (observations fill from every sync; concept rows only from unresolved titles), which is
    exactly when an operator runs this to check the backfill."""
    from tests.factories import GameFactory
    from trophies.services.psn_metadata_service import capture_title_stats_observation
    from types import SimpleNamespace

    game = GameFactory(np_communication_id='NPWR55555_00')
    capture_title_stats_observation(game, SimpleNamespace(
        title_id='CUSA05555_00', name='Stray', image_url='', category=None,
        play_count=0, first_played_date_time=None, last_played_date_time=None, play_duration=None,
    ))

    printed = _run()

    assert 'Title observations' in printed
    assert 'rows:              1' in printed


def test_cross_source_disagreement_is_not_counted_as_a_rename():
    """title_stats' name and trophy_titles' name disagree SYSTEMATICALLY ((TM), suffixes), so an
    unfiltered distinct-count reads as 'every dual-source game was renamed' -- noise, in a report
    that exists to be believed."""
    from psnawp_api.models.trophies import PlatformType
    from tests.factories import GameFactory
    from trophies.services.psn_metadata_service import (
        capture_title_page_bulk, capture_title_stats_observation,
    )
    from types import SimpleNamespace

    game = GameFactory(np_communication_id='NPWR66666_00')
    capture_title_page_bulk([SimpleNamespace(
        np_communication_id='NPWR66666_00', np_title_id=None, np_service_name='trophy',
        trophy_set_version='01.00', title_name='Stray™', title_detail='',
        title_icon_url='', title_platform=frozenset({PlatformType.PS5}),
        has_trophy_groups=False,
        defined_trophies=SimpleNamespace(bronze=1, silver=0, gold=0, platinum=0),
        progress=0, hidden_flag=False,
        earned_trophies=SimpleNamespace(bronze=0, silver=0, gold=0, platinum=0),
        last_updated_datetime=None,
    )])
    capture_title_stats_observation(game, SimpleNamespace(
        title_id='CUSA06666_00', name='Stray', image_url='', category=None,
        play_count=0, first_played_date_time=None, last_played_date_time=None, play_duration=None,
    ))

    printed = _run()

    assert 'renamed (>1 name): 0' in printed, 'never renamed; two sources disagreeing is not a rename'


# --- --names: sizing the list-switcher work with data instead of a guess ------------------------

def _names(*args):
    out = io.StringIO()
    call_command('audit_psn_capture', '--names', *args, stdout=out)
    return out.getvalue()


def _list(concept, title):
    from tests.factories import GameFactory
    return GameFactory(concept=concept, title_name=title)


def test_names_classifies_each_divergence_kind_once():
    """The whole report: identical / case-only / suffix / substantive must partition the total.
    One list of each kind, and each bucket must read exactly 1."""
    from tests.factories import ConceptFactory

    c = ConceptFactory(unified_title='Vampire Survivors')
    _list(c, 'Vampire Survivors')                        # identical
    _list(c, 'VAMPIRE SURVIVORS')                        # case-only
    _list(c, 'Vampire Survivors Additional Trophies')    # suffix -- the switcher's reason to exist
    _list(c, 'Totally Different Name')                   # substantive

    printed = _names('--sample', '0')

    assert 'Trophy lists with a non-stub concept title: 4' in printed
    for label in ['identical', 'case-only difference', 'concept title + suffix',
                  'substantively different']:
        row = [l for l in printed.splitlines() if l.strip().startswith(label)][0]
        assert '1 (25%)' in row, f'{label}: {row}'


def test_names_excludes_stub_concepts():
    """A PP_* stub's unified_title IS the list name by construction, so including stubs inflates
    'identical' and understates every divergence rate."""
    from tests.factories import ConceptFactory

    stub = ConceptFactory(concept_id='PP_123', unified_title='Some Game')
    _list(stub, 'Some Game')

    printed = _names('--sample', '0')

    assert 'Trophy lists with a non-stub concept title: 0' in printed


def test_names_reports_observed_divergence_through_approx_cleaning():
    """The helper-chain question: stored title_name vs what PSN currently says. The trademark mark
    must not count as divergence (cleaning strips it); a genuinely different observed name must."""
    from tests.factories import ConceptFactory, GameFactory
    from trophies.services.psn_metadata_service import capture_title_page_bulk
    from psnawp_api.models.trophies import PlatformType
    from types import SimpleNamespace

    def tt(np_id, name):
        return SimpleNamespace(
            np_communication_id=np_id, np_title_id=None, np_service_name='trophy',
            trophy_set_version='01.00', title_name=name, title_detail='',
            title_icon_url='', title_platform=frozenset({PlatformType.PS4}),
            has_trophy_groups=False,
            defined_trophies=SimpleNamespace(bronze=1, silver=0, gold=0, platinum=0),
            progress=0, hidden_flag=False,
            earned_trophies=SimpleNamespace(bronze=0, silver=0, gold=0, platinum=0),
            last_updated_datetime=None,
        )

    c = ConceptFactory(unified_title='Stray')
    GameFactory(concept=c, np_communication_id='NPWR77777_00', title_name='Stray' + chr(0x2122))
    GameFactory(concept=c, np_communication_id='NPWR88888_00', title_name='Old Stored Name')
    capture_title_page_bulk([tt('NPWR77777_00', 'Stray' + chr(0x2122)),
                             tt('NPWR88888_00', 'What PSN Says Now' + chr(0x2122))])

    printed = _names('--sample', '0')

    assert "!= PSN's current name (approx-cleaned): 1/2" in printed


def test_names_sample_shows_only_substantive_rows():
    from tests.factories import ConceptFactory

    c = ConceptFactory(unified_title='Vampire Survivors')
    _list(c, 'Vampire Survivors Additional Trophies')
    _list(c, 'Regional JP Name')

    printed = _names('--sample', '15')

    assert "'Regional JP Name'" in printed
    # Game.save() strips the trailing " Trophies" (clean_game_title), so the STORED suffix-class
    # name is 'Vampire Survivors Additional' -- asserting the unstripped string was vacuously true
    # (found by mutation: removing the sample's istartswith exclude still passed).
    assert "'Vampire Survivors Additional'" not in printed.split('Sample of')[1]


def test_names_never_walks_the_catalogue():
    """Runs on prod against the whole Game table; every query must be a COUNT or LIMIT-bounded."""
    from django.db import connection
    from django.test.utils import CaptureQueriesContext
    from tests.factories import ConceptFactory, GameFactory

    c = ConceptFactory(unified_title='Stray')
    for i in range(10):
        GameFactory(concept=c, title_name=f'Stray Variant {i}')

    with CaptureQueriesContext(connection) as ctx:
        call_command('audit_psn_capture', '--names', stdout=io.StringIO())

    unbounded = [q['sql'] for q in ctx.captured_queries
                 if 'count(' not in q['sql'].lower() and 'limit' not in q['sql'].lower()]
    assert not unbounded, 'these queries walk the catalogue: ' + ' | '.join(unbounded)
