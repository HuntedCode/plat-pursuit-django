"""Project the Contract XP economy from the badge-stage supply -- a calibration tool.

Each XP-granting badge stage is a potential Contract paying T XP split among its
auto-assigned jobs; summing per job gives the XP SUPPLY each element can accumulate. The
FLAT cap-less curve (level = supply // K + 1) turns that into the level (and prestige tier)
each element reaches at full completion, plus the completionist Pursuer Level.

NOTE on supply: the badge pool GROWS forever, so "level at full completion" is a moving
target -- read this for the per-element SPREAD / imbalance and the tier mix, not as a
fixed ceiling. Feel is set by per-contract pacing (T and K), which is supply-independent.

Run on PROD; sweep alternatives with --t / --k:

    python manage.py report_xp_economy
    python manage.py report_xp_economy --t 6000 --k 2000

Read-only, catalog-bounded. Models the auto-assigned feed (no curation trim).
"""
from collections import Counter, defaultdict

from django.core.management.base import BaseCommand

from trophies.services.job_detection import simulate_stage_jobs, CATALOG_ORDER
from trophies.util_modules.constants import CONTRACT_XP_TOTAL, JOB_XP_PER_LEVEL
from trophies.util_modules.leveling import JOB_TIERS, tier_for_level


def _level(total_xp, k):
    """Flat cap-less level for a cumulative XP total (>=1, uncapped)."""
    return total_xp // k + 1 if total_xp > 0 else 1


class Command(BaseCommand):
    help = "Project the flat cap-less Contract XP economy (per-job supply -> level/tier, Pursuer Level)."

    def add_arguments(self, parser):
        parser.add_argument('--t', type=int, default=CONTRACT_XP_TOTAL,
                            help=f'Total XP per Contract (default {CONTRACT_XP_TOTAL}).')
        parser.add_argument('--k', type=int, default=JOB_XP_PER_LEVEL,
                            help=f'XP per level, flat (default {JOB_XP_PER_LEVEL}).')

    def handle(self, *args, **options):
        t, k = options['t'], options['k']

        per_stage = simulate_stage_jobs()
        if not per_stage:
            self.stdout.write(self.style.WARNING('No qualifying series/developer badge stages found.'))
            return

        n = len(per_stage)
        supply = defaultdict(float)   # slug -> total XP a completionist banks into this job
        feed = Counter()              # slug -> # stages feeding it
        total_assignments = 0
        for jobs in per_stage:
            j = len(jobs)
            total_assignments += j
            share = t / j             # T split evenly among the stage's jobs
            for slug in jobs:
                supply[slug] += share
                feed[slug] += 1

        levels = {slug: _level(int(supply.get(slug, 0)), k) for slug in CATALOG_ORDER}
        pursuer_level = sum(levels.values())                       # all 25 jobs, min level 1
        lv_sorted = sorted(levels.values())
        tier_counts = Counter(tier_for_level(lv)['name'] for lv in levels.values())

        w = self.stdout.write
        head = self.style.MIGRATE_HEADING

        w(head(f'Contract XP economy  (FLAT cap-less  T={t:,}  K={k:,})'))
        w(f'  Stages (potential Contracts):  {n:>8,}')
        w(f'  Total system XP (stages x T):  {n * t:>8,}')
        w(f'  Avg jobs / stage:              {total_assignments / n:>8.2f}')

        w('')
        w(head('Per-job supply + level/tier at FULL completion (every feeding stage done)'))
        w(f'  {"job":<14}{"stages":>8}{"supply XP":>12}{"level":>7}   tier')
        for slug in sorted(CATALOG_ORDER, key=lambda s: supply.get(s, 0), reverse=True):
            lv = levels[slug]
            w(f'  {slug:<14}{feed.get(slug, 0):>8,}{int(supply.get(slug, 0)):>12,}{lv:>7}   {tier_for_level(lv)["name"]}')

        w('')
        w(head('Aggregate (a completionist who has done every stage)'))
        w(f'  Pursuer Level (sum of 25 job levels):  {pursuer_level:>6,}')
        w(f'  Job level  min / median / max:         {lv_sorted[0]} / {lv_sorted[len(lv_sorted)//2]} / {lv_sorted[-1]}')
        w('  Tier mix (jobs per tier):              ' +
          ', '.join(f'{name} {tier_counts.get(name, 0)}' for _, _, name in JOB_TIERS if tier_counts.get(name)))

        w('')
        w(head('Tier ladder  (level -> XP -> ~genre completions at T/2 each)'))
        for min_level, _key, name in JOB_TIERS:
            xp = k * (min_level - 1)
            completions = round(xp / (t / 2)) if t else 0
            w(f'  {name:<12} L{min_level:<4}{xp:>10,} XP   ~{completions} completions')
