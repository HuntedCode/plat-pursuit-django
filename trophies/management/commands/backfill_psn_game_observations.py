from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from trophies.models import Profile
from trophies.psn_manager import PSNManager


class Command(BaseCommand):
    help = (
        "Queue FORCED-WALK profile refreshes so PSNTitleObservation backfills from real syncs. "
        "Observations are captured during the trophy_titles walk, but a normal refresh takes the "
        "fast path whenever the fingerprint matches and walks nothing -- so an account whose "
        "trophies have not moved would never contribute its library. force_walk makes the "
        "orchestrator walk every title the account owns. Each profile costs only its trophy_titles "
        "pagination (a handful of PSN calls even for a whale); libraries overlap heavily, so a few "
        "large accounts cover most of the catalogue. The table also fills organically from every "
        "normal slow-path sync -- this just front-loads it. DEPLOY ORDER MATTERS: run this only "
        "after the WORKER is on the new build. An old worker drains the forced-walk job but does "
        "not know the flag, so the refresh silently degrades to a normal fingerprint check, "
        "fast-paths, and this command reports success having captured only page 1."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            '--usernames', default='',
            help='Comma-separated psn_usernames to refresh (exact accounts, e.g. scouts).',
        )
        parser.add_argument(
            '--top', type=int, default=0,
            help='Refresh the N synced profiles with the largest libraries. Greedy coverage: '
                 'libraries overlap, so top 10-20 typically covers most of the catalogue.',
        )
        parser.add_argument(
            '--dry-run', action='store_true',
            help='Preview the selection without queueing anything.',
        )
        parser.add_argument(
            '--yes', action='store_true',
            help='Skip the confirmation prompt. Required for non-interactive runs.',
        )

    def handle(self, *args, **options):
        # Same guard the concept sweep learned the hard way: with capture off, every forced walk
        # spends its PSN calls and writes nothing, silently, and a re-run cannot tell.
        if not settings.PSN_METADATA_CAPTURE_ENABLED:
            raise CommandError(
                "PSN_METADATA_CAPTURE_ENABLED is False, so forced walks would capture nothing. "
                "Enable it and re-run. (Strict == 'True' compare: 'true'/'1'/'yes' read as False.)"
            )

        usernames = [u.strip().lower() for u in options['usernames'].split(',') if u.strip()]
        top = options['top']
        if not usernames and not top:
            raise CommandError("Pass --usernames and/or --top N; an empty selection is refused "
                               "rather than defaulting to every profile on the site.")

        selected = {}
        missing = []
        for name in usernames:
            try:
                p = Profile.objects.get(psn_username=name)
                selected[p.id] = p
            except Profile.DoesNotExist:
                missing.append(name)
        if missing:
            raise CommandError(f"No profile for: {', '.join(missing)}")

        if top:
            # total_games is denormalized on Profile and indexed (profile_total_games_idx),
            # maintained on every sync. The GROUP BY over ProfileGame this replaces was a
            # full-table aggregate racing the 60s statement timeout.
            top_qs = (
                Profile.objects.filter(sync_status='synced')
                .order_by('-total_games')[:top]
            )
            for p in top_qs:
                selected.setdefault(p.id, p)

        self.stdout.write(f"Selected {len(selected)} profile(s):")
        for p in selected.values():
            self.stdout.write(f"  {p.psn_username:20} status={p.sync_status}")

        self.stdout.write(self.style.WARNING(
            "Forced walks queue on the ORCHESTRATOR lane -- the same lane real user syncs ride. "
            "Each is cheap (trophy_titles pagination only, no per-game drift work when nothing "
            "changed), but queue tens, not hundreds, per session."
        ))

        if options['dry_run']:
            self.stdout.write(self.style.SUCCESS("[DRY RUN] Nothing queued."))
            return
        if not options['yes'] and not self._confirm():
            self.stdout.write(self.style.ERROR("Operation cancelled."))
            return

        queued, skipped = 0, []
        for p in selected.values():
            # Re-fetched, not the object from before the prompt: a profile that started syncing
            # while the operator read the confirmation would be queued into a silent no-op and
            # reported as done. And ONLY 'synced' qualifies: an 'error' profile routes through
            # initial_sync, which queues args=[] -- force_walk silently dropped while this command
            # counted it as forced. Both were audit findings; both now land in `skipped`.
            p.refresh_from_db(fields=['sync_status'])
            if p.sync_status != 'synced':
                skipped.append(f"{p.psn_username} ({p.sync_status})")
                continue
            PSNManager.profile_refresh(p, force_walk=True)
            queued += 1

        note = f", skipped (not in synced state): {', '.join(skipped)}" if skipped else ""
        self.stdout.write(self.style.SUCCESS(
            f"Queued {queued} forced-walk refresh(es){note}. Observations land as each walk "
            f"drains; run `audit_psn_capture` afterwards to see game-level coverage."
        ))

    def _confirm(self):
        confirm = input("Queue forced-walk refreshes for these profiles? (y/n):").strip().lower()
        return confirm == 'y'
