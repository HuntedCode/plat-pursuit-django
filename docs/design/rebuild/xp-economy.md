# XP Economy: Flat Cap-less Leveling + Prestige Tiers

> **Status.** Locked + engine built. The engine ships via the `main` PR
> (`feature/gamification/xp-economy-report`); this doc is its spec. Calibration of the
> exact numbers is an accepted open thread (run `report_xp_economy` on prod).
>
> **Scope.** *How* per-job (element) XP turns into levels + prestige tiers, and how any
> XP source (contracts now; quests/events later) feeds the same ledger. The XP *engine*
> mechanics (reached/accepted gates, the T split) live in
> [job-board-contracts.md](job-board-contracts.md); this doc is the *economy* on top.

## TL;DR

| Knob | Value | Meaning |
|---|---|---|
| **T** (`CONTRACT_XP_TOTAL`) | **6,000** | XP a Contract pays, split evenly across its 1–6 jobs (6,000 divides cleanly) |
| **K** (`JOB_XP_PER_LEVEL`) | **3,000** | XP per level, **flat** |
| Curve | `level = total_xp // K + 1` | **cap-less** — the number climbs forever |
| Pursuer Level | sum of all 25 job levels | the headline identity number |

The one identity that drives everything: **Pursuer Level ≈ (6,000 / K) × contracts completed** → at K=3,000, **a contract is worth ~2 Pursuer Levels.**

## Why flat + cap-less (not a RuneScape curve)

An Element isn't a *bounded skill you master*; it's **"how much of this genre have you completed"** — open-ended by nature, like the trophy count itself. So:

- **Cap-less fits the product.** A cap would say "you maxed Champion" while you keep platting RPGs — the number would stop telling the truth. Every new game should always count, and the catalog grows forever.
- **Flat makes modifiers clean.** With a flat curve XP is *fungible*: a double-XP event always doubles levels gained; a quest's "+3,000" is always exactly one level, at level 5 or level 250. An escalating curve would quietly make bonuses worth less at high level — modifiers and a curve fight each other.
- **Cadence.** Each level ≈ one completion (hours of real play), so a flat curve ticks a level on most completions — the steady dopamine a slow grind needs. The *prestige* comes from the **tiers** + the sheer real-world time (99 levels ≈ ~99 games), not from escalating math.

## Pacing (supply-independent — this is the feel)

Per-job XP from one contract = `T / N` (N = jobs on it): solo-genre game → 6,000; 2-job → 3,000; 6-job → 1,000. At a typical N≈2 (~3,000/contract), `contracts to reach level L ≈ (L−1)`:

| Reach | Contracts (into that job) | Pursuer Level @ contracts |
|---|---|---|
| L10 | ~9 | 100 contracts → ~225 |
| L25 | ~24 | 500 contracts → ~1,025 |
| L50 | ~49 | 2,000 (whale) → ~4,025 |
| L99 | ~98 | |

The absolute pool size is irrelevant to feel — it only ever **grows**, so "complete everything" is a moving target. Design from pacing (T, K), not from a supply count.

## Prestige tiers

Cap-less levels carry the infinite endgame; a **bounded, named ladder** carries the milestone journey. `leveling.JOB_TIERS` + `tier_for_level(level)`:

| Tier (placeholder name) | Min level | ≈ genre completions |
|---|---|---|
| Initiate | 1 | — |
| Apprentice | 10 | ~9 |
| Adept | 25 | ~24 |
| Expert | 50 | ~49 |
| Veteran | 75 | ~74 |
| ★ **Master** | 99 | ~98 |
| Grandmaster | 150 | ~149 |
| Legend | 250 | ~249 |

- **99 = Master** is a deliberate nod (the iconic number), reachable but a genuine peak.
- **Legend is the open-ended top.** We do *not* invent tiers past it — beyond Legend the **raw level number is the flex** ("Legend · Lv 312"). This also absorbs any future XP inflation: thresholds are config we control, and the uncapped number always differentiates.
- **Names are placeholders** (mastery-rank flavor). An element-refinement theme (Trace → Pure → Crystalline) is an open option — only the display strings change.

## Pursuer rank (the account-wide ladder)

The element tiers above rank a *single* element's depth. The **Pursuer rank** ranks the whole account off the **Pursuer Level** (the sum of every element level) — a separate, deliberately **deep** ladder because there is one Pursuer per account, so it's the long-haul thing to reach toward. `leveling.PURSUER_RANKS` + `pursuer_rank_for_level(level)`.

**Shape: military-flavored named tier + Roman-numeral division (`V → I`).**

Thresholds are anchored on **games completed** (the meaningful unit), via `Pursuer Level ≈ 25 + 2 × games`:

| Band | Games | Pursuer Level | Divisions |
|---|---|---|---|
| **Newbie** | 0 | 25 (floor) | none — the divisionless floor |
| Recruit | ~5 | 35 | V → I |
| Seeker | ~20 | 65 | V → I |
| Hunter | ~45 | 115 | V → I |
| Ranger | ~85 | 195 | V → I |
| Warden | ~140 | 305 | V → I |
| Marshal | ~220 | 465 | V → I |
| Vanquisher | ~330 | 685 | V → I |
| Paragon | ~480 | 985 | V → I |
| Luminary | ~690 | 1,405 | V → I |
| **Ascendant** | ~1,000 | 2,025+ | none — open-ended apex |

Fast early promotions (a division every ~3 games at Recruit) widening to ~60 games/division near the apex — the ramp falls out of the even split because the tiers themselves widen.

- **Newbie** and **Ascendant** are the divisionless bookends: a humble floor (a brand-new account, every element at level 1) and a transcendent, open-ended ceiling (past it the raw Pursuer Level number is the flex, same cap-less spirit as the elements' Legend). The 9 middle tiers each split into **5 divisions**, entering at **V** and climbing to **I** (the gamer-ranked convention) — `1 + 9×5 + 1 = 47` rungs.
- Divisions are computed by splitting each tier's `[min, next_tier_floor)` range into 5 equal bands — so calibration only tunes the **~10 tier thresholds**, not 47 numbers.
- **Where it lives:** **inline next to the Pursuer name**, separated by a `·` (`Pursuer1 · Warden III`) — the DNA ring keeps its plain `Level` cap. The hero exposes it as `hero.pursuer_rank` (`{key, name, division, division_roman, label, next_label, levels_to_next, …}`); the inline rank (`.lab-rankline`) is tier-keyed (`--<key>`) so the top ranks glow and tiers can carry per-rank display flair later.
- **Names + thresholds are locked from the games model** (above) — all config, so if the curated Contract pool ends up too small for ~1,000 completions the apex slides down without touching the shape.

## The ledger is the universal source of truth

`ContractXPGrant` is the immutable, source-agnostic job-XP ledger; `ProfileJobXP.total_xp = Sum(all grants)` and `level = total_xp // K + 1`. **Any** XP mechanic plugs in by writing ledger rows — it never touches the leveling math.

- **`grant_job_xp(profile, job, amount, *, source=, source_id=, tier=, base_t=, multiplier=, earned_contract=)`** — the single grant primitive (ledger row + row-locked cache bump + re-level). `accept_contract` routes through it; quests/events call it with `source='quest'`/`'event'`.
- **Generalized fields:** `source` (`contract`/`quest`/`event`/`manual`) + `source_id`; `earned_contract`/`tier`/`base_t` are null for non-contract grants.
- **Modifiers:** every grant records its `multiplier` + `base_t`, so it's auditable and displayable ("+1,000, 2× event"). `_active_multiplier()` is the double-XP hook (a stub returning `1.00` until an `XPEvent` model backs it).

## Calibration + deploy

- **`report_xp_economy [--t --k]`** — run on prod to read the per-element level/tier spread + the **job-feed imbalance** (the one thing the supply data is genuinely for: niche-genre elements stay low forever; popular ones soar). Sweep `--k` to compare economies.
- **Deploy (after the engine PR merges + migrates):** run **`recompute_job_xp --all`** — the ledger amounts are immutable; only the *level derivation* changes (escalating → flat), so every `ProfileJobXP.level` must be re-derived.

## Gotchas and Pitfalls

- **Niche elements never reach high tiers.** Flat + cap-less honestly reflects "you don't play card games" — Card-Shark may sit at Initiate forever. If that feels bad, the fix is a per-genre XP weighting (breaks the clean "6,000 splits evenly"), not a cap. Decide with the imbalance report.
- **`recompute_job_xp --all` is mandatory after deploy** — skipping it leaves levels computed under the old curve.
- **Tiers carry prestige, not the curve.** Don't be tempted to re-add curve escalation "to make high levels feel earned" — that breaks modifier fungibility. Use tier spacing instead.
- **The ledger is append-only; the cache is `Sum(ledger)`.** Any future "remove / decay / expire XP" feature MUST delete or write a negating ledger row, **never** just decrement `ProfileJobXP` — `recompute_job_xp` rebuilds from the ledger and would resurrect the removed XP.
- **`grant_job_xp` has NO built-in idempotency for non-contract sources.** Contracts are guarded by `unique_together(earned_contract, job, tier)` + the accepted timestamps. Quests/events (null `earned_contract`) are unconstrained — the first quest/event integration must own idempotency (e.g. `get_or_create` on `(profile, job, source, source_id)` or a partial unique index), or it will double-pay into the permanent ledger.
- **The model is named `ContractXPGrant` but is source-agnostic.** A rename to `JobXPGrant` is optional polish; the `source` field is what matters.
- **Lab display:** the cap-based "mastered" state is gone — the Lab shows the element's prestige **tier** (rank on the tile/detail + a "N to <next tier>" goal). Done.
