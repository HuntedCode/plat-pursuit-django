"""Backfill Contract reach-detection from EXISTING completion data.

The sync only marks a Contract reached for the games that changed on that sync, so
completions earned before a Contract existed (or before this feature shipped) never get
recognized. This command re-runs reach-detection against each profile's CURRENT
ProfileGame / EarnedTrophy state, so a user's past completions become claimable WITHOUT
them having to re-sync the specific game.

Detection only -- it stamps EarnedContract.*_reached_at (makes the reward claimable) and
grants NO XP. Banking the reward stays a deliberate user action (the acceptance gate);
this never auto-accepts.

    python manage.py process_contracts --user <psn_username>   # one account
    python manage.py process_contracts --all                   # every eligible account
    python manage.py process_contracts --all --dry-run         # preview, write nothing

`--all` is whale-safe: for each live Contract it first finds only the profiles that have
actually completed a member game (a couple of bounded DB queries), then runs the real
engine detection (`mark_contract_reached`) for just those candidates -- it never scans the
whole userbase per Contract.
"""
from django.core.management.base import BaseCommand

from trophies.models import (
    Contract, EarnedContract, EarnedTrophy, Profile, ProfileGame,
)
from trophies.services.contract_service import _detect_tiers, mark_contract_reached


class Command(BaseCommand):
    help = "Backfill Contract reach-detection from existing completion data (--user <psn_username> or --all)."

    def add_arguments(self, parser):
        parser.add_argument('--user', type=str, help='psn_username of a single profile to process.')
        parser.add_argument('--all', action='store_true', dest='all_profiles', help='Process every eligible profile.')
        parser.add_argument('--dry-run', action='store_true', help='Report what would change; write nothing.')

    def handle(self, *args, **options):
        username = options.get('user')
        dry_run = options.get('dry_run', False)
        if dry_run:
            self.stdout.write(self.style.WARNING("DRY RUN -- no changes will be written.\n"))

        contracts = list(
            Contract.objects.filter(is_live=True)
            .prefetch_related('memberships', 'bundles__concepts')
            .order_by('name')
        )
        if not contracts:
            self.stderr.write(self.style.ERROR("No live Contracts to process."))
            return

        if username:
            try:
                profile = Profile.objects.get(psn_username=username)
            except Profile.DoesNotExist:
                self.stderr.write(self.style.ERROR(f"No profile with psn_username '{username}'."))
                return
            self._process_single(profile, contracts, dry_run)
            return

        if options.get('all_profiles'):
            self._process_all(contracts, dry_run)
            return

        self.stderr.write(self.style.ERROR("Provide --user <psn_username> or --all."))

    # -- one account: evaluate every live Contract directly (cheap for a single profile) --

    def _process_single(self, profile, contracts, dry_run):
        tier_marks = 0
        for contract in contracts:
            member_ids = list(contract.memberships.values_list('concept_id', flat=True))
            tier_marks += len(self._apply(profile, contract, member_ids, dry_run))
        verb = "would mark" if dry_run else "marked"
        self.stdout.write(self.style.SUCCESS(
            f"{profile.psn_username}: {verb} {tier_marks} new tier(s) reached across {len(contracts)} live Project(s)."
        ))

    # -- every account: candidate-filter per Contract, then run the real engine detection --

    def _process_all(self, contracts, dry_run):
        profiles_touched = set()
        total_tier_marks = 0
        for contract in contracts:
            member_ids = list(contract.memberships.values_list('concept_id', flat=True))
            candidate_ids = self._candidate_profile_ids(contract, member_ids)
            marks = 0
            if candidate_ids:
                for profile in Profile.objects.filter(id__in=candidate_ids).iterator(chunk_size=500):
                    newly = self._apply(profile, contract, member_ids, dry_run)
                    if newly:
                        marks += len(newly)
                        profiles_touched.add(profile.id)
            total_tier_marks += marks
            self.stdout.write(
                f"  {contract.name}: {len(candidate_ids)} candidate(s) -> {marks} new tier mark(s)."
            )
        verb = "would mark" if dry_run else "marked"
        self.stdout.write(self.style.SUCCESS(
            f"Done: {verb} {total_tier_marks} new tier(s) reached across {len(profiles_touched)} account(s)."
        ))

    @staticmethod
    def _candidate_profile_ids(contract, member_ids):
        """Profile ids that already have completion relevant to this Contract -- the only
        ones worth running detection on. Bounded DB queries, never a full userbase scan."""
        ids = set()
        if member_ids:
            ids |= set(
                ProfileGame.objects
                .filter(game__concept_id__in=member_ids, progress=100)
                .values_list('profile_id', flat=True)
            )
            ids |= set(
                EarnedTrophy.objects
                .filter(earned=True, trophy__trophy_type='platinum', trophy__game__concept_id__in=member_ids)
                .values_list('profile_id', flat=True)
            )
        for bundle in contract.bundles.all():
            bundle_ids = list(bundle.concepts.values_list('id', flat=True))
            if bundle_ids:
                ids |= set(
                    ProfileGame.objects
                    .filter(game__concept_id__in=bundle_ids, progress=100)
                    .values_list('profile_id', flat=True)
                )
        return ids

    @staticmethod
    def _apply(profile, contract, member_ids, dry_run):
        """Tiers newly reached for (profile, contract). Writes via the engine's
        mark_contract_reached unless dry_run. Returns the newly-stamped tier names."""
        platinum_reached, full_reached = _detect_tiers(profile, contract, member_ids)
        if not (platinum_reached or full_reached):
            return []
        ec = EarnedContract.objects.filter(profile=profile, contract=contract).first()
        newly = []
        if platinum_reached and (ec is None or ec.platinum_reached_at is None):
            newly.append('platinum')
        if full_reached and (ec is None or ec.full_reached_at is None):
            newly.append('full')
        if newly and not dry_run:
            mark_contract_reached(profile, contract)
        return newly
