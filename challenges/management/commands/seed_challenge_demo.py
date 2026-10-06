"""Seed a dev profile with Challenge runs in every state a reward surface can be in, so the panel, the
ledger, the pip, the Start card and BOTH public pages can be LOOKED AT without finishing fifty contracts
by hand.

SIX RUNS, TWO PER TYPE, and the pairing is deliberate: one finished and one in progress of each. The
finished three are what put a hero of each type on the Hall of Fame -- including the A-Z one, whose
26-square board and `A-Z Champion` chip had no way to be seen at all while the seeder produced a single
finished Job Coverage run, and the Plat Calendar's year band, which had no door at all because the type's
creation is gated shut. The in-progress three populate the Challenges browse page, the reward panel's
mixed-state ledger, and the Calendar's part-earned crest row.

THE CALENDAR PAIR DOES NOT READ THE HUNTER'S PLATINUMS, deliberately: a dev library decides how much of a
365-day board fills, so the result is sparse and unpredictable, and a sparse board cannot show what a
struck crest looks like beside an unstruck one. `_calendar_run` explains the whole trade.

BUT THE DESIGNED BOARD IS NOT FROZEN, and this is the one thing to know before looking at it. Fills are
MONOTONE, so anything that runs the real backfill against this profile afterwards ADDS its real platinum
and contract days on top of the designed ones -- the designed days survive, the picture changes. Three
doors do it and NONE of them waits for the nightly reconciliation watermark:

    process_challenges --only calendar --user <psn>     # `--user` ignores the due check by design
    process_challenges --all-calendars
    a sync of that profile                              # the `sync_complete` hook refreshes every run

What changes: the deliberately unstruck month can strike, the part-filled month fills further, and the
shovelware-only square can gain a shovelware-free platinum and stop showing the gap between the two
figures. `earned_on` also moves backwards onto real dates.
So: SEED LAST, and leave those three doors alone until the browser pass is done.

(The NIGHTLY sweep with no arguments is safe immediately after seeding: `apply_to_run` stamps the
watermarks from ground truth, so the run is correctly not due until the profile's platinum or
earned-contract count actually moves.)

    python manage.py seed_challenge_demo --user <psn_username> --reset   # re-runnable
    python manage.py seed_challenge_demo --user <psn_username> --list    # print what is there, write nothing
    python manage.py seed_challenge_demo --user <psn_username> --wipe    # remove ALL of that profile's runs
    python manage.py seed_challenge_demo --user <psn> --reset --fill-letter-gaps   # thin catalogue; see below

`--wipe` EXISTS BECAUSE THE SAFE VERSION DEADLOCKED. This command refuses to adopt a run it did not create
(see `_start_fresh` for why -- adopting one made `--reset` delete real progress), `--reset` only removes runs
it tagged, and the admin is read-only by design. So a profile that already had its own A-Z or Job Coverage run
could not be seeded and had no way to clear it: hiding a run does not free the slot either, because `start`
RESUMES a hidden run rather than creating a fresh one. `--wipe` removes every run of that profile, demo or
not, and says how many. It is deliberately not implied by `--reset`.

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
- **No demo Contracts, unless `--fill-letter-gaps` is passed.** By default it picks REAL live contracts out
  of the dev catalogue, because the whole reason to look at this in a browser is the cover art: a demo
  Concept renders the no-art placeholder, and half the questions worth asking ("does the XP pip read against
  a cover?") cannot be answered against a grey box.

  `--fill-letter-gaps` is the opt-in exception, and only for letters the catalogue cannot cover at all.
  Without it a dev database missing Q, X or Z produces no finished A-Z run, so the Hall of Fame has no A-Z
  hero to look at -- which is a worse outcome than two or three grey squares out of twenty-six.
  `_stand_in_for_letter` documents what those rows are, and what keeps them off the community announcer and
  the public board's Latest window.

So `--reset` removes FOUR things, each in its own method and each for a stated reason: the challenge XP
grants it paid (by slot id, before the slots go), the completion `UserTitle` rows its finished runs earned
(by `source_id`, same reason -- no FK ties them), the Challenge rows themselves (slots AND the Calendar's 365-per-run day rows cascade with them),
and any `--fill-letter-gaps` placeholder contracts. Then it rebuilds the job-XP cache from the remaining real
ledger, so real levels survive.

One exception inside that last removal, and it is deliberate: a placeholder a hunter has actually EARNED is
left in place, because `EarnedContract.contract` and `ContractXPGrant.earned_contract` are both CASCADE, so
deleting it would punch a hole in the append-only XP ledger.

(An earlier version of this paragraph said "exactly two things", which was true before the titles and the
placeholders existed.)

THE RUNS ARE NAMED so they are identifiable on sight and on reset -- `DEMO_TAG` in the name, which is also
the reset scope. A hunter's own runs are auto-named by the service and never carry it.
"""
from datetime import date, timedelta

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.urls import reverse

from challenges.models import (
    CALENDAR_MONTH_DAYS,
    CALENDAR_VIEW_ALL,
    CALENDAR_VIEW_CLEAN,
    CALENDAR_VIEW_FIELDS,
    CHALLENGE_TYPE_AZ,
    CHALLENGE_TYPE_CALENDAR,
    CHALLENGE_TYPE_JOBS,
    Challenge,
    ChallengeSlot,
)
from challenges.services import calendar_fill
from challenges.services import calendar_render
from challenges.services import challenge_service as svc
from challenges.services import rewards
from trophies.models import Contract, ContractXPGrant, EarnedContract, Job, Profile, UserTitle
from trophies.services import contract_service
from trophies.services.career_attention import CHALLENGE_XP_TTL
from trophies.util_modules.constants import CHALLENGE_SLOT_JOB_XP

#: In every seeded run's name. The reset scope, and a visible marker that a run is not the hunter's own.
DEMO_TAG = '[demo]'

#: Slug prefix for the stand-in contracts `--fill-letter-gaps` creates, and the scope `--reset`/`--wipe`
#: remove them by. A prefix rather than a flag field because `Contract` has no "this is fake" column and
#: adding one to a production model for a dev command's benefit is the wrong trade.
#:
#: PER PROFILE, which is a correction. Keyed on the letter alone, two dev profiles seeded with
#: `--fill-letter-gaps` SHARED the rows -- so resetting profile A deleted the contracts under profile B's
#: completed Q/X/Z squares, `SET_NULL`ing their FK and leaving them permanently de-anchored (the snapshot
#: keeps them filled and the check constraint still holds, which is exactly why nothing would have
#: complained). The run deletion is per-profile, so its companion cleanup has to be too.
GAP_SLUG_PREFIX = 'demo-az-gap-'


def _gap_prefix(profile):
    """The stand-in slug prefix for ONE profile. See `GAP_SLUG_PREFIX` on why this is not global."""
    return '%s%s-' % (GAP_SLUG_PREFIX, profile.pk)


def _seeded_date(month, day):
    """The `earned_on` a seeded square carries: its OWN date, in a fixed year.

    2019 because it is a real year with no leap day, so the twelve month lengths the keys come from and
    the dates written onto them agree exactly. A Calendar day has no year at all -- that is the premise
    of the whole type -- so the choice only has to be stable, and a fixed year means the seeded board
    looks the same in March as it does in November.
    """
    return date(2019, month, day)

#: How many squares the mixed-state jobs run fills, and how many of those are pre-claimed. Small on
#: purpose: this scenario is about a ledger holding BOTH states at once, which six rows show as well as
#: twenty-five and reads faster on a phone.
MIXED_FILLED = 6
MIXED_CLAIMED = 3

#: How far back a `--fill-letter-gaps` stand-in's `went_live_at` is set, so it falls outside
#: `contracts_service.new_contract_cutoff()` and cannot wear the public board's "New" chip. Comfortably
#: past `NEW_CONTRACT_WINDOW_DAYS` rather than exactly on it, so a change to that window cannot drag a
#: placeholder back onto the board.
GAP_BACKDATE_DAYS = 400

#: How much of the alphabet the IN-PROGRESS A-Z run fills. Enough that the board is not mostly empty, few
#: enough that the run is obviously unfinished.
AZ_FILLED = 9

#: The months the in-progress Calendar run completes, so a struck crest can be compared against an
#: unstruck one on a single board.
#:
#: IT USED TO SAY WHICH LENS COMPLETED EACH, mapping three months onto bronze, silver and gold so one
#: board showed all three metals. The one-lens collapse made a crest a boolean, so this is a plain set
#: of month numbers.
#:
#: WHICH MONTHS IS ARBITRARY -- a hand-written map costs nothing to fill, so there is no cheapest month
#: and an earlier comment claiming February was chosen for its 28 days was inventing a reason. What is
#: NOT arbitrary is the spread: three struck crests at 2, 5 and 9 sit apart in the switcher, so the row
#: does not read as a run of three followed by nine blanks.
CALENDAR_STRUCK = (2, 5, 9)

#: A month left deliberately PART-FILLED, so an unstruck crest sits beside the struck ones and the month
#: head shows THREE DIFFERENT per-lens figures rather than three zeros or three fulls.
#:
#: TWO FIGURES, NOT THREE. This carried a third count for the contracts lens, and before that filled
#: `all` and `clean` identically -- which gave two distinct numbers under a comment and a test both named
#: for three. With one lens the pair that matters is the filled count and the same count WITHOUT the
#: shovelware exclusion, so the clean figure is deliberately lower than the all figure: that difference
#: is what the comparison line on the page exists to show.
CALENDAR_PARTIAL_MONTH = 7
CALENDAR_PARTIAL_DAYS = 18
CALENDAR_PARTIAL_CLEAN = 11

#: A DAY FILLED BY A SHOVELWARE PLATINUM: present in the `all` count and absent from the board, which is
#: the one place a reader can see what the comparison figure is counting.
#:
#: IT USED TO BE A CONTRACTS-ONLY DAY, seeded because that was the feature's one genuinely confusing
#: state -- a contract reaching its 100% tier with no platinum filled a contracts day and no platinum
#: day, so the square was filled in one lens and empty in two. The collapse removed the lens and with it
#: the confusion; what is left worth looking at is the gap between "days you filled" and "days you hold
#: a platinum for".
CALENDAR_SHOVELWARE_ONLY = (3, 3)

#: SQUARES SEEDED WITH MORE THAN ONE PLATINUM, so the month panel's "busiest day" row has something to
#: name. The row is suppressed below two, so without this it would never render on a seeded board --
#: and a figure nobody can see on the dev board is a figure nobody reviews. One clear winner in a
#: struck month, a tie in another (the panel must name the EARLIER day), and one in the part-filled
#: month so the row is not only a property of finished months.
#: HOW THE MONTH THE BOARD OPENS ON IS SEEDED. Every other seeded month is a fixed one -- February, May
#: and September struck, July part-filled -- which was fine while the board always opened on January and
#: a reviewer could see the designed content immediately. The board now opens on the CURRENT month
#: (owner, 2026-10-05), so a fixed seed leaves whoever runs this looking at an empty panel, with the
#: struck months and every stacked day a click away and no sign they exist.
#:
#: WHICH MONTH IS PASSED IN, NOT READ FROM A CLOCK. The caller resolves it with `today_key`, the same
#: function the board opens on, so the two cannot disagree -- the first version read
#: `timezone.localtime().month`, the SERVER's month, which differs from the board's for up to a day at
#: each month boundary and made the test for this pattern depend on the day it ran. The month is the only
#: thing that varies: `_seeded_date` still pins the year, for the reason it records -- a date derived from
#: `today` would quietly change what the board looks like from month to month.
#:
#: A SPREAD, NOT A BLOCK, so the month reads like a real history rather than a filled prefix: a little
#: over a third of the days, with three stacks among them so the count badge is visible on arrival.
CURRENT_MONTH_FILLED = (2, 5, 6, 9, 13, 16, 17, 20, 24, 27, 28)
CURRENT_MONTH_STACKS = {5: 3, 16: 2, 27: 4}

#: READ BY THREE PATHS, AND ONLY THREE: the `finished` board, the STRUCK months and the PART-FILLED
#: month. A month that is none of those gets its days from the opening-month pattern above, which reads
#: `CURRENT_MONTH_STACKS` and never looks here -- so an entry for such a month changes nothing on an
#: in-progress board, which is the only board these figures exist to furnish.
#:
#: WHICH IS WHY SEPTEMBER WAS THE ONE GAP. It is struck, so the opening-month pattern is gated out of it,
#: and it had no entry here -- so for the whole of September a seeded board opened on thirty filled
#: squares and not one count badge, and the test for "the opening month shows a stack" failed every day
#: of that month and passed on either side of it. That is the worst shape a time-dependent test can have.
#: February, May and July were already covered.
#:
#: SO SEPTEMBER GOT TWO AND NOTHING ELSE DID. The first fix for that gap added eight more entries, one
#: per uncovered month, on the stated grounds that "no month is the unlucky one" -- eight dead keys, since
#: none of those months reads this map, and two of them (March and June) were then overwritten back down
#: to one by the opening-month pattern anyway. A dict entry that cannot affect the board it exists for is
#: the kind of thing this project deletes rather than keeps for symmetry.
#:
#: THE FOUR ORIGINALS ARE THE DESIGNED ONES: a clear winner in February, a deliberate TIE in May so the
#: panel must name the EARLIER day, and one in the part-filled July.
CALENDAR_BUSY = {
    (2, 14): 4,
    (5, 3): 3,
    (5, 21): 3,
    (7, 9): 2,
    (9, 12): 2,
    (9, 25): 3,
}


class Command(BaseCommand):
    help = "Seed a dev profile with Challenge runs in every reward state (--user <psn>)."

    def add_arguments(self, parser):
        parser.add_argument('--user', required=True, help='psn_username of the dev profile to seed.')
        parser.add_argument('--reset', action='store_true',
                            help='Remove previously seeded demo runs (and their XP) first.')
        parser.add_argument('--list', action='store_true',
                            help='Print the URLs of runs already seeded and exit. Writes nothing.')
        parser.add_argument('--wipe', action='store_true',
                            help="Remove ALL of this profile's challenge runs first, demo or not. The way "
                                 "out when the profile's own runs block seeding -- hiding does not free the "
                                 "slot, and the admin cannot delete.")
        parser.add_argument('--fill-letter-gaps', action='store_true',
                            help='Create stand-in contracts for letters the catalogue cannot cover, so '
                                 'the finished A-Z run can exist. Those squares show no cover art.')
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
            if opts['wipe']:
                self._wipe(profile)
            elif opts['reset']:
                self._reset(profile)
            self._seed(profile, fill_letter_gaps=opts['fill_letter_gaps'])

        self._warn_if_the_notification_template_is_missing()
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
        run_ids = list(demo.values_list('pk', flat=True))
        titles = self._remove_titles(profile, run_ids)
        runs = demo.count()
        demo.delete()

        # Rebuild the cache from what is LEFT, which is the real ledger. Without this the profile keeps
        # levels bought by XP whose grants no longer exist.
        # THE STAND-INS GO BEFORE THE RECOMPUTE. `recompute_profile_job_xp` exists because, as the note
        # above says, "without this the profile keeps levels bought by XP whose grants no longer exist" --
        # and it used to run BEFORE a delete that could itself remove grants, so in exactly the case the
        # recompute is for it was computed against a ledger about to shrink. The guard inside
        # `_remove_letter_stand_ins` now makes that unreachable, but an order that is only correct because
        # of a guard two methods away is the kind a later edit breaks.
        gaps = self._remove_letter_stand_ins(profile)
        contract_service.recompute_profile_job_xp(profile)
        self._forget_nav_marker(profile)
        self.stdout.write('  reset: %d demo run(s), %d seeded grant(s)%s%s removed; real XP rebuilt.'
                          % (runs, paid,
                             ', %d title(s)' % titles if titles else '',
                             ', %d placeholder contract(s)' % gaps if gaps else ''))

    def _wipe(self, profile):
        """Remove EVERY challenge run of this profile, demo or not, and the challenge XP tied to them.

        THE ESCAPE HATCH, and it needs to exist because the safe behaviour deadlocked. `_start_fresh` will
        not adopt a run this command did not create, `--reset` is scoped to the demo tag, `ChallengeAdmin` has
        `has_delete_permission -> False`, and hiding a run does not help because `start` RESUMES a hidden one.
        A dev profile that had ever pressed Start was therefore unseedable with no way back.

        It takes the same grants-before-slots order as `_reset` and for the same reason: a challenge grant's
        `source_id` IS the slot id, so once the slots are gone the rows cannot be identified again.

        DESTRUCTIVE ON PURPOSE and never implied by `--reset`, because on a dev profile the runs are scratch
        data and on any other one they are somebody's progress. The `DEBUG` guard above is what keeps the two
        apart.
        """
        runs = Challenge.objects.filter(profile=profile)
        slot_ids = list(ChallengeSlot.objects.filter(challenge__in=runs).values_list('pk', flat=True))

        grants = ContractXPGrant.objects.filter(
            profile=profile, source=rewards.XP_SOURCE, source_id__in=slot_ids)
        paid = grants.count()
        grants.delete()
        titles = self._remove_titles(profile, list(runs.values_list('pk', flat=True)))
        n = runs.count()
        runs.delete()

        gaps = self._remove_letter_stand_ins(profile)
        contract_service.recompute_profile_job_xp(profile)
        self._forget_nav_marker(profile)
        self.stdout.write(self.style.WARNING(
            '  wiped: %d run(s) (demo AND real), %d challenge grant(s)%s%s; real XP from other sources '
            'rebuilt.' % (n, paid,
                          ', %d title(s)' % titles if titles else '',
                          ', %d placeholder contract(s)' % gaps if gaps else '')))

    @staticmethod
    def _forget_nav_marker(profile):
        """Drop the cached "XP waiting" answer, on commit.

        BOTH REMOVAL PATHS NEED THIS. Deleting runs takes their unclaimed squares with them, so the pill's
        answer changes -- and the two writers that normally invalidate it (a square completing, a claim) are
        not involved. Without it the pill advertises XP on runs that no longer exist for up to the full TTL,
        which on a dev box is exactly when somebody is looking at the nav on purpose.

        ON COMMIT, AND THROUGH `rewards._forget_xp_marker`, which is a correction rather than a preference.
        Both call sites are inside this command's `transaction.atomic()`, so a bare delete ran BEFORE the runs
        were actually gone -- the window `_grant` and `contract_service.claim` both use `on_commit` to avoid,
        and which this branch's own comments cite as the original bug. A bare `forget_challenge_xp` also has no
        guard, so a Redis outage would roll back an entire wipe-and-seed over an ornament; `_forget_xp_marker`
        is the best-effort wrapper that exists for exactly that.
        """
        from challenges.services.rewards import _forget_xp_marker

        transaction.on_commit(lambda: _forget_xp_marker(profile))

    # ── seeding ───────────────────────────────────────────────────────────────────────────────────

    def _seed(self, profile, *, fill_letter_gaps):
        jobs = list(Job.objects.order_by('display_order', 'slug'))
        if not jobs:
            raise CommandError('The job catalogue is empty; seed the jobs first.')

        # THE FINISHED RUN FIRST, for BOTH types, because completing a run is what frees the
        # one-active-per-type slot for the in-progress one below. The other order means the second `start`
        # hands back the first run.
        self._jobs_run(profile, jobs, fill=len(jobs), claim=0, label='finished, nothing claimed')
        self._jobs_run(profile, jobs, fill=MIXED_FILLED, claim=MIXED_CLAIMED,
                       label='in progress, %d of %d claimed' % (MIXED_CLAIMED, MIXED_FILLED))

        # A FINISHED A-Z RUN, added 2026-09-30 so the Hall of Fame can be looked at with BOTH types on it.
        # Only a finished run reaches that page, and until now the seeder produced exactly one finished run
        # and it was Job Coverage -- so the A-Z hero, its 26-square board and its `A-Z Champion` title chip
        # had no way to be seen at all. Completing it also grants the real title through the real service,
        # which is what the chip reads.
        self._az_run(profile, fill=None, label='finished', leave_some_unfinished=False,
                     fill_letter_gaps=fill_letter_gaps)
        self._az_run(profile, fill=AZ_FILLED, label='in progress, %d letters' % AZ_FILLED,
                     leave_some_unfinished=True)

        # A FINISHED CALENDAR RUN AND AN IN-PROGRESS ONE, the same pairing as the other two types and for
        # the same reason: only a finished run reaches the Hall of Fame, so without the first the year
        # band has no way to be seen at all, and only an unfinished one shows the crest row part-earned.
        self._calendar_run(profile, label='finished', finished=True)
        self._calendar_run(profile, label='in progress, three months struck', finished=False)

    def _calendar_run(self, profile, *, label, finished):
        """One Plat Calendar run, filled through the REAL writer.

        `apply_to_run` TAKES THE DAYS AS AN ARGUMENT, which is what makes this honest: the command hands
        it a designed `{key: {(month, day): date}}` map and the writer does the filling, the
        clean-implies-all repair and the recount of `filled_count` / `is_complete`.
        Nothing here writes those columns, so what the board renders is what the real backfill produces
        -- the same reason the jobs run pays its XP through the real service rather than faking a ledger
        row.

        WHY NOT THE HUNTER'S ACTUAL PLATINUMS, which `apply_to_run(challenge)` would use. Two reasons,
        and the second is the point of the command. A dev profile's library decides how much of the board
        fills, so the result is unpredictable and usually sparse -- and a sparse board cannot answer the
        questions worth asking: what a struck crest looks like next to an unstruck one, whether the three
        metals read apart, and whether a contracts-only day is legible as empty in the All platinums
        lens. A hunter wanting their real calendar has the real door:
        `process_challenges --only calendar --user <psn>`.

        WHAT IT WRITES IS NOT WHAT IT STAYS. Fills are monotone, so the next real backfill against this
        profile -- `--only calendar --user`, `--all-calendars`, or simply a sync -- merges the hunter's
        actual days ON TOP of these. The module docstring lists the three doors and what moves.

        NO CREATION GATE TO LIFT ANY MORE. This used to swap `calendar` out of
        `TYPES_NOT_YET_CREATABLE` around the call and restore it in a `finally`, because the type was
        gated shut while its rewards were unbuilt and the surfaces it gated are the ones this command
        exists to show. The day-marker ladder is what the gate was waiting for, so the set is empty and
        the swap did nothing but describe a state that had ended. Deleted rather than left as a no-op:
        a `finally` that restores an unchanged value is the kind of machinery a reader assumes is load
        bearing. A future gated type re-adds it, with its own reason.
        """
        challenge = self._start_fresh(profile, CHALLENGE_TYPE_CALENDAR, label)
        if challenge is None:
            return None

        # THE BOARD'S OWN CLOCK, not the server's. `today_key` folds `timezone.now()` into the OWNER's
        # zone, which is the month the board will actually open on; `timezone.localtime()` is the server's
        # (UTC in production), so the two disagreed for up to a day at either end of a month and the
        # seeded pattern could land on the month next to the one being looked at.
        filled = calendar_fill.apply_to_run(
            challenge,
            found=self._calendar_days(finished, calendar_render.today_key(profile)[0]))
        challenge.refresh_from_db()
        self.stdout.write(
            '  calendar "%s": %d day(s) filled, %d/%d%s'
            % (challenge.name, filled, challenge.filled_count, challenge.total_slots,
               ' -- COMPLETE' if challenge.is_complete else ''))
        return challenge

    @staticmethod
    def _calendar_days(finished, now_month):
        """The designed `{view: {(month, day): DayFill(date, plats)}}` map `apply_to_run` fills from.

        A DATE PER DAY, because `earned_on` is what a day cell and the coming day modal read, and a null
        one would make every square look like a row the backfill half-wrote. The year is arbitrary -- a
        Calendar day has no year, which is the whole premise -- so one is picked and stated rather than
        left to `today`, whose month would quietly change what the seeded board looks like.

        EVERY DATE IS ITS OWN SQUARE'S DATE, which the first version got wrong in a way no test saw. It
        clamped the day (`min(day, 28)`), so 29 of the 365 squares held a date that was not their own --
        31 March reading "28 March 2019" -- and the coming day modal is named in the paragraph above as
        the reader that would have shown it. The clamp was also pure superstition, defended by a comment
        that was false three times over: it claimed a leap year "would let a (2, 29) key be constructed"
        (keys come from `CALENDAR_MONTH_DAYS`, whose February is 28, so that key is unreachable in any
        year), that "the model refuses that date by constraint" (`calendarday_day_within_month`
        constrains the month/day COLUMNS; nothing constrains `earned_on`, and `apply_to_run` only ever
        updates pre-created rows so an unknown key is ignored rather than refused), and that it was
        doing the real fill's Feb-29 fold (that is a one-day KEY remap, not a blanket date clamp across
        twelve months).

        A COUNT PER DAY, AND A FEW DAYS DESIGNED TO STACK. The month panel's "busiest day" row is
        suppressed unless some square holds two or more platinums -- every filled square holds at least
        one, so a busiest day of 1 would render on every month and mean nothing. Seeded at 1 everywhere
        the figure is not the point, so the row appears on exactly the months chosen for it rather than
        on all twelve or on none.
        """
        def fill(month, day, plats=1):
            return calendar_fill.DayFill(_seeded_date(month, day), plats)

        days = {view: {} for view, _field in CALENDAR_VIEW_FIELDS}

        if finished:
            # EVERY DAY, so the run completes and every crest is struck. `in_clean` is what completion
            # reads, and `in_all` is its superset, so filling both is the only consistent full board.
            for month, length in enumerate(CALENDAR_MONTH_DAYS, start=1):
                for day in range(1, length + 1):
                    for view in days:
                        days[view][(month, day)] = fill(month, day, CALENDAR_BUSY.get((month, day), 1))
            return days

        for month in CALENDAR_STRUCK:
            for day in range(1, CALENDAR_MONTH_DAYS[month - 1] + 1):
                for view in days:
                    days[view][(month, day)] = fill(month, day, CALENDAR_BUSY.get((month, day), 1))

        # A PART-FILLED MONTH, nested the way real data nests: every clean day is an all day, which
        # `calendarday_clean_implies_all` enforces. The clean figure is lower on purpose -- that gap is
        # what the page's comparison line is about.
        for day in range(1, CALENDAR_PARTIAL_DAYS + 1):
            days[CALENDAR_VIEW_ALL][(CALENDAR_PARTIAL_MONTH, day)] = fill(
                CALENDAR_PARTIAL_MONTH, day,
                CALENDAR_BUSY.get((CALENDAR_PARTIAL_MONTH, day), 1))
            if day <= CALENDAR_PARTIAL_CLEAN:
                days[CALENDAR_VIEW_CLEAN][(CALENDAR_PARTIAL_MONTH, day)] = fill(
                    CALENDAR_PARTIAL_MONTH, day,
                    CALENDAR_BUSY.get((CALENDAR_PARTIAL_MONTH, day), 1))

        # THE MONTH THE BOARD OPENS ON, so a seeded board shows something the moment it is opened.
        # Skipped when it collides with a month that already has a designed shape -- a struck month is a
        # better demonstration than this pattern, and overwriting it would cost the reviewer the
        # completed-month state.
        # PASSED IN RATHER THAN READ FROM THE CLOCK, so this function has no clock at all: it took
        # `timezone.localtime().month`, the SERVER's month, while the board opens on the month in the
        # OWNER's zone. The two differ for up to a day at each month boundary. It also made the test for
        # this pattern depend on the day it ran.
        if now_month not in CALENDAR_STRUCK and now_month != CALENDAR_PARTIAL_MONTH:
            for day in CURRENT_MONTH_FILLED:
                if day > CALENDAR_MONTH_DAYS[now_month - 1]:
                    continue
                plats = CURRENT_MONTH_STACKS.get(day, 1)
                for view in days:
                    days[view][(now_month, day)] = fill(now_month, day, plats)

        # ONE DAY IN `all` AND NOT ON THE BOARD: a shovelware platinum. It is the only place a reader can
        # see what the comparison figure counts that the board does not draw.
        # A COUNT ON A SQUARE THAT DRAWS NOTHING, which is the point of seeding it: the panel's
        # "busiest day" must ignore a shovelware-only day however many platinums it holds, and a seeded
        # 1 could not tell a reviewer whether the exclusion works.
        days[CALENDAR_VIEW_ALL][CALENDAR_SHOVELWARE_ONLY] = fill(*CALENDAR_SHOVELWARE_ONLY, plats=6)
        return days

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
                '  skipped "%s": %s already has a run of this type (%s), and this command will not adopt a '
                'run it did not create. Re-run with --wipe to clear this profile\'s runs first. (Hiding does '
                'NOT help: `start` resumes a hidden run rather than dealing a fresh one.)'
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

    def _az_run(self, profile, *, fill, label, leave_some_unfinished, fill_letter_gaps=False):
        # `fill_letter_gaps` KEEPS ITS DEFAULT, unlike `_seed`'s, because the in-progress A-Z call does
        # not pass it -- that run is meant to be unfinished, so standing letters up for it would be
        # work in service of nothing. `_seed`'s default was removed: `handle` always passes the parsed
        # option, so it could never be reached, and a mutation run proved it by flipping it with no
        # test noticing.
        """An A-Z run. `fill=None` means every letter, which is how the finished one is asked for.

        PARAMETERISED LIKE `_jobs_run`, because there are now two of these and they differ only in how far
        they get. `fill=None` rather than `fill=26` so the caller does not restate the alphabet's length --
        the run's own `total_slots` is the authority, and it is what `is_complete` is measured against.

        `leave_some_unfinished` is what makes the in-progress board show FILLED and COMPLETED squares side
        by side. It must be False for the finished run, and not merely because the last squares would look
        odd: a run only reaches `is_complete` when every square is completed, so a single merely-filled
        square keeps it off the Hall of Fame entirely -- which is the one thing the finished run exists for.

        A-Z pays no job XP, so neither run writes to the ledger; the finished one does grant the real
        `A-Z Champion` title through `rewards.on_run_completed`, which is what the hero's chip reads.

        `fill_letter_gaps` EXISTS BECAUSE A DEV CATALOGUE IS A SUBSET. The module docstring says this command
        makes no demo Contracts, and the reason is cover art: a demo Concept renders the no-art placeholder,
        and half the questions worth asking in a browser cannot be answered against a grey box. That holds
        for the general case and does not hold for this one. Prod has no empty letter (its thinnest are Q, X
        and Z at six to ten contracts), but a dev copy easily has several -- and the cost of refusing is not
        a few grey squares, it is NO finished A-Z run at all, so the Hall of Fame has no A-Z hero and the
        26-square board and its title chip cannot be looked at by any means short of finishing 26 contracts
        by hand. Two or three placeholder cells out of 26 is the better trade.
        OPT-IN, so the documented default is unchanged, and the stand-ins carry `GAP_SLUG_PREFIX` so
        `--reset` and `--wipe` take them away again.
        """
        challenge = self._start_fresh(profile, CHALLENGE_TYPE_AZ, label)
        if challenge is None:
            return None

        # `total_slots` IS THE ALPHABET, read off the run rather than hardcoded.
        wanted = challenge.total_slots if fill is None else fill

        filled = 0
        for slot in challenge.slots.order_by('position'):
            if filled >= wanted:
                break
            contract = self._contract_for(profile, challenge, slot.key)
            if contract is None and fill_letter_gaps:
                contract = self._stand_in_for_letter(profile, slot.key)
            if contract is None:
                continue
            svc.assign(challenge, profile, slot.key, contract)
            if not (leave_some_unfinished and filled % 3 == 2):
                svc.mark_slot_completed(challenge.slots.get(key=slot.key))
            filled += 1

        challenge.refresh_from_db()

        # SAID OUT LOUD WHEN THE CATALOGUE CANNOT FINISH IT, because a silently-unfinished "finished" run
        # just does not appear on the Hall of Fame and there is nothing on screen to explain why. The
        # letters most likely to be missing are the ones prod also finds thinnest (Q, X, Z).
        if fill is None and not challenge.is_complete:
            short = [s.key for s in challenge.slots.order_by('position') if not s.is_completed]
            self.stdout.write(self.style.WARNING(
                '  the A-Z run could not be finished: no live contract in this catalogue starts with '
                '%s. It stays OFF the Hall of Fame (only complete runs are listed) and earns no title.\n'
                '  Re-run with --fill-letter-gaps to stand those letters up with placeholder contracts '
                '(no cover art on those squares, removed again by --reset).'
                % ', '.join(short)))

        return challenge

    @staticmethod
    def _remove_titles(profile, run_ids):
        """Delete the completion titles the runs being removed had granted. Returns how many went.

        BEFORE THE RUNS, like the grants above, and for the same reason: a `UserTitle` is tied to its run by
        `source_id` alone -- no FK, no cascade -- so once the Challenge rows are gone there is nothing left
        to identify the titles by. They would survive forever, held by the hunter, pointing at a pk that no
        longer exists.

        AND THAT IS NOT MERELY UNTIDY. `granted_titles_for` matches `source_id` against the runs on the
        page, so an orphan is unreadable: the next seeded run completes, `grant_completion_title` finds the
        existing `(profile, title)` row and (before this was found) kept its stale `source_id`, and the
        Hall of Fame hero drew no title band. That is exactly how this surfaced -- a seeded Job Coverage
        finish with no title, reported from the browser, while the newer A-Z one showed fine because its
        title had only ever been granted against a live run.

        `rewards.grant_completion_title` now repairs an orphan it meets, so the bug is fixed on both sides.
        This half stops them being created; that half heals the ones already out there.

        SCOPED BY `source_type` AND `source_id`. Without the source term this would delete badge and
        milestone titles that happen to share a run's id.
        """
        if not run_ids:
            return 0
        titles = UserTitle.objects.filter(
            profile=profile, source_type=rewards.TITLE_SOURCE, source_id__in=run_ids)
        count = titles.count()
        titles.delete()
        return count

    def _remove_letter_stand_ins(self, profile):
        """Delete this profile's `--fill-letter-gaps` placeholders. Returns how many went.

        IT REFUSES ANY STAND-IN A HUNTER HAS ACTUALLY EARNED, and that guard is the important part. An
        earlier version discussed exactly one relation -- `ChallengeSlot.contract` `SET_NULL` -- and
        concluded "the worst case is survivable". It missed that `EarnedContract.contract` is **CASCADE**
        and `ContractXPGrant.earned_contract` is **CASCADE** behind it, so deleting a stand-in transitively
        deleted rows from the ledger whose own docstring calls it "Immutable job-XP ledger ... NEVER
        recomputed" and which this feature's XP guard calls append-only, offsettable only by a negating row.
        Reachable because a stand-in must be `is_live=True` to fill its square, so a hunter can platinum it
        like any other contract -- and `_reset`'s headline promise is that it touches no `EarnedContract`.

        So a stand-in with any `EarnedContract` is left in place and reported. A stray placeholder contract
        in a dev catalogue is a cosmetic problem; a hole in an append-only ledger is not.

        AFTER THE RUNS, never before. `ChallengeSlot.contract` is `SET_NULL`, so deleting a contract a live
        square points at leaves that square filled (the snapshot survives, which is what the snapshot is
        for) but pointing at nothing -- and a COMPLETED slot in that state must still satisfy
        `challengeslot_completed_is_filled_dated_and_explained`, which it does, because that constraint
        reads `contract_slug` rather than the FK. Taking the runs out first means it does not arise, and
        both callers do.

        SCOPED BY THIS PROFILE'S SLUG PREFIX. Not by name (a real contract could legitimately be named
        that), not by `igdb_id__isnull=True` (that is the shape of every admin and episodic contract in the
        catalogue, so it would delete real curation), and not by the shared prefix alone -- see
        `GAP_SLUG_PREFIX` on why the profile id is part of it.
        """
        stand_ins = Contract.objects.filter(slug__startswith=_gap_prefix(profile))
        earned = set(
            EarnedContract.objects.filter(contract__in=stand_ins).values_list('contract_id', flat=True))
        if earned:
            self.stdout.write(self.style.WARNING(
                '  %d placeholder contract(s) kept: a hunter has earned them, and deleting one would '
                'cascade into the append-only job-XP ledger.' % len(earned)))
        removable = stand_ins.exclude(pk__in=earned)
        count = removable.count()
        removable.delete()
        return count

    def _stand_in_for_letter(self, profile, letter):
        """A live Contract whose name starts with `letter`, created because nothing in the catalogue does.

        NO CONCEPT, NO IGDB MATCH, NO GAME, deliberately. Those three are what `covers_by_contract` needs to
        resolve art, and inventing them would mean inventing a game that does not exist in order to put a
        placeholder image on screen. Without them the square draws `.pp-chero__sq--bare`, which is a real
        rendering path with its own styling -- the "contract exists, cover does not resolve" case -- so the
        board stays honest about what it knows.

        `get_or_create` on the slug so re-seeding without `--reset` reuses the same stand-in rather than
        colliding on `Contract.slug`, which is globally unique.

        `igdb_id = None`, WHICH IS THE REAL SHAPE for a contract that represents no IGDB game -- the field's
        own help text calls that the admin/episodic case. A first draft of this put a negative number there
        on the stated grounds that the column was non-nullable; it is `null=True, blank=True, unique=True`,
        so that comment was false and the value was an invention. Postgres treats NULLs as distinct under a
        unique index, so several stand-ins coexist. It also means `member_concepts_by_contract` skips its
        igdb lookup entirely, which is how these squares reach the no-art path by the same route a real
        episodic contract would.
        """
        from django.utils import timezone

        slug = '%s%s' % (_gap_prefix(profile), letter.lower())
        # IT MUST NOT REACH A PUBLIC SURFACE, and the first version did on two of them.
        #
        # `is_live=True` IS REQUIRED -- `eligibility._slot_pool` filters on it, so a stand-in that is not
        # live cannot fill the square it exists for. But `is_live=True` plus a non-null `went_live_at` plus
        # a null `announced_at` is EXACTLY `contract_announcer.pending_contracts()`' predicate, so
        # `announce_contracts` would have posted "Q (placeholder for the demo A-Z run)" to the community
        # Discord as a newly published Job Board contract. The same two flags also put it inside
        # `contracts_service.new_contract_cutoff()`, so it wore the "New" chip on the public board.
        #
        # `announced_at` STAMPED AT CREATION takes it out of the announcer's queue permanently -- the row
        # reads as already announced, which is the only state that is never queued. And `went_live_at` is
        # backdated past the NEW window, so it cannot be "Latest" either. Both are lies about a row that is
        # itself a placeholder, which is the right trade: the alternative is a dev command that can post to
        # a real channel.
        now = timezone.now()
        contract, created = Contract.objects.get_or_create(
            slug=slug,
            defaults={
                'name': '%s (placeholder for the demo A-Z run)' % letter,
                'is_live': True,
                'igdb_id': None,
                'went_live_at': now - timedelta(days=GAP_BACKDATE_DAYS),
                'announced_at': now,
            },
        )
        if created:
            self.stdout.write('    stood up a placeholder contract for %s' % letter)
        return contract

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

    def _warn_if_the_notification_template_is_missing(self):
        """Tell the operator, rather than leaving a log line to explain itself.

        COMPLETING A RUN FIRES THE COMPLETION NOTIFICATION, and this command completes runs -- so on a
        database where the fixture has not been loaded, running it emits "challenge_completed template
        missing" from `rewards._notify_completion`, three layers below anything the operator asked for. The
        sender degrades on purpose (a missing template must never cost a hunter their completion), which is
        exactly what makes it easy to mistake for a bug in the feature.

        The row ships in the fixture; a dev database simply has to load it. Said here because this command is
        the thing most likely to surface the gap.
        """
        from notifications.models import NotificationTemplate

        if NotificationTemplate.objects.filter(name='challenge_completed').exists():
            return
        self.stdout.write('')
        self.stdout.write(self.style.WARNING(
            'The `challenge_completed` notification template is not in this database, so completing a run '
            'logged a warning and sent nothing. Nothing is broken -- the sender degrades rather than failing '
            'a completion -- and the inbox is parked, so the row would be write-only anyway. To silence it:'))
        self.stdout.write('    python manage.py loaddata notifications/fixtures/initial_templates.json')

    def _report(self, profile):
        runs = list(Challenge.objects.filter(profile=profile, name__contains=DEMO_TAG)
                    .order_by('challenge_type', 'pk'))
        # EVERYTHING ELSE THIS PROFILE HAS, because those are what block seeding and there was no way to see
        # them. A hidden run is included and marked: it does not free the one-active-per-type slot, which is
        # the least obvious thing about this whole flow.
        others = list(Challenge.objects.filter(profile=profile).exclude(name__contains=DEMO_TAG)
                      .order_by('challenge_type', 'pk'))
        if others:
            self.stdout.write('')
            self.stdout.write(self.style.MIGRATE_HEADING(
                "%s's own runs (NOT seeded; these block seeding)" % profile.psn_username))
            for run in others:
                state = 'finished' if run.is_complete else ('hidden' if run.is_deleted else 'active')
                self.stdout.write('  [%s] %s -- %d/%d squares%s'
                                  % (state, run.name, run.completed_count, run.total_slots,
                                     self._owed_clause(run)))
            self.stdout.write('  Clear them with --wipe if you want the demo runs instead.')

        # THE PILL SECTION BEFORE THE EARLY RETURN, which is where it belongs and is not where it shipped.
        # It sat after this `return`, so a profile with real runs and no seeded ones -- the shape of every
        # account that is not a fresh demo, including the one that produced the bug report this exists for --
        # got no pill diagnostic at all. The three tests all seeded first, so none of them saw it.
        self._report_nav_pill(profile)

        if not runs:
            self.stdout.write('')
            self.stdout.write(self.style.WARNING('No seeded demo runs for %s.' % profile.psn_username))
            return

        # THE GRANTED TITLES, not recomputed ones. `rewards.summary` derives `title_name`/`title_earned`
        # from a recomputed ordinal, which is exactly the divergence `granted_titles_for` exists to prevent:
        # if the title write was contained by this feature's rule 4, or the name collided with another
        # system and was logged-and-left, the hunter holds nothing and the hero draws no band -- while the
        # summary happily names a title. A diagnostic that denies the bug it exists to surface is worse than
        # no diagnostic.
        granted = rewards.granted_titles_for(runs)

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
            # WHICH PAGE EACH RUN IS ON, because that is the question two of these four exist to answer and
            # it is not guessable from the numbers: a run one square short of complete reads as finished in
            # a `25/26` and is nowhere near the Hall of Fame. `is_deleted` is checked too -- hiding takes a
            # finished run off the Hall as surely as being unfinished does.
            if run.is_deleted:
                w('    on NEITHER public page (hidden)')
            elif run.is_complete:
                held = granted.get(run.pk)
                if held:
                    w('    on the HALL OF FAME -- title: %s' % held)
                elif summary['title_name']:
                    # THE DISAGREEMENT IS WORTH PRINTING, because it is the shape of a real bug: the run's
                    # ordinal says it earned a title and no granted row names it.
                    w('    on the HALL OF FAME -- NO title band (ordinal says %s, but no UserTitle names '
                      'this run)' % summary['title_name'])
                elif run.challenge_type in rewards.TYPES_WITHOUT_ORDINAL_TITLES:
                    # NO *ORDINAL* TITLE, which is not the same as no title and not the same as having run
                    # out of them. The Calendar pays a ladder by filled days instead, so a finished run
                    # DOES wear a band -- its top rung -- and saying "grants none yet" would send a reader
                    # looking for a bug in the band they can see. This branch used to fall through to
                    # "third or later completion" and invent a reason outright; the correction then
                    # outlived the thing it corrected.
                    w('    on the HALL OF FAME -- no ordinal title (this type pays a day-marker ladder)')
                else:
                    w('    on the HALL OF FAME -- no title (third or later completion)')
            else:
                w('    on the CHALLENGES browse page (in flight)')
        w('')
        w(self.style.MIGRATE_HEADING('And the pages'))
        w('    %s   (yours)' % reverse('my_challenges'))
        # NO CALENDAR CARD THERE, and saying so beats letting the reader hunt for one. My Challenges
        # builds its Start cards from `CHALLENGE_TYPE_CHOICES` minus `TYPES_NOT_YET_CREATABLE`, so the
        # gated type has no card -- a FINISHED Calendar run still shows in the history list, but the
        # in-progress one is reachable only by its own URL and the public browse page.
        w(self.style.WARNING(
            '      (no Plat Calendar card: creation is gated, so the in-progress Calendar run is '
            'reachable only by its URL above)'))
        w('    %s   (public: runs in flight)' % reverse('challenges'))
        w('    %s   (public: finished runs)' % reverse('challenges_hall_of_fame'))
        w('')
        w('Signed out (or a private window) on any run URL above gives you the VISITOR view:')
        w('the reward panel with no Claim buttons, which is what a Hall of Fame visitor sees.')

    @staticmethod
    def _owed_clause(run):
        """", N owed (X XP)" for a jobs run that has unclaimed squares, or ''.

        A-Z AND PLAT CALENDAR RUNS OWE NOTHING BY DEFINITION, so they get no clause rather than a zero.
        Only Job Coverage pays per square.
        """
        owed = rewards.redeemable_slots(run).count()
        if not owed:
            return ''
        return ', %d owed (%s XP)' % (owed, '{:,}'.format(owed * CHALLENGE_SLOT_JOB_XP))

    def _report_nav_pill(self, profile):
        """Why the My Pursuit "XP" pill is lit or dark, traced to the runs responsible.

        THE DIAGNOSTIC THIS COMMAND WAS MISSING, and the owner's report is what showed it: the pill looked
        stuck after claiming everything he could find, and nothing here could say whether it was wrong or
        whether some run still owed. Three things make that hard to see from the page alone -- the pill is
        PROFILE-WIDE (any run, not the one you are looking at), HIDDEN runs count (they owe real XP and the
        payout door still pays them) while My Challenges does not list them, and the answer is CACHED.

        BOTH ANSWERS ARE PRINTED, and that is a correction: this claimed it "cannot disagree with the pill for
        a reason of its own" because it asks the same predicate. The pill does not ask the predicate -- it asks
        `career_attention.has_unclaimed_challenge_xp`, a 300-second cached wrapper. So a stale key is a reason
        of the pill's own, and it is the likeliest cause of a pill that looks stuck: the live answer reads dark
        while the browser stays lit for up to five minutes. A diagnostic that printed only the live answer
        would have denied a real bug, which is worse than not having one.
        """
        from trophies.services.career_attention import has_unclaimed_challenge_xp

        lit = rewards.has_unclaimed_xp(profile)
        cached = has_unclaimed_challenge_xp(profile)
        owing = [(run, rewards.redeemable_slots(run).count())
                 for run in Challenge.objects.filter(profile=profile,
                                                     challenge_type=CHALLENGE_TYPE_JOBS).order_by('pk')]
        owing = [(run, n) for run, n in owing if n]

        w = self.stdout.write
        w(self.style.MIGRATE_HEADING('The My Pursuit "XP" pill'))
        if cached != lit:
            # THE MOST USEFUL LINE THIS COMMAND PRINTS, when it prints it. The browser reads the cached
            # answer; everything else here reads the live one.
            w(self.style.ERROR(
                '  THE CACHE DISAGREES: the browser is showing %s and the truth is %s. The key expires within '
                '%ds, or a claim / a completed square clears it.'
                % ('LIT' if cached else 'dark', 'LIT' if lit else 'dark', CHALLENGE_XP_TTL)))
        if not lit:
            w('  dark -- nothing owed anywhere.')
            return
        total = sum(n for _run, n in owing)
        w(self.style.WARNING('  LIT -- %d square(s) owed, %s XP, across %d run(s):'
                             % (total, '{:,}'.format(total * CHALLENGE_SLOT_JOB_XP), len(owing))))
        for run, n in owing:
            flag = ' [HIDDEN -- not listed on My Challenges]' if run.is_deleted else ''
            w('    %d owed: %s%s' % (n, run.name, flag))
            w('      %s' % reverse('challenge_detail', args=[run.pk]))
        if not owing:
            # BELT AND BRACES, and worth printing rather than swallowing: the pill and this listing ask the
            # same predicate, so disagreeing means one of them is broken and that is the bug report.
            w(self.style.ERROR('    ...but no run reports an owed square. The pill and this listing '
                               'disagree, which should be impossible -- they share `_owed()`.'))
