# Leaderboards — Section Rebuild

> Status: **SECTION CLOSED (2026-08-19).** Built in steps 1-8, partly reversed (the three directories),
> then converged onto one board in steps 9-12. Kept as the record of what was decided and why; the build
> order below is a changelog now.
>
> The closing state: four surfaces -- Global Boards, badge detail's Ranks tab, job detail's Ranks tab and
> game detail's Ranks panel -- share one row partial, one board shell, one jump bar, one window parser,
> one JS engine and one hunter search, with country on every one of them. Uniformity is guarded per
> surface by `tests/engine/test_board_uniformity.py` rather than by anyone remembering. The last piece was
> a store for the per-edition badge board (`SeriesEditionStanding`, migration 0313), which is also the
> last thing in this section that needs a deploy step. Backend audit performed 2026-08; the three cost
> defects it found are already fixed (commit `2b0bf02e`) and are not part of this plan.
>
> ### The three directories were removed (2026-08)
>
> `/leaderboards/{games,badges,jobs}/` are **gone**, without redirects -- they never left a dev machine.
> The rest of this document stands: boards live on the thing they rank, and the per-entity Ranks panels on
> game, badge and job detail are the surfaces that survived and are the ones worth reading.
>
> **What the thin-directory rule below did not anticipate.** It was written to stop each directory
> becoming "a second Browse Games", and it worked -- but held to it strictly, what remained was a page
> whose only differentiator was a sort each browse counterpart already had: `played_count` on Browse
> Games, "Most earned" on Browse Badges, a hunter count on every card at `/jobs/`. The min-entrants gate,
> presented below as what pays for that sort, only ever HID entities. And nothing linked to any of the
> three except the Leaderboards rail, which existed because they did -- a circular justification that
> collapsed the moment either half was examined.
>
> The rule was right and the section below argues it well. The conclusion it should have reached is that
> a catalogue thin enough to obey it had no reason to be a separate page.
>
> Went with them: `BadgeSeries.entrants` / `Job.entrants` (+ migration `0308`, deleted before it reached
> prod), `recalc_board_entrants` and its `nightly` step, `BOARD_MIN_ENTRANTS`, and the preview machinery
> (`_top_n_by_partition` and the three `*_previews`). The Leaderboards hub is back to `items=()`.

## Why this exists

`/leaderboards/` became its own hub when the Community hub was retired, and it is currently a hub of one:
two April-era DaisyUI pages with zero rebuild primitives, reading a Redis layer that a rebuilt backend
(`services/badge_leaderboards.py`, "Lane B") was built to replace and does not yet feed.

The section also under-serves what the product now has. Game leaderboards exist and are rebuilt to
standard, but live buried in a tab on game detail — invisible from the section named after them. The
gamification economy (jobs, contracts, XP) has no boards at all, despite being a system whose entire
purpose is watching a number go up.

The governing constraint, stated by the product owner and adopted here as a rule:

> **Rather no leaderboards than laggy or heavy ones.**

That is achievable, and the audit says why: every board in this plan is `ORDER BY indexed_column LIMIT n`
over a denormalized standing. The expensive patterns — live aggregation per read, and materializing a
separate store per slice — are exactly what the current Redis design does and what cutover removes.

---

## 1. Information architecture

### The shape

Boards live with the thing they rank. The Leaderboards hub is a **discovery layer over them**, not a
second home for them.

| Surface | URL | Role |
|---|---|---|
| **Global Boards** (hub landing) | `/leaderboards/` | The three global boards, `.pp-switch` tabs |
| **Game Boards** | `/leaderboards/games/` | Thin directory → links to game detail's Ranks panel |
| **Badge Boards** | `/leaderboards/badges/` | Thin directory → links to badge detail's new Ranks panel |
| **Job Boards** | `/leaderboards/jobs/` | Thin directory → links to job detail's Ranks tab |

Sub-nav carries all four; the first item is the landing itself (`core/hub_subnav.py`
`LEADERBOARDS_HUB.items`, currently empty by design — its comment already anticipates this).

### Where the full boards live

**On the entity, never in the hub.** Game detail's Ranks panel is the precedent to copy everywhere: the
only panel there that is *not* server-rendered, fetched on first activation because its cost scales with
popularity and most visitors never open it.

| Entity | Full board |
|---|---|
| Game | Ranks panel on game detail (**exists**) |
| Badge series | New Ranks panel on badge detail (**replaces** `/leaderboards/badges/<slug>/`). ONE board, not two: earners and chasers merged — see below |
| Job | Ranks tab on new job detail (**new surface**) |

This is what keeps the directories honest: they are discovery, they own no data, and there is exactly one
canonical location per board. No page can drift from another because no second page exists.

### The per-series board is ONE board, not two

Earners and chasers were separate boards at different grains — earners per EDITION
(`UserGroupBadge`), progress per SERIES (`SeriesBadgeStanding`). They merge at **series grain**, which is
what progress already uses, so "earned" means "earned any edition" — consistent with `progress_bp` already
being the max across editions.

Ordering is `(-progress_bp, advanced_at)`: earners (10000 bp) on top by completion date, then each rung of
chasers with whoever got there first ahead. One table, one index, one query — no UNION and no
cross-table pagination.

**Why a tiebreak was needed at all.** `progress_bp` is discrete: `round(10000 * cleared / gating)` where a
stage clears the moment ONE qualifying game hits `base_complete`. A 3-stage series therefore stacks
everyone on 1/3 or 2/3, and without a second key those large ties sort by profile id — arbitrary, and it
reads as unranked. `advanced_at` breaks them the same way the earners board always did: first there wins.

Two things fall out of the engine's own semantics and are easy to get wrong:

- **Earned and chasing use DIFFERENT dates.** Earned takes the group's `earned_date`; chasing takes the
  latest cleared gating stage. Using "latest stage" for both breaks the `min_count` (megamix) policy,
  where `earned_date` is the date the need-th stage fell — a hunter clearing optional extra stages
  afterwards would have their completion pushed later and lose rank for doing more. Under the 'all' policy
  the two coincide, so this only surfaces on megamix series.
- **There is no "tied at zero" cohort.** A standing row only exists once `xp > 0`, i.e. at least one stage
  cleared, so nobody sits on the board at 0%.

The per-EDITION earners rank stays as-is: it is the number on the medallion back ("3rd to earn Ultra HD"),
not a board page. `earners_rank` keeps doing that job.

### The Jobs catalogue belongs to Browse, not Leaderboards

A public catalogue of jobs is a *browse* surface and sits with Games, Badges, Franchises and Companies.

| Surface | URL | Hub |
|---|---|---|
| Jobs browse | `/jobs/` | Browse |
| Job detail | `/jobs/<slug>/` | Browse |

Job detail carries two `.pp-switch` tabs:

- **Contracts** — every contract that grants XP toward this job. The aggregation that exists nowhere else
  (a contract is keyed to one Concept, so its natural home is the game; grouping *by job* is the new view).
- **Ranks** — the job's leaderboard.

**No public Contracts browse.** Contracts already have two homes: the game they belong to (via the
existing public `contract_modal_preview`, *"No auth by design: this is the pitch shown BEFORE a user has
an account"*) and Career's board for linked users. A third catalogue would be a third view of one dataset.

`/jobs/` vs Career's Dossier is the **Collection vs Browse Badges** split, already settled in this
codebase, whose recorded insight was *"SCOPE, not pagination"*: Career shows your standing across the 24
jobs, `/jobs/` shows what the jobs are. They coexist without competing.

### The thin-directory rule (load-bearing)

The three directories are catalogues of the same entities that `/games/`, `/badges/` and `/jobs/` already
catalogue. Without a stated differentiator they converge into second copies of those browse pages, and
then there are two walls, two filter sets, two test suites and a drift risk — the exact failure the badge
how-it-works modal was retired to avoid.

**Rule:** a leaderboard directory gets *search + exactly two sorts*, and nothing else. No filter drawer,
no facet panel, no genre/platform filters, **no country facet** (see below).

**The two sorts, settled:**

| Sort | Why |
|---|---|
| **Alphabetical** (default) | Stable and predictable. A catalogue whose order shifts between visits is disorienting, and anyone after a specific entity uses search |
| **Most entrants** | The only sort that answers the question the section exists for — *which boards do people actually compete on* |

Most entrants earns its place by being free: the **minimum-participants gate** needs those same counts
anyway, to keep the wall from filling with one-entrant boards that read as broken.

| Directory | Count source | Cost |
|---|---|---|
| Game Boards | `Game.played_count` (already denormalized) | Free |
| Job Boards | 24 rows via `profilejobxp_job_xp_idx` | Free |
| Badge Boards | count per `series_slug` off `sbs_series_xp_idx` | Cheap; needed for the gate regardless |

**Rejected:** *biggest mover* and *recently changed hands*. Both need a previous-position history that
nothing currently stores, and neither justifies a snapshot table. Revisit only if the section proves out.

Without "most entrants" the directories are alphabetical-only, and the differentiator from `/games/`
collapses to "the card has a top slice on it" — which would not justify three separate catalogues. That
one sort is what keeps the thin-directory rule from arguing itself out of existence.

If a directory grows a filter panel, it has become a second Browse Games and belongs folded back into the
real one as a view mode instead.

The natural pull during implementation will be to reuse Browse Games wholesale, because it is right there
and it works. Reuse its *card wall, `HtmxListMixin`, `InfiniteScroller` and mini-bar*. Do not reuse its
filter apparatus.

### Card contents

Entity identity + a **top slice** + a link to the full board.

- **Top 5 at `md:` and up, top 3 on a phone.** Identity plus five rows of avatar/name/value is a tall
  card, and the `--lib` wall goes single-column at 375px. Omitting the expendable rows beats scrolling
  past them (the same call made across the phone-fit pass).
- **No personal rank on the card.** A user is not on the vast majority of these boards, so the slot would
  be blank on most cards — worse than absent. More importantly, a card identical for every viewer is
  **cacheable**; a personal rank makes every response per-user and forfeits that. See §5.

---

## 2. The rename: badge XP → **Badge Points**  ✅ SHIPPED

Two sealed economies currently share one word. Badge XP lives in `ProfileBadgeStanding` /
`SeriesBadgeStanding`; job XP lives in `ProfileJobXP` and rolls up to Pursuer Level. The badge rebuild doc
states the separation as a rule: *"Badge XP + leaderboards live inside the box. They never read/write the
jobs/contracts economy."* A user can hold very different ranks in each, so one label for both is a
correctness problem in the reader's head, not just a naming preference.

XP also belongs to the gamification system on the merits: levels, curves and grants live there. The badge
system has none of that machinery — it has a score.

### Scope: user-facing labels ONLY

Internal names stay. `total_badge_xp`, `ProfileBadgeStanding.total_xp`, `SeriesBadgeStanding.xp`, the
`lb:xp:*` Redis keys, `xp_service`, `BADGE_TIER_XP` — all unchanged.

This follows the precedent set by the Hunters rename, which kept every `profile*` URL name because
*"churning them risks a `{% url %}` typo becoming a 500 to change a string nobody outside the codebase
sees."* A rename sweep across live Redis keys is an outage risk for zero reader-visible gain.

`Points` is currently unused in user-facing copy — verified, no collision.

### Surfaces carrying the string

| Surface | Note |
|---|---|
| `/badges/how-it-works/` | Reads **"XP on the table"**. The teaching surface for this vocabulary — the most important one to get right |
| Collection header | Stat label |
| Badge detail | Per-series figure |
| Global Boards landing | Tab label (see below) |
| Dashboard modules ×2 | Badge XP + Country XP providers |
| `badge_earnable_xp` | Site-heartbeat stat -- internal key, user-facing label only |

### Consequence for the landing tabs

Three explicit tabs, no ambiguity:

| Tab | Source |
|---|---|
| **Badge Trophies** | Trophies across badge games, ranked PLATINUMS first with total as the tiebreak (see §3) |
| **Badge Points** | `ProfileBadgeStanding.total_xp` |
| **Career XP** | Gamification total (see §3) |

> **Renamed 2026-08.** The first tab shipped as **Progress**, which named the STORE rather than what the
> board ranks -- every other board on the site is named for its figure. Key `progress` -> `trophies`,
> aliased in `LEGACY_TABS` alongside `xp` and `country`. The service functions moved with it
> (`progress_rows`/`progress_rank` -> `badge_trophy_rows`/`badge_trophy_rank`); the `pbs_progress_idx`
> index name did not, because renaming an index costs a migration on a large table to change a string only
> a DBA reads.

Do **not** merge into a single "total XP". The architecture forbids the economies mixing, and a merged
number would be the one figure on the site that means nothing.

---

## 3. Data layer

Everything here exists to make each board a single indexed range scan. Two new materialized columns, one
denormalized column, and a set of composite indexes.

### Badge Trophies (then called "Global Progress") — keep it, and materialize it

**The dedupe question is already answered, and the intuition about it is inverted.** The current rebuild
counts `EarnedTrophy` rows filtered by `trophy__game__in=games`, where `games` is a **subquery** of
badge-stage games. An `IN (subquery)` does not multiply rows, so each trophy is counted exactly once no
matter how many badges contain its game. Duplication would require *joining* through to stages — which is
both more expensive and produces a number that inflates with catalogue growth rather than with player
achievement. **Deduped is the cheap option and we already have it.**

What is missing is a Lane B equivalent. Today the board costs a full-population aggregate over
`EarnedTrophy` every 6 hours (four filtered `COUNT`s plus a `MAX` per linked profile).

**Materialize per-tier counts on `ProfileBadgeStanding`**, recomputed in the write seam that already
recomputes badge XP. This fits the subsystem's own stated principle — *materialize factual read-models,
keep relative data live*: a trophy count is factual and recompute-from-scratch, while rank and rarity stay
live because they are relative.

**Where it recomputes (checked 2026-08).** The new seam is not wired to sync yet — its only caller today is
the `evaluate_badges` command, whose own docstring calls `badge_apply` *"the same code the eventual sync"*
will use. Sync wiring is cutover step 5.

The position it will occupy is proven, though: legacy badge evaluation already runs at sync-complete
inside `bulk_gamification_update()`, and the Redis progress rebuild is explicitly *"Called at sync-complete
time after `bulk_gamification_update()` exits."* `recompute_standing` inherits that hook.

> **Pin this in the cutover spec.** If the new engine is wired to evaluate only the *affected* badges as an
> optimization, a profile's counts across all badge games will not be recomputed on a sync that touched
> none of them. The column must recompute whenever **badge-game trophies arrive**, not only when a badge's
> state changes. One line to get right at cutover; expensive to discover afterwards, because the failure is
> a slowly-drifting number rather than an error.

### Career XP — materialize the total

Pursuer Level is *computed* as the sum of a profile's `ProfileJobXP.level`, not stored. A global board
would aggregate ~24 rows per user across the whole population per read.

Materialize a per-profile career-XP total, rolled up in **`grant_job_xp`** -- the single primitive every
job-XP payout flows through (contracts, quests, events, manual awards).

> **Hook the PRIMITIVE, not the rebuild.** This was first wired to `recompute_profile_job_xp`, the ledger
> REBUILD, which only management commands call. A live contract accept bumped `ProfileJobXP` and left the
> standing frozen, so the board silently stopped at the last backfill and every accept after it was
> invisible -- with nothing erroring. The test that "covered" it asserted the call existed inside the
> rebuild function, which was true and said nothing about the seam that actually fires. Per-discipline totals have the same shape and the same fix, if discipline boards are
ever wanted — they are **not** in this plan.

### Per-job boards — already backed

`ProfileJobXP` already carries `models.Index(fields=['job', 'total_xp'], name='profilejobxp_job_xp_idx')`,
and its docstring reads *"The read side for the Lab + leaderboards."* Per-job boards need **no new index**.

### Country: one denorm, every board

The current design materializes a **separate Redis sorted set per country** plus an index set. Slicing
other boards the same way multiplies — series × country would be a store per combination. That is why
"slice everything by country" is unaffordable today and cheap after cutover.

**Denormalize `country_code` onto the standing rows** and add composite indexes. Every standings-backed
board then gets a country slice as an index range scan, at the same speed as its global form.

| Store | Index |
|---|---|
| `ProfileBadgeStanding` | `(-trophies_platinum, -trophies_total)` + `(country_code, -total_xp)` + `(country_code, -trophies_platinum, -trophies_total)` |
| `ProfileEditionStanding` | `(edition, -total_xp)` + `(edition, -plat, -total)` + both again with `country_code` between |
| `SeriesBadgeStanding` | `(series_slug, country_code, -xp)` |
| `ProfileJobXP` | `(job, country_code, -total_xp)` |
| `ProfileGame` | own composite; mirrors `pg_game_leaderboard_idx` with `country_code` |

Country is a **filter on a board, never a board of its own**. No per-country pages, no per-country URLs
beyond a query parameter. This is the single decision that keeps the section's surface area finite.

### Platform edition — the second filter (added 2026-08)

Legacy HD and Ultra HD are different games; the XP model says so outright, accruing XP per GROUP BADGE
rather than per series. So "who leads Legacy HD" is a real question the all-editions board cannot answer,
and it gets the same treatment country did rather than a board of its own.

**`ProfileEditionStanding`** — one row per (profile, platform group), carrying `total_xp` and the same
`trophies_*` tally, with columns **named identically to `ProfileBadgeStanding`'s**. That is what makes the
filter a STORE SWAP (`badge_store(edition)` returns a manager) rather than a branch through every query
body. Indexed `(edition, ...board order)` and `(edition, country, ...board order)`, so the two filters
compose and each is still a range scan.

Applied to the two BADGE boards only. Career XP has no editions; a control that renders and changes
nothing promises a slice that does not exist.

Three decisions worth keeping:

- **Per-edition XP is stored per SERIES** (`SeriesBadgeStanding.group_xp`, `{key: xp}`) and re-summed
  across all the profile's series rows. `recompute_standing` may be scoped to a subset of series, so a
  profile-wide figure written from the call's own results would silently halve every time one series was
  re-run. Same rule the grand total already followed.
- **The split is one grouped query**, `GROUP BY (title_platform, trophy_type)`, with games routed to
  editions in Python by the same INTERSECTION rule `badge_engine._qualifies` uses. A filtered count per
  edition looks free at two editions and becomes the cost model at six — and it would put the price of
  seeding a new group on every profile's sync.
- **An unknown edition key reads NOTHING**, not everything. Falling back to the all-editions store would
  show the global board under an edition heading: a wrong answer wearing a right one's clothes.

**Editions overlap and do not sum to the all-editions row.** A cross-gen game qualifies for both groups, so
its trophies count toward both. Splitting them would make an edition's trophy count disagree with the
badges that edition awards.

**Full boards only — the directories carry no country facet.** It was considered (filter the directory,
every card's slice becomes that country's) and rejected on caching: a directory cached per (page, sort) is
a handful of entries, and per (page, sort, country) is that times ~200. The section is public-identical by
design (§5), and a facet few people would use would forfeit most of that. Competitive comparison is what
country is for, and that happens on the board itself.

`country_code` must be kept current when a profile changes country — a signal, or the same recompute.

### The windowed preview query

A directory shows a top slice per card. The naive shape is one query per card, which compounds under
infinite scroll.

**Paginate the entities, then window their tops in one query:**

```sql
ROW_NUMBER() OVER (PARTITION BY <entity_id> ORDER BY <board_sort>) <= 5
```

Two queries per directory page — the window plus one batched profile fetch — constant against page size
and against catalogue size. This must be built this way from the start; it is not a retrofit onto a loop.

---

## 4. Retirements, redirects and cutover

### Retire `/leaderboards/badges/<slug>/`

Its content becomes a Ranks panel on badge detail. **Two** redirect routes name `badge_leaderboards` and
will 500 (not 404) if the URL name disappears — they must be repointed in the same change:

| Line | Route |
|---|---|
| `community/leaderboards/badges/<slug>/` | → `badge_detail` ✅ |
| `leaderboard/badges/<slug>/` | → `badge_detail` ✅ |

The panel endpoint is **`/badge-ranks/<slug>/`, deliberately top-level.** `/badges/<x>/<y>/` is the
profile-scoped shape that the Cloudflare-bypass guard redirects and that `badge_detail_with_profile`
claims, so an endpoint there 302s before it ever reaches the view -- the same trap the quick-peek routes
already record in `urls.py`, walked into anyway and caught by a test.

(Four further routes name `overall_badge_leaderboards`; the landing keeps that name and they are unaffected.)

### The Series tab folds into Badge Boards

The landing's current Series directory is built on `Badge.objects.live().filter(tier=1)` — the tier concept
the badge rebuild replaced with platform groups. It has to be rewritten regardless; it becomes the Badge
Boards directory on `BadgeSeries` / `GroupBadge`.

### The Redis layer

Cutover deletes `redis_leaderboard_service` reads, the `update_leaderboards` cron (every 6 hours), and 13
documented Redis keys. **Sequencing:** the standings stores are written on this branch (Lane A shipped),
so swapping page reads to Lane B is coherent here; prod requires the badge cutover to have seeded holds
first (`badge-backend-rebuild.md` §6 step 4).

### Dashboard modules

The badge XP and country XP dashboard providers duplicate boards this section will own, on a dashboard
that is being sunset. Retire with the dashboard, not before — some providers are still load-bearing.

---

## 5. Caching

Every directory and every board in this plan is **identical for every viewer** — that is a design property
bought by the no-personal-rank decision, not an accident. It makes the whole section cacheable per
(page, filter, sort) and servable to anonymous and signed-in users alike, which is the single largest
performance lever available.

> **Blocker to confirm.** Known open item from the 2026-08-12 scraper incident: an analytics cookie set on
> every response disables Cloudflare caching site-wide. These pages are the best edge-cache candidates on
> the site; that issue neutralizes the win until resolved.

---

## 6. Settled in planning

The four questions this plan opened, and how they closed. Recorded because each was weighed rather than
defaulted, and the reasoning is the part that rots first.

| Question | Answer | Where |
|---|---|---|
| What sorts the directories? | **Alphabetical default + most entrants.** Alphabetical because a catalogue that reorders between visits is disorienting; most entrants because it is the only sort that answers "which boards are alive", and the participants gate pays for it already. Biggest-mover / recently-changed-hands rejected — both need history nothing stores | §1 |
| Does the badge write seam run on every sync? | **Not wired to sync at all yet** — `evaluate_badges` is its only caller; cutover step 5 wires it. The sync-complete hook it will inherit is proven. Residual pinned for the cutover spec | §3 |
| How many contracts can a job have? | **Unbounded is fine** — the Contracts tab is a browse wall with infinite scroll like any other, which bounds per-request work by construction. The real risk is not length but per-row user state; see the batching gotcha | §1, Gotchas |
| Does country propagate into the directories? | **No.** Full boards only. Rejected on cache cardinality: ~200× the entries for a facet few would use, against a section whose defining property is being public-identical | §3, §5 |

Still genuinely open, and outside this plan's control:

- **The analytics-cookie caching blocker** (§5). Not a leaderboards decision, but it determines whether
  the section's central performance property can actually be realized.

---

## 7. Build order

Each step leaves the site working.

| # | Step | Ships |
|---|---|---|
| 1 | ✅ Materialized columns + country denorm + indexes (0297/0298) | Nothing user-visible |
| 2 | ✅ Lane B read swap; PROGRESS boards deleted from Redis entirely. Earners/XP/country reads remain, blocked on the badge cutover repointing their legacy consumers. **Still to pin: the recompute trigger -- badge-game trophy arrival, not badge state change (§3)** | Same pages, new backend |
| 3 | ✅ Badge Points rename (labels) | Vocabulary fixed before new surfaces spread it |
| 4 | ✅ Global Boards landing rebuilt (3 tabs, country filter, `.lb-*` component) | The hub landing |
| 5 | ✅ Badge detail Ranks panel (`/badge-ranks/<slug>/`, lazy); `/leaderboards/badges/<slug>/` retired + 2 redirects repointed | Boards move to entities |
| 6 | ~~Badge Boards + Game Boards directories; hub sub-nav live~~ **REMOVED in step 9** | — |
| 7 | ✅ `/jobs/` + `/jobs/<slug>/` (Contracts + Ranks tabs, public) | New Browse surface |
| 8 | ~~Job Boards directory; sub-nav live with all four~~ **REMOVED in step 9** | — |
| 9 | ✅ Directories removed (2026-08); the three boards VIRTUALIZED onto one shared shell, row, jump bar, window parser and JS engine | Every board is the same board |
| 10 | ✅ Game detail folded in: shared row + chrome, `invert` and `registered_only` removed, country added to it and to job detail | FOUR boards, one board |
| 11 | ✅ Board SEARCH on all four (prefix-matched, board-scoped, ranked); board descriptions rewritten in the site's voice | The last game-detail-only feature spreads |
| 12 | ✅ Sticky minibar on `/leaderboards/` | The board-first page keeps its controls reachable |

Steps 1–2 are the performance work. **Finishing the cutover *is* the optimization** — it is not a
prerequisite to it.

---

## 8. One board, four surfaces (2026-08)

The three boards were each virtualized separately and had begun to diverge -- badge detail appended 25
rows at a time behind a "show more", job detail had a prev/next pager, and only the Global Boards landing
scrolled. All three now run the same pieces:

| Piece | Where | What it owns |
|---|---|---|
| `PlatPursuit.virtualBoard` | `static/js/utils.js` | The ENGINE: spacer, absolute placement, eviction, jump |
| `PlatPursuit.wireBoard` | `static/js/utils.js` | The WIRING: read the data attributes, build the rows URL, hook the jump chip and rank box |
| `PlatPursuit.boardEntrance` | `static/js/utils.js` | The first screenful's cascade, in WAAPI |
| `leaderboard_board.html` | partial | The shell: root, data attributes, the wall (shipped as FLOW; the engine promotes it) |
| `leaderboard_row.html` | partial | One row |
| `leaderboard_rows.html` | partial | One WINDOW: bare rows, no wrapper |
| `leaderboard_jumpbar.html` | partial | Jump-to-me + the rank box |
| `leaderboard_boardcard.html` | partial | The identity: name, one-line meaning, counting tally |
| `board_helpers.window_params` | `trophies/views/` | `?range=` / `?count=`, clamped at both ends |
| `board_helpers.PAGE_SIZE` | `trophies/views/` | 50, once. It was declared three times |
| `board_suggest` + `wireBoardSearch` | service + `utils.js` | The hunter typeahead, on all four boards |

**Every board endpoint answers two requests, told apart by the PRESENCE of `range`:** no `range` builds
the full panel; `?range=N` returns bare rows for display positions `[N, N+count)`. The value is not what
distinguishes them -- junk `range` is still a window request, because the caller asked for rows and
splicing a jump bar into the middle of a wall is worse than an off-by-one.

Each surface composes the same two bands: a compact utility card (`.lb-controls`, `p-3 md:p-4`) holding
the board card and the jump bar, then the wall free below it. That is the site-wide STACKED CHROME CARDS
+ FREE CONTENT rule, and it is the half the first propagation missed -- badge and job detail got the
wall, the row and the jump bar, and then sat all of it bare on the page background. The boards behaved
identically and did not look like the same product, which was the entire point. `test_board_uniformity`
now asserts the design as well as the contract.

**Game detail joined in step 10.** It was the holdout: same engine, but its own row, chrome and
controls, which made the most featured board on the site the one that looked least like the others. It
was REDUCED onto the shared row rather than the shared row being grown to fit it -- the per-tier trophy
dots, the completion bar and the speed board's second date are gone, and the stage that mattered is that
this was a deliberate trade, recorded in `test_game_leaderboard_view`, not a silent trim.

Three things went with the convergence:

| Gone | Why |
|---|---|
| `invert` (bottom-first) | Existed only here, and forced a second answer to every ordering question: display order vs canonical rank, `from` vs `start`, nulls-first vs nulls-last. The engine lost its `invert` and `from` with it. |
| `registered_only` | An OPT-IN "members only" over a board that otherwise ranked every scraped PSN profile -- so the behaviour every other board has by default was the one you had to ask for. The board is `is_linked`-gated unconditionally now. |
| Toggle chips | `aria-pressed` buttons doing the job the other boards do with `.lb-filters` selects. |

And country arrived on the two boards that lacked it. Job detail reads its denormalized `country_code`;
game detail has none (a mirror on a per-(profile, game) table is the wrong trade) so it rides the Profile
join the `is_linked` gate already makes.

What game detail still has that the others do not -- hunter search, three board kinds, trophy-group
scoping -- are FEATURES, not drift. The search sits in `leaderboard_jumpbar.html`'s `extra_partial` slot
precisely so it can spread when the `pg_trgm` question is answered. It runs the same engine, but its rows carry
per-tier trophy counts, a completion bar and three board-kind variants (progress / speed / playtime) that
the shared row has no slot for. Folding it in would mean growing the shared row four ways to serve one
caller. Uniformity across the other three is guarded by `tests/engine/test_board_uniformity.py`.

### Step 12: the per-edition board gets a store

The edition filter on badge detail read three different things before it was right, and each wrong turn
was wrong in a way that looked fine:

1. **`UserGroupBadge`** (earners) -- genuinely per (series x edition), so it read as the obvious store. It
   holds only FINISHERS, so any badge with chasers and no finishers emptied under every edition. Fixed by
   reading the chasers' store instead.
2. **`SeriesBadgeStanding`'s JSON maps** -- the right population, ordered on `Cast(group_xp -> key)` and
   gated on `Cast(group_progress -> key -> 0) > 0`. Correct answers, and two problems underneath them:
   nothing past `series_slug` was indexable (so every virtual window re-sorted the whole series), and the
   tiebreak was `SeriesBadgeStanding.advanced_at`, which is SERIES-wide. **Two hunters tied on Legacy HD
   points were separated by their Ultra HD progress -- advancing in one edition could drop a rank in
   another.**
3. **`SeriesEditionStanding`** (migration 0313) -- a row per (profile, series, STARTED edition), with that
   edition's points and its own date.

The store cost no new evaluation. `recompute_standing` already loops every edition holding its
`GroupBadgeResult`, and `_advanced_at` is a pure function of one of those -- `compute_series_standings`
only ever asked it for the furthest-along edition, so the per-edition date was always one call away rather
than one computation away. What it costs is WRITE VOLUME in the nightly chain, held down by storing only
STARTED editions -- the board's own membership rule, moved from every read to one write.

Deploy needs nothing new: the table is created empty and filled by the `evaluate_badges --all` a cutover
runs anyway, exactly as `ProfileEditionStanding` was in 0300. A seeder command was written first and
deleted -- the only `advanced_at` it could derive was the series-wide one this store exists to stop using,
so it would have produced a board that looks migrated and still tiebreaks wrong. See the
[deploy checklist](prod-deploy-checklist.md).

---

## Gotchas and Pitfalls

- **`IN (subquery)` is what dedupes the Badge Trophies board.** Anyone "fixing" it into a join through Stage will
  silently start double-counting trophies for games in multiple badges, and the number will look plausible.
- **The per-edition figures do not sum to the all-editions row.** Cross-gen games count in both. The
  all-editions total is READ from `ProfileBadgeStanding`, never added up from the editions.
- **A profile-wide figure must be re-summed from every `SeriesBadgeStanding` row**, never from the results
  of the recompute call, which may be scoped to one series.
- **Any new store with a `country_code` mirror has to join `signals.country_mirrored_standings()`.**
  Forgetting it does not error; it strands a relocated hunter under their old flag on that board alone.
- **A board's `> 0` membership rule belongs in the row function, not only on the count.** An edition
  standing survives on zero points when the hunter has trophies there but no cleared gating stage, so an
  unfiltered read hands the last page rows the count never promised.
- **A board that SCOPES a population must scope every key it orders on.** The per-edition board scoped its
  points and inherited its DATE from the series, so a hunter's rank on one edition moved when they
  advanced on another. Nothing on the board being read had changed. Scoping the leading key is the part
  you notice; the tiebreak is the part you do not.
- **Two stores over one truth need their prune written at the same time as their write.** A series that
  drops to zero XP deletes its `SeriesBadgeStanding` row, so nothing would ever revisit its edition rows
  -- leaving the edition board ranking somebody the series board had already dropped. Both deletes live
  in `recompute_standing`, next to each other, with a test each.
- **Tab links are built in the VIEW, per target board.** A single shared querystring tail was the first
  attempt and it handed Career an edition it ignores — so the link went one place and the rank shown beside
  it was measured somewhere else.
- **Retiring a URL *name* 500s its `RedirectView`s**, it does not 404 them. Repoint in the same change.
- **The directories will want Browse Games' filter panel.** That is the convergence the thin-directory
  rule exists to prevent. Reuse the wall, not the facets.
- **A personal rank on a directory card forfeits caching for the whole section.** It is not a small
  addition; it changes the section from public-cacheable to per-user.
- **Country as a page rather than a filter** re-introduces the per-slice materialization this design
  removes. Query parameter only — and not on the directories at all (§3).
- **Per-row user state on a long list must be batched per PAGE.** The job Contracts tab is the live case:
  each row needs the viewer's completion on that contract's concept, and a `for contract in page:` loop
  doing a lookup each looks fine at 24 rows and is not at 200. Infinite scroll bounds the rows rendered,
  never the queries per row — those are two different problems and only one of them is solved by
  pagination.
- ~~**The participants gate is dataset-sized, and its failure mode is a confident lie.**~~ **GONE with the
  directories (2026-08).** `BOARD_MIN_ENTRANTS` was deleted from settings along with the pages that read
  it, so there is nothing to set: `BOARD_MIN_ENTRANTS_GAMES=1` in a dev `.env` is now dead and can be
  removed. The lesson survives the setting, though, and is worth carrying to any future gate: a
  prod-calibrated threshold empties the page on every smaller dataset, and "no board has enough hunters
  on it yet" is specific, authoritative, and wrong.
- **The game gate reads `Game.played_count`, a signal-maintained denorm.** It is incremented by a
  `post_save` on ProfileGame CREATION, so `bulk_create`, fixtures and database restores bypass it and
  leave 0 while the rows sit there intact. The nightly `recalc_earn_rates` repairs it (chunked, resumable,
  30-minute budget -- one run may not cover a large catalogue), or `backfill_played_count` does it in one
  DB-side UPDATE.
- **`earners_ranks()` is len(held) queries by its own docstring.** Fine for a page of medallions, not for
  a directory. Use the windowed query.
- **`staggerReveal` and a virtualized wall are incompatible, and the collision is silent.** The helper
  adds `.pp-reveal` to the container PERMANENTLY, and `.pp-reveal .lb-row { opacity: 0 }` holds every row
  invisible until an IntersectionObserver grants `.is-revealed`. A virtual wall mounts and evicts rows by
  scroll position, so they never reach that observer -- the board renders a full-height spacer of blank
  space. This shipped twice (badge detail's "show more", then the Global Boards wall) before the boards
  moved to `boardEntrance`, which uses WAAPI and leaves no class behind to outlive its frames.
- **A board's page size must be read off the DOM, never as a JS constant.** `wireBoard` takes every
  number from `data-lb-*`. A client that pages by 25 against a server that pages by 50 does not error --
  it shows GAPS in the rows, which reads as missing hunters rather than as a bug.
- **A row without `data-lb-rank` is invisible on a virtual wall.** The engine places rows by it. The row
  is spliced in successfully and then positioned nowhere.
- **Test the MOUNT, not just the markup.** A board is inert until something calls `wireBoard` on it, and
  the failure looks like a working page whose scrolling never loads anything. Job detail's switcher was
  once written into `{% block extra_js %}` -- a block `base.html` does not declare -- and Django discards
  an undeclared child block with no error and no warning, so the script never reached the browser at all.
- **Assert the call, not the name.** These pages carry an `if (!PlatPursuit.wireBoard) return;` capability
  guard, so a test looking for the bare name passes on the guard and goes vacuously green the moment the
  call is deleted. Caught by a mutation run, not by reading.
- **An offset cap only does something if it sits BELOW the board.** `OFFSET n` costs
  `min(n, board_size)` rows walked, because the scan stops when the rows run out. A cap set "past any
  real board" is by construction a cap that never binds — which is what briefly replaced badge's 10,000
  and job's 9,975 with 100,000,000, making both boards *cheaper to attack than before the rebuild*.
  `MAX_START` is 1,000,000: every board is bounded by the linked-profile count, so that is roughly 20x
  the current population — high enough that no real reader meets it, low enough to still be a bound. It
  cannot go back to 10,000: a hunter ranked #40,000 has to be able to reach their own row.
- **A clamp test that only asserts `CONSTANT < some_literal` pins nothing.** Three of them shipped that
  way and none would have failed on the change above. A real one needs a board LARGER than the ceiling
  and a range INSIDE it, so the clamp is observed rather than assumed — an over-large request against a
  small board returns an empty window whether or not any clamping happens.
- **Test the second response mode against the FIRST one's gates.** The dormant-series 404 sat above the
  `?range=` branch and was only ever tested on the full-panel path, so hoisting that branch for
  performance would have served unreleased boards with the suite green.
- **An unmounted virtual wall is a zero-height pile, so the class that virtualizes it must come from the
  ENGINE.** `lb-wall--virtual` absolutely positions every row; the height reserving their space is set at
  mount. Ship them together and any board that fails to mount -- JS off, a failed panel fetch, a missing
  `data-lb-page-size`, a cached older `utils.js` -- renders its rows on top of each other with the rest of
  the page drawn through them. The server ships a flow list; `virtualBoard` promotes it.
- **`data-lb-board` belongs to the engine.** Game detail used the same name for its own "which board is
  selected" param on the `.gd-lb` wrapper AND on every switcher chip. The wrapper is outermost, so
  `querySelector('[data-lb-board]')` returned it, and it has no `data-lb-total` -- the engine read a size
  of zero and declined to mount. Every symptom was an ABSENCE (no viewer highlight, no jump, no infinite
  scroll), nothing threw, and once the wall shipped as flow it did not even look broken. That page's param
  is `data-lb-boardparam` now.
- **A test that proves the markup is not a test that proves the feature.** `data-lb-viewer-rank` reaching
  the board root was asserted and passing throughout the collision above. It only ever proved the server's
  half. The uniformity suite now also pins that the rank MATCHES the viewer's own row, per surface --
  those two numbers come from different code (rows numbered by slot, rank counted by walking ahead) and
  agree only while both reads share one population.
- **A sticky proxy must not reuse the attributes it proxies.** The minibar's rank chip and search are
  `data-lb-mb-*`, not `data-lb-*`, because `wireBoard` finds the search field with `querySelector` -- the
  FIRST match -- so a duplicate inside the same scope wires one field and leaves the other silently dead.
  Game detail's older minibar made the same call for the same reason, and the one time this rule was
  broken (`data-lb-board` on the panel wrapper) it cost a day.
- **The bar goes OUTSIDE the swapped wrapper; the sentinel cannot.** The sentinel marks where the chrome
  ends, so it lives inside `[data-lb-page]` and every swap hands the observer a detached node --
  `StickyReveal.init()` is idempotent and re-runnable for exactly this, and is called per mount. Putting
  the BAR inside too would tear it out from under a reader mid-scroll and kill its wired-once listeners.
- **Two XP economies, one word** was the original sin here. After the rename, resist any "total XP" that
  sums them — the architecture seals them apart on purpose.

## Integration Points

- [Badge backend rebuild](badge-backend-rebuild.md) — Lane B read layer, cutover sequencing (§6)
- [Leaderboard system](../../architecture/leaderboard-system.md) — the Redis architecture this replaces;
  goes substantially stale at cutover and must be rewritten in the same branch
- [Game leaderboards](../../features/game-leaderboards.md) — shipped Phase 1; the Ranks-panel precedent
- [Game leaderboards Phase 2](game-leaderboards-phase2-plan.md) — unbuilt per-game board family
  (per-DLC-group progress, speed, playtime). Out of scope here; would slot into Game Boards when built
- [IA and sub-nav](../../architecture/ia-and-subnav.md) — hub definitions, `core/hub_subnav.py`
- [Rebuild playbook](rebuild-playbook.md) — page-status tracker
