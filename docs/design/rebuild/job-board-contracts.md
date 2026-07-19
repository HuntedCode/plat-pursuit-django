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
- **Contracts** are a **staff-curated** "Job Board" of games. Each Contract grants job
  XP once per user.
- The **Concept** is the shared atom both layers point at. Badges keep selecting Concepts at
  fine granularity (e.g. a "PS2 Ports" badge requires *only* the legit PS2-port Concept);
  Contracts group Concepts coarsely for job-XP dedup.

## Membership is DERIVED from the IGDB id (no through-table)

A Contract is **keyed on a raw IGDB game id** (`Contract.igdb_id`). Its member concepts are
not stored — they are **derived**: a Concept is a member iff it is

1. **anchored** (`anchor_migration_completed_at` set — resolved to a specific IGDB version), **and**
2. **trusted-matched** (`igdb_match.status ∈ IGDBMatch.TRUSTED_STATUSES`), **and**
3. its `igdb_match.igdb_id` **equals** the Contract's `igdb_id`.

Usually that's one Concept per Contract; concepts that share a raw IGDB id (same-entry
multi-platform/regional siblings) fall under the same Contract automatically. A separate-entry
remaster has its **own** IGDB id, so it is simply its own Contract — there is no cross-credit.

`Contract.member_concept_ids()` implements the derivation (returns `[]` when `igdb_id` is None).
The gate is **anchored AND trusted** everywhere membership is read (the board's per-user
subqueries, the browse filters, the coverage audit, the sync detection hook).

## Data model

```
Job ──< Contract.jobs (M2M)            ProfileJobXP (cache: profile×job → total)
                 │                                  ▲
Concept.igdb_match.igdb_id ══derives══> Contract     │ (sum of grants)
   │  (anchored + trusted, == Contract.igdb_id)  │   │
   │                                      ├─ EarnedContract (profile×contract, once)
   └──< ContractBundle.concepts           │        │
        (episodic satisfier, M2M)         └────────┴─ ContractXPGrant (ledger)
```

### `Job` — the 24 specializations (static reference)
| field | notes |
|---|---|
| `slug`, `name` | e.g. `gunslinger` / "Gunslinger" |
| `discipline` | one of the **5 disciplines** below — drives the radar axes |
| `description`, `icon` | display |

Seeded from the locked catalog (24 + Freelancer fallback). Detection (`suggest_jobs_for_contract`)
*suggests* a Contract's jobs from IGDB genres/themes; staff confirm/trim. Suggestions are trimmed
to **at most `MAX_CONTRACT_JOBS` (6)** by signal strength (combos > genre > theme/partition, then
catalog order) — see `job_detection.top_jobs`.

**The 24 jobs by discipline (5×5, + Freelancer).** Disciplines are named for *what the
player does*, not for content — Mind ↔ Heart are the think/feel faculties:

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
| `name`, `slug` | display + URL. `name` = the member Concept's `unified_title` (the IGDB-canonical game name) at creation |
| `igdb_id` | **the raw IGDB game id this Contract keys on.** `IntegerField(null=True, unique=True)` — nullable+unique so episodic (bundle-only) contracts can exist with no id |
| `is_live` | curation gate (mirrors `Badge.is_live`); hidden until released |
| `jobs` | **M2M → Job** — the job profile (≤ 6); XP splits **evenly** across these |
| `xp_total_override` | nullable; default uses the global base `T`, override for specials |

- **Home membership is derived** (see above) — there is no through-table. "Each game has at most
  one home Contract" is guaranteed by the **unique `igdb_id`** on Contract.
- **`ContractBundle(contract, concepts M2M)`** — retained **only** for the **episodic** case
  (Telltale-style: episodes with individual trophy lists and different/no IGDB ids that should
  count as one). Completing the bundle satisfies the Contract. Bundle concepts do **not** need to
  be anchored/trusted or share the Contract's `igdb_id`.

### `EarnedContract` — per-user completion (idempotency + tier state)
| field | notes |
|---|---|
| `profile`, `contract` | **unique_together** — one per user per Contract |
| `platinum_reached_at` / `full_reached_at` | when each tier was reached (nullable) |
| `platinum_accepted_at` / `full_accepted_at` | when each tier's XP was banked (nullable) |
| `has_platinum` | frozen at first reach — drives the tier split |

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
2. `check_profile_contracts(profile, concepts)` resolves the affected live Contracts by the
   completed concepts' trusted IGDB ids (∪ any bundle Contracts those concepts satisfy), then
   `mark_contract_reached` checks: is any member completed (via `member_concept_ids()`), or any
   bundle satisfied, at Platinum? at 100%?
3. On a **newly reached tier**, compute and grant:
   - `N = contract.jobs.count()`
   - Platinum grant per job = `round(platinum_frac × T / N)` (remainder distributed)
   - 100% grant per job = `round(full_frac × T / N)`
   - **No-platinum games**: 100% pays the **full** `T / N` (nothing before 100%).
   - Apply the active `multiplier`; write `ContractXPGrant` rows + bump `ProfileJobXP`.
4. `EarnedContract` records the tier so it never re-grants. **Accept is required for XP** —
   detection only makes a reward claimable (`POST /api/v1/projects/accept/`).

**Invariants**
- Each game → at most one home Contract (unique `Contract.igdb_id`).
- Each (profile, Contract, tier) granted **at most once**.
- Every Contract is worth the same total `T` (split among its ≤6 jobs) unless overridden.

## Creating Contracts (admin)

- **`StageAdmin.convert_to_contract`** — for each **anchored** concept in the selected stages,
  resolve its trusted `igdb_id`; **skip** if a Contract with that id already exists (deduped
  within the run too) and skip un-anchored / no-match concepts (both reported). Otherwise create
  `Contract(igdb_id=…, name=concept.unified_title, slug=…)` and seed `suggest_jobs_for_contract`.
  A stage's `concept_bundles` (episodic) carry over to a single `igdb_id=None` Contract (deduped
  by slug). Only anchored concepts create contracts.
- **`ContractAdminForm.clean_jobs`** rejects more than `MAX_CONTRACT_JOBS` (6).

## Coverage audit (keeps the pool ⊇ badge games)

`audit_job_board_coverage`: every anchored + trusted Concept referenced by a live Badge stage
should have its `igdb_id` key a Contract (or be a bundle member). Anything missing is flagged so
"every badge game is on the Job Board" stays true without double-authoring.

## `absorb()` on Concept merge

Home membership is derived, so a merge has **no membership rows to re-point**. `absorb()` only:
- migrates `ContractBundle.concepts` (episodic satisfier membership, deduped), and
- **propagates the anchor stamp** — when the survivor inherits `other`'s IGDBMatch (the target had
  none), it also copies `other.anchor_migration_completed_at` if the survivor lacked one, so the
  survivor immediately keys the same Contract the merged concept did.

## Deferred / open

- **FOLLOW-UP — bundle-only (episodic) contracts must render like any other contract everywhere**
  (decided 2026-07-19). Today a Contract with `igdb_id=None` (episodic, members come only from
  `ContractBundle.concepts`) has **no igdb-derived members**, so the board shows it with
  `game_count=0`, no cover, and a possibly-wrong tier split. This carried over faithfully from the
  old membership model (which was home-members-only too), but the desired behavior is: an
  episodic/bundle-only contract shows its **bundle games** and its real completion/tier state
  everywhere a normal contract does. Touch points to union the bundle concepts' games (in addition
  to the igdb-derived members) — all in `contracts_service.py` unless noted:
  - `_member_games` / `_member_games_by_igdb` — surface bundle games (cover + `game_count`).
  - `annotated_contracts`: `member_pg` (drives `max_progress` / `any_plat` → board progress) and
    `defines_plat` (the tier split) — mirror `contract_service._has_platinum`, which already unions
    `contract.bundles`.
  - `_platform_exists` (platform filter) and `_filter_contracts` search (member-game titles).
  - **Reverse direction** (game → its contract): `contract_by_concept_map` (`contract_service.py`)
    resolves only via igdb; a bundle-episode game won't find its contract, so badge/game/pursuer
    cards omit the contract hook for episode games. Decide whether episode games should show the
    contract, and if so add a bundle path to the map.
  Simplest shape: a small `contract_member_games_qs(contract)` / batch variant that returns
  `igdb-derived ∪ bundle` games, used by all the board readers; and a bundle branch in
  `contract_by_concept_map` for the reverse lookup.
- **XP numbers + curve + tiers**: DONE — see [xp-economy.md](xp-economy.md) (T=6,000, flat
  cap-less K=3,000, prestige tiers). Exact-number *calibration* is CLOSED (keeping current values).
- **Pursuer rank** (profile-wide, distinct from job tiers) — built.
- **Even split only** for v1; dominance-weighting is a possible later refinement.

## Gotchas & Pitfalls

- **Membership is a derived query, not a stored relation.** You cannot `prefetch_related` it or
  join a through-table. Read it via `member_concept_ids()` / `contract_by_concept_map()`, or in
  ORM subqueries via `concept__igdb_match__igdb_id=<contract.igdb_id>` **plus** the anchored +
  trusted gate. Forgetting the trusted gate lets a `pending_review`/`rejected` match leak in.
- **Only ANCHORED + TRUSTED concepts are members.** A concept with a raw IGDB id but no anchor
  stamp (or a non-trusted match) is *not* a member even if the id matches — the gate is all three.
- **Per-job totals MUST aggregate in the DB.** Never iterate a whale's `ContractXPGrant`/
  `EarnedContract` rows in Python to total job XP — `Sum().values('job')`. (The May 2026 OOM rule.)
- **Don't recompute granted XP from the Contract's *current* config.** Read the ledger. A
  Contract that changes its jobs or `T` later must not retroactively rewrite past grants.
- **Unique `igdb_id` is what guarantees "once per game."** Two Contracts can't share an IGDB id,
  so a game's completion reaches exactly one home Contract.
- **Episodic bundles are the documented exception** — they satisfy a Contract without sharing its
  IGDB id (and don't need anchoring), which is why `ContractBundle` still exists. **Known gap
  (see Deferred):** the board readers currently derive member games/tiers from the igdb key only,
  so a bundle-ONLY contract renders with no games — union `contract.bundles` concepts when fixing.
- **Job count is HARD-capped at 6.** Detection may surface more; `top_jobs` trims to the 6
  strongest by signal, and the admin form rejects manual sets over 6.
- **Tiers accrue across time.** Platinum (maybe during a 2× weekend) and 100% (a week later,
  normal rate) are separate grants, each locked at its own moment's `base_t` × `multiplier`.
