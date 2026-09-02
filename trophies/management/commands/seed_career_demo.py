"""Seed a dev profile with demo Contracts, banked XP, and tier/rank milestones so the Career
ladders + journey and the Contracts board can be viewed populated on a dev server.

    python manage.py seed_career_demo --user <psn_username> --reset   # re-runnable (removes prior seed first)
    python manage.py seed_career_demo --user <psn_username>           # additive (XP stacks -- use --reset to re-run)

DEV-ONLY. It creates Concepts/Games/Contracts prefixed 'demo-'/'DEMO' and grants XP from
source='seed'. --reset removes ONLY those (prefix + source scoped) and rebuilds the XP cache from
the remaining real ledger, so real levels/rank/milestones are never destroyed. Milestones are
logged through the real engine (grant_job_xp + the rank detector), so this exercises the same path
a live accept would. Because plain grants are additive, re-run with --reset (not bare).
"""
from datetime import timedelta

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from trophies.models import (
    Concept, Contract, ContractXPGrant, EarnedContract, EarnedTrophy,
    Game, IGDBMatch, Job, Profile, ProfileGame, ProfileJobXP, ProgressionMilestone, Trophy,
)
from trophies.services import contract_service
from trophies.util_modules.constants import JOB_XP_PER_LEVEL

DEMO_PREFIX = 'demo-'
DEMO_NP = 'DEMO'
# Target job levels for the ladder demo: a couple Expert (50), some Adept (25), Apprentice (10), etc.
_LADDER_TARGETS = [50, 40, 27, 18, 12, 8, 5, 3]


class Command(BaseCommand):
    help = "Seed a dev profile's Career with demo Contracts + banked XP + tier milestones (--user <psn>)."

    def add_arguments(self, parser):
        parser.add_argument('--user', required=True, help='psn_username of the dev profile to seed.')
        parser.add_argument('--reset', action='store_true', help='Clear prior demo data + the profile XP first.')

    @transaction.atomic
    def handle(self, *args, **opts):
        try:
            profile = Profile.objects.get(psn_username=opts['user'])
        except Profile.DoesNotExist:
            raise CommandError(f"No profile with psn_username '{opts['user']}'.")
        jobs = list(Job.objects.all().order_by('display_order'))
        if len(jobs) < 12:
            raise CommandError("Job catalog not seeded (need the 25 jobs first).")

        if opts['reset']:
            self._reset(profile)

        existing_ms = set(ProgressionMilestone.objects.filter(profile=profile).values_list('id', flat=True))
        self._grant_ladder_xp(profile, jobs)
        n = self._seed_board(profile, jobs)
        self._spread_dates(profile, existing_ms)   # give the NEW milestones a believable date spread
        self.stdout.write(self.style.SUCCESS(
            f"Seeded {profile.psn_username}: banked XP across {len(_LADDER_TARGETS)} jobs, "
            f"{ProgressionMilestone.objects.filter(profile=profile).count()} milestones, {n} demo Contracts."
        ))

    def _reset(self, profile):
        """Undo a prior seed WITHOUT touching real data: demo objects are prefix-scoped and the seed
        ledger is source-scoped, so removing them + rebuilding the XP cache from the REMAINING (real)
        ledger restores real levels. ProgressionMilestone rows are left intact -- they're forward-only
        history, idempotent on re-seed (get_or_create), and can't be scoped to seed activity, so a
        profile-wide wipe would destroy real journey data."""
        EarnedContract.objects.filter(profile=profile, contract__slug__startswith=DEMO_PREFIX).delete()
        Contract.objects.filter(slug__startswith=DEMO_PREFIX).delete()
        Game.objects.filter(np_communication_id__startswith=DEMO_NP).delete()     # cascades Trophy/ProfileGame/EarnedTrophy
        Concept.objects.filter(concept_id__startswith=DEMO_NP).delete()           # cascades IGDBMatch (OneToOne)
        ContractXPGrant.objects.filter(profile=profile, source='seed').delete()
        contract_service.recompute_profile_job_xp(profile)   # rebuild the cache from the real ledger
        self.stdout.write("  reset prior demo data (real XP + milestones preserved).")

    def _spread_dates(self, profile, existing_ids):
        """Spread the newly-created milestones' reached_at across the last ~120 days (oldest = lowest
        tiers) so the journey demo isn't all one timestamp. Only touches milestones this run created --
        pre-existing (possibly real) rows keep their dates."""
        new = list(
            ProgressionMilestone.objects.filter(profile=profile).exclude(id__in=existing_ids)
            .order_by('kind', 'level_at')
        )
        if len(new) < 2:
            return
        now = timezone.now()
        span = timedelta(days=120)
        last = len(new) - 1
        for i, m in enumerate(new):
            m.reached_at = now - span * (last - i) / last   # i=0 (lowest) -> 120d ago, last -> now
            m.save(update_fields=['reached_at'])

    def _grant_ladder_xp(self, profile, jobs):
        """Grant a concentrated first-claim burst so several jobs climb through prestige tiers and the
        Pursuer rank climbs -- then one small later burst (unflagged) so the journey shows both states."""
        old_pl = contract_service._pursuer_level(profile)
        for job, lvl in zip(jobs, _LADDER_TARGETS):
            xp = (lvl - 1) * JOB_XP_PER_LEVEL   # cumulative XP to BE at `lvl`
            if xp > 0:
                contract_service.grant_job_xp(profile, job, xp, source='seed', first_claim=True)
        contract_service._log_rank_milestones(
            profile, old_pl, contract_service._pursuer_level(profile), first_claim=True)
        # A later, organic (non-first-claim) crossing: push a fresh job to Apprentice.
        contract_service.grant_job_xp(profile, jobs[len(_LADDER_TARGETS)], 11 * JOB_XP_PER_LEVEL,
                                      source='seed', first_claim=False)

    def _seed_board(self, profile, jobs):
        """Demo Contracts in mixed board states so the Contracts tab shows every card variant."""
        specs = [('claimed', 3), ('claimable', 3), ('pursuing', 2), ('available', 2)]
        idx = 0
        for state, count in specs:
            for _ in range(count):
                idx += 1
                demo_igdb = 990000 + idx
                # Anchored + trusted-matched concept keyed to the Contract's igdb_id, so membership
                # derives the same way it does in prod (no ContractMembership rows anymore).
                concept = Concept.objects.create(
                    concept_id=f'{DEMO_NP}{idx:04d}', unified_title=f'Demo Game {idx}',
                    anchor_migration_completed_at=timezone.now())
                IGDBMatch.objects.create(
                    concept=concept, igdb_id=demo_igdb, status='auto_accepted',
                    igdb_name=f'Demo Game {idx}')
                game = Game.objects.create(
                    title_name=f'Demo Game {idx}', np_communication_id=f'{DEMO_NP}{idx:05d}_00',
                    concept=concept, title_platform=['PS5'])
                plat = Trophy.objects.create(
                    game=game, trophy_id=1, trophy_type='platinum', trophy_name='Platinum')
                contract = Contract.objects.create(
                    name=f'Demo Game {idx}', slug=f'{DEMO_PREFIX}{idx}', is_live=True,
                    igdb_id=demo_igdb)
                contract.jobs.set([jobs[(idx + k) % len(jobs)] for k in range(3)])
                if state in ('claimed', 'claimable'):
                    ProfileGame.objects.create(profile=profile, game=game, progress=100,
                                               has_plat=True, most_recent_trophy_date=timezone.now())
                    EarnedTrophy.objects.create(profile=profile, trophy=plat, earned=True,
                                                earned_date_time=timezone.now())
                    contract_service.mark_contract_reached(profile, contract)
                    if state == 'claimed':
                        contract_service.accept_contract(profile, contract, first_claim=False)
                elif state == 'pursuing':
                    ProfileGame.objects.create(profile=profile, game=game, progress=60,
                                               has_plat=False, most_recent_trophy_date=timezone.now())
        return idx
