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
import logging
from datetime import timedelta

from django.core.management.base import BaseCommand
from django.db.models import Q
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from trophies.models import (
    Contract, EarnedContract, EarnedTrophy, Profile, ProfileGame,
)
from trophies.services.contract_service import _detect_tiers, mark_contract_reached
from trophies.util_modules.cache import redis_client

logger = logging.getLogger(__name__)


#: How often the incremental mode still does a FULL pass. A Contract's own `updated_at` does not move
#: when its MEMBERSHIP changes: members are derived from IGDB matches (`member_concept_ids`), which the
#: sync's igdb_enrich phase writes. So a concept anchored today can join an untouched Contract, and only
#: a full pass will see it. Weekly keeps the nightly cost near zero without letting that case rot.
FULL_SWEEP_INTERVAL = timedelta(days=7)
WATERMARK_KEY = 'contract_detection:last_run'
FULL_WATERMARK_KEY = 'contract_detection:last_full_run'


class Command(BaseCommand):
    help = "Backfill Contract reach-detection from existing completion data (--user <psn_username> or --all)."

    def add_arguments(self, parser):
        parser.add_argument('--user', type=str, help='psn_username of a single profile to process.')
        parser.add_argument('--all', action='store_true', dest='all_profiles', help='Process every eligible profile.')
        parser.add_argument('--dry-run', action='store_true', help='Report what would change; write nothing.')
        parser.add_argument(
            '--incremental', action='store_true',
            help='With --all: sweep only Contracts changed since the last run, plus a full pass if '
                 'the last full pass is older than the refresh interval. This is the nightly mode.',
        )

    def handle(self, *args, **options):
        username = options.get('user')
        dry_run = options.get('dry_run', False)
        if dry_run:
            self.stdout.write(self.style.WARNING("DRY RUN -- no changes will be written.\n"))

        # Member concepts are igdb-derived (no stored `memberships` relation to prefetch);
        # member_concept_ids() resolves them per contract. Only the episodic bundles prefetch.
        live = Contract.objects.filter(is_live=True).prefetch_related('bundles__concepts').order_by('name')

        full_sweep = True
        if options.get('incremental') and options.get('all_profiles') and not username:
            watermark = self._get_watermark()
            full_sweep = watermark is None or (timezone.now() - watermark) >= FULL_SWEEP_INTERVAL
            if not full_sweep:
                live = live.filter(updated_at__gt=watermark)

        contracts = list(live)
        if not contracts:
            # Not an error in incremental mode: no Contract changed since the last run is the normal
            # nightly outcome, and the whole point of the mode.
            msg = "No Contracts changed since the last run."
            if full_sweep:
                self.stderr.write(self.style.ERROR("No live Contracts to process."))
            else:
                self.stdout.write(msg)
                self._set_watermark(timezone.now(), full=False)
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
            scope = 'FULL sweep' if full_sweep else f'incremental ({len(contracts)} changed)'
            self.stdout.write(self.style.MIGRATE_HEADING(f"Contract reach detection: {scope}"))
            self._process_all(contracts, dry_run)
            if options.get('incremental') and not dry_run:
                self._set_watermark(timezone.now(), full=full_sweep)
            return

        self.stderr.write(self.style.ERROR("Provide --user <psn_username> or --all."))

    # -- one account: evaluate every live Contract directly (cheap for a single profile) --

    def _process_single(self, profile, contracts, dry_run):
        tier_marks = 0
        for contract in contracts:
            member_ids = contract.member_concept_ids()
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
            member_ids = contract.member_concept_ids()
            marks = candidates = 0
            for profile in self._candidate_profiles(contract, member_ids):
                candidates += 1
                newly = self._apply(profile, contract, member_ids, dry_run)
                if newly:
                    marks += len(newly)
                    profiles_touched.add(profile.id)
            total_tier_marks += marks
            self.stdout.write(
                f"  {contract.name}: {candidates} candidate(s) -> {marks} new tier mark(s)."
            )
        verb = "would mark" if dry_run else "marked"
        self.stdout.write(self.style.SUCCESS(
            f"Done: {verb} {total_tier_marks} new tier(s) reached across {len(profiles_touched)} account(s)."
        ))

    @staticmethod
    def _candidate_profiles(contract, member_ids):
        """Profiles with completion relevant to this Contract -- the only ones worth running
        detection on. Streams; never materialises the id set in Python.

        THIS USED TO BUILD A PYTHON SET and pass it to `Profile.objects.filter(id__in=ids)`. Two
        things wrong with that. It is the "per-user querysets must DB-aggregate" rule inverted: a
        profile-scaled result pulled into memory. And on psycopg3 (server-side binding) PostgreSQL
        caps a statement at 65,535 parameters, and Django emits one placeholder per element -- so a
        Contract on a widely-platinumed game did not merely run slowly, it raised
        `the number of query arguments cannot exceed 65535`, which `nightly` then swallowed as a
        failed step every night. Subqueries keep the ids on the server, where the count does not
        matter.

        `.only('id')`: `_detect_tiers` and `mark_contract_reached` read nothing else off Profile,
        and hydrating full rows for every candidate was pure waste.
        """
        q = Q(pk__in=[])
        if member_ids:
            q |= Q(pk__in=ProfileGame.objects
                   .filter(game__concept_id__in=member_ids, progress=100)
                   .values('profile_id'))
            q |= Q(pk__in=EarnedTrophy.objects
                   .filter(earned=True, trophy__trophy_type='platinum',
                           trophy__game__concept_id__in=member_ids)
                   .values('profile_id'))
        for bundle in contract.bundles.all():
            # list(...) rather than values_list: `bundles__concepts` is prefetched, and values_list
            # on a related manager issues a fresh query, silently bypassing the prefetch.
            bundle_ids = [c.id for c in bundle.concepts.all()]
            if bundle_ids:
                q |= Q(pk__in=ProfileGame.objects
                       .filter(game__concept_id__in=bundle_ids, progress=100)
                       .values('profile_id'))
        return Profile.objects.filter(q).only('id').iterator(chunk_size=500)

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

    # -- incremental-mode watermarks -------------------------------------------------------------

    @staticmethod
    def _get_watermark():
        """When the last FULL pass ran. Incremental scoping keys off this, not the last run of any
        kind, so a week of incremental runs cannot postpone the full pass indefinitely."""
        try:
            raw = redis_client.get(FULL_WATERMARK_KEY)
        except Exception:
            logger.warning("process_contracts: redis unavailable for watermark read")
            return None            # unreadable watermark -> full sweep, which is the safe direction
        if not raw:
            return None
        parsed = parse_datetime(raw.decode() if isinstance(raw, bytes) else raw)
        return parsed if parsed and timezone.is_aware(parsed) else None

    @staticmethod
    def _set_watermark(when, *, full):
        try:
            redis_client.set(WATERMARK_KEY, when.isoformat())
            if full:
                redis_client.set(FULL_WATERMARK_KEY, when.isoformat())
        except Exception:
            logger.warning("process_contracts: redis unavailable for watermark write")
