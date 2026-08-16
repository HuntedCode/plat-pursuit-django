# Leaderboards — Section Rebuild

> Status: **PLAN — agreed in design, not yet built.** Supersedes nothing; the section has never had a
> rebuild-playbook row. Backend audit performed 2026-08; the three cost defects it found are already
> fixed (commit `2b0bf02e`) and are not part of this plan.

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
| Badge series | New Ranks panel on badge detail (**replaces** `/leaderboards/badges/<slug>/`) |
| Job | Ranks tab on new job detail (**new surface**) |

This is what keeps the directories honest: they are discovery, they own no data, and there is exactly one
canonical location per board. No page can drift from another because no second page exists.

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

**Rule:** a leaderboard directory gets *search plus two or three leaderboard-native sorts*, and nothing
else. No filter drawer, no facet panel, no genre/platform filters.

- Native sorts are about the **board**, not the entity: most contested, recently changed hands, biggest
  mover, most entrants.
- Scoped by a **minimum-participants gate** — a directory full of one-entrant boards reads as broken and
  drowns the boards worth looking at.
- If a directory grows a filter panel, it has become a second Browse Games and belongs folded back into
  the real one as a view mode instead.

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

## 2. The rename: badge XP → **Badge Points**

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
| `badge_earnable_xp` | Site-heartbeat stat — internal key, user-facing label only |

### Consequence for the landing tabs

Three explicit tabs, no ambiguity:

| Tab | Source |
|---|---|
| **Progress** | Trophies earned across badge games (see §3) |
| **Badge Points** | `ProfileBadgeStanding.total_xp` |
| **Career XP** | Gamification total (see §3) |

Do **not** merge into a single "total XP". The architecture forbids the economies mixing, and a merged
number would be the one figure on the site that means nothing.

---

## 3. Data layer

Everything here exists to make each board a single indexed range scan. Two new materialized columns, one
denormalized column, and a set of composite indexes.

### Global Progress — keep it, and materialize it

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

> **Open — must be confirmed before building.** The badge write seam runs on badge evaluation. Badge-game
> trophy counts change on **any** sync. If the seam is narrower than every sync, this column needs its own
> trigger or it goes stale between badge events.

### Career XP — materialize the total

Pursuer Level is *computed* as the sum of a profile's `ProfileJobXP.level`, not stored. A global board
would aggregate ~24 rows per user across the whole population per read.

Materialize a per-profile career-XP total, bumped in the seam that already bumps `ProfileJobXP` on each
`ContractXPGrant`. Per-discipline totals have the same shape and the same fix, if discipline boards are
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
| `ProfileBadgeStanding` | `(country_code, -total_xp)` |
| `SeriesBadgeStanding` | `(series_slug, country_code, -xp)` |
| `ProfileJobXP` | `(job, country_code, -total_xp)` |
| `ProfileGame` | own composite; mirrors `pg_game_leaderboard_idx` with `country_code` |

Country is a **filter on a board, never a board of its own**. No per-country pages, no per-country URLs
beyond a query parameter. This is the single decision that keeps the section's surface area finite.

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
| `plat_pursuit/urls.py:281` | `community/leaderboards/badges/<slug>/` |
| `plat_pursuit/urls.py:389` | `leaderboard/badges/<slug>/` |

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

## 6. Open questions

1. **What sorts the directories.** The one genuinely undecided piece of design. Sorted arbitrarily, a
   directory of boards is a phone book; sorted by something live, it is the discovery surface this section
   exists to be. Must be answered in design, not at build time.
2. **Does the badge write seam run on every sync?** Gates the Global Progress column (§3).
3. **How many contracts can a job have?** The job-detail Contracts tab is its own wall, and for an authed
   viewer each row carries per-user state. Needs a stated bound, not an assumed one — that is the
   assumption that stops being true after a content push.
4. **Does country propagate from a directory into the board it links to?** Coherent if it does; more state
   to carry if it does.

---

## 7. Build order

Each step leaves the site working.

| # | Step | Ships |
|---|---|---|
| 1 | Materialized columns + country denorm + indexes; backfill commands | Nothing user-visible |
| 2 | Lane B read swap for existing boards; retire Redis reads + cron | Same pages, new backend |
| 3 | Badge Points rename (labels) | Vocabulary fixed before new surfaces spread it |
| 4 | Global Boards landing rebuilt (3 tabs, country filter) | The hub landing |
| 5 | Badge detail Ranks panel; retire `/leaderboards/badges/<slug>/` + repoint 2 redirects | Boards move to entities |
| 6 | Badge Boards + Game Boards directories | Discovery, on shipped machinery |
| 7 | `/jobs/` + `/jobs/<slug>/` (Contracts + Ranks tabs) | New Browse surface |
| 8 | Job Boards directory; sub-nav goes live | Section complete |

Steps 1–2 are the performance work. **Finishing the cutover *is* the optimization** — it is not a
prerequisite to it.

---

## Gotchas and Pitfalls

- **`IN (subquery)` is what dedupes Global Progress.** Anyone "fixing" it into a join through Stage will
  silently start double-counting trophies for games in multiple badges, and the number will look plausible.
- **Retiring a URL *name* 500s its `RedirectView`s**, it does not 404 them. Repoint in the same change.
- **The directories will want Browse Games' filter panel.** That is the convergence the thin-directory
  rule exists to prevent. Reuse the wall, not the facets.
- **A personal rank on a directory card forfeits caching for the whole section.** It is not a small
  addition; it changes the section from public-cacheable to per-user.
- **Country as a page rather than a filter** re-introduces the per-slice materialization this design
  removes. Query parameter only.
- **`earners_ranks()` is len(held) queries by its own docstring.** Fine for a page of medallions, not for
  a directory. Use the windowed query.
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
