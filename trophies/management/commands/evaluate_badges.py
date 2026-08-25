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
from trophies.services.badge_orchestrator import (
    build_catalog, evaluate_with_catalog, recompute_required_stages, resolve_group_badges,
)

# diff action -> the apply-summary key, so dry-run and write share one totals dict.
_ACTION_TOTAL = {'award': 'awarded', 'revoke': 'revoked', 'update': 'updated'}


class Command(BaseCommand):
    help = "Evaluate a profile's grouping badges and apply earns (or --dry-run to preview)."

    def add_arguments(self, parser):
        parser.add_argument('username', nargs='?', help="psn_username to evaluate.")
        # "every profile with a PSN username", NOT "every LINKED profile", which is what this said and did
        # not do. The distinction is ~300,000 rows against ~50,000: badges are evaluated for scraped
        # profiles too, so an unlinked hunter's own page shows their badges. The BOARDS gate that
        # population back out at read time (`badge_leaderboards._linked`); this does not.
        parser.add_argument('--all', action='store_true',
                            help="Evaluate every profile with a PSN username (batch), linked or not.")
        parser.add_argument('--series', help="Scope to one series_slug's LIVE group badges.")
        parser.add_argument('--include-dormant', action='store_true',
                            help="With --series, also evaluate unreleased editions. Requires --dry-run.")
        parser.add_argument('--dry-run', action='store_true', help="Preview the changes; write nothing.")
        parser.add_argument('--compare-legacy', action='store_true',
                            help="Read-only: report kept/lost/gained recognition vs legacy tier badges. Pair with --series or --all.")

    def handle(self, *args, **opts):
        if opts['compare_legacy']:
            self._compare_legacy(opts)
            return

        dry = opts['dry_run']

        # Resolve which group badges to evaluate: a specific series, or the default live set.
        #
        # LIVE-ONLY on a write run, `--include-dormant` notwithstanding. This scope used to include dormant
        # editions unconditionally "for testing", and a write run through it did two things nothing undoes:
        #
        #   1. It wrote XP for an unreleased edition. `_live_standings` gates the profile-wide sum at
        #      SERIES level, so a series with one live edition and one dormant one had the dormant one's XP
        #      counted in the holder's Badge Points -- points from a badge no reader can see.
        #   2. It awarded `UserGroupBadge` rows for dormant editions that the nightly `evaluate_badges
        #      --all` can never revoke, because that run is live-scoped and never revisits them. A hold
        #      created here outlives the state that justified it.
        #
        # Previewing a dormant edition is still legitimate -- it is how a curator checks a badge before
        # launching it -- so `--include-dormant` keeps that, gated to `--dry-run` where neither hazard
        # exists. Releasing a badge is what makes it evaluable, which is the same rule `is_live` already
        # expresses everywhere else.
        if opts['include_dormant'] and not (opts['series'] and dry):
            self.stderr.write("--include-dormant requires --series and --dry-run (it must never write).")
            return

        group_badges = None
        if opts['series']:
            qs = GroupBadge.objects.filter(series__series_slug=opts['series'])
            if not opts['include_dormant']:
                qs = qs.filter(is_live=True)
            group_badges = list(qs.select_related('series', 'platform_group'))
            if not group_badges:
                dormant = GroupBadge.objects.filter(series__series_slug=opts['series']).exists()
                self.stderr.write(
                    f"No LIVE group badges for series '{opts['series']}'; its editions are all dormant. "
                    f"Preview them with --dry-run --include-dormant, or release one first."
                    if dormant else
                    f"No group badges for series '{opts['series']}' (run convert_series_to_groups first)."
                )
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
            # The catalog-level denorm, refreshed before the per-profile work. `required_stages` is a pure
            # function of the stage graph and the group's platform routing -- no profile involved -- so it
            # belongs to a catalog pass, not to apply_changes (which only visits badges whose HELD state
            # changed and would leave every unchanged badge at its default 0 forever). Browse Badges reads
            # it as the medallion's "X / Y" count, and a 0 hides that count entirely rather than showing
            # "0 / 0", which is why it went unnoticed.
            # Scoped to catalogue-wide runs. `evaluate_badges <one-username>` resolves group_badges to
            # ALL live badges, so without this a single-profile debug run silently rewrote
            # required_stages across the whole catalogue -- a global write side effect from a local
            # command, which is not a thing a debug run should do.
            resolved = resolve_group_badges(group_badges)
            catalog = None
            if opts['all'] or opts['series']:
                # `--all` is the ONE caller that sweeps the whole live catalogue across every profile,
                # which is the regime the subquery filter shape exists for. `--series` is scoped, so it
                # takes the same shape as a sync or a page render.
                catalog = build_catalog(resolved, whole_catalogue=bool(opts['all']))
                changed = recompute_required_stages(catalog)
                if changed:
                    self.stdout.write(f"required_stages refreshed on {changed} group badge(s).")

            # Batch write: awards are stamped in completion-date order, so earn_rank reflects who
            # finished first. Both the resolved list AND the catalog are handed over, so neither the
            # resolve nor the six-query catalog build happens a second time.
            totals = evaluate_and_apply_batch(profiles, resolved, catalog=catalog)

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
