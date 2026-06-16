"""DEV ONLY: seed sample ProfileJobXP rows so the Lab is populated.

No Contracts have been accepted anywhere yet, so every real profile's elements sit at
the level-1 floor. This command writes a varied spread of job XP for one profile (default
id 3) to exercise the active/mastered states while building the Lab. Idempotent
(update_or_create); `--clear` removes the seeded rows.

    python manage.py seed_test_job_xp --profile-id 3
    python manage.py seed_test_job_xp --profile-id 3 --clear

NOT for production data: it writes the cache (ProfileJobXP) directly without a backing
ContractXPGrant ledger, so `recompute_job_xp` would wipe it. Remove with --clear.
"""
from itertools import cycle

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from trophies.models import Job, Profile, ProfileJobXP
from trophies.util_modules.constants import JOB_LEVEL_CAP
from trophies.util_modules.leveling import level_for_xp, xp_for_level


class Command(BaseCommand):
    help = "DEV ONLY: seed sample ProfileJobXP for a profile so the Lab is populated."

    # A varied spread (level-1 floors, mids, and a couple of mastered) across ~25 jobs.
    TARGETS = [
        14, 8, 1, 27, 5,
        9, 1, 33, 6, 12,
        18, 4, 1, 50, 7,
        3, 22, 1, 9, 6,
        50, 11, 2, 16, 1,
    ]

    def add_arguments(self, parser):
        parser.add_argument('--profile-id', type=int, default=3)
        parser.add_argument('--clear', action='store_true', help='Remove all seeded job XP for the profile.')

    def handle(self, *args, **options):
        if not settings.DEBUG:
            raise CommandError("seed_test_job_xp is DEV ONLY; refusing to run with DEBUG=False (it writes synthetic XP to a real profile).")
        try:
            profile = Profile.objects.get(id=options['profile_id'])
        except Profile.DoesNotExist:
            self.stderr.write(self.style.ERROR(f"No profile with id {options['profile_id']}."))
            return

        if options['clear']:
            count, _ = ProfileJobXP.objects.filter(profile=profile).delete()
            self.stdout.write(self.style.SUCCESS(f"Cleared {count} job XP row(s) for {profile.display_psn_username}."))
            return

        jobs = list(Job.objects.all().order_by('discipline', 'display_order'))
        for job, target in zip(jobs, cycle(self.TARGETS)):
            if target >= JOB_LEVEL_CAP:
                total_xp = xp_for_level(JOB_LEVEL_CAP)
            else:
                span = xp_for_level(target + 1) - xp_for_level(target)
                total_xp = xp_for_level(target) + span // 3  # a third into the level, for a varied bar
            ProfileJobXP.objects.update_or_create(
                profile=profile, job=job,
                defaults={'total_xp': total_xp, 'level': level_for_xp(total_xp)},
            )
        self.stdout.write(self.style.SUCCESS(
            f"Seeded {len(jobs)} job XP row(s) for {profile.display_psn_username}. "
            f"Run with --clear to remove."
        ))
