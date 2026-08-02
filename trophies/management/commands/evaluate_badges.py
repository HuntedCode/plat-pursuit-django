"""Evaluate a profile's grouping badges and apply earns -- the manual trigger + verifier.

Since the new engine isn't wired to sync yet, this is how you exercise it: author a badge, run this against a
real profile, and confirm the right UserGroupBadge rows / holo / title appear. `--dry-run` previews
without writing; `--series <slug>` scopes to one series' group badges INCLUDING dormant ones, so you can test a
badge before flipping it live. It reuses badge_apply.plan/evaluate_and_apply -- the same code the eventual sync
wiring, backfill, and reconciliation harness build on.

    python manage.py evaluate_badges TrophyHunter_Jay --dry-run
    python manage.py evaluate_badges TrophyHunter_Jay --series god-of-war --dry-run   # test a dormant badge
    python manage.py evaluate_badges --series god-of-war --dry-run                    # process a badge for
    python manage.py evaluate_badges --series god-of-war                              #   everyone who played it
    python manage.py evaluate_badges --all                                             # batch (all live badges)
    python manage.py evaluate_badges --series god-of-war --compare-legacy             # old-vs-new sanity glance

`--compare-legacy` is the lightweight pre-cutover check (in place of a full reconciliation harness): read-only,
it reports how many profiles KEPT / LOST / GAINED recognition for a series under the new engine vs the old tier
badges, at the SERIES level (held any old tier vs earns any new group). Losses are expected where the reframe
raised the bar (old low tier -> full platform group); the glance just surfaces them to eyeball before flipping.
"""
from collections import Counter

from django.core.management.base import BaseCommand
from django.db.models import Q, Max

from trophies.models import Profile, GroupBadge, BadgeSeries, Game, ProfileGame, UserBadge
from trophies.services.badge_apply import plan, evaluate_and_apply_batch
from trophies.services.badge_orchestrator import build_catalog, evaluate_with_catalog

# diff action -> the apply-summary key, so dry-run and write share one totals dict.
_ACTION_TOTAL = {'award': 'awarded', 'revoke': 'revoked', 'update': 'updated'}


class Command(BaseCommand):
    help = "Evaluate a profile's grouping badges and apply earns (or --dry-run to preview)."

    def add_arguments(self, parser):
        parser.add_argument('username', nargs='?', help="psn_username to evaluate.")
        parser.add_argument('--all', action='store_true', help="Evaluate every linked profile (batch).")
        parser.add_argument('--series', help="Scope to one series_slug's group badges, INCLUDING dormant (for testing).")
        parser.add_argument('--dry-run', action='store_true', help="Preview the changes; write nothing.")
        parser.add_argument('--compare-legacy', action='store_true',
                            help="Read-only: report kept/lost/gained recognition vs legacy tier badges. Pair with --series or --all.")

    def handle(self, *args, **opts):
        if opts['compare_legacy']:
            self._compare_legacy(opts)
            return

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
            profiles = (
                Profile.objects.filter(id__in=self._series_player_ids(opts['series']))
                .only('id', 'psn_username').order_by('psn_username')
            )
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
            f"\n{verb}: {totals['awarded']} awarded, {totals['revoked']} revoked, {totals['updated']} updated."
        ))

    @staticmethod
    def _series_player_ids(slug):
        """Distinct profile ids that have PLAYED any game in the series (standalone or bundle member)."""
        game_ids = Game.objects.filter(
            Q(concept__stages__series_slug=slug) | Q(concept__bundles__stage__series_slug=slug)
        ).values_list('id', flat=True)
        return ProfileGame.objects.filter(game_id__in=game_ids).values_list('profile_id', flat=True).distinct()

    def _compare_legacy(self, opts):
        """Read-only pre-cutover glance: per series, old-vs-new recognition at the series level."""
        if opts['series']:
            slugs = [opts['series']]
        elif opts['all']:
            slugs = list(BadgeSeries.objects.values_list('series_slug', flat=True))
        else:
            self.stderr.write("--compare-legacy needs --series <slug> or --all.")
            return
        for slug in slugs:
            self._compare_series(slug)

    def _compare_series(self, slug):
        group_badges = list(
            GroupBadge.objects.filter(series__series_slug=slug).select_related('series', 'platform_group')
        )
        if not group_badges:
            self.stdout.write(f"  {slug}: skip (no group badges; run convert_series_to_groups first)")
            return
        catalog = build_catalog(group_badges)

        # Old recognition (series-level): held ANY earned legacy tier. Keep each profile's MAX tier for context.
        old = dict(
            UserBadge.objects.filter(badge__series_slug=slug, status='earned')
            .values('profile_id').annotate(t=Max('badge__tier')).values_list('profile_id', 't')
        )
        old_ids = set(old)

        # Evaluate old earners (only they can LOSE) UNION series players (to catch GAINS). Losses are the point.
        population = old_ids | set(self._series_player_ids(slug))
        new_ids = {
            p.id for p in Profile.objects.filter(id__in=population).only('id')
            if any(r.base_earned for r in evaluate_with_catalog(p, catalog).values())
        }

        lost, gained, kept = old_ids - new_ids, new_ids - old_ids, old_ids & new_ids
        self.stdout.write(self.style.MIGRATE_HEADING(
            f"  {slug}: old {len(old_ids)} | new {len(new_ids)} | kept {len(kept)} | lost {len(lost)} | gained {len(gained)}"
        ))
        if lost:
            sample = list(lost)[:15]
            names = dict(Profile.objects.filter(id__in=sample).values_list('id', 'psn_username'))
            self.stdout.write(self.style.WARNING(f"    lost {len(lost)} (showing {len(sample)}); confirm each is an intended bar-raise:"))
            for pid in sample:
                self.stdout.write(f"      {names.get(pid, pid)} (held legacy tier {old.get(pid)})")
