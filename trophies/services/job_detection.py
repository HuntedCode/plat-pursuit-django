"""Genre/theme -> Job detection: the single source for job suggestions.

Maps a game's pooled IGDB genres/themes to job slugs (matching the seeded Job
catalog). Used to SUGGEST jobs for a Contract (staff confirm/trim) and by the
report_job_assignment analysis command. See docs/design/rebuild/job-board-contracts.md.
"""
from trophies.util_modules.constants import MAX_CONTRACT_JOBS

# (slug, genres, themes, override_slug). Match rules:
#   genres only  -> the game has ANY of these genres
#   themes only  -> the game has ANY of these themes
#   both (combo) -> a genre AND a theme; matching removes `override_slug` (its base job)
JOB_RULES = [
    ('champion',    ['Role-playing (RPG)'], [], None),
    ('gunslinger',  ['Shooter'], [], None),
    ('pathfinder',  ['Platform'], [], None),
    ('slayer',      ["Hack and slash/Beat 'em up"], [], None),
    ('mastermind',  ['Puzzle'], [], None),
    ('tactician',   ['Strategy', 'Turn-based strategy (TBS)', 'Tactical', 'Real Time Strategy (RTS)', 'MOBA'], [], None),
    ('tycoon',      ['Simulator'], [], None),
    ('gamer',       ['Arcade'], [], None),
    ('driver',      ['Racing'], [], None),
    ('warrior',     ['Fighting'], [], None),
    ('librarian',   ['Visual Novel', 'Point-and-click'], [], None),
    ('athlete',     ['Sport'], [], None),
    ('maestro',     ['Music'], [], None),
    ('card-shark',  ['Card & Board Game'], [], None),
    ('infiltrator', [], ['Stealth'], None),
    ('survivalist', [], ['Survival'], None),
    ('architect',   [], ['Sandbox'], None),
    ('exorcist',    [], ['Horror'], None),
    # Combos: need genre AND theme; override (remove) their base genre job.
    ('mage',        ['Role-playing (RPG)'], ['Fantasy'], 'champion'),
    ('vanguard',    ['Shooter'], ['Science fiction'], 'gunslinger'),
]

# Open-world and Comedy PARTITION on a paired genre -- a game gets exactly one side.
COMBAT_GENRES = {'Shooter', "Hack and slash/Beat 'em up", 'Fighting'}
FALLBACK_SLUG = 'freelancer'

# combo slug -> the base genre job it REPLACES (mage -> champion, vanguard -> gunslinger).
# Public because drift analysis has to tell a combo flip (a game gaining the Fantasy theme
# moves XP champion -> mage) apart from an unrelated add + remove. Kept separate from
# `_COMBO_SLUGS` below on purpose: that set asks "is this rule a genre+theme combo", this map
# asks "what does it override" -- the same two rules answer both today, but a future combo
# could carry no override and the two questions would part ways.
COMBO_OVERRIDES = {slug: override for slug, _g, _t, override in JOB_RULES if override}

# All 25 slugs (24 specializations + fallback), in catalog order.
CATALOG_ORDER = [slug for slug, *_ in JOB_RULES] + ['outlaw', 'cartographer', 'mascot', 'jester', FALLBACK_SLUG]

# Signal-strength tiers for trimming to MAX_CONTRACT_JOBS: combos (genre+theme, most specific)
# > genre jobs (core gameplay) > theme/partition jobs (flavor); catalog order breaks ties.
_COMBO_SLUGS = frozenset(slug for slug, g, t, _ in JOB_RULES if g and t)
_GENRE_SLUGS = frozenset(slug for slug, g, t, _ in JOB_RULES if g and not t)
_THEME_SLUGS = (frozenset(slug for slug, g, t, _ in JOB_RULES if t and not g)
                | frozenset({'outlaw', 'cartographer', 'mascot', 'jester'}))  # open-world/comedy partitions
_CATALOG_INDEX = {slug: i for i, slug in enumerate(CATALOG_ORDER)}


def _job_tier(slug):
    if slug in _COMBO_SLUGS:
        return 0
    if slug in _GENRE_SLUGS:
        return 1
    if slug in _THEME_SLUGS:
        return 2
    return 3   # freelancer / anything unlisted


def top_jobs(slugs, limit=MAX_CONTRACT_JOBS):
    """Rank job slugs by signal strength (combo > genre > theme/partition; catalog order breaks
    ties) and keep the strongest `limit`. Returns a list, strongest first."""
    ranked = sorted(slugs, key=lambda s: (_job_tier(s), _CATALOG_INDEX.get(s, 999)))
    return ranked[:limit]


def assign_job_slugs(genres, themes):
    """Return the set of job slugs a game qualifies for. Combos override their base
    genre job; Open-world -> Outlaw|Cartographer and Comedy -> Mascot|Jester partition;
    Freelancer is the fallback when nothing else matches."""
    genres, themes = set(genres), set(themes)
    matched = set()
    for slug, g, t, _ in JOB_RULES:
        if g and t:                                  # combo
            if (genres & set(g)) and (themes & set(t)):
                matched.add(slug)
        elif g:                                      # genre job
            if genres & set(g):
                matched.add(slug)
        elif t:                                      # theme job
            if themes & set(t):
                matched.add(slug)
    for slug, _, _, override in JOB_RULES:
        if override and slug in matched:
            matched.discard(override)

    if 'Open world' in themes:
        matched.add('outlaw' if genres & COMBAT_GENRES else 'cartographer')
    if 'Comedy' in themes:
        matched.add('mascot' if 'Platform' in genres else 'jester')

    if not matched:
        matched.add(FALLBACK_SLUG)
    return matched


def pool_tags(concept_ids):
    """(genres_by_concept, themes_by_concept) for the given concepts, as {id: set(names)}.

    Two queries however many concepts are asked for (zero for an empty list), which is what lets
    every caller that needs per-concept tags stay flat in catalogue size.

    KEYS ARE THE DB's OWN `concept_id` VALUES, i.e. ints. Django coerces a string id for the
    `__in` filter but returns the int, so `pool_tags(['5'])` yields `{5: ...}` and a subsequent
    `flatten_tags(['5'], ...)` silently finds nothing. Pass ids straight from `values_list`/`pk`.
    The flat-set version this replaced had no such coupling, so it is new surface area.

    Returns plain dicts rather than defaultdicts so that a `d[cid]` miss raises instead of
    minting an empty entry; `.get(cid, set())` is the intended access and behaves identically
    either way.

    Extracted 2026-09 from three near-identical copies (`suggest_job_slugs`,
    `simulate_stage_jobs`, and the job-drift scanner, which was about to be the third). They had
    already begun to differ in shape -- flat sets here, per-concept dicts there -- while
    answering the same question, and the pooling belongs next to the rule it feeds.
    """
    from trophies.models import ConceptGenre, ConceptTheme
    concept_ids = list(concept_ids)
    genres, themes = {}, {}
    if not concept_ids:
        return genres, themes
    for cid, name in ConceptGenre.objects.filter(
            concept_id__in=concept_ids).values_list('concept_id', 'genre__name'):
        genres.setdefault(cid, set()).add(name)
    for cid, name in ConceptTheme.objects.filter(
            concept_id__in=concept_ids).values_list('concept_id', 'theme__name'):
        themes.setdefault(cid, set()).add(name)
    return genres, themes


def flatten_tags(concept_ids, genres_by_concept, themes_by_concept):
    """Union one group's per-concept tags into the (genres, themes) pair `assign_job_slugs`
    takes. The other half of the `pool_tags` split: pool once over many concepts, flatten per
    group (a contract, a stage)."""
    genres, themes = set(), set()
    for cid in concept_ids:
        genres |= genres_by_concept.get(cid, set())
        themes |= themes_by_concept.get(cid, set())
    return genres, themes


def suggest_job_slugs(concept_ids):
    """Pool genres/themes across the given concepts and return suggested job slugs."""
    concept_ids = list(concept_ids)
    if not concept_ids:
        return set()
    genres, themes = flatten_tags(concept_ids, *pool_tags(concept_ids))
    return assign_job_slugs(genres, themes)


def suggest_jobs_for_contract(contract):
    """Suggested job slugs for a Contract, pooling its member + bundle concepts (the game's
    full genre/theme profile), capped to MAX_CONTRACT_JOBS by signal strength. Empty if the
    Contract has no concepts."""
    ids = set(contract.member_concept_ids())
    for bundle in contract.bundles.all():
        ids |= set(bundle.concepts.values_list('id', flat=True))
    return top_jobs(suggest_job_slugs(ids))


def simulate_stage_jobs():
    """Auto-assign jobs to every XP-granting (series + developer) badge stage.

    Returns a list of job-slug sets, one per qualifying stage -- the auto-assigned job
    feed the Contract economy is projected from (each stage is a potential Contract paying
    T, split among its jobs). Concept scope mirrors report_concept_taxonomy: anchored,
    non-shovelware, developer/porter-attributed. Read-only and catalog-bounded (loads id
    sets in memory, not per-user). Shared by report_job_assignment + report_xp_economy.
    """
    from collections import defaultdict
    from django.db.models import Q
    from trophies.models import Badge, Concept, Stage

    xp_badge_types = ('series', 'developer')  # the only XP-granting badge types
    non_shovelware = ('clean', 'manually_cleared')

    xp_slugs = set(Badge.objects.filter(badge_type__in=xp_badge_types).values_list('series_slug', flat=True))
    xp_slugs.discard(None)
    qualifying_ids = set(
        Concept.objects
        .filter(anchor_migration_completed_at__isnull=False)
        .filter(games__shovelware_status__in=non_shovelware)
        .filter(Q(concept_companies__is_developer=True) | Q(concept_companies__is_porting=True))
        .values_list('id', flat=True)
    )

    stages = Stage.objects.filter(series_slug__in=xp_slugs)
    stage_concepts = defaultdict(set)
    for sid, cid in stages.values_list('id', 'concepts__id'):
        if cid in qualifying_ids:
            stage_concepts[sid].add(cid)
    for sid, cid in stages.values_list('id', 'concept_bundles__concepts__id'):
        if cid in qualifying_ids:
            stage_concepts[sid].add(cid)
    stage_concepts = {s: cs for s, cs in stage_concepts.items() if cs}
    if not stage_concepts:
        return []

    id_set = set().union(*stage_concepts.values())
    genre_by_concept, theme_by_concept = pool_tags(id_set)

    result = []
    for cs in stage_concepts.values():
        genres, themes = flatten_tags(cs, genre_by_concept, theme_by_concept)
        result.append(assign_job_slugs(genres, themes))
    return result
