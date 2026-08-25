"""Reconcile ONE profile's derived state against ground truth. Read-only.

WHY THIS EXISTS: the rebuild ran for months without being exercised against a real sync, because
"did the sync land correctly" had no single answer -- it meant running `recalc_profile_counters
--dry-run`, reading badge standings by hand, checking contracts on the Career page, and eyeballing
milestones, with no one place that said yes or no. Every denormalized value a rebuilt page renders
is written by the sync path or the nightly chain, so a broken writer shows up as a plausible-looking
zero rather than an error. This is the tool that catches that.

    python manage.py verify_profile_sync <psn_username>
    python manage.py verify_profile_sync <psn_username> --verbose   # also list the PASSing checks

WRITES NOTHING, ever. It recomputes each denorm from its source of truth and compares. Exits
non-zero when anything drifted, so it can gate a deploy or a smoke test.

Read it as: run a real sync, then run this. A clean result means every read-model the rebuilt pages
depend on agrees with the trophy data underneath it.
"""
from django.core.management.base import BaseCommand, CommandError
from django.db.models import Count, Q, Sum

from trophies.models import (
    EarnedTrophy, Profile, ProfileGame, SeriesBadgeStanding, UserGroupBadge,
)


class Check:
    """One reconciliation: a name, the stored value, the recomputed truth, and a note on what breaks."""

    __slots__ = ('name', 'stored', 'truth', 'consequence')

    def __init__(self, name, stored, truth, consequence):
        self.name = name
        self.stored = stored
        self.truth = truth
        self.consequence = consequence

    @property
    def ok(self):
        return self.stored == self.truth


class Command(BaseCommand):
    help = "Reconcile one profile's denormalized state against ground truth. Read-only."

    def add_arguments(self, parser):
        parser.add_argument('psn_username')
        parser.add_argument('--verbose', action='store_true',
                            help='List every check, not just the drifted ones.')

    def handle(self, *args, **opts):
        profile = Profile.objects.filter(psn_username__iexact=opts['psn_username']).first()
        if profile is None:
            raise CommandError(f"No profile for '{opts['psn_username']}'.")

        self.stdout.write(self.style.MIGRATE_HEADING(
            f"Reconciling {profile.display_psn_username or profile.psn_username} "
            f"(profile {profile.id}) against ground truth"
        ))

        checks = []
        checks += self._trophy_counters(profile)
        checks += self._library_totals(profile)
        checks += self._badge_standings(profile)
        checks += self._contract_reach(profile)

        drifted = [c for c in checks if not c.ok]
        for c in checks:
            if c.ok and not opts['verbose']:
                continue
            if c.ok:
                self.stdout.write(self.style.SUCCESS(f"  PASS  {c.name}: {c.stored}"))
            else:
                self.stdout.write(self.style.ERROR(
                    f"  DRIFT {c.name}: stored={c.stored} truth={c.truth}"
                ))
                self.stdout.write(f"        {c.consequence}")

        self.stdout.write('')
        if drifted:
            self.stdout.write(self.style.ERROR(
                f"{len(drifted)} of {len(checks)} checks drifted."
            ))
            raise CommandError('profile state does not match ground truth')
        self.stdout.write(self.style.SUCCESS(f"All {len(checks)} checks agree with ground truth."))

    # ── the checks ────────────────────────────────────────────────────────

    def _trophy_counters(self, profile):
        """`Profile.total_bronzes/silvers/golds/plats`, maintained incrementally by the EarnedTrophy
        signals during sync and reconciled nightly. Drift here means a signal did not fire."""
        truth = EarnedTrophy.objects.filter(profile=profile, earned=True).aggregate(
            bronze=Count('id', filter=Q(trophy__trophy_type='bronze')),
            silver=Count('id', filter=Q(trophy__trophy_type='silver')),
            gold=Count('id', filter=Q(trophy__trophy_type='gold')),
            platinum=Count('id', filter=Q(trophy__trophy_type='platinum')),
        )
        pairs = [
            ('total_bronzes', profile.total_bronzes, truth['bronze']),
            ('total_silvers', profile.total_silvers, truth['silver']),
            ('total_golds', profile.total_golds, truth['gold']),
            ('total_plats', profile.total_plats, truth['platinum']),
        ]
        return [
            Check(f'Profile.{name}', stored, real,
                  'The navbar, profile header and every trophy tally read this.')
            for name, stored, real in pairs
        ]

    def _library_totals(self, profile):
        """`total_games` / `total_completes` / `total_trophies`, written ONLY by sync_complete and the
        settings POST. No cron reconciles these, so a missed write persists until the next sync.

        THE FILTERS ARE MIRRORED EXACTLY, and the two writers do not use the same set:
        `update_profile_games` honours `hide_hiddens` alone, while `update_profile_trophy_counts`
        honours `hide_hiddens` AND `hide_zeros`. Reconciling any of them against an unfiltered count
        reports DRIFT on a perfectly healthy profile the moment either toggle is on -- and since this
        exits non-zero on any drift, that made the whole command useless rather than merely noisy.
        A verifier that cries wolf gets ignored, which is worse than not having one.
        """
        games = ProfileGame.objects.filter(profile=profile)
        if profile.hide_hiddens:
            games = games.filter(user_hidden=False)

        trophies = games.exclude(earned_trophies_count=0) if profile.hide_zeros else games

        counts = games.aggregate(
            n=Count('id'),
            complete=Count('id', filter=Q(progress=100)),
        )
        earned = trophies.aggregate(earned=Sum('earned_trophies_count'))['earned'] or 0

        return [
            Check('Profile.total_games', profile.total_games, counts['n'],
                  'The home trophy card and the milestones metrics read this.'),
            Check('Profile.total_completes', profile.total_completes, counts['complete'],
                  'Feeds the full_completions milestone ladder.'),
            Check('Profile.total_trophies', profile.total_trophies, earned,
                  'The profile header, Browse Hunters and the lifetime_trophies ladder read this.'),
        ]

    def _badge_standings(self, profile):
        """The Collection reads `SeriesBadgeStanding.group_progress` and NEVER live-evaluates, so a
        standing that disagrees with the held badges is invisible on the page: it renders confidently
        wrong rather than empty."""
        # SeriesBadgeStanding keys on `series_slug`, not a FK, so both sides join through the slug.
        series_held = set(
            UserGroupBadge.objects.filter(profile=profile)
            .values_list('group_badge__series__series_slug', flat=True)
        )
        series_standing = set(
            SeriesBadgeStanding.objects.filter(profile=profile).values_list('series_slug', flat=True)
        )
        return [
            Check(
                'series with a held badge but no standing row',
                len(series_held - series_standing), 0,
                'Those badges are invisible on the Collection, which reads standings and never '
                'live-evaluates. Fix with `evaluate_badges <username>`; nightly step 1 does it for all.',
            ),
        ]

    def _contract_reach(self, profile):
        """Contracts are stamped reached by sync for TOUCHED games only, and by `process_contracts`
        (nightly step 3) for everything else. A contract whose games are complete but which is not
        stamped is the drift-net gap this command was written to make visible."""
        from trophies.models import Contract, EarnedContract
        from trophies.services.contract_service import _detect_tiers

        stale = 0
        earned = {
            ec.contract_id: ec
            for ec in EarnedContract.objects.filter(profile=profile)
        }
        # Same two calls `mark_contract_reached` makes, minus the write: detection is pure, so
        # running it here tells us what the stamped state SHOULD be without changing anything.
        for contract in Contract.objects.filter(is_live=True).prefetch_related('bundles__concepts'):
            member_ids = contract.member_concept_ids()
            platinum_reached, full_reached = _detect_tiers(profile, contract, member_ids)
            if not (platinum_reached or full_reached):
                continue
            ec = earned.get(contract.id)
            if platinum_reached and not (ec and ec.platinum_reached_at):
                stale += 1
            elif full_reached and not (ec and ec.full_reached_at):
                stale += 1

        return [Check(
            'contracts complete but not marked reachable', stale, 0,
            'These are claimable rewards the hunter cannot see. `process_contracts --all` is '
            'nightly step 3; run it directly to fix.',
        )]
