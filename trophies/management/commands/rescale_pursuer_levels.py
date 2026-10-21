"""Move every stored Pursuer Level onto the levels-gained definition (2026-10). One-off, idempotent.

Pursuer Level used to be the sum of every job's HELD level, so a hunter with 0 XP was level 25. It now
counts levels GAINED (`contract_service.gained_levels`): a newcomer is 1. Live reads change on deploy;
two stored copies do not, and this command moves them:

1. `ProfileCareerStanding.pursuer_level` (the Career XP board's "level" column), recomputed from
   ProfileJobXP with the SAME `gained_levels` expression the live reads use. Recompute-from-scratch, so a
   second run changes nothing.
2. `ProgressionMilestone.level_at` on Pursuer rank rows, which records the rank's threshold when it was
   reached. Set to the rank's current threshold from `PURSUER_RANKS`, by key, so it holds whatever the
   row held before, including a row the new code wrote between the deploy and this run.

WHY NOT `recompute_job_xp --all`: it rebuilds every hunter's job XP from the ledger, about 30 queries per
profile, unlocked, and the 2026-09 floor entry in prod-deploy-checklist.md records that it can overwrite a
claim landing mid-rebuild. Neither stored copy needs that: both derive from rows that are already right.

LIVE CLAIMS. A claim updates ProfileJobXP, then its own standing row, in one transaction. A bare UPDATE
here that waited on that standing row would, once released, still compute from the job XP as it was when
the UPDATE began, and overwrite the claim's correct level with a stale one. So each chunk first LOCKS its
standing rows in a statement of its own (waiting out any claim holding one), then UPDATEs in a second
statement, which under READ COMMITTED reads the job XP fresh. A claim that arrives after the lock waits for
the chunk to commit and then writes its own, correct, value.

CHUNKED by primary key because a Render Shell on the web service inherits its 15s statement timeout, and
the correlated subquery is only ever evaluated for one chunk at a time.

Then run `seed_milestones` (Pursuer Ascent rung thresholds) and `recompute_milestones` (current values).
"""
from django.core.management.base import BaseCommand
from django.db import transaction
from django.db.models import IntegerField, OuterRef, Subquery, Value
from django.db.models.functions import Coalesce

from trophies.models import ProfileCareerStanding, ProfileJobXP, ProgressionMilestone
from trophies.services.contract_service import gained_levels
from trophies.util_modules.leveling import PURSUER_RANKS


def stored_level_expression():
    """`1 + levels gained` for the standing's profile, as SQL. `pursuer_level_from` in a subquery."""
    gained = (ProfileJobXP.objects.filter(profile_id=OuterRef('profile_id')).order_by()
              .values('profile_id').annotate(g=gained_levels()).values('g')[:1])
    return Value(1) + Coalesce(Subquery(gained, output_field=IntegerField()), Value(0))


class Command(BaseCommand):
    help = 'Move stored Pursuer Levels and Pursuer rank milestones onto the levels-gained definition.'

    def add_arguments(self, parser):
        parser.add_argument('--dry-run', action='store_true', help='Report what would change without writing.')
        parser.add_argument('--chunk-size', type=int, default=2000, help='Standing rows per UPDATE.')

    def handle(self, *args, **options):
        dry_run = options['dry_run']
        # Zero or negative would make the chunk loop empty and still report success.
        chunk_size = max(1, options['chunk_size'])

        pks = list(ProfileCareerStanding.objects.order_by('pk').values_list('pk', flat=True))
        standings = 0
        for i in range(0, len(pks), chunk_size):
            chunk = pks[i:i + chunk_size]
            stale = ProfileCareerStanding.objects.filter(pk__in=chunk).exclude(pursuer_level=stored_level_expression())
            if dry_run:
                standings += stale.count()
                continue
            with transaction.atomic():
                list(ProfileCareerStanding.objects.select_for_update().filter(pk__in=chunk)
                     .values_list('pk', flat=True))
                standings += stale.update(pursuer_level=stored_level_expression())

        ranks = 0
        for min_level, key, _name, _has_div in PURSUER_RANKS:
            rows = ProgressionMilestone.objects.filter(
                kind=ProgressionMilestone.PURSUER_RANK, key=key).exclude(level_at=min_level)
            ranks += rows.count() if dry_run else rows.update(level_at=min_level)

        verb = 'Would move' if dry_run else 'Moved'
        self.stdout.write(self.style.SUCCESS(
            f'{verb} {standings:,} career standing(s) and {ranks:,} Pursuer rank milestone(s) onto the '
            f'levels-gained Pursuer Level.'))
        if not dry_run:
            self.stdout.write('Next: `seed_milestones`, then `recompute_milestones`.')
