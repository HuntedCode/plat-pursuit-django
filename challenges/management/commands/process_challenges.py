"""Complete Challenge squares whose contract the owner has finished. The drift net for challenges.

WHY IT EXISTS, given the sync hook already does this. Sync only sees what a sync TOUCHED, so a square
goes unfinished forever in three cases the hook cannot reach:

1. **The contract was published after the hunter finished the game.** `mark_contract_reached` only fires
   for LIVE contracts, so a square assigned before curation caught up waits for a sweep. This is the
   same hole `process_contracts` step 6 of `nightly` was added to close, one layer up.
2. **The hunter has stopped syncing.** Their `EarnedContract` rows still arrive via
   `process_contracts --all`, and without this nothing turns them into squares.
3. **Credit was revoked and re-earned.** `reconcile_contracts` deletes an `EarnedContract` row; a later
   honest re-earn recreates it, and only a sweep notices.

MUST RUN AFTER `process_contracts`, which is why it sits immediately after it in `nightly.STEPS`. A
square completes when an `EarnedContract` row exists, and that command is what creates one -- running
first, this would sweep yesterday's rows and report nothing to do on precisely the night a contract
went live.

NO WATERMARK, unlike `process_contracts`. That command needs one because it cannot tell which profiles
might qualify without walking candidates, so a full pass is expensive and has to be rationed. This one
asks a narrower question -- "which filled, unfinished squares name a contract their owner has
finished?" -- which is one indexed-ish query over a table holding 26 rows per run. There is nothing to
ration, and a cursor would only create a way to miss something.
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
        rows = list(candidates.select_related('challenge', 'challenge__profile'))
        if not rows:
            self.stdout.write('Nothing to complete.')
            return

        completed = 0
        finished_runs = []
        for slot in rows:
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
                slot.challenge.refresh_from_db()
                if slot.challenge.is_complete:
                    finished_runs.append(label)

        if dry_run:
            self.stdout.write(self.style.SUCCESS(f'\n{len(rows)} square(s) would be completed.'))
            return

        self.stdout.write(self.style.SUCCESS(f'\n{completed} square(s) completed.'))
        if finished_runs:
            self.stdout.write(self.style.SUCCESS(f'{len(finished_runs)} run(s) FINISHED:'))
            for label in finished_runs:
                self.stdout.write(f'  {label}')
