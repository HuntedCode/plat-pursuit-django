# Gamification (Implemented Systems)

The gamification layer tracks XP accumulation across the platform. Two XP systems are active:

1. **Badge XP** (see [badge-system.md](badge-system.md)): earned by clearing gating stages and completing group badges. Materialized on `ProfileBadgeStanding` / `SeriesBadgeStanding` / `SeriesEditionStanding` / `ProfileEditionStanding`.
2. **Contract / job XP engine** (below): per-job XP banked when a user *accepts* a completed Contract. Decoupled from badges, with its own immutable ledger.

The two are SEALED from each other on purpose. Badge XP measures curated-set completion; job XP measures
what a hunter plays. They share no table, no constant and no recompute path, so recalibrating one cannot
move the other.

The legacy P.L.A.T.I.N.U.M. 8-stat system was **retired** (2026-06); the 5 job disciplines (Combat, Exploration, Mind, Heart, Finesse) are the sole characterization radar, derived from job levels. The `StatType` / `StageStatValue` schema it would have used remains in the DB but is **vestigial** (no data, not on any roadmap). See `docs/design/product-identity.md` for the committed model and `docs/design/rebuild/job-board-contracts.md` for the Contract architecture.

## Badge XP

Documented in full in [badge-system.md](badge-system.md#xp). In brief: `XP_PER_STAGE = 500` per gating
stage cleared plus a flat `XP_BADGE_COMPLETION_BONUS = 600` when the badge is earned, accruing PER
EDITION and recomputed from scratch by `badge_xp.recompute_standing` on every evaluation.

> **Removed 2026-08 (badge cutover 5b.4).** `ProfileGamification`, `xp_service`, the gamification
> signals, `recalculate_gamification` and the `bulk_gamification_update` context manager are all gone.
> They served the tier engine: XP was recomputed by Django signals on `UserBadge` / `UserBadgeProgress`
> writes, denormalized onto `ProfileGamification`, and mirrored into Redis sorted sets by four separate
> incremental writers.
>
> The `ProfileGamification` TABLE is retained (rollback + audit) but nothing reads or writes it, and its
> `total_badge_xp` is NOT the current figure -- it is a frozen tier-economy number. Never show it beside
> `ProfileBadgeStanding.total_xp`; they count different things.
>
> What replaced the machinery is the absence of it: standings are recomputed from scratch from the
> evaluation's own result, so there is no incremental write to drift, nothing to reconcile, and no bulk
> deferral context needed to stop N recalculations during a sync.

## File Map

| File | Purpose |
|------|---------|
| `trophies/util_modules/constants.py` | Contract: `CONTRACT_XP_TOTAL=6000`, `CONTRACT_PLATINUM_FRAC=0.70`, `JOB_XP_PER_LEVEL=3000` (flat, cap-less) |
| `trophies/models.py` | Contract engine: `EarnedContract`, `ContractXPGrant`, `ProfileJobXP`; vestigial `StatType`, `StageStatValue` |
| `trophies/services/contract_service.py` | Contract XP engine: detection (`mark_contract_reached` / `check_profile_contracts`), acceptance (`accept_contract` / `accept_contracts`), `claimable_contracts`, `recompute_profile_job_xp` |
| `trophies/util_modules/leveling.py` | Per-job leveling curve (`xp_for_level` / `level_for_xp`) |
| `trophies/services/badge_xp.py` | Badge XP (the other economy) -- see [badge-system.md](badge-system.md) |

## Data Model

### StatType (Schema exists, 1 record: "badge_xp")
- `slug` (PK), `name`, `description`, `icon`, `color`, `is_active`, `display_order`
- Was designed for the P.L.A.T.I.N.U.M. 8-stat system, now **retired** (2026-06). Vestigial: only the `badge_xp` record exists; the 8 stat records were never populated and won't be
- Admin UI registered via `StageStatValueAdmin`

### StageStatValue (Schema exists, no data populated)
- FK to `Stage` + FK to `StatType`, unique together
- `bronze_value`, `silver_value`, `gold_value`, `platinum_value` (per-tier point values)
- **Vestigial** (P.L.A.T.I.N.U.M. retired). The Logbook's discipline radar derives from job levels (`ProfileJobXP`), not from this table

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

In `token_keeper.py`'s `sync_complete` (phase `stats_badges`, right after `evaluate_for_sync`), the engine derives the touched games' concept ids and calls `check_profile_contracts(profile, concept_ids)` → `mark_contract_reached` per affected Contract. **Detection only — no XP.** It is `try`-wrapped so a failure can't break the sync.

## Integration Points

- [Token Keeper](token-keeper.md): badge evaluation during sync recomputes badge standings. The Contract engine's `check_profile_contracts` detection hook also runs here (detection only).
- [Badge System](badge-system.md): Badge XP is recomputed by `badge_xp.recompute_standing` on every evaluation, from scratch. There are no XP signals: `UserBadgeProgress` / `UserBadge` no longer trigger anything.
- [Notification System](notification-system.md): Shareable cards read the standing tables directly; `get_badge_xp_for_game()` died with `xp_service`.
- Gamification Vision (the gamification-vision doc was never written; see design/gamification-plan.md): Original RPG system design (note: the P.L.A.T.I.N.U.M. 8-stat layer is retired; jobs now live on the Contract layer per `job-board-contracts.md`).

## Gotchas and Pitfalls

- **`ProfileGamification.total_badge_xp` is a FROZEN legacy number.** The table survives for rollback and
  audit; nothing writes it. It counts the retired tier economy, so it is not comparable to
  `ProfileBadgeStanding.total_xp` and the two must never be shown as the same figure.
- **StageStatValue / the 8 StatType records are vestigial**: the P.L.A.T.I.N.U.M. system they were built for is retired (2026-06). The discipline radar derives from job levels, not this table. Leave the schema in place (it's cheap) but don't build on it.

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
| `recompute_job_xp` | Rebuild `ProfileJobXP` from the `ContractXPGrant` ledger | `python manage.py recompute_job_xp --user <psn_username>` or `--all` |

## Related Docs

- [Badge System](badge-system.md): How badges are evaluated and awarded
- [Token Keeper](token-keeper.md): Sync pipeline that triggers badge evaluation
- Gamification Vision (the gamification-vision doc was never written; see design/gamification-plan.md): Full RPG system design document
