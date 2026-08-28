"""Keeping PSN's own metadata instead of discarding it.

`Concept` blends two sources into one row: PSN writes `unified_title`, `publisher_name`, `genres`,
`subgenres`, `descriptions` and `content_rating`, and IGDB writes over some of the same columns. The
guard rails around that collision -- `title_lock`, the CJK-regression guard, fill-when-empty -- were
each added after a real regression.

And on the anchoring path PSN's payload was discarded outright: `_do_sync_trophies` holds the full
details object when a Game lands on an IGDB-anchored Concept and rescued exactly one field from it.

THE CONSTRAINT THIS WAS BUILT UNDER: nothing on any page may change. That is why the capture is purely
additive and nothing reads these tables yet -- and why the most important test in this file is the one
asserting Concept's own columns are untouched.
"""
import copy

import pytest

from trophies.models import Concept, PSNConceptData, PSNRawPayload
from trophies.services.psn_metadata_service import capture_psn_concept_data
from tests.factories import ConceptFactory

pytestmark = pytest.mark.django_db


def _details(psn_id='12345', **over):
    """A PSN get_details response, shaped the way PSN actually sends one."""
    payload = {
        'id': psn_id,
        'name': 'ゴースト・オブ・ツシマ',
        'nameEn': 'Ghost of Tsushima',
        'publisherName': 'Sony Interactive Entertainment',
        'genres': ['Action', 'Adventure'],
        'subGenres': ['Open World'],
        'descriptions': [
            {'type': 'SHORT', 'desc': 'A short one.'},
            {'type': 'LONG', 'desc': 'A considerably longer one.'},
        ],
        'contentRating': {'rating': 'M', 'system': 'ESRB'},
        # A DICT of images/videos, in BOTH places PSN uses -- matching how `_extract_media` reads it
        # (defaultProduct first, then root). The first draft of this fixture declared a flat list
        # under a docstring claiming it was PSN's real shape, which is why the media handling shipped
        # wrong and no test noticed.
        'media': {'images': [{'type': 'MASTER', 'url': 'https://psn/master.png'}], 'videos': []},
        'defaultProduct': {
            'media': {'images': [{'type': 'GAMEHUB_COVER_ART', 'url': 'https://psn/hub.png'}]},
        },
        # Keys we do not parse. The whole point of the raw table is that these survive -- and these
        # are REAL unparsed keys the repo can prove arrive (util_modules/region.py reads
        # categorizedProducts; conceptIconUrl is documented in the PSN API returns notes), not
        # invented ones, so the claim below is about the actual response.
        'categorizedProducts': [{'id': 'UP9000-CUSA00000_00', 'name': 'Standard Edition'}],
        'conceptIconUrl': 'https://psn/icon.png',
    }
    payload.update(over)
    return payload


def test_the_psn_payload_is_kept_whole():
    concept = ConceptFactory()

    row = capture_psn_concept_data(concept, _details(), country='US', language='en')

    assert row.psn_concept_id == '12345'
    assert row.name_en == 'Ghost of Tsushima'
    assert row.name == 'ゴースト・オブ・ツシマ', 'the native name is what Concept collapses away'
    assert row.publisher_name == 'Sony Interactive Entertainment'
    assert row.genres == ['Action', 'Adventure']
    assert row.subgenres == ['Open World']
    assert row.descriptions == {'short': 'A short one.', 'long': 'A considerably longer one.'}
    assert row.content_rating == {'rating': 'M', 'system': 'ESRB'}
    assert row.country == 'US' and row.language == 'en'
    # Both media blocks, unflattened, under names that say which is which. `all_media` is NOT used:
    # it already means the flattened deduped list in `_extract_media` and `Concept.media`.
    assert row.media == {
        'root': {'images': [{'type': 'MASTER', 'url': 'https://psn/master.png'}], 'videos': []},
        'default_product': {'images': [{'type': 'GAMEHUB_COVER_ART', 'url': 'https://psn/hub.png'}]},
    }, 'defaultProduct media is the block _extract_media checks FIRST; dropping it loses the cover'


def test_the_raw_response_keeps_what_we_do_not_parse():
    """We consume about eight keys. Everything else was unrecoverable -- we could not even answer
    what PSN sends us, because no copy survived the parse."""
    concept = ConceptFactory()

    row = capture_psn_concept_data(concept, _details())

    assert row.raw.payload['categorizedProducts'] == [
        {'id': 'UP9000-CUSA00000_00', 'name': 'Standard Edition'}
    ]
    assert row.raw.payload['conceptIconUrl'] == 'https://psn/icon.png'


def test_capture_does_not_touch_a_single_concept_column():
    """THE constraint. Nothing on any page may move, and the guarantee is structural rather than
    hopeful: capture writes only its own tables, so there is nothing for a render to notice.

    Snapshots every field on Concept rather than a chosen few, so a future edit that starts writing
    one fails here instead of on a page.

    TWO THINGS THIS GETS RIGHT THAT THE OBVIOUS VERSION DOES NOT:

    `deepcopy`, because half of Concept's columns are JSONFields holding mutable lists and dicts. A
    plain `getattr` snapshot stores a live reference, so `concept.genres.extend(...)` followed by a
    real committed `save()` compares equal to itself and the test passes while a page changes. That
    is not hypothetical -- it was verified against this exact test.

    `f.attname` over `f.name`, because `is_relation` skipped the concrete FK columns: `family` is
    rendered, and the previous filter never snapshotted it. `attname` also reads `family_id` off the
    instance rather than fetching the related object.
    """
    def snapshot(obj):
        return {
            f.attname: copy.deepcopy(getattr(obj, f.attname))
            for f in Concept._meta.get_fields() if f.concrete
        }

    concept = ConceptFactory(unified_title='Curated Title', publisher_name='Curated Publisher')
    before = snapshot(concept)

    capture_psn_concept_data(concept, _details())

    concept.refresh_from_db()
    assert before == snapshot(concept), 'capture wrote a Concept column; a page can now change'


def test_a_refresh_updates_in_place_rather_than_accumulating():
    """Bounded at one row per PSN concept. The goal is to stop discarding what PSN sends, not to
    archive every sync."""
    concept = ConceptFactory()

    capture_psn_concept_data(concept, _details())
    capture_psn_concept_data(concept, _details(nameEn='Ghost of Tsushima Director’s Cut'))

    assert PSNConceptData.objects.count() == 1
    assert PSNRawPayload.objects.count() == 1
    assert PSNConceptData.objects.get().name_en.endswith('Director’s Cut')


def test_a_sparse_response_is_skipped_rather_than_keyed_on_nothing():
    """The region fallback can answer without an id. That is not an error and must not create a row
    that cannot be identified later."""
    concept = ConceptFactory()

    assert capture_psn_concept_data(concept, _details(id=None)) is None
    assert capture_psn_concept_data(concept, {}) is None
    assert capture_psn_concept_data(None, _details()) is None
    assert PSNConceptData.objects.count() == 0


def test_capture_never_costs_a_sync(monkeypatch):
    """It runs inside the sync job path and is purely additive. A failure here must not cost a hunter
    their sync -- but it logs loudly rather than vanishing, because this codebase has spent real time
    on failures that reported success."""
    from trophies.services import psn_metadata_service as svc

    def boom(*a, **k):
        raise RuntimeError('database went away')

    monkeypatch.setattr(svc.PSNConceptData.objects, 'update_or_create', boom)

    assert svc.capture_psn_concept_data(ConceptFactory(), _details()) is None


def test_malformed_descriptions_do_not_lose_the_rest_of_the_payload():
    concept = ConceptFactory()

    row = capture_psn_concept_data(concept, _details(descriptions=['not a dict', {'type': 'SHORT', 'desc': 'kept'}]))

    assert row.descriptions == {'short': 'kept'}
    assert row.publisher_name == 'Sony Interactive Entertainment'


# --- absorb: the branch CLAUDE.md says is non-negotiable ------------------------------------------

def test_absorb_repoints_every_psn_row_and_loses_none():
    """One Concept legitimately has several PSN entries -- regional variants, editions -- which is why
    `title_ids` merges rather than choosing. Picking a winner here would be the exact data loss these
    tables exist to stop."""
    survivor = ConceptFactory()
    doomed = ConceptFactory()

    capture_psn_concept_data(survivor, _details(psn_id='1000'))
    capture_psn_concept_data(doomed, _details(psn_id='2000'))
    capture_psn_concept_data(doomed, _details(psn_id='3000'))

    survivor.absorb(doomed)

    assert set(survivor.psn_data.values_list('psn_concept_id', flat=True)) == {'1000', '2000', '3000'}


def test_absorb_keeps_the_raw_payloads_with_their_rows():
    survivor = ConceptFactory()
    doomed = ConceptFactory()
    capture_psn_concept_data(doomed, _details(psn_id='4000'))

    survivor.absorb(doomed)
    # absorb() only MIGRATES; every caller deletes afterwards. Without this line the row sits
    # untouched on `doomed` and the assertion below is true whether or not absorb re-pointed
    # anything -- which is what the first version of this test did, pinning nothing.
    doomed.delete()

    assert PSNRawPayload.objects.filter(psn_data__concept=survivor,
                                        psn_data__psn_concept_id='4000').exists(), (
        'the payload did not travel with its row to the surviving concept'
    )


# --- wiring: the part that was completely untested ---------------------------------------------

def _capture_calls_in_sync_title_id():
    """Every `capture_psn_concept_data(...)` Call node inside `_job_sync_title_id`.

    Parsed with `ast`, not grepped. A source-string scan would be satisfied by the several COMMENTS
    in that function that name the capture -- the exact trap that has made guard tests in this repo
    pass while the code they guarded was absent.
    """
    import ast
    import pathlib

    src = pathlib.Path('trophies/token_keeper.py').read_text(encoding='utf-8')
    for node in ast.walk(ast.parse(src)):
        if isinstance(node, ast.FunctionDef) and node.name == '_job_sync_title_id':
            return [
                c for c in ast.walk(node)
                if isinstance(c, ast.Call)
                and isinstance(c.func, ast.Name)
                and c.func.id == 'capture_psn_concept_data'
            ]
    raise AssertionError('_job_sync_title_id not found -- it was renamed or moved; retarget this test')


def test_every_branch_that_resolves_a_concept_captures():
    """The gap this file did not cover: deleting every call site left the whole suite green.

    `_job_sync_title_id` resolves `concept` through a three-armed if/elif/else -- freshly anchored,
    already anchored, and PSN-native. All three hold the full `details` response. The already-anchored
    arm was missed on the first pass precisely because three docstrings named the wrong enclosing
    function, so this asserts the count rather than trusting a reading of the branch.
    """
    calls = _capture_calls_in_sync_title_id()

    assert len(calls) == 3, (
        f'expected one capture per concept-resolving branch, found {len(calls)}. '
        'A new branch that binds `concept` needs one too, or PSN data is silently dropped there.'
    )


def test_every_capture_site_passes_the_answering_region():
    """Region is part of the row's identity, and it is only knowable at these call sites.

    Without this, `country`/`language` are `''` on every production row while the unit tests above
    pass them explicitly and look like coverage -- a column the model calls essential, dead in prod.
    """
    for call in _capture_calls_in_sync_title_id():
        kwargs = {k.arg for k in call.keywords}
        assert {'country', 'language'} <= kwargs, (
            f'capture at token_keeper.py:{call.lineno} omits the region; '
            f'passes only {sorted(kwargs)}'
        )


def test_two_regions_of_one_concept_coexist_instead_of_overwriting():
    """The whole reason `name` and `name_en` are separate columns.

    Keyed on psn_concept_id ALONE, the US sync overwrites the JP row's native name and its raw
    payload, and the CJK name is gone from both copies -- the exact loss this table exists to stop,
    relocated one layer down. Keyed per region, they are two rows.
    """
    concept = ConceptFactory()

    capture_psn_concept_data(concept, _details(), country='JP', language='ja')
    capture_psn_concept_data(
        concept, _details(name='Ghost of Tsushima'), country='US', language='en-US',
    )

    assert PSNConceptData.objects.count() == 2
    assert PSNConceptData.objects.get(country='JP').name == 'ゴースト・オブ・ツシマ'
    assert PSNConceptData.objects.get(country='US').name == 'Ghost of Tsushima'
    assert PSNRawPayload.objects.count() == 2, 'the raw payloads must not share a row either'


def test_the_kill_switch_stops_every_write(settings):
    """`capture is eating disk` should be an env-var flip and a worker restart, not a deploy."""
    settings.PSN_METADATA_CAPTURE_ENABLED = False

    assert capture_psn_concept_data(ConceptFactory(), _details()) is None
    assert PSNConceptData.objects.count() == 0
    assert PSNRawPayload.objects.count() == 0


def test_a_failure_is_logged_rather_than_vanishing(monkeypatch, caplog):
    """The companion to `test_capture_never_costs_a_sync`: swallowing is only acceptable because the
    failure is still visible. Without this, deleting the logger call passes the whole suite."""
    from trophies.services import psn_metadata_service as svc

    def boom(*a, **k):
        raise RuntimeError('database went away')

    monkeypatch.setattr(svc.PSNConceptData.objects, 'update_or_create', boom)

    with caplog.at_level('WARNING', logger=svc.logger.name):
        svc.capture_psn_concept_data(ConceptFactory(), _details())

    assert any('PSN metadata capture failed' in r.message for r in caplog.records), (
        'the capture failed silently'
    )
