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

THE REGIONAL UNIT. Candidate siblings are lists of the same IGDB VERSION (raw `IGDBMatch.igdb_id`,
trusted matches only) on the same HARDWARE platform. Not the Concept: a Concept also requires a
matching trophy fingerprint, so a regional list with any trophy difference (Chinese Astro Bot) lives in
an `X-N` sibling Concept. Not the `X`/`X-N` canonical family either: it holds other versions (canonical
1009 holds The Last of Us, Remastered and Part I). Lists without a trusted match fall back to their
Concept, then to themselves. VR and PC tags are not platforms here: a PS4 list and a PS5 list of No
Man's Sky both carry `PSVR`, and grouping on it made them each other's siblings (2026-10-23 baseline).

THE REGIONAL RULE (`regional_verdicts`), settled against the prod baseline (2026-10-23). Being a
candidate sibling is NOT enough: Dead by Daylight splits one game's trophies across several lists,
Fall Guys got a new list at its free-to-play relaunch, and Call of Duty HQ carries several PS5 lists,
all from the same regions. What separates a regional split is the REGIONS: a list is regional when
another product in its unit covers regions that do not overlap with its own (Astro Bot's AS/EU/JP/NA
list beside its CN list). Owner decisions: a list from Asian regions only is regional even with no
sibling (it is regional to that region), and a list that cannot be judged because it or a sibling has
no region data keeps whatever flag it has today.

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
from trophies.util_modules.constants import PLATFORM_PRIORITY_ORDER

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

#: Regions served by the Asian storefronts. A list from these alone is regional by itself.
ASIAN_REGIONS = frozenset({'JP', 'AS', 'KR', 'CN'})

#: The hardware platforms siblings are grouped on. `PLATFORM_PRIORITY_ORDER` lists exactly the
#: consoles, without the PSVR/PSVR2/PSPC tags that ride along on a console list.
BASE_PLATFORMS = frozenset(PLATFORM_PRIORITY_ORDER)


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


def sibling_platforms(platforms):
    """The platforms a list is grouped on: its consoles, or its tags when it has no console."""
    base = [p for p in platforms if p in BASE_PLATFORMS]
    return base or list(platforms)


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


# --- the regional verdict ---------------------------------------------------

REGIONAL = 'regional'
NOT_REGIONAL = 'not_regional'
UNJUDGED = 'unjudged'

#: Why a verdict came out the way it did. Printed beside each row and tallied in the summary.
DISJOINT_SIBLING = 'disjoint_sibling'
ASIA_ONLY = 'asia_only'
OVERLAPPING_SIBLINGS = 'overlapping_siblings'
GLOBAL = 'global'
NO_REGION_DATA = 'no_region_data'
SIBLING_WITHOUT_REGION_DATA = 'sibling_without_region_data'


def _disjoint(own, theirs):
    """Two products are regional versions of each other when both name regions and share none."""
    return bool(own and theirs and not (own & theirs))


def regional_verdicts(groups, tids_by_pk, regions_by_pk):
    """`(verdicts, compared)` for every list, by THE REGIONAL RULE (module docstring).

    `verdicts` is `{pk: (verdict, reason)}`. `compared` is `{pk: {sibling pk: (regions, disjoint)}}`:
    exactly the lists each verdict was weighed against, with the PRODUCT regions the rule used and
    whether that sibling was disjoint. Returned from here rather than rebuilt by a caller, so what the
    report prints as a verdict's evidence cannot drift from what decided it.

    `groups` maps `(unit, platform)` to the list pks in it; a list on several platforms sits in
    several groups and is regional if ANY of them gives it a disjoint sibling. `regions_by_pk` holds
    what each list's title IDs say, not its stored label: the stored label is what is being judged.

    A list is judged by its PRODUCT's regions (every list sharing a title ID with it), so the lists of
    one storefront product cannot come out with opposite verdicts.
    """
    disjoint, unknown_sibling, overlap = set(), set(), set()
    product_regions = {pk: set(regions) for pk, regions in regions_by_pk.items()}
    compared = defaultdict(dict)
    for pks in groups.values():
        if len(pks) < 2:
            continue
        product = _products(pks, tids_by_pk)
        regions = defaultdict(set)
        for pk in pks:
            regions[product[pk]] |= regions_by_pk[pk]
        for pk in pks:
            product_regions[pk] |= regions[product[pk]]
        for pk in pks:
            own = regions[product[pk]]
            others = [r for root, r in regions.items() if root != product[pk]]
            if not others:
                continue
            for other in pks:
                if product[other] != product[pk]:
                    theirs = regions[product[other]]
                    seen, was_disjoint = compared[pk].get(other, (frozenset(), False))
                    compared[pk][other] = (seen | theirs,
                                           was_disjoint or _disjoint(own, theirs))
            if any(_disjoint(own, r) for r in others):
                disjoint.add(pk)
            elif any(not r for r in others):
                unknown_sibling.add(pk)
            else:
                overlap.add(pk)

    verdicts = {}
    for pk, own in product_regions.items():
        if pk in disjoint:
            verdicts[pk] = (REGIONAL, DISJOINT_SIBLING)
        elif not own:
            verdicts[pk] = (UNJUDGED, NO_REGION_DATA)
        # Ahead of the unknown-sibling check: an Asia-only list is regional on its own data, whatever
        # an unknown sibling turns out to be.
        elif own <= ASIAN_REGIONS:
            verdicts[pk] = (REGIONAL, ASIA_ONLY)
        elif pk in unknown_sibling:
            verdicts[pk] = (UNJUDGED, SIBLING_WITHOUT_REGION_DATA)
        elif pk in overlap:
            verdicts[pk] = (NOT_REGIONAL, OVERLAPPING_SIBLINGS)
        else:
            verdicts[pk] = (NOT_REGIONAL, GLOBAL)
    return verdicts, compared


# --- buckets ----------------------------------------------------------------

WRONG_REGION = 'wrong_region'
MISSING_REGION = 'missing_region'
KOREA_AS_JP = 'korea_as_jp'
NA_FROM_UNKNOWN = 'na_from_unknown'
REGIONAL_MISSED = 'regional_missed'
REGIONAL_STALE = 'regional_stale'
REGIONAL_NO_REGION = 'regional_no_region'

#: Display order: wrong labels first, then the regional flag.
BUCKETS = [
    WRONG_REGION, MISSING_REGION, KOREA_AS_JP, NA_FROM_UNKNOWN,
    REGIONAL_MISSED, REGIONAL_STALE, REGIONAL_NO_REGION,
]

BUCKET_LABELS = {
    WRONG_REGION: "stored region includes one the list's title IDs do not support",
    MISSING_REGION: "stored region lacks one the list's title IDs support (and has nothing wrong)",
    KOREA_AS_JP: 'has a Korean (KP) title ID, no Japanese one, and is labeled JP',
    NA_FROM_UNKNOWN: 'labeled NA, but none of its title IDs has a known region',
    REGIONAL_MISSED: 'regional by the rule, but not flagged regional',
    REGIONAL_STALE: 'flagged regional, but not regional by the rule',
    REGIONAL_NO_REGION: 'flagged regional with no region stored, so it shows no chip',
}


def _sibling_rows(compared, label_by_pk, locked_pks):
    """A row's siblings for the read-out: disjoint ones first (they decide a `disjoint_sibling`
    verdict, so a capped list must not hide them), then those with no region data, then overlapping.
    `regions` are the PRODUCT regions the rule compared; `locked` marks an admin-set label standing in
    for title IDs."""
    def order(item):
        sibling, (regions, disjoint) = item
        return (0 if disjoint else 1 if not regions else 2, label_by_pk[sibling][1] or '')

    return [
        {'name': label_by_pk[s][0], 'np_id': label_by_pk[s][1], 'platforms': label_by_pk[s][2],
         'regions': sorted(regions), 'disjoint': disjoint, 'locked': s in locked_pks}
        for s, (regions, disjoint) in sorted(compared.items(), key=order)
    ]


def scan(platform=None):
    """Classify every trophy list. Returns `(rows, stats)`.

    `rows` holds ONLY the lists in at least one bucket, one dict each. `stats` holds the denominators:
    lists scanned, lists skipped for `region_lock`, per-platform region coverage, and how many lists
    got each verdict.

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

    groups = defaultdict(list)
    stored_by_pk, tids_by_pk, regions_by_pk, label_by_pk = {}, {}, {}, {}
    locked_pks = set()
    for (pk, name, np_id, plats, region, tids, _reg, locked, _played, concept_pk, _cid,
         igdb_id, status) in lists:
        label_by_pk[pk] = (name, np_id, _as_list(plats))
        if locked:
            locked_pks.add(pk)
        stored_by_pk[pk] = frozenset(_as_list(region))
        tids_by_pk[pk] = _as_list(tids)
        # A locked list's region was set by an admin, so it is the truth its siblings are judged
        # against. Everyone else is judged by what their title IDs say.
        regions_by_pk[pk] = (set(stored_by_pk[pk]) if locked
                             else expected_regions(tids_by_pk[pk], tid_map))
        unit = unit_key(concept_pk, igdb_id, status)
        if unit is None:
            continue
        for plat in sibling_platforms(_as_list(plats)):
            groups[(unit, plat)].append(pk)
    verdicts, compared = regional_verdicts(groups, tids_by_pk, regions_by_pk)

    # Sizes the store walk: every distinct title ID that sits on a list, and how many the TitleID
    # table has a region for. Catalogue-wide on purpose, the walk does not follow --platform.
    on_lists = {tid for tids in tids_by_pk.values() for tid in tids}
    stats = {
        'scanned': 0, 'locked': 0, 'coverage': defaultdict(lambda: [0, 0]),
        'verdicts': defaultdict(int), 'unjudged_flagged': 0,
        'title_ids_on_lists': len(on_lists),
        'title_ids_with_region': sum(1 for tid in on_lists if normalize_region(tid_map.get(tid))),
    }
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

        verdict, reason = verdicts[pk]
        stats['verdicts'][verdict] += 1
        if verdict == UNJUDGED and is_regional:
            stats['unjudged_flagged'] += 1

        tids = tids_by_pk[pk]
        expected = regions_by_pk[pk]
        raw = [(tid, tid_map.get(tid)) for tid in tids]
        buckets = []
        if expected and stored - expected:
            buckets.append(WRONG_REGION)
        elif expected - stored:
            buckets.append(MISSING_REGION)
        if 'JP' in stored and 'JP' not in expected and any(
                (code or '').strip().upper() == 'KP' for _tid, code in raw):
            buckets.append(KOREA_AS_JP)
        # Problem 3 only: NA with title IDs that name no region at all (`IP` or missing). A list with
        # no title IDs gives no evidence either way, and NA beside a KNOWN non-NA title ID is the
        # storefront bug, which wrong_region already counts.
        if 'NA' in stored and tids and not expected:
            buckets.append(NA_FROM_UNKNOWN)
        if verdict == REGIONAL and not is_regional:
            buckets.append(REGIONAL_MISSED)
        if verdict == NOT_REGIONAL and is_regional:
            buckets.append(REGIONAL_STALE)
        if is_regional and not stored:
            buckets.append(REGIONAL_NO_REGION)
        if buckets:
            rows.append({
                'pk': pk, 'name': name, 'np_id': np_id, 'platforms': plats,
                'stored': sorted(stored), 'expected': sorted(expected), 'title_ids': raw,
                'is_regional': is_regional, 'played': played or 0, 'concept_id': concept_id,
                'unit': unit_key(concept_pk, igdb_id, status), 'buckets': buckets,
                'verdict': verdict, 'reason': reason,
                'siblings': _sibling_rows(compared.get(pk, {}), label_by_pk, locked_pks),
                # Chips render only for a regional list with a stored region, so this is the
                # difference between a wrong label hunters SEE and one sitting unread in the column.
                'shown': bool(is_regional and stored),
            })

    stats['coverage'] = dict(stats['coverage'])
    stats['verdicts'] = dict(stats['verdicts'])
    return rows, stats
