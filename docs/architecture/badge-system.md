# Badge System

The Badge System is the core gamification layer of Platinum Pursuit, rewarding users for completing curated sets of PlayStation games organized into badge series. Each series tracks a theme (franchise, developer, or curated collection) and offers up to four tiers of difficulty. Completing stages within a series earns XP, and completing all required stages for a tier awards the badge itself along with a 3,000 XP bonus. The system also includes a parallel Milestone subsystem that rewards cumulative platform-wide achievements (platinum counts, playtime, community engagement, and more) using a pluggable handler architecture. Both badges and milestones integrate with Discord role assignments, in-app notifications, and a leaderboard system backed by periodic cache refreshes.

## Architecture Overview

### Badge Series and Tiers

A **badge series** is identified by a `series_slug` (e.g., `god-of-war`, `resident-evil`). Each series contains up to four `Badge` rows, one per tier:

| Tier | Name     | Stage XP per Concept | Completion Criterion |
|------|----------|---------------------|----------------------|
| 1    | Bronze   | 250 XP              | Platinum the game    |
| 2    | Silver   | 75 XP               | 100% completion      |
| 3    | Gold     | 250 XP              | Platinum the game    |
| 4    | Platinum | 75 XP               | 100% completion      |

Tiers are sequential prerequisites: you cannot earn Gold without first earning Silver, which requires Bronze. This is enforced by `_check_prerequisite_tier()`.

Odd tiers (1, 3) check for `has_plat=True` on ProfileGame. Even tiers (2, 4) check for `progress=100`. This alternating pattern applies to `series`, `collection`, and `developer` badge types. Megamix badges always use platinum checks regardless of tier.

### Stages and Concepts

**Stages** are the building blocks. Each Stage belongs to a `series_slug` and contains one or more **Concepts** via an M2M relationship. A Concept represents an abstract game identity (cross-platform), and each Concept links to one or more **Games**. Stage completion means the user has a qualifying ProfileGame (plat or 100%) for at least one Game within at least one of the Stage's Concepts.

Stage 0 is special: it is always skipped during badge evaluation and serves as an optional/tangential grouping (e.g., "bonus" games that count for XP but not for badge completion).

The `required_tiers` ArrayField on Stage controls tier-specific visibility. An empty array means the stage applies to all tiers. A value like `[3, 4]` means the stage only counts toward Gold and Platinum tiers.

### Badge Types

- **series**: A franchise badge (e.g., God of War). All non-zero stages must be completed.
- **collection**: A themed collection across franchises. Same logic as series.
- **developer**: Groups games by studio. Same logic as series.
- **megamix**: Flexible completion. Can use `requires_all` (complete everything) or `min_required` (complete N of M stages). Always uses platinum checks.
- **misc**: Admin-awarded only. Never evaluated automatically.

### XP System

XP is calculated from two sources:

1. **Progress XP**: `completed_concepts * tier_xp` for each badge tier in the user's progress. Tier XP values are defined in `trophies/util_modules/constants.py`.
2. **Badge Completion Bonus**: 3,000 XP per fully earned badge (the `BADGE_TIER_XP` constant).

XP is denormalized into `ProfileGamification` (one row per profile) with both a total and a per-series JSONField breakdown. This denormalized data is updated in real-time via Django signals and powers the XP leaderboard without expensive recalculation on every page load.

## File Map

| File | Purpose |
|------|---------|
| `trophies/models.py` | Badge, Stage, UserBadge, UserBadgeProgress, ProfileGamification, StatType, StageStatValue, Milestone, UserMilestone, UserMilestoneProgress, Title, UserTitle model definitions |
| `trophies/managers.py` | BadgeManager, BadgeQuerySet, MilestoneManager, MilestoneQuerySet with custom filter methods |
| `trophies/services/badge_service.py` | Core badge evaluation, awarding, revocation, Discord role management, and batch checking |
| `trophies/services/xp_service.py` | XP calculation, ProfileGamification updates, and bulk update context manager |
| `trophies/services/leaderboard_service.py` | Leaderboard computation: earners, progress, total progress, XP rankings, community XP |
| `trophies/services/milestone_service.py` | Milestone checking, awarding, and batch processing with notification consolidation |
| `trophies/milestone_handlers.py` | Pluggable handler registry: one function per `criteria_type` (plat_count, trophy_count, etc.) |
| `trophies/milestone_constants.py` | Shared constants: MONTH_MAP, ONE_OFF_TYPES, MILESTONE_CATEGORIES, CRITERIA_TYPE_DISPLAY_NAMES |
| `trophies/signals.py` | Django signal handlers: earned_count updates, gamification recalculation, stage icon auto-population |
| `trophies/util_modules/constants.py` | XP constants: BRONZE_STAGE_XP (250), SILVER_STAGE_XP (75), GOLD_STAGE_XP (250), PLAT_STAGE_XP (75), BADGE_TIER_XP (3000) |
| `trophies/token_keeper.py` | Sync pipeline: calls `check_profile_badges()` and `check_all_milestones_for_user()` after game sync |
| `core/management/commands/update_leaderboards.py` | Cron command to compute and cache all leaderboard data |
| `trophies/management/commands/` | Various badge/milestone management commands (see Management Commands section) |

## Data Model

### Badge

The central model. Each row represents one tier of one badge series.

- `series_slug`: Groups tiers together. All tiers of "God of War" share the same slug.
- `tier`: 1-4 (Bronze through Platinum). Sequential prerequisite chain.
- `badge_type`: Determines evaluation logic (series/collection/developer/megamix/misc).
- `base_badge`: FK to Tier 1 badge. Higher tiers can inherit display properties (image, title, description) from their base badge via `effective_*` properties.
- `requires_all` / `min_required`: Megamix flexibility. When `requires_all=False`, only `min_required` stages need completion.
- `is_live`: Visibility flag. New badges start hidden until released.
- `earned_count`: Denormalized count, updated via signals on UserBadge create/delete using `F()` expressions.
- `required_stages`: Denormalized count of applicable stages, updated by `update_required()`.
- `funded_by`: FK to Profile, tracking badge artwork donors from the fundraiser system.
- `discord_role_id`: Optional Discord role assigned/removed when badge is earned/revoked.
- `title`: FK to Title, creating a UserTitle when the badge is awarded.

### Stage

- `series_slug`: Links to the badge series (not a FK; matched by slug).
- `stage_number`: Position within the series. 0 = optional (skipped during evaluation).
- `concepts`: M2M to Concept. A stage is "complete" when any Game under any linked Concept meets the tier's criterion.
- `required_tiers`: PostgreSQL ArrayField. Empty = all tiers. `[1, 2]` = only Bronze and Silver.
- `stage_icon`: Auto-populated from the first Concept's icon via the `auto_populate_stage_icon` signal.

### UserBadge

Join table: Profile + Badge. `unique_together` constraint prevents duplicates.
- `earned_at`: Auto-set on creation.
- `is_displayed`: User-selectable display badge for their profile.

### UserBadgeProgress

Tracks incremental progress per profile per badge.
- `completed_concepts`: Count of completed stages for this badge tier.
- `last_checked`: Timestamp of last evaluation.

### ProfileGamification

Denormalized XP summary (OneToOneField to Profile).
- `total_badge_xp`: Sum of all progress XP + badge completion bonuses.
- `series_badge_xp`: JSONField mapping `series_slug` to per-series XP totals.
- `total_badges_earned`: Count of all earned badge tiers.
- `unique_badges_earned`: Count of distinct series where at least one tier is earned.

### StatType and StageStatValue

Future-proofing models for the P.L.A.T.I.N.U.M. gamification system. `StatType` defines stat categories (currently just `badge_xp`). `StageStatValue` allows per-stage, per-tier stat grants. These are not yet actively used in badge evaluation (XP still uses the constant-based tier map) but the schema is ready for expansion.

### Milestone

Platform-wide achievement definitions.
- `criteria_type`: Determines which handler evaluates progress (e.g., `plat_count`, `trophy_count`, `badge_count`).
- `criteria_details`: JSONField with handler-specific config, typically `{"target": N}`.
- `required_value`: Denormalized from `criteria_details.target` on every save.
- `premium_only`: Some milestones (subscription loyalty) are restricted to premium users.
- `title` / `discord_role_id`: Same reward pattern as badges.

### UserMilestone / UserMilestoneProgress

Same pattern as badge equivalents. `UserMilestone` records the award, `UserMilestoneProgress` tracks current value vs. target.

### Relationship Diagram

```
Badge (series_slug, tier)
  |-- base_badge --> Badge (tier 1)
  |-- title --> Title
  |-- funded_by --> Profile
  |-- earned_by: UserBadge --> Profile
  |-- progress_for: UserBadgeProgress --> Profile
  |
  +-- series_slug links to Stage.series_slug
        |-- concepts --> Concept (M2M)
              |-- games --> Game
                    |-- ProfileGame --> Profile (has_plat, progress)

ProfileGamification (1:1 Profile)
  |-- total_badge_xp, series_badge_xp, total_badges_earned

Milestone
  |-- criteria_type --> MILESTONE_HANDLERS registry
  |-- UserMilestone --> Profile
  |-- UserMilestoneProgress --> Profile
```

## Key Flows

### Badge Evaluation Flow

The primary evaluation path runs during PSN sync completion:

1. **Sync triggers** (`token_keeper.py: _job_sync_complete`): After trophies and profile games are updated, `check_profile_badges(profile, touched_profilegame_ids)` is called.

2. **Scope reduction**: The service resolves which badge series could be affected:
   - ProfileGame IDs -> Game concept_ids -> Stages containing those concepts -> series_slugs -> Badges with those slugs.
   - Only `is_live=True` badges are evaluated. Results are ordered by tier (ascending) so prerequisites are checked first.

3. **Context pre-fetch** (`_build_badge_context`): A single batch query gathers:
   - All earned badge IDs for this profile (avoids per-badge existence checks).
   - A `badges_by_key` dict keyed by `(series_slug, tier)` for prerequisite lookups.
   - All stage data: `series_slug -> [(stage_number, required_tiers, game_ids)]`.
   - Sets of `plat_game_ids` and `complete_game_ids` for the profile.

   This context turns what would be O(2B) queries (2 per badge for stage completion + profile game checks) into O(0) during iteration.

4. **Bulk gamification wrapper**: All badge evaluations run inside `bulk_gamification_update()`, which defers ProfileGamification recalculation until the entire batch is done (one recalc instead of N).

5. **Per-badge evaluation** (`handle_badge`):
   - **Prerequisite check**: Tier > 1 must have the previous tier earned. Uses context for fast lookups.
   - **Stage completion**: Uses `_get_stage_completion_from_cache()` to check each stage against pre-fetched plat/complete game ID sets. Stage 0 is always skipped.
   - **Completion logic**:
     - Series/collection/developer: ALL non-zero stages must be complete.
     - Megamix with `requires_all=True`: ALL non-zero stages must be complete.
     - Megamix with `requires_all=False`: At least `min_required` stages must be complete.
   - **Progress update**: `_update_badge_progress()` writes `completed_concepts` count to UserBadgeProgress.
   - **Award/revoke**: `_process_badge_award_revoke()` creates or deletes UserBadge, updates context in-place so subsequent tier checks see the new state.
   - **Discord**: Role assignment/removal happens via `transaction.on_commit()` to avoid holding DB connections during HTTP calls.

6. **Post-evaluation**: Deferred badge notifications are consolidated by the `DeferredNotificationService` (highest tier only per series, preventing spam).

### Initial Badge Check

`initial_badge_check(profile)` is used for first-time syncs or full recalculations. It differs from the incremental path:
- Checks ALL ProfileGames, not just recently updated ones.
- Collects all newly earned badges with Discord roles and sends a single batch notification.
- Uses `add_role_only=True` during iteration (suppresses individual Discord messages).

### XP Calculation

XP follows a two-component formula:

```
Total XP = Sum(completed_concepts * tier_xp for each badge/tier)
         + Sum(BADGE_TIER_XP for each fully earned badge)
```

Where tier XP values are:
- Bronze (tier 1): 250 XP per concept
- Silver (tier 2): 75 XP per concept
- Gold (tier 3): 250 XP per concept
- Platinum (tier 4): 75 XP per concept
- Badge completion bonus: 3,000 XP per earned badge

The asymmetry (250/75/250/75) is intentional. Bronze and Gold check for platinums (harder), while Silver and Platinum check for 100% completion (also hard, but different). Both paths are rewarded, but the platinum-check tiers get more XP per stage.

**Update triggers**: ProfileGamification is recalculated via Django signals:
- `UserBadgeProgress` saved: progress XP changed.
- `UserBadge` created: badge completion bonus added.
- `UserBadge` deleted: badge completion bonus removed.

During sync (bulk operations), the `bulk_gamification_update()` context manager collects affected profiles in a thread-local set and runs one `update_profile_gamification()` call per profile after the context exits.

### Milestone System

Milestones use a **handler registry pattern**:

1. `milestone_handlers.py` defines a `MILESTONE_HANDLERS` dict populated by the `@register_handler(criteria_type)` decorator.

2. Each handler receives `(profile, milestone, _cache=None)` and returns `{'achieved': bool, 'progress': int}`.

3. Handlers use the `_cache` dict for value reuse across tiers of the same criteria type. For example, the `plat_count` handler queries once and caches the count, so checking 20 tiers of plat_count milestones makes 1 DB query instead of 20.

4. `check_all_milestones_for_user()` orchestrates batch checking:
   - Groups milestones by `criteria_type`.
   - For **tiered types** (plat_count, trophy_count, etc.): checks all tiers but only sends an in-app notification for the highest newly earned tier. This prevents notification spam when a user qualifies for multiple tiers at once.
   - For **one-off types** (psn_linked, discord_linked, calendar months): notifies individually since there is at most one tier.

5. Calendar month milestones use a parameterized handler: a single `_handle_calendar_month()` function is registered for all 12 `calendar_month_*` types. It shares a pre-fetched `_calendar_month_counts` cache across all months to avoid 12 separate COUNT queries.

**Currently registered handlers** (22 types):
`manual`, `plat_count`, `psn_linked`, `discord_linked`, `rating_count`, `playtime_hours`, `trophy_count`, `comment_upvotes`, `checklist_upvotes`, `badge_count`, `unique_badge_count`, `completion_count`, `stage_count`, `az_progress`, `genre_progress`, `subgenre_progress`, `calendar_month_jan` through `calendar_month_dec`, `calendar_months_total`, `calendar_complete`, `is_premium`, `subscription_months`.

### Leaderboard Calculation

Leaderboards are computed by management command (`update_leaderboards`) and cached in Redis. They are NOT computed on-the-fly.

**Four leaderboard types per badge series:**
1. **Earners** (`lb_earners_{slug}`): Users ranked by highest tier earned, then earliest earn date. One entry per user (highest tier only), computed via SQL window function (`RowNumber` partitioned by profile).
2. **Progress** (`lb_progress_{slug}`): Users ranked by trophy counts within series games (plats > golds > silvers > bronzes > earliest date).
3. **Community XP** (`lb_community_xp_{slug}`): Aggregate XP for a series across all users, extracted from ProfileGamification's JSONField via RawSQL.

**Two global leaderboards:**
4. **Total Progress** (`lb_total_progress`): Same as series progress but across ALL badge-related games.
5. **Total XP** (`lb_total_xp`): Users ranked by `ProfileGamification.total_badge_xp`.

Cache TTL is 7 hours (25,200 seconds). Companion keys `{key}_refresh_time` store ISO timestamps of last computation.

## Integration Points

### Sync Pipeline (token_keeper.py)
`_job_sync_complete()` calls `check_profile_badges()` after all trophy data is processed, then calls `check_all_milestones_for_user()` excluding challenge-specific types (those are checked by their own services). Deferred badge notifications are consolidated afterward via `DeferredNotificationService`.

### Discord Bot
- **Role assignment**: `notify_bot_role_earned()` calls POST to `BOT_API_URL/assign-role`.
- **Role removal**: `notify_bot_role_removed()` calls POST to `BOT_API_URL/remove-role`.
- **Full role sync**: `sync_discord_roles()` iterates all earned badge and milestone roles plus premium roles and re-assigns them (idempotent). Called when a user first verifies via Discord.
- All Discord HTTP calls are deferred via `transaction.on_commit()` to avoid holding database connections.
- Calls are skipped entirely when `settings.DEBUG` is True.

### Notification System
- Badge notifications are deferred and consolidated by `DeferredNotificationService` (one notification per series, highest tier only).
- Milestone notifications use `create_milestone_notification()` from `notifications/signals.py`.
- Tiered milestones only notify for the highest newly earned tier in a batch.

### Title System
Both badges and milestones can award Titles. When a badge/milestone is earned, `UserTitle` is created with `source_type='badge'|'milestone'` and `source_id=badge.id|milestone.id`. Titles are revoked when badges are revoked.

### Fundraiser System
The `funded_by` field on Badge tracks which donor funded the badge artwork. Higher tiers inherit this via the `effective_funded_by` property through `base_badge`.

### Challenge System
Challenge-related milestones (`az_progress`, `genre_progress`, `subgenre_progress`, calendar types) are excluded from the main milestone check in the sync pipeline and are instead checked by their respective challenge services (`check_az_challenge_progress`, `check_calendar_challenge_progress`, `check_genre_challenge_progress`).

## Gotchas and Pitfalls

### Stage 0 is silently skipped
Stage 0 is never counted toward badge completion but still contributes XP through progress tracking. If you add a concept to a Stage 0 expecting it to be required for the badge, it will not be. This is by design for "bonus" stages.

### Prerequisite chain is strict
If a user somehow loses their Bronze badge (e.g., a game's concept is reassigned and the stage no longer qualifies), all higher tiers in the chain will also be revoked on the next evaluation pass. The context is updated in-place during iteration so downstream tier checks immediately see the revocation.

### Context mutation during batch processing
`_process_badge_award_revoke()` mutates the `_context['earned_badge_ids']` set in-place when badges are awarded or revoked. This is intentional: it allows subsequent tiers in the same batch to see the correct state without a DB round-trip. However, this means the context is only valid within its evaluation loop and should not be reused across separate calls.

### Gamification signal handlers must check bulk state
The `post_save` and `post_delete` signals on UserBadge and UserBadgeProgress always fire. When a bulk update context is active (`is_bulk_update_active()`), handlers must defer the profile to `defer_profile_update()` instead of immediately recalculating. Forgetting this check would cause N redundant ProfileGamification recalculations during sync.

### earned_count uses F() expressions for race safety
Both Badge and Milestone `earned_count` fields are updated via `F('earned_count') + 1` / `F('earned_count') - 1` to prevent race conditions when multiple workers award the same badge concurrently. Direct read-then-write would lose increments under concurrency.

### Leaderboard data is cached, not live
Badge views read leaderboard data from cache. If the `update_leaderboards` cron fails or the cache is flushed, leaderboard pages will show empty results until the next successful cron run. The 7-hour TTL means data can be up to 7 hours stale under normal operation.

### `requires_all` vs `min_required` only matters for megamix
For series, collection, and developer badges, ALL non-zero qualifying stages must be complete regardless of the `requires_all` flag. The `min_required` field is only consulted when `badge_type='megamix'` and `requires_all=False`.

### Milestone handler caching is per-batch, not persistent
The `_cache` dict passed to milestone handlers lives only for the duration of a single `check_all_milestones_for_user()` call. It prevents redundant queries across tiers of the same type within that call, but the next call starts with a fresh cache.

### Calendar handlers share a single challenge instance
All 12 calendar month handlers plus `calendar_months_total` share a single `_calendar_challenge` cached reference (the most-progressed calendar challenge). If a user has multiple calendar challenges, only the one with the highest `completed_count` is evaluated for milestones.

### Discord calls are skipped in DEBUG mode
`notify_bot_role_earned()` and `notify_bot_role_removed()` short-circuit when `settings.DEBUG` is True. This means local development never triggers Discord API calls, which is correct but can make debugging role-related issues difficult. Test in staging with DEBUG=False if investigating Discord integration.

### `Concept.absorb()` must handle badge-related relationships
When a Concept is reassigned (and the old one orphaned), `absorb()` migrates data. Badge-related data flows through `Stage.concepts` (M2M) and `Badge.most_recent_concept` (FK). Both are handled in absorb(), but if new Concept relationships are added, absorb() must be updated or badge evaluation may reference stale data.

### Series slug is a string match, not a FK
Stages link to badges via `series_slug` string matching, not a foreign key. This means orphan stages (with a slug that no Badge uses) and orphan badges (with a slug that no Stage references) are possible. The `update_badge_requirements` command reconciles these but it must be run manually after structural changes.

## Management Commands

### Badge Commands

| Command | Usage | Purpose |
|---------|-------|---------|
| `populate_badges` | `--username <user>`, `--notify` | Run `initial_badge_check` for one user or all profiles |
| `check_all_badges` | `--username <user>`, `--dry-run` | Full badge recheck for all profiles with before/after diff reporting |
| `refresh_badge_series` | `--series <slug>` (required) | Check all profiles against a specific badge series; consolidates notifications |
| `check_profile_badge_series` | `--username <user>`, `--series <slug>` (both required) | Check one user against one badge series |
| `update_badge_requirements` | (no args) | Recalculate `required_stages` and `most_recent_concept` for all badges |

### Milestone Commands

| Command | Usage | Purpose |
|---------|-------|---------|
| `populate_milestones` | `--dry-run` | Create/update all milestone definitions and associated Titles (idempotent) |
| `populate_user_milestones` | `--type <criteria_type>`, `--username <user>`, `--silent` | Batch check milestones for all or one user |
| `grant_milestone` | `<name>`, `--username <user>`, `--usernames <csv>`, `--dry-run`, `--silent` | Manually grant a milestone (with full side effects) |
| `check_subscription_milestones` | `--silent`, `--dry-run` | Daily cron: check subscription_months milestones for active subscribers only |

### Gamification Commands

| Command | Usage | Purpose |
|---------|-------|---------|
| `recalculate_gamification` | `--profile <user>`, `--dry-run` | Recalculate ProfileGamification XP from source data |
| `audit_profile_gamification` | `--profile <user>`, `--fix`, `--verbose` | Compare denormalized XP values against recalculated totals; optionally fix mismatches |

### Leaderboard Commands

| Command | Usage | Purpose |
|---------|-------|---------|
| `update_leaderboards` | (no args) | Compute and cache all leaderboard data for all live badge series |

## Cache Keys

All leaderboard cache keys are set by the `update_leaderboards` management command with a 7-hour (25,200 second) TTL.

| Key Pattern | Type | Description |
|-------------|------|-------------|
| `lb_earners_{series_slug}` | list[dict] | Earners leaderboard for a badge series |
| `lb_earners_{series_slug}_refresh_time` | str (ISO datetime) | When the earners leaderboard was last computed |
| `lb_progress_{series_slug}` | list[dict] | Progress leaderboard for a badge series |
| `lb_progress_{series_slug}_refresh_time` | str (ISO datetime) | When the progress leaderboard was last computed |
| `lb_community_xp_{series_slug}` | int | Total community XP for a badge series |
| `lb_total_progress` | list[dict] | Global progress leaderboard across all badge games |
| `lb_total_progress_refresh_time` | str (ISO datetime) | When the total progress leaderboard was last computed |
| `lb_total_xp` | list[dict] | Global XP leaderboard from ProfileGamification |
| `lb_total_xp_refresh_time` | str (ISO datetime) | When the total XP leaderboard was last computed |

## Related Docs

- [Gamification System Design](../features/gamification-design.md): P.L.A.T.I.N.U.M. stats, Jobs system, and future XP expansion plans
- [Dashboard](../dashboard.md): Dashboard modules that display badge progress and XP
- [Community Hub](../community-hub.md): Reviews and ratings system (feeds into `rating_count` milestones)
