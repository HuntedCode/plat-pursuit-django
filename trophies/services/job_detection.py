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
