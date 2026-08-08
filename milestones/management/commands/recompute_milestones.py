"""Recompute milestone progress + awards.

- No args: sweep ALL linked profiles (the nightly cron + the launch backfill), then drift-correct every
  tier's `earned_count`. Discord roles are NOT reconciled unless `--reconcile-discord` is passed.
- `--reconcile-discord`: also reconcile each profile's Discord roles. This is the **cutover batch** (grant
  everyone the roles they've already earned) and the **periodic safety-net** the design calls for.
- `--profile <psn_username>`: sweep a single profile, reconciling its Discord roles.

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

    def handle(self, *args, **options):
        from trophies.models import Profile

        username = options.get('profile')
        if username:
            profile = Profile.objects.filter(psn_username__iexact=username).first()
            if not profile:
                raise CommandError(f"No profile with psn_username {username!r}.")
            newly = recompute_milestones(profile, reconcile_discord=True)
            self.stdout.write(self.style.SUCCESS(
                f"{profile.psn_username}: {len(newly)} new tier(s) awarded."
            ))
            return

        reconcile = options.get('reconcile_discord', False)
        swept = awarded = 0
        # No .only() -- two metrics read denormalized Profile columns (total_trophies/total_completes); deferring
        # them would trigger a per-profile refetch (an N+1 across the whole fleet). Profile is one narrow row.
        for profile in Profile.objects.filter(is_linked=True).iterator(chunk_size=500):
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
