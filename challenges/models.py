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
    #: There is deliberately NO frozen `igdb_id` here. It was in the first draft and had no reader:
    #: `contract_slug` is the identity the uniqueness key uses and the re-find path would use, the
    #: A-Z letter comes from `contract_name`, and completion is detected through `EarnedContract`. A
    #: third column nothing reads would go stale on the first re-anchor -- which is the very event
    #: the snapshot exists for -- and a stale value is worse than an absent one.
    contract = models.ForeignKey(Contract, on_delete=models.SET_NULL, null=True, blank=True,
                                 related_name='challenge_slots')
    # `db_index=False` because `SlugField` defaults to True, and a global btree plus a
    # `varchar_pattern_ops` index on this column would serve no read that exists: the only access
    # pattern is the per-challenge partial unique below.
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
            # duplicates among the EMPTY slots -- which is every slot on a brand-new run.
            # `contract_slug` is blank-not-null for exactly this reason, so the condition is a string
            # test and the index holds only assigned slots.
            #
            # NO precedent is cited here on purpose. An earlier draft pointed at "`GameListItem`'s
            # nullable game column", which does not exist -- that column was planned in
            # docs/design/game-list-types.md and never built; the shipped model's `concept` is
            # non-null and its unique is plain. Mutation-verified instead: drop the condition and
            # `test_a_run_holds_a_full_set_of_empty_slots` fails on the 26-row bulk_create.
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
