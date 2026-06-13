"""Simulate the Job catalog against today's SERIES + DEVELOPER badge stages.

Uses the shared genre/theme -> job detection (trophies/services/job_detection.py) to
assign jobs to every XP-granting badge stage, then reports the job feed + the
jobs-per-stage distribution. A stage earns EVERY job it qualifies for (no cap); combos
override their base, Open-world/Comedy partition, Freelancer is the fallback.

Only SERIES and DEVELOPER badge stages count (the XP-granting types). Concept scope:
anchored + non-shovelware + developer-attributed (mirrors report_concept_taxonomy).
Read-only, offline (catalog-bounded). Output is keyed by job slug.
"""
from collections import Counter, defaultdict

from django.core.management.base import BaseCommand
from django.db.models import Q

from trophies.models import Badge, Concept, ConceptGenre, ConceptTheme, Stage
from trophies.services.job_detection import assign_job_slugs, CATALOG_ORDER, FALLBACK_SLUG

NON_SHOVELWARE_STATUSES = ('clean', 'manually_cleared')
# Only series + developer badges grant XP (one consistent home per game), so the
# true XP footprint is their stages only.
XP_BADGE_TYPES = ('series', 'developer')


class Command(BaseCommand):
    help = "Simulate the Job catalog against current Badge stages (job feed + jobs-per-stage)."

    def handle(self, *args, **options):
        xp_slugs = set(
            Badge.objects.filter(badge_type__in=XP_BADGE_TYPES).values_list('series_slug', flat=True)
        )
        xp_slugs.discard(None)

        qualifying_ids = set(
            Concept.objects
            .filter(anchor_migration_completed_at__isnull=False)
            .filter(games__shovelware_status__in=NON_SHOVELWARE_STATUSES)
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
            self.stdout.write(self.style.WARNING('No qualifying series/developer badge stages found.'))
            return

        id_set = set().union(*stage_concepts.values())
        genre_by_concept = defaultdict(set)
        for cid, g in ConceptGenre.objects.filter(concept_id__in=id_set).values_list('concept_id', 'genre__name'):
            genre_by_concept[cid].add(g)
        theme_by_concept = defaultdict(set)
        for cid, t in ConceptTheme.objects.filter(concept_id__in=id_set).values_list('concept_id', 'theme__name'):
            theme_by_concept[cid].add(t)

        n_stages = len(stage_concepts)
        job_feed = Counter()          # job slug -> # stages awarding it
        per_stage_hist = Counter()    # # jobs -> # stages
        total_assignments = 0

        for cs in stage_concepts.values():
            genres, themes = set(), set()
            for cid in cs:
                genres |= genre_by_concept.get(cid, set())
                themes |= theme_by_concept.get(cid, set())
            jobs = assign_job_slugs(genres, themes)
            per_stage_hist[len(jobs)] += 1
            total_assignments += len(jobs)
            for slug in jobs:
                job_feed[slug] += 1

        w = self.stdout.write
        head = self.style.MIGRATE_HEADING
        fallback_n = job_feed.get(FALLBACK_SLUG, 0)
        w(head('Job assignment simulation (SERIES + DEVELOPER badges, no cap)'))
        w(f'  XP badges (slugs):    {len(xp_slugs):>6,}')
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
        w(head('Job feed (# of stages awarding each job, by slug)'))
        for slug in CATALOG_ORDER:
            c = job_feed.get(slug, 0)
            w(f'  {c:>5,}  {c / n_stages * 100:5.1f}%  {slug}')
