# Gamification (Implemented Systems)

The gamification layer tracks XP accumulation across the platform. Two XP systems are active:

1. **Badge XP** (this doc, below): users earn XP by completing badge stages (concepts within a stage) and by earning full badges. Denormalized on `ProfileGamification` for fast leaderboard queries.
2. **Contract / job XP engine** (the "Element" system, see [Contract / Job XP Engine](#contract--job-xp-engine)): per-job XP banked when a user *accepts* a completed Contract. Decoupled from badges, with its own immutable ledger.

The legacy P.L.A.T.I.N.U.M. 8-stat system was **retired** (2026-06); the 5 job disciplines (Combat, Exploration, Mind, Heart, Finesse) are the sole characterization radar, derived from job levels. The `StatType` / `StageStatValue` schema it would have used remains in the DB but is **vestigial** (no data, not on any roadmap). See `docs/design/product-identity.md` for the committed model and `docs/design/rebuild/job-board-contracts.md` for the Contract architecture.

## Architecture Overview

Badge XP is calculated from two sources:

1. **Stage progress XP**: For each badge tier a user has progress in, they earn `completed_concepts * tier_xp` per stage. Bronze and Gold stages are worth 250 XP per concept. Silver and Platinum stages are worth 75 XP per concept.
2. **Badge completion bonus**: 3,000 XP per fully earned badge (any tier).

XP is recalculated and denormalized onto `ProfileGamification` via Django signals. When a `UserBadgeProgress` or `UserBadge` record changes, the signal handler calls `update_profile_gamification()` which recomputes everything from scratch (full recalculation, not incremental). During bulk operations (sync), the `bulk_gamification_update()` context manager defers signal handling to avoid N recalculations, processing all affected profiles once at the end.

## File Map

| File | Purpose |
|------|---------|
| `trophies/services/xp_service.py` | Central XP calculation and update logic (342 lines) |
| `trophies/signals.py` | Signal handlers for gamification updates (172 lines) |
| `trophies/util_modules/constants.py` | XP constants: `BRONZE_STAGE_XP=250`, `SILVER_STAGE_XP=75`, `GOLD_STAGE_XP=250`, `PLAT_STAGE_XP=75`, `BADGE_TIER_XP=3000`; Contract: `CONTRACT_XP_TOTAL=6000`, `CONTRACT_PLATINUM_FRAC=0.70`, `JOB_XP_PER_LEVEL=3000` (flat, cap-less) |
| `trophies/models.py` | `ProfileGamification`, `StatType`, `StageStatValue`; Contract engine: `EarnedContract`, `ContractXPGrant`, `ProfileJobXP` |
| `trophies/services/contract_service.py` | Contract XP engine: detection (`mark_contract_reached` / `check_profile_contracts`), acceptance (`accept_contract` / `accept_contracts`), `claimable_contracts`, `recompute_profile_job_xp` |
| `trophies/util_modules/leveling.py` | Per-job leveling curve (`xp_for_level` / `level_for_xp`) |

## Data Model

### ProfileGamification (Active)
- OneToOneField to Profile (primary key)
- `total_badge_xp` (PositiveIntegerField, indexed): total XP across all series
- `series_badge_xp` (JSONField): per-series XP breakdown as `{"series-slug": 1500, ...}`
- `total_badges_earned`: count of fully earned badges
- `unique_badges_earned`: count of distinct badge series with earned badges
- `last_updated`: auto-updated timestamp

### StatType (Schema exists, 1 record: "badge_xp")
- `slug` (PK), `name`, `description`, `icon`, `color`, `is_active`, `display_order`
- Was designed for the P.L.A.T.I.N.U.M. 8-stat system, now **retired** (2026-06). Vestigial: only the `badge_xp` record exists; the 8 stat records were never populated and won't be
- Admin UI registered via `StageStatValueAdmin`

### StageStatValue (Schema exists, no data populated)
- FK to `Stage` + FK to `StatType`, unique together
- `bronze_value`, `silver_value`, `gold_value`, `platinum_value` (per-tier point values)
- **Vestigial** (P.L.A.T.I.N.U.M. retired). The Logbook's discipline radar derives from job levels (`ProfileJobXP`), not from this table

## Key Flows

### XP Update on Badge Progress Change

1. User earns a trophy, triggering badge evaluation in sync pipeline
2. `UserBadgeProgress.completed_concepts` is updated
3. Django `post_save` signal fires `update_gamification_on_progress`
4. Signal checks `is_bulk_update_active()`:
   - If bulk active: calls `defer_profile_update(profile)` (adds profile to thread-local set)
   - If not bulk: calls `update_profile_gamification(profile)` directly
5. `update_profile_gamification()` recalculates from scratch via `calculate_total_xp()`
6. `ProfileGamification` is updated via `update_or_create`

### XP Update on Badge Earned/Revoked

1. `UserBadge` is created (earned) or deleted (revoked)
2. Two signals fire: `update_badge_earned_count_on_save/delete` (F() expression counter) and `update_gamification_on_badge_earned/revoked`
3. Same bulk-aware flow as above

### Bulk Gamification Update (During Sync)

1. Token Keeper wraps badge evaluation in `with bulk_gamification_update():`
2. Thread-local `_bulk_update_context.active = True`
3. All signal handlers detect bulk mode and call `defer_profile_update()` instead
4. Affected profiles accumulate in `_bulk_update_context.profiles` (a set, so deduped)
5. When context exits: each deferred profile gets a single `update_profile_gamification()` call
6. Thread-local state is cleaned up

### Full Recalculation (Admin)

1. `recalculate_all_gamification()` iterates all profiles with badge progress
2. Each profile gets `update_profile_gamification()` (chunked, 100 at a time)
3. Management command: `python manage.py recalculate_gamification`

## XP Constants

| Tier | Stage XP (per concept) | Badge Completion Bonus |
|------|----------------------|----------------------|
| Bronze (1) | 250 | 3,000 |
| Silver (2) | 75 | 3,000 |
| Gold (3) | 250 | 3,000 |
| Platinum (4) | 75 | 3,000 |

XP formula for a single badge: `(completed_concepts * tier_xp) + (earned ? 3000 : 0)`

Total XP: sum across all badge series.

## Contract / Job XP Engine

A second, badge-independent XP system. A **Contract** is keyed on a raw IGDB game id (`Contract.igdb_id`): every **anchored + trusted-matched** Concept (`anchor_migration_completed_at` set) whose `igdb_match.igdb_id` matches is a member automatically — usually one Concept, but same-entry multi-platform/regional siblings share the id. A `ContractBundle` (M2M, all-must-100%) handles the niche episodic case (individual trophy lists with different/no IGDB ids). It pays the same global total **T** (`CONTRACT_XP_TOTAL = 6000`, override per Contract via `xp_total_override`), split evenly among the Contract's assigned **jobs** (Elements, capped at `MAX_CONTRACT_JOBS = 6`). Each user banks that XP per job and levels each job independently; the headline **Pursuer Level** is the sum of all per-job levels.

> **Naming skin:** the backend models are `Job` / `Contract`; everything user-facing is **Element / Project / The Lab**. No user-visible text says "job" or "contract".

### Two-gate model (the acceptance mechanic)

| Gate | Trigger | Effect |
|------|---------|--------|
| **Reached** | Automatic on sync (`mark_contract_reached`) | Stamps `EarnedContract.*_reached_at`. Makes the reward *claimable*. **Grants no XP.** |
| **Accepted** | User action (`accept_contract`) | Writes the `ContractXPGrant` ledger, bumps the `ProfileJobXP` cache, stamps `*_accepted_at`. **This is the only path that grants XP.** |

A user must *accept* a completed Contract to bank its XP (forces engagement; there is no "start"). **One accept banks all of a Contract's claimable tiers at once** (Platinum + 100% together).

### Tiers

Two tiers per Contract: **Platinum** (`PLATINUM_FRAC = 0.70`, the bulk) and **100%** (the remainder). Games with no platinum trophy pay the **full T at 100%**. The 100% tier is computed as `grand_total - platinum_total` (not by rounding its fraction independently) so the two tiers always sum to exactly T, even for odd overrides.

### Data Model

- **`EarnedContract(profile, contract)`** — `unique_together(profile, contract)`. Holds the four gate timestamps + `has_platinum`, which is **frozen at first reach** (so a platinum added/removed between split accepts can't over/underpay the 100% tier).
- **`ContractXPGrant`** — the immutable ledger / source of truth: `(earned_contract, profile, job, tier, amount, base_t, multiplier, granted_at)`, `unique_together(earned_contract, job, tier)` for idempotency. **Never recomputed from current config.**
- **`ProfileJobXP`** — read cache: `unique_together(profile, job)` (~24 rows/user), `total_xp`, `level`. Bumped incrementally on accept under a row lock; rebuildable from `Sum(ContractXPGrant.amount)` grouped by `(profile, job)`.

### Leveling

`trophies/util_modules/leveling.py`: the curve is **flat + cap-less**, 1-based (level 1 = 0 XP, the floor every job starts at). `xp_for_level(L) = JOB_XP_PER_LEVEL * (L-1)` and `level_for_xp(xp) = xp // JOB_XP_PER_LEVEL + 1` (`JOB_XP_PER_LEVEL = 3000`); always >= 1, never clamps. Prestige tiers (`JOB_TIERS` + `tier_for_level`) carry the per-element milestone journey on top of the open-ended number. Pursuer Level = sum of every job's level (min 1 each); the account-wide **Pursuer rank** (`PURSUER_RANKS` + `pursuer_rank_for_level`) is a deep `Newbie -> Recruit V..I -> ... -> Ascendant` ladder off that sum, shown inline beside the Pursuer name in the Lab hero. Full rationale: [../design/rebuild/xp-economy.md](../design/rebuild/xp-economy.md).

### Sync seam

In `token_keeper.py`'s `sync_complete` (phase `stats_badges`, right after `check_profile_badges`), the engine derives the touched games' concept ids and calls `check_profile_contracts(profile, concept_ids)` → `mark_contract_reached` per affected Contract. **Detection only — no XP.** It runs outside `bulk_gamification_update()` (it writes no XP/signals) and is `try`-wrapped so a failure can't break the sync.

## Integration Points

- [Token Keeper](token-keeper.md): Badge evaluation during sync triggers XP updates. Uses `bulk_gamification_update()` context manager. The Contract engine's `check_profile_contracts` detection hook also runs here (detection only).
- [Badge System](badge-system.md): `UserBadgeProgress` and `UserBadge` changes are the sole triggers for XP recalculation.
- [Notification System](notification-system.md): Badge XP is included in shareable card data via `get_badge_xp_for_game()`.
- [Gamification Vision](../design/gamification-vision.md): Original RPG system design (note: the P.L.A.T.I.N.U.M. 8-stat layer is retired; jobs now live on the Contract layer per `job-board-contracts.md`).

## Gotchas and Pitfalls

- **Full recalculation, not incremental**: `update_profile_gamification()` always recomputes from all `UserBadgeProgress` and `UserBadge` records. This is intentional for correctness but means each call does 2 database queries. The `bulk_gamification_update()` context manager exists specifically to batch these during sync.
- **Thread-local state**: The bulk update context uses `threading.local()`. This works because Django processes requests in separate threads. If the project ever moves to async workers, this pattern would need revisiting.
- **Signal ordering matters**: Both `update_badge_earned_count_on_save` and `update_gamification_on_badge_earned` fire on `UserBadge` post_save. The earned_count update uses `F()` expressions (race-safe), while the gamification update does a full recalc.
- **StageStatValue / the 8 StatType records are vestigial**: the P.L.A.T.I.N.U.M. system they were built for is retired (2026-06). The discipline radar derives from job levels, not this table. Leave the schema in place (it's cheap) but don't build on it.
- **series_badge_xp is a JSONField**: It stores a Python dict serialized as JSON. Query filtering on individual series values requires JSON path queries or Python-side processing.

**Contract / job XP engine:**
- **The ledger is immutable, never recomputed from config**: `ContractXPGrant` rows record the amount *as paid* (with `base_t` + `multiplier`). Changing `CONTRACT_XP_TOTAL` or a Contract's jobs later does NOT retroactively change banked XP. `recompute_job_xp` rebuilds only the *cache* (`ProfileJobXP`) by re-summing the existing ledger; it never re-derives amounts.
- **`has_platinum` is frozen at first reach**: read it from `EarnedContract.has_platinum`, never recompute it live at accept time. Recomputing could over/underpay the 100% tier if a platinum is added/removed between split accepts.
- **Reached is a one-way ratchet; accept is once-per-tier**: a 100%'d game that later drops below 100% (new DLC) keeps its reached/accepted state and its banked XP. Re-detect + re-accept grants nothing more (idempotent via the accepted timestamps + the ledger `unique_together`). Surfaces that show "claimable" status must read `EarnedContract`, not live `ProfileGame.progress`.
- **Accept is required for XP**: detection on sync only makes a reward *claimable*. No XP exists until the user accepts. The Research Panel's Accept button (`POST /api/v1/projects/accept/` with `{slug}` for one or `{all: true}` for every claimable; `api/contract_views.py:AcceptContractView`) is the only grant path. The view is the sole request path that writes grants; it just delegates to `accept_contract` / `accept_contracts`.
- **Per-user reads must DB-aggregate**: `ProfileJobXP` is ~24 rows/user, but `recompute_profile_job_xp` and any leaderboard read must aggregate in the DB (`Sum` / `annotate`), never iterate the ledger in Python (whale-OOM rule).
- **When adding a model FK'd to Concept, update `Concept.absorb()`**: `EarnedContract` / `ContractXPGrant` / `ProfileJobXP` FK Profile/Job/Contract (not Concept directly), so they need no absorb branch. Contract home membership is DERIVED (a Contract keys on a raw IGDB id; there is no through-table), so absorb only migrates `ContractBundle.concepts` (episodic satisfier membership) + propagates the anchor stamp when inheriting an IGDBMatch.

## Management Commands

| Command | Purpose | Usage |
|---------|---------|-------|
| `recalculate_gamification` | Recalculate badge XP for all profiles | `python manage.py recalculate_gamification` |
| `recompute_job_xp` | Rebuild `ProfileJobXP` from the `ContractXPGrant` ledger | `python manage.py recompute_job_xp --user <psn_username>` or `--all` |

## Related Docs

- [Badge System](badge-system.md): How badges are evaluated and awarded
- [Token Keeper](token-keeper.md): Sync pipeline that triggers badge evaluation
- [Gamification Vision](../design/gamification-vision.md): Full RPG system design document
