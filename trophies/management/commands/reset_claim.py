"""Rewind a dev profile's Contract claim state so the REAL claim flow (accept -> ceremony) can be
re-tested end-to-end without hand-editing the DB.

    python manage.py reset_claim --user <psn_username>                 # full reset: everything claimable again
    python manage.py reset_claim --user <psn_username> --contract <slug>   # un-accept just one Contract

DEV-ONLY. The full reset un-accepts every Contract (clears the accepted stamps, keeping the reached
stamps so they're claimable again), deletes the profile's XP ledger + milestones, and zeroes the
ProfileJobXP cache -- so the next claim re-fires from scratch, `first_claim` included. `--contract`
targets one Contract's acceptance only (its grants + accepted stamps); milestones are cumulative and
aren't rewound in that mode. This DESTROYS banked XP for the profile, so it's guarded on DEBUG.
"""
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from trophies.models import ContractXPGrant, EarnedContract, ProgressionMilestone, Profile
from trophies.services import contract_service


class Command(BaseCommand):
    help = "DEV: rewind a profile's Contract claim state so the claim flow can be re-tested (--user <psn>)."

    def add_arguments(self, parser):
        parser.add_argument('--user', required=True, help='psn_username of the dev profile to reset.')
        parser.add_argument('--contract', help='Un-accept only this Contract slug (default: full reset).')

    @transaction.atomic
    def handle(self, *args, **opts):
        if not settings.DEBUG:
            raise CommandError("reset_claim is DEV-only (settings.DEBUG must be True).")
        try:
            profile = Profile.objects.get(psn_username=opts['user'])
        except Profile.DoesNotExist:
            raise CommandError(f"No profile with psn_username '{opts['user']}'.")

        if opts.get('contract'):
            self._reset_one(profile, opts['contract'])
        else:
            self._reset_all(profile)

        claimable = contract_service.claimable_contracts(profile).count()
        self.stdout.write(self.style.SUCCESS(f"{profile.psn_username}: {claimable} Contract(s) now claimable."))

    def _reset_all(self, profile):
        grants = ContractXPGrant.objects.filter(profile=profile).count()
        ms = ProgressionMilestone.objects.filter(profile=profile).count()
        ContractXPGrant.objects.filter(profile=profile).delete()
        ProgressionMilestone.objects.filter(profile=profile).delete()
        EarnedContract.objects.filter(profile=profile).update(platinum_accepted_at=None, full_accepted_at=None)
        contract_service.recompute_profile_job_xp(profile)   # empty ledger -> cache zeroed to the level-1 floor
        self.stdout.write(f"  full reset: removed {grants} grant(s) + {ms} milestone(s); all XP zeroed.")

    def _reset_one(self, profile, slug):
        ec = EarnedContract.objects.filter(profile=profile, contract__slug=slug).first()
        if ec is None:
            raise CommandError(f"No EarnedContract for '{slug}' on {profile.psn_username} (never reached).")
        n = ContractXPGrant.objects.filter(profile=profile, earned_contract=ec).count()
        ContractXPGrant.objects.filter(profile=profile, earned_contract=ec).delete()
        ec.platinum_accepted_at = None
        ec.full_accepted_at = None
        ec.save(update_fields=['platinum_accepted_at', 'full_accepted_at'])
        contract_service.recompute_profile_job_xp(profile)   # rebuild cache from the remaining ledger
        self.stdout.write(f"  un-accepted '{slug}': removed {n} grant(s) (milestones left intact).")
