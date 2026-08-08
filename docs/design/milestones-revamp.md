# Milestones Revamp — Design Spec

> **Status: Design (not implemented).** Working spec for the from-scratch rebuild of the Milestones system.
> Supersedes the legacy `criteria_type`-enum milestone system (`trophies/models.py` `Milestone` +
> `milestone_handlers.py` + `milestone_constants.py`), which is retired and rebuilt fresh (see §9).

## 1. Vision & Purpose

Milestones are a **personal-pride recognition layer**: a beacon that says *"you hit this in your
trophy-hunting career — congrats."* They reward the **long-horizon achievements that Badges and the
gamification economy don't already cover**, they are **tiered** (there's always a next rung to reach), and
they **speak for themselves**.

Explicitly:
- **No Titles.** Titles stay a Badge-unlock reward. A milestone's reward is *the milestone itself*.
- **No XP.** Milestones do **not** feed the Pursuer level / gamification economy. This is **not** a quest
  system, a currency sink, or a task list.
- **Tiered.** Every milestone is a ladder of thresholds.
- **Start conservative.** A small, meaningful, high-quality set; grow it through later updates.

The old system failed by being the opposite of this: 31 hardcoded criteria types, one row per tier, Title +
Discord coupling, XP-adjacent, and evaluation smeared across ~11 trigger sites. The brief here is
**straightforward and simple**.

## 2. Positioning (vs Badges / Jobs / Titles)

| Layer | Scope | Reward | Feeds economy? |
|---|---|---|---|
| **Badges** | per-game *series* | tiers + XP + **Titles** | yes (Pursuer XP) |
| **Contracts / Jobs** | per-*concept* curated work | job XP + levels | yes (Pursuer XP) |
| **Milestones** | **account-wide, cross-system lifetime feats** | **the recognition itself** | **no** |

Milestones are the **capstone / "Hall of Records"** above the other two — the one place that celebrates your
whole PlayStation career at a glance, deliberately decoupled from the XP economy so it stays pure pride.

## 3. Data Model

The new system is a **dedicated `milestones` Django app** (not part of `trophies`). This sidesteps the name
collision with the legacy `trophies.Milestone*` models entirely — no rename churn — and keeps the new system
self-contained (own models, metrics, services, admin, migrations). Cross-app FKs point at `trophies.Profile`.

Four tables. The split that makes it extensible: **a milestone + its rungs are DATA; the only code is the
measurement (§4).**

### `Milestone` — one row per *concept* (e.g. "Platinum Hunter")
| Field | Type | Notes |
|---|---|---|
| `slug` | Slug, unique | stable key (URL anchor, fixtures) |
| `name` | Char | display name |
| `description` | Text | one-line "what this celebrates" |
| `icon` | Char (Lucide key) or Image | the milestone glyph |
| `metric` | Char | key into the metric registry (§4) |
| `category` | Char (choices) | grouping bucket (Trophy Hunting / Collection / Dedication / …) |
| `sort_order` | Int | ordering within category |
| `is_active` | Bool, indexed | soft-hide without deleting earned history |

### `MilestoneTier` — the rungs, as data rows
| Field | Type | Notes |
|---|---|---|
| `milestone` | FK | |
| `index` | Int | 1-based rung order |
| `threshold` | PositiveInt | metric value required to earn this rung |
| `name` | Char, blank | **optional** flavor name (e.g. "Legend"); default display is numeric (§7) |
| `earned_count` | PositiveInt, default 0 | denormalized global earn counter → rarity (§6) |
| `discord_role_id` | BigInt, null/blank | **optional** Discord role granted on earn (§5.1). Admin-config only, never rendered. |

`unique_together(milestone, index)`. "10 / 50 / 100 / 500 platinums" = **one** `Milestone` + four
`MilestoneTier` rows.

### `EarnedMilestoneTier` — the permanent "you reached this rung" record
| Field | Type | Notes |
|---|---|---|
| `profile` | FK | |
| `tier` | FK → MilestoneTier | |
| `earned_at` | DateTime | powers the congrats moment + "most recently earned" |

`unique_together(profile, tier)`. **Never deleted** — permanent career record (mirrors the old system's
retention promise).

### `UserMilestone` — materialized progress read-model (whale-safe render)
| Field | Type | Notes |
|---|---|---|
| `profile` | FK | |
| `milestone` | FK | |
| `current_value` | Int | last computed metric value |
| `highest_tier_index` | Int, default 0 | 0 = none earned yet |
| `updated_at` | DateTime | |

`unique_together(profile, milestone)`. Written by the recompute sweep (§5) so the page reads O(1) — never
live-evaluates metrics on the request path.

## 4. Metric Registry — the extensibility mechanism

The **one thing that needs code**. A metric is a named function returning a **single whale-safe per-profile
aggregate**:

```python
# trophies/milestone_metrics.py
MILESTONE_METRICS: dict[str, Callable[[Profile], int]] = {}

def milestone_metric(key):
    def deco(fn): MILESTONE_METRICS[key] = fn; return fn
    return deco

@milestone_metric("lifetime_platinums")
def _lifetime_platinums(profile) -> int:
    return profile.profile_games.filter(has_plat=True).count()
```

**Contract (enforced by convention + review):** a metric MUST be a single bounded aggregate
(`.count()` / `.aggregate(Sum/​Count(distinct))` / a denormalized field) — **never** per-row Python
iteration (CLAUDE.md whale-safety rule; users range to 250K+ trophies).

**What this buys us:**
- **New milestone on an existing metric = pure data** (an admin row / a fixture) — no deploy.
- **A genuinely new *measurement* = one function** in this file. That's the floor; you cannot measure
  something new without *some* code, but it's one place, not the old four.

## 5. Evaluation & Freshness

One sweep, no scattered triggers.

```
recompute_milestones(profile):
  actives = Milestone.objects.filter(is_active=True).prefetch_related('tiers')
  cache = {}                                  # metric key -> value, computed once per sweep
  for m in actives:
      value = cache.setdefault(m.metric, MILESTONE_METRICS[m.metric](profile))
      newly = tiers of m with threshold <= value AND not already in EarnedMilestoneTier
      award newly (create EarnedMilestoneTier, F()-bump tier.earned_count)
      notify the HIGHEST newly-earned tier only (anti-spam "congrats")
      upsert UserMilestone(current_value=value, highest_tier_index=max earned index)
```

- **Seams:** post-sync (`sync_complete`) covers the trophy-derived metrics; a **nightly cron**
  (`recompute_milestones_all`) covers time-based metrics (e.g. tenure) + catches everything + refreshes
  rarity denominators. No per-metric trigger wiring, no exclusion sets.
- **Launch backfill:** run the nightly once → every hunter instantly lights up the tiers their real stats
  already earn (see §9).
- Because a metric is just an aggregate, one sweep re-evaluates the *whole* catalog — adding a milestone
  never requires touching the evaluation code.

### 5.1 Discord roles (backend side-effect)

A tier may carry an optional `discord_role_id` (§3). Discord roles are a **backend-only** side-effect —
nothing about the role or its mapping is shown in the web UI, and it does not compromise the "pure pride"
principle (a Discord role is external, not an in-app reward: no XP, no Title). **First consumer: Badge
Collector** (roles keyed off total badges earned); set `discord_role_id` only on the rungs that should grant
a role, no obligation to make one per tier.

**Escalation policy: highest-only.** A hunter holds only their **highest** earned role-bearing bracket per
milestone (one role that reflects their current tier), so crossing a higher rung removes the lower role.

**Reconciliation, not fire-and-forget.** Event-only granting is fragile — it misses the "linked Discord
*after* earning," bot-downtime, re-sync, and manual-removal cases. So every path computes the FULL desired
set and syncs it idempotently (this is the robust version of the legacy "just send all their roles"):

- `desired_milestone_roles(profile) -> set[int]` — for each milestone with role-bearing tiers, the
  `discord_role_id` of the **highest earned** role-bearing rung. Union across milestones = the exact set the
  member should hold.
- `managed_milestone_roles() -> set[int]` — every `discord_role_id` across all tiers = the universe the bot
  is allowed to touch (so reconciliation never disturbs non-milestone server roles).
- `reconcile(profile)` — hand PlatBot the desired + managed sets via `transaction.on_commit`; the bot makes
  the member's managed roles exactly equal `desired` (add missing, remove superseded). Idempotent.
- **Triggers (all call `reconcile`):** Discord **link/verify** (grants everything already earned on connect),
  each **tier crossing** in the sweep, and a periodic **safety-net cron**. Nothing is ever missed; it
  self-heals. Reuses the existing PlatBot role plumbing the legacy milestones used.

## 6. Rarity / Prestige Signal

"**X% of hunters have reached this**" per tier, to reinforce the pride/beacon feel. Cheap:
- `MilestoneTier.earned_count` — denormalized, `F()`-bumped on award, **recomputed nightly** for drift
  correction (same pattern as badge rarity + the old `Milestone.earned_count`).
- Denominator = a cached **registered-member** count (`user__isnull=False`), refreshed by the nightly cron.
  Synced / scouted profiles without a site account are excluded so the `%` isn't skewed by sync noise (the
  sweep is scoped the same way). NOT `is_linked` — that includes scouts.
- `rarity_pct = earned_count / total_hunters` — a plain division at render, **O(1)**, zero per-render
  queries. Whale-safe.

## 7. Rung Naming

Default is **numeric** ("100 Platinums") — straightforward and simple, covers most milestones. A tier MAY set
an optional `name` for flavor (e.g. *Platinum Hunter → Novice / Adept / Master / Legend*); when set, the rung
shows the name with the threshold as a subtitle. No named rungs required.

## 8. Starter Catalog (v1 — six, metric-driven ladders)

Six milestones, **10 tiers each** (locked with product). Metric-driven ladders only — no manual/secret/
one-off in v1 (§9). Rung growth roughly doubles up the ladder; the top rung is ~2× the earlier draft max,
**except** Total Trophies (fixed final = 60,000) and Playtime (fixed final = 20,000 hrs), which set those
ceilings deliberately and space the intermediate rungs to reach 10.

| Milestone | Metric | Tiers (10) |
|---|---|---|
| **Platinum Hunter** | `lifetime_platinums` | 1, 5, 10, 25, 50, 100, 250, 500, 1000, **2000** |
| **Trophy Collector** | `lifetime_trophies` *(fixed 60k)* | 100, 500, 1000, 2500, 5000, 10000, 20000, 35000, 50000, **60000** |
| **Completionist** | `full_completions` (100%) | 1, 5, 10, 25, 50, 100, 250, 500, 750, **1000** |
| **Badge Collector** | `total_badges_earned` | 1, 5, 10, 25, 50, 100, 150, 250, 375, **500** ⚠ tune vs badge catalogue |
| **Pursuer Ascent** | `pursuer_level` = `Sum(ProfileJobXP.level)` | ⚠ calibrate (cap-less curve; nonzero baseline) |
| **Time Invested** | `playtime_hours` *(fixed 20k)* | 10, 50, 100, 250, 500, 1000, 2500, 5000, 10000, **20000** |

**Metrics needed** (all single-aggregate / whale-safe):
- `lifetime_platinums` — `ProfileGame.filter(has_plat=True).count()`
- `lifetime_trophies` — denorm `profile.total_trophies`
- `full_completions` — denorm `profile.total_completes`
- `total_badges_earned` — `UserGroupBadge.filter(profile=…).count()` (held group badges in the new subsystem, the same surface the Collection reads; NOT the legacy `ProfileGamification.total_badges_earned` tier count)
- `pursuer_level` — `ProfileJobXP.filter(profile=…).aggregate(Sum('level'))`
- `playtime_hours` — `Sum(ProfileGame.play_duration)` → hours

**Two ladders to calibrate against real data before locking thresholds:**
1. **Badge Collector** — depends on how many badges the catalogue can yield; 500 is a placeholder.
2. **Pursuer Ascent** — the Pursuer Level curve is **flat + cap-less** (`util_modules/leveling.py`) and every
   synced hunter starts with a nonzero baseline (~24 jobs floored at level 1), so the first rung must sit
   above baseline and the ceiling is aspirational, not a hard cap. This one is In Flight (the level economy
   is still settling) — calibrate the ladder once when we build, don't hard-guess now.

**Supporter group (added):** two more milestones live under a light **"Supporter"** section (community + the
premium tenure ones aren't trophy-hunting feats, so a quiet group label separates them from the core):

| Milestone | Metric | Accent | Tiers (months) |
|---|---|---|---|
| **Loyal Member** | `community_months` (since `user.date_joined`) | teal | 1 / 3 / 6 / 12 / 24 / 36 / 48 / 60 / 96 / 120 |
| **Premium Supporter** | `premium_months` (summed `SubscriptionPeriod`) | rose | 1 / 3 / 6 / 12 / 18 / 24 / 36 / 48 / 60 / 84 |

Both are time-based but need no new cron — the nightly sweep re-evaluates every metric. Community tenure reads
the **sign-up date** (`user.date_joined`), not `Profile.created_at` (a synced profile can predate registration).

**Categories:** the six core milestones carry `category="Trophy Hunting"`; the two Supporter ones carry
`category="Supporter"`. Both render under a quiet section label (`{% regroup %}`, ordered by `sort_order` so
each grouper is contiguous), giving the page two clearly separated bands. The `Milestone.category` field
supports more sections as the catalogue grows.

**Page polish (the "what would Google/Apple reach for" pass).** All whale-safe — derived from the already-
assembled cards / read-model, no extra queries:

- **Focal number** — each card's *current total* for its measure is the hero (big accent `.pp-tally`,
  counts up on reveal) with a `metric_unit()` sub-label ("47 / platinums"). The tier count moved to a quiet
  status-line pill so the number isn't crowded. Number-forward, matching the Career page.
- **Header spotlights** (`ms_nearest`, `ms_rarest` in the context; `_spotlights(cards)`): two hooks above the
  summary counts. *Closest milestone* — the non-maxed ladder furthest along toward its next rung (ties broken
  by fewest units remaining): forward pull. *Rarest feat* — the earned tier with the smallest "% of hunters":
  the brag. Both are accent-tinted link tiles into the milestone's surface.
- **Actionable cards** — the whole card is a stretched-link into "where you move this" (`_metric_action`
  maps each metric → a route: trophy/plat/completion/playtime → the viewer's **profile**, badges →
  Collection, level → Career, tenure → Support, premium → membership). Affordance = a name chevron
  (`.msc--link`) + hover lift.
  The ladder is lifted above the overlay so per-rung rarity tooltips survive.
- **Prestige foil** — a fully-cleared (`.msc--maxed`) ladder gets a slow gold foil sweep (reserved for the
  rare "complete" state, rests most of the loop, stills on reduced-motion) — a meaningful-moment flourish,
  not motion-everywhere.

The card icon SVGs live in one partial, `milestones/_msc_icon.html` (shared by the card head + both
spotlight tiles), keyed by the `icon` string.

**Deliberately deferred to a later expansion** (to avoid the old sprawl): community *contributions*
(ratings/reviews — distinct from the tenure-based Loyal Member above),
Supporter (subscription tenure), breadth (distinct genres/franchises), Secret/hidden feats, per-tier flair.
The model supports them with **zero schema change** — new metrics + data rows when we choose to add them.

## 9. Migration / Fresh Start

Because every v1 metric is **derived from data users already have**, going fresh costs users nothing:

1. Ship the new tables (§3) + metrics (§4) + the v1 catalog (§8) as data.
2. Run `recompute_milestones_all` once at launch → every hunter's earned tiers + progress are backfilled
   from their **current real stats**. Nobody "loses" a milestone; they light up what they've already done.
3. **Retire the legacy CODE, preserve the DATA — no rename needed.** Because the new system lives in its own
   `milestones` app (§3), there is **no name collision** with `trophies.Milestone*`, so the legacy models are
   **left in place, untouched and dormant** (data preserved). At cutover (Phase 2) the legacy *code* is
   removed — `milestone_handlers` / `milestone_constants` / `milestone_service` / the old view + templates +
   the ~11 award-trigger call sites — while the `trophies.Milestone` / `UserMilestone` / `UserMilestoneProgress`
   *tables* stay as a dormant data store (§9.5). (This replaces the earlier "rename to `Legacy*`" plan, which
   the dedicated app makes unnecessary.)
4. **Legacy milestone Titles:** the old system granted `UserTitle(source_type='milestone')`. Since titles go
   Badge-only, those grants are removed (a hunter displaying one gets auto-unequipped). **CONFIRMED** — OK to
   remove on cutover.
5. **Legacy manual/one-off recognitions** (fundraiser "Badge Artwork Patron", easter-egg awards):
   **CONFIRMED — keep the data as-is, do not delete, do not port into the new system.** They stay in the
   dormant `trophies.Milestone`/`UserMilestone` tables (unrendered) as a **seed for a potential future
   "Feats / special recognitions" expansion**. The new metric-driven system ignores them entirely.

## 10. Phasing

1. **Model + metrics + evaluation — ✅ SHIPPED (dedicated `milestones` app).** The 4 tables + migration, the
   metric registry (six v1 metrics), the `recompute_milestones` sweep + `reconcile_discord_roles`
   (highest-only) + `recompute_tier_earned_counts` drift-correction, admin (author milestones/tiers as data),
   the `seed_milestones` catalogue command, and the `recompute_milestones` management command (nightly cron +
   launch backfill; `--profile` for one). Tested (`tests/engine/test_milestones.py`). **Not yet wired to any
   trigger or UI** — that's Phase 2. Two ladders (Badge Collector, Pursuer Ascent) still carry placeholder
   thresholds pending real-data calibration.
2. **The page + surfaces** — rebuild `/milestones/` to the Career/Platinum-Pursuit standard (tiered ladders,
   progress, rarity line, congrats states); update the home glance + profile surface. Retire the old
   templates.
3. **Polish** — the "congrats you hit this" celebration moment, rarity prestige styling, empty/guest states.
4. **Expansion (later updates)** — Community / Supporter / Secret metrics + catalog, per-tier flair.

## 11. Gotchas & Pitfalls

- **Metric contract is load-bearing.** A metric that iterates rows in Python (not a DB aggregate) will OOM
  on whales. Enforce single-aggregate in review; the sweep runs each metric once per profile.
- **`current_value` is a read-model, not truth.** It's only as fresh as the last sweep. The nightly cron is
  the freshness floor; don't read live metric values on the request path.
- **Rarity denominator drift.** `earned_count` is `F()`-bumped live but recomputed nightly — don't compute
  rarity off a live `COUNT` per render (whale-unsafe); always read the denorm.
- **Retirement preserves earned history.** `is_active=False` hides a milestone from the catalog but must
  **not** delete `EarnedMilestoneTier` rows (permanent career record). Only the legacy *Title grants* are
  removed at migration (§9.4).
- **No XP / no Title coupling — keep it that way.** The moment a milestone grants XP or a Title it stops
  being "pure pride" and re-tangles with the economy. If that's ever wanted, it's a badge, not a milestone.
- **Tier ordering is by `index`, not `threshold`.** Two rungs could in theory share a threshold; `index` is
  the authority for order + "highest earned."
- **`highest_tier_index` ratchets off EARNED rungs, not current thresholds.** The sweep derives it from the
  union of already-earned + newly-crossed tiers, so raising a tier's threshold above a hunter's current value
  (an upward re-seed) can never walk the read-model back below a rung they permanently earned.
- **Discord reconcile fires unconditionally when the caller asks** (`recompute_milestones(reconcile_discord=
  True)` / `--profile` / link / `recompute_milestones --reconcile-discord`), so it grants roles a hunter
  ALREADY earned — not only fresh crossings. The high-frequency sync trigger (Phase 2) instead passes
  `reconcile_discord=False` and reconciles itself only on a role-bearing crossing (to avoid re-asserting roles
  every sync). `managed_milestone_roles()` includes **retired** milestones' roles so reconcile can strip them.
