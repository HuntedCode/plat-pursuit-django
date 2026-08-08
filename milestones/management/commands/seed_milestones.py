"""Seed / update the v1 milestone catalogue (idempotent).

The catalogue is DATA — this command upserts it. Re-running updates names/thresholds/metadata and PRESERVES
each tier's `earned_count`, `discord_role_id`, and every `EarnedMilestoneTier`. Safe to re-run.

Note: it does NOT auto-shrink a ladder — if a catalogue entry loses tiers, the old higher-index rows are
left in place (deleting them would drop earned history) and the command WARNS about them so an operator can
decide. Growing a ladder or changing thresholds is fully handled.

Two ladders are placeholders pending real-data calibration (badge catalogue size, the cap-less Pursuer Level
economy) — see docs/design/milestones-revamp.md §8. They're pure data, so re-seeding with tuned numbers is
just editing CATALOG and re-running.
"""
from django.core.management.base import BaseCommand
from django.db import transaction

from milestones.models import Milestone, MilestoneTier

CATALOG = [
    {
        'slug': 'platinum-hunter', 'name': 'Platinum Hunter', 'icon': 'trophy',
        'description': 'Platinums earned across your whole library.',
        'metric': 'lifetime_platinums', 'category': 'Trophy Hunting', 'sort_order': 10,
        'tiers': [1, 5, 10, 25, 50, 100, 250, 500, 1000, 2000],
    },
    {
        'slug': 'trophy-collector', 'name': 'Trophy Collector', 'icon': 'award',
        'description': 'Total trophies of every grade earned.',
        'metric': 'lifetime_trophies', 'category': 'Trophy Hunting', 'sort_order': 20,
        'tiers': [100, 500, 1000, 2500, 5000, 10000, 25000, 50000, 75000, 100000],
    },
    {
        'slug': 'completionist', 'name': 'Completionist', 'icon': 'check-circle',
        'description': 'Games taken all the way to 100% completion.',
        'metric': 'full_completions', 'category': 'Trophy Hunting', 'sort_order': 30,
        'tiers': [1, 5, 10, 25, 50, 100, 250, 500, 750, 1000],
    },
    {
        'slug': 'badge-collector', 'name': 'Badge Collector', 'icon': 'shield',
        'description': 'Badges earned across the collection.',
        'metric': 'total_badges_earned', 'category': 'Collection', 'sort_order': 40,
        # PLACEHOLDER ceiling — calibrate against the real badge catalogue size.
        'tiers': [1, 5, 10, 25, 50, 100, 150, 250, 375, 500],
    },
    {
        'slug': 'pursuer-ascent', 'name': 'Pursuer Ascent', 'icon': 'trending-up',
        'description': 'Your total Pursuer Level across every job.',
        'metric': 'pursuer_level', 'category': 'Collection', 'sort_order': 50,
        # PLACEHOLDER — the Pursuer Level curve is flat + cap-less with a nonzero baseline; calibrate.
        'tiers': [25, 50, 100, 200, 300, 500, 750, 1000, 1500, 2000],
    },
    {
        'slug': 'time-invested', 'name': 'Time Invested', 'icon': 'clock',
        'description': 'Hours logged across your PlayStation library.',
        'metric': 'playtime_hours', 'category': 'Dedication', 'sort_order': 60,
        'tiers': [10, 50, 100, 250, 500, 1000, 2500, 5000, 7500, 10000],
    },
]


class Command(BaseCommand):
    help = "Upsert the v1 milestone catalogue (idempotent; preserves earned history)."

    @transaction.atomic
    def handle(self, *args, **options):
        created_m = updated_m = created_t = updated_t = 0
        for spec in CATALOG:
            tiers = spec['tiers']
            defaults = {k: v for k, v in spec.items() if k not in ('slug', 'tiers')}
            milestone, m_created = Milestone.objects.update_or_create(slug=spec['slug'], defaults=defaults)
            created_m += int(m_created)
            updated_m += int(not m_created)
            for i, threshold in enumerate(tiers, start=1):
                _, t_created = MilestoneTier.objects.update_or_create(
                    milestone=milestone, index=i,
                    defaults={'threshold': threshold},   # earned_count + discord_role_id preserved
                )
                created_t += int(t_created)
                updated_t += int(not t_created)

            # Warn (don't delete -- preserves earned history) if the ladder shrank vs. what's stored.
            stale = milestone.tiers.filter(index__gt=len(tiers)).count()
            if stale:
                self.stderr.write(self.style.WARNING(
                    f"  {milestone.slug}: {stale} stale tier(s) with index > {len(tiers)} remain "
                    f"(ladder shrank). Left in place to preserve earned history; remove manually if intended."
                ))

        self.stdout.write(self.style.SUCCESS(
            f"Milestones seeded: {created_m} created / {updated_m} updated; "
            f"tiers: {created_t} created / {updated_t} updated."
        ))
