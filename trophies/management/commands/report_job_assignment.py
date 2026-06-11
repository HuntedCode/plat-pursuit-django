"""Simulate the gamification Job catalog against today's Badge stages.

Encodes the v1 Job catalog (genre/theme detection rules + combo overrides) and
assigns jobs to every Badge stage (the XP unit). A stage earns EVERY job it
qualifies for -- no cap. Three wrinkles keep the catalog balanced: a more-specific
combo job (genre + theme) overrides its base genre job (Mage over Roleplayer); the
broad Open-world / Comedy signals PARTITION on a paired genre instead of being one
oversized job each (Outlaw|Wayfarer, Mascot|Jester); and a Freelancer fallback
catches games that specialize in nothing. Reports how many stages would feed each
job and how many jobs a stage gets, so we see where the catalog lands on real data.

Concept scope mirrors report_concept_taxonomy --badge-stages: anchored
(anchor_migration_completed_at set) + non-shovelware (>=1 game clean/manually_
cleared) + developer-attributed (ConceptCompany is_developer or is_porting).
A stage's genre/theme profile = the union across its qualifying member concepts
(direct + ConceptBundle members). Read-only, offline analysis.

NOTE: the JOBS catalog below is the working v1 design artifact. When the real Job
system ships it should move to config/model; this command keeps it inline so we
can iterate the rules and re-count quickly.
"""
from collections import Counter, defaultdict

from django.core.management.base import BaseCommand
from django.db.models import Q

from trophies.models import Concept, ConceptGenre, ConceptTheme, Stage

NON_SHOVELWARE_STATUSES = ('clean', 'manually_cleared')

# (name, genres, themes, overrides) -- a job matches when:
#   genres only  -> stage has ANY of these genres
#   themes only  -> stage has ANY of these themes
#   both (combo) -> stage has a genre AND a theme; matching removes `overrides`
JOBS = [
    ('Roleplayer',  ['Role-playing (RPG)'],                                                        [], None),
    ('Gunslinger',  ['Shooter'],                                                                   [], None),
    ('Acrobat',     ['Platform'],                                                                  [], None),
    ('Slayer',      ["Hack and slash/Beat 'em up"],                                                [], None),
    ('Puzzler',     ['Puzzle'],                                                                    [], None),
    ('Tactician',   ['Strategy', 'Turn-based strategy (TBS)', 'Tactical', 'Real Time Strategy (RTS)', 'MOBA'], [], None),
    ('Tycoon',      ['Simulator'],                                                                 [], None),
    ('Arcader',     ['Arcade'],                                                                    [], None),
    ('Driver',      ['Racing'],                                                                    [], None),
    ('Brawler',     ['Fighting'],                                                                  [], None),
    ('Storyteller', ['Visual Novel', 'Point-and-click'],                                           [], None),
    ('Athlete',     ['Sport'],                                                                     [], None),
    ('Maestro',     ['Music'],                                                                     [], None),
    ('Card Shark',  ['Card & Board Game'],                                                         [], None),
    ('Infiltrator', [], ['Stealth'],      None),
    ('Survivalist', [], ['Survival'],     None),
    ('Builder',     [], ['Sandbox'],      None),
    ('Nightstalker', [], ['Horror'],      None),
    # Combo jobs (genre AND theme) -- override their base genre job.
    ('Mage',        ['Role-playing (RPG)'], ['Fantasy'],          'Roleplayer'),
    ('Starfarer',   ['Shooter'],            ['Science fiction'],  'Gunslinger'),
]

# Open world and Comedy are broad structures/tones, so instead of one big job each
# they PARTITION on a paired genre: a game gets exactly ONE side, so this splits an
# oversized job in two WITHOUT inflating jobs-per-stage.
#   Open world  -> Outlaw (+ a combat genre) | Wayfarer (exploration/RPG/builder)
#   Comedy      -> Mascot (+ Platform: cartoon mascot platformers) | Jester (rest)
COMBAT_GENRES = {'Shooter', "Hack and slash/Beat 'em up", 'Fighting'}

# Freelancer is the fallback: the job for games that match no specialization at all
# (pure Adventure/Action). Deliberately NOT named for a genre ("Adventurer" would
# invite "it's an adventure game, why no Adventurer XP?"); a Freelancer takes
# whatever job comes, which is exactly the no-specialization case.
FALLBACK_JOB = 'Freelancer'

# Display order for the report: base catalog, then the partition + fallback jobs.
CATALOG_ORDER = [name for name, *_ in JOBS] + ['Outlaw', 'Wayfarer', 'Mascot', 'Jester', FALLBACK_JOB]


def assign_jobs(genres, themes):
    """Return the set of jobs a stage qualifies for (combos override their base)."""
    matched = set()
    for name, g, t, _ in JOBS:
        if g and t:                                   # combo
            if (genres & set(g)) and (themes & set(t)):
                matched.add(name)
        elif g:                                       # genre job
            if genres & set(g):
                matched.add(name)
        elif t:                                       # theme job
            if themes & set(t):
                matched.add(name)
    for name, _, _, override in JOBS:
        if override and name in matched:
            matched.discard(override)

    # Open-world partition (exactly one side).
    if 'Open world' in themes:
        matched.add('Outlaw' if genres & COMBAT_GENRES else 'Wayfarer')
    # Comedy partition (exactly one side).
    if 'Comedy' in themes:
        matched.add('Mascot' if 'Platform' in genres else 'Jester')

    # Fallback: a game that specialized in nothing is a Freelancer.
    if not matched:
        matched.add(FALLBACK_JOB)
    return matched


class Command(BaseCommand):
    help = "Simulate the v1 Job catalog against current Badge stages (job feed + jobs-per-stage)."

    def handle(self, *args, **options):
        concepts = (
            Concept.objects
            .filter(anchor_migration_completed_at__isnull=False)
            .filter(games__shovelware_status__in=NON_SHOVELWARE_STATUSES)
            .filter(Q(concept_companies__is_developer=True) | Q(concept_companies__is_porting=True))
            .filter(Q(stages__isnull=False) | Q(bundles__isnull=False))
            .distinct()
        )
        id_set = set(concepts.values_list('id', flat=True))
        if not id_set:
            self.stdout.write(self.style.WARNING('No qualifying badge-stage concepts found.'))
            return

        genre_by_concept = defaultdict(set)
        for cid, g in ConceptGenre.objects.filter(concept_id__in=id_set).values_list('concept_id', 'genre__name'):
            genre_by_concept[cid].add(g)
        theme_by_concept = defaultdict(set)
        for cid, t in ConceptTheme.objects.filter(concept_id__in=id_set).values_list('concept_id', 'theme__name'):
            theme_by_concept[cid].add(t)

        stage_concepts = defaultdict(set)
        for sid, cid in Stage.objects.values_list('id', 'concepts__id'):
            if cid in id_set:
                stage_concepts[sid].add(cid)
        for sid, cid in Stage.objects.values_list('id', 'concept_bundles__concepts__id'):
            if cid in id_set:
                stage_concepts[sid].add(cid)
        stage_concepts = {s: cs for s, cs in stage_concepts.items() if cs}

        n_stages = len(stage_concepts)
        job_feed = Counter()          # job -> # stages awarding it
        per_stage_hist = Counter()    # # jobs -> # stages
        total_assignments = 0

        for cs in stage_concepts.values():
            genres, themes = set(), set()
            for cid in cs:
                genres |= genre_by_concept.get(cid, set())
                themes |= theme_by_concept.get(cid, set())
            jobs = assign_jobs(genres, themes)
            per_stage_hist[len(jobs)] += 1
            total_assignments += len(jobs)
            for j in jobs:
                job_feed[j] += 1

        w = self.stdout.write
        head = self.style.MIGRATE_HEADING
        fallback_n = job_feed.get(FALLBACK_JOB, 0)
        w(head('Job assignment simulation (v1 catalog, badge stages, no cap)'))
        w(f'  Stages:               {n_stages:>6,}')
        w(f'  Avg jobs / stage:     {total_assignments / n_stages:>6.2f}')
        w(f'  Freelancer fallback:  {fallback_n:>6,}  ({fallback_n / n_stages * 100:.1f}%)')
        w(f'  Jobs in catalog:      {len(CATALOG_ORDER):>6,}')

        w('')
        w(head('Jobs per stage (histogram)'))
        for k in sorted(per_stage_hist):
            c = per_stage_hist[k]
            w(f'  {c:>5,}  {c / n_stages * 100:5.1f}%  {k} job{"" if k == 1 else "s"}')

        w('')
        w(head('Job feed (# of stages awarding each job)'))
        for name in CATALOG_ORDER:
            c = job_feed.get(name, 0)
            w(f'  {c:>5,}  {c / n_stages * 100:5.1f}%  {name}')
