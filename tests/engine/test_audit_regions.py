"""The region audit: the corrected region table, the regional unit, the regional rule, each bucket.

These rules are what the region rebuild will implement, so they are pinned here first. The ones the old
code got wrong: `KP` is Korea and `IP` names no region (both checked against the andshrew dataset); the
unit is the raw IGDB VERSION on a hardware platform, so a list in an `X-N` sibling Concept (Chinese
Astro Bot) can be regional while another version in the same canonical family (The Last of Us
Remastered) cannot, and a shared PSVR tag does not make a PS4 and a PS5 list siblings; and being a
sibling is not enough, the regions must not overlap (the prod baseline's Dead by Daylight split lists
and Fall Guys relaunch list are siblings from the same regions, not regional versions).
"""
import io
import itertools

import pytest
from django.core.management import call_command
from django.utils import timezone

from tests.factories import ConceptFactory, GameFactory, IGDBMatchFactory
from trophies.models import TitleID
from trophies.services import region_service as rs

pytestmark = pytest.mark.django_db

_TIDS = itertools.count(10000)


def _tid(title_id, region, platform='PS5', *, walked=True, stores=None, source=TitleID.REGION_SOURCE_DATASET):
    """A TitleID with dataset prefix `region`. By default WALKED, with the stores agreeing with the
    prefix (`stores` overrides, `[]` = sold nowhere), because the regional rule only acts on walked
    title IDs; pass `walked=False` for one the store walk has not reached."""
    if stores is None:
        stores = [r for r in [rs.normalize_region(region)] if r]
    TitleID.objects.create(
        title_id=title_id, platform=platform, region=region, region_source=source,
        store_regions=stores if walked else [], store_checked_at=timezone.now() if walked else None,
    )
    return title_id


def _anchored(igdb_id, status='auto_accepted', concept_id=None):
    concept = ConceptFactory(concept_id=concept_id) if concept_id else ConceptFactory()
    IGDBMatchFactory(concept=concept, igdb_id=igdb_id, status=status)
    return concept


def _list(concept=None, codes=('UP',), *, region=None, title_ids=None, **kw):
    """A trophy list whose title IDs carry `codes` (dataset prefixes). Its stored region defaults to
    what those title IDs say, so a test only spells out the label when the label is the point."""
    if title_ids is None:
        title_ids = [_tid(f'PPSA{next(_TIDS)}_00', code) for code in codes]
    if region is None:
        region = sorted({rs.normalize_region(code) for code in codes} - {None})
    return GameFactory(concept=concept, title_ids=title_ids, region=region, **kw)


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


# --- a title ID's regions ------------------------------------------------------------------

_NOW = '2026-10-23T00:00:00Z'


@pytest.mark.parametrize('prefix,source,stores,checked,label,verdict', [
    # Walked and sold somewhere: the stores are the truth for both, whatever the prefix says.
    ('HP', 'dataset', ['NA', 'EU', 'JP', 'AS', 'KR'], _NOW, {'NA', 'EU', 'JP', 'AS', 'KR'},
     {'NA', 'EU', 'JP', 'AS', 'KR'}),
    # Walked, sold nowhere (delisted / relisted): the trusted prefix is the best evidence left.
    ('EP', 'dataset', [], _NOW, {'EU'}, {'EU'}),
    ('UP', '', [], _NOW, set(), set()),
    # Unwalked: the trusted prefix labels it, but the flag must not act on a guess.
    ('EP', 'dataset', [], None, {'EU'}, set()),
    # Unwalked and untrusted (sync used to write the US store's answer here): nothing at all.
    ('UP', '', [], None, set(), set()),
    ('IP', 'dataset', [], None, set(), set()),
])
def test_title_regions_by_source(prefix, source, stores, checked, label, verdict):
    t = rs._title_regions(prefix, source, stores, checked)
    assert set(t.label) == label
    assert set(t.verdict) == verdict
    assert t.walked is (checked is not None)


def test_label_and_verdict_regions_union_a_lists_title_ids():
    _tid('A_00', 'EP')
    _tid('B_00', 'IP')
    _tid('C_00', 'JP', walked=False)
    info = rs.title_id_info()
    assert rs.label_regions(['A_00', 'B_00', 'C_00', 'MISSING_00'], info) == {'EU', 'JP'}
    assert rs.verdict_regions(['A_00', 'B_00', 'C_00', 'MISSING_00'], info) == {'EU'}
    assert rs.label_regions([], info) == set() and rs.verdict_regions(None, info) == set()


def test_title_id_info_can_be_scoped():
    _tid('A_00', 'EP')
    _tid('B_00', 'UP')
    assert set(rs.title_id_info(['A_00'])) == {'A_00'}


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


@pytest.mark.parametrize('platforms,expected', [
    (['PS4', 'PSVR'], ['PS4']),
    (['PS5', 'PSVR2', 'PSVR'], ['PS5']),
    (['PS5', 'PSPC'], ['PS5']),
    (['PS4', 'PSVITA'], ['PS4', 'PSVITA']),
    (['PSVR'], ['PSVR']),       # a tag-only list keeps its tags rather than vanishing from grouping
    ([], []),
])
def test_sibling_platforms_are_the_consoles(platforms, expected):
    assert rs.sibling_platforms(platforms) == expected


# --- label buckets -------------------------------------------------------------------------

def test_wrong_region_when_stored_has_a_code_the_title_ids_do_not_support():
    bad = _list(codes=['EP'], region=['NA', 'EU'])
    good = _list(codes=['EP'])
    assert rs.WRONG_REGION in _buckets(bad)
    assert rs.MISSING_REGION not in _buckets(bad)
    assert _buckets(good) == []


def test_missing_region_when_stored_only_lacks_a_supported_code():
    empty = _list(codes=['JP'], region=[])
    partial = _list(codes=['EP', 'JP'], region=['EU'])
    assert rs.MISSING_REGION in _buckets(empty)
    assert rs.MISSING_REGION in _buckets(partial)
    assert rs.WRONG_REGION not in _buckets(partial)


def test_a_list_both_wrong_and_missing_counts_as_wrong():
    game = _list(codes=['EP', 'JP'], region=['NA', 'EU'])
    buckets = _buckets(game)
    assert rs.WRONG_REGION in buckets
    assert rs.MISSING_REGION not in buckets


def test_no_label_verdict_when_title_ids_say_nothing():
    """Unknown title IDs give nothing to compare against: that is not a disagreement."""
    game = _list(codes=['IP'], region=['EU'])
    buckets = _buckets(game)
    assert rs.WRONG_REGION not in buckets
    assert rs.MISSING_REGION not in buckets


def test_korea_labeled_jp():
    bad = _list(codes=['KP'], region=['JP'])
    good = _list(codes=['KP'], region=['KR'])
    assert rs.KOREA_AS_JP in _buckets(bad)
    assert rs.KOREA_AS_JP not in _buckets(good)


def test_korea_as_jp_ignores_a_list_that_is_also_genuinely_jp():
    """JP and KP title IDs on one list: JP is right, KR is missing. That gap is missing_region, not a
    Korean list mislabeled as Japanese."""
    game = _list(codes=['JP', 'KP'], region=['JP'])
    buckets = _buckets(game)
    assert rs.KOREA_AS_JP not in buckets
    assert rs.MISSING_REGION in buckets


def test_na_from_unknown():
    bad = _list(codes=['IP'], region=['NA'])
    no_tids = GameFactory(title_ids=[], region=['NA'])
    good = _list(codes=['UP'])
    assert rs.NA_FROM_UNKNOWN in _buckets(bad)
    # No title IDs is no evidence either way: not the IP-derived case this bucket measures.
    assert rs.NA_FROM_UNKNOWN not in _buckets(no_tids)
    assert _buckets(good) == []


def test_na_beside_a_known_non_na_title_id_is_the_storefront_bug_not_unknown():
    game = _list(codes=['EP'], region=['NA', 'EU'])
    buckets = _buckets(game)
    assert rs.NA_FROM_UNKNOWN not in buckets
    assert rs.WRONG_REGION in buckets


# --- the regional rule -------------------------------------------------------------------

def test_sibling_concepts_on_one_igdb_version_with_disjoint_regions_are_regional():
    """The Chinese Astro Bot shape: a different trophy fingerprint puts it in its own Concept, but it
    is the same IGDB version on the same platform, and its regions do not overlap."""
    west = _list(_anchored(204580, concept_id='204580'), ['UP', 'EP', 'HP', 'JP'])
    china = _list(_anchored(204580, concept_id='204580-2'), ['CN'])
    rows = _rows()
    for game in (west, china):
        assert rs.REGIONAL_MISSED in rows[game.pk]['buckets']
        assert (rows[game.pk]['verdict'], rows[game.pk]['reason']) == (rs.REGIONAL, rs.DISJOINT_SIBLING)


def test_other_versions_in_one_canonical_family_are_not_siblings():
    """The Last of Us shape: the original and Remastered share a canonical family but are different
    IGDB versions. Disjoint regions would make them regional IF they grouped, so they must not."""
    original = _list(_anchored(1009, concept_id='1009'), ['UP'], title_platform=['PS4'], is_regional=True)
    remaster = _list(_anchored(5328, concept_id='1009-2'), ['EP'], title_platform=['PS4'], is_regional=True)
    for game in (original, remaster):
        assert rs.REGIONAL_STALE in _buckets(game)


def test_split_lists_from_the_same_regions_are_not_regional():
    """The Dead by Daylight shape: one game's trophies split across lists, every list EU/NA."""
    concept = _anchored(18866)
    parts = [_list(concept, ['UP', 'EP'], is_regional=True) for _ in range(3)]
    rows = _rows()
    for game in parts:
        assert rs.REGIONAL_STALE in rows[game.pk]['buckets']
        assert rows[game.pk]['reason'] == rs.OVERLAPPING_SIBLINGS


def test_a_relaunch_list_with_overlapping_regions_is_not_regional():
    """The Fall Guys shape: the free-to-play relaunch list (EU) beside the original (EU/NA)."""
    concept = _anchored(119313)
    original = _list(concept, ['UP', 'EP'], is_regional=True)
    relaunch = _list(concept, ['EP'], is_regional=True)
    for game in (original, relaunch):
        assert rs.REGIONAL_STALE in _buckets(game)


def test_the_rule_reads_title_ids_not_the_stored_label():
    """Both stored NA (the storefront bug), but the title IDs are NA and JP: a regional pair."""
    concept = _anchored(52)
    na = _list(concept, ['UP'], region=['NA'])
    jp = _list(concept, ['JP'], region=['NA'])
    rows = _rows()
    assert rs.REGIONAL_MISSED in rows[na.pk]['buckets']
    assert rs.REGIONAL_MISSED in rows[jp.pk]['buckets']
    assert rs.WRONG_REGION in rows[jp.pk]['buckets']


def test_an_asia_only_lone_list_is_regional():
    """Owner, 2026-10-23: a list from Asian regions alone is regional to that region."""
    unflagged = _list(codes=['JP'])
    flagged = _list(codes=['HP', 'KP'], is_regional=True)
    rows = _rows()
    assert rs.REGIONAL_MISSED in rows[unflagged.pk]['buckets']
    assert rows[unflagged.pk]['reason'] == rs.ASIA_ONLY
    assert flagged.pk not in rows


def test_a_global_lone_list_flagged_regional_is_stale():
    game = _list(codes=['UP', 'EP', 'JP', 'HP'], is_regional=True)
    row = _rows()[game.pk]
    assert rs.REGIONAL_STALE in row['buckets']
    assert (row['verdict'], row['reason']) == (rs.NOT_REGIONAL, rs.GLOBAL)


def test_a_list_without_region_data_keeps_its_flag():
    """Owner, 2026-10-23: unjudgeable lists keep whatever flag they have."""
    concept = _anchored(53)
    flagged = GameFactory(concept=concept, title_ids=[], region=['NA'], is_regional=True)
    unflagged = GameFactory(concept=concept, title_ids=[], region=[], is_regional=False)
    rows, stats = rs.scan()
    by_pk = {r['pk']: r for r in rows}
    assert flagged.pk not in by_pk
    assert unflagged.pk not in by_pk
    assert stats['verdicts'] == {rs.UNJUDGED: 2}
    assert stats['unjudged_flagged'] == 1


def test_a_sibling_without_region_data_leaves_the_list_unjudged():
    concept = _anchored(54)
    known = _list(concept, ['UP'], is_regional=True)
    GameFactory(concept=concept, title_ids=[], region=[])
    rows, stats = rs.scan()
    assert known.pk not in {r['pk'] for r in rows}
    assert stats['verdicts'][rs.UNJUDGED] == 2


def test_a_disjoint_sibling_wins_over_an_unknown_one():
    concept = _anchored(55)
    na = _list(concept, ['UP'])
    _list(concept, ['JP'])
    GameFactory(concept=concept, title_ids=[], region=[])
    assert rs.REGIONAL_MISSED in _buckets(na)


def test_untrusted_matches_do_not_group():
    a = _list(_anchored(777, status='pending_review'), ['UP'], is_regional=True)
    b = _list(_anchored(777, status='pending_review'), ['EP'], is_regional=True)
    assert rs.REGIONAL_STALE in _buckets(a)
    assert rs.REGIONAL_STALE in _buckets(b)


def test_unmatched_lists_group_by_concept():
    concept = ConceptFactory()
    a = _list(concept, ['UP'])
    _list(concept, ['EP'])
    assert rs.REGIONAL_MISSED in _buckets(a)


def test_lists_without_a_concept_have_no_siblings():
    a = _list(None, ['UP'])
    b = _list(None, ['EP'])
    assert _buckets(a) == [] and _buckets(b) == []


def test_siblings_must_share_a_platform():
    concept = _anchored(42)
    ps4 = _list(concept, ['UP'], title_platform=['PS4'], is_regional=True)
    _list(concept, ['EP'], title_platform=['PS5'], is_regional=True)
    assert rs.REGIONAL_STALE in _buckets(ps4)


def test_a_shared_vr_tag_does_not_make_ps4_and_ps5_lists_siblings():
    """The No Man's Sky shape from the baseline: PS4/PSVR and PS5/PSVR lists of one game."""
    concept = _anchored(3225)
    ps4 = _list(concept, ['UP'], title_platform=['PS4', 'PSVR2', 'PSVR'], is_regional=True)
    ps5 = _list(concept, ['EP'], title_platform=['PS5', 'PSVR2', 'PSVR'], is_regional=True)
    for game in (ps4, ps5):
        assert rs.REGIONAL_STALE in _buckets(game)


def test_a_tag_only_list_groups_on_its_tags():
    concept = _anchored(56)
    a = _list(concept, ['UP'], title_platform=['PSVR'])
    _list(concept, ['EP'], title_platform=['PSVR'])
    assert rs.REGIONAL_MISSED in _buckets(a)


def test_cross_buy_list_is_a_sibling_on_each_platform():
    concept = _anchored(43)
    cross = _list(concept, ['UP'], title_platform=['PS4', 'PS5'])
    ps5 = _list(concept, ['EP'], title_platform=['PS5'])
    assert rs.REGIONAL_MISSED in _buckets(cross)
    assert rs.REGIONAL_MISSED in _buckets(ps5)


def test_flagged_regional_siblings_are_clean():
    concept = _anchored(44)
    na = _list(concept, ['UP'], is_regional=True)
    eu = _list(concept, ['EP'], is_regional=True)
    assert _buckets(na) == []
    assert _buckets(eu) == []


def test_lists_sharing_a_title_id_are_one_product_not_siblings():
    """Two trophy lists under one title ID come from the same storefront product: not regional."""
    concept = _anchored(49)
    shared = [_tid('PPSA77777_00', 'UP')]
    a = _list(concept, ['UP'], title_ids=shared, is_regional=True)
    b = _list(concept, ['UP'], title_ids=shared, is_regional=True)
    rows = _rows()
    for game in (a, b):
        assert rows[game.pk]['reason'] == rs.GLOBAL


def test_one_product_gets_one_verdict_from_its_combined_regions():
    """A holds a JP title ID; B holds the same one plus a UP one. That is one product covering JP and
    NA, so neither list is Asia-only, and they are not each other's (overlapping) siblings."""
    concept = _anchored(50)
    jp = _tid('PPSA77778_00', 'JP')
    a = _list(concept, title_ids=[jp], region=['JP'])
    b = _list(concept, title_ids=[jp, _tid('PPSA77779_00', 'UP')], region=['JP', 'NA'])
    rows, stats = rs.scan()
    assert rows == []
    assert stats['verdicts'] == {rs.NOT_REGIONAL: 2}


def test_a_lone_list_without_region_data_is_unjudged():
    game = GameFactory(title_ids=[], region=['NA'], is_regional=True)
    rows, stats = rs.scan()
    assert game.pk not in {r['pk'] for r in rows}
    assert stats['verdicts'] == {rs.UNJUDGED: 1}
    verdicts, _compared = rs.regional_verdicts({}, {game.pk: []}, {game.pk: set()})
    assert verdicts[game.pk] == (rs.UNJUDGED, rs.NO_REGION_DATA)


def test_an_asia_only_list_is_regional_beside_an_unknown_sibling():
    concept = _anchored(57)
    jp = _list(concept, ['JP'])
    GameFactory(concept=concept, title_ids=[], region=[])
    row = _rows()[jp.pk]
    assert rs.REGIONAL_MISSED in row['buckets']
    assert row['reason'] == rs.ASIA_ONLY


def test_a_cross_buy_list_disjoint_on_one_console_is_regional():
    """Any group wins: overlapping on PS5 does not cancel a disjoint sibling on PS4."""
    concept = _anchored(58)
    cross = _list(concept, ['UP'], title_platform=['PS4', 'PS5'])
    _list(concept, ['EP'], title_platform=['PS4'])
    _list(concept, ['UP'], title_platform=['PS5'])
    row = _rows()[cross.pk]
    assert (row['verdict'], row['reason']) == (rs.REGIONAL, rs.DISJOINT_SIBLING)


def test_regional_no_region():
    game = GameFactory(region=[], is_regional=True)
    assert rs.REGIONAL_NO_REGION in _buckets(game)


def test_shown_marks_lists_whose_chips_render():
    shown = _list(codes=['EP'], region=['NA', 'EU'], is_regional=True)
    hidden = _list(codes=['EP'], region=['NA', 'EU'], is_regional=False)
    rows = _rows()
    assert rows[shown.pk]['shown'] is True
    assert rows[hidden.pk]['shown'] is False


# --- scan behaviour ------------------------------------------------------------------------

def test_region_locked_lists_are_skipped_and_counted():
    locked = _list(codes=['IP'], region=['NA'], region_lock=True)
    rows, stats = rs.scan()
    assert locked.pk not in {r['pk'] for r in rows}
    assert stats['locked'] == 1 and stats['scanned'] == 1
    assert stats['verdicts'] == {}


def test_locked_lists_still_count_as_siblings():
    """A locked list's region is admin truth, but it is still another list of the same game."""
    concept = _anchored(47)
    _list(concept, ['JP'], is_regional=True, region_lock=True)
    open_list = _list(concept, ['UP'])
    assert rs.REGIONAL_MISSED in _buckets(open_list)


def test_a_locked_list_is_judged_by_its_admin_set_region():
    """Its title IDs name no region, but an admin set it to JP: its NA sibling is regional."""
    concept = _anchored(59)
    _list(concept, ['IP'], region=['JP'], is_regional=True, region_lock=True)
    open_list = _list(concept, ['UP'])
    row = _rows()[open_list.pk]
    assert (row['verdict'], row['reason']) == (rs.REGIONAL, rs.DISJOINT_SIBLING)


def test_platform_filter_keeps_siblings_found_on_other_platforms():
    concept = _anchored(48)
    cross = _list(concept, ['UP'], title_platform=['PS4', 'PS5'])
    ps5_only = _list(concept, ['EP'], title_platform=['PS5'])
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
    GameFactory(title_platform=['PS4', 'PS4'], region=['NA'])
    _rows_, stats = rs.scan()
    assert stats['coverage'] == {'PS4': [1, 1]}


def test_legacy_scalar_shapes_are_read_as_one_value():
    """jsonb stores a bare string happily; reading `'NA'` as an iterable would give {'N', 'A'}."""
    game = GameFactory(title_ids='CUSA02834_00', region='JP')
    _tid('CUSA02834_00', 'KP', 'PS4')
    row = _rows()[game.pk]
    assert row['stored'] == ['JP']
    assert row['title_ids'] == [('CUSA02834_00', 'KP', ['KR'])]
    assert rs.KOREA_AS_JP in row['buckets']


def test_platform_filter_scopes_coverage_to_that_platform():
    GameFactory(title_platform=['PS4', 'PS5'], region=['NA'])
    _rows_, stats = rs.scan(platform='PS5')
    assert stats['coverage'] == {'PS5': [1, 1]}


def test_row_carries_raw_title_id_codes_for_the_read_out():
    game = GameFactory(title_ids=[_tid('CUSA02834_00', 'KP', 'PS4'), _tid('CUSA02835_00', 'KP', 'PS4', walked=False),
                                  'UNKNOWN_00'], region=['JP'])
    row = _rows()[game.pk]
    # (title ID, dataset prefix, stores that sell it); None stores = not walked yet.
    assert row['title_ids'] == [('CUSA02834_00', 'KP', ['KR']), ('CUSA02835_00', 'KP', None), ('UNKNOWN_00', None, None)]
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
    assert 'rule says regional (asia_only)' in out


def test_command_orders_samples_most_played_first_and_caps_them():
    for name, plays in [('Quiet', 1), ('Loud', 900), ('Middle', 50)]:
        _list(codes=['IP'], title_name=name, played_count=plays, region=['NA'])
    out = _run('--sample', '2')
    section = out.split('na_from_unknown --', 1)[1]
    assert section.index('Loud') < section.index('Middle')
    assert 'Quiet' not in section.split('... and', 1)[0]
    assert '... and 1 more (--bucket na_from_unknown' in section


def test_command_bucket_drill_down_lists_everything():
    for n in range(3):
        _list(codes=['IP'], title_name=f'NA Guess {n}', region=['NA'])
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
    _list(codes=['IP'], region=['NA'])
    GameFactory(region=['NA'], region_lock=True)
    out = _run()
    assert '2 lists scanned; 1 skipped' in out
    assert '1 list (100.0% of the 1 judged)' in out


def test_command_when_every_list_is_locked():
    GameFactory(region=['NA'], region_lock=True)
    assert 'Every list is region-locked; nothing to judge.' in _run()


def test_command_tallies_reasons_and_verdicts():
    concept = _anchored(51)
    _list(concept, ['UP'])
    _list(concept, ['JP'])
    _list(codes=['UP', 'EP'], is_regional=True)
    GameFactory(title_ids=[], region=['NA'], is_regional=True)
    out = _run()
    assert '(regional_missed: 2 disjoint_sibling)' in out
    assert '(regional_stale: 1 global)' in out
    verdict_lines = {line.split()[0]: line.split()[1] for line in out.splitlines()
                     if line.strip().split()[:1] in (['regional'], ['not_regional'], ['unjudged'])}
    assert verdict_lines == {'regional': '2', 'not_regional': '1', 'unjudged': '1'}
    assert '(1 unjudged lists are flagged regional today and keep it' in out
    assert 'rule says regional (disjoint_sibling)' in out


def test_command_shown_column_counts_rendered_chips():
    _list(codes=['EP'], region=['NA', 'EU'], is_regional=True)
    _list(codes=['EP'], region=['NA', 'EU'], is_regional=False)
    line = next(line for line in _run().splitlines() if line.strip().startswith('wrong_region'))
    assert line.split()[1:4] == ['2', '100.0%', '1']


# --- sibling detail and walk size --------------------------------------------------------

def test_a_row_names_the_siblings_its_verdict_compared_against():
    concept = _anchored(60)
    shared = [_tid('PPSA88880_00', 'UP')]
    target = _list(concept, ['UP'], title_ids=shared, is_regional=True)
    same_product = _list(concept, ['UP'], title_ids=shared, is_regional=True)
    other = _list(concept, ['UP', 'EP'], is_regional=True)
    row = _rows()[target.pk]
    assert [s['np_id'] for s in row['siblings']] == [other.np_communication_id]
    assert row['siblings'][0]['regions'] == ['EU', 'NA']
    assert same_product.np_communication_id not in {s['np_id'] for s in row['siblings']}


def test_a_listed_sibling_shows_its_product_regions():
    """B alone names no region (IP), but B and C share a title ID: one product worth EU. That union is
    what the rule compared, so it is what the read-out shows, for both lists."""
    concept = _anchored(62)
    t1 = _tid('PPSA88883_00', 'IP')
    target = _list(concept, ['UP'])
    b = _list(concept, title_ids=[t1], region=[])
    c = _list(concept, title_ids=[t1, _tid('PPSA88884_00', 'EP')], region=['EU'])
    row = _rows()[target.pk]
    assert row['reason'] == rs.DISJOINT_SIBLING
    by_np = {s['np_id']: s for s in row['siblings']}
    assert by_np[b.np_communication_id]['regions'] == ['EU']
    assert by_np[c.np_communication_id]['regions'] == ['EU']


def test_disjoint_siblings_sort_first_so_the_cap_cannot_hide_them():
    concept = _anchored(63)
    target = _list(concept, ['UP'])
    for _ in range(4):
        _list(concept, ['UP'])
    eu = _list(concept, ['EP'])
    row = _rows()[target.pk]
    assert row['siblings'][0]['np_id'] == eu.np_communication_id
    assert row['siblings'][0]['disjoint'] is True
    assert not any(s['disjoint'] for s in row['siblings'][1:])


def test_siblings_on_another_console_are_not_listed():
    concept = _anchored(64)
    target = _list(concept, ['UP'], title_platform=['PS4'], is_regional=True)
    _list(concept, ['EP'], title_platform=['PS5'])
    assert _rows()[target.pk]['siblings'] == []


def test_a_locked_sibling_is_marked_and_shows_its_admin_label():
    concept = _anchored(65)
    target = _list(concept, ['UP'])
    locked = _list(concept, ['IP'], region=['JP'], region_lock=True)
    sibling = _rows()[target.pk]['siblings'][0]
    assert sibling['np_id'] == locked.np_communication_id
    assert sibling['locked'] is True and sibling['regions'] == ['JP']


def test_a_list_with_a_concept_but_no_sibling_lists_none():
    game = _list(_anchored(66), ['UP', 'EP'], is_regional=True)
    assert _rows()[game.pk]['siblings'] == []


def test_walk_size_counts_distinct_title_ids_on_lists():
    shared = _tid('PPSA88881_00', 'EP')
    GameFactory(title_ids=[shared, _tid('PPSA88882_00', 'IP')])
    GameFactory(title_ids=[shared, 'NOT_IN_TABLE_00'])
    _, stats = rs.scan()
    assert stats['title_ids_on_lists'] == 3
    assert stats['title_ids_with_region'] == 1


def test_walk_size_ignores_the_platform_filter():
    GameFactory(title_platform=['PS4'], title_ids=[_tid('CUSA88885_00', 'UP', 'PS4')])
    GameFactory(title_platform=['PS5'], title_ids=[_tid('PPSA88886_00', 'EP')])
    _, stats = rs.scan(platform='PS5')
    assert stats['scanned'] == 1
    assert stats['title_ids_on_lists'] == 2


def test_command_prints_siblings_capped_and_the_walk_size():
    concept = _anchored(61)
    _list(concept, ['UP'], title_name='Split Part', is_regional=True)
    for _ in range(5):
        _list(concept, ['UP'], is_regional=True)
    out = _run('--bucket', 'regional_stale')
    lines = out.splitlines()
    # A sample is three lines: the list, its detail, then its siblings.
    line = lines[next(i for i, text in enumerate(lines) if text.strip().startswith('Split Part')) + 2]
    assert line.strip().startswith('siblings:')
    assert line.count('(NPWR') == 4
    assert line.endswith('; +1 more')
    assert 'distinct title IDs sit on trophy lists (whole catalogue, ignores --platform)' in out


def test_command_marks_disjoint_and_admin_siblings():
    concept = _anchored(67)
    _list(concept, ['UP'], title_name='Marked Target')
    _list(concept, ['IP'], title_name='Locked Sib', region=['JP'], region_lock=True)
    out = _run('--bucket', 'regional_missed')
    lines = out.splitlines()
    line = lines[next(i for i, text in enumerate(lines) if text.strip().startswith('Marked Target')) + 2]
    assert '*Locked Sib (' in line and line.endswith('JP [admin]')
