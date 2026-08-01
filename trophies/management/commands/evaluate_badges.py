"""Evaluate a profile's grouping badges and apply earns -- the manual trigger + verifier.

Since the new engine isn't wired to sync yet, this is how you exercise it: author a badge, run this against a
real profile, and confirm the right UserGroupBadge rows / earn_rank / holo / title appear. `--dry-run` previews
without writing; `--series <slug>` scopes to one series' group badges INCLUDING dormant ones, so you can test a
badge before flipping it live. It reuses badge_apply.plan/evaluate_and_apply -- the same code the eventual sync
wiring, backfill, and reconciliation harness build on.

    python manage.py evaluate_badges TrophyHunter_Jay --dry-run
    python manage.py evaluate_badges TrophyHunter_Jay --series god-of-war --dry-run   # test a dormant badge
    python manage.py evaluate_badges --series god-of-war --dry-run                    # process a badge for
    python manage.py evaluate_badges --series god-of-war                              #   everyone who played it
    python manage.py evaluate_badges --all                                             # batch (all live badges)
"""
from collections import Counter

from django.core.management.base import BaseCommand
from django.db.models import Q

from trophies.models import Profile, GroupBadge, Game, ProfileGame
from trophies.services.badge_apply import plan, evaluate_and_apply_batch

# diff action -> the apply-summary key, so dry-run and write share one totals dict.
_ACTION_TOTAL = {'award': 'awarded', 'reactivate': 'reactivated', 'lapse': 'lapsed', 'holo': 'holo_changed'}


class Command(BaseCommand):
    help = "Evaluate a profile's grouping badges and apply earns (or --dry-run to preview)."

    def add_arguments(self, parser):
        parser.add_argument('username', nargs='?', help="psn_username to evaluate.")
        parser.add_argument('--all', action='store_true', help="Evaluate every linked profile (batch).")
        parser.add_argument('--series', help="Scope to one series_slug's group badges, INCLUDING dormant (for testing).")
        parser.add_argument('--dry-run', action='store_true', help="Preview the changes; write nothing.")

    def handle(self, *args, **opts):
        dry = opts['dry_run']

        # Resolve which group badges to evaluate: a specific series (incl. dormant), or the default live set.
        group_badges = None
        if opts['series']:
            group_badges = list(
                GroupBadge.objects.filter(series__series_slug=opts['series']).select_related('series', 'platform_group')
            )
            if not group_badges:
                self.stderr.write(f"No group badges for series '{opts['series']}' (run convert_series_to_groups first).")
                return

        # Resolve profiles.
        if opts['username']:
            profile = Profile.objects.filter(psn_username__iexact=opts['username']).first()
            if not profile:
                self.stderr.write(f"No profile found for '{opts['username']}'.")
                return
            profiles = [profile]
        elif opts['all']:
            # .only(): the batch reads profile.id; dry-run also prints psn_username -- don't hydrate full rows.
            profiles = Profile.objects.exclude(psn_username='').only('id', 'psn_username').order_by('psn_username')
        elif opts['series']:
            # Process this badge for everyone who has PLAYED a game in the series (the "may have progress"
            # set) -- bounded by the series' players, not the whole user base.
            game_ids = Game.objects.filter(
                Q(concept__stages__series_slug=opts['series']) | Q(concept__bundles__stage__series_slug=opts['series'])
            ).values_list('id', flat=True)
            player_ids = ProfileGame.objects.filter(game_id__in=game_ids).values_list('profile_id', flat=True).distinct()
            profiles = Profile.objects.filter(id__in=player_ids).only('id', 'psn_username').order_by('psn_username')
        else:
            self.stderr.write("Provide a psn_username, --series <slug>, or --all.")
            return

        if not isinstance(profiles, list):
            self.stdout.write(f"Evaluating {profiles.count()} profile(s)...")

        totals = Counter()
        if dry:
            for profile in profiles:
                changes, gb_map = plan(profile, group_badges)
                if not changes:
                    continue
                self.stdout.write(f"  {profile.psn_username}:")
                for ch in changes:
                    gb = gb_map[ch.group_badge_id]
                    self.stdout.write(f"    {ch.action}: {gb.series.name} - {gb.platform_group.name}")
                    totals[_ACTION_TOTAL[ch.action]] += 1
        else:
            # Batch write: awards are stamped in completion-date order, so earn_rank reflects who finished first.
            totals = evaluate_and_apply_batch(profiles, group_badges)

        verb = "Would" if dry else "Did"
        self.stdout.write(self.style.SUCCESS(
            f"\n{verb}: {totals['awarded']} awarded, {totals['reactivated']} reactivated, "
            f"{totals['lapsed']} lapsed, {totals['holo_changed']} holo."
        ))
