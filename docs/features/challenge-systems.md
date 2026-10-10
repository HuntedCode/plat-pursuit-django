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
| Services: `challenge_service`, `eligibility`, `picker`, `slot_render`, `calendar_fill`, `calendar_render`, `rewards`, `share_card`, `tutorials` | **built** |
| Detection: the sync hook, `process_challenges` (squares, then the Calendar phase), nightly | **built** |
| My Challenges, the run page, the picker (square-first, contract-first, history) | **built** |
| Public hub (`community/challenges/`) and Hall of Fame (`community/challenges/hall-of-fame/`) | **built** |
| Plat Calendar: one shovelware-free lens, filled from history at creation, one run per hunter, the day sheet, the year heatmap, the title ladder, the opening ceremony | **built** |
| Plat Calendar: richer crest artwork | **not built.** The crest renders with its month's face and hue |
| Rewards: A-Z and Job Coverage titles and job XP, the Calendar's day-marker ladder, the completion notification | **built.** `challenges/services/rewards.py` is the only writer |
| Share cards, all three types | **built.** Minting one as a Hall of Fame cover was **CUT**; see [The Hall of Fame draws heroes](#the-hall-of-fame-draws-heroes-not-cards) |
| Tutorials (system intro + one per type) | **built.** See [The tutorials](#the-tutorials) |
| Beta gate (`CHALLENGES_BETA_MEMBERS_ONLY`) | **built, and on by default** |
| Beta release audit (chunk 12) | **done.** See [Beta hardening](#beta-hardening) |
| Badge + holo award | **deferred to a follow-up branch**, post-beta. Completions are recorded from day one so badges backfill |

What a challenge is worth is now stated on both surfaces a hunter sees — the Start card before they
commit, and the run's own page — from one reader, so the two cannot quote different figures. See
[Rewards](#rewards) below.

---

## The three types

Genre challenges do **not** return. The **Plat Calendar does** — revived 2026-10-02, reversing a decision
settled on 2026-09-26. That reversal is recorded rather than edited away, in the plan and in
`challenges/models.py`, because the original reasoning is still worth reading.

| | A-Z Challenge | Job Coverage Challenge | Plat Calendar |
|---|---|---|---|
| Squares | 26, one per letter `A`-`Z` | one per `Job` in the catalogue, read live at creation | 365, one per calendar day |
| A square's key | the letter | the job's slug | `(month, day)`, across every year |
| Its rows | `ChallengeSlot` | `ChallengeSlot` | **`CalendarDay`** |
| The atom | a Contract | a Contract | **a date** |
| What fills it | a Contract whose `name` starts with that letter | a Contract carrying that job | the hunter's own history — nothing is picked |
| Win condition | every letter | every job, Freelancer included | every day, filled by a shovelware-free platinum (below) |

**The Calendar is the one type whose atom is not a Contract**, which is why several rules stated elsewhere
in this doc as if they were universal describe the first two only. It also picks nothing: its squares are
filled from the hunter's trophy history rather than chosen, so it has no picker, no eligibility query, no
scarcity hatch and no importer.

### The Calendar's one lens

**A day is filled by a shovelware-free platinum earned on that calendar day, in any year.** That is the
whole rule, and it is the only board drawn. It was three lenses until 2026-10-04 (all platinums,
shovelware-free, and contracts), collapsed by the owner's call: *"perhaps we should condense down to just
one view: non-shovelware plats. It makes the system much simpler ... I know contracts fuel the other
challenges but those are more curated sets of games and this is more wholistic."* A Contract is a curated
subset of the catalogue; a Calendar is a hunter's whole platinum history laid against the year, and a
switcher between them was conflating two kinds of object. `challenges/models.py` records what the collapse
took with it (the contracts lens and its expensive fill, `Challenge.completed_view`,
`calendar_contracts_seen`, and a whole class of cross-lens defect).

| Population | Stored as | Drawn? |
|---|---|---|
| shovelware-free platinums | `CalendarDay.in_clean` | **yes**: the fill, the progress number, the ladder, the completion |
| every platinum, shovelware included | `CalendarDay.in_all` | no: a comparison figure only ("297 shovelware-free of 340 platinum days") |

`in_clean` implies `in_all` by construction (a shovelware-free platinum is still a platinum), and
`calendarday_clean_implies_all` enforces it. Both come out of one fill statement, so keeping `in_all`
costs nothing and makes the headline interpretable rather than a bare number.

**One lens means one finish.** A run completes when all 365 days are filled shovelware-free, the progress
number is that count, and the 365 rung of the title ladder IS the finish. A complete run cannot change
afterwards: 365 clean days are 365 all days too.

The shovelware rule is the **broad** `SHOVELWARE_FLAGGED_STATUSES`, matching the Shovelware Free board,
NOT the trophy tracker's stricter `status == 'clean'` (which also excludes `manually_cleared`).

### Calendar rules worth knowing before you touch it

- **29 February folds into 28 February.** A run is keyed on `(month, day)` across every year, so there is
  no leap-day square — but the platinum is real, and the retired system dropped it.
- **The hunter's own timezone decides which day a platinum landed on.** Not the server's and not the
  viewer's: the run page is public, so an instant would render as a different day to a reader far enough
  east. `CalendarDay.earned_on` stores the resolved local **date** for that reason.
- **`hide_hiddens` is ignored.** The Hall of Fame is a board, and `Profile.total_trophies_raw` exists
  because ranking on a filter-respecting figure makes a board unreproducible by anyone but its owner.
- **A new run is filled at creation.** `start_reporting` runs `apply_to_run` straight after creating the
  365 rows, in its own savepoint (a failed fill is logged and left to the next sync or sweep; it never
  blocks the start). Without it the first visit, which is the opening ceremony, read "0 of 365 days".
  `seed_challenge_demo` passes `backfill=False` because it draws a designed board instead.
- **Fills are monotone.** A day that is true is never set false. The predicate can stop matching for
  reasons that are not the hunter's doing — a game reclassified as shovelware, a removed trophy row — and
  none may retract an earned square.
- **The sweep reconciles before it refreshes.** `calendar_fill.runs_due_for_sweep()` compares ONE stored
  counter, the hunter's platinum count at the last pass, against `Profile.total_plats` in one site-wide
  query. A run whose number has not moved is skipped without reading a trophy, because recomputing a whole
  history is the expensive thing and most syncs cannot fill a day. (There was a second counter for earned
  contracts while the contracts lens existed; it went with the lens.) **What it cannot see:** a shovelware
  reclassification moves no counter, so after a bulk one run `process_challenges --all-calendars`.
- **Refresh one hunter by hand** with `process_challenges --user <psn_username> --only calendar`. It
  deliberately ignores the reconciliation check — you reach for it when you suspect the watermarks are
  wrong.

**The board has a thirteenth tab: the All crest, an overview of the whole year.** Twelve rows of up to
31 cells, one row per month, columns aligned on day-of-month. Three things about it are decisions rather
than details:

- **The Hall of Fame hero now shares this shape** (2026-10-08): a row per month, a column per date, each row
  in its month's hue off the same `[data-month]` table. It used to draw 365 cells as seven rows flowing by
  column; see "The Calendar's Hall of Fame board is a heatmap" below for why that was replaced. Here the
  overview is a nested loop over the month groups the page already has, so **no new render function and no
  new query**.
- **Each cell carries its date from `md:` up, and the fill recipe changes with it.** Which breakpoint is
  arithmetic: 12px is the stylesheet's type floor, a two-digit tabular numeral advances ~14.4px at that
  size, and 31 columns inside the 343px a 375px viewport gives this panel is an 8.9px cell. Numerals at
  mobile would need sub-floor type or a horizontally scrolling year, and a year you scroll is not a year
  at a glance. **The coupled part is the one to remember:** measured across the twelve hues, `--pp-text`
  on the mobile cell's strong 68% tint holds 2.27:1 and a dark ink manages 3.54 at best — there is *no*
  text colour that works on it. So from `md:` the cell switches to `.pp-cal__day--on`'s own shipped
  recipe (a 22% tint with a 45% ring, already measured at 7.30-9.95 for white text). Raising that tint
  back toward the mobile figure fails AA at every hue; `test_the_overviews_dates_wait_for_room_and_bring_a_readable_fill_with_them`
  pins the pair together for that reason.
- **A row jumps to its month's tab, and a cell is inside a row** — so "click a box" and "click a row"
  are one handler, and there are twelve targets rather than 365. It jumps to the TAB, never to the day
  modal: a 9px square opening the same dialog a 44px day square opens one tab away would be a worse
  affordance, not an extra one. **The row is deliberately not a `<button>`:** the matrix is `aria-hidden`,
  so a focusable control inside it would be reachable by Tab and announced as nothing, and dropping
  `aria-hidden` instead would mean twelve row buttons announcing exactly what the twelve crests already
  announce. The overview is a pointer surface throughout — hover preview and click-to-jump both — and the
  keyboard and screen-reader path is the crest tablist, which does the same job with arrow keys at 44px.
- **The crest carries no `data-month`.** The twelve hues each mean one specific month, so a thirteenth
  reading that table would either steal a meaning or add a second colour axis. It declares its own
  near-neutral silver instead. Not the brand cyan: that is already every coin's completion arc, so a cyan
  rim over a cyan arc loses the distinction the two rings exist to make. Its rim is a **dial of the
  year** — one slot lit per struck month, on the same `--seg` mechanism — and its gauge reads days/365.

`totals_for` grew `struck` and `open` for this, both sums over the month groups, so the overview's figures
cannot disagree with the board and cost no query.

**The Calendar pays a ladder of five titles, climbed rather than won.** `Calendar Marker` at 50 filled
days, `Keeper` at 100, `Chronicler` at 200, `Champion` at 300 and `Legend` at 365 — granted on every
recount rather than only at the end, because 50 days is about 54 platinums where finishing is about
2,153. A run keyed on 365 days has no first/second-completion shape, so the Calendar is in
`TYPES_WITHOUT_ORDINAL_TITLES` and the 365 rung IS its ultimate: the lens collapse left one completion
condition, so the top rung and the finish are the same event.

**Three things about the ladder are load-bearing.** The rungs live in `challenges.models` and are
re-exported by `calendar_render` and keyed by `rewards.CALENDAR_DAY_TITLES`, because two copies of a
reward threshold is a drift nobody notices until a hunter is owed a title the page does not show. They are
granted **ascending**, because `granted_titles_for` picks a run's title with an ascending `(earned_at, pk)`
ordering and last-wins — so the highest rung must be the last row written, which is what puts
`Calendar Legend` rather than `Calendar Marker` on a finished run's plaque (a backfill grants several at
once, microseconds apart, so `pk` is what actually breaks the tie). And the grant is **idempotent**, since
it runs on every sync and every nightly sweep for every Calendar run.

**The run's header draws the ladder as the five titles** (`_calendar_rail.html`, `.pp-cal-ladder`; owner,
2026-10-08: the linear rail before it "doesn't really do a great job of explaining what you get"). Each rung
names its day count, its title and its state: Earned, Title pending, Already yours (to the owner; a
visitor reads "Already held"), "N to go" (the next one only) or "Finishes the run". Five equal cells, each filling across its own span (0-50, 50-100, ... 300-365), in
columns from `md:` and as a list on a phone. Two rules make it agree with the Start card:

- **It reads the GRANTED titles, not the day count** (`rewards.held_calendar_titles`, one query, the
  OWNER's). A reached rung whose title row is not there yet (the grant raised and will retry on the next
  recount) says "Title pending" and never claims it. A name collision does not show this: the hunter
  holds a title of that name, so the rung reads Earned.
- **"Next" skips titles already held**, because titles belong to the hunter, not the run: on a second run
  below 50 days the first rung says "Already yours" and the next is Calendar Keeper. Same rule as
  `next_calendar_rung`, which now reads the same helper. When every title is held the opening ceremony says
  so rather than claiming every day is filled.

**The opening ceremony fires once per RUN, not once per user.** A Calendar run backfills a hunter's
entire platinum history the moment it is created, so a veteran opens one already standing on two or three
rungs — the most dramatic thing the feature does, and it would otherwise happen silently between a form
POST and a redirect. It is gated on `Challenge.opening_seen_at` rather than a `CustomUser.ui_flags` key
like every other one-shot on the site, because a hunter can finish or hide a run and start another, and
the second backfills again. A `ui_flags` key would show it to somebody's first Calendar run and never
again.

**It is gated SERVER-side and wears the house mold.** `data-auto` is rendered only for the owner of an
unacknowledged run, because `PlatPursuit.DetailModal` reads that attribute — a script deciding would
risk a flash of a stranger's progress, and a run page is public. The controller supplies auto-open-once,
focus restore, Escape and a recorded dismissal; the career explainer and the new-contracts sheet are its
other two consumers, so nothing new was invented. `autoOpenDelay` is passed **only** when the attribute is
present: the controller reads `armed` from it for the recording half, so passing the delay unconditionally
would replay the ceremony on every visit.

**Preview it with `?preview=calendar-opening`** on any Calendar run's page (staff or moderator only,
via `core.previews`). It renders `data-preview` rather than `data-auto` and the script gives that branch
an auto-open and nothing else, so **looking never stamps the run** — `DetailModal` reads `data-auto` to
decide whether a dismissal is recorded. A genuinely unseen ceremony still outranks the preview for its
owner, since that is the real thing happening to them.

**Only the finish notifies.** `challenge_completed` fires from `on_run_completed` as it always did; the
four rungs below it are silent, because a backfilling run passes several at once on day one and would
otherwise fire three notifications in the same second.

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
| Types | **A-Z only** | **A-Z and Job Coverage.** Both are meaningless for the Plat Calendar, which picks nothing: its days are filled from the hunter's own history, so there is no pool to run thin and nothing to import into |
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

### Paging and the sheet's filters (2026-10-10)

Each panel used to be one `PAGE` and the sheet ended there, so a square with 340 games showed 24 (owner: "the
modal ONLY shows 24 total games"). Now every list reaches the whole pool:

| Panel | Pages by | Returns |
|---|---|---|
| Square, search | `?offset=` | `offset`, `more` (and the full `total`, so the subtitle is the real count) |
| History | `?cursor=`, a position in the candidate pool | `cursor` (where the next page starts, or null), `more` |

The client (`loadMore` in `challenge-detail.js`) fetches the next page as a sentinel under the rows nears the
bottom of the sheet, with a "Show more" button as the keyboard and fallback path. The catch-up block rides only
the first page of a square, and it now sits ABOVE the rows: below an endless list it was unreachable.

Three filters, all reset each time the sheet opens:

| Filter | Default | Effect |
|---|---|---|
| **Only this square** | on | With a term, searches the square's own pool (`slot_panel` with `q`). Off: the catalogue-wide contract-first search. Hidden where there is no square (history mode, the page's history door). |
| **In a badge** | off | `eligibility.in_live_badge`: a member game's concept is in a `Stage` of a series with a live group badge, Browse Games' rule. `?badge=1`. |
| **Platforms** | none | `eligibility.on_platforms` over `Game.objects.for_platform`: a member game on ANY chosen platform. `?platform=` (repeatable; unknown values dropped). |

**The filters are display filters, never rules.** `hatch_is_open`, `catchup_offers` and `assign` all read the
unfiltered pool, so turning a filter on can never open the hatch or change which rule a square is under.

Every row carries `platforms`: the union over the contract's member games in display order
(`ordered_platform_union`), each with the `platform_color_str` tone, read in one `Game` query per page
(`picker._decorations`, which shares one membership read with the covers).

The Challenges browse page's toolbar also links **My Challenges** (`show_my_challenges`, browse page only).

### The history window

The pool is "completed, live, fits an open letter". Whether a candidate is *importable* depends on its
date, which is not a SQL predicate — so the panel dates a `HISTORY_SCAN` (96) window and pages the result
to `PAGE` (24).

Since paging, the window starts at the request's `cursor`, and the next page starts just past the last
candidate the page used: mid-window when the page filled first, after the whole window when it did not. A page
can come back empty (a window of games all finished before joining) with `more` still true; the sheet keeps
looking by itself for up to three such pages, then leaves it to the button.

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

### The celebration is the Contract claim's, not a copy of it

A redeem plays the same full-screen claim ceremony a Contract claim plays: the award arrives, the XP flows
into the job it levelled, the bar fills and the level ticks up. `static/js/claim-ceremony.js` is the player
and **`contract_service.ceremony_payload` is the one builder** — extracted from `claim` when this path
needed it, so "the same animation" is structural rather than a resemblance somebody has to maintain. The
challenge path varies exactly one thing: the eyebrow line, because the player's default counts Contract slugs
and a square's payout has none (it would have announced "Contract claimed" over a challenge reward).

**It fires in the same response as the payout, and it has to.** The first design deferred it to the hunter's
next Career visit, with the nav marker standing until they had seen it. That cannot work: every number in the
payload is a difference between a reading taken before the grant and one taken after, and a level is a
*threshold* — so any other payout landing in the gap (a Contract claim in another tab, a sync) would be
attributed to the square, and a tier bloom a Contract had earned would play over a square's reward. That is
the mechanism the design was abandoned on, before it shipped, rather than an incident anyone observed. The
payload is therefore built inside the write, by the one function that holds both readings, and handed back in
the redeem's reply under `claim`.

Consequences worth knowing:

- The page needs `{% job_icon_sprite %}` (the player draws job icons by `<use href="#jobicon-...">`) and
  `claim-ceremony.js`. Both are emitted only for a Job Coverage run, and the script only for its **owner**:
  a visitor to the Hall of Fame has no Claim button and should not download the animation.
- `claim-ceremony.css` is imported by `input.css`, so it is already in the compiled bundle. No page tag.
- The reply's `granted` figure still travels, because the toast is the **fallback** for when the player
  cannot run (a missing module, a payload with no job to animate). A toast *and* a full-screen ceremony
  announcing the same number would be two answers to one question.
- Unlike the Career page, this path does **not** reload afterwards: the same reply already carries the
  re-rendered panel and squares, which are applied before the overlay opens — so the page behind it is
  already correct when the ceremony closes.
- **Not reloading has one cost, and it is the nav.** The chrome is server-rendered, so the XP pill on My
  Pursuit was rendered before the claim and clearing its cached answer does nothing to the lozenge already on
  screen. The reply therefore also carries `xp_pending` (profile-wide — another run may still owe), and the
  client removes the pill *and* its clause from the nav item's `aria-label` when it is false. The Career page
  gets this free by reloading; this one has to do by hand the one thing the reload was doing for it.
- **The panel's reward total ticks on dismissal, not on the swap.** Started with the swap it played out behind
  the scrim fade and the ceremony, so the number the panel is built around never animated for anybody.

**A full Claim all is a long ceremony, and that is the decision** (owner, 2026-09-30). Twenty-five squares means five pages of job tiles: roughly 12 to 28 seconds, with the Continue control inert until every page has auto-played, so the only early exits are Escape or tapping the scrim. Career's contract claims are usually one to five jobs, so this is a pre-existing property of the shared player that a challenge makes routine rather than rare. It was weighed and kept: finishing all twenty-five jobs is a once-per-run event and the long payoff is the reward, and a hunter who wants a shorter moment can claim squares one at a time from the panel. Do not "fix" the pacing without asking — and note that any change lands in `claim-ceremony.js`, so it would change Career too.

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
| The **My Pursuit** nav item, site-wide | an `XP` pill while any square anywhere is finished and unclaimed |
| The **My Challenges** sub-nav item | the same `XP` mark, so the strip says which of the two paying pages it meant |
| The Job Coverage card on `/my-challenges/` | a link: `XP  +N waiting`, with the figure |

**The card's indicator is scoped per TYPE, across every run — not to the run the card is showing**, and that is
the whole design rather than a detail. `card['run']` is the ACTIVE run, or a resumable one, or nothing; a
FINISHED run with unclaimed squares is none of those. Scoped to the card's run, a hunter who finished a run
without claiming saw a card reading "Start" while 150,000 XP sat on a run the page never mentioned — which is
exactly how the site-wide pill came to look stuck. It is a LINK for the same reason: when the owed run is not
the one the card offers, the card's own button does not lead to it, and when that run is HIDDEN this is the
only route to it at all. `rewards.owed_runs(profile)` answers it in one aggregated query for the whole page.

The nav pill is the third marker on that item (count, then XP, then New) and is documented with its siblings
in [job-board-contracts.md](../design/rebuild/job-board-contracts.md#the-nav-markers-trophiesservicescareer_attentionpy):
one `EXISTS` over a partial index, cached per hunter, armed when a square completes and spent by the redeem,
both on commit. Its predicate is `redeemable_slots`', so it cannot light for a square the payout would skip — other than for
up to the marker's own TTL, since the answer is cached for 300s and a `Job` deletion goes through neither
writer. `seed_challenge_demo --list` prints the live answer AND the cached one, and says so when they differ.

The panel renders for **everybody**, visitor included — "what is this worth" is the Hall of Fame's own
question — and the Claim buttons are gated on ownership inside the partial. The claim is deliberately *not*
on the square: a square is ~109px at 375px, so a button there is either under the touch floor or the only
thing in the tile.

The panel's rows are every finished square, paid ones included, with three states (paid / claimable / its
`Job` was deleted). Owed-only rows meant a claim deleted its row, so the "Claimed" state could never render.
The claimable count is the write's own predicate, so a Claim button cannot promise a payout the service skips.

**The ledger is in two lists, split by what a hunter can act on** (owner, 2026-09-30). Rows with a Claim
button stay in the open list; paid rows collapse behind a `Claimed (N)` disclosure. The reason is height: a
finished run has 25 finished squares, which was ~1,250px of ledger on desktop and ~2,300px at 375px where the
rows wrap — all of it above the board the page is about, and on a finished run (the page most likely to be
read by somebody else) every row of it inert. A fully-paid run is now one line. `<details>` rather than a JS
toggle, because the panel is re-rendered from the server on every claim and a JS-managed open state would be
destroyed by each swap.

**The headline measures what has been CLAIMED, for the whole run**, and the foot carries what a full run pays.
Swapping the headline's meaning part-way through a run was the alternative and was rejected: a hunter reading
one number that means two things depending on when they look is worse than a zero on a fresh run.

**A claim is acknowledged on the receipts drawer**, after the ceremony closes
(`.pp-cpay__done--just-paid`, reusing the Contract card's own `rpAcceptFlash` / `rpDoneIn` keyframes). This had
to be added by hand: the panel is replaced by server markup rather than flipping a class, so unlike the
Contract board there was nothing to animate — a hunter returning from the overlay found rows that were simply
already green.

**On the drawer rather than the row, and that is a correction.** A per-row version shipped first and could
never be seen: every row a claim pays has just *become* paid, so the re-render puts it inside the `<details>`,
which the server always emits closed — content in a closed `<details>` is `display: none`, so the animation
never ran, and the class then sat on the row and fired whenever the hunter expanded the receipts later.
Opening the drawer to fix it would undo the collapse decision on a 25-square Claim-all. The `Claimed (N)` line
is visible and its count is exactly what changed.

**The drawer does not remember being open.** The server emits it closed on every render, so a hunter who
expanded the receipts finds them shut after their next claim. Accepted cost, not a feature — and the reason
the acknowledgement could not live inside the list.

**Known seam, deliberately unresolved:** the challenge XP thread is `--pp-accent` (square pip, nav pill, panel
tally) and hands off to a `--pp-primary` ceremony at the most emphatic beat. Recolouring the ceremony is not an
option — the Career page shares it — and `visual-identity.md` does not name either colour "the earn colour",
so there is nothing to appeal to. What accent buys is separation: the XP pill sits beside a claimable-contract
count in primary, and hue tells the two kinds of waiting apart as well as content does.

### The notification

`challenge_completed` was already a `NotificationTemplate` choice; its template **row** ships in
`notifications/fixtures/initial_templates.json` and is NOT loaded at the beta deploy (owner, 2026-10-09): it waits
for the notifications rebuild (deploy checklist row M). Until then each finish logs one warning and skips the
write, and the run completes normally. The write stays so the rebuild inherits a working producer. It is sent
from `transaction.on_commit(..., robust=True)`: the callback runs after commit on the caller's thread, which
for the nightly sweep is inside no guard at all, so an escaping exception would abort the whole command. The
inbox is parked, so this row is **write-only** for now — do not add a route, a bell or a poller to make it
visible.

### A type reads as itself on every surface

Each type has **one glyph** (`partials/icons/challenge_type.html`), **one unit** (`CHALLENGE_TYPE_UNITS`:
letters, jobs, days, read through `Challenge.unit`), and **one pitch**, and every surface that names a type
uses them: My Challenges' cards and finished rows, the hub's run cards and type filter, the run page header,
and the Hall of Fame plaque's count. Added 2026-10-08 after the third type showed every place that assumed
two: the Calendar's Start card printed Job Coverage's description, the hub filter gave it the "All" grid,
and the Hall of Fame counted it in "squares".

**Short labels where the page already says "Challenges"** (`CHALLENGE_TYPE_SHORT_LABELS`, read through
`Challenge.short_label`): A-Z, Job Coverage, Plat Calendar on My Challenges' cards, the hub's filter and run
cards, and the Hall of Fame plaque. The full label stays where a type stands alone: the auto-generated run
names, the run page's title, and the share card.

**My Challenges lays the three cards out three across from `lg:`** (one column on phones, two at `md:` with
the third spanning). The spanning card goes **side by side at `md:` only** (`pp-ccard--wide`: who and how far
on the left, the reward and actions on the right), because one stack stretched across two columns left the
right half empty. **The Calendar's reward line names a rung**, not a completion title: its titles are a
ladder climbed during a run, so the card shows the next rung the hunter can still earn and where it sits
("Calendar Keeper at 100 days"), via `rewards.next_calendar_rung`. "Next" skips rungs the run has passed AND
rungs the hunter already holds, because the titles belong to the hunter: a second run cannot grant Calendar
Marker twice, so promising it would be false. A hunter holding the whole ladder gets no reward line.

---

## The two public pages

Two surfaces, one feature, split on a single question: **is the run finished?**

| | `community/challenges/` | `community/challenges/hall-of-fame/` |
|---|---|---|
| Holds | `visible()` + `is_complete=False` | `visible()` + `is_complete=True` |
| Entry | `_run_card.html` in a grid | `_run_hero.html` in a stack |
| Accent | primary | primary — the Hall of Fame shipped on `accent` and was corrected; `visual-identity.md` allows one brand accent across the kit |
| Sorts | newest / oldest / most progress / hunter A-Z | newest finish / oldest finish / hunter A-Z |
| Page size | 24 | **8** — see the cover budget below |
| Rate limit | `key='ip'`, 60/m, own bucket | `key='ip'`, 60/m, own bucket |

Both are `_ChallengeBrowseView` subclasses, which is the third use of the browse shape `gamelists.BrowseListsView`
established. A subclass answers four things: `base_queryset`, `SORTS`, its page furniture (`BROWSE_URL_NAME`,
`EMPTY_COPY`) and its entry (`ENTRY_TEMPLATE`, `GRID_CLASS`). Hidden runs appear on **neither** — hiding means
off the owner's profile and out of the hub, and that is the one thing the public read path honours everywhere.

`browse_results.html` is shared: the grid wrapper, the count attributes and the three empty states (searched
nothing / filtered nothing / genuinely nothing) are common, and only the entry differs. **"Most progress" is
absent from the Hall of Fame on purpose** — every run there is complete, so the option would be a no-op that
implies otherwise.

Two hooks separate work by where it is needed, because `get_context_data` runs for partial renders too (an
htmx filter swap and an `InfiniteScroller` page both render the grid partial):

- `full_page_context()` — gated behind `is_partial_render()`. Breadcrumb, headline count, SEO string.
- `enrich(runs)` — **not** gated. Anything an ENTRY draws, built from the paginated page.

### The profile tab

A hunter's profile carries a **Challenges** tab (owner, 2026-10-09) so their runs can be seen without the
browse page. It follows the Lists tab's rules: the chip appears only when the hunter has a VISIBLE run (hidden
runs never show, on your own profile included), and a hand-typed `?tab=challenges` with nothing behind it
lands on Games. Runs in progress come first, in the types' own order (A-Z, Job Coverage, Calendar), then finished ones, newest first (capped at
`ProfileDetailView.CHALLENGES_TAB_FINISHED_LIMIT`), drawn as the Challenges page's run cards with
`hide_hunter=True`. That card gained a finished state for it: the foot line reads "Finished <date>". Two
queries for the runs, whatever is on the tab. View: `trophies/views/profile_views.py`
(`_build_challenges_tab_context`); template: `tabs/challenges_tab.html`.

### The Calendar's Hall of Fame board is a heatmap

Every run in the Hall of Fame is finished, so a finished Calendar's 365 days are ALL filled -- and the first
hero drew exactly that: seven rows of identically filled squares, the same block for every hunter, tinted with
the hunter's RANK colour (`--rk`), so grey for every Newbie. It said nothing about anyone.

It is now the year overview as a **heatmap** (`calendar_render._hero_group`, owner's pick): twelve month rows
in their `--cal-c` hues, each day shaded by `plat_count` (shovelware-free platinums on that date, across every
year) in `HEAT_LEVELS` = 4 steps, with a small Fewer/More key under the board. **The shade is relative to the
run's own counts** on a square-root curve, so every board uses its whole range -- a fixed scale would put a
whale's whole year at the top step and a modest hunter's at the bottom. The top step is anchored to the run's
**95th-percentile day, not its busiest**, so one spike cannot flatten the rest of the year back into a block;
days above it cap at the top. A run whose anchor is one platinum is lit at the top step throughout, and a filled
day whose live count fell to zero (a reclassification: fills are monotone, counts are not) takes the bottom step.

**The plaque leads with what differs between finished runs**, not "365/365 days": total platinums, the busiest
day ("6 platinums on 4 Jan"), and the years the platinums were earned ("Earned 2016 to 2023") (`earned_on` is when a day was FIRST filled, so the latest of them
is when the calendar became complete). All of it comes from the 365 rows the board already reads -- one query
for the page, unchanged.

**Layout:** stacked on phones, full width from `md:` until 1280, then beside the plaque like A-Z. The break is
where the cell stops shrinking as the window widens: full width it is ~19px at 768, the generic 2.4fr share at
1024 would make it ~17px, and the 2.6fr share from 1280 gives ~23px.

### The Hall of Fame draws heroes, not cards

A finished run is 25 or 26 completed contracts, so this page holds single figures for a long time. A 4-across
grid both draws the hardest thing the feature asks for at the size of a browse tile and leaves a page of
eight entries reading as a failed load. So each finish is a full-width row whose face is the run's own board.

**The board is affordable because it is batched, not because it is small.** `slot_render.boards_for(challenges)`
resolves every square of every run on the page through the same `covers_by_contract` the detail grid uses —
a **fixed number of queries, regardless of entry count** — six where a Job Coverage run is on the page
(slots, contracts, two for membership, one for every cover, and the job catalogue), five without one, and
fewer again when there is nothing to resolve. `slot_render.boards_for`'s own docstring carries the full
range; quoting a single number here was wrong twice, first as "four or five" (the *per-run* figure for
`slot_cards`) and then as a flat "five" (the A-Z-only case). `slot_cards` is flat per RUN at four or five
depending on type, so looping it over a page would be four-to-five per entry. `test_challenges_live.py` pins the shape by
measuring one entry against four rather than asserting a number, so a later per-entry resolve fails instead of
merely getting slower.

**Query count is not the only bound, and the page size is the other one.** `cover_games_for` caps its fetch
at four rows per concept, and that cap is sized and argued for a 200-concept surface. A page of heroes hands
it the union of every square of every entry, so 24 entries would reach 624 concepts and authorise ~2,500
joined `Game` rows — each dragging a `Concept` and an `IGDBMatch` — on an anonymous, uncached URL, re-paid on
every InfiniteScroller page. **`HallOfFameView.paginate_by = 8`** keeps that union at 208 — a 4%
overshoot of the 200-concept reference surface, accepted deliberately (the cap's own comment calls
four-per-concept *generous rather than tight*) and stated rather than rounded away. 8 is the last page size
that stays within a rounding of it; 9 × 26 = 234 does not.

The query *shape* stays flat either way, which is precisely why the flatness pin cannot
see it: this is the bytes axis, the one the May 2026 OOM was actually about. The page size is emitted as
`data-page-size` and read by `challenges-browse.js`, because one JS literal cannot match two views and the
scroller uses it to decide which page to resume from after a history restore.

**Both pages are rate limited `key='ip'`, in separate buckets.** `?q=` is an `icontains` behind a join to
`Profile` with no index serving it, driven by a debounced live-search box, with no login in front — the
harder version of the case `SlotPickerView` is limited for. `key='user'` would bucket every anonymous caller
under one key. The buckets are named explicitly (`challenges:browse`, `challenges:hall-of-fame`) because
`django_ratelimit` derives its default group from the decorated function's qualname, and a `method_decorator`
on a subclass that does not define `get` wraps the *inherited* `BaseListView.get` — so both pages would
otherwise share one bucket.

`method=('GET', 'HEAD')`, not `method='GET'`: django_ratelimit does not count a method outside the list, and
`View.setup` aliases `self.head = self.get` when a class defines no `head` — so a HEAD request ran the
wrapped `get`, executed the full queryset, and was never metered. `curl -I` in a loop would have run the
unindexed `LIKE` for free. **Every other limiter in the project still has this hole**; these two are the
first that do not, and closing the rest belongs in the parked `refactor/` branch.

`test_my_challenges.py::test_both_write_doors_share_one_rate_limit_bucket` is the anti-drift guard, and it now
knows four kinds of door. Browse is the one kind whose two doors must **not** share a bucket, so it asserts
one call each plus `key='ip'` and the HEAD method on both. Its extraction balances parentheses rather than
matching `ratelimit\((.*?)\)\)`, which assumed every limiter is a method decorator ending in `))` — a
class-level one ends `name='get')`, so the old regex swallowed the entire class body.

**Every square carries its own context**, which replaced a bare mosaic. `boards_for` reads the job catalogue
once for the page (skipped entirely when no Job Coverage run is on it) and each square comes back as
`{key, label, job, cover}` — four keys, each with a reader in `_run_hero.html`. An A-Z square draws its
letter; a Job Coverage square draws its job's icon, tinted by discipline off the `--disc` its shelf sets.

That reverses the first cut, which returned bare covers on the argument that the board "is a mosaic, not a
labelled grid, with the named grid one click away". The owner overruled it on a browser pass (2026-09-30):
twenty-six covers with no key is pretty and says nothing about what the run was, and the keys are also what
make the types look like different achievements rather than one template with different art. (The Plat
Calendar's board is a different problem again: 365 day cells carry no cover art at all, because eight rows
of them is ~2,920 images, and at the 5-13px a hero cell actually lands on the art would be invisible
rather than merely unreadable. Clicking a day on the run page opens a sheet listing the games that
satisfy it; the hero's cells stay decorative.)

The exact-four-keys rule still holds, for the reason `slot_render._card` states outright — a dict that grows
a field per guess is how unread columns get fetched for two hundred rows — and
`test_the_board_carries_exactly_what_it_draws` pins the set.

Nine per row at every breakpoint, as a **wrapping flex container** rather than a grid. Nine by three holds
25 or 26, so no layout needs to know the run's length — but it is 27 slots for 25 or 26 items, so the last
row is always short, and `justify-content` centres each *line's* items in a wrapping flex container where a
grid aligns the whole track set and cannot centre a partial last row at all. The basis carries **1px of
slack**: `9 × basis + 8 × gap` came to exactly the content width on a quotient no browser can represent, and
a UA rounding the wrong way wraps the ninth cell into a fourth row that `overflow: hidden` then clips —
silently losing two covers. `fr` tracks were immune by construction; 1px buys that back.

### The plaque is the Pursuer Card, compact

The right-hand column of each hero is a **plaque**, and it is the [Pursuer Card](../design/visual-identity.md)
at its `Mini` size rather than a panel of its own design. `visual-identity.md` lists "earned by these
Pursuers" panels among the places that primitive appears, so this is a surface it was already specified for.

**What it replaced was the primitive's own first listed anti-pattern:** *"Generic 'user profile card' (avatar
circle + username + bio, like every social app)."* The plaque was an avatar, a name and a run-type subtitle.
The owner read it as "boring", which is that anti-pattern seen from outside.

| Zone | Surface | Holds |
|---|---|---|
| **Crest** | rank-tinted material over `--pp-bg-2` → `--pp-bg-1` | avatar, hunter name, the standing line (rank + Pursuer Level), the earned title band |
| **Plinth** | its own opaque `--pp-bg-1` plate | the shared disciplines ring, squares tally + Career XP, the job XP this run paid, the record line (type · date) |

**No corner diamonds.** The plaque carried four — the Frame's brand mark — and the owner cut them on a
browser pass (2026-10-01: *"they don't really look great"*). The structural reading agrees, and it is the
second orphan this component borrowed from: the Frame's production partial is rendered by **nothing**, not
even `templates/design/frame_preview.html` (a self-contained prototype that includes no partials), having
been superseded on the badge surfaces by the [Badge Medallion](../reference/badge-medallion.md), which draws
no notches. One failed reuse check produced
both that and the discipline band. What carries the plaque's identity now is all live — the rank-tinted
material, the gold title band, the plinth, and the shared ring —  and
`test_the_plaque_wears_no_orphaned_primitives_mark` pins the removal, because the citation that produced the
diamonds (the Frame as a "signature primitive") is still in the design constitution for the next reader.

**The two zones exist for a contrast reason, not a layout one.** `--pp-text-mute` is tuned to ~4.5:1 on
`--pp-bg-2` with no headroom, so the rank wash pushes it under AA wherever it sits in the crest. Every muted
line therefore lives in the plinth, which paints an opaque measured surface and so is immune to whatever the
rank does above it. `test_no_muted_text_sits_outside_the_plinth` derives the muted class list from the
stylesheet and asserts placement, rather than pinning the background literal the previous version of that
test pinned — which broke on a change that *improved* the thing it guarded.

**The rank hue is `--rank-<key>`, not the Pursuer Card's `--pc-tier`.** `pursuer-card.css` assigns
`--pc-tier` in two rules, so it carries two hues across four escalation rungs; `elements.css` defines a full
per-rank spectrum and `career.html` already reads the per-rank token for its rank ladder. Using the ladder's
own token keeps the plaque and the Career page agreeing about what a Paragon looks like. It arrives as an
inline `--rk`, following that template's own precedent, so eleven hues cost no extra rules.

**The cost of that choice, on the record.** It also makes the plaque *disagree* with the home Pursuer Card:
the same Marshal is violet-glassed at home and olive here, the same Paragon cyan there and coral here.
`visual-identity.md` names that as an anti-pattern — *"inconsistent treatments between hero/compact/mini that
break the family read"* — so one of the two surfaces should eventually move. Recorded rather than left to be
discovered; the plaque is the newer surface, but the per-rank spectrum is the one the Career page already
uses, so the home card is arguably the one that is out of step.

**Escalation is layered, and the floor is deliberately handsome.** Four bands (`matte` / `lift` / `tinted` /
`radiant`), close to `pursuer-card.css`'s rungs but not identical — that file has a step at vanquisher which
this map merges, because the hue here already changes at every rung. Every band keeps the material and the
plinth, so a hunter who finished a 25-game run is never shown a stripped plaque because of an unrelated rank;
the Frame's rule is that tiers "should feel like the same family, not different products".

Two honest limits, both of which an earlier version of this paragraph glossed by listing "the four diamonds,
the gold title band": the diamonds were **cut**, and the title band is conditional on a title actually being
held, which a finished run can legitimately lack. Below 768px the band treatments and the plate do not apply
at all, so a `matte` and a `radiant` plaque are identical there.

#### What the spine costs, and why it is its own service

`challenges/services/plaque.py` is page-batched like its two siblings: `plaques_for(challenges)` takes the
whole page and returns `{run id: spine}`. **Its docstring carries the query count; this doc deliberately does
not restate it**, because the equivalent figure in the section above was wrong twice and in `enrich`'s
docstring three times. What is guaranteed is the *shape* — flat per page, pinned by
`test_the_plaque_spine_costs_a_flat_number_of_queries`.

It does **not** mount `pursuer_card_service`. That builds the full card from a per-profile Career hero
(platinum showcase, DNA ring, rarest/recent slices); eight of those on an anonymous uncached URL is the shape
of the May 2026 OOM. The spine feeds the **shared disciplines ring** instead, from page-wide aggregates.

#### The band that became a ring, and why it is worth recording

The plaque's first cut carried a **bespoke five-tile discipline band**, modelled on `pursuer-card.css`'s and
justified by the Pursuer Card's anti-pattern about "inconsistent treatments between hero/compact/mini that
break the family read".

Two things were wrong with that, and the owner caught both:

1. **The Pursuer Card is mounted on nothing in production.** `d739bf2b` (2026-06-29) mounted it as the home
   hero; `4a730fd9` (2026-08-13, "make / the lobby") dropped it and nothing re-mounted it. Its only renderer
   today is `/design/pursuer-card-ranks/`, behind `StaffRequiredMixin`.
2. **That same commit put the real primitive in its place.**
   `partials/components/_disciplines_ring.html` (`.lab-dna`) is live on the Career hero *and* the home lobby,
   and its own docstring states the contract the band violated: *"the lobby's smaller ring is a scale, not a
   second implementation, which is what keeps the two surfaces from drifting."*

So the plaque hosts that partial -- at its NATURAL size, not `compact`; it passed `compact` at first and
dropped it when the owner asked for a bigger ring (2026-10-02), which also retired a host-scoped width
override. `job_render.discipline_ring` now owns the
cumulative arc geometry for all three hosts — moved out of `career_service._RING_C`, which was private and so
forced a third host to choose between importing a private name and copying the constant.

**Switching also deleted a cluster of defects outright**, which is the usual tell that the duplicate was the
problem: three AA failures (the tile number lost WCAG's large-text allowance at 15px), a sub-12px label that
broke the type-floor guard, a five-across grid that squeezed to ~37px at `lg`, no clip for a three-digit
average, and the five-full-bars lie below. The ring proportions arcs against the **sum**, so five families at
the level floor draw five equal arcs rather than five full bars.

**The reuse is pinned**, by `test_the_plaque_hosts_the_shared_ring_rather_than_its_own_band` — because nothing
pinned it before and that is precisely how the duplicate shipped: the page rendered, the suite passed, and a
reimplementation looked like a deliberate design.

**It reads the career standings itself rather than relying on the caller's `select_related`.** Rank, level and
Career XP live on `ProfileCareerStanding`, a reverse `OneToOne` off `Profile`, so a hint in the view would make
them free. The extra query is bought on purpose: a caller who forgets the hint gets a silent per-entry query
instead of a visible failure, which is the shape of every N+1 this project has had to go back and fix.

#### Gotchas and Pitfalls

- **"Relative to your strongest" is the trap the ring avoids.** Scaling each discipline against the
  hunter's strongest is undefined when every discipline sits at the level-1 floor, and resolves to
  `1.0 / 1.0 = 100%` — five *full* bars for a hunter who has never been paid a contract, claiming mastery of
  everything on the page built to display mastery. Reachable: an A-Z run finished entirely through the history
  importer pays no job XP. The ring proportions against the **sum** instead, so equal floors draw equal arcs,
  and `discipline_ring` handles the all-zero case as an even split rather than five zero-length arcs.
  **`pursuer_card_service` still carries the strongest-family version and so still carries the bug** — dormant,
  since that component renders nowhere but a staff preview. Whoever re-mounts it should fix it on the way in.
- **The level floor must match `pursuer_level_from`, and it has to apply to the *headline* too.** An
  untouched job is level 1, not 0, and `contract_service` records what happens when two surfaces disagree:
  the Career XP board and the hunter's own Career page showed different levels for the same hunter. The band
  floors per discipline for that reason — and the first cut floored the band while defaulting the Pursuer
  Level itself to `0`, so a hunter with no standing row read **Lv 0** here and **Lv 25** on their own Career
  page, with the plaque contradicting itself on one row (five families averaging 1.0 across a 25-job
  catalogue, beside a headline of zero). Two independent audits put that first. `floor_level` now goes
  through the shared helper.
- **The per-discipline job count is data, not five.** `DISCIPLINE_LABELS` fixes the five disciplines, but how
  many jobs sit in each is a staff-editable catalogue and it is the denominator of both the floor and the
  average. Hard-coding it would be right today and silently wrong after one catalogue edit.
- **A-Z runs pay no job XP, so the "job XP from this run" line is Job Coverage only.** `redeemable_slots` gates
  on the type, so the per-square bonus belongs to that side even when one platinum advanced both boards. An
  unclaimed jobs run is zero, and zero omits the line rather than printing `+0`, which would read as a failed
  payout.
- **A missing `ProfileCareerStanding` row is a real state, and the level it implies is the floor, not zero.**
  The row is written only by `contract_service.recompute_career_standing`, which runs on a contract claim
  **and** on a challenge redeem — so a hunter who has claimed neither has no row (an earlier version of this
  bullet said "once a profile has been paid a contract", which missed the second door). `plaques_for` seeds an
  entry for every run regardless, at the **catalogue floor** (25 today, since every untouched job sits at
  level 1 — the same figure their own Career page shows), because the template reads `plaque.level` and
  `plaque.rank.label` directly and Django would silently blank the plaque's whole lower half for exactly the
  hunters most likely to have finished via the importer. This bullet said "level 0 → the `newbie` rank" while
  the Gotcha above it described that as the worst defect of the first cut; the rank is still `newbie`, since
  25 is under recruit's floor of 35.
- **The plinth shapes the corner it paints.** It squares its own bottom corners with an explicit radius and
  pins itself to the bottom with `margin-top: auto`, rather than relying on the parent — which matters because
  the plaque only wins the height contest while the board is shorter, and at 2xl an A-Z frame overtakes it.
  This bullet used to read "the plaque must not clip, its four notches sit at `-4px`"; the notches were cut,
  so nothing hangs off the plaque's edges and nothing forces its `overflow` either way.
- **Nothing here may go inside a minted share image.** Rank, Pursuer Level and Career XP all change after a
  run is finished. See [Nothing mutable may go inside the minted image](#nothing-mutable-may-go-inside-the-minted-image)
  — this is the same rule that killed `PlatinumShareImage`, and the plaque is the live half of the row by
  design.

### Why the frame is *not* pinned to a ratio

This took three attempts and the arithmetic is the reason. **A 1.905:1 box cannot be tiled by 25–26 cells of
ratio 3:4.** Three rows of 3:4 cells fill the height at only 7.6 columns (21 cells, not enough); four rows
want ten columns (40 cells, fourteen of them empty for an A-Z run). So a frame pinned to
`DIMENSIONS['landscape']` forces a choice, and the first two attempts each made it badly:

| Attempt | Frame | Cells | What went wrong |
|---|---|---|---|
| 1 | `aspect-ratio: 1200/630` | `3/4` | The mosaic floated in 22–59px of bare background at every width — a grey letterbox with a picture in it. |
| 2 | `aspect-ratio: 1200/630` | stretched to fill | Cells came out **1:1.575**, *taller* than the art, so `object-fit: cover` matched the height and **sliced ~15% off each cover's left and right edges** — and only ~36% of a 16:9 PSN fallback survived. `object-position: top` was inert throughout, because the vertical fit was exact. |
| 3 | height from the board | `3/4` | The cells are the ratio the art is in, so there is nothing to crop. |

**`object-position: top` only works because the cell is 3:4**, and that is worth knowing before anyone
changes the cell ratio again: in a cell taller than the art, `cover` fits the height and crops *sideways*,
which makes top-anchoring meaningless and slices off the logo it exists to protect. `test_challenges_live.py`
now pins the cell ratio, the absence of a frame ratio, the 1px slack and the centring — none of which had a
test when attempts 1 and 2 shipped, which is exactly why both landed.

**Consequence for the minted cover (chunk 7):** it must be generated at the *board's* ratio (~2.25:1), not at
`DIMENSIONS['landscape']`. That costs nothing — the downloadable share card stays 1200×630 because that is
what social embeds want, and it is the same template rendered at a second `format_type`, which is what that
dict is for. One design, two output sizes.

**The prestige chip reads the granted `UserTitle` row**, not a recomputed ordinal (`rewards.granted_titles_for`,
one query for the page). A title write is *contained* when it fails, and `grant_completion_title` declines to
re-point a row another system already holds under the same name — so a finished run with no title held is a
real state. Reading the row means the chip is absent exactly when the title is, and appears the moment a
backfill grants it. `source_type` is part of the lookup: `source_id` is a bare `PositiveIntegerField`, so a
badge-granted row with a colliding id would otherwise be read as this run's prize.

### Nothing mutable may go inside the minted image

`notifications.PlatinumShareImage` stored generated share PNGs to S3 from January 2026 and was dropped by
`notifications/migrations/0016_drop_platinum_share_image.py` in May — **not** because storing images failed.
`user_total_platinums` was computed at notification-creation time and held in the notification's metadata,
which the PNG rendered from, so two plats from one sync processed out of order could swap their
"Platinum #N". A real user reported it; commit `8b981dd0`'s fix was to compute at click time, one source of
truth.

So the hunter's name, their avatar and any ranking are rendered by the **page**, around the image, from live
rows — which is how `_run_hero.html` is already built. Safe to bake in: the covers, the snapshotted game
names, the completion date, the type, the earned title. One caveat worth writing down rather than
discovering: covers derive from IGDB matches and staff can re-anchor one, so the image is a snapshot of the
minting moment rather than a permanent guarantee. That is arguably the point — it is a memento of the finish.

**The live board is the permanent un-minted state**, not a placeholder: a run finished since the last
generation pass, or one whose generation failed, wears it. And generation belongs in an idempotent management
command on a cron, never at completion time — `rewards.on_run_completed` is built so nothing there can stall
or raise, and Playwright is 1–3s.

### The share card is rendered at click time, so the rule holds by construction

Minting was cut, but runs still get a share card: `challenges/services/share_card.py` +
`templates/shareables/challenge_card.html`, owner-only, finished or in progress. It DOES carry the hunter's
name and avatar -- the things the rule above keeps out of a minted image -- and that is fine for the
same reason commit `8b981dd0` was: it is computed when the hunter asks for it, from live rows, and never
stored. Built one type at a time (A-Z, Job Coverage, then the Plat Calendar); see
[share-images.md](share-images.md#the-challenge-card-a-runs-share-card).

---

## The tutorials

Two kinds, on the `.pp-howto` mold and the Career explainer's `.cxp__*` beats, driven by one script
(`static/js/challenge-tutorials.js`) over `PlatPursuit.DetailModal`. Gates and markers live in
`challenges/services/tutorials.py`, which reads `ui_flags` off the loaded user at **zero queries**.

| | System intro (`#challenges-intro`) | Type tutorial (`#challenge-tutorial`) |
|---|---|---|
| Auto-opens | once, on **My Challenges** | once, for the run's **owner**, on their first visit to a run of that type |
| Also rendered (recall only) | the public hub | every run page, for every reader, anonymous included |
| Marker | `ui_flags['challenges_intro_seen']`, a **version**: `beta` then `live` | `ui_flags['challenge_tutorial_<type>'] = True`, sticky |
| Endpoint branch | its own (`challenges_intro_seen`), validated | the existing `ui_flag` allow-list, built from `tutorials.TYPE_FLAGS` |
| Preview door | `?preview=challenges-intro` (current), `?preview=challenges-intro-live` | `?preview=challenge-tutorial`, any run |

**Why the intro is a version and not a boolean.** Its copy changes when the beta ends (the "members
first" line goes), so "has this person dismissed it" is the wrong question. The marker holds the newest
version the hunter was SHOWN; `live` supersedes `beta`, so a beta hunter meets the live intro once.
`current_intro_version()` reads `challenge_service.beta_is_on()`, the same predicate the creation gate
reads, so the intro cannot announce a beta that has ended.

**One auto-opening modal per load.** A Calendar run's opening ceremony goes first; while it is unseen,
real or previewed, the type tutorial stays shut and arms itself on the next visit.

**The copy quotes the constants.** `HATCH_THRESHOLD`, `CHALLENGE_SLOT_JOB_XP` and `CALENDAR_DAY_MARKERS`
reach the tutorial as context, and the type pitches come from `CHALLENGE_TYPE_PITCHES` (shared with the My
Challenges cards), so a changed rule cannot leave the tutorial teaching the old one.

---

## Beta hardening

The chunk 12 audit (2026-10-09) ran five reviews before the members-first beta. The rules it added, so they
are not undone by accident:

| Rule | Why |
|---|---|
| **One Plat Calendar run per hunter, for good** (`TYPES_WITH_ONE_RUN`). A visible finished run refuses another Start, and My Challenges shows it with View and Share; a HIDDEN finished one (hidden runs keep filling, so one can finish while hidden) is brought back by Start, and its card offers Resume. | Every Calendar run reads the same history, so a second run is a copy. With fill-on-create it was worse: a hunter with all 365 days minted a finished run, a Hall of Fame entry and a notification on every Start press. |
| **A new Calendar run fills from history at creation** (`start_reporting` -> `_backfill_calendar`, in a savepoint). | The first visit is the opening ceremony; left to the next sync it read "0 of 365". `seed_challenge_demo` opts out with `backfill=False`. |
| **Title grants run in their own savepoints** (`_recount_calendar`, `on_run_completed`). | A caught DATABASE error inside the caller's transaction aborts it, so a bare try/except rolled the whole fill or the finished run back at commit. |
| **Start locks the profile `FOR NO KEY UPDATE`.** | A plain `FOR UPDATE` blocks the `FOR KEY SHARE` every foreign-key insert takes, so a whale's fill held up their own sync's trophy inserts. (The sync's own `UPDATE` of the profile's counters still waits for the fill, which is brief.) |
| **The sync path and the nightly sweep contain each square** (`detect_for_profile`, `process_challenges`). | One bad square ended the slot phase and skipped the Calendar phase for the night. |
| **Rate-limited JSON doors answer 429 with an `error`** (`_json_when_limited`, `block=False`); Start says it on the page. | `Ratelimited` renders the HTML 403 page, so the picker could only say "That did not save." |
| **"Most progress" on the hub ranks by share done**, count as the tie-break. | A Calendar run counts to 365 and fills at creation, so the raw count put every one above every A-Z and Job Coverage run. |
| **The A-Z archive promise is kept by the first-run history importer** (owner, 2026-10-09). | The old placeholder said "your past A-Z runs are safe... they come with you". `ArchivedAZChallenge` stays, unread. |

The audit's page fixes (the history import that asks first and stays open, the day sheet's loading and error
states, the year heatmap, the ceremony's climb, the empty launch states, the copy for three types) are
pinned in `tests/engine/test_challenge_beta_hardening.py`.

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

**`MAX_OFFSET` ends a picker list, it never clamps one.** Clamping `offset=5024` back to 5000 re-served that
page with `more: true`, and a scrolling sheet appended it forever. Every picker slice stops at the ceiling and
`more` is false past it.

**The year overview carries the count two ways, and both are deliberate.** Below `md:` the cell has no
numeral, so the shade carries the count (like the Hall of Fame year). From `md:` a numeral sits on a 22% tint
whose contrast was measured for that text, so the tint stays and the count becomes a bar in the cell's own
`--yc-heat` shadow slot. Shading the background there would break the measured contrast. The key under the
matrix is built from the same cell classes, so it always matches the encoding in use.

**`TYPES_WITH_ONE_RUN` is about what a run READS, not about the Calendar.** A type belongs in it when every
run of it is computed from the same hunter-wide data. A-Z and Job Coverage are picked square by square, so a
second run is genuinely new and they stay out.

**A tutorial preview must never carry `data-auto`.** `DetailModal` reads that attribute to decide
whether a dismissal RECORDS, so a preview rendering it would spend the real tutorial on close. Previews
render `data-preview`, which the script gives an auto-open and nothing else: no `onDismiss`, no
`seenKey` (a key left by a failed write makes `DetailModal` skip the open and retry the write). A REAL
unseen intro also outranks the live preview, because an armed intro rendered as `live` during the beta
would post a version the endpoint refuses.

**The intro endpoint refuses a version above the current one and never rewinds.** One POST of `live`
during the beta would otherwise suppress the live intro before it exists, with nothing in the UI to
undo it; a stale beta tab dismissed after the live one was read keeps `live`.

**A new `.pp-howto` modal needs its ID in BOTH closing lists in `series-list.css`.** The settle list
names DIALOGS (`#id.is-closing .pp-detail-modal__dialog`). `#cal-opening` was listed there bare, so its
root ran the settle instead of the fade and the ceremony slid and vanished;
`test_the_dialog_settle_list_targets_dialogs_only` pins every entry now.

**A horizontal scroll strip inside the page's flex column widens the whole page on mobile.** The crest
row is thirteen 44px coins — 620px of content in a 308px box with `overflow-x: auto`. That clips
correctly, but the container still leaks its INTRINSIC width upward through `#zoom-wrapper`, and a mobile
browser answers by **expanding the layout viewport**: measured on an emulated iPhone 12, `innerWidth` was
619 against a 390 layout viewport, so the whole document panned sideways into empty space.
`game-detail.css` records the identical defect for its pill row and chose to wrap instead.

**Three things make this expensive to find, so they are worth writing down.** (1) A desktop-sized headless
viewport **cannot reproduce it** — the meta viewport is ignored, `window.scrollX` stays 0, and the page
looks clean from 320 to 1024. Only true mobile emulation (`is_mobile=True`) shows it. (2)
`documentElement.scrollWidth` reports the inflated figure in both cases and is a **red herring**: it does
not move even with `overflow-x: clip` on `html`. Measure `window.innerWidth` against
`document.documentElement.clientWidth` instead. (3) Almost nothing fixes it. Measured as ineffective:
`min-width: 0` on the strip, on `.pp-cal`, on `main` or on `#zoom-wrapper`; `max-width: 100%` or an
explicit `width: 100%`; `overflow-x: clip` on any ancestor up to `html`; and `contain: inline-size`
anywhere. What works is `contain: layout size` on the strip itself — **both keywords**, since
`contain: size` alone does not — plus a `contain-intrinsic-height`, because size containment sizes the
box as though empty and it would otherwise collapse. Scope it to the band where the scroll container
exists; from `md:` the strip is `overflow-x: visible` and containment would break a row whose height comes
from coins that size themselves.

**And bring a crest into view by writing the strip's `scrollLeft`, never with `scrollIntoView`.** The
strip opens centred on the live month: the board has always opened on the current month, but nothing
moved the row, so a hunter in October met a row showing January with the live crest five coins past the
edge. `scrollIntoView` walks EVERY scrollable ancestor, so it can pan the document on both axes — not
hypothetical on this board. On load the vertical half is the worse one: `block: 'nearest'` stops the
browser centring a control already on screen, but a board below the fold is not "nearest", so the page
would jump down to it on load. A `scrollLeft` write touches one box and is instant without asking,
since `scroll-behavior: smooth` sits on `html` and is not inherited.

**The Calendar's hover preview is pointer-only, deliberately.** The side column swaps the month's figures
for a hovered day's. It mirrored `focusin` at first, on the reasoning that a keyboard reader has no hover,
and that was a net loss: the preview carries `aria-hidden="true"`, so focusing a square announced nothing
while removing the month's figures from the accessibility tree, for all 28-31 consecutive day stops. The
keyboard path is the day modal, which Enter opens and which is a real dialog with real content. Re-adding
the listener is the easy mistake, so `test_the_side_column_can_preview_a_day_without_fetching` asserts the
peek registers no listener other than `mouseover` and `mouseout`. Asserted on the listener SET rather than
on the word `focusin`, because `addEventListener('focus', fn, true)` is the first thing anyone re-adding
the feature would reach for and the deleted comment named it.

**And its two faces are swapped with a CLASS, never the `hidden` attribute.** They share one grid area so
the column cannot resize under a moving cursor, which requires the hidden face to stay IN FLOW. `hidden` is
`display: none`: no box, not a grid item, no contribution to the container's height. So exactly one face was
ever in layout and the panel shrank about 96px on every hover, below `lg:` taking the page's scroll height
with it.

**Overriding `[hidden]` from the stylesheet is not possible, and that is the part worth remembering.**
Tailwind's preflight ships `[hidden]:where(:not([hidden=until-found])) { display: none !important }`. An
important author declaration beats every normal author declaration regardless of specificity or layer, so
`display: block` loses — and so does `display: block !important` from outside a layer. The first attempt at
this fix shipped with four places (the stylesheet, the template, this doc and a test docstring) asserting a
fix that a browser measurement showed had changed nothing: 170px to 74px, before and after.

**The crest rim's dash list is written out, not repeated.** The outer gauge leaves a 28-unit notch at the
bottom for its counter plate, and the rim tiles the remaining 72 — but a repeating `stroke-dasharray`
cannot be told to stop at unit 72, and `100/6` is not a whole number of periods. A repeating pattern there
painted five stray dashes across the notch plus a double-length run at the seam. It was defended by a
comment claiming the leftovers sat "behind an opaque plate": `.pp-cal__sub` is `display: none` until
1024px, so on every phone and tablet nothing covered them.

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

**Decided (owner, 2026-10-09): the first-run history importer keeps the promise.** A hunter's first A-Z run
can fill letters straight away from games they finished after joining, which is the past they bring with
them. `ArchivedAZChallenge` is NOT imported and stays in place, unread: restoring it would have to travel
`np_communication_id → Game → Concept → igdb_match.igdb_id → Contract` per row, with an unmeasured match
rate, and unmatched letters would simply be lost. If restoring it is ever wanted, measure that coverage on
prod first.

---

## Where things live

| | |
|---|---|
| `challenges/models.py` | `Challenge`, `ChallengeSlot`, and every constant and constraint of this app (the two XP-grant guards live on `ContractXPGrant` in `trophies/models.py`) |
| `challenges/services/challenge_service.py` | **every write**, and every gate |
| `challenges/services/eligibility.py` | the pools, the hatch count, the importer's date |
| `challenges/services/picker.py` | the three panels — read-only, decides nothing |
| `challenges/services/slot_render.py` | the A-Z and Job Coverage board: squares, discipline shelves, covers |
| `challenges/services/calendar_fill.py` | the Plat Calendar's fill predicate (shovelware-free platinums, with `in_all` kept as the comparison count), the backfill writer, the reconciling sweep scope, and the sync-path refresh |
| `challenges/services/calendar_render.py` | the Plat Calendar board: twelve month groups plus the year totals for the detail page, one 365-day board for the hero. Separate from `slot_render` because a day shares no fields with a contract-backed square |
| `templates/challenges/partials/_calendar_board.html` | the Calendar's detail board: the crest switcher, twelve month panels and the year overview |
| `static/js/challenges-calendar.js` | the Calendar's month tablist (through `PlatPursuit.wireTablist`), the day sheet, the hover peek (fine pointers only) and the opening ceremony's climb |
| `challenges/services/plaque.py` | the Hall of Fame plaque's Pursuer Card spine: rank, Pursuer Level, Career XP, the shared disciplines ring's arcs, and the XP a run paid — page-batched, reads nothing per entry |
| `challenges/services/rewards.py` | **every reward write**: the XP redemption, the titles, the completion hook |
| `challenges/views.py` | four page views (My Challenges, the run, and the two public browse pages); `GET` reads: the three picker panels (JSON), the Calendar day square (an HTML fragment), and the share card's preview (JSON) and download (PNG); seven thin POST actions (start, assign, clear, hide, redeem, redeem-all, opening-seen) |
| `challenges/management/commands/process_challenges.py` | the nightly sweep |
| `challenges/management/commands/seed_challenge_demo.py` | **dev only**: runs in every reward state, so the panel and the pip can be looked at without finishing 25 contracts |
| `templates/challenges/` | `my_challenges.html`, `challenge_detail.html`, `browse.html`, `hall_of_fame.html`, `partials/_square_body.html`, `partials/_run_card.html`, `partials/_run_hero.html`, `partials/browse_results.html`, `partials/_share_dialog.html` + `partials/_share_button.html` (the share card's dialog and its trigger) |
| `static/js/challenges-browse.js` | the two public pages' reveal + infinite scroll (filters are `browse-filters.js`) |
| `static/js/challenge-detail.js` | the picker's three modes, the reward panel's claims, and the board's entrance |
| `challenges/services/tutorials.py` + `static/js/challenge-tutorials.js` | the tutorials' markers and gates, and their one controller. Markup in `partials/_challenges_intro.html` and `partials/_type_tutorial.html` |
| `challenges/services/share_card.py` + `static/js/challenge-share.js` | the run's share card and its dialog (two owner-only `GET` doors on `views.py`) |
| `templates/challenges/partials/_rewards_panel.html` | the reward panel and its ledger of finished squares |
| `static/css/components/challenges.css` | `.pp-csq*` (the slot board), `.pp-cal*` (the Calendar board), `.pp-cpick*` (the sheet), `.pp-cpay*` (the reward panel), `.pp-crun*` (the browse card), `.pp-chero*` (the Hall of Fame hero, incl. `.pp-chero__year` for the Calendar's year heatmap), BEM throughout |

**Related docs:** [job-board-contracts.md](../design/rebuild/job-board-contracts.md) for the Contract and
Job model the slot atom comes from, [xp-economy.md](../design/rebuild/xp-economy.md) for the ledger the
rewards chunk will write into, and [ia-and-subnav.md](../architecture/ia-and-subnav.md) for where these
pages sit.
