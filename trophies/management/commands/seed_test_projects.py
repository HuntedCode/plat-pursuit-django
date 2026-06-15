"""DEV ONLY: seed test Projects (Contracts + accepted EarnedContracts) so the Logbook's
compound shelf is populated.

The shelf shows the molecules of Projects a profile has accepted, but no Contracts have
been created/accepted on most dev servers. This creates a handful of throwaway Contracts
with varied job-sets and marks them accepted for one profile (default id 3). The molecule
is deterministic per Contract slug, so the shelf renders distinct compounds.

    python manage.py seed_test_projects --profile-id 3
    python manage.py seed_test_projects --clear

NOT for production: it writes real Contract rows (slug-prefixed `test-project-`) and bypasses
the real reach/accept flow. `--clear` removes them (cascades the EarnedContracts).
"""
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from trophies.models import Contract, EarnedContract, Job, Profile

SLUG_PREFIX = 'test-project-'

# Varied job-sets (1 to 4 elements) to exercise the compound generator across sizes.
JOB_SETS = [
    (['gunslinger', 'slayer'], 'Test Project: Doomsday'),
    (['mage', 'cartographer', 'exorcist'], 'Test Project: Witchwood'),
    (['driver'], 'Test Project: Apex'),
    (['mastermind', 'tactician', 'architect', 'tycoon'], 'Test Project: Grand Design'),
    (['athlete', 'champion'], 'Test Project: Podium'),
    (['pathfinder', 'survivalist', 'mascot'], 'Test Project: Wilds'),
    (['maestro'], 'Test Project: Encore'),
    (['infiltrator', 'gunslinger', 'vanguard'], 'Test Project: Black Site'),
]


class Command(BaseCommand):
    help = "DEV ONLY: seed test Projects + accepted EarnedContracts so the Logbook compound shelf is populated."

    def add_arguments(self, parser):
        parser.add_argument('--profile-id', type=int, default=3)
        parser.add_argument('--clear', action='store_true', help='Remove the seeded test Projects (and their acceptances).')

    def handle(self, *args, **options):
        if not settings.DEBUG:
            raise CommandError("seed_test_projects is DEV ONLY; refusing to run with DEBUG=False (it writes real Contract rows).")

        if options['clear']:
            count, _ = Contract.objects.filter(slug__startswith=SLUG_PREFIX).delete()
            self.stdout.write(self.style.SUCCESS(f"Cleared {count} test-project row(s) (Contracts + cascaded acceptances)."))
            return

        try:
            profile = Profile.objects.get(id=options['profile_id'])
        except Profile.DoesNotExist:
            self.stderr.write(self.style.ERROR(f"No profile with id {options['profile_id']}."))
            return

        now = timezone.now()
        made = 0
        for i, (job_slugs, name) in enumerate(JOB_SETS):
            jobs = list(Job.objects.filter(slug__in=job_slugs))
            if not jobs:
                continue
            contract, _ = Contract.objects.update_or_create(
                slug=f'{SLUG_PREFIX}{i}', defaults={'name': name, 'is_live': True},
            )
            contract.jobs.set(jobs)
            EarnedContract.objects.update_or_create(
                profile=profile, contract=contract,
                defaults={'full_reached_at': now, 'full_accepted_at': now},
            )
            made += 1
        self.stdout.write(self.style.SUCCESS(
            f"Seeded {made} accepted test Project(s) for {profile.display_psn_username}. Run with --clear to remove."
        ))
