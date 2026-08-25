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
#:   1. evaluate_badges --all  writes SeriesBadgeStanding / ProfileBadgeStanding / ProfileEditionStanding
#:   2. detect_dlc_and_refresh re-evaluates series whose games gained DLC (writes the same tables) AND
#:      rewrites ProfileGame.progress for the affected games, dropping owners back below 100%
#:   3. process_contracts --all reads ProfileGame.progress, so it MUST follow the DLC sweep or it would
#:      stamp contract reaches that step 2 is about to invalidate
#:   4. recompute_milestones reads badge standings, ProfileJobXP and the profile counters, so it is last
#:      among the writers
#:   5. audit_badge_coverage   read-only report; last because it is the least urgent
#:
#: Steps 3 and 4 are the DRIFT NETS, and they are the reason this list is not just the badge chain.
#: Sync only evaluates what a sync TOUCHED, so anything authored after a hunter last touched the relevant
#: game is invisible to them forever without a sweep. `evaluate_badges --all` has always been badges'
#: net; contracts and milestones had none. A Contract published for a game 10,000 hunters already
#: platinumed reached exactly zero of them until this ran.
STEPS = [
    ('badge evaluation', 'evaluate_badges', {'all': True}),
    ('DLC detection', 'detect_dlc_and_refresh', {}),
    ('contract detection', 'process_contracts', {'all': True}),
    ('milestone recompute', 'recompute_milestones', {}),
    ('badge coverage audit', 'audit_badge_coverage', {}),
]


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
        for label, command, kwargs in steps:
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
            # buried in the logs.
            raise SystemExit(f"nightly: {len(failed)} step(s) failed: {', '.join(failed)}")
        self.stdout.write(self.style.SUCCESS(f'nightly: all {len(steps)} steps ok'))
