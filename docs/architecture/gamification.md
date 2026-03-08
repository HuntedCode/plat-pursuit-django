# Gamification (Implemented Systems)

The gamification layer tracks badge XP accumulation across the platform. Currently, only the Badge XP system is active: users earn XP by completing badge stages (concepts within a stage) and by earning full badges. This XP is denormalized on `ProfileGamification` for fast leaderboard queries. The infrastructure for future P.L.A.T.I.N.U.M. stats (Power, Luck, Agility, etc.) and Jobs exists in the schema but has no data populated yet. See [Gamification Vision](../design/gamification-vision.md) for the full RPG system design.

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
| `trophies/util_modules/constants.py` | XP constants: `BRONZE_STAGE_XP=250`, `SILVER_STAGE_XP=75`, `GOLD_STAGE_XP=250`, `PLAT_STAGE_XP=75`, `BADGE_TIER_XP=3000` |
| `trophies/models.py` | `ProfileGamification`, `StatType`, `StageStatValue` models |

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
- Designed for future P.L.A.T.I.N.U.M. stats (8 records: power, luck, agility, toughness, intelligence, navigation, utility, magic)
- Admin UI registered via `StageStatValueAdmin`

### StageStatValue (Schema exists, no data populated)
- FK to `Stage` + FK to `StatType`, unique together
- `bronze_value`, `silver_value`, `gold_value`, `platinum_value` (per-tier point values)
- Will drive the radar chart on the Explorer's Logbook once populated

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

## Integration Points

- [Token Keeper](token-keeper.md): Badge evaluation during sync triggers XP updates. Uses `bulk_gamification_update()` context manager.
- [Badge System](badge-system.md): `UserBadgeProgress` and `UserBadge` changes are the sole triggers for XP recalculation.
- [Notification System](notification-system.md): Badge XP is included in shareable card data via `get_badge_xp_for_game()`.
- [Gamification Vision](../design/gamification-vision.md): Full design for P.L.A.T.I.N.U.M. stats, Jobs, quests, and currency that will build on this foundation.

## Gotchas and Pitfalls

- **Full recalculation, not incremental**: `update_profile_gamification()` always recomputes from all `UserBadgeProgress` and `UserBadge` records. This is intentional for correctness but means each call does 2 database queries. The `bulk_gamification_update()` context manager exists specifically to batch these during sync.
- **Thread-local state**: The bulk update context uses `threading.local()`. This works because Django processes requests in separate threads. If the project ever moves to async workers, this pattern would need revisiting.
- **Signal ordering matters**: Both `update_badge_earned_count_on_save` and `update_gamification_on_badge_earned` fire on `UserBadge` post_save. The earned_count update uses `F()` expressions (race-safe), while the gamification update does a full recalc.
- **StageStatValue has no data**: The model exists and admin UI is registered, but no records have been populated. When the P.L.A.T.I.N.U.M. system goes live, this needs to be populated for all stages.
- **series_badge_xp is a JSONField**: It stores a Python dict serialized as JSON. Query filtering on individual series values requires JSON path queries or Python-side processing.

## Management Commands

| Command | Purpose | Usage |
|---------|---------|-------|
| `recalculate_gamification` | Recalculate XP for all profiles | `python manage.py recalculate_gamification` |

## Related Docs

- [Badge System](badge-system.md): How badges are evaluated and awarded
- [Token Keeper](token-keeper.md): Sync pipeline that triggers badge evaluation
- [Gamification Vision](../design/gamification-vision.md): Full RPG system design document
