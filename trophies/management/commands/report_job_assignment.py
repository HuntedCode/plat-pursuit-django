"""Simulate the gamification Job catalog against today's Badge stages.

Encodes the v1 Job catalog (genre/theme detection rules + combo overrides) and
assigns jobs to every Badge stage (the XP unit). A stage earns EVERY job it
qualifies for -- no cap -- and a more-specific combo job (genre + theme) overrides
its base genre job. Reports how many stages would feed each job and how many jobs
a stage gets, so we can see exactly where the catalog lands against real data.

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
    ('Wanderer',    [], ['Open world'],   None),
    ('Infiltrator', [], ['Stealth'],      None),
    ('Survivalist', [], ['Survival'],     None),
    ('Builder',     [], ['Sandbox'],      None),
    ('Nightstalker', [], ['Horror'],      None),
    ('Jester',      [], ['Comedy'],       None),
    # Combo jobs (genre AND theme) -- override their base genre job.
    ('Mage',        ['Role-playing (RPG)'], ['Fantasy'],          'Roleplayer'),
    ('Starfarer',   ['Shooter'],            ['Science fiction'],  'Gunslinger'),
]


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
        no_job = 0
        total_assignments = 0

        for cs in stage_concepts.values():
            genres, themes = set(), set()
            for cid in cs:
                genres |= genre_by_concept.get(cid, set())
                themes |= theme_by_concept.get(cid, set())
            jobs = assign_jobs(genres, themes)
            per_stage_hist[len(jobs)] += 1
            total_assignments += len(jobs)
            if not jobs:
                no_job += 1
            for j in jobs:
                job_feed[j] += 1

        w = self.stdout.write
        head = self.style.MIGRATE_HEADING
        w(head('Job assignment simulation (v1 catalog, badge stages, no cap)'))
        w(f'  Stages:               {n_stages:>6,}')
        w(f'  Avg jobs / stage:     {total_assignments / n_stages:>6.2f}')
        w(f'  Stages with NO job:   {no_job:>6,}  ({no_job / n_stages * 100:.1f}%)')
        w(f'  Jobs in catalog:      {len(JOBS):>6,}')

        w('')
        w(head('Jobs per stage (histogram)'))
        for k in sorted(per_stage_hist):
            c = per_stage_hist[k]
            w(f'  {c:>5,}  {c / n_stages * 100:5.1f}%  {k} job{"" if k == 1 else "s"}')

        w('')
        w(head('Job feed (# of stages awarding each job)'))
        for name, _, _, _ in JOBS:
            c = job_feed.get(name, 0)
            w(f'  {c:>5,}  {c / n_stages * 100:5.1f}%  {name}')
