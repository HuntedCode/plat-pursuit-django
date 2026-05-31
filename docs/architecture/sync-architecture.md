# PSN Sync Architecture: Fingerprint-Based Unified Sync

This document describes how the PSN sync pipeline reconciles a profile's local DB state against PlayStation Network on every sync, using a cheap "fingerprint" check to skip expensive work when nothing has changed and falling through to a full reconciliation walk when it has. It also captures the design rationale: why this shape was chosen over the older split (separate initial-sync, follow-up-refresh, and gated health-check flows).

For the operational view of the worker / queue / token system that runs the sync, see [token-keeper.md](token-keeper.md).

---

## Background: What This Replaced

Before this redesign the sync pipeline had three structurally separate flows:

1. **Initial sync** (`PSNManager.initial_sync` → `_job_sync_trophy_titles`): full walk of `trophy_titles`, queue everything.
2. **Follow-up refresh** (`PSNManager.profile_refresh` → `_job_profile_refresh`): paginate `trophy_titles` with an early-exit on `last_updated_datetime > last_synced`.
3. **Health check** (inside `_job_sync_complete`): gated on `summary_total != tracked_trophies + total_hiddens`. When the gate tripped, walked `trophy_titles` AGAIN to identify drift and queue re-syncs.

Three problems fell out of that shape:

**Problem 1: Visibility-only toggles were invisible to the gate.** PSN's `trophy_summary` endpoint reports total earned trophies *including hidden ones*. So when a user hid or unhid a game on PSN, `summary_total` did not change. The old health-check gate compared `summary_total` to `tracked_trophies + total_hiddens` and never tripped on a pure visibility flip. Hide/unhide changes were reconciled only as a side effect of the gate tripping for some other reason (typically: the user earned a new trophy). A user who toggled visibility without earning anything afterward saw stale state on their profile until their next trophy.

**Problem 2: When the gate did trip, the system paginated twice.** The follow-up refresh paginated `trophy_titles` once with an early exit. Then `sync_complete` paginated again with no early exit. Same endpoint, overlapping data, two round-trips.

**Problem 3: `last_synced` was load-bearing for sync internals.** Drift in `last_synced`, manual admin adjustments, or an interrupted sync that never updated `last_synced` all degraded correctness in subtle ways. The system had no self-healing path; it trusted that the timestamp was correct.

Those three problems shared a root cause: the system used `last_synced` and trophy-count totals as proxies for "does the DB match PSN?" but neither proxy was fully reliable. The fix was to replace the proxy with a direct check on every sync.

---

## The Fingerprint

The PSN side of the wall exposes two cheap signals that, together, characterize the state of a user's library:

| Signal | Source | Cost | Captures |
|---|---|---|---|
| Total earned trophies (by type) | `trophy_summary` endpoint | 1 API call | All trophy earnings, regardless of hidden status |
| Visible game count | `trophy_titles` endpoint, `totalItemCount` field on any page response | 1 API call (page 1, any size) | The size of the user's non-hidden game set |

We define the **PSN fingerprint** as `(earned_bronze, earned_silver, earned_gold, earned_platinum, visible_game_count)`.

The corresponding **DB fingerprint**:

```python
psn_fingerprint = (
    summary.earned_trophies.bronze,
    summary.earned_trophies.silver,
    summary.earned_trophies.gold,
    summary.earned_trophies.platinum,
    trophy_titles_iterator._total_item_count,  # set after first fetch
)

db_fingerprint = (
    profile.total_bronzes,
    profile.total_silvers,
    profile.total_golds,
    profile.total_plats,
    ProfileGame.objects.filter(profile=profile, user_hidden=False).count(),
)
```

If they match, the DB and PSN agree on every dimension we can cheaply observe. The user has not earned trophies, has not toggled visibility, and the game count is consistent. We can skip the full pagination walk.

If they differ, something has changed. We walk to figure out what.

### What the fingerprint cannot catch

A **symmetric swap**: user hides game A (with N trophies of types T) and unhides game B (with the same N trophies of the same types T) in the same window, with no other activity. Trophy totals stay the same, visible game count stays the same, fingerprints match, drift goes undetected.

This is rare in practice. Two games with identical trophy breakdowns being toggled in the same sync window is a narrow case. We accept the gap. Any normal trophy earning afterward heals it automatically because the fingerprint will mismatch on the next sync. A weekly safety-net cron was originally planned for this but dropped as not worth the cron clutter for an edge case this narrow.

---

## Unified Flow

The new flow has one orchestrator path. `profile_refresh` keeps its name. `sync_trophy_titles` (the initial-sync orchestrator) is retired; the unified `profile_refresh` handles both first-time and follow-up syncs. Whether the DB starts empty or partially populated is no longer a code-branch decision. It just shows up as a larger fingerprint mismatch.

### Sequence

```
PSNManager.profile_refresh(profile)         # cron or admin trigger
  └─ assign_job('sync_profile_data')        # fetches level/avatar/region (unchanged)
  └─ assign_job('profile_refresh')          # the unified orchestrator

_job_profile_refresh(profile_id):
  1. Fetch trophy_summary                   # 1 API call
  2. Compute psn_fingerprint and db_fingerprint
  3. If matched:
        - Bypass walk
        - Set sync_progress_target to 0
        - Schedule sync_complete via pending_sync_complete
  4. If mismatched:
        - Walk trophy_titles fully (pagination)
        - Build psn_visible_set (np_communication_ids)
        - For each title:
            - Create/update Game and ProfileGame (existing helpers)
            - Compare PSN earned count vs DB; if drift, mark for sync_trophies
            - Track concept resolution needs (sync_title_id) inline
        - After walk:
            - Bulk-update ProfileGame.user_hidden and EarnedTrophy.user_hidden
              based on psn_visible_set vs db_visible_set diff
            - Set sync_progress_target accurately
            - Queue sync_trophy_groups, sync_trophies, sync_title_id as needed
            - Decide bulk_priority routing based on total job count
  5. Set sync_orchestrator_pending and pending_sync_complete (existing mechanism)
  6. Return; per-game jobs run on their queues; sync_complete fires when they finish

_job_sync_complete(profile_id, ...):
  Pure finalization (the health check is now upstream, in profile_refresh):
  1. Orphan-concept reconciliation: mint stub Concepts for any Game on this
     profile with concept=NULL (catches modern games omitted from PSN
     title_stats, where sync_title_id never queued). Pure DB work, no PSN
     calls. Runs before the IGDB drain so the new stubs ride along.
  2. Drain deferred IGDB enrichments
  3. Recompute total_hiddens from authoritative DB state
  3b. TrophyGroup/Trophy completeness + orphan self-heal (each gated by its own
      6h Redis cooldown): re-queue per-game sync jobs for games that are
      missing all Trophy records, missing all TrophyGroup records, OR whose
      Trophy rows reference a trophy_group_id with no matching TrophyGroup row
      (orphaned/missing DLC groups while the trophies survive). When a group
      gap is found, the sync_trophy_groups re-queue is expanded to ALL games
      sharing the detected game's concept (stacked/regional siblings tend to
      gain the same DLC, and an unpopular sibling may have no active syncer to
      catch it). Finding work resets the profile to 'syncing' and re-enters
      per-game jobs via pending_sync_complete. For a catalog-wide refresh
      outside the sync path, see the `resync_trophy_groups` command.
  4. update_profile_games (with hide_hiddens fix)
  5. update_profile_trophy_counts
  6. Badge eval, milestones, challenges (unchanged)
  7. Deferred notifications (unchanged)
  8. Cache invalidation (unchanged)
  9. set_sync_status('synced'), update last_synced
```

Note: `sync_complete` retains its mismatch-retry capability for individual games. If `sync_trophies` for a game fails to converge, the existing `MAX_MISMATCH_RETRIES = 3` budget per game per 24h still applies. This is preserved by re-queueing through the same `pending_sync_complete` mechanism.

---

## Fast-Path vs Slow-Path

| Path | When | Cost | What runs |
|---|---|---|---|
| Fast | Fingerprints match | 2 PSN API calls (`trophy_summary` + page 1 of `trophy_titles`) | Skip the walk. Skip per-game queueing. Sync_complete still runs (drains IGDB, recomputes stats, runs badges, invalidates caches). |
| Slow | Fingerprints mismatch | Full pagination of `trophy_titles` (~25 calls for 10k-game whales) plus per-game work as detected | Full reconciliation: trophy drift, new games, hide/unhide flips, concept resolution. |

The fast path is the common case for users syncing more often than they earn trophies. For a typical user who syncs every 12 hours and earns trophies a few times a week, most syncs are fast-path. For whales who earn frequently, every sync mismatches and walks; this is the same cost they pay today, just consolidated into one walk instead of potentially two.

**Stat consistency on fast path.** The fingerprint can match while the user has toggled a profile setting like `hide_hiddens`, which changes the filter that `update_profile_games` and `update_profile_trophy_counts` apply. To handle this without extra logic on the fast path, both stat updaters are called from `sync_complete` (which runs after both paths) rather than from the orchestrator job. This makes stat refresh canonical at the end of every sync, regardless of which path got us there.

---

## Walk Semantics

The walk paginates `trophy_titles` newest-to-oldest (PSN's natural order, sorted by `last_updated_datetime` descending). We always walk the full list when the fingerprint mismatches; the early-exit on `last_updated_datetime > last_synced` is removed because:

- Trophy-count drift on an old game is detectable per-title regardless of timestamp ordering.
- Visibility flips don't bump `last_updated_datetime`, so the early-exit can miss them.
- The new fingerprint check at the top of the flow is the cheap path; once we've decided to walk, walking fully is structurally simpler and not measurably more expensive than walking with a mid-loop early-exit.

During the walk, we collect three things:

1. **Per-title state for ProfileGame upsert** (existing logic).
2. **The set of np_communication_ids returned** (the PSN-visible-game set).
3. **Games whose PSN earned count differs from DB earned count** (for sync_trophies queueing).

After the walk completes, we bulk-update visibility:

```python
psn_visible_ids = {t.np_communication_id for t in walked_titles}
db_visible_ids = set(ProfileGame.objects.filter(
    profile=profile, user_hidden=False
).values_list('game__np_communication_id', flat=True))

newly_hidden = db_visible_ids - psn_visible_ids
newly_unhidden = psn_visible_ids - db_visible_ids
```

Then bulk-flip `ProfileGame.user_hidden` and `EarnedTrophy.user_hidden` for each set. This replaces the existing gated logic at `token_keeper.py:1390-1421` and removes the dependency on `current_tracked_games` shrinking-set bookkeeping inside the walk.

---

## Field Changes

| Field | Before | After |
|---|---|---|
| `Profile.last_synced` | Used for sync internal gating (cooldown, refresh threshold, early-exit) | Used only for cooldowns, refresh-tier thresholds, sort/index. Sync internals no longer read it for gating logic. PSN outage recovery still backdates it `-10 days` so the next refresh cron picks the profile up regardless of tier. |
| `Profile.last_profile_health_check` | Write-only (no readers gating logic) | **Removed.** Migration drops the column. |
| `Profile.total_hiddens` | Computed via subtraction trick (`summary_total - tracked_trophies['total']`) using a stale snapshot, gated on mismatch | Computed authoritatively in `sync_complete` from `EarnedTrophy.objects.filter(earned=True, user_hidden=True).count()`. Always runs. |
| `Profile.total_games`, `Profile.total_completes` | Counts all ProfileGames regardless of `hide_hiddens` setting | Honors `profile.hide_hiddens` (matching `update_profile_trophy_counts`). |
| `ProfileGame.last_updated_datetime` | Drives early-exit walk | No longer drives walk termination. Still used for ordering display and PSN's natural sort. |
| `ProfileGame.user_hidden` / `EarnedTrophy.user_hidden` | Bulk-flipped only when health check gate trips | Bulk-flipped on every sync where the visible set differs from PSN. |
| `ProfileGame.hidden_flag` (PSN-side metadata) | Updated on per-title path | Unchanged. |

Migration plan: a single migration drops `last_profile_health_check` and updates `update_profile_games` in the same release. `total_hiddens` math change is a code-only change (no schema migration).

---

## Adjacent Systems

The refactor preserves every existing integration point in `sync_complete`. What changes is the timing of upstream work, not the downstream hooks.

| System | Hook | Change |
|---|---|---|
| Badge evaluation | `check_profile_badges()` in `sync_complete` | Unchanged. Still fires after stats are updated. Retroactive credit principle preserved. |
| Milestones | `check_all_milestones_for_user()` | Unchanged. |
| Challenges (A-Z, Calendar, Genre) | Three check functions in `sync_complete` | Unchanged. |
| Deferred notifications | Platinum during `sync_trophies`, badge consolidation in `sync_complete` | Unchanged. |
| IGDB enrichment | `_drain_deferred_igdb_enrich()` at top of `sync_complete` | Unchanged. New concepts created during the walk still defer their enrichment to the same Redis queue. |
| Scout `games_discovered` | Increment during the walk when a new ProfileGame is created | Unchanged. |
| Cache invalidation | `invalidate_dashboard_cache`, `invalidate_stats_cache`, `invalidate_timeline_cache` | Unchanged. |
| Site Heartbeat, Community Trophy Tracker | Read sync-derived state on their own crons | Unaffected by the refactor; they read from `EarnedTrophy` and `Profile`. |
| Discord-verified 12h cadence | Configured in `refresh_profiles` cron | Unchanged. |
| `bulk_gamification_update()` context | Wraps badge eval | Unchanged. |
| Signal suppression patterns | `sync_signal_suppressor()` and `_sync_previous_earned` stamps | Preserved. The walk still uses the same per-game `sync_trophies` job for trophy data, which already wraps signal suppression correctly. |

The frontend surfaces (hotbar polling, home syncing page, finalize_phase UI, mobile API) are unchanged. `ProfileSyncStatusView` response schema stays stable.

---

## State Machine

Externally identical: `synced`, `syncing`, `error`, `no_psn`. Transitions and triggers are unchanged.

Internally simpler: there's no longer a meaningful difference between "is this an initial sync or a follow-up?" The Profile model's existing `sync_status`, `sync_progress_value`, and `sync_progress_target` all still represent the same things to consumers.

`_check_stuck_syncing_profiles` ([token_keeper.py:721-758](../../trophies/token_keeper.py#L721-L758)) needs review during implementation. Its 90-second grace period and pending-data semantics are tied to the existing orchestrator handoff. They likely keep working as-is because the unified `profile_refresh` still uses `sync_orchestrator_pending` and `pending_sync_complete` keys, but this should be verified explicitly.

---

## Symmetric-Swap Edge Case

Acknowledged limitation, see "What the fingerprint cannot catch" earlier. We accept the gap rather than running a periodic full-sweep cron just to backstop an edge case this rare. If it ever becomes a real source of user-visible bugs, the recovery path is straightforward: a `reconcile_visibility` management command that paginates `trophy_titles` for one profile and runs the bulk visibility update without queueing per-game work, scheduled on whatever cadence makes sense at the time.

---

## Migration Approach (historical)

The refactor shipped as a single rollout to all profiles, no per-profile flags, no progressive rollout. The reasoning was that per-profile flagging adds complexity that's hard to retire later, the old and new code paths could not both run for the same profile (they'd write the same fields with different logic), and the fingerprint check is conservative by design (falls through to the slow path when in doubt).

The Phase A rollout shipped behind a `SYNC_V2_ENABLED` env-var kill-switch so production issues could revert to the legacy path with a config flip and worker restart. Phase C deleted the kill-switch alongside the legacy paths once v2 had been live and stable.

The daily reconciliation crons `recalc_profile_counters` and `recalc_earn_rates` continue to run as drift safety nets independent of the sync flow.

## Implementation History

The work shipped across three phases. The two safety-net phases originally planned (D and E perf optimization) were dropped: Phase D's symmetric-swap weekly cron was judged too much cron clutter for too rare an edge case, and the whale-pagination parallelization remains an open optional perf win without an architectural impact.

| Phase | Scope | Status |
|---|---|---|
| **A** | Build unified `profile_refresh` orchestrator alongside the old paths, gated by `SYNC_V2_ENABLED`. Fingerprint compute + comparison, full-walk pagination, visibility set diff + bulk-update, `sync_complete` health check moved out, `update_profile_games` honors `hide_hiddens`, `total_hiddens` from authoritative DB state, structured logging. Old code path remained reachable via kill-switch. | Shipped |
| **B** | Migration `0227_drop_profile_last_profile_health_check` removes the field. All write sites and the admin fieldset entry are dropped. PSN outage recovery's `-10 days` backdate is preserved as the cron's net-catch mechanism. | Shipped |
| **C** | Legacy paths deleted: `_job_sync_trophy_titles`, the legacy `_job_profile_refresh`, the gated health-check block in `_job_sync_complete`, the dispatcher branches, the kill-switch setting and its `from django.conf import settings` import in `token_keeper`. `PSNManager.initial_sync` collapsed to queue `profile_refresh` directly. Method renames: `_job_sync_v2` → `_job_profile_refresh`, helpers similarly. Codebase is single-path. | Shipped |
| **D** | Weekly `reconcile_visibility` cron for symmetric-swap edge case. | Dropped (insufficient value for the cron clutter) |
| **E (perf)** | Parallelize `trophy_titles` pagination in the slow path for whale profiles. After page 1 returns `totalItemCount`, remaining pages fan out via a `ThreadPoolExecutor` capped at 3 workers (matches typical instance pool size, doesn't starve other concurrent jobs). Same API-call count, ~3x faster wall-clock for 10k-game accounts. Sequential fallback when only 0-1 extra pages remain. | Shipped |

---

## Gotchas and Pitfalls

- **PSN's `trophy_summary` includes hidden trophies in its earned counts.** This is the reason the old gate is blind to visibility flips. Any future logic that compares `summary_total` to a DB count must account for this asymmetry.
- **`totalItemCount` from `trophy_titles` excludes hidden games.** PSN's hidden-game machinery removes them from the response entirely; they don't appear with a flag, they just aren't there. The visible-game-count signal works because of this exclusion, but it also means we can never enumerate hidden games by walking `trophy_titles` alone.
- **Walking newest-to-oldest does not terminate cheaply when the only drift is a hidden flip on an old game.** Fingerprint mismatch tells us something is off; we walk the full list to find it. This is the cost we accept by design. It only fires when something has actually changed.
- **`bulk_update` on `ProfileGame.user_hidden` and `EarnedTrophy.user_hidden` bypasses signals.** The downstream stats (`update_profile_trophy_counts`) are recomputed explicitly in `sync_complete` to catch this. Don't add per-flip logic to signals; recompute in `sync_complete`.
- **`_check_stuck_syncing_profiles` has implicit dependencies on the orchestrator handoff timing.** The unified `profile_refresh` job uses the same `sync_orchestrator_pending` and `pending_sync_complete` Redis keys the legacy paths used, which is why this kept working through the migration. Anyone changing those Redis-key semantics needs to think about the stuck-detector at the same time.
- **Symmetric swap is a known blind spot.** Hide A and unhide B in the same window where both have identical trophy counts and types: trophy totals stay equal, visible-game count stays equal, fingerprint matches, drift goes undetected. Rare in practice; the weekly cron originally planned to backstop this was dropped as not worth the cron clutter. Any normal trophy earning afterward heals it because the fingerprint will mismatch on the next sync.
- **The `-10 days` outage backdate is intentional.** It exists so the next `refresh_profiles` cron picks the profile up regardless of its tier, even though `last_synced` is no longer load-bearing for sync internals.
- **The concept-less safety net forces slow path when any of the profile's games has `concept IS NULL`.** This catches matching pipeline failures, IGDB outages, and manual concept cleanup. One indexed `EXISTS` query per sync; trips slow path only when actually needed.
- **Orphan-concept reconciliation in `sync_complete` closes the safety-net loop.** The slow-path's inline fallbacks (legacy-platform inline stubs, sync_title_id fallbacks) only fire for games that actually reach them. A modern game in `trophy_titles` but missing from PSN's `title_stats` response (never-played or hidden modern title) never gets a sync_title_id queued, so without this reconciliation the fingerprint-level concept-less recovery would re-force slow path on every sync without progress. The reconciliation step at the top of `sync_complete` mints a stub for any such orphan, so the next sync's fingerprint check passes cleanly. Pure DB work plus a deferred IGDB enrich; bounded by the (typically tiny) orphan count per profile.
- **Orphaned TrophyGroup rows are a blind spot for both fingerprint and zero-group checks.** A game can keep every `Trophy` row but lose one or more `TrophyGroup` rows (typically DLC groups), from old data-cleanup or migration issues. The slow-path drift check can't see it (the game-level `defined_trophies` total still matches PSN), and the zero-group completeness check can't either (`group_count > 0` because the base group survives). `Trophy.trophy_group_id` is a plain CharField, not an FK to `TrophyGroup`, so the corruption is detectable as a `(game, trophy_group_id)` pair present on `Trophy` rows but absent from `TrophyGroup`. The `orphan_group_check` step in `sync_complete` finds these via a DB-side `Exists`/`distinct` query (bounded for whales) and re-queues `sync_trophy_groups`, which idempotently `get_or_create`s the missing rows. For backfilling the existing catalog outside the sync path, use `backfill_concept_trophy_groups --audit-orphaned-groups [--fix]`.

---

## Related Docs

- [token-keeper.md](token-keeper.md): the operational reference for the worker / queue / token system that runs the sync.
- [sync-optimization.md](sync-optimization.md): historical optimizations preserved by this design (signal suppression, batched updates, deadlock handling).
- [Cron Jobs](../guides/cron-jobs.md): scheduling reference for the daily reconciliation crons (`recalc_profile_counters`, `recalc_earn_rates`).
- [Product Identity](product-identity.md): Pursuer-centric product spine. The reliability of sync (and the hidden-game handling specifically) underpins user trust in their badge progression numbers.
