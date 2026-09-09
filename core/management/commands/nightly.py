"""The nightly maintenance run, in dependency order, as ONE cron entry.

WHY THIS EXISTS: the nightly work has real ordering constraints, and they used to be expressed as
wall-clock spacing in the Render dashboard -- `evaluate_badges --all` at 04:00, `detect_dlc_and_refresh`
at 04:30. Thirty minutes is a guess. `evaluate_badges --all` walks every linked profile, so when it
outgrows that gap the two overlap, and then two processes call `recompute_standing` for the same profiles
while two `recompute_standing` calls raced for the same profile. (That one now takes a per-profile lock,
so a race serializes rather than corrupting -- but two full passes over 300,000 profiles serializing is
not a thing to leave scheduled.)

Sequence is the point: `detect_dlc_and_refresh` re-evaluates series whose games gained DLC, so it must
follow the evaluation rather than overlap it.

WHAT THIS IS NOT: a single monolithic command. Each step is isolated -- one failing step is logged and
the rest still run, because "the DLC sweep failed" should not also cost you the coverage email. Steps
report individually and the command exits non-zero if any failed, so per-step alerting still works off
the logs and the exit code still fails the cron loudly.

Every step remains independently runnable by hand; this only fixes their ORDER and their number of cron
entries. Add a step here rather than adding a nightly cron entry.
"""
import time

from django.core.management import call_command
from django.core.management.base import BaseCommand


#: (label, command, kwargs). Order is a DEPENDENCY order, not a preference:
#:   1. update_shovelware      re-evaluates which games are flagged. FIRST -- see below
#:   2. recompute_clean_standings rebuilds the Shovelware Free board's store from those flags, so it MUST
#:      follow step 1
#:   3. recompute_rarity_standings rebuilds the Rarity Score board. NO dependency on anything above it: it reads
#:      `Trophy.trophy_earn_rate`, PSN's own figure written during SYNC, not our `Trophy.earn_rate`.
#:      Placed beside the other board rebuild because they are the same kind of work, not because it
#:      needs to follow one.
#:   4. evaluate_badges --all  writes SeriesBadgeStanding / ProfileBadgeStanding / ProfileEditionStanding
#:   5. detect_dlc_and_refresh re-evaluates series whose games gained DLC (writes the same tables) AND
#:      rewrites ProfileGame.progress for the affected games, dropping owners back below 100%
#:   6. process_contracts --all reads ProfileGame.progress, so it MUST follow the DLC sweep or it would
#:      stamp contract reaches that step 5 is about to invalidate
#:   7. recompute_milestones reads badge standings, ProfileJobXP and the profile counters, so it is last
#:      among the writers
#:   8. audit_badge_coverage   read-only report; last because it is the least urgent
#:
#: STEPS 1 AND 2 MOVED HERE (2026-09) from `update_shovelware`'s own 04:00 Render entry -- the same slot
#: this command runs in, so the two overlapped and the order between them was undefined. Folding them in
#: is what makes the dependency real: `recompute_clean_standings` rebuilds the Shovelware Free board's
#: store from the flags `update_shovelware` writes, and a recompute that wins that race rebuilds the
#: board from YESTERDAY's catalogue -- silently, and plausibly.
#:
#: WHY SHOVELWARE LEADS rather than sitting beside the recompute further down. `evaluate_contract_
#: candidates` runs on its own Render entry at 04:45 and reads these same flags ("MUST run after
#: update_shovelware"), which today is safe because update_shovelware STARTS at 04:00. Putting it
#: anywhere but first in this chain would push its start behind `evaluate_badges --all`'s pass over
#: every profile and quietly break that 45-minute assumption. First preserves the existing start time
#: exactly; nothing earlier in this chain needed to precede it.
#:
#: That 04:45 job is still ordered by wall clock, which is the thing this command exists to stop. Folding
#: it in too is the right end state and is left as the next bite of the standing FOLLOW-UP in
#: docs/guides/cron-jobs.md, rather than widening a leaderboard branch into the contracts pipeline.
#:
#: Steps 6 and 7 are the DRIFT NETS, and they are the reason this list is not just the badge chain.
#: Sync only evaluates what a sync TOUCHED, so anything authored after a hunter last touched the relevant
#: game is invisible to them forever without a sweep. `evaluate_badges --all` has always been badges'
#: net; contracts and milestones had none. A Contract published for a game 10,000 hunters already
#: platinumed reached exactly zero of them until this ran.
#:
#: Step 6 runs INCREMENTAL. A full contract sweep is O(contracts x candidates) and, stacked on step 4's
#: pass over every profile, put this chain past any plausible window. Incremental sweeps only Contracts
#: whose `updated_at` moved since the last run -- usually none -- and still forces a full pass weekly,
#: because a Contract's membership is derived from IGDB matches and can change without the row being
#: touched. Nightly cost is near zero; the weekly pass is the real net.
STEPS = [
    ('shovelware detection', 'update_shovelware', {}),
    ('clean standings', 'recompute_clean_standings', {}),
    ('pp standings', 'recompute_rarity_standings', {}),
    ('badge evaluation', 'evaluate_badges', {'all': True}),
    ('DLC detection', 'detect_dlc_and_refresh', {}),
    ('contract detection', 'process_contracts', {'all_profiles': True, 'incremental': True}),
    ('milestone recompute', 'recompute_milestones', {}),
    ('badge coverage audit', 'audit_badge_coverage', {}),
]


#: {dependent label: the label it reads from}. Failures are ISOLATED by default, which is right when the
#: steps are independent -- losing a whole night because one raised is the worse outcome. This is the
#: exception: `clean standings` rebuilds the Shovelware Free board from the flags `shovelware detection`
#: writes, and that command applies them PER GAME. A mid-sweep failure leaves a HALF-APPLIED catalogue, so
#: recomputing over it materializes a board from a flag set that never existed as a consistent snapshot.
#: Rebuilding over YESTERDAY's flags would be fine and self-heals the next night; rebuilding over half of
#: today's does not announce itself and does not self-correct in any bounded way.
#:
#: Kept beside STEPS rather than as a fourth tuple element so the (label, command, kwargs) shape every
#: other reader unpacks stays intact.
DEPENDS_ON = {
    'clean standings': 'shovelware detection',
}


class Command(BaseCommand):
    help = "Run the nightly maintenance steps in dependency order. One cron entry, isolated failures."

    def add_arguments(self, parser):
        parser.add_argument('--only', help="Run a single step by label, e.g. --only 'DLC detection'.")
        parser.add_argument('--skip', action='append', default=[],
                            help='Skip a step by label. Repeatable.')
        parser.add_argument('--dry-run', action='store_true',
                            help='List the steps in order without running them.')

    def handle(self, *args, **opts):
        steps = STEPS
        if opts['only']:
            steps = [s for s in STEPS if s[0] == opts['only']]
            if not steps:
                self.stderr.write(f"Unknown step {opts['only']!r}. Known: {[s[0] for s in STEPS]}")
                return
        if opts['skip']:
            steps = [s for s in steps if s[0] not in opts['skip']]

        if opts['dry_run']:
            self.stdout.write('Would run, in this order:')
            for i, (label, command, kwargs) in enumerate(steps, 1):
                flags = ' '.join(f'--{k}' for k in kwargs) or ''
                self.stdout.write(f'  {i}. {label}  ({command} {flags})'.rstrip())
            return

        failed = []
        skipped = []
        for label, command, kwargs in steps:
            depends_on = DEPENDS_ON.get(label)
            if depends_on and depends_on in failed:
                skipped.append(label)
                self.stderr.write(self.style.WARNING(
                    f'{label} SKIPPED: it reads what {depends_on!r} writes, and that failed. Rebuilding '
                    f'from a half-applied result is worse than not rebuilding.'
                ))
                continue
            started = time.monotonic()
            self.stdout.write(f'--- {label} ---')
            try:
                call_command(command, **kwargs)
            except Exception as exc:
                # Isolated deliberately: a later step is usually independent of an earlier failure, and
                # losing the whole night's maintenance because one step raised is the worse outcome. The
                # exit code below still makes the run fail loudly.
                failed.append(label)
                self.stderr.write(self.style.ERROR(f'{label} FAILED: {exc!r}'))
            else:
                self.stdout.write(self.style.SUCCESS(
                    f'{label} ok ({time.monotonic() - started:.1f}s)'
                ))

        if failed:
            # Non-zero so the cron platform reports a failed run rather than a green one with an error
            # buried in the logs. A SKIPPED step counts here too: it did not run, and the reason it did
            # not is a failure -- reporting green would say the board was rebuilt when it was not.
            tail = f" ({len(skipped)} skipped: {', '.join(skipped)})" if skipped else ''
            raise SystemExit(
                f"nightly: {len(failed)} step(s) failed: {', '.join(failed)}{tail}")
        self.stdout.write(self.style.SUCCESS(f'nightly: all {len(steps)} steps ok'))
