"""Seed a dev profile with Challenge runs in every state a reward surface can be in, so the panel, the
ledger, the pip and the Start card can all be LOOKED AT without finishing twenty-five contracts by hand.

    python manage.py seed_challenge_demo --user <psn_username> --reset   # re-runnable
    python manage.py seed_challenge_demo --user <psn_username> --list    # just print the URLs

DEV-ONLY, and it refuses to run with `DEBUG = False` unless `--force` is passed, because unlike
`seed_career_demo` this one can write into the APPEND-ONLY job-XP ledger (the "some squares already
claimed" scenario pays real grants through the real service, which is the point -- a faked paid row would
not exercise the code the panel actually renders from).

WHAT IT DOES NOT TOUCH, which is what makes `--reset` safe:

- **No `EarnedContract` rows.** A square is completed by calling `mark_slot_completed` directly rather than
  by faking the hunter's completion history. That is writing around the detector on purpose: detection's
  job is to decide WHEN a square completes, and here we already know. It means the command never invents
  trophies, platinums or contract credit for a real account, so nothing it does can leak into Career, the
  boards, or a real claim.
- **No demo Contracts.** It picks REAL live contracts out of the dev catalogue, because the whole reason to
  look at this in a browser is the cover art: a demo Concept renders the no-art placeholder, and half the
  questions worth asking ("does the XP pip read against a cover?") cannot be answered against a grey box.

So `--reset` removes exactly two things: the Challenge rows this command created (slots cascade with them)
and the challenge XP grants it paid, identified by the slot ids it is about to delete. Then it rebuilds the
job-XP cache from the remaining real ledger, so real levels survive.

THE RUNS ARE NAMED so they are identifiable on sight and on reset -- `DEMO_TAG` in the name, which is also
the reset scope. A hunter's own runs are auto-named by the service and never carry it.
"""
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.urls import reverse

from challenges.models import (
    CHALLENGE_TYPE_AZ,
    CHALLENGE_TYPE_JOBS,
    Challenge,
    ChallengeSlot,
)
from challenges.services import challenge_service as svc
from challenges.services import rewards
from trophies.models import Contract, ContractXPGrant, Job, Profile
from trophies.services import contract_service

#: In every seeded run's name. The reset scope, and a visible marker that a run is not the hunter's own.
DEMO_TAG = '[demo]'

#: How many squares the mixed-state jobs run fills, and how many of those are pre-claimed. Small on
#: purpose: this scenario is about a ledger holding BOTH states at once, which six rows show as well as
#: twenty-five and reads faster on a phone.
MIXED_FILLED = 6
MIXED_CLAIMED = 3

#: How much of the alphabet the A-Z run fills. Enough that the board is not mostly empty, few enough that
#: the run is obviously unfinished.
AZ_FILLED = 9


class Command(BaseCommand):
    help = "Seed a dev profile with Challenge runs in every reward state (--user <psn>)."

    def add_arguments(self, parser):
        parser.add_argument('--user', required=True, help='psn_username of the dev profile to seed.')
        parser.add_argument('--reset', action='store_true',
                            help='Remove previously seeded demo runs (and their XP) first.')
        parser.add_argument('--list', action='store_true',
                            help='Print the URLs of runs already seeded and exit. Writes nothing.')
        parser.add_argument('--force', action='store_true',
                            help='Allow running with DEBUG=False. This command writes to the XP ledger.')

    def handle(self, *args, **opts):
        profile = self._profile(opts['user'])

        if opts['list']:
            self._report(profile)
            return

        if not settings.DEBUG and not opts['force']:
            raise CommandError(
                'This command pays real job XP into an append-only ledger and is meant for a dev '
                'database. Re-run with --force if you really mean it here.'
            )

        # ONE TRANSACTION for the writes, so a failure half way through does not leave a profile holding
        # three runs of one type and no way to reach the fourth. `--list` is outside it, being read-only.
        with transaction.atomic():
            if opts['reset']:
                self._reset(profile)
            self._seed(profile)

        self._report(profile)

    # ── the profile ───────────────────────────────────────────────────────────────────────────────

    @staticmethod
    def _profile(psn_username):
        try:
            profile = Profile.objects.select_related('user').get(psn_username=psn_username)
        except Profile.DoesNotExist:
            raise CommandError("No profile with psn_username '%s'." % psn_username)
        if not profile.is_linked:
            # Every challenge write refuses an unlinked profile, so this would fail one call in with the
            # runs half built. Said plainly up front instead.
            raise CommandError("'%s' has no linked PSN account; challenge writes refuse it." % psn_username)
        return profile

    # ── reset ─────────────────────────────────────────────────────────────────────────────────────

    def _reset(self, profile):
        """Remove prior demo runs and the XP they were paid. Real data is untouched.

        THE GRANTS GO FIRST, and by slot id. A challenge grant's `source_id` IS the slot id, so once the
        slots are deleted there is nothing left to identify the rows by -- they would stay in the ledger
        forever, counted by `recompute_profile_job_xp`, with no slot behind them.
        """
        demo = Challenge.objects.filter(profile=profile, name__contains=DEMO_TAG)
        slot_ids = list(ChallengeSlot.objects.filter(challenge__in=demo).values_list('pk', flat=True))

        grants = ContractXPGrant.objects.filter(
            profile=profile, source=rewards.XP_SOURCE, source_id__in=slot_ids)
        paid = grants.count()
        grants.delete()
        runs = demo.count()
        demo.delete()

        # Rebuild the cache from what is LEFT, which is the real ledger. Without this the profile keeps
        # levels bought by XP whose grants no longer exist.
        contract_service.recompute_profile_job_xp(profile)
        self.stdout.write('  reset: %d demo run(s), %d seeded grant(s) removed; real XP rebuilt.'
                          % (runs, paid))

    # ── seeding ───────────────────────────────────────────────────────────────────────────────────

    def _seed(self, profile):
        jobs = list(Job.objects.order_by('display_order', 'slug'))
        if not jobs:
            raise CommandError('The job catalogue is empty; seed the jobs first.')

        # THE FINISHED RUN FIRST, because completing a run is what frees the one-active-per-type slot for
        # the mixed run below. The other order means the second `start` hands back the first run.
        self._jobs_run(profile, jobs, fill=len(jobs), claim=0, label='finished, nothing claimed')
        self._jobs_run(profile, jobs, fill=MIXED_FILLED, claim=MIXED_CLAIMED,
                       label='in progress, %d of %d claimed' % (MIXED_CLAIMED, MIXED_FILLED))
        self._az_run(profile)

    def _start_fresh(self, profile, challenge_type, label):
        """A run this command CREATED, or None -- never one that was already there.

        THIS IS THE GUARD THAT KEEPS `--reset` SAFE, and its absence was the worst bug in the first draft.
        `start` RESUMES a hidden run and returns an ALREADY-ACTIVE one, so a bare call adopted the hunter's
        own run; `_tag` then wrote the demo marker into its name, and the marker is the reset scope. The next
        `--reset` would have deleted somebody's real progress, with the command reporting success.

        It also explains the thin-catalogue case honestly. If the first jobs run could not be completed (no
        live contract for any job), nothing freed the active slot, so the second `start` hands the same run
        back -- which the first draft then tagged a second time, leaving two runs where three were promised.
        """
        challenge, outcome = svc.start_reporting(profile, challenge_type)
        if outcome != svc.CREATED:
            self.stdout.write(self.style.WARNING(
                '  skipped "%s": %s already has a run of this type in progress (%s). Finish or hide it, '
                'or seed a scratch profile -- this command will not adopt a run it did not create.'
                % (label, profile.psn_username, challenge.name)))
            return None
        self._tag(challenge, label)
        return challenge

    def _jobs_run(self, profile, jobs, *, fill, claim, label):
        """A Job Coverage run with `fill` squares completed, `claim` of them already paid."""
        challenge = self._start_fresh(profile, CHALLENGE_TYPE_JOBS, label)
        if challenge is None:
            return None

        filled = 0
        for job in jobs:
            if filled >= fill:
                break
            contract = self._contract_for(profile, challenge, job.slug)
            if contract is None:
                continue
            svc.assign(challenge, profile, job.slug, contract)
            svc.mark_slot_completed(challenge.slots.get(key=job.slug))
            filled += 1

        if filled < fill:
            self.stdout.write(self.style.WARNING(
                '  only %d of %d squares could be filled -- the dev catalogue has no live contract for '
                'the rest of the jobs. The panel still shows every state; the ledger is just shorter.'
                % (filled, fill)))

        for slot in challenge.slots.filter(is_completed=True).order_by('position')[:claim]:
            rewards.redeem_slot(challenge, profile, slot.key)

        challenge.refresh_from_db()
        return challenge

    def _az_run(self, profile):
        """An A-Z run part way through. It pays no XP, so it is here for the header's title chip and for
        the fact that it draws NO reward panel at all."""
        label = 'in progress, %d letters' % AZ_FILLED
        challenge = self._start_fresh(profile, CHALLENGE_TYPE_AZ, label)
        if challenge is None:
            return None

        filled = 0
        for slot in challenge.slots.order_by('position'):
            if filled >= AZ_FILLED:
                break
            contract = self._contract_for(profile, challenge, slot.key)
            if contract is None:
                continue
            svc.assign(challenge, profile, slot.key, contract)
            # Two thirds of them finished, so the board shows filled AND completed squares side by side.
            if filled % 3 != 2:
                svc.mark_slot_completed(challenge.slots.get(key=slot.key))
            filled += 1

        challenge.refresh_from_db()
        return challenge

    @staticmethod
    def _contract_for(profile, challenge, key):
        """A real live contract that fits `key`, or None.

        THROUGH `eligibility`, not a hand-rolled query, so the command can only ever assign something the
        service would accept -- and so a square filled here looks exactly like one a hunter filled. The
        pool is ordered by name and this takes the first, which makes the seed deterministic: re-seeding
        the same catalogue produces the same board, so a screenshot means something.
        """
        from challenges.services import eligibility

        return eligibility.eligible_contracts(profile, challenge, key).order_by('name').first()

    @staticmethod
    def _tag(challenge, label):
        """Mark the run as seeded, in its own name. The marker is the reset scope AND the thing that tells
        somebody looking at the page that this run was not played.

        BUILT WITHIN `NAME_MAX_LENGTH`, read off the model rather than assumed. The first version pasted the
        tag and a sentence onto an auto-generated name and overflowed a 60-character column -- and because
        the whole seed runs in one transaction, that failed the command with the runs half built. The TAG is
        what must survive truncation, since it is the reset scope, so it goes first and the label takes
        whatever room is left.
        """
        from challenges.models import NAME_MAX_LENGTH

        head = '%s %s ' % (DEMO_TAG, challenge.name)
        room = NAME_MAX_LENGTH - len(head)
        challenge.name = (head + label[:room]) if room > 0 else head[:NAME_MAX_LENGTH]
        challenge.save(update_fields=['name'])

    # ── what to go and look at ────────────────────────────────────────────────────────────────────

    def _report(self, profile):
        runs = list(Challenge.objects.filter(profile=profile, name__contains=DEMO_TAG)
                    .order_by('challenge_type', 'pk'))
        if not runs:
            self.stdout.write(self.style.WARNING('No seeded demo runs for %s.' % profile.psn_username))
            return

        w = self.stdout.write
        w('')
        w(self.style.MIGRATE_HEADING('Seeded runs for %s' % profile.psn_username))
        for run in runs:
            summary = rewards.summary(run)
            w('  %s' % run.name)
            w('    %s' % reverse('challenge_detail', args=[run.pk]))
            w('    %d/%d squares, %s claimable, %s XP claimed'
              % (run.completed_count, run.total_slots, summary['claimable_count'],
                 '{:,}'.format(summary['paid_xp'])))
        w('')
        w(self.style.MIGRATE_HEADING('And the personal page'))
        w('    %s' % reverse('my_challenges'))
        w('')
        w('Signed out (or a private window) on any run URL above gives you the VISITOR view:')
        w('the reward panel with no Claim buttons, which is what the Hall of Fame will show.')
