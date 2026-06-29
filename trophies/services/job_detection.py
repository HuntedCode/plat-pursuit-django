"""Genre/theme -> Job detection: the single source for job suggestions.

Maps a game's pooled IGDB genres/themes to job slugs (matching the seeded Job
catalog). Used to SUGGEST jobs for a Contract (staff confirm/trim) and by the
report_job_assignment analysis command. See docs/design/rebuild/job-board-contracts.md.
"""

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

# All 25 slugs (24 specializations + fallback), in catalog order.
CATALOG_ORDER = [slug for slug, *_ in JOB_RULES] + ['outlaw', 'cartographer', 'mascot', 'jester', FALLBACK_SLUG]


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


def suggest_job_slugs(concept_ids):
    """Pool genres/themes across the given concepts and return suggested job slugs."""
    from trophies.models import ConceptGenre, ConceptTheme
    concept_ids = list(concept_ids)
    if not concept_ids:
        return set()
    genres = set(
        ConceptGenre.objects.filter(concept_id__in=concept_ids).values_list('genre__name', flat=True)
    )
    themes = set(
        ConceptTheme.objects.filter(concept_id__in=concept_ids).values_list('theme__name', flat=True)
    )
    return assign_job_slugs(genres, themes)


def suggest_jobs_for_contract(contract):
    """Suggested job slugs for a Contract, pooling its member + bundle concepts (the
    game's full genre/theme profile). Empty if the Contract has no concepts."""
    ids = set(contract.memberships.values_list('concept_id', flat=True))
    for bundle in contract.bundles.all():
        ids |= set(bundle.concepts.values_list('id', flat=True))
    return suggest_job_slugs(ids)


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
    from trophies.models import Badge, Concept, ConceptGenre, ConceptTheme, Stage

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
    genre_by_concept = defaultdict(set)
    for cid, g in ConceptGenre.objects.filter(concept_id__in=id_set).values_list('concept_id', 'genre__name'):
        genre_by_concept[cid].add(g)
    theme_by_concept = defaultdict(set)
    for cid, t in ConceptTheme.objects.filter(concept_id__in=id_set).values_list('concept_id', 'theme__name'):
        theme_by_concept[cid].add(t)

    result = []
    for cs in stage_concepts.values():
        genres, themes = set(), set()
        for cid in cs:
            genres |= genre_by_concept.get(cid, set())
            themes |= theme_by_concept.get(cid, set())
        result.append(assign_job_slugs(genres, themes))
    return result
