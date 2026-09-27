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
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import F, Q

from trophies.models import Contract, Profile

#: The two types. `az` is the returning A-Z Challenge; `jobs` is the new Job Coverage Challenge,
#: which replaces the retired Genre challenge. The Platinum Calendar does NOT return.
#:
#: "A-Z Challenge", not "A-Z Platinum Challenge": platinum stopped being a requirement, so the old
#: name would now overstate the bar. The placeholder page's public promise already used the bare form
#: ("your past A-Z runs are safe"), so the community-facing string does not change.
CHALLENGE_TYPE_AZ = 'az'
CHALLENGE_TYPE_JOBS = 'jobs'
CHALLENGE_TYPE_CHOICES = [
    (CHALLENGE_TYPE_AZ, 'A-Z Challenge'),
    (CHALLENGE_TYPE_JOBS, 'Job Coverage Challenge'),
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

NAME_MAX_LENGTH = 60

#: How a completed slot got that way. Worth recording rather than inferring, because two of the three
#: are catch-up mechanisms with different justifications and a hunter reading their own run deserves
#: to see which applied.
#:
#: `live`   -- assigned, then completed, detected by the sync hook or the nightly sweep.
#: `import` -- landed complete at assignment, via the first-run history importer.
#: `hatch`  -- landed complete at assignment, via the scarcity hatch (see `HATCH_THRESHOLD`).
#:
#: `import` WINS when both apply. It is the more specific rule (first run only) and the one with a
#: fairness date behind it, so it is the more honest label for what happened.
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
#: hunters. Applies to BOTH types -- Q at 6 and Card Shark at 27 are the same problem, and Card Shark
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

    def visible(self):
        return self.filter(is_deleted=False)

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
    # index on a two-value column over a table this size is read past anyway. The browse that DOES
    # filter on it is served by `chal_type_completed_idx`, which leads with this column, so a bare
    # btree here would be a fourth index earning nothing.
    challenge_type = models.CharField(max_length=10, choices=CHALLENGE_TYPE_CHOICES)
    name = models.CharField(max_length=NAME_MAX_LENGTH)

    #: FROZEN AT CREATION, and that is the whole point of storing a number that looks derivable.
    #:
    #: For `az` it is always 26. For `jobs` it is however many jobs the catalogue held the day the
    #: run started -- which is why it cannot be `Job.objects.count()` read at render time: seeding a
    #: 26th job would move the goalposts under every in-flight run and, worse, would un-complete
    #: runs that had already finished. The `ChallengeSlot` rows created at the same moment are the
    #: real snapshot; this is their count, kept so a card does not need a query to show "18 / 25".
    total_slots = models.PositiveSmallIntegerField()
    filled_count = models.PositiveSmallIntegerField(default=0)
    completed_count = models.PositiveSmallIntegerField(default=0)

    is_complete = models.BooleanField(default=False)
    completed_at = models.DateTimeField(null=True, blank=True)

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
            # not only in the service -- the admin, a shell and a data migration all write around it.
            # Its limit, stated rather than implied: it catches `''` only, so a whitespace-only name
            # still passes here. `clean()` catches that for any ModelForm, and the service strips
            # before it writes; neither runs for those same out-of-service writers.
            models.CheckConstraint(condition=~Q(name=''), name='challenge_name_not_blank'),
            # THE DENORMALIZED COUNTERS, ORDERED. Without this, `completed_count=99` on a 26-slot run
            # is legal and renders "99 / 26", and a `total_slots=0` run is complete before it starts.
            # Pinned here because the counters are recomputed from rows by the service on every
            # assignment, so a drift bug shows up as an arithmetic impossibility rather than as a
            # wrong-looking number nobody notices.
            models.CheckConstraint(
                condition=Q(total_slots__gt=0), name='challenge_has_slots'),
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

    def clean(self):
        """Catch the whitespace-only name that `challenge_name_not_blank` cannot.

        Mirrors `GameList.clean()`. The check constraint compares against `''`, so `'   '` reaches
        the database intact; this is what closes it for the admin and any ModelForm.
        """
        if self.name and not self.name.strip():
            raise ValidationError({'name': 'A challenge needs a name.'})


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
    #: There is deliberately NO frozen `igdb_id` here. It was in the first draft and had no reader:
    #: `contract_slug` is the identity the uniqueness key uses and the re-find path would use, the
    #: A-Z letter comes from `contract_name`, and completion is detected through `EarnedContract`. A
    #: third column nothing reads would go stale on the first re-anchor -- which is the very event
    #: the snapshot exists for -- and a stale value is worse than an absent one.
    contract = models.ForeignKey(Contract, on_delete=models.SET_NULL, null=True, blank=True,
                                 related_name='challenge_slots')
    contract_slug = models.SlugField(max_length=255, blank=True, default='')
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

    #: THE JOB-XP GUARD, and it is load-bearing. A challenge redeem is the FIRST non-contract source
    #: to write the job-XP ledger, and `docs/design/rebuild/xp-economy.md` warned about this case in
    #: advance: `grant_job_xp` has no built-in idempotency for grants with a null `earned_contract`,
    #: and the ledger is APPEND-ONLY, so a double-pay cannot be deleted -- only offset by a negating
    #: row. This timestamp is the first of two guards; the second is a partial unique index on
    #: `ContractXPGrant(profile, job, source, source_id)` for `source='challenge'`, where `source_id`
    #: is this slot's id. `jobs` runs only; an `az` slot pays nothing and leaves this null.
    #:
    #: NOTE THE WIDTH MISMATCH, which is remote but real: `ContractXPGrant.source_id` is a
    #: `PositiveIntegerField` (int4, max 2,147,483,647) while this app sets `BigAutoField`, so a slot
    #: id above that ceiling could not be recorded as the grant's `source_id` -- and that round trip
    #: is the whole identity of `xpgrant_challenge_once_per_slot`. Two billion slots is not a number
    #: this feature reaches, so it is documented rather than engineered around.
    xp_redeemed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        # `pk` TIEBREAK, not a bare `position`. Positions are stamped once and unique per run (below),
        # so in practice the sort is total -- but `Meta.ordering` is what every unqualified read
        # inherits, and a repair or backfill that ever duplicated a position would otherwise make the
        # slot grid reshuffle between page loads. Same reasoning as `ChallengeQuerySet.completed()`.
        ordering = ['position', 'pk']
        indexes = [
            # The `ordering` read, matching what `GameListSection` and `GameListItem` both carry for
            # their own `position` sort. Cheap either way at 26 rows; it is here so the app does not
            # diverge from its template without a reason.
            models.Index(fields=['challenge', 'position'], name='chalslot_position_idx'),
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
            # PARTIAL, and that is the part that is easy to get wrong rather than a detail: Postgres
            # treats NULLs as distinct, so a plain unique over a nullable column permits unlimited
            # duplicates among the EMPTY slots -- which is every slot on a brand-new run. The same
            # trap is documented for `GameListItem`'s nullable game column. `contract_slug` is
            # blank-not-null for exactly this reason, so the condition is a string test.
            models.UniqueConstraint(
                fields=['challenge', 'contract_slug'],
                condition=~Q(contract_slug=''),
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
            # `~Q(field='')` rather than `field__gt=''` in both places, deliberately one spelling:
            # "filled" is a load-bearing concept here and the partial unique above has to use the
            # negated-equality form for its index predicate, so matching it keeps one meaning of the
            # word in the DB layer. (The two are equivalent under any deterministic collation, which
            # is what a slug column has.)
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
