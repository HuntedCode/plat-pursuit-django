"""Permanently delete ONE challenge run, for the rare case that needs it (owner, 2026-10-17).

THE ADMIN STAYS READ-ONLY, and deliberately: `ChallengeAdmin.has_delete_permission` is False because a run's
squares are the only record of which game paid which job's XP. Hiding a run is the hunter's own soft delete,
but it is not a way out either, because Start RESUMES a hidden run instead of dealing a fresh one. So a team
member who genuinely needs a run gone (testing the optimizer on their own account with a fresh A-Z, say) had
no door at all. This is that door, narrow on purpose:

    python manage.py delete_challenge 412                         # show what would go; changes nothing
    python manage.py delete_challenge 412 --confirm               # delete it
    python manage.py delete_challenge --user <psn> --type az      # the same, found by owner and type

A DRY RUN UNLESS `--confirm`: the report (owner, type, state, squares, paid XP, titles) prints either way.

A RUN WITH ANY REWARD ON IT IS REFUSED unless `--force`: job XP a square paid, job XP a finished square is still
OWED (unclaimed, so deleting the run loses it), or a completion title the run granted. With `--force` the run goes,
paid XP and titles STAY, and owed XP is lost. The job-XP ledger is append-only (a pay can be offset, never
removed), the titles were earned, and a run's deletion is not the hunter un-earning them. The grants then name a
square that no longer exists (`source_id` is the slot id), which is the trade `--force` says out loud.

A FINISHED A-Z OR JOB COVERAGE RUN CARRIES ONE MORE CONSEQUENCE: their completion titles go by how many runs of
the type the hunter has finished (first finish, second finish), so deleting one lowers that count and changes which
title their next finish earns. The report says so; it is part of what `--force` accepts. (The Calendar has no such
ordinal titles: `rewards.TYPES_WITHOUT_ORDINAL_TITLES`.)

An unfinished run with nothing earned, the case this exists for, deletes cleanly.

ONE RUN, NEVER A PROFILE'S WORTH: `seed_challenge_demo --wipe` already clears every run of a DEV profile and
its grants (and refuses to run with `DEBUG = False`); this is the one for a real account.
"""
import logging

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.db.models import Sum

from challenges.models import CHALLENGE_TYPE_CHOICES, Challenge
from challenges.services import rewards
from trophies.models import ContractXPGrant, Profile, UserTitle

logger = logging.getLogger(__name__)

TYPE_KEYS = [key for key, _ in CHALLENGE_TYPE_CHOICES]


class Command(BaseCommand):
    help = 'Permanently delete one challenge run (a dry run unless --confirm).'

    def add_arguments(self, parser):
        parser.add_argument('run_id', nargs='?', type=int, help='The run (Challenge) id.')
        parser.add_argument('--user', help="The owner's PSN username, with --type, instead of an id.")
        parser.add_argument('--type', choices=TYPE_KEYS, dest='run_type', help='The run type, with --user.')
        parser.add_argument('--confirm', action='store_true', help='Actually delete. Without it nothing changes.')
        parser.add_argument('--force', action='store_true',
                            help='Allow a run with rewards on it: paid job XP and titles are KEPT, owed job XP is lost.')

    def handle(self, *args, run_id=None, user=None, run_type=None, confirm=False, force=False, **options):
        challenge = self._find(run_id, user, run_type)
        report = self._report(challenge)
        self._print(challenge, report)

        needs_force = self._rewarded(report) and not force
        if not confirm:
            # A DRY RUN ONLY REPORTS, including that the real one would be refused.
            if needs_force:
                self.stdout.write(self.style.WARNING('This run has rewards on it: deleting it needs --force.'))
            self.stdout.write('Dry run: nothing was deleted. Add --confirm to delete this run.')
            return
        if needs_force:
            raise CommandError('This run has rewards on it (paid or owed job XP, or a title). Re-run with --force to '
                               'delete it anyway: paid XP and titles are kept, owed XP is lost.')

        with transaction.atomic():
            # LOCKED AND RE-ASKED: a claim or a completion landing between the report and this delete would
            # otherwise slip a reward past the check above.
            locked = Challenge.objects.select_for_update().get(pk=challenge.pk)
            report = self._report(locked)
            if self._rewarded(report) and not force:
                raise CommandError('This run earned a reward while the report was printing. Nothing was deleted.')
            profile = locked.profile
            locked.delete()   # its squares (or a Calendar's days) cascade with it
            # The cached "XP waiting" nav answer may have counted this run's unclaimed squares: dropped once the
            # delete has committed, through the best-effort wrapper the claim path uses.
            transaction.on_commit(lambda: rewards._forget_xp_marker(profile))

        logger.warning('delete_challenge: run %s (%s, %s) of profile %s deleted%s', challenge.pk,
                       challenge.challenge_type, challenge.name, challenge.profile_id,
                       ' with --force (paid rewards kept, owed XP lost)' if force and self._rewarded(report) else '')
        kept = ''
        if force and self._rewarded(report):
            kept = (' Kept: %d job XP grant(s) worth %d XP and %d title(s); %d owed square(s) lost.'
                    % (report['grants'], report['xp'], report['titles'], report['owed']))
        self.stdout.write(self.style.SUCCESS('Deleted run %d.%s' % (challenge.pk, kept)))

    # ── finding the run ───────────────────────────────────────────────────────────────────────────

    def _find(self, run_id, user, run_type):
        if run_id is not None and (user or run_type):
            raise CommandError('Give a run id, or --user with --type, not both.')
        if run_id is not None:
            try:
                return Challenge.objects.select_related('profile').get(pk=run_id)
            except Challenge.DoesNotExist:
                raise CommandError('No run with id %d.' % run_id)
        if not (user and run_type):
            raise CommandError('Give a run id, or --user with --type.')
        try:
            profile = Profile.objects.get(psn_username__iexact=user)
        except Profile.DoesNotExist:
            raise CommandError("No profile with psn_username '%s'." % user)
        runs = list(Challenge.objects.select_related('profile')
                    .filter(profile=profile, challenge_type=run_type).order_by('-updated_at'))
        if not runs:
            raise CommandError("'%s' has no %s run." % (user, run_type))
        if len(runs) > 1:
            listing = '\n'.join('  %d  %s  (%s)' % (r.pk, r.name, self._state(r)) for r in runs)
            raise CommandError("'%s' has %d %s runs; give the id of the one to delete:\n%s"
                               % (user, len(runs), run_type, listing))
        return runs[0]

    # ── what would go ─────────────────────────────────────────────────────────────────────────────

    @staticmethod
    def _report(challenge):
        """Squares and rewards, read BY SOURCE: a challenge grant's `source_id` is the slot id, a challenge
        title's is the run id, and both need their `source` term or they would match other systems' rows."""
        slot_ids = list(challenge.slots.values_list('pk', flat=True))
        grants = ContractXPGrant.objects.filter(source=rewards.XP_SOURCE, source_id__in=slot_ids) if slot_ids \
            else ContractXPGrant.objects.none()
        return {
            'squares': len(slot_ids),
            'filled': challenge.slots.exclude(contract_slug='').count() if slot_ids else 0,
            'completed': challenge.slots.filter(is_completed=True).count() if slot_ids else 0,
            # A Calendar run's 365 days are pre-created; a filled one is `in_clean`, the board's own test.
            'days': challenge.calendar_days.filter(in_clean=True).count(),
            'grants': grants.count(),
            'xp': grants.aggregate(total=Sum('amount'))['total'] or 0,
            'titles': UserTitle.objects.filter(source_type=rewards.TITLE_SOURCE, source_id=challenge.pk).count(),
            # FINISHED BUT UNCLAIMED job XP: no grant exists yet, so only the square says it is owed. The payout
            # door's own query (`rewards._owed()`, which the nav pill and the page share), not a copy of its terms:
            # it is empty for non-jobs runs and skips a square whose job left the catalogue, which can never pay.
            'owed': rewards.redeemable_slots(challenge).count(),
        }

    @staticmethod
    def _rewarded(report):
        return bool(report['grants'] or report['titles'] or report['owed'])

    @staticmethod
    def _state(challenge):
        if challenge.is_complete:
            return 'finished'
        return 'hidden' if challenge.is_deleted else 'active'

    def _print(self, challenge, report):
        owner = challenge.profile.display_psn_username or challenge.profile.psn_username or challenge.profile_id
        self.stdout.write('Run %d: "%s", %s, owned by %s, %s.'
                          % (challenge.pk, challenge.name, challenge.challenge_type, owner, self._state(challenge)))
        if report['squares']:
            self.stdout.write('  %d squares: %d filled, %d finished.'
                              % (report['squares'], report['filled'], report['completed']))
        if report['days']:
            self.stdout.write('  %d calendar day(s) filled.' % report['days'])
        self.stdout.write('  Paid: %d job XP grant(s) worth %d XP; %d title(s).'
                          % (report['grants'], report['xp'], report['titles']))
        if report['owed']:
            self.stdout.write('  Owed: %d finished square(s) with job XP not yet claimed (lost if deleted).'
                              % report['owed'])
        if challenge.is_complete and challenge.challenge_type not in rewards.TYPES_WITHOUT_ORDINAL_TITLES:
            self.stdout.write('  Finished: deleting it lowers how many of this type the hunter has finished, which '
                              'decides the title their next finish earns.')
