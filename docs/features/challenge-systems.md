# Challenge Systems

Platinum Pursuit's Challenges: self-imposed collection goals with PlatPursuit-authored rules and
PlatPursuit-specific rewards. A Game List is hunter-authored, curated and unrewarded; a Challenge is the
opposite of all three, and that is the line between the two features.

---

## Status

The system was **demolished in 2026-08** (`trophies/migrations/0281_drop_challenge_system`, finished by `0292_drop_challenge_showcase`) and **rebuilt from
scratch** on `feature/challenges/rebuild`. This doc describes the rebuild. The old system shares no models,
no services and no templates with it; the few lessons worth carrying forward are in
[What the retired system taught us](#what-the-retired-system-taught-us).

| Piece | State |
|---|---|
| Models, constraints, admin | **built** |
| `challenge_service` / `eligibility` / `picker` / `slot_render` | **built** |
| Detection (sync hook + `process_challenges` + nightly) | **built** |
| My Challenges (`/my-challenges/`) | **built** |
| The run's page (`community/challenges/<id>/`) | **built** |
| The picker: square-first, contract-first, history | **built** |
| Public hub + Hall of Fame | **NOT built.** `community/challenges/` is still the coming-soon placeholder |
| Rewards (titles, job-XP payout, notification) | **built.** `challenges/services/rewards.py` is the only writer |
| Beta gate (`CHALLENGES_BETA_MEMBERS_ONLY`) | **built, and on by default** |
| Badge + holo award | **deferred to a follow-up branch**, post-beta. Completions are recorded from day one so badges backfill |

What a challenge is worth is now stated on both surfaces a hunter sees — the Start card before they
commit, and the run's own page — from one reader, so the two cannot quote different figures. See
[Rewards](#rewards) below.

---

## The two types

Calendar and Genre challenges do **not** return.

| | A-Z Challenge | Job Coverage Challenge |
|---|---|---|
| Squares | 26, one per letter `A`-`Z` | one per `Job` in the catalogue, read live at creation |
| A square's key | the letter | the job's slug |
| What fits | a Contract whose `name` starts with that letter | a Contract carrying that job |
| Win condition | every letter | every job, Freelancer included |

**Starting a run is members-only during the beta.** `CHALLENGES_BETA_MEMBERS_ONLY` (env var, defaults
to **on**) is checked inside `start_reporting` after the profile lock, so it refuses the write rather than
hiding the door: a free hunter sees the Start button marked `aria-disabled`, with the reason as visible text
above it. `aria-disabled` deliberately rather than `disabled` — a disabled button takes no pointer events, so
its explanation is unreachable — which means the POST still goes through and the **service** is what refuses
it. It is an env var so ending the beta is a config flip rather than a deploy. Staff preview the free-tier view with
`?preview=challenges-free`. A lapsed member keeps a run they already started; the gate is on starting, not
on owning.

**Runs are sequential.** One active run per type per hunter, enforced by a partial unique
(`challenge_one_active_per_type`) rather than a service-side count — a count is a read-then-write and two
tabs would both pass it.

**There is no delete, only hide.** Hiding takes a run off the profile and out of the hub; pressing Start
again resumes that same run with its progress. The card's verb changes to "Resume" so pressing it is never
a surprise.

---

## Three architectural bets

### 1. The slot atom is a Contract, and platinum is not required

A square holds a `Contract` (the Job Board's unit), not a `Game` or a `Concept`. Completion is "the hunter
finished this contract" at either tier, which reuses the contract engine's own oracle rather than
re-deriving one. A single completion can legitimately advance an A-Z square **and** a Job Coverage square;
that is a feature, and only the Job Coverage side will pay XP.

### 2. The slot snapshots its contract's identity

`ChallengeSlot` stores the FK **plus** frozen `contract_slug` and `contract_name`. A finished square must
say what it said the day it was finished, whatever staff later do to the catalogue — rename, re-anchor,
delete.

`contract_slug` is a **snapshot, not an identity**. The uniqueness key is the FK (see
[Gotchas](#gotchas-and-pitfalls)).

### 3. Completion belongs to the slot, never to `EarnedContract`

`reconcile_contracts` **deletes** `EarnedContract` rows when derived membership stops qualifying (a Concept
split, a re-anchor, a match leaving the trusted statuses, a staff `igdb_id` edit). A slot that read its
completion live from that table would silently un-complete a finished run because of a catalogue correction
the hunter never saw.

So the slot owns `is_completed` / `completed_at` / `completed_via`, and `EarnedContract` is consulted only
as the **detector**. Once true, never false again: the hunter did the thing, and the catalogue's later
bookkeeping is not their problem.

---

## The two catch-up rules

Filling a square normally requires a game the hunter has **not** completed — otherwise the square would
land complete instantly. Two rules lift that, and they are not interchangeable: a square records which one
applied, permanently, and a hunter reading their own finished run is entitled to see it.

| | History importer (`import`) | Scarcity hatch (`hatch`) |
|---|---|---|
| Types | **A-Z only** | **both** |
| When | first run only — never completed one of that type | whenever the square's eligible pool is ≤ `HATCH_THRESHOLD` (3) |
| Test | the completion happened **strictly after** `CustomUser.date_joined` | none; it is about our supply, not their timing |
| Why it exists | a hunter has been here a while and finished contracts covering half the alphabet before ever starting a run | our curation gap for a thin job or letter |
| Reward | full payout. Our gap, not their shortcut | same |

`import` wins when both apply, which only ever happens on A-Z.

**Either rule lands the square COMPLETE, so the write confirms first — in the service, not the client.**
`assign(..., acknowledge_lock=False)` raises `ConfirmationRequired` for any placement that would land a
square complete, for the hatch as much as for the importer. A confirmation the server does not know about is
one a stale tab, a double-submit or a second client can skip, and this is the only action on a run that
cannot be undone. Once complete, a square is **never clearable** — which is also what stops a job being
paid twice, since the XP guard keys on the slot. Incomplete squares clear and reassign freely. Starting a run and every slot write additionally require a
linked profile (`_refuse_if_unlinked`); `hide` requires only ownership, and its view enforces the link.

**The date test is narrower than "a library that already covers the alphabet", deliberately.** Only
completions **strictly after** `date_joined` count, so nothing earned before signing up ever imports. The
boundary is the signup **instant**, not the day: `date_joined` is a datetime, so a contract finished an hour
after joining does import. That is the anchor doing its job (an unbounded import would finish a run in one
sitting), but it means the importer rewards time spent here rather than a back catalogue. Any copy promising
otherwise is wrong.

**One gate.** `challenge_service.importer_is_available` is the only place the importer's rule lives, and
every caller reaches it rather than re-expressing it — the offers builder, the history panel, and the page's
own decision about whether the door opens at all. So the panel cannot offer an import that `assign` would
refuse, or the reverse. On Job Coverage the type check short-circuits before the
query, so a jobs panel pays nothing for it.

**Why the date comes from trophy data.** `EarnedContract`'s `*_reached_at` columns stamp `timezone.now()`
at **detection** time, not when the hunter earned anything — a contract staged today and published next
month stamps them for everyone who finished it years ago, and the weekly full sweep does the same. So the
importer reads the real moment out of `EarnedTrophy.earned_date_time` / `ProfileGame.most_recent_trophy_date`
and takes the **earliest** qualifying one.

---

## The picker's three panels

One `<dialog>`, three modes, sharing one row container. A hunter arrives with one of three questions and
none is a subset of another.

| Panel | The question | Route |
|---|---|---|
| Square-first (`slot_panel`) | "this square is empty — what fits?" | `challenge_slot` |
| Contract-first (`search_panel`) | "I just finished this — where does it go?" | `challenge_search` |
| History (`history_panel`) | "what have I already finished that counts?" | `challenge_history` |

Every list is a bounded slice; the expensive per-hunter work touches only the slice, never the pool. The
two slot-shaped panels carry a DB `COUNT` beside their slice. **`history_panel` deliberately does not** —
membership there depends on a completion date read from trophy data, so an honest total would mean dating
the whole pool. It reports a boolean and whether its window was exhaustive.

### The history window

The pool is "completed, live, fits an open letter". Whether a candidate is *importable* depends on its
date, which is not a SQL predicate — so the panel dates a `HISTORY_SCAN` (96) window and pages the result
to `PAGE` (24).

Slicing to `PAGE` **first** was a shipped bug: a hunter with two dozen pre-join completions early in the
alphabet filled the window with them, and the panel said "Nothing here yet" while a post-join game sat
under Z. When the window is not exhaustive the panel says so, because "nothing importable" is a claim it
cannot make.

Ordering by a detection stamp would not help. Those record when *we noticed*, and a first-run hunter's
first sync notices a 2019 platinum and last week's within minutes of each other — useless as a proxy for
exactly the population the importer serves.

---

## Detection

A square completes when an `EarnedContract` row exists for `(profile, contract)`. Two paths, neither
trusting that table for storage:

1. **The sync hook** (`challenge_service.detect_for_profile`, called from `token_keeper` on sync
   completion). It sits **immediately after** the block that creates `EarnedContract` rows, and that
   ordering is why it lives exactly there: run first, it would miss every square finished on this very
   sync and leave it for the nightly — so a hunter watching their own sync land would see the trophy
   arrive and the square stay empty.

   Deliberately **unscoped** by contract, unlike its neighbour: a hunter's pending squares are bounded by
   one A-Z run plus one jobs run, so one query settles it and narrowing to the concepts this sync touched
   would cost more than it saves.
2. **`process_challenges`**, nightly. It catches the contract published *after the hunter finished the
   game* (they have not synced since, so nothing on the sync path ever runs for them), and any other
   `EarnedContract` row written off the sync path — the nightly contract sweep, a staff
   `process_contracts --contract`, a re-earn after a reconcile. "Published after assignment" is NOT one of
   these: both `assign` and `eligibility.live_contracts` refuse a draft, so a square can only ever have been
   assigned a contract that was live at the time.

**The nightly ordering is load-bearing.** `process_challenges` must run **after** `process_contracts`,
which itself must follow the DLC sweep. `core/management/commands/nightly.py` documents the chain and its
reasons; the step list is the single definition of the order.

---

## Rewards

`challenges/services/rewards.py` is the only thing that writes one.

| | A-Z Challenge | Job Coverage Challenge |
|---|---|---|
| On completion | a title | a title |
| Per finished square | nothing | `CHALLENGE_SLOT_JOB_XP` to that square's job, **redeemed by the hunter** |
| Titles | A-Z Champion (1st completion), A-Z Legend (2nd) | Job Challenge Champion, Job Challenge Legend |
| Badge + holo | deferred to a follow-up branch | deferred |

**A-Z pays no job XP, ever** — not "not yet". A letter is not a job, so there is nothing for job XP to land
in, and inventing a target would be a random payout into a system it does not belong to. The rule is
enforced in the service rather than by a constraint, because no table constraint can see a slot's challenge
*type*: that lives one FK away.

**Paid per square, as they finish.** Not gated on the run completing. I argued for gating it, on the theory
that hiding a run released the one-active-run constraint and so allowed a bank-hide-restart farm — that was
wrong: `start_reporting` is resume-before-create, so pressing Start un-hides the same run and there is no
route to a second run of a type without finishing the first.

**The catch-up rules pay full.** A square filled by the history importer or the scarcity hatch pays exactly
what a live one pays. The hatch covers *our* supply gap, and the importer is a head start we offered; neither
is the hunter's shortcut.

### The three guards on one payout

The job-XP ledger is append-only: rows are never rewritten, and the only honest reversal is a negating row.
So a double-pay cannot be undone, only offset — and `grant_job_xp` has no idempotency of its own for
non-contract sources. Three things stand behind one square's XP:

| Guard | What it catches |
|---|---|
| `ChallengeSlot.xp_redeemed_at` | the second press, as a **sentence** a hunter can read |
| `xpgrant_challenge_once_per_slot` | a bug past the stamp, as an `IntegrityError` rather than free XP |
| `xpgrant_challenge_needs_source_id` | a grant naming no slot, which is what makes the row above mean anything (Postgres treats NULLs as distinct) |

The property that actually prevents a double-pay is **not** statement order: it is that the stamp and the
grant happen in one atomic block under the *run's* row lock, with every precondition re-read on the locked
row. A second request blocks on the lock, reads the stamp, and refuses before reaching the ledger.

### What the grant must be wrapped in

`grant_job_xp_bulk` logs job-tier milestones and nothing else. `accept_contracts_bulk` brackets it with a
Pursuer level reading before and after; `rewards._grant` does the same, and that bracket is **not optional**.
`ranks_crossed(old, new)` only returns ranks in `(old, new]`, so a rank crossed by an unbracketed payout is
never logged *and never loggable* — the next claim starts from the already-raised level. A full run is fifty
job levels, so the visible damage was permanently blank dates on Career hero rungs the hunter really crossed.
`first_claim` is derived there too, so a hunter whose first job XP ever is a redeem keeps their onboarding
flag. No multiplier: a double-XP weekend does not scale challenge XP, because "exactly two job levels a
square" is the promise the figure makes.

### The completion ordinal is a property of the run

Which completion a run *is* — first or second — is counted as "completed runs finished no later than this
one", not as a live count of the set. The difference is recovery: with a live count, a title write that
failed on run 1 could never be backfilled, because once run 2 finished the count read 2 and re-granted the
*second* title, leaving the first unreachable by any code path. `grant_completion_title(challenge)` is now a
safe backfill from a shell for any completed run.

### Where the reward is shown

| Surface | What it says |
|---|---|
| The Start card (`/my-challenges/`) | the title the next completion earns, and the per-square XP where there is any |
| The run's page | a panel: the title, the XP waiting, and a **ledger** of finished squares |
| A finished square on the grid | an `XP` pip while its XP is unclaimed |

The panel renders for **everybody**, visitor included — "what is this worth" is the Hall of Fame's own
question — and the Claim buttons are gated on ownership inside the partial. The claim is deliberately *not*
on the square: a square is ~109px at 375px, so a button there is either under the touch floor or the only
thing in the tile.

The panel's rows are every finished square, paid ones included, with three states (paid / claimable / its
`Job` was deleted). Owed-only rows meant a claim deleted its row, so the "Claimed" state could never render.
The claimable count is the write's own predicate, so a Claim button cannot promise a payout the service skips.

### The notification

`challenge_completed` was already a `NotificationTemplate` choice; its template **row** ships in
`notifications/fixtures/initial_templates.json` and must be loaded (see the deploy checklist). It is sent
from `transaction.on_commit(..., robust=True)`: the callback runs after commit on the caller's thread, which
for the nightly sweep is inside no guard at all, so an escaping exception would abort the whole command. The
inbox is parked, so this row is **write-only** for now — do not add a route, a bell or a poller to make it
visible.

---

## Constraints

Written in the database because a shell and a data migration write around the service. (The admin is the
one place that cannot: it is fully read-only — no add, no delete, every field locked — by design.)

| Constraint | What it stops |
|---|---|
| `challenge_one_active_per_type` | two active runs of one type (partial: completed and hidden runs release the slot) |
| `challengeslot_unique_key` | two squares for one letter/job |
| `challengeslot_unique_position` | non-deterministic ordering |
| `challengeslot_unique_contract` | one contract filling six job squares — six payouts for one completion |
| `challengeslot_completed_is_filled_dated_and_explained` | a completed square with no game, no date, or no `completed_via` |
| `challengeslot_completed_via_valid` | an unknown `completed_via`, which would render as none of the three labels |
| `challengeslot_xp_needs_completion` | an XP stamp on an unfinished square |
| `xpgrant_challenge_once_per_slot` | a second payout for the same `(profile, job, slot)` |
| `xpgrant_challenge_needs_source_id` | a challenge grant naming no slot — which is what makes the row above mean anything, since Postgres treats NULLs as distinct and a `source_id=None` grant collides with nothing |

Those are the ones that stop a user-visible failure. `Challenge` also constrains its own shape (type
validity, a non-blank name, positive `total_slots`, `completed <= filled <= total`, and each timestamp
agreeing with its flag); `challenges/models.py` Meta is the full set.

---

## Gotchas and Pitfalls

**`challengeslot_unique_contract` keys on the FK, not the snapshot.** It read `contract_slug` first, which
disagreed with the service's own duplicate check — and the disagreement 500s: staff rename a contract, the
slug frees, another contract takes it, and a placement the service allows collides on two identical frozen
slugs. Migration `0002_slot_uniqueness_keys_on_the_contract_fk`.

**A deleted Contract escapes that constraint.** `on_delete=SET_NULL` nulls the FK on every square that held
it, and a partial unique does not constrain NULLs. The game can come back as a *new* row — staff recreate
it, or `evaluate_contract_candidates` restages it once the delete frees its igdb id — and would then take a
second square silently. `assign` has a dead-snapshot clause for this; **no index can express it**, because
the comparison is between one row's snapshot and another row's contract's current slug.

**A jobs slot stamped `import` is a historical row.** The importer applied to both types until 2026-09-28.
Nothing rejects such a row and no reader treats it specially; it is an honest record of the rule as it
stood. Do not "fix" it — rewriting to `hatch` asserts a hatch that may not have been open.

**`Job.discipline` is `choices=` with no DB constraint**, and `slot_keys_for` builds a run from every `Job`
with no discipline filter. So a discipline seeded without a matching `DISCIPLINE_LABELS` entry would draw
fewer squares than the run counts and leave one unfillable forever — the shelf loop used to iterate the
canonical five and silently drop anything else. `slot_render.slot_groups` now emits every bucket, unmapped
ones included, named after themselves. (Hypothetical rather than history: `Job.DISCIPLINES` and
`DISCIPLINE_LABELS` currently hold the same five.)

**Deleting a `Job` under a live run makes that run unwinnable.** `Challenge.total_slots` is frozen at
creation, so the square survives — but every door resolves a job square through the `Contract.jobs` M2M,
and deleting the `Job` cascades those rows away. There is no single choke point to check: `_shape` for the
slot panel, `fits_slot` and `assign`'s shape check; `fitting_keys_for` for the contract-first search panel;
`completed_contracts_across_keys` for the history panel. All three spellings filter `jobs__slug`, so they go
dead together and no result can ever fill the square. `slot_render.label_for_key` is what keeps the orphan drawable. Staff deleting a Job
with live runs against it is the hazard.

**A digit-initial contract can never fill an A-Z square**, and neither can `Ōkami`. The letter filter is
`name__istartswith` — case-insensitive, folded by **Postgres**, with no article stripping and no Unicode
folding — which matches the browse page's existing letter filter. (The fold must stay in the database:
`eligibility`'s module docstring records the divergence that followed from doing it in Python, and
`fits_slot` delegates to `_shape` for exactly that reason.) Accepted knowingly; a denormalised `sort_letter` is the fix if the non-ASCII count ever grows.

**`Concept.absorb()` needs no branch for challenges.** `ChallengeSlot` points at `Contract`, not `Concept`,
and Contracts are keyed on the raw IGDB id. If Contract merging is ever added, `challengeslot_unique_contract`
is the first thing it will hit — see below.

---

## What the retired system taught us

Everything else about the old system is in git history before `0281_drop_challenge_system`. One lesson is
load-bearing enough to keep:

**`GenreBonusSlot` was keyed on `(challenge, concept)` — a mergeable catalogue row — and it died in
production.** An admin `ConceptJoinReview` approval raised a duplicate-key error when two games in one
challenge's bonus pool later resolved to the same Concept. `Concept.absorb()` had to gain an explicit
collapse branch, and the doomed slot had to be **deleted** rather than skipped, because `SET_NULL` left a
skipped duplicate looking assigned in the database while rendering empty.

The instruction it left for the rewrite was: *either keep the mergeable row out of the slot's uniqueness
key, or give the merge path an explicit collapse branch from day one.* Today's
`challengeslot_unique_contract` keys on a `Contract` FK, which re-adopts that shape one table over.
Contracts have no `absorb()` and no merge path, so there is no live failure — but if one is ever added,
this constraint is where it will break first.

---

## The archive the placeholder promised

`community/challenges/` has been promising since 2026-08 that *"Your past A–Z runs are safe. We kept them,
and they come with you."* The data behind that is `ArchivedAZChallenge` in `trophies/models.py`, written by
the teardown migration: every retired A-Z run's per-letter progress, keyed on `psn_username` +
`np_communication_id`. It is the only copy.

**Nothing imports it yet.** The rebuilt app reads none of it, and it cannot be restored by a straight
re-point: a square's atom is now a `Contract`, so each archived row has to travel
`np_communication_id → Game → Concept → igdb_match.igdb_id → Contract`, and how much of the archive
survives that hop is a question only prod can answer. Keeping the promise is a requirement of the hub chunk,
not an optional extra — measure the coverage before deciding what the hub says about it.

---

## Where things live

| | |
|---|---|
| `challenges/models.py` | `Challenge`, `ChallengeSlot`, and every constant and constraint of this app (the two XP-grant guards live on `ContractXPGrant` in `trophies/models.py`) |
| `challenges/services/challenge_service.py` | **every write**, and every gate |
| `challenges/services/eligibility.py` | the pools, the hatch count, the importer's date |
| `challenges/services/picker.py` | the three panels — read-only, decides nothing |
| `challenges/services/slot_render.py` | the board: squares, discipline shelves, covers |
| `challenges/services/rewards.py` | **every reward write**: the XP redemption, the titles, the completion hook |
| `challenges/views.py` | two page views, three JSON read endpoints (the picker panels, all `GET`), six thin POST actions (start, assign, clear, hide, redeem, redeem-all) |
| `challenges/management/commands/process_challenges.py` | the nightly sweep |
| `templates/challenges/` | `my_challenges.html`, `challenge_detail.html`, `partials/_square_body.html` |
| `static/js/challenge-detail.js` | the picker's three modes, the reward panel's claims, and the board's entrance |
| `templates/challenges/partials/_rewards_panel.html` | the reward panel and its ledger of finished squares |
| `static/css/components/challenges.css` | `.pp-csq*` (the board), `.pp-cpick*` (the sheet), `.pp-cpay*` (the reward panel), BEM throughout |

**Related docs:** [job-board-contracts.md](../design/rebuild/job-board-contracts.md) for the Contract and
Job model the slot atom comes from, [xp-economy.md](../design/rebuild/xp-economy.md) for the ledger the
rewards chunk will write into, and [ia-and-subnav.md](../architecture/ia-and-subnav.md) for where these
pages sit.
