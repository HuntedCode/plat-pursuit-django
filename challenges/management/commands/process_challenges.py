"""Complete Challenge squares whose contract the owner has finished. The drift net for challenges.

WHY IT EXISTS, given the sync hook already does this. Sync only sees what a sync TOUCHED, so a square
goes unfinished forever in three cases the hook cannot reach:

1. **The contract was published after the hunter finished the game, and they have not synced since.**
   `process_contracts --all` stamps the reach; nothing on the sync path runs to turn it into a square.
   The same hole step 6 of `nightly` closes for contracts, one layer up.
2. **Any other `EarnedContract` row written off the sync path** -- the nightly sweep, a staff
   `process_contracts --contract`, a re-earn after a reconcile. The hook never sees those.

TWO holes, not three. An earlier version listed a revoke-and-re-earn case separately -- that is hole 2
restated, since completion is STICKY and a revoke cannot un-complete a square, leaving a sweep nothing
to redo.

It also claimed a square "can never hold a non-live contract", which is FALSE and is contradicted by
`test_a_draft_contract_still_completes_a_square_it_already_occupies` in this feature's own detection
suite. `assign` refuses a draft and `eligibility._shape` filters `is_live`, but both bind at ASSIGN
time: nothing un-assigns a square when staff later un-publish its contract, and the FK is `SET_NULL`
rather than `PROTECT`.

THE REPLACEMENT CLAIM WAS ALSO TOO WIDE, which is the part worth recording, because it sounded narrow
enough to be safe: it said a square holding an un-published contract is unreachable by EITHER detector,
since `mark_contract_reached` only runs for live contracts. That is the same test's scenario, and the
test asserts the opposite. Neither detector reads `is_live` -- both ask only whether an `EarnedContract`
row EXISTS -- and un-publishing does not take a reached stamp back, so a square assigned while its
contract was live completes perfectly well afterwards. (`mark_contract_reached` has no `is_live` check
of its own either; its CALLERS filter.)

What is actually true is the other order: a contract un-published BEFORE the hunter's completion was
ever detected has no stamp, so there is nothing for either path to find. Not a hole this sweep closes;
the one case neither path covers.

MUST RUN AFTER `process_contracts`, which is why it sits immediately after it in `nightly.STEPS`. A
square completes when an `EarnedContract` row exists, and that command is what creates one -- running
first, this would sweep yesterday's rows and report nothing to do on precisely the night a contract
went live.

NO WATERMARK, unlike `process_contracts` -- and its watermark is not doing what an earlier version of
this docstring said. It narrows the CONTRACT queryset (`live.filter(updated_at__gt=...)`), because a
full pass is O(contracts x candidates) and because a contract's membership is IGDB-derived and can
change without the row being touched. The profile axis is a separate, always-on mechanism there
(`_candidate_profiles`), so that command can already tell cheaply which hunters might qualify.

This one needs neither, because a square NAMES its own contract: "which filled, unfinished squares name
a contract their owner has finished?" is one query over a table holding 26 rows for a letter run and 25
for a jobs run. Nothing to ration, and a cursor would only create a way to miss something.
"""
import logging

from django.core.management.base import BaseCommand, CommandError

from challenges.services import challenge_service as svc
from trophies.models import Profile

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = ("Complete Challenge squares whose contract the owner has already finished. "
            "Runs nightly after process_contracts; --user narrows it to one hunter.")

    def add_arguments(self, parser):
        parser.add_argument('--user', type=str,
                            help='psn_username of a single profile to sweep, instead of everyone.')
        parser.add_argument('--dry-run', action='store_true',
                            help='Report what would change; write nothing.')
        parser.add_argument('--only', choices=['slots', 'calendar'],
                            help='Run only one phase. Default runs both.')
        parser.add_argument('--all-calendars', action='store_true',
                            help='Refresh EVERY calendar run, ignoring the reconciliation check. Use '
                                 'after a bulk shovelware reclassification, which no watermark sees.')

    def handle(self, *args, **options):
        """Two PEER phases, and that structure is the fix for a real bug rather than a tidy-up.

        The Calendar phase was first added as a trailing call at the end of this method -- after the
        slot sweep's own `return`s for "nothing to complete" and "dry run". Those returns mean "this
        phase is finished", but at the top level of `handle` they meant "the COMMAND is finished", so on
        any night where no square was completable -- which is the ordinary night -- `nightly` ran this
        command and the Calendar was never swept. The whole reconciliation design was inert, gated on an
        unrelated phase happening to find work.

        Nothing failed. The command printed "Nothing to complete." and exited zero, which is exactly
        what it printed before the Calendar existed.

        So each phase is its own method now, and a `return` inside one can only end that one. The only
        test that would have caught it is the invocation `nightly` actually makes -- no arguments, on a
        database with no completable square -- and every command test in the suite passed
        `--only calendar`, so the default path had no coverage at all.
        """
        username = options.get('user')
        dry_run = options.get('dry_run', False)
        only = options.get('only')
        all_calendars = options.get('all_calendars', False)
        if dry_run:
            self.stdout.write(self.style.WARNING('DRY RUN -- no changes will be written.\n'))

        # Resolved once, for both phases, so `--user` validates the hunter exists even when the phase
        # that would have looked them up is skipped.
        profile = None
        if username:
            profile = Profile.objects.filter(psn_username=username).first()
            if profile is None:
                raise CommandError(f'No profile with psn_username {username!r}.')

        if only != 'calendar':
            self._sweep_slots(username, profile, dry_run)
        if only != 'slots':
            self._sweep_calendars(username, profile, dry_run, force=all_calendars)

    def _sweep_slots(self, username, profile, dry_run):
        """Complete squares whose contract the owner has finished. The original phase, unchanged in
        behaviour -- its `return`s now end the phase rather than the command."""
        if profile is not None:
            scope = f'one hunter ({profile.psn_username})'
            candidates = svc.completable_slots().filter(challenge__profile=profile)
        else:
            scope = 'every hunter with a run in flight'
            candidates = svc.completable_slots()

        self.stdout.write(self.style.MIGRATE_HEADING(f'Challenge square detection: {scope}'))

        # `select_related` so the report can name the run and its owner without a query per square. The
        # snapshot columns already carry the game's name, which is the point of freezing them -- nothing
        # here joins `Contract`.
        #
        # `.iterator()` rather than `list()`, so a site-wide result is not pulled into the Python heap
        # whole -- every pending square plus a Challenge and a Profile object per row -- before a single
        # write happens.
        #
        # HONEST ABOUT WHAT IT BUYS, because `process_contracts` carries the same call with a
        # qualification that does NOT transfer. A management command runs in autocommit, so Django's
        # server-side cursor is `DECLARE ... WITH HOLD` and Postgres materialises the whole result into a
        # tuplestore anyway. That command calls this harmless because its held set is ID-ONLY; this one
        # selects every column of three joined tables, so the change moves the result from Python's heap
        # to Postgres's rather than streaming it. Still the better default, and at 26 rows per run it is
        # academic either way -- `.only()` is where to go first if this table ever gets big, before
        # anything cleverer.
        #
        # It also makes the sweep depend on server-side cursors for its whole duration, which a
        # transaction-mode connection pooler would break. `process_contracts` already takes that bet.
        #
        # `order_by('challenge_id', 'position')` overrides `Meta.ordering`, and that is not cosmetic.
        # Inherited, the sweep walks position 0 of every run, then position 1 of every run -- so a
        # 26-square run finishing tonight takes and releases its Challenge row lock 26 times
        # NON-CONSECUTIVELY and recounts 26 times, maximising contention with concurrent user writes. It
        # also interleaved hunters in the report.
        candidates = (candidates
                      .select_related('challenge', 'challenge__profile')
                      .order_by('challenge_id', 'position'))
        rows = candidates.iterator(chunk_size=200)
        # The empty case cannot be `if not rows:` any more -- an iterator is always truthy -- so it is
        # settled after the loop, which costs nothing and keeps the query single-pass.

        completed = 0
        seen = 0
        finished_runs = []
        # NOT one enclosing transaction: that would hold every Challenge row lock for the whole sweep.
        # Per-square transactions mean the sweep is not atomic, which is fine because completion is
        # idempotent -- and mean `rows` is a stale snapshot for the sweep's duration, which is why
        # `mark_slot_completed` re-asserts both the square's and the run's preconditions under its lock.
        for slot in rows:
            seen += 1
            label = (f'{slot.challenge.profile.psn_username} / {slot.challenge.name} / '
                     f'{slot.key} -> {slot.contract_name}')
            if dry_run:
                self.stdout.write(f'  would complete  {label}')
                continue
            # Through the service, never a `bulk_update`: each write takes the Challenge row lock, and
            # completing the LAST square is what finishes the run and stamps `completed_at`. A bulk write
            # would skip both and leave finished runs looking unfinished forever.
            # CONTAINED PER SQUARE: one raise used to end the slot phase AND skip the Calendar phase for the
            # night, since nothing between here and `handle` caught it. `refresh_for_profile` contains each
            # Calendar run the same way.
            try:
                did = svc.mark_slot_completed(slot)
            except Exception:
                logger.exception('challenge sweep: slot %s could not be completed', slot.pk)
                self.stdout.write(self.style.ERROR(f'  failed          {label}'))
                continue
            if did:
                completed += 1
                self.stdout.write(f'  completed       {label}')
                # A DIFFERENT instance of the same row from the one `mark_slot_completed` locked (Django
                # has no identity map), and that inner transaction has committed by now because this loop
                # is not itself atomic -- so this reads the new state.
                slot.challenge.refresh_from_db()
                if slot.challenge.is_complete:
                    finished_runs.append(f'{slot.challenge.profile.psn_username} / '
                                         f'{slot.challenge.name}')

        # THE EMPTY CASE, settled after the loop rather than before it: an iterator is always truthy,
        # so `if not rows:` would have been dead and the sweep would have printed a bare success line
        # on a quiet night. Costs nothing and keeps the query single-pass.
        if seen == 0:
            self.stdout.write('Nothing to complete.')
            return

        if dry_run:
            self.stdout.write(self.style.SUCCESS(f'\n{seen} square(s) would be completed.'))
            return

        self.stdout.write(self.style.SUCCESS(f'\n{completed} square(s) completed.'))
        if finished_runs:
            self.stdout.write(self.style.SUCCESS(f'{len(finished_runs)} run(s) FINISHED:'))
            for label in finished_runs:
                self.stdout.write(f'  {label}')


    # ── the Calendar phase ───────────────────────────────────────────────────────────────────────

    def _sweep_calendars(self, username, profile, dry_run, *, force=False):
        """Refresh Plat Calendar runs whose numbers have moved, and ALL of a named hunter's.

        A SECOND PHASE OF THE SAME COMMAND RATHER THAN A SECOND COMMAND, because it is the same drift
        net for the same feature, `nightly`'s docstring says to add a step here rather than a cron
        entry, and `--user` / `--dry-run` already mean the right things. `--only` picks one phase when
        you want just one.

        THE TWO PHASES ASK OPPOSITE-SHAPED QUESTIONS, which is why this is not folded into the loop
        above. A slot sweep is one site-wide query over squares whose contract may have been finished;
        a Calendar sweep recomputes a hunter's whole platinum history, which is expensive per run. So
        this one reconciles first (`runs_due_for_sweep` compares two stored counters against live ones)
        and only pays for runs that could actually have gained a day.

        `--user` DELIBERATELY IGNORES THAT CHECK. The reconciliation is an optimisation for the nightly
        pass; a human asking for one hunter's calendar to be refreshed is asking for the work to be
        done, usually BECAUSE they suspect the watermarks are wrong. Making the manual door obey the
        optimisation would make it useless in exactly the case it exists for.
        """
        from challenges.models import CHALLENGE_TYPE_CALENDAR, Challenge
        from challenges.services import calendar_fill

        if profile is not None:
            runs = Challenge.objects.filter(
                profile=profile, challenge_type=CHALLENGE_TYPE_CALENDAR)
            scope = f'one hunter ({profile.psn_username}), ignoring the due check'
        elif force:
            # THE DOOR FOR THE CHANGE NO WATERMARK CAN SEE. The reconciliation watches the hunter's
            # platinum and earned-contract counts, and a shovelware reclassification moves NEITHER while
            # genuinely changing the `clean` view -- so a hunter whose game was un-flagged would wait for
            # their next platinum before that day filled, and a dormant hunter would wait forever.
            #
            # A third watermark was considered and rejected: the only site-wide signal is "some game's
            # flag moved tonight", which marks EVERY run due and defeats the reconciliation on any night
            # `update_shovelware` touches anything. Targeted invalidation means shovelware detection
            # reaching into challenges, which is a coupling this does not earn. So it is a staff door,
            # used after a bulk reclassification, and the limitation is documented rather than hidden.
            runs = Challenge.objects.filter(challenge_type=CHALLENGE_TYPE_CALENDAR)
            scope = 'EVERY run, ignoring the due check (--all-calendars)'
        else:
            runs = calendar_fill.runs_due_for_sweep()
            scope = 'every run whose platinum count has moved'

        self.stdout.write(self.style.MIGRATE_HEADING(f'\nPlat Calendar refresh: {scope}'))

        runs = list(runs.select_related('profile'))
        if not runs:
            self.stdout.write('No calendar run needs refreshing.')
            return

        if dry_run:
            for run in runs:
                self.stdout.write(f'  would refresh   {run.profile.psn_username} / {run.name}')
            self.stdout.write(self.style.SUCCESS(f'\n{len(runs)} calendar run(s) would be refreshed.'))
            return

        total_new = 0
        for run in runs:
            # ONE RUN'S FAILURE IS NOT THE SWEEP'S. A nightly pass over every calendar must not stop at
            # the first hunter with odd data -- the same reason the square loop above is per-square
            # rather than one enclosing transaction. `logger.exception` so the traceback survives.
            try:
                filled = calendar_fill.apply_to_run(run)
            except Exception:
                logger.exception('calendar refresh failed for challenge %s', run.pk)
                self.stdout.write(self.style.ERROR(
                    f'  FAILED          {run.profile.psn_username} / {run.name}'))
                continue
            total_new += filled
            if filled:
                self.stdout.write(f'  +{filled:<3} day(s)     {run.profile.psn_username} / {run.name}')

        self.stdout.write(self.style.SUCCESS(
            f'\n{len(runs)} calendar run(s) refreshed, {total_new} new day(s) filled.'))

