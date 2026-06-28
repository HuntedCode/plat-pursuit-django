"""Project the Contract XP economy from the badge-stage supply -- a calibration tool.

Each XP-granting badge stage is a potential Contract paying T XP, split evenly among the
jobs it auto-assigns (job_detection). Summing that per job gives the XP SUPPLY each job
(element) can ever accumulate, and the leveling curve turns that into the LEVEL each job
reaches when a completionist has done every feeding stage -- and the resulting Pursuer
Level (sum of all 25 job levels).

Run on PROD (the real curated pool) to see whether the current numbers feel right, and
re-run with --t / --base / --cap to compare alternative economies before committing:

    python manage.py report_xp_economy
    python manage.py report_xp_economy --base 900 --cap 99 --t 6000

Read-only, catalog-bounded. NOTE: models the AUTO-assigned feed (every job a stage
qualifies for, no curation trim), so it's an upper bound on jobs-per-contract; real
curation that trims jobs raises each remaining job's per-contract share.
"""
import math
from collections import Counter, defaultdict

from django.core.management.base import BaseCommand

from trophies.services.job_detection import simulate_stage_jobs, CATALOG_ORDER
from trophies.util_modules.constants import (
    CONTRACT_XP_TOTAL, JOB_LEVEL_BASE, JOB_LEVEL_CAP,
)


def _xp_for_level(level, base, cap):
    """Cumulative XP to BE at `level` (1-based; level 1 = 0). base * (L-1)L/2, capped."""
    if level <= 1:
        return 0
    level = min(level, cap)
    return base * (level - 1) * level // 2


def _level_for_xp(total_xp, base, cap):
    """Level for a cumulative XP total (>=1, capped at cap)."""
    if total_xp <= 0:
        return 1
    level = int((1 + math.sqrt(1 + 8 * total_xp / base)) / 2)
    level = min(max(level, 1), cap)
    while level < cap and _xp_for_level(level + 1, base, cap) <= total_xp:
        level += 1
    while level > 1 and _xp_for_level(level, base, cap) > total_xp:
        level -= 1
    return level


class Command(BaseCommand):
    help = "Project the Contract XP economy (per-job supply -> level, Pursuer Level) from badge stages."

    def add_arguments(self, parser):
        parser.add_argument('--t', type=int, default=CONTRACT_XP_TOTAL,
                            help=f'Total XP per Contract (default {CONTRACT_XP_TOTAL}).')
        parser.add_argument('--base', type=int, default=JOB_LEVEL_BASE,
                            help=f'Level curve coefficient (default {JOB_LEVEL_BASE}).')
        parser.add_argument('--cap', type=int, default=JOB_LEVEL_CAP,
                            help=f'Per-job level cap (default {JOB_LEVEL_CAP}).')

    def handle(self, *args, **options):
        t, base, cap = options['t'], options['base'], options['cap']

        per_stage = simulate_stage_jobs()
        if not per_stage:
            self.stdout.write(self.style.WARNING('No qualifying series/developer badge stages found.'))
            return

        n = len(per_stage)
        supply = defaultdict(float)   # slug -> total XP a completionist banks into this job
        feed = Counter()              # slug -> # stages feeding it
        total_assignments = 0
        for jobs in per_stage:
            k = len(jobs)
            total_assignments += k
            share = t / k             # T split evenly among the stage's jobs
            for slug in jobs:
                supply[slug] += share
                feed[slug] += 1

        levels = {slug: _level_for_xp(int(supply.get(slug, 0)), base, cap) for slug in CATALOG_ORDER}
        pursuer_level = sum(levels.values())                       # all 25 jobs, min level 1
        lv_sorted = sorted(levels.values())
        at_cap = sum(1 for lv in levels.values() if lv >= cap)

        w = self.stdout.write
        head = self.style.MIGRATE_HEADING

        w(head(f'Contract XP economy projection  (T={t:,}  BASE={base}  CAP={cap})'))
        w(f'  Stages (potential Contracts):  {n:>8,}')
        w(f'  Total system XP (stages x T):  {n * t:>8,}')
        w(f'  Avg jobs / stage:              {total_assignments / n:>8.2f}')
        w(f'  XP to reach the cap (L{cap}):     {_xp_for_level(cap, base, cap):>8,}')

        w('')
        w(head('Per-job supply + level at FULL completion (every feeding stage done)'))
        w(f'  {"job":<14}{"stages":>8}{"supply XP":>12}{"level":>7}')
        for slug in sorted(CATALOG_ORDER, key=lambda s: supply.get(s, 0), reverse=True):
            lv = levels[slug]
            flag = '  (cap)' if lv >= cap else ''
            w(f'  {slug:<14}{feed.get(slug, 0):>8,}{int(supply.get(slug, 0)):>12,}{lv:>7}{flag}')

        w('')
        w(head('Aggregate (a completionist who has done every stage)'))
        w(f'  Pursuer Level (sum of 25 job levels):  {pursuer_level:>6,}')
        w(f'  Job level  min / median / max:         {lv_sorted[0]} / {lv_sorted[len(lv_sorted)//2]} / {lv_sorted[-1]}')
        w(f'  Jobs at cap ({cap}):                       {at_cap} of {len(CATALOG_ORDER)}')

        w('')
        w(head('Curve reference (cumulative XP to reach level L)'))
        for lv in (2, 5, 10, 20, 30, 40, 50, cap):
            if lv <= cap:
                w(f'  L{lv:<3} {_xp_for_level(lv, base, cap):>10,}')
