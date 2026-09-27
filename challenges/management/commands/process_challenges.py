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
rather than `PROTECT`. What is actually true is narrower -- a square holding an un-published contract is
unreachable by EITHER detector, because `mark_contract_reached` is only ever called for live contracts,
so no `EarnedContract` row is stamped and there is nothing to find. Not a hole this sweep closes; a case
neither path covers.

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
from django.core.management.base import BaseCommand, CommandError

from challenges.services import challenge_service as svc
from trophies.models import Profile


class Command(BaseCommand):
    help = ("Complete Challenge squares whose contract the owner has already finished. "
            "Runs nightly after process_contracts; --user narrows it to one hunter.")

    def add_arguments(self, parser):
        parser.add_argument('--user', type=str,
                            help='psn_username of a single profile to sweep, instead of everyone.')
        parser.add_argument('--dry-run', action='store_true',
                            help='Report what would change; write nothing.')

    def handle(self, *args, **options):
        username = options.get('user')
        dry_run = options.get('dry_run', False)
        if dry_run:
            self.stdout.write(self.style.WARNING('DRY RUN -- no changes will be written.\n'))

        if username:
            profile = Profile.objects.filter(psn_username=username).first()
            if profile is None:
                raise CommandError(f'No profile with psn_username {username!r}.')
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
            if svc.mark_slot_completed(slot):
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
