"""Recompute milestone progress + awards.

- No args: sweep ALL linked profiles (the nightly cron + the launch backfill), then drift-correct every
  tier's `earned_count`. Discord roles are NOT mass-reconciled here (avoids flooding the bot); real-time
  crossings + the Discord-link trigger + a targeted `--profile` run cover role assignment.
- `--profile <psn_username>`: sweep a single profile, reconciling its Discord roles.

Whale-safe: each profile's sweep is a handful of bounded aggregates; profiles are streamed with `.iterator()`.
"""
from django.core.management.base import BaseCommand, CommandError

from milestones.services import recompute_milestones, recompute_tier_earned_counts


class Command(BaseCommand):
    help = "Recompute milestone tiers + progress for all linked profiles (or one via --profile)."

    def add_arguments(self, parser):
        parser.add_argument('--profile', help="Recompute a single profile by PSN username (reconciles Discord).")

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

        swept = awarded = 0
        qs = Profile.objects.filter(is_linked=True).only('id').iterator(chunk_size=500)
        for profile in qs:
            newly = recompute_milestones(profile, reconcile_discord=False)
            swept += 1
            awarded += len(newly)
            if swept % 1000 == 0:
                self.stdout.write(f"  …{swept} profiles swept")

        drift = recompute_tier_earned_counts()
        self.stdout.write(self.style.SUCCESS(
            f"Swept {swept} profiles, awarded {awarded} new tier(s); "
            f"corrected earned_count on {drift} tier(s)."
        ))
