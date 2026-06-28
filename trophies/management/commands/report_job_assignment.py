"""Simulate the Job catalog against today's SERIES + DEVELOPER badge stages.

Uses the shared genre/theme -> job detection (trophies/services/job_detection.py) to
assign jobs to every XP-granting badge stage, then reports the job feed + the
jobs-per-stage distribution. A stage earns EVERY job it qualifies for (no cap); combos
override their base, Open-world/Comedy partition, Freelancer is the fallback.

Supply gathering is shared with report_xp_economy via job_detection.simulate_stage_jobs().
Read-only, catalog-bounded. Output is keyed by job slug.
"""
from collections import Counter

from django.core.management.base import BaseCommand

from trophies.services.job_detection import simulate_stage_jobs, CATALOG_ORDER, FALLBACK_SLUG


class Command(BaseCommand):
    help = "Simulate the Job catalog against current Badge stages (job feed + jobs-per-stage)."

    def handle(self, *args, **options):
        per_stage = simulate_stage_jobs()
        if not per_stage:
            self.stdout.write(self.style.WARNING('No qualifying series/developer badge stages found.'))
            return

        n_stages = len(per_stage)
        job_feed = Counter()          # job slug -> # stages awarding it
        per_stage_hist = Counter()    # # jobs -> # stages
        total_assignments = 0
        for jobs in per_stage:
            per_stage_hist[len(jobs)] += 1
            total_assignments += len(jobs)
            for slug in jobs:
                job_feed[slug] += 1

        w = self.stdout.write
        head = self.style.MIGRATE_HEADING
        fallback_n = job_feed.get(FALLBACK_SLUG, 0)
        w(head('Job assignment simulation (SERIES + DEVELOPER badges, no cap)'))
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
