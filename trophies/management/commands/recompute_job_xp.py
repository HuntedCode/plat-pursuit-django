"""Rebuild the ProfileJobXP cache from the immutable ContractXPGrant ledger.

The cache is bumped incrementally on every accept; this repairs it (after data
edits, a bug, or a manual ledger fix) by re-summing the ledger in the DB. Run for one
profile (`--user <psn_username>`) or every affected profile (`--all`).

`--all` also rolls up ProfileCareerStanding (via `recompute_profile_job_xp`), so it is the
backfill to run after any change to how Pursuer Level is DEFINED -- the standing stores a
materialized copy, and every row written before such a change carries the old definition.
"""
from django.core.management.base import BaseCommand

from trophies.models import ContractXPGrant, Profile, ProfileCareerStanding
from trophies.services.contract_service import recompute_profile_job_xp


class Command(BaseCommand):
    help = "Rebuild ProfileJobXP from the ContractXPGrant ledger (--user <psn_username> or --all)."

    def add_arguments(self, parser):
        parser.add_argument('--user', type=str, help='psn_username of a single profile to recompute.')
        parser.add_argument('--all', action='store_true', dest='all_profiles',
                            help='Recompute every profile with grants OR a career standing row.')

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
            # Profiles with grants UNION profiles that merely have a standing row. The second half
            # is not redundant: a hunter whose grants were all removed (`reconcile_contracts`, or
            # `reset_claim` in dev) keeps a ProfileCareerStanding at zero with no ledger behind it,
            # so a grants-only sweep silently skips exactly the rows a repair run is most likely to
            # be chasing. Both subqueries stay in the DB (no id list in Python).
            profile_ids = (ContractXPGrant.objects.values('profile_id')
                           .union(ProfileCareerStanding.objects.values('profile_id')))
            count = 0
            for profile in Profile.objects.filter(id__in=profile_ids).iterator():
                recompute_profile_job_xp(profile)
                count += 1
            self.stdout.write(self.style.SUCCESS(
                f"Recomputed job XP for {count} profile(s) with grants or a standing row."))
            return

        self.stderr.write(self.style.ERROR("Provide --user <psn_username> or --all."))
