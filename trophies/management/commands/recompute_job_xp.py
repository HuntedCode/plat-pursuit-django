"""Rebuild the ProfileJobXP cache from the immutable ContractXPGrant ledger.

The cache is bumped incrementally on every accept; this repairs it (after data
edits, a bug, or a manual ledger fix) by re-summing the ledger in the DB. Run for one
profile (`--user <psn_username>`) or every profile with grants (`--all`).
"""
from django.core.management.base import BaseCommand

from trophies.models import ContractXPGrant, Profile
from trophies.services.contract_service import recompute_profile_job_xp


class Command(BaseCommand):
    help = "Rebuild ProfileJobXP from the ContractXPGrant ledger (--user <psn_username> or --all)."

    def add_arguments(self, parser):
        parser.add_argument('--user', type=str, help='psn_username of a single profile to recompute.')
        parser.add_argument('--all', action='store_true', dest='all_profiles', help='Recompute every profile that has grants.')

    def handle(self, *args, **options):
        username = options.get('user')
        if username:
            try:
                profile = Profile.objects.get(psn_username=username)
            except Profile.DoesNotExist:
                self.stderr.write(self.style.ERROR(f"No profile with psn_username '{username}'."))
                return
            recompute_profile_job_xp(profile)
            self.stdout.write(self.style.SUCCESS(f"Recomputed job XP for {username}."))
            return

        if options.get('all_profiles'):
            profile_ids = ContractXPGrant.objects.values_list('profile_id', flat=True).distinct()
            count = 0
            for profile in Profile.objects.filter(id__in=profile_ids).iterator():
                recompute_profile_job_xp(profile)
                count += 1
            self.stdout.write(self.style.SUCCESS(f"Recomputed job XP for {count} profile(s) with grants."))
            return

        self.stderr.write(self.style.ERROR("Provide --user <psn_username> or --all."))
