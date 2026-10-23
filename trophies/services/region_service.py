"""Trophy-list regions: the corrected region rules, and the read-only audit that measures how far
the stored regions have drifted from them.

How regions get stored today (`sync_title_id`): each PS4/PS5 title ID a hunter has played is linked to
its trophy list, PSN is asked for that title's store details, and the 2-letter prefix of the product
id it returns is appended to `Game.region`. A live probe (2026-10-23) showed that the answer names the
storefront we ASKED, not the title ID: asking the US store about the EU, JP and HK Ghost of Tsushima
title IDs returns a `UP` product every time. So every list the US store carries picks up NA, which
then outranks the andshrew dataset's correct per-title-ID region. Two table errors compound it: `KP`
(Korea) is filed under JP, and `IP` (our "unknown" placeholder, and Sony's own worldwide prefix) is
filed under NA. Regions and `is_regional` are also append-only, so none of this ever clears.

READ-ONLY. This module only measures. The rules live here, not in the command, so the rebuild that
follows classifies exactly the way the report that sized it did (the `job_drift` precedent).

THE REGIONAL UNIT. Lists are regional versions of each other when they belong to the same IGDB
VERSION (raw `IGDBMatch.igdb_id`, trusted matches only) on the same platform. Not the Concept: a
Concept also requires a matching trophy fingerprint, so a regional list with any trophy difference
(Chinese Astro Bot) lives in an `X-N` sibling Concept. Not the `X`/`X-N` canonical family either: it
holds other versions (canonical 1009 holds The Last of Us, Remastered and Part I). Lists without a
trusted match fall back to their Concept, then to themselves.

KNOWN BLIND SPOT. "What the title IDs say" reads `TitleID.region`, and sync overwrites every row it
finds holding `IP` with whatever the US store answered. That covers title IDs missing from the dataset
(auto-created as `IP`) AND the ~9.7k dataset rows that have no region (`populate_title_ids` stores
those as `IP` too). Those rows are wrong in the same way the lists are, so lists built only from them
agree with their title IDs here and are not flagged. A `populate_title_ids` run resets the dataset
rows (not the others), so the blind spot's size depends on when it last ran. Fixing it needs
provenance on TitleID, which the rebuild adds.
"""
from collections import defaultdict

from trophies.models import Game, IGDBMatch, TitleID

# --- the corrected region table ---------------------------------------------

#: 2-letter PSN content-id prefix -> the region code `Game.region` stores. Differs from the
#: `*_REGION_CODES` lists in constants.py in exactly two places, both verified against the andshrew
#: dataset: `KP` is Korea (its rows carry Hangul names), and `IP` names no region at all (Sony's
#: worldwide/system titles, and our own placeholder for "not known yet"). The report has to describe
#: what SHOULD be stored, so it cannot reuse the table that produced the errors; the rebuild moves
#: this into constants.py and points sync at it.
PREFIX_REGIONS = {
    'UP': 'NA', 'UB': 'NA', 'US': 'NA', 'UT': 'NA',
    'EP': 'EU', 'EB': 'EU',
    'JP': 'JP', 'JA': 'JP', 'JB': 'JP',
    'HP': 'AS', 'HA': 'AS', 'HB': 'AS', 'HT': 'AS',
    'KP': 'KR', 'KR': 'KR',
    'CN': 'CN',
}

#: Regions served by the Asian storefronts, used to describe a lone regional list.
ASIAN_REGIONS = frozenset({'JP', 'AS', 'KR', 'CN'})


def normalize_region(code):
    """A content-id prefix as the region code it stands for, or None when it names no region."""
    if not code:
        return None
    return PREFIX_REGIONS.get(str(code).strip().upper())


def title_id_region_map():
    """`{title_id: raw region prefix}` for every TitleID that has one. One flat query of short strings
    (the dataset is ~71k rows), the same shape `backfill_game_regions` already loads."""
    return dict(TitleID.objects.exclude(region='').values_list('title_id', 'region'))


def expected_regions(title_ids, tid_map):
    """The regions a list's title IDs support, by the corrected table."""
    found = (normalize_region(tid_map.get(tid)) for tid in (title_ids or []))
    return {r for r in found if r}


def unit_key(concept_pk, igdb_id, match_status):
    """The regional unit a list belongs to (see the module docstring)."""
    if igdb_id is not None and match_status in IGDBMatch.TRUSTED_STATUSES:
        return f'igdb:{igdb_id}'
    if concept_pk is not None:
        return f'concept:{concept_pk}'
    return None


# --- buckets ----------------------------------------------------------------

DATASET_DISAGREES = 'dataset_disagrees'
KOREA_AS_JP = 'korea_as_jp'
NA_FROM_UNKNOWN = 'na_from_unknown'
REGIONAL_MISSED = 'regional_missed'
SAME_REGION_SIBLINGS = 'same_region_siblings'
REGIONAL_STALE = 'regional_stale'
REGIONAL_NO_REGION = 'regional_no_region'

#: Display order: wrong labels first, then the regional flag.
BUCKETS = [
    DATASET_DISAGREES, KOREA_AS_JP, NA_FROM_UNKNOWN,
    REGIONAL_MISSED, SAME_REGION_SIBLINGS, REGIONAL_STALE, REGIONAL_NO_REGION,
]

BUCKET_LABELS = {
    DATASET_DISAGREES: "stored region differs from what the list's title IDs say",
    KOREA_AS_JP: 'has a Korean (KP) title ID, no Japanese one, and is labeled JP',
    NA_FROM_UNKNOWN: 'labeled NA, but none of its title IDs has a known region',
    REGIONAL_MISSED: 'has another list of the same game on its platform (a different product), but is not flagged regional',
    SAME_REGION_SIBLINGS: 'shares an identical region label with another list of the same game on its platform',
    REGIONAL_STALE: 'flagged regional, but no other product of its game shares its platform',
    REGIONAL_NO_REGION: 'flagged regional with no region stored, so it shows no chip',
}


def _as_list(raw):
    """A JSON list field as a de-duplicated list, whatever shape a legacy row holds. A bare string is
    one value (iterating `'NA'` would give `{'N', 'A'}`), and a repeated platform must not count a list
    twice in the coverage table."""
    if isinstance(raw, list):
        return list(dict.fromkeys(v for v in raw if v))
    return [raw] if raw else []


def _products(pks, tids_by_pk):
    """Split one (unit, platform) group into PRODUCTS: `{pk: product root}`. Lists that share a title ID
    are one product carrying two trophy lists, so they come from the same storefront and are not
    regional versions of each other. (The one thing `count_unique_game_groups` got right.)"""
    parent = {pk: pk for pk in pks}

    def find(pk):
        while parent[pk] != pk:
            parent[pk] = parent[parent[pk]]
            pk = parent[pk]
        return pk

    owner = {}
    for pk in pks:
        for tid in tids_by_pk[pk]:
            if tid in owner:
                parent[find(pk)] = find(owner[tid])
            else:
                owner[tid] = pk
    return {pk: find(pk) for pk in pks}


def scan(platform=None):
    """Classify every trophy list. Returns `(rows, stats)`.

    `rows` holds ONLY the lists in at least one bucket, one dict each. `stats` holds the denominators:
    lists scanned, lists skipped for `region_lock`, and per-platform region coverage.

    Catalogue-wide rather than per-user, but it never builds a model instance: one `values_list` over
    Game read as tuples (held for two passes, so the whole catalogue is in memory at once as plain
    tuples), and dicts only for the flagged lists. Siblings are grouped across the WHOLE catalogue
    before `platform` narrows the output, because a cross-buy list's sibling may sit on the platform
    that was filtered out.
    """
    tid_map = title_id_region_map()
    lists = list(
        Game.objects.values_list(
            'pk', 'title_name', 'np_communication_id', 'title_platform', 'region', 'title_ids',
            'is_regional', 'region_lock', 'played_count', 'concept_id', 'concept__concept_id',
            'concept__igdb_match__igdb_id', 'concept__igdb_match__status',
        )
    )

    # (unit, platform) -> [pk, ...]; plus each list's stored label and title IDs for the checks below.
    groups = defaultdict(list)
    stored_by_pk = {}
    tids_by_pk = {}
    for (pk, _name, _np, plats, region, tids, _reg, _lock, _played, concept_pk, _cid,
         igdb_id, status) in lists:
        stored_by_pk[pk] = frozenset(_as_list(region))
        tids_by_pk[pk] = _as_list(tids)
        unit = unit_key(concept_pk, igdb_id, status)
        if unit is None:
            continue
        for plat in _as_list(plats):
            groups[(unit, plat)].append(pk)

    has_sibling = set()
    same_label = set()
    for pks in groups.values():
        if len(pks) < 2:
            continue
        product = _products(pks, tids_by_pk)
        if len(set(product.values())) < 2:
            continue
        has_sibling.update(pks)
        # label -> the PRODUCTS wearing it. Two lists of one product share a label by construction,
        # so only a label worn by two different products is a duplicate.
        by_label = defaultdict(set)
        for pk in pks:
            if stored_by_pk[pk]:
                by_label[stored_by_pk[pk]].add(product[pk])
        for pk in pks:
            if len(by_label.get(stored_by_pk[pk], ())) > 1:
                same_label.add(pk)

    stats = {'scanned': 0, 'locked': 0, 'coverage': defaultdict(lambda: [0, 0])}
    rows = []
    for (pk, name, np_id, plats, _region, _tids, is_regional, locked, played, concept_pk, concept_id,
         igdb_id, status) in lists:
        plats = _as_list(plats)
        if platform and platform not in plats:
            continue
        stats['scanned'] += 1
        stored = set(stored_by_pk[pk])
        for plat in ([platform] if platform else plats):
            stats['coverage'][plat][0] += 1
            stats['coverage'][plat][1] += bool(stored)
        if locked:
            # An admin set this list's region by hand: that is the truth the rules are measured
            # against, not something to report.
            stats['locked'] += 1
            continue

        tids = tids_by_pk[pk]
        expected = expected_regions(tids, tid_map)
        raw = [(tid, tid_map.get(tid)) for tid in tids]
        buckets = []
        if expected and stored != expected:
            buckets.append(DATASET_DISAGREES)
        if 'JP' in stored and 'JP' not in expected and any(
                (code or '').strip().upper() == 'KP' for _tid, code in raw):
            buckets.append(KOREA_AS_JP)
        # Problem 3 only: NA with title IDs that name no region at all (`IP` or missing). A list with
        # no title IDs gives no evidence either way, and NA beside a KNOWN non-NA title ID is the
        # storefront bug, which dataset_disagrees already counts.
        if 'NA' in stored and tids and not expected:
            buckets.append(NA_FROM_UNKNOWN)
        if pk in has_sibling and not is_regional:
            buckets.append(REGIONAL_MISSED)
        if pk in same_label:
            buckets.append(SAME_REGION_SIBLINGS)
        if is_regional and pk not in has_sibling:
            buckets.append(REGIONAL_STALE)
        if is_regional and not stored:
            buckets.append(REGIONAL_NO_REGION)
        if buckets:
            rows.append({
                'pk': pk, 'name': name, 'np_id': np_id, 'platforms': plats,
                'stored': sorted(stored), 'expected': sorted(expected), 'title_ids': raw,
                'is_regional': is_regional, 'played': played or 0, 'concept_id': concept_id,
                'unit': unit_key(concept_pk, igdb_id, status), 'buckets': buckets,
                # A lone regional list whose labels are all Asian is the one REGIONAL_STALE case
                # that may be intended (a JP-only release). Counted apart so the rebuild's rule for
                # lone lists can be decided from data rather than guessed.
                'asian_only': bool(stored) and stored <= ASIAN_REGIONS,
            })

    stats['coverage'] = dict(stats['coverage'])
    return rows, stats
