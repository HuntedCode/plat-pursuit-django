"""Challenges, rebuilt (2026-09).

WHY A SEPARATE APP. Nothing forced it the way `gamelists` was forced -- the old models are gone, not
sitting in `trophies/models.py` under the same names -- so this is a choice rather than a workaround.
It is the same choice: a namespace, a service and a migration history of its own, matching
`gamelists`, `milestones`, `notifications` and `fundraiser`. The old system's 13,700 lines lived
across `trophies/models.py`, one 960-line service, a 1,150-line view module, six API modules and six
JS files, and that sprawl is most of why it was cheaper to delete than to fix.

WHAT IS ACTUALLY DIFFERENT, since "rebuilt" should mean something:

1. **A slot holds a CONTRACT, not a Game.** The old A-Z picked a `Game` and checked
   `ProfileGame.has_plat`; the old Genre challenge picked a `Concept`. Both types here pick a
   `Contract`, which buys three things at once: the pool is staff-curated so shovelware exclusion is
   free, a contract already knows its jobs so Job Coverage needs no taxonomy of its own, and one
   eligibility rule serves both types instead of two that drift.

2. **Platinum is not required, so completion has ONE definition and we did not write it.** A slot is
   complete when the hunter has completed the contract -- which the contract engine already decides,
   and already records as an `EarnedContract` row. The old system carried a parallel platinum check
   per type. Deleting the platinum requirement deleted that code rather than adding a branch to it.

3. **A slot SNAPSHOTS its contract, and owns its own completion.** Contract membership is DERIVED
   from the raw IGDB id, so it moves: a Concept split, a re-anchor, a match leaving
   `TRUSTED_STATUSES` or a staff edit to `Contract.igdb_id` all change who qualifies, and
   `reconcile_contracts` responds by DELETING the `EarnedContract` row (docs/design/rebuild/
   job-board-contracts.md). A slot that read its completion live from that row would silently
   un-complete a finished run because of catalogue bookkeeping the hunter never saw. So
   `EarnedContract` is consulted as the DETECTOR and never trusted as the STORE, and the contract's
   identity is frozen onto the slot at assignment.

4. **There is no prior-progress exclusion.** The old system refused any game you had >= 50% of, and
   expanded that refusal through Concept siblings and GameFamily siblings to stop you picking the EU
   copy of a game you had half-finished. That was the single most bug-prone surface in the feature
   (`get_excluded_game_ids`), and it is gone: owner's call, 2026-09-26 -- if you can still complete
   the contract, picking it is fine. The only exclusion left is a contract you have ALREADY
   completed, which is one indexed existence check.
"""
from django.db import models
from django.db.models import F, Q

from trophies.models import Contract, Profile

#: The three types. `az` is the returning A-Z Challenge; `jobs` is the new Job Coverage Challenge,
#: which replaces the retired Genre challenge; `calendar` is the Plat Calendar, revived 2026-10-02.
#:
#: THE CALENDAR'S RETURN REVERSES A SETTLED DECISION, recorded rather than quietly edited. This comment
#: read "The Platinum Calendar does NOT return" from 2026-09-26 until the owner revived it. It is also
#: the one type whose atom is NOT a Contract: a day is filled by a date, which is why it has its own
#: `CalendarDay` model rather than a `ChallengeSlot`, and why several rules stated below as if they
#: covered every type ("a slot holds a CONTRACT") describe the first two only.
#:
#: "A-Z Challenge", not "A-Z Platinum Challenge": platinum stopped being a requirement, so the old
#: name would now overstate the bar. The placeholder page's public promise already used the bare form
#: ("your past A-Z runs are safe"), so the community-facing string does not change.
CHALLENGE_TYPE_AZ = 'az'
CHALLENGE_TYPE_JOBS = 'jobs'
CHALLENGE_TYPE_CALENDAR = 'calendar'
CHALLENGE_TYPE_CHOICES = [
    (CHALLENGE_TYPE_AZ, 'A-Z Challenge'),
    (CHALLENGE_TYPE_JOBS, 'Job Coverage Challenge'),
    (CHALLENGE_TYPE_CALENDAR, 'Plat Calendar'),
]
#: Frozenset for service validation, mirroring `gamelists.models.LIST_TYPES`.
CHALLENGE_TYPES = frozenset(value for value, _ in CHALLENGE_TYPE_CHOICES)

#: The A-Z alphabet, as the single definition of both the slot keys and how many there are.
#:
#: RAW first letter, no article stripping: "The Last of Us" sits under T. Three precedents agree --
#: the retired A-Z system, the live letter filter (`trophies/views/browse_helpers.py`, a plain
#: `title_name__istartswith`), and PSN's own sorting -- and stripping articles is not indexable
#: without a denormalized column. Two consequences are accepted knowingly: contracts whose name
#: starts with a DIGIT can never fill a letter slot (the eligibility query filters them out
#: explicitly rather than letting them silently never match), and a contract starting with a
#: non-ASCII letter is unpickable under its Latin equivalent. At the time of writing that is one
#: contract out of 2,390 (Okami, spelled with a macron). If that count grows, the fix is a
#: denormalized sort letter fed by `IGDBService._unicode_normalize_for_matching`, NOT a new helper.
AZ_LETTERS = 'ABCDEFGHIJKLMNOPQRSTUVWXYZ'

#: Days per month for the Plat Calendar, as the single definition of both its day keys and how many
#: there are. 365, NOT 366.
#:
#: FEBRUARY IS 28. A calendar run is keyed on (month, day) across ALL years, so 29 February belongs to
#: no single year and would be a slot most hunters cannot fill by accident of the Gregorian calendar
#: rather than by difficulty. The retired `CalendarChallengeDay` excluded it the same way. The
#: difference here is what happens to a platinum earned ON 29 February: it folds into 28 February
#: rather than being dropped, because dropping it silently loses a real platinum from a hunter's
#: history and the alternative costs one line in the fill query.
CALENDAR_MONTH_DAYS = (31, 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31)

#: ONE LENS: A DAY IS FILLED BY A SHOVELWARE-FREE PLATINUM. `in_clean` is the fill; `in_all` is the same
#: population WITHOUT the shovelware exclusion, kept as a comparison figure and never as a second board.
#:
#: THIS REPLACES THREE LENSES, and the collapse is the owner's call (2026-10-04) rather than a
#: simplification found in the code: "perhaps we should condense down to just one view: non-shovelware
#: plats. It makes the system much simpler, we don't have to worry about all the confusion around it
#: all... I know contracts fuel the other challenges but those are more curated sets of games and this
#: is more wholistic." Which is the right distinction: a Contract is a curated subset of the catalogue,
#: and a Calendar is a hunter's whole platinum history laid against the year. Those are different kinds
#: of object and a switcher between them was conflating them.
#:
#: WHAT THE COLLAPSE TOOK WITH IT, recorded because each of these was real work and somebody will wonder
#: whether it was lost by accident:
#:   - the `contracts` lens and `_days_from_contracts`, which was the EXPENSIVE half of the fill (it went
#:     through `eligibility.completion_dates` in batches; the two platinum lenses come out of one
#:     statement). Dropping it made the backfill cheaper, not merely simpler.
#:   - `Challenge.completed_view`, which existed only to say WHICH lens finished a run.
#:   - `Challenge.calendar_contracts_seen` and the `EarnedContract` subquery in `runs_due_for_sweep`,
#:     which existed only because a contracts day could move without a platinum. The reconciliation is
#:     now a single-column comparison.
#:   - the sweep's "finished runs keep filling" exception, which was justified by a hunter still working
#:     toward the OTHER lens's ultimate. With one lens a complete run cannot change: `in_all` is a
#:     superset of `in_clean`, so 365 clean days means 365 all days too.
#:   - a whole class of cross-lens defect that every audit of this feature found some version of: a
#:     per-cell "best lens reached" value that painted a filled tint on a square empty in the active
#:     lens, a per-month crest that said one thing while the squares under it said another, an
#:     `earned_on` that could not be printed because it was the earliest date across any lens, and
#:     screen-reader text that could not follow a switcher it could not see.
#:
#: `in_clean` IMPLIES `in_all` BY CONSTRUCTION -- a shovelware-free platinum is still a platinum -- and
#: `calendarday_clean_implies_all` still enforces it. That constraint is the reason `in_all` is worth
#: keeping: it makes the headline figure interpretable ("297 shovelware-free of 340 platinum days")
#: rather than a bare number, and it costs nothing because the one fill statement computes both.
CALENDAR_VIEW_ALL = 'all'
CALENDAR_VIEW_CLEAN = 'clean'

#: `{key: CalendarDay field}` for the two stored populations, narrowest first. NOT a list of lenses any
#: more: only `clean` is drawn. `all` is here so one loop can read both figures for the comparison line.
CALENDAR_VIEW_FIELDS = (
    (CALENDAR_VIEW_CLEAN, 'in_clean'),
    (CALENDAR_VIEW_ALL, 'in_all'),
)


#: Types that EXIST in the catalogue but cannot be STARTED yet, because their mechanics are not built.
#:
#: WHY THIS IS NEEDED AT ALL, and it is the second trap the Calendar sprang by simply joining
#: `CHALLENGE_TYPE_CHOICES`. My Challenges builds its cards by iterating that list and renders a real
#: POST to `challenge_start` for every non-active card, so adding the choice shipped a working Start
#: button for a type with no fill logic, no completion path and no CSS. Pressing it created a run that
#: could never finish, could never be replaced (one active run per type, and there is no delete), listed
#: itself publicly as `0/365`, and described itself with the Job Coverage copy.
#:
#: THE GATE IS IN THE SERVICE, not only in the template, because the template is one of several doors
#: and the service is the only writer. The card list reads this too, so nothing renders a button whose
#: only outcome is a refusal.
#:
#: Remove the entry in the same change that makes the type playable. An empty frozenset is the normal
#: state; this is scaffolding for the window between a type's rows existing and its rules existing.
#: NOTHING IS GATED OFF ANY MORE. The Calendar sat here from the moment it joined `CHALLENGE_TYPE_CHOICES`
#: -- real enough to read, with no rules to fill or finish it -- and My Challenges renders a Start button
#: per choice, so it had to be refused somewhere. Its ladder is built, so the gate is empty.
#:
#: KEPT RATHER THAN DELETED, because the mechanism is what a fourth type will need on its first day, and
#: because `test_every_challenge_type_declares_its_reward_shape` reads it. An empty frozenset says "every
#: type is creatable" out loud; removing the constant would make the next addition re-derive the argument.
TYPES_NOT_YET_CREATABLE = frozenset()

#: THE DAY-MARKER LADDER'S RUNGS, in filled days. One home for a figure the rail DRAWS and the rewards
#: GRANT, because two copies of a reward threshold is the kind of drift nobody notices until a hunter is
#: owed a title they cannot see.
#:
#: DAYS, NOT MONTHS, and the arithmetic is what settled it. Covering a calendar day needs a platinum
#: earned on that month and day in ANY year, so with N platinums spread across the year expected coverage
#: is `365 * (1 - e^(-N/365))`: 50 days is about 54 platinums, 100 about 117, 200 about 290, 300 about 630
#: and 365 about 2,153. A MONTH ladder was rejected on the same arithmetic -- one full month needs roughly
#: 1,250 platinums, so twelve month rungs are not twelve steps but twelve steps clustered at the ceiling.
#:
#: ASCENDING, AND THAT IS LOAD-BEARING rather than tidy: `rewards.grant_day_markers` walks this in order
#: so the highest rung a run reaches is also the LAST title it earns, which is what makes
#: `granted_titles_for`'s ascending `(earned_at, pk)` last-wins pick the right one for the Hall of Fame.
CALENDAR_DAY_MARKERS = (50, 100, 200, 300, 365)


def calendar_day_keys():
    """The 365 `(month, day)` pairs of a Calendar run, in render order.

    The counterpart of `AZ_LETTERS` for this type: one definition of both the keys and the count, so
    `total_slots` and the created rows cannot disagree.
    """
    return [(month, day)
            for month, days in enumerate(CALENDAR_MONTH_DAYS, start=1)
            for day in range(1, days + 1)]


NAME_MAX_LENGTH = 60

#: How a completed slot got that way. Worth recording rather than inferring, because two of the three
#: are catch-up mechanisms with different justifications and a hunter reading their own run deserves
#: to see which applied.
#:
#: `live`   -- assigned, then completed, detected by the sync hook or the nightly sweep.
#: `import` -- landed complete at assignment, via the history importer: **A-Z runs only**, first run
#:             only, and only for a completion earned after the hunter joined.
#: `hatch`  -- landed complete at assignment, via the scarcity hatch (see `HATCH_THRESHOLD`). **Both
#:             types**, any run.
#:
#: `import` WINS when both apply, which is only ever on A-Z. It is the more specific rule and the one with
#: a fairness date behind it, so it is the more honest label for what happened.
#:
#: A JOBS SLOT STAMPED `import` IS A HISTORICAL ROW, not a live possibility: the importer applied to both
#: types until 2026-09-28. Nothing rejects such a row and no reader treats it specially -- it renders as any
#: other completed square, and it is an honest record of the rule as it stood when it was written. Rewriting
#: it would assert a hatch that may not have been open, or a detection that never happened.
COMPLETED_VIA_LIVE = 'live'
COMPLETED_VIA_IMPORT = 'import'
COMPLETED_VIA_HATCH = 'hatch'
COMPLETED_VIA_CHOICES = [
    (COMPLETED_VIA_LIVE, 'Completed during the run'),
    (COMPLETED_VIA_IMPORT, 'Imported from history'),
    (COMPLETED_VIA_HATCH, 'Filled under the scarcity hatch'),
]
#: Frozenset for the DB check, mirroring `CHALLENGE_TYPES`. An incomplete slot carries `''`.
COMPLETED_VIA_VALUES = frozenset(value for value, _ in COMPLETED_VIA_CHOICES)

#: THE SCARCITY HATCH. When a slot has this many or fewer contracts the hunter could still complete,
#: an already-completed contract becomes selectable for it.
#:
#: This exists because global supply is the wrong measure. The live pool has no empty letters and no
#: job with fewer than 27 contracts, but eligibility is PER HUNTER: a veteran who has already
#: finished four of the six contracts starting with Q has two choices, and a real whale can have
#: none. That failure is invisible in aggregate counts and lands hardest on the most invested
#: hunters. Applies to the two CONTRACT-ATOM types -- Q at 6 and Card Shark at 27 are the same problem,
#: and it is meaningless for `calendar`, which picks nothing: its days are filled by the hunter's own
#: history, so there is no pool to run thin. Card Shark
#: and Maestro are thin structurally (small genres on PlayStation) rather than pending curation, so
#: they will not resolve as the pool grows.
#:
#: Read ONLY by `services.eligibility`, so the rule has exactly one enforcement point. The count it
#: compares against is a DB COUNT computed when a slot's picker opens -- never for all 26 slots on a
#: page render, and never by iterating a profile-scoped queryset in Python.
HATCH_THRESHOLD = 3


class ChallengeQuerySet(models.QuerySet):
    """Reads that cannot forget a flag, mirroring `gamelists.GameListQuerySet`.

    There is no privacy axis here, deliberately: a challenge is a community activity and the hub's
    Hall of Fame is the point of the feature, so challenges are public and soft-deletion is the only
    way to take one out of view. That matches the retired system, which also had no `is_public`.
    """

    #: THE VISIBILITY PREDICATE, hoisted so it has exactly one spelling.
    #:
    #: It was written out twice -- once in `visible()` and once in `readable_by()` -- inside a class whose
    #: docstring is about reads that "cannot forget a flag", and under a `readable_by` docstring that
    #: claimed the flag had one home. The failure that docstring described was therefore live: add a
    #: second condition to `visible()` (a suspended profile's runs dropping out of public view is the
    #: obvious one) and anonymous readers would respect it while every signed-in reader would not.
    #:
    #: `readable_by` CANNOT just call `visible()`, which is the structural detail that made the duplicate
    #: look necessary. `GameListQuerySet.readable_by` does exactly that -- `self.visible().filter(...)` --
    #: because a GameList has two axes and can AND a privacy OR onto a visibility floor. A Challenge has
    #: one flag and has to OR *around* it, so there is no floor to AND onto. Sharing the Q object is what
    #: the precedent actually amounts to here; citing it as "the same call" was wrong.
    VISIBLE = models.Q(is_deleted=False)

    def visible(self):
        return self.filter(self.VISIBLE)

    def readable_by(self, profile):
        """Everything `profile` may OPEN, which is every visible run plus their own hidden ones.

        THE POINT IS THAT THE FLAG LIVES HERE. `ChallengeDetailView` spelled this inline as
        `filter(Q(is_deleted=False) | Q(profile=viewer))` while its own docstring claimed this queryset
        was "the only place that knows it" -- so "visible" had two definitions, one of them in a view.
        The cost lands the day visibility gains a second condition (a suspended profile's runs dropping
        out of public view is the obvious one): `visible()` would get it, the anonymous path would
        inherit it, and the signed-in path would not. A run that should have gone dark would then be
        served to every signed-in visitor and nobody else, which is the hardest kind of leak to notice.

        `profile is None` is an explicit branch rather than something clever, following
        `GameListQuerySet.readable_by`: an anonymous reader gets `visible()` and nothing else. (Not
        strictly required -- `Q(profile=None)` compiles to `profile_id IS NULL`, which a non-null FK never
        matches, so the OR would already reduce correctly. It is here because a reader should not have to
        work that out.)
        """
        if profile is None:
            return self.visible()
        return self.filter(self.VISIBLE | models.Q(profile=profile))

    def owned_by(self, profile):
        return self.visible().filter(profile=profile)

    def active(self):
        """The runs still being worked. Mirrors the one-active-run constraint's predicate exactly."""
        return self.visible().filter(is_complete=False)

    def completed(self):
        """The Hall of Fame's population, most recently finished first.

        Ordering lives in the vocabulary rather than at the call site, because "completed challenges"
        means "newest finish first" on every surface that asks. `-pk` tiebreaks so two runs completing
        inside the same timestamp cannot flip between page loads.
        """
        return self.visible().filter(is_complete=True).order_by('-completed_at', '-pk')


class ChallengeManager(models.Manager.from_queryset(ChallengeQuerySet)):
    """Deliberately NOT filtering in `get_queryset`, for the reasons `GameListManager` states: a
    default manager that hides soft-deleted rows makes `objects` lie about the table, forces a second
    manager for admin and undelete, and lets a cascade or a `count()` disagree with the database. The
    floor is opt-in and one word long, and the service is the only writer.
    """


class Challenge(models.Model):
    profile = models.ForeignKey(Profile, on_delete=models.CASCADE, related_name='challenges')
    # NOT `db_index=True`, following the position `gamelists.GameList.list_type` argues at length: an
    # index on a column of three values over a table this size is read past anyway. The browse that DOES
    # filter on it is served by `chal_type_completed_idx`, which leads with this column, so a bare
    # btree here would be a fourth index earning nothing.
    challenge_type = models.CharField(max_length=10, choices=CHALLENGE_TYPE_CHOICES)
    name = models.CharField(max_length=NAME_MAX_LENGTH)

    #: FROZEN AT CREATION, and that is the whole point of storing a number that looks derivable.
    #:
    #: For `az` it is always 26, and for `calendar` always 365 -- both fixed by their key sets. For
    #: `jobs` it is however many jobs the catalogue held the day the run started, which is why it cannot
    #: be `Job.objects.count()` read at render time: seeding a 26th job would move the goalposts under
    #: every in-flight run and, worse, would un-complete runs that had already finished.
    #:
    #: IT COUNTS THE RUN'S ROWS, WHICHEVER MODEL HOLDS THEM. For the first two types that is
    #: `ChallengeSlot`; for `calendar` it is `CalendarDay`, and a Calendar run has ZERO slots. Both come
    #: from `slot_keys_for`, so the count and the rows cannot disagree -- but code reading this field
    #: must not infer that `challenge.slots.count()` would reproduce it.
    total_slots = models.PositiveSmallIntegerField()

    #: WHAT THE CARD'S "X / 365" COUNTS, and for a Calendar run it is not the obvious number.
    #:
    #: For the two contract-atom types these mean what they say: `filled_count` is slots with a contract
    #: assigned, `completed_count` is slots finished, and the gap between them is what "planned" shows.
    #:
    #: FOR A CALENDAR RUN THEY ARE EQUAL, because a day has no assigned-but-unfinished state -- it is
    #: filled or it is not -- and both track the BEST GENUINE VIEW: the higher of the shovelware-free and
    #: contracts counts. Since either of those completing finishes the run, that number is literally
    #: distance-to-finish. The all-platinums count is deliberately NOT it: that view is the one
    #: shovelware inflates and it cannot finish a run, so leading with it would show a card at 298/365
    #: whose run completes on a view sitting at 164. The all count still renders on the page, as context
    #: rather than as the headline.
    filled_count = models.PositiveSmallIntegerField(default=0)
    completed_count = models.PositiveSmallIntegerField(default=0)

    is_complete = models.BooleanField(default=False)
    completed_at = models.DateTimeField(null=True, blank=True)

    #: WHAT THE CALENDAR SWEEP SAW LAST TIME IT LOOKED -- a reconciliation watermark, not a timestamp.
    #: Zero for every other type.
    #:
    #: THE OBVIOUS SCOPE IS TOO WIDE, which is what these replace. Scoping the nightly sweep to "the
    #: owner has synced since we last looked" catches every hunter who opened the app, and a sync earns
    #: a platinum only occasionally -- most move bronzes, silvers and golds, none of which can fill a
    #: calendar day. That is a full history recomputation per account per night to discover nothing.
    #:
    #: So the sweep asks a CHEAP question first and only then does the expensive thing, and with one
    #: lens the question is ONE NUMBER: a day needs a shovelware-free platinum, so if `total_plats` has
    #: not moved there is nothing a full history read could discover. It is compared in one site-wide
    #: query against the live value, and a run whose number has not moved is skipped without reading a
    #: single trophy.
    #:
    #: THERE WERE TWO OF THESE, and the second is worth naming because its absence is the point. A
    #: `calendar_contracts_seen` counter sat beside this one, because a CONTRACTS day could move without
    #: a platinum -- a contract reaches its 100% tier with no platinum term -- so the sweep had to watch
    #: an `EarnedContract` count as well, through a `Subquery` annotation on every row. The one-lens
    #: collapse removed the lens, the counter, the annotation and the column together.
    #:
    #: NOT `updated_at` AND NOT A SWEEP TIMESTAMP, both of which were tried. `updated_at` only moves when
    #: the run CHANGES, so the first sync after a hunter's last new square leaves it due forever; a bare
    #: "when did we last look" stamp fixes that and still re-sweeps everyone who synced. This is the
    #: two-watermark lesson the contract engine already paid for, one layer further in: the question is
    #: not "has anything happened" but "has anything happened THAT COULD MATTER HERE".
    calendar_plats_seen = models.PositiveIntegerField(default=0)

    is_deleted = models.BooleanField(default=False)
    deleted_at = models.DateTimeField(null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    objects = ChallengeManager()

    class Meta:
        ordering = ['-updated_at']
        indexes = [
            # "my challenges", newest first. Partial on the visible predicate so soft-deleted rows
            # are not in the index at all rather than being scanned and discarded.
            models.Index(fields=['profile', '-updated_at'], name='chal_profile_idx',
                         condition=Q(is_deleted=False)),
            # The Hall of Fame browse, whose predicate is `ChallengeQuerySet.completed()` exactly.
            models.Index(fields=['-completed_at'], name='chal_completed_idx',
                         condition=Q(is_deleted=False, is_complete=True)),
            # The same browse filtered to one type, which is the hub's default view.
            models.Index(fields=['challenge_type', '-completed_at'], name='chal_type_completed_idx',
                         condition=Q(is_deleted=False, is_complete=True)),
        ]
        constraints = [
            # SEQUENTIAL RUNS, ENFORCED IN THE DATABASE. One active run per type per hunter: finish
            # it, then start a fresh one. A partial unique rather than a service-side count, because
            # a count is a read-then-write and two tabs submitting "create" together would both pass
            # it -- the same READ COMMITTED double-insert `game_list_service.create_list` takes a row
            # lock to prevent. Here the constraint IS the lock, and it is cheaper.
            #
            # The predicate has to name BOTH flags: a completed run and a deleted run must each stop
            # occupying the slot, or finishing your A-Z would lock you out of ever starting another.
            models.UniqueConstraint(
                fields=['profile', 'challenge_type'],
                condition=Q(is_complete=False, is_deleted=False),
                name='challenge_one_active_per_type',
            ),
            # `choices` is a form and admin concern; Postgres does not enforce it. Same reasoning as
            # `gamelist_list_type_valid`: the admin, the shell and a data migration all write around
            # the service, and a challenge with an unknown type would render as neither -- no slot
            # keys make sense for it, so there is no UI path back.
            models.CheckConstraint(
                condition=Q(challenge_type__in=[value for value, _ in CHALLENGE_TYPE_CHOICES]),
                name='challenge_type_valid'),
            # A name is how a hunter tells their runs apart, so an empty one is refused in the DB and
            # not only in the service -- a shell and a data migration both write around it.
            # Its limit, stated rather than implied: it catches `''` only, so a whitespace-only name
            # still passes. There is deliberately NO `clean()` to close that: names are GENERATED by
            # the service, never typed, so no form path exists -- and a `clean()` raising on a field
            # the read-only admin's form does not contain turns Save into a ValueError 500 instead of
            # a validation error. An earlier draft added one and that is exactly what it did.
            models.CheckConstraint(condition=~Q(name=''), name='challenge_name_not_blank'),
            # THE DENORMALIZED COUNTERS, ORDERED. Without this, `completed_count=99` on a 26-slot run
            # is legal and renders "99 / 26", and a `total_slots=0` run is complete before it starts.
            # Pinned here because the counters are recomputed from rows by the service on every
            # assignment, so a drift bug shows up as an arithmetic impossibility rather than as a
            # wrong-looking number nobody notices.
            models.CheckConstraint(
                condition=Q(total_slots__gt=0), name='challenge_total_slots_positive'),
            models.CheckConstraint(
                condition=Q(filled_count__lte=F('total_slots')),
                name='challenge_filled_within_total'),
            models.CheckConstraint(
                condition=Q(completed_count__lte=F('filled_count')),
                name='challenge_completed_within_filled'),
            # A completed run has a completion date and an incomplete one does not. Both halves,
            # because the Hall of Fame orders on `completed_at` and a null would sort a finished run
            # into an unpredictable place while a stale date on a reopened run would sort it into a
            # wrong one.
            models.CheckConstraint(
                condition=(Q(is_complete=True, completed_at__isnull=False)
                           | Q(is_complete=False, completed_at__isnull=True)),
                name='challenge_completed_at_matches_flag'),
            # The same rule for the delete pair, for internal consistency rather than for the
            # ordering reason above -- nothing sorts on `deleted_at`. It is here because a
            # soft-deleted row with no deletion date cannot be told apart from a bug, and an undelete
            # path has no timestamp to clear.
            models.CheckConstraint(
                condition=(Q(is_deleted=True, deleted_at__isnull=False)
                           | Q(is_deleted=False, deleted_at__isnull=True)),
                name='challenge_deleted_at_matches_flag'),
        ]

    def __str__(self):
        return f'{self.name} ({self.profile.display_psn_username})'



class ChallengeSlot(models.Model):
    challenge = models.ForeignKey(Challenge, on_delete=models.CASCADE, related_name='slots')

    #: A letter for `az`, a `Job.slug` for `jobs`. One CharField rather than a nullable letter plus a
    #: nullable Job FK: `Job.slug` IS that model's primary key, so an FK would store the same string
    #: while adding a join, a null column and a second shape for every reader to branch on. The job
    #: catalogue is ~25 rows, so resolving slugs to jobs is a bounded dict the page already builds
    #: for its radar (`job_render.build_profile_jobs`).
    key = models.CharField(max_length=50)

    #: RENDER ORDER, stored rather than derived from `key`.
    #:
    #: Sorting on `key` is right for letters and wrong for jobs: job slugs sort alphabetically while
    #: the canonical sequence is the radar's (combat, exploration, mind, heart, finesse), which
    #: `job_render.discipline_order()` exists precisely because it disagrees with the column. Two
    #: disciplines agree before they diverge, so the wrong order reads as correct until the third
    #: row. Stamping position at creation also freezes it, so re-ordering the catalogue later cannot
    #: reshuffle a run somebody is halfway through.
    position = models.PositiveSmallIntegerField()

    #: THE SNAPSHOT. `contract` stays as a live FK for the joins a page wants (cover art, jobs,
    #: platform chips) and is SET_NULL so a deleted contract cannot take a hunter's finished run with
    #: it. The two frozen columns are what the slot actually IS: they survive the contract being
    #: un-published, re-anchored onto a different IGDB id, or absorbed into another concept, all of
    #: which are routine catalogue work. `contract_name` is frozen too, which also makes the A-Z
    #: letter stable -- `Contract.name` is itself a snapshot of the member concept's title at
    #: creation, but a staff rename would otherwise move a filled slot to a different letter.
    #:
    #: There is deliberately NO frozen `igdb_id` here, and the argument is weaker than it was. It was in
    #: the first draft and had no reader; `contract_slug` was then the identity the uniqueness key used.
    #: The key is the FK now, so that half is void -- and a frozen `igdb_id` is what would let a square
    #: recognise its own game after the `Contract` row is deleted and restaged (see `assign`'s dead-snapshot
    #: clause, which has to compare slugs instead). It is still not added, for one concrete reason:
    #: `Contract.igdb_id` is NULL for admin/episodic contracts, so it would need the slug fallback beneath
    #: it anyway and would be a second identity rather than a replacement. Worth revisiting if the
    #: delete-and-restage case ever shows up in practice. The rest of the original argument stands:
    #: A-Z letter comes from `contract_name`, and completion is detected through `EarnedContract`. A
    #: third column nothing reads would go stale on the first re-anchor -- which is the very event
    #: the snapshot exists for -- and a stale value is worse than an absent one.
    contract = models.ForeignKey(Contract, on_delete=models.SET_NULL, null=True, blank=True,
                                 related_name='challenge_slots')
    # `db_index=False` because `SlugField` defaults to True, and a global btree plus a
    # `varchar_pattern_ops` index on this column would serve no read worth indexing. The reason used to be
    # "the only access pattern is the per-challenge partial unique below", and that stopped being true when
    # the unique moved onto the FK: this column now has NO index, and `pending_slots()`'s
    # `exclude(contract_slug='')` plus `assign`'s dead-snapshot clause are live unindexed predicates. Still
    # the right call at 25-26 rows per run; the justification is the table's size, not a covering index.
    contract_slug = models.SlugField(max_length=255, blank=True, default='', db_index=False)
    contract_name = models.CharField(max_length=255, blank=True, default='')

    assigned_at = models.DateTimeField(null=True, blank=True)

    #: COMPLETION IS OURS. Detected from `EarnedContract` but never read from it -- see the module
    #: docstring's point 3 and `reconcile_contracts`, which DELETES that row when derived membership
    #: stops qualifying. Once true this never goes back to false: the hunter did the thing, and the
    #: catalogue's later bookkeeping is not their problem.
    is_completed = models.BooleanField(default=False)
    completed_at = models.DateTimeField(null=True, blank=True)
    completed_via = models.CharField(max_length=10, choices=COMPLETED_VIA_CHOICES, blank=True,
                                     default='')

    #: THE JOB-XP GUARD, and it is load-bearing. `docs/design/rebuild/xp-economy.md` warned about
    #: this case in advance: `grant_job_xp` has no built-in idempotency for grants with a null
    #: `earned_contract`, and the ledger is APPEND-ONLY, so a double-pay cannot be deleted -- only
    #: offset by a negating row.
    #:
    #: THREE guards, not two: this timestamp, a partial unique on
    #: `ContractXPGrant(profile, job, source, source_id)` for `source='challenge'` with `source_id`
    #: set to this slot's id, and a check that such a grant names a slot at all -- without the third,
    #: a `source_id=None` grant collides with nothing and pays without limit. `jobs` runs only; an
    #: `az` slot pays nothing and leaves this null.
    #:
    #: Note what is NOT claimed here: that this is the first non-contract source. An earlier draft
    #: said so and it was false -- `seed_career_demo` has been writing `source='seed'` all along (and
    #: that value is not even in `SOURCE_CHOICES`, which is why no blanket source check could be
    #: added beside the two new constraints). What is true is narrower: this is the first non-contract
    #: source with a USER-FACING payout, and so the first that has to be idempotent.
    #:
    #: NOTE THE WIDTH MISMATCH, which is remote but real: `ContractXPGrant.source_id` is a
    #: `PositiveIntegerField` (int4, max 2,147,483,647) while this app sets `BigAutoField`, so a slot
    #: id above that ceiling could not be recorded as the grant's `source_id` -- and that round trip
    #: is the whole identity of `xpgrant_challenge_once_per_slot`. Two billion slots is not a number
    #: this feature reaches, so it is documented rather than engineered around.
    xp_redeemed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        # `pk` TIEBREAK on a sort that `challengeslot_unique_position` already makes total, so this is
        # belt-and-braces rather than a fix for a reachable bug -- worth saying, because an earlier
        # draft justified it with a duplicate-position scenario the unique below makes
        # unrepresentable. What it actually buys: `Meta.ordering` is what every unqualified read
        # inherits, so if that unique is ever relaxed -- and a reorder feature would have to, since it
        # is not DEFERRABLE -- the grid does not silently start reshuffling between page loads.
        ordering = ['position', 'pk']
        # NO explicit index on (challenge, position). `GameListSection` and `GameListItem` both carry
        # one and an earlier draft copied them -- but neither has a UNIQUE on `position`, which is
        # exactly why they need it and this does not: `challengeslot_unique_position` below already
        # builds a btree on those two columns in that order. A second copy would have contradicted,
        # in the same commit, the argument for dropping `db_index` from `challenge_type`.
        #
        # ONE INDEX, AND IT IS PARTIAL. The nav marker asks "does this hunter have Job XP waiting to be
        # claimed?" on every page for every signed-in hunter (behind a cache), and the rows that can answer
        # yes are the handful in flight -- never the hundreds a played-out account accumulates. A partial
        # index does not carry the settled ones, which is what makes it cheap enough to justify here when
        # the Meta comment above turned down a full one.
        #
        # IT WAS BRIEFLY A DIFFERENT QUESTION. The first version asked "claimed and not yet CELEBRATED",
        # because the ceremony was going to be deferred to the hunter's next Career visit. It is not: the
        # celebration fires on this page, in the same response as the payout, exactly as a contract claim
        # does -- so there is no such thing as an uncelebrated payout and the column that recorded one is
        # gone. What is left is the simpler question, answerable from columns that already existed.
        indexes = [
            models.Index(fields=['challenge'], name='chalslot_unclaimed_xp_idx',
                         condition=Q(is_completed=True, xp_redeemed_at__isnull=True)),
        ]
        constraints = [
            models.UniqueConstraint(fields=['challenge', 'key'], name='challengeslot_unique_key'),
            # One slot per position, which is what makes `ordering` deterministic rather than merely
            # usually-deterministic. Free: `position` is stamped at creation and never changes.
            models.UniqueConstraint(fields=['challenge', 'position'],
                                    name='challengeslot_unique_position'),
            # ONE CONTRACT CANNOT FILL SIX SLOTS. A contract carries up to six jobs, so without this
            # a hunter could point the same game at six Job Coverage slots and bank 36,000 XP for one
            # completion.
            #
            # KEYED ON THE FK, NOT ON THE FROZEN SLUG, and the two are not interchangeable. The service's
            # duplicate check reads `contract_id` (it was moved there when one game filling two job squares
            # paid twice, because a staff rename made the frozen slugs differ while the game was the same).
            # This constraint still read `contract_slug`, so the database and the only writer disagreed about
            # what "the same game" means -- and the disagreement is reachable in the direction that 500s:
            #
            #   1. a hunter fills a square with contract X, freezing slug `sonic-frontiers`;
            #   2. staff rename X, freeing that string;
            #   3. a different contract Y takes the slug `sonic-frontiers` (it is globally unique, so this is
            #      legal the moment it is free);
            #   4. the hunter assigns Y to another square. The service allows it -- different `contract_id` --
            #      and Postgres raises `IntegrityError` on two identical frozen slugs in one run. Uncaught.
            #
            # One key, in both places. `contract_slug` keeps its real job as the snapshot that survives the
            # row being deleted; it is no longer an identity.
            #
            # STILL PARTIAL, BUT FOR A DIFFERENT REASON THAN BEFORE, and the old reasoning must not be carried
            # across. When the key was the blank-not-null slug, the condition was CORRECTNESS: without it every
            # empty slot collided on `''`, and a brand-new run is 26 empty slots. `contract` is NULLABLE, and
            # Postgres treats NULLs as distinct, so a plain unique would already permit unlimited empty slots.
            # The condition now only keeps the index to the rows it is about and keeps the intent readable.
            # `test_a_run_holds_a_full_set_of_empty_slots` therefore no longer pins it -- nothing does, and
            # that is correct rather than a gap.
            #
            # A DELETED CONTRACT STOPS BEING CONSTRAINED, and this is the cost of the change rather than a
            # free consequence -- an earlier version of this comment called it acceptable on two grounds that
            # are both false. `on_delete=SET_NULL` nulls the FK on every square that held the contract, and a
            # partial unique does not constrain NULLs. It is NOT true that "no new assignment can duplicate a
            # contract that is gone from the pool": the game returns as a NEW row (staff re-create it, or
            # `evaluate_contract_candidates._stage_contract` restages it once the delete frees its igdb id),
            # and the dead square cannot recognise it. Nor is it true that those squares "cannot be
            # reassigned" -- `assign` refuses only a COMPLETED square, so an unfinished one with a dead
            # snapshot is freely reassignable and `clear` empties it.
            #
            # WHAT ACTUALLY CLOSES IT is a second clause in `assign`'s duplicate guard, matching a dead
            # square's frozen slug. It cannot live here: the comparison is between one row's snapshot and
            # another row's contract's CURRENT slug, which is cross-row and cross-table, so no unique index
            # can express it. `assign` is the only writer, so a guard there is the whole rule in practice --
            # and it refuses cleanly where the old slug-keyed constraint raised `IntegrityError`.
            models.UniqueConstraint(
                fields=['challenge', 'contract'],
                condition=Q(contract__isnull=False),
                name='challengeslot_unique_contract'),
            # An empty slot cannot be completed, and a filled one cannot be completed without a date
            # and without saying HOW. Guards the state machine at the only level every writer passes.
            #
            # The third clause was missing from the first draft while this comment already claimed it,
            # so a completed slot with `completed_via=''` was accepted. It matters because the three
            # values are not decoration: two of them (`import`, `hatch`) are catch-up mechanisms with
            # different justifications, and a hunter reading their own finished run is entitled to see
            # which one applied to which slot. A blank would render as neither.
            #
            # `~Q(field='')` rather than `field__gt=''` in both places, deliberately one spelling.
            # A PREFERENCE, not a requirement -- `__gt=''` is a legal partial-index predicate too, and
            # the two are equivalent under any deterministic collation, which is what a slug column
            # has. The reason to pick one is that "filled" is load-bearing here and should not be
            # spelled three different ways across two constraints and a property.
            models.CheckConstraint(
                condition=(Q(is_completed=False)
                           | (Q(is_completed=True, completed_at__isnull=False)
                              & ~Q(contract_slug='') & ~Q(completed_via=''))),
                name='challengeslot_completed_is_filled_dated_and_explained'),
            # `choices` is not enforced by Postgres, and the argument `challenge_type_valid` makes
            # applies here verbatim: the admin, a shell and a data migration all write around the
            # service. An unknown `completed_via` renders as none of the three labels.
            models.CheckConstraint(
                condition=Q(completed_via='') | Q(completed_via__in=sorted(COMPLETED_VIA_VALUES)),
                name='challengeslot_completed_via_valid'),
            # XP can only have been redeemed for a completed slot.
            models.CheckConstraint(
                condition=Q(xp_redeemed_at__isnull=True) | Q(is_completed=True),
                name='challengeslot_xp_needs_completion'),
        ]

    def __str__(self):
        return f'{self.challenge_id}:{self.key} -> {self.contract_slug or "(empty)"}'

    @property
    def is_filled(self):
        """Filled means a contract is ASSIGNED, which is the snapshot's presence and not the FK's.

        Reading `self.contract_id` instead would report an assigned slot as empty the moment the
        contract row went away, which is precisely the case the snapshot exists to survive.
        """
        return bool(self.contract_slug)


class CalendarDay(models.Model):
    """One of the 365 day squares in a Plat Calendar run.

    ITS OWN MODEL RATHER THAN A `ChallengeSlot`, and the reasons are structural rather than tidiness.
    A slot's atom is a CONTRACT: it carries a `contract` FK, a frozen slug/name/igdb_id snapshot, a
    partial unique forbidding one contract from filling two slots of a run, and `xp_redeemed_at`. A
    calendar day's atom is a DATE. None of those four apply, a day is filled by a platinum in two of
    the three views, and a day legitimately has SEVERAL satisfiers where a slot has exactly one. Bolting
    a nullable second identity onto `ChallengeSlot` would have meant four always-null columns, one
    constraint that never fires, and a `key` column meaning two different kinds of thing.

    PRE-CREATED, ALL 365, mirroring `start_reporting`'s `bulk_create` of A-Z letters and job slugs. A
    calendar always draws every day whether filled or not, so rows-for-filled-days-only would trade a
    cheap ordered queryset for assembling the grid in Python and left-joining onto it. The row count is
    the honest cost of that and it is bounded: one run is 365 rows, and runs are sequential per profile.

    TWO NESTED BOOLEANS, AND ONLY ONE OF THEM IS A BOARD. `in_clean` is the fill -- a day a
    shovelware-free platinum landed on -- and `in_all` is the same population without that exclusion,
    kept so the page can say "297 shovelware-free of 340 platinum days" rather than a bare figure. The
    comparison is the only reason the second column exists, and it costs nothing because one statement
    computes both.

    `in_clean` IMPLIES `in_all` BY CONSTRUCTION: a shovelware-free platinum is still a platinum, so
    `calendarday_clean_implies_all` below is a real integrity guard and exactly what catches a backfill
    that half-populated one of them.

    THERE WAS A THIRD, `in_contracts`, and this docstring argued at length for keeping all three
    independent because that one genuinely did not nest -- `contract_service._detect_tiers` reaches the
    100% tier with no platinum term, so a contracts day need not be a platinum day. The owner collapsed
    the feature to one lens (2026-10-04) on the grounds that contracts are curated subsets while a
    calendar is a whole history, which is a distinction the switcher was blurring. See the note on
    `CALENDAR_VIEW_FIELDS` for everything that came out with it.

    WHAT IS STORED IS THE FILL; WHAT IS COMPUTED IS THE SATISFIERS. The booleans are snapshots, so a
    catalogue correction the hunter never saw -- a shovelware reclassification, a `reconcile_contracts`
    deletion -- cannot un-fill a day they already earned. The LIST of games behind a day is derived live
    when the day modal opens, because it is a view onto trophy data that no longer needs to be frozen
    once the day itself is.
    """
    #: `db_index=False` because `calendarday_unique_day` already leads on this column, so Django's
    #: automatic FK btree would be a second index over the same thing -- paid for on every one of the
    #: 365 inserts a run creation does.
    challenge = models.ForeignKey(
        'challenges.Challenge', on_delete=models.CASCADE, related_name='calendar_days',
        db_index=False)

    month = models.PositiveSmallIntegerField(help_text='1-12.')
    day = models.PositiveSmallIntegerField(help_text='1-31, within that month.')

    #: `in_clean` IS THE FILL; `in_all` is the same population without the shovelware exclusion, kept
    #: for the comparison line. See the class docstring: nested, not a ladder.
    in_all = models.BooleanField(default=False)
    in_clean = models.BooleanField(default=False)

    #: The LOCAL DATE that first filled this day, resolved in the hunter's own timezone -- from a
    #: platinum's `earned_date_time` or a 100% completion's `most_recent_trophy_date`. User-facing
    #: ("first filled 3 March 2019"), and the reason a day cell can say anything at all.
    #:
    #: A `DateField`, NOT a `DateTimeField`, and the difference is a rendering bug rather than a
    #: preference. The day KEY is a local date by decision -- the hunter's timezone decides which square
    #: a platinum lands on -- but an instant gets re-interpreted by whoever reads it: `plat_pursuit/
    #: middleware.py` activates the VIEWER's timezone and the run page is PUBLIC, so a day keyed (3, 3)
    #: holding `2019-03-03 23:40Z` renders as 4 March to a reader in Tokyo, on the square labelled
    #: 3 March. The owner sees it correctly and nobody else necessarily does. Storing the resolved date
    #: makes the stored value the same fact as the key, and no timezone can disagree with it.
    #:
    #: The instant is not kept alongside it: the unit of this feature is the day, nothing shows a time,
    #: and two representations of one fact is how they drift. Note this is the ONLY record of the
    #: achievement date once a day is filled -- `EarnedContract.*_reached_at` are DETECTION stamps
    #: ("became claimable"), not when the hunter earned it.
    earned_on = models.DateField(null=True, blank=True)

    #: HOW MANY PLATINUMS THE HUNTER HOLDS ON THIS SQUARE, counted in the lens the square draws in --
    #: `clean` for a square on the board, `all` for one that only counts. Stored rather than counted at
    #: render time because the board is ONE query over this table and a count would mean a second,
    #: timezone-aware, whale-sized aggregate on the request path. The fill already groups the hunter's
    #: platinums by (month, day) to find each square's earliest date, so this rides a statement that is
    #: already running. (One such query per DETAIL page. This said "eight of them on a Hall of Fame
    #: page", which is the one page that does not draw the figure -- `_hero_group`'s cells carry no
    #: count.)
    #:
    #: LIVE, NOT FROZEN, AND IT CAN DISAGREE WITH `earned_on`. A date is the record of an achievement,
    #: so it survives a game being reclassified as shovelware; a count is a tally, and the day modal
    #: will derive the same tally from trophy rows when a hunter opens a day. A glance figure that
    #: contradicts the list the hunter just opened is worse than one that contradicts history they
    #: cannot see, so this follows the aggregate downwards -- to zero if the lens empties.
    #:
    #: SO THE TWO FIELDS CAN DESCRIBE DIFFERENT POPULATIONS, and an earlier version of this comment
    #: claimed they "always describe one population" while the slice's own
    #: `test_the_count_follows_the_aggregate_down_where_the_date_does_not` asserted the opposite. After
    #: a reclassification a square can read "first filled 2015" beside a count holding nothing from
    #: 2015. That is the designed split, not a bug: the date answers when the square was earned, the
    #: count answers what the modal will list.
    #:
    #: THEY AGREE UNTIL A LENS'S OWN EARLIEST RISES, which is the real boundary and NOT "every square
    #: whose lens still holds it" as this said a slice ago -- a square with a flagged 2015 platinum and
    #: a clean 2021 one is still held by the clean lens, and the two fields disagree on it. Anything
    #: that removes the earliest member of a square's population reaches this: a shovelware flag, a
    #: hunter changing their own timezone (which re-keys every day), or an `EarnedTrophy` row going away
    #: on a resync correction. Not only staff.
    #:
    #: ON A SQUARE THAT DRAWS IN NO LENS this holds the `all` figure, so it is shovelware-INCLUSIVE and
    #: must not be shown. Every reader tests `in_clean` first -- `calendar_render._is_filled` is the one
    #: place that decision lives.
    #:
    #: THE LEAP-DAY FOLD ADDS RATHER THAN PICKS. 29 February folds onto 28 February, so a hunter who
    #: platted on both holds two platinums on that one square. Dates take a `min` on that collision;
    #: counts sum, which is the one place the two fields' fold logic must differ.
    plat_count = models.PositiveSmallIntegerField(default=0)

    #: When WE wrote the fill. Separate from `earned_on` because they answer different questions and
    #: conflating them is the mistake `*_reached_at` already made elsewhere in this codebase: a
    #: detection timestamp is not an achievement timestamp.
    filled_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['month', 'day']
        constraints = [
            models.UniqueConstraint(fields=['challenge', 'month', 'day'],
                                    name='calendarday_unique_day'),
            # The keys are generated by `calendar_day_keys()`, so these guard a hand-written row or a
            # data migration rather than the normal path -- which is exactly when a bad key would be
            # hardest to notice.
            models.CheckConstraint(condition=Q(month__gte=1, month__lte=12),
                                   name='calendarday_month_range'),
            models.CheckConstraint(condition=Q(day__gte=1, day__lte=31),
                                   name='calendarday_day_range'),
            # `clean` is `all` minus shovelware, so a clean day is always an all day. The database says
            # so because this is the shape a half-written backfill produces -- three views populated by
            # three predicates, one of them wrong or interrupted -- and that is a silent wrong answer
            # on a reward ladder rather than a visible failure. (It used to carry a note that it was
            # deliberately NOT extended to a third `in_contracts` column, which genuinely did not nest;
            # that column went with the one-lens collapse.)
            models.CheckConstraint(condition=Q(in_clean=False) | Q(in_all=True),
                                   name='calendarday_clean_implies_all'),
            # THE DAY MUST EXIST IN ITS OWN MONTH, which `calendarday_day_range` does not say: it caps
            # the day at 31 for every month, so `(2, 29)`, `(2, 31)` and `(4, 31)` are all legal under
            # it. That is not a theoretical gap, because of how the board is DRAWN: `calendar_render`
            # generates its cells from `calendar_day_keys()` and looks the rows up against them, which
            # makes it immune to a MISSING row and blind to an EXTRA one. A row on an impossible date
            # is therefore invisible on the board forever -- while `calendar_fill._recount_calendar`
            # aggregates with no key filter, so it still counts toward `filled_count` and can push a
            # run to `is_complete` on a square its owner cannot see or reach. Same class as the
            # `_board_groups` regression (a tally the board disagrees with), in the opposite direction.
            #
            # 28 IS ALWAYS SAFE, so February needs no clause of its own -- a leap day folds into the
            # 28th by decision, which is why 29 February must be refused here rather than tolerated.
            # The two month lists are the 31-day and 30-day months; anything in neither falls through
            # to the `day <= 28` branch.
            models.CheckConstraint(
                condition=(Q(day__lte=28)
                           | Q(month__in=[1, 3, 5, 7, 8, 10, 12])
                           | (Q(month__in=[4, 6, 9, 11]) & Q(day__lte=30))),
                name='calendarday_day_within_month'),
        ]
        # NO INDEXES BEYOND THE UNIQUE, and the three partials this replaces were a reflex rather than a
        # measurement. One per view, each `(challenge) WHERE in_<view>`, on the theory that "how many
        # filled" wants its own index.
        #
        # They bought nothing. Every query here is scoped to ONE challenge, where the unique composite
        # already yields at most 365 rows -- and the query the surfaces actually want (the three view
        # totals and the twelve crests at once) is a single grouped pass over exactly those rows:
        #
        #     SELECT month, COUNT(*) FILTER (WHERE in_all), ... WHERE challenge_id = X GROUP BY month
        #
        # Meanwhile `in_*` are precisely the columns a fill UPDATES, so three extra indexes turn every
        # fill into a non-HOT update maintaining three more entries, and every run creation into 365
        # inserts across six indexes instead of two. Cost on the write path, nothing on the read path.

    def __str__(self):
        return '%02d-%02d' % (self.month, self.day)
