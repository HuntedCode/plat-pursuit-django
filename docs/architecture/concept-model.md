# Concept Model System

The Concept model is the central abstraction that unifies PlayStation games sharing the same trophy list. When Sony publishes a game on multiple platforms (e.g., PS4 and PS5), each platform version is a separate `Game` record in PlatPursuit, but they all share the same "concept" in the PSN API. The `Concept` model captures this grouping so that ratings, reviews, roadmaps, badges, IGDB enrichment, and other user-facing content are scoped to the logical game rather than a specific platform stack. This means a user's review of "God of War Ragnarok" applies whether they played the PS4 or PS5 version.

## Architecture Overview

The system has four core models and several supporting relationships:

1. **Concept**: The logical game entity. Identified by `concept_id` (from the PSN API, or a `PP_` prefix for stub concepts). Groups one or more `Game` records. All user-facing content (ratings, reviews, roadmaps, IGDB enrichment) is scoped to Concept, not Game.
2. **Game**: A specific platform release. Has a FK to `Concept` (nullable, SET_NULL). The `add_concept()` method handles assignment and triggers `absorb()` when a concept reassignment orphans the old one.
3. **TitleID**: Links PSN title IDs (per-platform identifiers) to the system. Used during sync to look up concept details from the PSN API and for deduplication.
4. **GameFamily**: A lightweight cross-generation grouping layer. Unlike Concept (which merges content), GameFamily groups related Concepts WITHOUT merging them. Each Concept in a family retains its own comments, ratings, and checklists.

The most critical operation in the system is `Concept.absorb(other)`. When a concept reassignment leaves the old Concept with zero games, `absorb()` migrates every piece of related data to the surviving Concept before the orphan is deleted. Failing to update `absorb()` when adding new Concept relationships causes silent data loss.

## File Map

| File | Purpose |
|------|---------|
| `trophies/models.py` (lines 658-881) | `Concept` model: fields, `absorb()`, `create_default_concept()`, `add_title_id()`, `check_and_mark_regional()`, slug generation |
| `trophies/models.py` (lines 427-526) | `Game` model: fields, `add_concept()`, `add_region()` |
| `trophies/models.py` (lines 882-894) | `TitleID` model |
| `trophies/models.py` (GameFamily) | `GameFamily` model (keyed on `igdb_id`, populated via IGDB enrichment) |
| `trophies/services/psn_api_service.py` | `create_concept_from_details()`: creates or retrieves a Concept from PSN API response data; `update_profile_game_with_title_stats()`: checks for stale concepts during sync |
| `trophies/token_keeper.py` | `_job_sync_title_id()`: the sync pipeline entry point that calls `create_concept_from_details()`, `game.add_concept()`, and `Concept.create_default_concept()` |
| `trophies/management/commands/backfill_default_concepts.py` | One-time backfill command for games missing concepts |
| `trophies/services/igdb_service.py` (`_link_concept_to_family`) | GameFamily linking — runs on every IGDB match acceptance, keyed on `igdb_id` |

## Data Model

### Concept

| Field | Type | Notes |
|-------|------|-------|
| `concept_id` | CharField(50), unique | PSN concept ID (e.g., `"234783"`) or stub ID (e.g., `"PP_42"`) |
| `unified_title` | CharField(255) | Display name. Cleaned by `clean_title_field()` on save |
| `title_ids` | JSONField (list) | Accumulated PSN title IDs associated with this concept. Merged during `absorb()` |
| `family` | FK to GameFamily (nullable, SET_NULL) | Cross-generation grouping. Inherited during `absorb()` if the target has none |
| `publisher_name` | CharField(255) | Publisher from PSN API |
| `release_date` | DateTimeField (nullable) | Used for badge `most_recent_concept` tracking |
| `genres` | JSONField (list) | Genre tags from PSN API |
| `subgenres` | JSONField (list) | Subgenre tags from PSN API |
| `descriptions` | JSONField (dict) | `{"short": "...", "long": "..."}` from PSN API |
| `content_rating` | JSONField (dict) | ESRB/PEGI rating data |
| `media` | JSONField (dict) | Screenshots and video URLs from PSN API |
| `bg_url` | URLField (nullable) | PSN landscape background URL (`GAMEHUB_COVER_ART` or `BACKGROUND_LAYER_ART`). Used as share-card backdrops. Only available for modern titles (PS4/PS5) that go through the `sync_title_stats` pipeline |
| `concept_icon_url` | URLField (nullable) | PSN portrait cover art (`MASTER` media). Primary source for game cover images shown in browse cards, game detail headers, and all other portrait/square containers |
| `guide_slug` | CharField(50, nullable) | Link to external guide |
| `guide_created_at` | DateTimeField (nullable) | Guide creation timestamp |
| `slug` | SlugField(300), unique, nullable | URL-friendly slug for Review Hub pages. Auto-generated from `unified_title` on first save, with collision handling via counter suffix |

**Indexes**: `concept_id`, `unified_title`, `publisher_name`, `release_date`, `slug`

### Game (Concept-related fields only)

| Field | Type | Notes |
|-------|------|-------|
| `concept` | FK to Concept (nullable, SET_NULL) | The concept this game belongs to. Assigned via `add_concept()` |
| `concept_lock` | BooleanField (default False) | Admin lock. When True, `add_concept()` is a no-op and sync cannot overwrite the concept |
| `concept_stale` | BooleanField (default False) | Flag for concept re-lookup on next sync. Checked in `update_profile_game_with_title_stats()` |
| `title_ids` | JSONField (list) | PSN title IDs for this specific game (separate from Concept.title_ids) |

### TitleID

| Field | Type | Notes |
|-------|------|-------|
| `title_id` | CharField(50), unique | PSN title ID string (e.g., `"PPSA01501_00"`) |
| `platform` | CharField(10) | Platform code (`"PS4"`, `"PS5"`, etc.). Can be corrected during sync if it mismatches the Game's platform |
| `region` | CharField(10) | Region code. Initialized as `"IP"` (indeterminate) and updated from PSN API response |

**Indexes**: `title_id`, `region`

### GameFamily

| Field | Type | Notes |
|-------|------|-------|
| `canonical_name` | CharField(255), indexed | Display name for the family grouping |
| `admin_notes` | TextField | Internal notes for staff |
| `is_verified` | BooleanField | Whether the grouping has been staff-verified |
| `created_at` | DateTimeField (auto) | Creation timestamp |
| `updated_at` | DateTimeField (auto) | Last modification timestamp |

### ~~GameFamilyProposal~~ (removed in Phase 2.6)

The proposal model was deleted when GameFamily switched to a deterministic
IGDB-id-keyed creation model. No proposal / review queue exists anymore —
families are created inline during IGDB enrichment via `get_or_create`. See
[Game Family System](../features/game-family.md) for the full flow.

## Key Flows

### Concept Assignment During Sync (`_job_sync_title_id`)

1. Token Keeper resolves the `Game` and `TitleID` for the sync job
2. Platform mismatch check: if `TitleID.platform` does not match `Game.title_platform`, the Game's platform is trusted and the API call uses the corrected platform. If successful, `TitleID.platform` is updated at source
3. PSN API is called to get concept details via `game_title` endpoint
4. If the API returns valid details (no `errorCode`):
   - `PsnApiService.create_concept_from_details()` does `get_or_create` on `Concept` using the PSN `concept_id`
   - `concept.update_release_date()` and `concept.update_media()` populate metadata
   - `game.add_concept(concept)` assigns the concept (triggers `absorb()` if needed)
   - `concept.add_title_id()` records the title ID association
   - `concept.check_and_mark_regional()` flags games with multiple stacks per platform
5. If the API returns an error or fails:
   - `Concept.create_default_concept(game)` creates a stub concept (`PP_N`)
   - `game.add_concept(default_concept)` assigns it
6. Exception recovery: if anything fails and the game still has no concept, a default concept is created as a last resort

### Concept Reassignment via `Game.add_concept(concept)`

1. If `concept` is None or `self.concept_lock` is True: return immediately (no-op)
2. If the game already has this same concept: clear `concept_stale` if set, then return
3. Store reference to `old_concept`
4. Update `self.concept` to the new concept, clear `concept_stale`, save
5. Invalidate game page caches (`game:imageurls:*`)
6. Check if `old_concept` is now orphaned (has zero remaining games):
   - If orphaned: call `concept.absorb(old_concept)` then `old_concept.delete()`
   - Invalidate comment and rating caches on the surviving concept

### Data Migration via `Concept.absorb(other)` (CRITICAL)

This method migrates ALL related data from `other` (the orphaned concept) to `self` (the surviving concept). The order of operations matters. Here is every relationship handled:

1. **Comments** (legacy concept-level, trophy-level, and checklist-level historical data): `other.comments.update(concept=self)`. Bulk update, no deduplication needed. The comment system itself is no longer accepting new comments, but historical rows are still migrated so vote/report/moderation paths over old data remain valid.

2. **UserConceptRatings**: Iterates `other.user_ratings.all()`. Skips duplicates keyed by `(profile_id, concept_trophy_group_id)`. Non-duplicates are re-pointed with `save(update_fields=['concept'])`.

3. **ConceptTrophyGroups**: Iterates `other.concept_trophy_groups.all()`. Non-duplicate groups (by `trophy_group_id`) are re-pointed to `self`. Duplicate groups are left on `other` and cascade-delete with it. This step MUST happen before Reviews so that re-pointed reviews can reference the surviving CTG.

4. **Reviews**: Iterates `other.reviews.all()`. Skips duplicates keyed by `(profile_id, concept_trophy_group_id)`. Non-duplicates are re-pointed with `save(update_fields=['concept'])`.

5. **Checklists** (legacy): `other.checklists.update(concept=self)`. Bulk update, includes cascaded sections, items, votes, reports, and user progress. The checklist system was removed in favor of staff-authored Roadmaps, but the underlying tables are retained so historical data is preserved across concept merges.

6. **FeaturedGuide entries**: `other.featured_entries.update(concept=self)`.

7. **Profile.selected_background**: `other.selected_by_profiles.update(selected_background=self)`. Re-points premium users who selected the old concept as their profile background.

8. **Badge.most_recent_concept**: `other.most_recent_for_badges.update(most_recent_concept=self)`. Re-points badge records that track the most recently released concept in their series.

9. **Stage.concepts (M2M)**: Iterates `other.stages.all()`. For each stage, adds `self` and removes `other`. This maintains badge stage requirements.

10. ~~**GameFamilyProposal.concepts (M2M)**~~: Removed in Phase 2.6 with the proposal model.

11. **GenreChallengeSlot**: `other.genre_challenge_slots.update(concept=self)`. Re-points genre challenge game picks.

12. **GenreBonusSlot**: `other.genre_bonus_slots.update(concept=self)`. Re-points genre challenge bonus game picks.

13. **GameFamily inheritance**: If `other.family` is set and `self.family` is not, `self` inherits the family FK.

14. **title_ids merge**: Appends any title IDs from `other.title_ids` that are not already in `self.title_ids`. Deduplicates during merge.

### Default Concept Creation (`Concept.create_default_concept`)

1. Uses a Redis atomic counter (`pp_concept_counter`) for thread-safe unique ID generation
2. If the counter key does not exist in Redis, initializes it from the database by finding the max numeric suffix among existing `PP_*` concept IDs (`SET NX` to avoid race conditions)
3. Increments the counter and attempts `Concept.objects.create()` with ID `PP_{next_id}`
4. If `IntegrityError` (collision), retries up to 5 times with the next incremented ID
5. The stub concept gets `unified_title` set to `"{game.title_name} ({platforms})"` and `concept_icon_url` from the game's icon

### Slug Generation (on `Concept.save`)

1. Only runs if `self.slug` is empty and `self.unified_title` is set
2. Slugifies the title, truncated to 280 characters
3. Falls back to `"concept-{concept_id}"` if slugification produces an empty string (e.g., for Asian-character-only titles)
4. Appends `-1`, `-2`, etc. if the slug collides with an existing Concept

### TitleID Addition (`Concept.add_title_id`)

1. Uses `select_for_update(nowait=True)` with `@retry` decorator (3 attempts, 0.5s wait) to handle concurrent sync workers
2. Appends the title ID to the list only if not already present
3. Updates `self.title_ids` in-memory after the atomic save

## Integration Points

- **Token Keeper** (`trophies/token_keeper.py`): The sync pipeline calls `_job_sync_title_id()` for each game during profile sync. This is where concepts are created, assigned, and reassigned. Health check at sync completion ensures every game has a concept.
- **PSN API Service** (`trophies/services/psn_api_service.py`): `create_concept_from_details()` does `get_or_create` on Concept from PSN API data. `update_profile_game_with_title_stats()` detects stale concepts and triggers re-sync.
- **Rating System** (`trophies/services/rating_service.py`): `UserConceptRating` is scoped to Concept + ConceptTrophyGroup. Cache invalidation is triggered after `absorb()`.
- **Review System**: `Review` model has FK to Concept. Reviews are migrated during `absorb()` with deduplication by `(profile_id, concept_trophy_group_id)`.
- **Roadmap System**: `Roadmap` is 1:1 with Concept (staff-authored platinum guides). Migrated as part of the Checklist legacy data preservation since roadmaps replaced checklists as the user-facing guide surface.
- **IGDB Integration**: `IGDBMatch` is 1:1 with Concept and is transferred during `absorb()` if the surviving concept lacks one. `ConceptCompany`, `ConceptGenre`, `ConceptTheme`, and `ConceptEngine` M2M-through rows are migrated with role merging and de-duplication. `Concept.get_cover_url(size)` / `Concept.cover_url` property returns the PSN MASTER icon (`concept_icon_url`) for non-stub concepts, else constructs an IGDB cover URL from the matched game's `igdb_cover_image_id` (trusted matches only). Note: this per-concept helper is NOT the primary cover-art entry point at render time. `Game.display_image_url` is the single source of truth for templates and prefers **trusted IGDB cover first**, then falls back to `concept.concept_icon_url` → `game.title_image` → `game.title_icon_url`.
- **Badge System**: `Stage.concepts` (M2M) defines which concepts count toward badge completion. `Badge.most_recent_concept` tracks the newest game in a badge series. Both are handled by `absorb()`.
- **Challenge System**: `GenreChallengeSlot` and `GenreBonusSlot` reference Concept for genre challenge game picks.
- **GameFamily**: Families are now keyed deterministically by `GameFamily.igdb_id`. On IGDB match acceptance, `IGDBService._link_concept_to_family()` creates (or joins) the family by `igdb_id` and reassigns the concept. Orphaned families are cleaned up in the same step. Backfill via `python manage.py backfill_game_families_from_igdb`. No cron or heuristic matcher runs; the old `core/services/game_family_service.py` was removed.
- **Profile Backgrounds**: Premium users select a Concept for their profile background via `Profile.selected_background`. Re-pointed during `absorb()`.
- **Review Hub**: Uses `Concept.slug` for URL routing to game pages (ratings, reviews, discussions).

## Gotchas and Pitfalls

- **Forgetting to update `absorb()` causes data loss.** Any new model with a FK, M2M, or other relationship to Concept MUST have its migration logic added to `absorb()`. When a concept is reassigned and the old one becomes orphaned, `absorb()` runs and then the orphan is deleted. Any relationships not handled by `absorb()` will cascade-delete or become null with no recovery. The "Concept Model: Critical `absorb()` Method" checklist in the project [CLAUDE.md](../../CLAUDE.md) tracks every relationship currently handled and must be updated alongside the code.

- **`concept_lock` prevents sync from overwriting concepts.** When `concept_lock` is True on a Game, `add_concept()` returns immediately without any changes. This is used by admins to manually assign concepts that differ from what the PSN API returns. If a locked game's concept needs changing, the lock must be explicitly removed first.

- **`concept_stale` is a re-sync trigger, not a data quality flag.** It signals that the game's concept should be re-looked-up on the next sync pass. It is checked in `update_profile_game_with_title_stats()` and cleared by `add_concept()`. Setting it does not invalidate any caches or user data.

- **Stub concepts (`PP_*`) are intended to be temporary.** Games that fail PSN API lookup get a default concept so the system can function. These should be re-resolved on subsequent syncs. `update_profile_game_with_title_stats()` returns False for `PP_` concepts so they get queued for concept refresh.

- **ConceptTrophyGroup merge order matters.** In `absorb()`, CTGs must be merged BEFORE Reviews. Reviews have a FK to ConceptTrophyGroup, so the target CTG must exist on `self` before a review can be re-pointed. Duplicate CTGs left on the orphan cascade-delete with it, which also deletes any duplicate reviews that were intentionally skipped.

- **`add_title_id()` uses optimistic locking.** The `select_for_update(nowait=True)` call will raise `OperationalError` if another worker holds the lock. The `@retry` decorator handles this with 3 attempts at 0.5-second intervals. This is necessary because multiple sync workers can process different games that share the same concept simultaneously.

- **Redis counter for default concept IDs can drift.** If Redis is flushed or restarted, the `pp_concept_counter` key disappears. On next use, `create_default_concept()` re-initializes from the database max. The `SET NX` ensures only one worker initializes it, but there is a brief window where multiple workers could race on initialization.

- **Slug collisions for Asian-character titles.** If `slugify()` produces an empty string (common for CJK titles), the fallback `"concept-{concept_id}"` is used. This means the URL is less human-readable but still functional and unique.

- **`cover_url` vs `bg_url` are not interchangeable.** `Concept.get_cover_url()` (exposed as the `cover_url` property) returns a portrait image for display in square/portrait containers. Priority: `concept_icon_url` (PSN MASTER) > trusted IGDB cover. `bg_url` is deliberately **excluded** from this chain because it's landscape art (GAMEHUB_COVER_ART) and crops badly in portrait containers. If you want the landscape background specifically (e.g. share-card backdrops), use `concept.bg_url` directly.

- **`absorb()` calls `save()` multiple times.** The method saves `self` up to 2 times (for `family` and `title_ids`). Each is a targeted `save(update_fields=[...])` to avoid overwriting concurrent changes, but the method is not wrapped in an explicit transaction. The caller (`add_concept()`) also does not wrap the absorb-then-delete sequence in a transaction, relying on Django's ATOMIC_REQUESTS or the caller's context.

## Management Commands

| Command | Purpose | Usage |
|---------|---------|-------|
| `backfill_default_concepts` | Create stub concepts for games that have no concept assigned | `python manage.py backfill_default_concepts [--dry-run] [--batch-size 50]` |
| `backfill_game_families_from_igdb` | One-shot historical pass: populate `GameFamily` records from existing accepted IGDB matches. Live path (`_apply_enrichment`) handles new matches automatically. | `python manage.py backfill_game_families_from_igdb [--dry-run]` |

## Related Docs

- [Gamification](gamification.md): Badge stages reference Concepts via `Stage.concepts` M2M
- [Review Hub](../features/review-hub.md): Uses `Concept.slug` for game-scoped ratings, reviews, and discussions
- [IGDB Integration](igdb-integration.md): Per-concept enrichment via `IGDBMatch`, `ConceptCompany`, `ConceptGenre`, `ConceptTheme`, and `ConceptEngine`
- [Roadmap System](../features/roadmap-system.md): 1:1 with Concept; replaced the legacy checklist system on game detail pages
