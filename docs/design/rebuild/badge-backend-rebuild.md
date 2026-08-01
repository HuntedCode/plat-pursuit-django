# Badge Backend Rebuild — Architecture Review & Target Design

Status: **proposal / under discussion** (2026-08). This is the review we align on before writing the new
engine. It captures the current-state debt, the target "sealed subsystem" design, and the migration plan.

Related design threads: the badge *reframe* itself (tiers → platform-compatibility grouping badges, base→holo,
gate-vs-satisfy, the delisted policy, Legacy HD / Ultra HD) is the product spec this backend serves. See the
badge-system architecture doc for the *current* system being replaced.

---

## 1. Scope & principles

**Charter: rebuild the badge earn/eval/XP subsystem as a sealed unit.** Touch only what we must.

- **In:** the evaluation engine, the earn model (grouping badges, base/holo, maintenance), `earn_rank`,
  badge XP, and badge leaderboards (all confirmed closed-circuit).
- **Out:** the Milestone system (may be archived pre-release; do not build for it), the Notification system
  (needs its own rework; we emit to it, we don't own it), and the jobs/contracts economy (already decoupled).
- **Reuse as read-only inputs:** `Stage` / `ConceptBundle` / `Concept` / `Game` / `ProfileGame`. The engine
  *reads* these; it never writes badge state into them.

**The sealing contract (ports & adapters):**

- **Inputs (read-only):** the two completion signals — `ProfileTrophyGroup` default-group progress (base) and
  `ProfileGame.progress` (full/holo) — plus the `Stage`/`Concept`/`Game`
  graph, and the badge/group definitions.
- **Outputs:** a queryable earn/holo/progress result, plus a single **"badge earned" event**.
- **Badge XP + leaderboards live inside the box.** They never read/write the jobs/contracts economy.
- **Notifications, milestones, titles consume from outside through thin adapters** we keep deliberately crude,
  so those systems can be reworked/archived without opening the engine.

**Why separate tables (not additive fields on `Badge`):** *a new table is dormant by construction; a new row
in an existing table is dormant only if every query remembers to exclude it.* The current `Badge` table is read
by many `series_slug`/`tier` queries that don't filter `is_live` (incl. an admin bulk-toggle that flips every
row sharing a slug), so in-place dormancy is a standing hazard. New tables remove the hazard entirely.

---

## 2. Current-state pain points (what the rebuild fixes)

From a full read of `badge_service.py`, `xp_service.py`, `leaderboard_service.py`, the managers, signals, and
models. Prioritized:

1. **Duplicated stage-completion logic.** `_get_stage_completion_from_cache` (badge_service) and
   `Badge.get_stage_completion` (models) implement the same plat/progress/bundle rules twice, hand-synced.
2. **Denorm held current by 3-4 parallel mechanisms.** `ProfileGamification` is refreshed by a UserBadge-create
   signal, a delete signal, *and* manual `refresh_profile_gamification` calls wherever `.update()` bypasses
   signals (the maintenance transitions), *plus* a `bulk_gamification_update` deferral mode. Every new state
   transition risks forgetting one. This is the single most brittle pattern in the system.
3. **Per-badge query fan-out.** `_find_stage_completion_details` issues fresh per-stage `ProfileGame` queries
   *outside* the prefetched context (completion *booleans* are cached; completion *dates* are not), giving
   O(badges × stages) queries for a profile in many series.
4. **`earn_rank` is a live COUNT at insert.** Non-serialized (documented tie risk), semantically "award order"
   not "completion order," and it counts maintenance rows. The true completion date lives elsewhere
   (`StageCompletionEvent.completed_at`, `ProfileGame.most_recent_trophy_date`).
5. **`earned_count` ≠ active earners.** Incremented/decremented only on UserBadge create/delete, never on the
   maintenance lapse — while `rarity_pct` counts all rows. Two "how many earned this" semantics coexist.
6. **Stale catalog denorms.** `required_stages` and `most_recent_concept` refresh only via an out-of-band
   command, never during eval — so a stage edit silently desyncs award math (`required_stages` feeds megamix).
7. **Per-profile, per-series, in-Python eval on the sync worker.** No cross-profile batching; the series-refresh
   path loops profiles serially. Synchronous inside `_job_sync_complete`.
8. **Award correctness rides on mutating shared `_context['earned_badge_ids']` mid-loop** — stateful, ordering-
   sensitive, hard to test.
9. **Side-effects interleaved with award logic** — notifications / leaderboards / Discord fire from inside the
   award path, so you can't reason about "what did the user earn" separately from "what got sent."
10. **Thin coverage of the date/event machinery** — `StageCompletionEvent`, the real-platinum bundle path, and
    whale query-counts are the least tested and the most load-bearing for the backfill and performance.

---

## 3. Target architecture

### 3.1 Models (new, isolated tables) — SCAFFOLDED

Four models, in a three-layer composition (group look / series definition + defaults / per-group earnable +
overrides). Live in `trophies/models.py` (marked "BADGE REBUILD" section); migrations `0267` (schema) + `0268`
(seeds Legacy HD / Ultra HD).

- **`PlatformGroup`** — the group config + shared look: `key`, `name`, `platforms` (list), `exclude_delisted`,
  `medallion_shape`, `backing_key`, **`background_image`** (the group's shared backdrop — bg1 for Legacy, bg2
  for Ultra) + `holo_background_image`, `sort_order`, `is_active`. `matches_platforms()` does the routing.
- **`BadgeSeries`** — the **abstract series layer** (one per `series_slug`): identity, `badge_type` (series /
  franchise / collection / developer / user / event / **megamix** — all in one table; they share the earn
  engine and differ only in attribution/set/label), `completion_policy` (`all` | `min_count`) + `min_required`
  (the megamix knobs), subject attribution (franchise/collection/developer/`submitted_by`), and the **defaults**:
  `title` (one per series), `badge_image` + `holo_badge_image` (subject art), `funded_by`. Replaces the old
  `base_badge` self-FK inheritance.
- **`GroupBadge`** — the **earnable** per-group badge (one per `series` × `platform_group`): `is_live`,
  `set_number`, per-group denorm (`earned_count`, `required_stages`, `rarity_*`), and **nullable overrides**
  (`badge_image_override`, `holo_badge_image_override`, `funded_by_override`) with `effective_funded_by` /
  `effective_holo_image` / `art_layers()` accessors. **No `tier` field** — the `tier=1` collision disappears.
- **`UserGroupBadge`** — the earn: `profile`, `group_badge`, `status` (earned/maintenance), `earn_rank`,
  `is_holo`, `earned_at`, `is_displayed`.

**Art composition (single source of truth in `GroupBadge.art_layers()`):** group `background_image` +
`backing_key` + `medallion_shape`, with the subject art resolved *per-group override → series default → (for
`user` badges) submitter's `avatar_url` → static default*. So artwork defaults to one asset per series and only
diverges when a group overrides it — keeping art production bounded.

`Stage`/`ConceptBundle`/`Concept`/`Game`/`ProfileGame` are reused unchanged as read-only inputs.

### 3.2 The engine: pure **evaluate → diff → apply**

The core fix for pain points 1, 8, 9. Three separable steps:

1. **`evaluate(profile) → DesiredState`** — a **pure function** of ProfileGames + definitions. No writes, no
   side-effects. Returns, per grouping badge: base-earned?, holo?, per-gating-stage status + **completion
   dates**, and progress. Encodes the gate/satisfy + base/holo rules **once** (single source of truth).
2. **`diff(DesiredState, CurrentState) → Changes`** — awards, lapses, holo on/off. Stateless.
3. **`apply(Changes)`** — writes `UserGroupBadge`, updates denorm in one place, and emits events through the
   adapters. The *only* step with side-effects.

This makes the engine idempotent, reconcilable (run `evaluate` twice → identical), whale-testable (assert
query counts), and lets the reconciliation harness call `evaluate` without touching the DB.

### 3.3 The evaluation model (single source of truth)

Per grouping badge (`series_slug` + `platform_group`), for each `Stage` of the series:

- **Qualifying games** = games under the stage's concepts whose `title_platform` ∩ `group.platforms`.
- **Gates** (stage is *required*): qualifying game that is `is_obtainable` **and** (group includes delisted, or
  `not is_delisted`).
- **Satisfies** (counts if the user earned it): *any* qualifying game the user reached a bar on.
- **Base bar (per game) = the default trophy group at 100%** (`ProfileTrophyGroup.progress == 100`, floored,
  the base list). This IS the platinum for plat games and the main list for no-plat games — DLC-independent —
  so the engine reads a single `base_complete` boolean with **no platinum-specific branching**, and it fixes
  the old wart where no-plat games needed DLC to earn base. (`ProfileTrophyGroup` is a denormalized, indexed
  per-group completion written every sync, so this is a cheap bounded lookup, not a trophy aggregation.)
- **Holo bar (per game) = the whole game at 100%** incl DLC (`ProfileGame.progress == 100`) -> `full_complete`.
- **Base** = every gating stage has a qualifying game at `base_complete`. **Holo** = ...at `full_complete`
  (live, cosmetic, no XP — flips both ways, no maintenance). The base->holo gap is exactly "did the DLC too."
- `ConceptBundle` keeps its "all members at 100% = synthesized platinum" semantics.

### 3.4 Denormalization: service-owned, not signal-scattered

Kill the `.update()`-bypasses-signals fragility (pain point 2). `apply()` owns all denorm, derived from
`DesiredState` in one place: `GroupBadge.earned_count`, `required_stages` (recomputed from the group's gating
stages so it can't go stale — fixes 6), XP, leaderboard deltas. One authoritative **recompute-from-scratch**
path (idempotent); incremental updates, if kept for speed, call the *same* computation. Define **one**
"how many earned this" semantic (recommend: `earned_count` = active `status='earned'`; rarity uses the same).

### 3.5 XP + leaderboards (sealed core)

Badge XP moves into the new subsystem's own store (cleanly — it was never commingled with milestones). Keep a
**stable read surface for `total_badges_earned` / `unique_badges_earned`**, the only thing milestones read.
Leaderboards (XP, per-series earners, progress, country/community) are all badge-specific and fold in; the
progress leaderboards' reads into the trophy engine (`EarnedTrophy`/`Game` scoped by `Stage`) become a defined
input contract, not a coupling.

### 3.6 The adapter seams

- **Titles (outbound write — top priority).** Award/lapse currently writes the shared `UserTitle` table
  directly. Invert to `grant_title(profile, title, source='badge', id)` / `revoke_title(...)`.
- **"Badge earned → payload" (outbound).** The notification layer currently reaches *into* badge internals to
  compute XP / next-stage progress. Invert: the engine publishes a self-contained payload; notifications
  consume it. Keep the adapter crude — notifications get reworked later.
- **Discord embed (outbound).** Route the consolidated embed through the same event/adapter.
- **Inbound reads (stable query surfaces, low risk):** milestone badge-counts, showcase/collection/Frame reads.

---

## 4. `earn_rank` — completion-ordered (the history checker)

**Decided:** rank means **first-to-complete**, not first-to-be-awarded. Define a user's
"earned-when" for a grouping badge = the date they satisfied their **last gating stage** in that group (the max
over gating stages of that stage's earliest satisfying-game base-completion date — cleanly available as the
game's **default `ProfileTrophyGroup.last_trophy_at`**, the moment the base list was finished). Assign
`earn_rank` deterministically by ordering qualifying profiles on that date (tie-break: earliest contributing
trophy date, then profile id — no live COUNT, so no tie hazard).

- **At cutover:** seed `1..N` per grouping badge from historical completion dates, so "you were the 47th to
  complete God of War — Ultra HD" is true even though the badge is new.
- **Go-forward:** new earners get a rank continuing the sequence, stamped at their completion moment.

This fixes pain point 4 and makes the migrated serial genuinely prestigious rather than an artifact of when we
happened to create the row.

---

## 5. XP model (DECIDED)

Tiers are gone, so the old `completed_concepts × tier_xp (250/75/250/75) + 3000 bonus` doesn't map. Kept
**simple and distinct from the jobs/contracts economy** (per the "don't build a second economy" rule):

- **Flat XP per gating stage cleared** (a progression drip as you work a group) **+ a flat badge-completion
  bonus** when the base badge is earned. **No holo XP** — holo is live/cosmetic, and attaching XP would make
  totals blink on and off as it flips.

**Built to change later without replumbing.** The formula lives in **one pure function**,
`compute_badge_xp(DesiredState) -> (total, per_series_breakdown)`, reading **named constants**
(`XP_PER_STAGE`, `XP_BADGE_COMPLETION_BONUS`). Because it's handed the full `DesiredState` — every gating stage,
every earned badge, group + completion facts — any future formula (group weighting, stage-count curves, rarity
bonuses) is a **single-function edit with all inputs already present**, not a new data path. We deliberately do
NOT build a speculative plugin/registry now (YAGNI); centralization plus a rich input is what makes it cheap to
evolve.

---

## 6. Migration, reconciliation & cutover

Standard parallel-change / expand–contract with a separate schema:

1. **Build** the new tables + engine beside the old; old system runs untouched.
2. **Old-vs-new glance (`evaluate_badges --compare-legacy`)** — a read-only, SERIES-level report of
   kept / lost / gained recognition vs the legacy tier earns (held any old tier vs earns any new group), plus a
   sample of the lost to eyeball. This REPLACES the originally-planned full reconciliation harness: with the
   engine exhaustively unit-tested, any profile inspectable via `--dry-run`, and cutover per-badge + reversible
   (old `UserBadge` retained), a dedicated full-population harness was over-built. Idempotency ("evaluate twice
   → identical") is true by construction (pure engine, no randomness) and already covered by the deterministic
   tests, so it needs no runtime assertion. Losses are EXPECTED where the reframe raised the bar (old low tier
   → full platform group); the glance surfaces them per series before flipping so each can be confirmed intended.
3. **Author dormant badges + art on prod** — in the new tables, invisible with zero guarding (nothing on prod
   reads them yet).
4. **`earn_rank` backfill** from completion dates (§4).
5. **Cutover behind a flag:** rebuild display reads the new store; sync calls the new engine; the old
   `badge_service` stops; XP/titles/notifications source from the new adapters. Reversible.
6. **Soak**, then **decommission** the old `Badge`/`UserBadge`/`badge_service` once stable.

Old `UserBadge` history is never deleted at cutover — retained for rollback and audit.

---

## 7. Decisions

1. **XP model** — DECIDED: flat XP per gating stage + a badge-completion bonus, no holo XP, in one swappable
   `compute_badge_xp` function (§5).
2. **`earn_rank` semantics** — DECIDED: first-to-complete via completion dates (§4).
3. **Reconciliation depth** — DECIDED: descoped to the read-only `evaluate_badges --compare-legacy` series-level
   glance (kept/lost/gained + a lost sample), in place of a full-population harness. See §6.2.
4. **Denorm** — DECIDED: fully service-owned recompute in `apply()`, no scattered signals.
5. **`earned_count` semantic** — DECIDED: active earners only (`status='earned'`); rarity uses the same count.

---

## 8. Suggested sequencing

1. Models + `PlatformGroup` config.
2. The pure **evaluate** engine + tests (single-source completion, gate/satisfy, base/holo, whale query-count).
3. **diff/apply** + service-owned denorm + the three adapters.
4. XP + leaderboards (sealed).
5. Authoring + processing tools: `convert_series_to_groups`, `evaluate_badges` (its completion-ordered batch IS
   the `earn_rank` backfill), `--compare-legacy` glance. [DONE — Phase 3 complete]
6. Author dormant badges + art on prod.
7. Cutover flag + soak + decommission.

---

## 9. Gotchas & risks

- **A from-scratch earn engine can silently mis-award.** Mitigated by the exhaustive engine unit tests, the
  `--dry-run` inspectability of any profile, per-badge reversible cutover, and the `--compare-legacy` glance
  (§6.2) — in place of a heavyweight reconciliation harness.
- **Milestones read badge counts.** Preserve `total_badges_earned` / `unique_badges_earned` as a stable query,
  or the two milestone criteria break.
- **Holo XP is a trap.** Live + lapsing + XP = fluctuating totals. Keep holo cosmetic.
- **Completion-date data must be trustworthy** for `earn_rank` — it leans on `StageCompletionEvent.completed_at`
  and `ProfileGame` dates, which are exactly the least-tested paths today. Backfill coverage first.
- **Don't let the sealed core regrow tendrils.** Every new outbound need goes through an adapter, or the "closed
  off" property erodes.
