"""The region audit: the corrected region table, the regional unit, and each bucket rule.

These rules are what the region rebuild will implement, so they are pinned here first. The two that
matter most are the ones the old code got wrong: `KP` is Korea and `IP` names no region (both checked
against the andshrew dataset), and the regional unit is the raw IGDB VERSION, so a list in an `X-N`
sibling Concept (Chinese Astro Bot) counts as regional while another version in the same canonical
family (The Last of Us Remastered next to the original) does not.
"""
import io

import pytest
from django.core.management import call_command

from tests.factories import ConceptFactory, GameFactory, IGDBMatchFactory
from trophies.models import TitleID
from trophies.services import region_service as rs

pytestmark = pytest.mark.django_db


def _tid(title_id, region, platform='PS5'):
    TitleID.objects.create(title_id=title_id, platform=platform, region=region)
    return title_id


def _anchored(igdb_id, status='auto_accepted', concept_id=None):
    concept = ConceptFactory(concept_id=concept_id) if concept_id else ConceptFactory()
    IGDBMatchFactory(concept=concept, igdb_id=igdb_id, status=status)
    return concept


def _rows():
    rows, _stats = rs.scan()
    return {r['pk']: r for r in rows}


def _buckets(game):
    row = _rows().get(game.pk)
    return row['buckets'] if row else []


def _run(*args):
    out = io.StringIO()
    call_command('audit_regions', *args, stdout=out)
    return out.getvalue()


# --- the corrected table -------------------------------------------------------------------

@pytest.mark.parametrize('code,region', [
    ('KP', 'KR'),     # Korea, NOT Japan: the dataset's KP rows carry Hangul names
    ('IP', None),     # Sony worldwide titles and our "unknown" placeholder: no region, NOT NA
    ('UP', 'NA'), ('UB', 'NA'), ('EP', 'EU'), ('EB', 'EU'), ('JP', 'JP'), ('JB', 'JP'),
    ('HP', 'AS'), ('HB', 'AS'), ('CN', 'CN'),
    ('', None), (None, None), ('ZZ', None),
    ('ep', 'EU'), (' UP ', 'NA'),
])
def test_normalize_region_fixes_and_edges(code, region):
    assert rs.normalize_region(code) == region


def test_expected_regions_skips_unknown_and_missing_title_ids():
    tid_map = {'A': 'EP', 'B': 'IP', 'C': 'UP'}
    assert rs.expected_regions(['A', 'B', 'C', 'MISSING'], tid_map) == {'EU', 'NA'}
    assert rs.expected_regions([], tid_map) == set()
    assert rs.expected_regions(None, tid_map) == set()


# --- the regional unit ---------------------------------------------------------------------

def test_unit_key_prefers_a_trusted_igdb_version():
    assert rs.unit_key(5, 1009, 'accepted') == 'igdb:1009'
    assert rs.unit_key(5, 1009, 'auto_accepted') == 'igdb:1009'


@pytest.mark.parametrize('igdb_id,status', [(1009, 'pending_review'), (1009, 'rejected'), (None, 'accepted'),
                                            (None, None)])
def test_unit_key_falls_back_to_the_concept(igdb_id, status):
    assert rs.unit_key(5, igdb_id, status) == 'concept:5'


def test_unit_key_without_a_concept_is_none():
    assert rs.unit_key(None, None, None) is None


# --- label buckets -------------------------------------------------------------------------

def test_dataset_disagrees_when_stored_differs_from_title_ids():
    bad = GameFactory(title_ids=[_tid('PPSA03208_00', 'EP')], region=['NA', 'EU'])
    good = GameFactory(title_ids=[_tid('PPSA08338_00', 'EP')], region=['EU'])
    assert rs.DATASET_DISAGREES in _buckets(bad)
    assert _buckets(good) == []


def test_dataset_disagrees_also_catches_a_missing_region():
    game = GameFactory(title_ids=[_tid('PPSA03209_00', 'JP')], region=[])
    assert rs.DATASET_DISAGREES in _buckets(game)


def test_no_dataset_verdict_when_title_ids_say_nothing():
    """Unknown title IDs give nothing to compare against: that is not a disagreement."""
    game = GameFactory(title_ids=[_tid('CUSA00029_00', 'IP', 'PS4')], region=['EU'])
    assert rs.DATASET_DISAGREES not in _buckets(game)


def test_korea_labeled_jp():
    bad = GameFactory(title_ids=[_tid('CUSA02834_00', 'KP', 'PS4')], region=['JP'])
    good = GameFactory(title_ids=[_tid('CUSA03038_00', 'KP', 'PS4')], region=['KR'])
    assert rs.KOREA_AS_JP in _buckets(bad)
    assert rs.KOREA_AS_JP not in _buckets(good)


def test_na_from_unknown():
    bad = GameFactory(title_ids=[_tid('CUSA00015_00', 'IP', 'PS4')], region=['NA'])
    no_tids = GameFactory(title_ids=[], region=['NA'])
    good = GameFactory(title_ids=[_tid('PPSA02225_00', 'UP')], region=['NA'])
    assert rs.NA_FROM_UNKNOWN in _buckets(bad)
    # No title IDs is no evidence either way: not the IP-derived case this bucket measures.
    assert rs.NA_FROM_UNKNOWN not in _buckets(no_tids)
    assert _buckets(good) == []


def test_na_beside_a_known_non_na_title_id_is_the_storefront_bug_not_unknown():
    game = GameFactory(title_ids=[_tid('PPSA02101_00', 'EP')], region=['NA', 'EU'])
    buckets = _buckets(game)
    assert rs.NA_FROM_UNKNOWN not in buckets
    assert rs.DATASET_DISAGREES in buckets


def test_korea_as_jp_ignores_a_list_that_is_also_genuinely_jp():
    """JP and KP title IDs on one list: JP is right, KR is missing. That gap is dataset_disagrees,
    not a Korean list mislabeled as Japanese."""
    game = GameFactory(title_ids=[_tid('CUSA90001_00', 'JP', 'PS4'), _tid('CUSA90002_00', 'KP', 'PS4')],
                       region=['JP'])
    buckets = _buckets(game)
    assert rs.KOREA_AS_JP not in buckets
    assert rs.DATASET_DISAGREES in buckets


# --- regional-flag buckets -----------------------------------------------------------------

def test_sibling_concepts_on_one_igdb_version_are_regional():
    """The Chinese Astro Bot shape: a different trophy fingerprint puts it in its own Concept, but it
    is the same IGDB version on the same platform, so the pair is regional."""
    west = GameFactory(concept=_anchored(204580, concept_id='204580'), region=['NA'], is_regional=False)
    china = GameFactory(concept=_anchored(204580, concept_id='204580-2'), region=['CN'], is_regional=False)
    assert rs.REGIONAL_MISSED in _buckets(west)
    assert rs.REGIONAL_MISSED in _buckets(china)


def test_other_versions_in_one_canonical_family_are_not_regional():
    """The Last of Us shape: the original and Remastered share a canonical family but are different
    IGDB versions, so they are not each other's regional siblings."""
    # A real canonical family: '1009' and its sibling '1009-2', anchored to different raw versions.
    original = GameFactory(concept=_anchored(1009, concept_id='1009'), title_platform=['PS4'],
                           is_regional=True, region=['NA'])
    remaster = GameFactory(concept=_anchored(5328, concept_id='1009-2'), title_platform=['PS4'],
                           is_regional=True, region=['NA'])
    for game in (original, remaster):
        buckets = _buckets(game)
        assert rs.REGIONAL_STALE in buckets
        assert rs.SAME_REGION_SIBLINGS not in buckets


def test_untrusted_matches_do_not_group():
    a = GameFactory(concept=_anchored(777, status='pending_review'), is_regional=False)
    b = GameFactory(concept=_anchored(777, status='pending_review'), is_regional=False)
    assert rs.REGIONAL_MISSED not in _buckets(a)
    assert rs.REGIONAL_MISSED not in _buckets(b)


def test_unmatched_lists_group_by_concept():
    concept = ConceptFactory()
    a = GameFactory(concept=concept, is_regional=False)
    GameFactory(concept=concept, is_regional=False)
    assert rs.REGIONAL_MISSED in _buckets(a)


def test_lists_without_a_concept_have_no_siblings():
    a = GameFactory(concept=None, is_regional=False)
    GameFactory(concept=None, is_regional=False)
    assert _buckets(a) == []


def test_siblings_must_share_a_platform():
    concept = _anchored(42)
    ps4 = GameFactory(concept=concept, title_platform=['PS4'], is_regional=False)
    GameFactory(concept=concept, title_platform=['PS5'], is_regional=False)
    assert rs.REGIONAL_MISSED not in _buckets(ps4)


def test_cross_buy_list_is_a_sibling_on_each_platform():
    concept = _anchored(43)
    cross = GameFactory(concept=concept, title_platform=['PS4', 'PS5'], is_regional=False)
    ps5 = GameFactory(concept=concept, title_platform=['PS5'], is_regional=False)
    assert rs.REGIONAL_MISSED in _buckets(cross)
    assert rs.REGIONAL_MISSED in _buckets(ps5)


def test_flagged_siblings_with_different_labels_are_clean():
    concept = _anchored(44)
    na = GameFactory(concept=concept, title_ids=[_tid('PPSA00001_00', 'UP')], region=['NA'], is_regional=True)
    eu = GameFactory(concept=concept, title_ids=[_tid('PPSA00002_00', 'EP')], region=['EU'], is_regional=True)
    assert _buckets(na) == []
    assert _buckets(eu) == []


def test_same_region_siblings():
    concept = _anchored(45)
    a = GameFactory(concept=concept, region=['NA'], is_regional=True)
    b = GameFactory(concept=concept, region=['NA'], is_regional=True)
    c = GameFactory(concept=concept, region=['JP'], is_regional=True)
    assert rs.SAME_REGION_SIBLINGS in _buckets(a)
    assert rs.SAME_REGION_SIBLINGS in _buckets(b)
    assert rs.SAME_REGION_SIBLINGS not in _buckets(c)


def test_lists_sharing_a_title_id_are_one_product_not_siblings():
    """Two trophy lists under one title ID come from the same storefront product: not regional."""
    concept = _anchored(49)
    shared = _tid('PPSA77777_00', 'UP')
    a = GameFactory(concept=concept, title_ids=[shared], region=['NA'], is_regional=False)
    b = GameFactory(concept=concept, title_ids=[shared], region=['NA'], is_regional=False)
    for game in (a, b):
        assert _buckets(game) == []


def test_a_shared_title_id_pair_still_duplicates_a_third_product():
    concept = _anchored(50)
    shared = _tid('PPSA77778_00', 'UP')
    a = GameFactory(concept=concept, title_ids=[shared], region=['NA'], is_regional=True)
    GameFactory(concept=concept, title_ids=[shared], region=['NA'], is_regional=True)
    other = GameFactory(concept=concept, title_ids=[_tid('PPSA77779_00', 'UP')], region=['NA'], is_regional=True)
    assert rs.SAME_REGION_SIBLINGS in _buckets(a)
    assert rs.SAME_REGION_SIBLINGS in _buckets(other)


def test_empty_labels_are_not_same_region_siblings():
    concept = _anchored(46)
    a = GameFactory(concept=concept, region=[], is_regional=True)
    GameFactory(concept=concept, region=[], is_regional=True)
    assert rs.SAME_REGION_SIBLINGS not in _buckets(a)


def test_regional_stale_marks_asian_only_lone_lists():
    jp = GameFactory(region=['JP'], is_regional=True)
    na = GameFactory(region=['NA'], is_regional=True)
    rows = _rows()
    assert rs.REGIONAL_STALE in rows[jp.pk]['buckets'] and rows[jp.pk]['asian_only'] is True
    assert rs.REGIONAL_STALE in rows[na.pk]['buckets'] and rows[na.pk]['asian_only'] is False


def test_regional_no_region():
    game = GameFactory(region=[], is_regional=True)
    assert rs.REGIONAL_NO_REGION in _buckets(game)


# --- scan behaviour ------------------------------------------------------------------------

def test_region_locked_lists_are_skipped_and_counted():
    locked = GameFactory(title_ids=[_tid('CUSA00015_00', 'IP', 'PS4')], region=['NA'], region_lock=True)
    rows, stats = rs.scan()
    assert locked.pk not in {r['pk'] for r in rows}
    assert stats['locked'] == 1 and stats['scanned'] == 1


def test_locked_lists_still_count_as_siblings():
    """A locked list's region is admin truth, but it is still another list of the same game."""
    concept = _anchored(47)
    GameFactory(concept=concept, region=['JP'], is_regional=True, region_lock=True)
    open_list = GameFactory(concept=concept, region=['NA'], is_regional=False)
    assert rs.REGIONAL_MISSED in _buckets(open_list)


def test_platform_filter_keeps_siblings_found_on_other_platforms():
    concept = _anchored(48)
    cross = GameFactory(concept=concept, title_platform=['PS4', 'PS5'], is_regional=False)
    ps5_only = GameFactory(concept=concept, title_platform=['PS5'], is_regional=False)
    rows, stats = rs.scan(platform='PS4')
    by_pk = {r['pk']: r for r in rows}
    assert rs.REGIONAL_MISSED in by_pk[cross.pk]['buckets']
    assert ps5_only.pk not in by_pk
    assert stats['scanned'] == 1


def test_coverage_counts_lists_with_a_region_per_platform():
    GameFactory(title_platform=['PS4'], region=['NA'])
    GameFactory(title_platform=['PS4'], region=[])
    GameFactory(title_platform=['PSVITA'], region=[])
    _rows_, stats = rs.scan()
    assert stats['coverage'] == {'PS4': [2, 1], 'PSVITA': [1, 0]}


def test_a_repeated_platform_counts_once_in_coverage():
    GameFactory(title_platform=['PS4', 'PS4'], region=['NA'], is_regional=False)
    _rows_, stats = rs.scan()
    assert stats['coverage'] == {'PS4': [1, 1]}


def test_legacy_scalar_shapes_are_read_as_one_value():
    """jsonb stores a bare string happily; reading `'NA'` as an iterable would give {'N', 'A'}."""
    game = GameFactory(title_ids='CUSA02834_00', region='JP')
    _tid('CUSA02834_00', 'KP', 'PS4')
    row = _rows()[game.pk]
    assert row['stored'] == ['JP']
    assert row['title_ids'] == [('CUSA02834_00', 'KP')]
    assert rs.KOREA_AS_JP in row['buckets']


def test_platform_filter_scopes_coverage_to_that_platform():
    GameFactory(title_platform=['PS4', 'PS5'], region=['NA'])
    _rows_, stats = rs.scan(platform='PS5')
    assert stats['coverage'] == {'PS5': [1, 1]}


def test_row_carries_raw_title_id_codes_for_the_read_out():
    game = GameFactory(title_ids=[_tid('CUSA02834_00', 'KP', 'PS4'), 'UNKNOWN_00'], region=['JP'])
    row = _rows()[game.pk]
    assert row['title_ids'] == [('CUSA02834_00', 'KP'), ('UNKNOWN_00', None)]
    assert row['expected'] == ['KR'] and row['stored'] == ['JP']


# --- the command ---------------------------------------------------------------------------

def test_command_on_an_empty_catalogue():
    assert 'No trophy lists found.' in _run()


def test_command_summary_and_sample():
    GameFactory(title_name='Korean List', title_ids=[_tid('CUSA02834_00', 'KP', 'PS4')], region=['JP'])
    out = _run()
    assert '1 list scanned' in out
    assert 'korea_as_jp' in out
    assert 'Korean List' in out
    assert 'CUSA02834_00=KP' in out
    assert 'title IDs say KR' in out


def test_command_orders_samples_most_played_first_and_caps_them():
    for n, (name, plays) in enumerate([('Quiet', 1), ('Loud', 900), ('Middle', 50)]):
        GameFactory(title_name=name, played_count=plays, region=['NA'],
                    title_ids=[_tid(f'CUSA8000{n}_00', 'IP', 'PS4')])
    out = _run('--sample', '2')
    section = out.split('na_from_unknown --', 1)[1]
    assert section.index('Loud') < section.index('Middle')
    assert 'Quiet' not in section.split('... and', 1)[0]
    assert '... and 1 more (--bucket na_from_unknown' in section


def test_command_bucket_drill_down_lists_everything():
    for n in range(3):
        GameFactory(title_name=f'NA Guess {n}', region=['NA'], title_ids=[_tid(f'CUSA8100{n}_00', 'IP', 'PS4')])
    out = _run('--bucket', 'na_from_unknown', '--sample', '1')
    assert all(f'NA Guess {n}' in out for n in range(3))
    assert '... and' not in out


def test_command_empty_bucket_drill_down_says_so():
    GameFactory(region=[], title_ids=[])
    out = _run('--bucket', 'korea_as_jp')
    assert 'No lists in this bucket.' in out


def test_command_platform_scope():
    GameFactory(title_platform=['PS4'], region=['NA'], title_ids=[])
    out = _run('--platform', 'PS5')
    assert 'No trophy lists found.' in out


def test_command_percentages_leave_out_locked_lists():
    GameFactory(region=['NA'], title_ids=[_tid('CUSA82000_00', 'IP', 'PS4')])
    GameFactory(region=['NA'], region_lock=True)
    out = _run()
    assert '2 lists scanned; 1 skipped' in out
    assert '1 list (100.0% of the 1 judged)' in out


def test_command_when_every_list_is_locked():
    GameFactory(region=['NA'], region_lock=True)
    assert 'Every list is region-locked; nothing to judge.' in _run()


def test_command_notes_legacy_platform_misses():
    concept = _anchored(51)
    GameFactory(concept=concept, title_platform=['PSVITA'], is_regional=False)
    GameFactory(concept=concept, title_platform=['PSVITA'], is_regional=False)
    assert '(2 of the regional_missed lists are PS3/Vita/other, which sync never checks.)' in _run()
