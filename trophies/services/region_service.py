"""Trophy-list regions: where a title ID is sold, what a list's region label and regional flag should be,
and the read-only audit that measures the catalogue against those rules.

WHERE THE TRUTH IS (store probes, 2026-10-23). A title ID is a product SKU, and its regions are the
storefronts that sell that exact title ID. PSN's concept details answer for the storefront ASKED and
describe the whole concept, so the old sync read (the prefix of the US store's `defaultProduct.id`)
mostly just said "NA". Even an exact match's content-id prefix is not a region: it names the account
the product is published under, and worldwide products keep one prefix everywhere (Black Myth: Wukong
is `HP` in all five stores). So:

- `store_regions` (filled by the store walk) is the truth whenever a store sells the title ID.
- The content-id prefix (`TitleID.region`, trusted only from the andshrew dataset) is the fallback for
  a title ID no store sells any more, and the provisional LABEL until the walk reaches it.
- A list's region LABEL is the union of its title IDs' regions.

THE REGIONAL UNIT. Candidate siblings are lists of the same IGDB VERSION (raw `IGDBMatch.igdb_id`,
trusted matches only) on the same HARDWARE platform. Not the Concept: a Concept also requires a
matching trophy fingerprint, so a regional list with any trophy difference (Chinese Astro Bot) lives in
an `X-N` sibling Concept. Not the `X`/`X-N` canonical family either: it holds other versions (canonical
1009 holds The Last of Us, Remastered and Part I). Lists without a trusted match fall back to their
Concept, then to themselves. VR and PC tags are not platforms here: a PS4 list and a PS5 list of No
Man's Sky both carry `PSVR`, and grouping on it made them each other's siblings.

THE REGIONAL RULE (`regional_verdicts`), settled against the prod baseline. Being a candidate sibling
is NOT enough: Dead by Daylight splits one game's trophies across several lists, Fall Guys got a new
list at its free-to-play relaunch, Call of Duty HQ carries several PS5 lists, all from the same
regions. A list is regional when another product in its unit covers regions that do not overlap with
its own. Owner decisions: a list from Asian regions only is regional even with no sibling, and a list
that cannot be judged (it or a sibling has no region data) keeps whatever flag it has.

The verdict reads only VERIFIED regions: a title ID's store set once walked (or, for one no store
sells, its dataset prefix). An unwalked title ID's prefix is a guess the flag must not act on: by
prefix alone Wukong would read as an Asia-only list. So a list's flag only changes once the walk has
reached its title IDs, while its label improves straight away.

Everything that writes regions goes through `rebuild` (catalogue or a scoped set), so the nightly
rebuild, sync, the store walk and the audit cannot classify differently. The one exception is
`apply_script_region`, sync's fallback for a list PSN gave no details for.
"""
import json
import logging
from collections import defaultdict, namedtuple

from django.db.models import Q
from django.utils import timezone

from trophies.models import Concept, Game, IGDBMatch, TitleID
from trophies.util_modules.cache import redis_client
from trophies.util_modules.constants import (
    PLATFORM_PRIORITY_ORDER, PREFIX_REGIONS, REGIONS, STOREFRONTS,
)
from trophies.util_modules.language import detect_asian_language

logger = logging.getLogger('psn_api')

#: Regions served by the Asian storefronts. A list from these alone is regional by itself.
ASIAN_REGIONS = frozenset({'JP', 'AS', 'KR', 'CN'})

#: The hardware platforms siblings are grouped on. `PLATFORM_PRIORITY_ORDER` lists exactly the
#: consoles, without the PSVR/PSVR2/PSPC tags that ride along on a console list.
BASE_PLATFORMS = frozenset(PLATFORM_PRIORITY_ORDER)

_REGION_ORDER = {code: i for i, code in enumerate(REGIONS)}


def normalize_region(code):
    """A content-id prefix as the region code it stands for, or None when it names no region."""
    if not code:
        return None
    return PREFIX_REGIONS.get(str(code).strip().upper())


def ordered(regions):
    """Region codes in display order (`REGIONS`), the order `Game.region` is stored in."""
    return sorted(set(regions), key=lambda code: (_REGION_ORDER.get(code, len(_REGION_ORDER)), code))


# --- a title ID's regions ---------------------------------------------------

#: What one title ID says. `label` feeds a list's region label; `verdict` feeds the regional flag (see
#: the module docstring for why they differ); `walked` is whether every store has answered; `stores` is
#: what they said.
TitleRegions = namedtuple('TitleRegions', 'prefix label verdict walked stores')

_UNKNOWN = TitleRegions('', frozenset(), frozenset(), False, frozenset())


def _title_regions(prefix, source, store_regions, checked_at):
    trusted_prefix = frozenset(
        {normalize_region(prefix)} - {None} if source == TitleID.REGION_SOURCE_DATASET else ()
    )
    stores = frozenset(r for r in (store_regions or []) if r)
    walked = checked_at is not None
    if walked and stores:
        return TitleRegions(prefix, stores, stores, True, stores)
    if walked:
        # Sold nowhere now (delisted, or relisted under a newer ID): the prefix is the best evidence left.
        return TitleRegions(prefix, trusted_prefix, trusted_prefix, True, stores)
    return TitleRegions(prefix, trusted_prefix, frozenset(), False, stores)


def title_id_info(title_ids=None):
    """`{title_id: TitleRegions}`, for the given title IDs or every TitleID. One flat `values_list`."""
    qs = TitleID.objects.all()
    if title_ids is not None:
        qs = qs.filter(title_id__in=list(title_ids))
    return {
        tid: _title_regions(prefix, source, stores, checked)
        for tid, prefix, source, stores, checked in qs.values_list(
            'title_id', 'region', 'region_source', 'store_regions', 'store_checked_at')
    }


def label_regions(title_ids, info):
    """The region label a list's title IDs support."""
    return set().union(*(info.get(tid, _UNKNOWN).label for tid in (title_ids or [])))


def verdict_regions(title_ids, info):
    """The VERIFIED regions a list's title IDs support, the only ones the regional flag acts on."""
    return set().union(*(info.get(tid, _UNKNOWN).verdict for tid in (title_ids or [])))


# --- the regional unit ------------------------------------------------------

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
    regional versions of each other."""
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
    each list's VERIFIED regions, not its stored label: the stored label is what is being judged.

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
                    compared[pk][other] = (seen | theirs, was_disjoint or _disjoint(own, theirs))
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


# --- loading lists ----------------------------------------------------------

_LIST_FIELDS = (
    'pk', 'title_name', 'np_communication_id', 'title_platform', 'region', 'title_ids',
    'is_regional', 'region_lock', 'played_count', 'concept_id', 'concept__concept_id',
    'concept__igdb_match__igdb_id', 'concept__igdb_match__status',
)

#: One trophy list, as the rules read it. Built from a `values_list` tuple: never a model instance.
#: `stored` keeps the stored order, so an unchanged label is never rewritten just to reorder it.
#: `raw` is the stored JSON exactly as loaded (a legacy row can hold a bare string, blanks or repeats),
#: which the conditional write must match. `all_walked` is whether every title ID has a store answer.
ListRow = namedtuple('ListRow', 'pk name np_id platforms stored raw title_ids is_regional locked played '
                                'concept_id unit label verdict all_walked')


def _load(qs, info=None):
    """`(rows, groups, info)` for the lists in `qs`. `info` is loaded for exactly their title IDs when
    not passed. A locked list's `verdict` is its stored label: an admin set it, so it is the truth its
    siblings are judged against."""
    raw = list(qs.values_list(*_LIST_FIELDS))
    if info is None:
        info = title_id_info({tid for r in raw for tid in _as_list(r[5])})
    rows, groups = [], defaultdict(list)
    for (pk, name, np_id, plats, region, tids, is_regional, locked, played, concept_pk, concept_id,
         igdb_id, status) in raw:
        tids, plats, stored = _as_list(tids), _as_list(plats), tuple(_as_list(region))
        unit = unit_key(concept_pk, igdb_id, status)
        all_walked = bool(tids) and all(info.get(tid, _UNKNOWN).walked for tid in tids)
        row = ListRow(pk, name, np_id, plats, stored, region, tids, is_regional, locked, played or 0,
                      concept_id, unit, label_regions(tids, info),
                      set(stored) if locked else verdict_regions(tids, info), all_walked)
        rows.append(row)
        if unit is not None:
            for plat in sibling_platforms(plats):
                groups[(unit, plat)].append(pk)
    return rows, groups, info


def _verdicts_for(rows, groups):
    return regional_verdicts(groups, {r.pk: r.title_ids for r in rows}, {r.pk: r.verdict for r in rows})


# --- writing: the rebuild ---------------------------------------------------

def target_region(row):
    """The region label a list SHOULD store. Its title IDs' regions; when they name none, the script of
    its title (the old fallback for Asia-only lists PSN gave no details for).

    A list whose title IDs name no region keeps what it has until EVERY one of them is walked: with no
    title IDs, or with any still unwalked, "names no region" only means "not known yet". That is the
    label's version of "unjudged keeps its flag", and it is load-bearing: right after the migration no
    prefix is trusted until `populate_title_ids` re-runs, and without this every rebuild in that window
    would wipe the catalogue's labels. A stored NA that came from an `IP` title ID therefore stays until
    the walk answers for it."""
    if row.label:
        return ordered(row.label)
    if not row.all_walked:
        return list(row.stored)
    script = detect_asian_language(row.name or '')
    return [script] if script in REGIONS else []


def target_flag(row, verdict):
    """The `is_regional` a list SHOULD store: the verdict, except an unjudged list keeps its flag."""
    if verdict == REGIONAL:
        return True
    if verdict == NOT_REGIONAL:
        return False
    return row.is_regional


def rebuild(qs=None, dry_run=False):
    """Rewrite region labels and regional flags for the lists in `qs` (default: every list) from the
    rules above. Region-locked lists are never written. Returns change counts.

    Pass a whole unit at a time (`refresh_units` does): a verdict depends on every sibling, so a partial
    unit would judge a list against only some of them.
    """
    # The catalogue-wide load reads TitleID unfiltered rather than through a ~70k-id IN list.
    rows, groups, _info = (_load(Game.objects.all(), title_id_info()) if qs is None else _load(qs))
    verdicts, _compared = _verdicts_for(rows, groups)
    # {(raw stored JSON, new label): [pk, ...]}. Grouped so every write is CONDITIONAL on the exact value
    # this rebuild loaded and on the list still being unlocked: an admin lock, or a walk that rebuilt the
    # list since, wins over this rebuild's now-stale answer. Matched on the RAW value, because jsonb
    # equality is exact and a legacy row (a bare string, blanks, repeats) never equals its normalised
    # form; such a row is rewritten to a clean list once even when its regions did not change.
    region_changes, flag_on, flag_off = defaultdict(list), [], []
    for row in rows:
        if row.locked:
            continue
        region = target_region(row)
        if set(region) != set(row.stored) or row.raw != list(row.stored):
            new = region if set(region) != set(row.stored) else list(row.stored)
            region_changes[(json.dumps(row.raw), tuple(new))].append(row.pk)
        flag = target_flag(row, verdicts[row.pk][0])
        if flag != row.is_regional:
            (flag_on if flag else flag_off).append(row.pk)
    if dry_run:
        return {'lists': len(rows), 'regions': sum(len(pks) for pks in region_changes.values()),
                'flagged': len(flag_on), 'unflagged': len(flag_off)}
    # Counts are what the UPDATEs actually wrote, so a run that lost every row to a newer value or a lock
    # reports 0 rather than re-announcing the same planned changes every night.
    written = sum(
        Game.objects.filter(pk__in=pks, region_lock=False, region=json.loads(raw)).update(region=list(new))
        for (raw, new), pks in region_changes.items()
    )
    flagged = (Game.objects.filter(pk__in=flag_on, region_lock=False, is_regional=False)
               .update(is_regional=True) if flag_on else 0)
    unflagged = (Game.objects.filter(pk__in=flag_off, region_lock=False, is_regional=True)
                 .update(is_regional=False) if flag_off else 0)
    return {'lists': len(rows), 'regions': written, 'flagged': flagged, 'unflagged': unflagged}


def unit_queryset(game_pks):
    """Every list in the units the given lists belong to (plus the lists themselves), so `rebuild` can
    judge each against all of its siblings."""
    seeds = list(Game.objects.filter(pk__in=list(game_pks)).values_list(
        'pk', 'concept_id', 'concept__igdb_match__igdb_id', 'concept__igdb_match__status'))
    igdb_ids, concept_pks = set(), set()
    for _pk, concept_pk, igdb_id, status in seeds:
        unit = unit_key(concept_pk, igdb_id, status)
        if unit and unit.startswith('igdb:'):
            igdb_ids.add(igdb_id)
        elif unit:
            concept_pks.add(concept_pk)
    # Resolved to concept pks first: one ORed filter across the concept and IGDB-match joins would
    # scan Game, and this runs on every sync_title_id. Both lookups below are indexed.
    if igdb_ids:
        concept_pks |= set(Concept.objects.filter(
            igdb_match__igdb_id__in=igdb_ids, igdb_match__status__in=IGDBMatch.TRUSTED_STATUSES,
        ).values_list('pk', flat=True))
    q = Q(pk__in=[pk for pk, *_ in seeds])
    if concept_pks:
        q |= Q(concept_id__in=concept_pks)
    return Game.objects.filter(q)


def refresh_units(game_pks):
    """Rebuild the given lists and every list sharing a unit with them. The scoped write path for sync
    and the store walk; the nightly rebuild covers everything that moves lists between units."""
    if not game_pks:
        return None
    return rebuild(unit_queryset(game_pks))


def apply_script_region(game, code):
    """The concept-less fallback in `sync_title_id`: PSN gave no details and the title's script names an
    Asian region. Adds the region and flags the list, unless an admin locked it."""
    if game.region_lock or code not in REGIONS:
        return
    game.region = ordered(set(_as_list(game.region)) | {code})
    game.is_regional = True
    game.save(update_fields=['region', 'is_regional'])


# --- the store walk ---------------------------------------------------------

WALK_JOB = 'resolve_title_id_region'
REFILL_JOB = 'region_walk_refill'

#: Set while a title ID's lookup is queued, so a refill or a second sync does not queue it twice, and
#: kept after a FAILED lookup as a cooldown (the walk deletes it on success). A full day, because a
#: lookup can wait on the bulk queue for hours (a 500-ID batch is ~3,000 calls at the token rate) and a
#: lapsed key mid-wait gets it queued again. The nightly retries a failed one once its day is up.
QUEUED_KEY = 'region_walk:queued:{title_id}'
QUEUED_TTL_SECONDS = 24 * 3600

#: Held while a bulk walk chain is alive, so a second `resolve_title_regions` cannot start a second
#: chain. Refreshed by every refill AND by every walk job the chain queued, so a batch that takes hours
#: to drain keeps it; a sync-queued walk never touches it, so a chain that died (a worker crash, a DB
#: blip during its refill) frees it within the TTL and the nightly restarts it.
CHAIN_KEY = 'region_walk:chain'
CHAIN_TTL_SECONDS = 2 * 3600


def sells_title_id(details, title_id):
    """Whether one storefront's concept response sells THIS title ID: a product ID embeds the title ID
    (`EP9000-PPSA03208_00-...`), and only the stores selling it list a product carrying it. A store that
    does not know the title answers normally with no products, so absence is an answer, not an error."""
    details = details or {}
    ids = [(details.get('defaultProduct') or {}).get('id') or '']
    for category in details.get('categorizedProducts') or []:
        ids.extend(category.get('ids') or [])
    return any(title_id in (pid or '') for pid in ids)


def record_store_answers(title_id, answers):
    """Save what every storefront said about a title ID and rebuild the lists carrying it. `answers` is
    `{region: details}` and must hold EVERY storefront: a partial walk would record "not sold" for the
    stores it never asked. Returns the regions found."""
    missing = [region for _c, _l, region in STOREFRONTS if region not in answers]
    if missing:
        raise ValueError(f'store walk for {title_id} is missing {missing}')
    regions = ordered(region for _c, _l, region in STOREFRONTS if sells_title_id(answers[region], title_id))
    # update_or_create, not update: a row deleted between queue and answer would drop six calls' worth.
    answer = {'store_regions': regions, 'store_checked_at': timezone.now()}
    TitleID.objects.update_or_create(title_id=title_id, defaults=answer,
                                     create_defaults={**answer, 'platform': platform_for(title_id)})
    redis_client.delete(QUEUED_KEY.format(title_id=title_id))
    refresh_units(Game.objects.filter(title_ids__contains=[title_id]).values_list('pk', flat=True))
    return regions


def walk_title_id(title_id, fetch, chain=False):
    """The store walk for one title ID: `fetch(country, language)` returns that storefront's concept
    details, and every storefront in STOREFRONTS is asked before anything is recorded. All or nothing:
    if any call raises, it raises out of here and nothing is written, so a title ID is never recorded as
    "sold nowhere" because a store was not asked. Returns the regions found.

    `chain` marks a lookup the bulk chain queued: it keeps the chain lock alive while its batch drains
    (`expire` only, so it never re-creates a lock that was released)."""
    if chain:
        redis_client.expire(CHAIN_KEY, CHAIN_TTL_SECONDS)
    answers = {region: fetch(country, language) for country, language, region in STOREFRONTS}
    return record_store_answers(title_id, answers)


def platform_for(title_id, known=None):
    """The platform to ask the catalog about: the TitleID's when it is a console the catalog takes,
    else the ID's own prefix (CUSA is PS4). An auto-created TitleID copies the list's first platform,
    which can be a tag like PSVR."""
    if known in ('PS4', 'PS5'):
        return known
    return 'PS5' if title_id.upper().startswith('PPSA') else 'PS4'


def walk_candidates(limit=None):
    """Title IDs on lists that no store walk has answered for, most-played list first:
    `[(title_id, platform, np_communication_id), ...]`. Skips IDs whose lookup is already queued."""
    best = {}
    for tids, np_id, played in (Game.objects.exclude(title_ids=[])
                                .values_list('title_ids', 'np_communication_id', 'played_count')):
        for tid in _as_list(tids):
            if tid not in best or (played or 0) > best[tid][0]:
                best[tid] = (played or 0, np_id)
    known = dict(TitleID.objects.filter(store_checked_at__isnull=True)
                 .values_list('title_id', 'platform'))
    walked = set(TitleID.objects.filter(store_checked_at__isnull=False).values_list('title_id', flat=True))
    order = sorted((tid for tid in best if tid not in walked), key=lambda tid: (-best[tid][0], tid))
    picked = []
    for tid in order:
        if limit is not None and len(picked) >= limit:
            break
        if redis_client.exists(QUEUED_KEY.format(title_id=tid)):
            continue
        picked.append((tid, platform_for(tid, known.get(tid)), best[tid][1]))
    return picked


def walked_count():
    """How many title IDs any store walk has answered for. The refill chain's progress measure: unlike
    the unwalked count, sync adding new title IDs cannot make it look like the walk went backwards."""
    return TitleID.objects.filter(store_checked_at__isnull=False).count()


def unwalked_count():
    """How many title IDs on lists the walk has not answered for yet."""
    on_lists = {tid for tids in Game.objects.exclude(title_ids=[]).values_list('title_ids', flat=True)
                for tid in _as_list(tids)}
    walked = set(TitleID.objects.filter(store_checked_at__isnull=False).values_list('title_id', flat=True))
    return len(on_lists - walked)


def queue_lookup(title_id, platform, np_communication_id, queue='bulk_priority', chain=False):
    """Queue one title ID's store walk. Profile-less and UNCOUNTED on purpose: a counted job would make
    a hunter's `sync_complete` wait on store lookups. Ensures the TitleID row exists, since the walk
    writes onto it. Returns False when the lookup is already queued."""
    from trophies.psn_manager import PSNManager

    TitleID.objects.get_or_create(title_id=title_id, defaults={'platform': platform})
    key = QUEUED_KEY.format(title_id=title_id)
    if not redis_client.set(key, 1, nx=True, ex=QUEUED_TTL_SECONDS):
        return False
    try:
        PSNManager.assign_job(WALK_JOB, [title_id, platform, np_communication_id, chain], profile_id=None,
                              priority_override=queue, skip_counter=True)
    except Exception:
        # Nothing was queued, so nothing may hold the key: it would block this title ID for an hour.
        redis_client.delete(key)
        raise
    return True


def refill_walk(batch, last_walked=None):
    """One step of the bulk walk: queue the next `batch` unwalked title IDs, then a refill job BEHIND
    them (`lpush`, so it runs after they drain). Returns `(queued, remaining)`.

    The chain ends when:
    - the previous batch walked NOTHING (`walked_count` did not move): a PSN outage short-circuits every
      lookup, and the chain would otherwise spin. Logged as a warning;
    - no candidate is free to queue. Either the walk is done, or every one left is cooling down after a
      failed lookup (QUEUED_KEY); the second is logged as a warning, since re-running the command after
      the cooldown resumes it.
    A refill that dies itself (a DB blip) is not re-queued by the worker (profile-less), so the chain
    stops there too; CHAIN_KEY lapses and `resolve_title_regions` restarts it."""
    from trophies.psn_manager import PSNManager

    walked = walked_count()
    remaining = unwalked_count()
    if last_walked is not None and walked <= last_walked:
        redis_client.delete(CHAIN_KEY)
        logger.warning(f'region walk stopped: the last batch walked nothing ({remaining} unwalked); '
                       f're-run resolve_title_regions once PSN is healthy')
        return 0, remaining
    queued = sum(queue_lookup(tid, plat, np_id, queue='bulk_priority', chain=True)
                 for tid, plat, np_id in walk_candidates(batch))
    if not queued:
        redis_client.delete(CHAIN_KEY)
        if remaining:
            logger.warning(f'region walk paused: {remaining} unwalked, all cooling down after a failed '
                           f'lookup; the nightly resolve_title_regions retries them once their cooldown lapses')
        else:
            logger.info('region walk finished: every title ID on a list has been walked')
        return 0, remaining
    redis_client.set(CHAIN_KEY, 1, ex=CHAIN_TTL_SECONDS)
    PSNManager.assign_job(REFILL_JOB, [batch, walked], profile_id=None,
                          priority_override='bulk_priority', skip_counter=True)
    logger.info(f'region walk: queued {queued}, {remaining} unwalked')
    return queued, remaining


def walk_is_running():
    """Whether a bulk walk chain holds the chain lock."""
    return bool(redis_client.exists(CHAIN_KEY))


def start_walk(batch):
    """Start the bulk walk chain unless one is running. The lock is taken atomically (`nx`), so two
    starts at once cannot both win, and released if queueing the first refill fails. Returns whether
    this call started it."""
    from trophies.psn_manager import PSNManager

    if not redis_client.set(CHAIN_KEY, 1, nx=True, ex=CHAIN_TTL_SECONDS):
        return False
    try:
        PSNManager.assign_job(REFILL_JOB, [max(batch, 1), None], profile_id=None,
                              priority_override='bulk_priority', skip_counter=True)
    except Exception:
        redis_client.delete(CHAIN_KEY)
        raise
    return True


def after_title_id_synced(game, title_id):
    """Sync's region step for one title ID now on `game`: rebuild the list and its unit with what is
    known, and queue the store walk for a title ID it has not reached yet. On the BULK queue: six calls
    per title ID must never sit ahead of a hunter's own sync jobs (sync_title_id itself can run on bulk
    when `backfill_psn_concept_data` drains it). The chip fills in once the bulk queue reaches it."""
    refresh_units([game.pk])
    if title_id.store_checked_at is None:
        queue_lookup(title_id.title_id, platform_for(title_id.title_id, title_id.platform),
                     game.np_communication_id, queue='bulk_priority')


# --- the audit (read-only) --------------------------------------------------

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


def _sibling_rows(compared, by_pk):
    """A row's siblings for the read-out: disjoint ones first (they decide a `disjoint_sibling`
    verdict, so a capped list must not hide them), then those with no region data, then overlapping.
    `regions` are the PRODUCT regions the rule compared; `locked` marks an admin-set label standing in
    for title IDs."""
    def order(item):
        sibling, (regions, disjoint) = item
        return (0 if disjoint else 1 if not regions else 2, by_pk[sibling].np_id or '')

    return [
        {'name': by_pk[s].name, 'np_id': by_pk[s].np_id, 'platforms': by_pk[s].platforms,
         'regions': sorted(regions), 'disjoint': disjoint, 'locked': by_pk[s].locked}
        for s, (regions, disjoint) in sorted(compared.items(), key=order)
    ]


def scan(platform=None):
    """Classify every trophy list. Returns `(rows, stats)`. Read-only.

    `rows` holds ONLY the lists in at least one bucket, one dict each. `stats` holds the denominators:
    lists scanned, lists skipped for `region_lock`, per-platform region coverage, how many lists got
    each verdict, and the store walk's progress.

    Catalogue-wide rather than per-user, but it never builds a model instance: one `values_list` over
    Game read as tuples, and dicts only for the flagged lists. Siblings are grouped across the WHOLE
    catalogue before `platform` narrows the output, because a cross-buy list's sibling may sit on the
    platform that was filtered out.
    """
    info = title_id_info()
    lists, groups, _info = _load(Game.objects.all(), info)
    verdicts, compared = _verdicts_for(lists, groups)
    by_pk = {r.pk: r for r in lists}

    # Sizes the store walk. Catalogue-wide on purpose, the walk does not follow --platform.
    on_lists = {tid for r in lists for tid in r.title_ids}
    stats = {
        'scanned': 0, 'locked': 0, 'coverage': defaultdict(lambda: [0, 0]),
        'verdicts': defaultdict(int), 'unjudged_flagged': 0,
        'title_ids_on_lists': len(on_lists),
        'title_ids_walked': sum(1 for tid in on_lists if info.get(tid, _UNKNOWN).walked),
        'title_ids_with_region': sum(1 for tid in on_lists if info.get(tid, _UNKNOWN).label),
    }
    rows = []
    for r in lists:
        if platform and platform not in r.platforms:
            continue
        stats['scanned'] += 1
        stored = set(r.stored)
        for plat in ([platform] if platform else r.platforms):
            stats['coverage'][plat][0] += 1
            stats['coverage'][plat][1] += bool(stored)
        if r.locked:
            # An admin set this list's region by hand: that is the truth the rules are measured
            # against, not something to report.
            stats['locked'] += 1
            continue

        verdict, reason = verdicts[r.pk]
        stats['verdicts'][verdict] += 1
        if verdict == UNJUDGED and r.is_regional:
            stats['unjudged_flagged'] += 1

        expected = r.label
        raw = [(tid, info.get(tid, _UNKNOWN)) for tid in r.title_ids]
        buckets = []
        if expected and stored - expected:
            buckets.append(WRONG_REGION)
        elif expected - stored:
            buckets.append(MISSING_REGION)
        if 'JP' in stored and 'JP' not in expected and any(
                (t.prefix or '').strip().upper() == 'KP' for _tid, t in raw):
            buckets.append(KOREA_AS_JP)
        # NA with title IDs that name no region at all (`IP` or missing). A list with no title IDs
        # gives no evidence either way, and NA beside a KNOWN non-NA title ID is the storefront bug,
        # which wrong_region already counts.
        if 'NA' in stored and r.title_ids and not expected:
            buckets.append(NA_FROM_UNKNOWN)
        if verdict == REGIONAL and not r.is_regional:
            buckets.append(REGIONAL_MISSED)
        if verdict == NOT_REGIONAL and r.is_regional:
            buckets.append(REGIONAL_STALE)
        if r.is_regional and not stored:
            buckets.append(REGIONAL_NO_REGION)
        if buckets:
            rows.append({
                'pk': r.pk, 'name': r.name, 'np_id': r.np_id, 'platforms': r.platforms,
                'stored': sorted(stored), 'expected': sorted(expected),
                'title_ids': [(tid, t.prefix or None, sorted(t.stores) if t.walked else None)
                              for tid, t in raw],
                'is_regional': r.is_regional, 'played': r.played, 'concept_id': r.concept_id,
                'unit': r.unit, 'buckets': buckets, 'verdict': verdict, 'reason': reason,
                'siblings': _sibling_rows(compared.get(r.pk, {}), by_pk),
                # Chips render only for a regional list with a stored region, so this is the
                # difference between a wrong label hunters SEE and one sitting unread in the column.
                'shown': bool(r.is_regional and stored),
            })

    stats['coverage'] = dict(stats['coverage'])
    stats['verdicts'] = dict(stats['verdicts'])
    return rows, stats
