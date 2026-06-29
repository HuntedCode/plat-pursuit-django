# Job Board & Contracts — Gamification Data Model

> **Status:** Built. The organizational spine for jobs + job XP, decoupled from badges.
> The **XP side** (T, the flat cap-less curve, K, prestige tiers, source-agnostic grants)
> is specified in **[xp-economy.md](xp-economy.md)**; this doc covers the engine mechanics
> (the reached/accepted gates, the T split, curation).

## Why this exists

Job XP must be earned **once per game**, while a game can sit in **many badges**. Tying
job XP to badge stages would double-pay (a game in a Series badge *and* a Developer badge).
So jobs live on their own layer:

- **Badges** stay exactly as they are (stages, tiers, badge XP) — they reference Concepts.
- **Contracts** are a new, **staff-curated** "Job Board" of games. Each Contract grants job
  XP once per user.
- The **Concept** is the shared atom both layers point at. Badges keep selecting Concepts at
  fine granularity (e.g. a "PS2 Ports" badge requires *only* the legit PS2-port Concept);
  Contracts group Concepts coarsely for job-XP dedup (all 5 J&D versions = one game).

The two granularities are independent **by design** — that's what makes the J&D edge case work.

## Data model

```
Job ──< Contract.jobs (M2M)            ProfileJobXP (cache: profile×job → total)
                 │                                  ▲
Concept ──1── ContractMembership ──> Contract       │ (sum of grants)
   │   (unique concept = one home)        │         │
   │                                      ├─ EarnedContract (profile×contract, once)
   └──< ContractBundle.concepts           │         │
        (collection-spanning satisfier)   └─────────┴─ ContractXPGrant (ledger)
```

### `Job` — the 24 specializations (static reference)
| field | notes |
|---|---|
| `slug`, `name` | e.g. `gunslinger` / "Gunslinger" |
| `category` | one of the **5 disciplines** below — drives the radar axes |
| `description`, `icon` | display |

Seeded from the locked catalog (24 + Freelancer fallback). Detection (`report_job_assignment`)
*suggests* a Contract's jobs from IGDB genres/themes; staff confirm/trim.

**The 24 jobs by discipline (5×5, + Freelancer).** Disciplines are named for *what the
player does* (a "discipline you specialize in"), not for content — Mind ↔ Heart are the
think/feel faculties:

| Discipline | Jobs |
|---|---|
| **Combat** | Slayer · Gunslinger · Vanguard · Outlaw · Warrior |
| **Exploration** | Pathfinder · Infiltrator · Cartographer · Mascot · Survivalist |
| **Mind** | Mastermind · Tactician · Architect · Tycoon · Card&Board *(name TBD)* |
| **Heart** | Mage · Champion · Librarian · Jester · Exorcist |
| **Finesse** | Gamer · Driver · Athlete · Maestro · Freelancer |

(`★combo` jobs — Vanguard = Shooter+Sci-fi, Mage = RPG+Fantasy, Mascot = Comedy+Platform —
override their base genre job. Freelancer is the no-specialization fallback, housed in Finesse.)

### `Contract` — a Job Board entry (the curated game)
| field | notes |
|---|---|
| `name`, `slug` | display + URL |
| `is_live` | curation gate (mirrors `Badge.is_live`); hidden until released |
| `jobs` | **M2M → Job** — the job profile; XP splits **evenly** across these |
| `xp_total_override` | nullable; default uses the global base `T`, override for specials |

- **`ContractMembership(contract, concept)`** — explicit through table, **`concept` UNIQUE**.
  This enforces the core invariant: *each Concept has at most one home Contract.* Direct
  members are the "version variants" (PS4/PS3/Vita/remaster) — completing **any one** = done.
- **`ContractBundle(contract, concepts M2M)`** — replicates `ConceptBundle` for the rare
  collection-spanning case (a collection list that covers several games which *also* have
  their own lists). Completing the bundle satisfies the Contract. The collection Concept is a
  *satisfier* of multiple Contracts, not a home (so it's exempt from the membership-unique rule).

### `EarnedContract` — per-user completion (idempotency + tier state)
| field | notes |
|---|---|
| `profile`, `contract` | **unique_together** — one per user per Contract |
| `platinum_at` | when the Platinum tier was reached (nullable) |
| `completed_at` | when 100% was reached (nullable) |

Existence + tier timestamps make grants idempotent (we never pay the same tier twice).

### `ContractXPGrant` — the XP ledger (source of truth)
| field | notes |
|---|---|
| `earned_contract` | FK |
| `profile`, `job` | **denormalized** so per-job totals SUM in the DB (whale-OOM rule) |
| `tier` | `platinum` \| `full` |
| `amount` | the **actual** XP granted |
| `base_t`, `multiplier` | the inputs at grant time (e.g. 2× weekend) |
| `granted_at` | timestamp |

One row per (job × tier) per earn. **Never recompute history** — value changes and double-XP
weekends are captured here permanently, and a reversal subtracts the *recorded* amount.

### `ProfileJobXP` — the read cache
| field | notes |
|---|---|
| `profile`, `job` | unique_together (~24 rows/user) |
| `total_xp`, `level` | running total + derived level |

Updated **incrementally** on each grant; repairable via `Sum(ContractXPGrant.amount)` grouped
by (profile, job) — **DB aggregation, never Python iteration**. Mirrors the existing
`ProfileGamification.total_badge_xp` denorm pattern.

## Completion & XP flow

1. Trophy sync updates a user's per-game completion (existing Platinum / 100% data).
2. `handle_contract(profile, contract)` (parallels `handle_badge`) checks: is any direct
   member completed, or any bundle satisfied, at Platinum? at 100%?
3. On a **newly reached tier**, compute and grant:
   - `N = contract.jobs.count()`
   - Platinum grant per job = `round(platinum_frac × T / N)` (remainder distributed)
   - 100% grant per job = `round(full_frac × T / N)`
   - **No-platinum games**: 100% pays the **full** `T / N` (nothing before 100%).
   - Apply the active `multiplier`; write `ContractXPGrant` rows + bump `ProfileJobXP`.
4. `EarnedContract` records the tier so it never re-grants.

**Invariants**
- Each Concept → at most one home Contract (`ContractMembership.concept` unique).
- Each (profile, Contract, tier) granted **at most once**.
- Every Contract is worth the same total `T` (split among its jobs) unless overridden.

## Coverage audit (keeps the pool ⊇ badge games)

A command (sibling of `audit_badge_coverage`): every Concept referenced by a live Badge stage
must be a `ContractMembership` of some Contract. Anything missing is flagged (or dropped into a
solo Contract for staff to merge), so "every badge game is on the Job Board" stays true without
double-authoring.

## Deferred / open

- **XP numbers + curve + tiers**: DONE — see [xp-economy.md](xp-economy.md) (T=6,000, flat
  cap-less K=3,000, prestige tiers). Exact-number *calibration* remains an open thread.
- **Pursuer rank** (profile-wide, distinct from element tiers) — data-calibrated fast-follow.
- **"Badge XP" rename** (Renown / Prestige / Acclaim) — pending the team.
- **Card & Board job name** — one word (Gambit / Knave / …).
- **Even split only** for v1; dominance-weighting is a possible later refinement.

## Gotchas & Pitfalls

- **Per-job totals MUST aggregate in the DB.** Never iterate a whale's `ContractXPGrant`/
  `EarnedContract` rows in Python to total job XP — `Sum().values('job')`. (The May 2026 OOM rule.)
- **Don't recompute granted XP from the Contract's *current* config.** Read the ledger. A
  Contract that changes its jobs or `T` later must not retroactively rewrite past grants.
- **The membership-unique rule is what guarantees "once per game."** A Concept slipping into two
  Contracts' direct members would double-pay. Enforce at the DB (unique) + validate in admin.
- **Collection satisfiers are the documented exception** to membership-unique — they live in
  `ContractBundle`, not `ContractMembership`, precisely so they can satisfy several Contracts
  without being a "home" of any.
- **Tiers accrue across time.** Platinum (maybe during a 2× weekend) and 100% (a week later,
  normal rate) are separate grants, each locked at its own moment's `base_t` × `multiplier`.
- **Curation, not a cap, controls job count.** Detection may suggest 6 jobs; staff trim to the
  ones the game is genuinely about so each job's XP stays meaningful.
