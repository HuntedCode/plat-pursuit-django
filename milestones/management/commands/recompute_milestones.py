"""Recompute milestone progress + awards.

- No args: sweep ALL linked profiles (the nightly cron + the launch backfill), then drift-correct every
  tier's `earned_count`. Discord roles are NOT reconciled unless `--reconcile-discord` is passed.
- `--reconcile-discord`: also reconcile each profile's Discord roles. This is the **cutover batch** (grant
  everyone the roles they've already earned) and the **periodic safety-net** the design calls for.
- `--profile <psn_username>`: sweep a single profile, reconciling its Discord roles.
- `--reset`: WIPE earned milestones (EarnedMilestoneTier + UserMilestone) before recomputing, for a clean
  re-award off the current metric definitions (scoped to `--profile` if given, else ALL). Prompts unless
  `--yes`. Pair with `--reconcile-discord` to also drop Discord roles that are no longer earned.
- `--milestone <slug>`: narrow `--reset` to ONE ladder (e.g. after a single metric changes). The recompute
  still runs the full per-profile sweep (idempotent for the untouched ladders); only the wipe is scoped.

Whale-safe: each profile's sweep is a handful of bounded aggregates; profiles are streamed with `.iterator()`.
"""
from django.core.management.base import BaseCommand, CommandError

from milestones.services import recompute_milestones, recompute_tier_earned_counts, refresh_total_hunters


class Command(BaseCommand):
    help = "Recompute milestone tiers + progress for all linked profiles (or one via --profile)."

    def add_arguments(self, parser):
        parser.add_argument('--profile', help="Recompute a single profile by PSN username (reconciles Discord).")
        parser.add_argument('--reconcile-discord', action='store_true',
                            help="Also reconcile each profile's Discord roles (cutover / periodic safety-net).")
        parser.add_argument('--reset', action='store_true',
                            help="Wipe earned milestones (EarnedMilestoneTier + UserMilestone) BEFORE recomputing, "
                                 "for a clean re-award. Scoped to --profile if given, else ALL profiles.")
        parser.add_argument('--milestone', help="Narrow --reset to a single milestone ladder (by slug).")
        parser.add_argument('--yes', action='store_true', help="Skip the --reset confirmation prompt.")

    def handle(self, *args, **options):
        from trophies.models import Profile
        from milestones.models import EarnedMilestoneTier, Milestone, MilestoneTier, UserMilestone

        username = options.get('profile')
        reset = options.get('reset', False)
        slug = options.get('milestone')

        if slug:
            if not reset:
                raise CommandError("--milestone only applies with --reset.")
            if not Milestone.objects.filter(slug=slug).exists():
                raise CommandError(f"No milestone with slug {slug!r}.")

        def _scope_wipe(qs, path):
            """Narrow a wipe queryset to the chosen milestone ladder, when --milestone is given."""
            return qs.filter(**{path: slug}) if slug else qs

        if reset and not options.get('yes'):
            scope = f"profile {username!r}" if username else "ALL profiles"
            scope += f", milestone {slug!r}" if slug else " (all milestones)"
            if input(f"Wipe earned milestones for {scope} and recompute? [y/N] ").strip().lower() not in ('y', 'yes'):
                self.stdout.write("Aborted.")
                return

        label = f" [{slug}]" if slug else ""

        if username:
            profile = Profile.objects.filter(psn_username__iexact=username).first()
            if not profile:
                raise CommandError(f"No profile with psn_username {username!r}.")
            if reset:
                _scope_wipe(EarnedMilestoneTier.objects.filter(profile=profile), 'tier__milestone__slug').delete()
                _scope_wipe(UserMilestone.objects.filter(profile=profile), 'milestone__slug').delete()
                self.stdout.write(self.style.WARNING(f"Wiped {profile.psn_username}{label}: earned + progress records removed."))
            newly = recompute_milestones(profile, reconcile_discord=True)
            if reset:
                recompute_tier_earned_counts()   # the wiped re-award double-bumps earned_count; recompute it from truth
                refresh_total_hunters()
            self.stdout.write(self.style.SUCCESS(f"{profile.psn_username}: {len(newly)} new tier(s) awarded."))
            return

        if reset:
            _scope_wipe(EarnedMilestoneTier.objects.all(), 'tier__milestone__slug').delete()
            _scope_wipe(UserMilestone.objects.all(), 'milestone__slug').delete()
            _scope_wipe(MilestoneTier.objects.all(), 'milestone__slug').update(earned_count=0)   # re-bumped from 0 by the sweep
            self.stdout.write(self.style.WARNING(f"Wiped ALL earned milestones + progress{label}."))

        reconcile = options.get('reconcile_discord', False)
        swept = awarded = 0
        # REGISTERED members only (user__isnull=False) -- milestones are for site accounts, not synced/scouted
        # profiles (which would also skew the rarity denominator). No .only(): two metrics read denormalized
        # Profile columns (total_trophies/total_completes) + a metric reads profile.user; deferring would cause
        # a per-profile refetch N+1. Profile is one narrow row.
        for profile in Profile.objects.filter(user__isnull=False).select_related('user').iterator(chunk_size=500):
            newly = recompute_milestones(profile, reconcile_discord=reconcile)
            swept += 1
            awarded += len(newly)
            if swept % 1000 == 0:
                self.stdout.write(f"  …{swept} profiles swept")

        drift = recompute_tier_earned_counts()
        hunters = refresh_total_hunters()   # refresh the rarity denominator
        self.stdout.write(self.style.SUCCESS(
            f"Swept {swept} profiles, awarded {awarded} new tier(s); "
            f"corrected earned_count on {drift} tier(s); rarity denominator = {hunters} hunters."
            + (" Reconciled Discord roles." if reconcile else "")
        ))
        if reset and not reconcile:
            self.stdout.write(self.style.NOTICE(
                "Note: Discord roles were NOT reconciled. Re-run with --reconcile-discord to drop roles that "
                "are no longer earned after the reset."
            ))
