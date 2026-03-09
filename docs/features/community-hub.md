# Community Reviews & Ratings Hub

A Steam-inspired community review system for PlatPursuit. Users can write thumbs-up/thumbs-down reviews with markdown text, vote reviews as helpful/funny, reply to reviews, and rate DLC packs independently. Everything lives on a dedicated Community Hub page at `/community/<concept-slug>/` with tab navigation per trophy group (base game + each DLC).

**Status**: Phases 1-5 complete (models, services, management commands, API views, page view + templates). Phase 6 (admin moderation) is next.

## Architecture Overview

The Community Hub extends the existing rating system with full-text reviews, voting, and DLC-aware grouping. The key architectural addition is `ConceptTrophyGroup`, which bridges game-level `TrophyGroup` records to concept-level groups. This enables per-DLC ratings and reviews without modifying the trophy sync pipeline.

Reviews are separate from ratings by design. Ratings keep the platinum requirement (high barrier, numeric scores). Reviews have a lower barrier (just own the game for base, 1+ earned trophy for DLC) and use thumbs up/down plus markdown text. This separation means a user can rate without reviewing and vice versa.

The review feed uses **client-side rendering** from JSON API responses (not server-rendered partials) for infinite scroll performance. All write operations go through `ReviewService` which returns `(result, error)` tuples following the established service pattern.

## File Map

| File | Purpose |
|------|---------|
| `trophies/models.py` | 6 new models: ConceptTrophyGroup, Review, ReviewVote, ReviewReply, ReviewReport, ReviewModerationLog |
| `trophies/services/review_service.py` | Full CRUD + voting + replies + reporting + stats (641 lines) |
| `trophies/services/concept_trophy_group_service.py` | Sync, access checks, mismatch detection (346 lines) |
| `trophies/services/rating_service.py` | Extended with DLC group rating methods (232 lines) |
| `trophies/management/commands/backfill_concept_slugs.py` | One-time slug generation for all Concepts (88 lines) |
| `trophies/management/commands/backfill_concept_trophy_groups.py` | Trophy group sync + mismatch auditing (292 lines) |
| `notifications/models.py` | `review_reply` and `review_milestone` notification types |
| `trophies/token_keeper.py` | Sync hook for `ConceptTrophyGroupService.sync_for_concept()` |

| `api/review_views.py` | 8 view classes covering 12 API endpoints (832 lines) |
| `trophies/views/community_views.py` | `CommunityHubView` page view |
| `static/js/community-hub.js` | Frontend logic (CommunityHub class) |
| `templates/trophies/community_hub.html` | Main page template |
| `templates/trophies/partials/community/` | 7 partial templates (header, tabs, banner, ratings, form, your review, feed) |

### Files To Create (Phases 6-8)
| File | Purpose |
|------|---------|
| `trophies/views/admin_views.py` | Review moderation views |

## Data Model

### Concept.slug
- `SlugField(max_length=300, unique, nullable)`: URL-safe identifier for hub pages
- Backfilled via `backfill_concept_slugs` command (1,614 concepts slugged on prod)
- Falls back to `concept-{id}` for untitled concepts

### ConceptTrophyGroup
- FK to `Concept`, stores `trophy_group_id` (integer from PSN API)
- `unique_together(concept, trophy_group_id)`: One CTG per DLC per concept
- `name`, `icon_url`: Display data grabbed from first game stack during sync
- `is_base_game` (bool): Marks the default base game group
- Created/updated by `ConceptTrophyGroupService.sync_for_concept()` during trophy sync

### Review
- FK to `Profile` (author), `Concept`, nullable FK to `ConceptTrophyGroup`
- `body` (50-8000 chars, markdown), `recommended` (bool: thumbs up/down)
- Denormalized counts: `helpful_count`, `funny_count`, `reply_count`
- `is_deleted` (soft delete), `created_at`, `updated_at`

### ReviewVote
- FK to `Review` + `Profile`, `vote_type` (helpful/funny)
- `unique_together(review, profile, vote_type)`

### ReviewReply
- FK to `Review` + `Profile` (author)
- Plain text only (max 2000 chars), no nesting (single-level)
- `is_deleted` (soft delete)

### ReviewReport / ReviewModerationLog
- Follow existing `CommentReport` / `ModerationLog` patterns

### UserConceptRating Extension
- Added nullable FK to `ConceptTrophyGroup` for DLC-specific ratings
- `unique_together` now includes `concept_trophy_group`
- Base game ratings: `concept_trophy_group__isnull=True` (backward compatible)

## Key Flows

### Trophy Group Sync

1. Token Keeper completes trophy sync for a game
2. If `game.concept` exists, calls `ConceptTrophyGroupService.sync_for_concept(concept)`
3. Service iterates all games in the concept, discovers `TrophyGroup` records
4. Deduplicates by `trophy_group_id` (first name/icon wins across stacks)
5. Creates/updates `ConceptTrophyGroup` via `update_or_create`

### Review Creation (Phase 4+)

1. User submits review via POST to `/api/v1/reviews/<concept_id>/group/<group_id>/create/`
2. API validates: concept not shovelware, user meets access requirement
3. `ReviewService.create_review()`: validates body length, banned words, creates Review
4. Returns review data as JSON for client-side rendering

### Vote Toggle

1. User clicks helpful/funny button
2. POST to `/api/v1/reviews/<review_id>/vote/`
3. `ReviewService.toggle_vote()`: creates or deletes ReviewVote
4. Updates denormalized count via `F()` expression
5. Milestone notification at thresholds [5, 10, 25, 50] helpful votes

### Access Requirements

| Action | Base Game | DLC |
|--------|-----------|-----|
| Rate | Platinum earned | 100% DLC trophies |
| Review | ProfileGame exists | 1+ earned trophy in group |
| Vote/Reply | Authenticated | Authenticated |

## API Endpoints (Phase 4, Implemented)

| Method | Path | Auth | Purpose |
|--------|------|------|---------|
| GET | `/api/v1/reviews/<concept_id>/group/<group_id>/` | No | List reviews (paginated, sortable) |
| POST | `/api/v1/reviews/<concept_id>/group/<group_id>/create/` | Yes | Create review |
| GET | `/api/v1/reviews/<review_id>/` | No | Single review detail |
| PUT | `/api/v1/reviews/<review_id>/` | Yes | Edit own review |
| DELETE | `/api/v1/reviews/<review_id>/` | Yes | Delete own review |
| POST | `/api/v1/reviews/<review_id>/vote/` | Yes | Toggle helpful/funny |
| POST | `/api/v1/reviews/<review_id>/report/` | Yes | Report review |
| GET | `/api/v1/reviews/<review_id>/replies/` | No | List replies |
| POST | `/api/v1/reviews/<review_id>/replies/` | Yes | Create reply |
| PUT | `/api/v1/reviews/replies/<reply_id>/` | Yes | Edit reply |
| DELETE | `/api/v1/reviews/replies/<reply_id>/` | Yes | Delete reply |
| POST | `/api/v1/reviews/<concept_id>/group/<group_id>/rate/` | Yes | DLC rating |

## Page View (Phase 5, Implemented)

**Route**: `/community/<slug>/` with optional `?group=<trophy_group_id>` query param.

**CommunityHubView** extends `DetailView` + `ProfileHotbarMixin` + `BackgroundContextMixin`. Uses Concept slug for URL lookup. Shovelware gate returns 404 if all games in concept are flagged.

**Layout**: Two-column on desktop (sidebar 33%, main 67%), stacked on tablet. Tab bar for trophy groups (hidden if only base game).

**Sidebar**: Recommendation banner (Steam-style percentage) + community ratings grid (reuses `community_ratings.html` visual pattern) + collapsible rating form.

**Main content**: Review form (if user can review), "Your Review" section (server-rendered with `render_markdown`), review feed (client-rendered from JSON API via InfiniteScroller-style IntersectionObserver).

**Key design decisions**:
- Review feed is **client-rendered** from JSON. API returns `body_html` (server-rendered markdown via `ChecklistService.process_markdown()`), so no client-side markdown library needed.
- Tab switching uses **full page reload** with `?group=<id>` query param for simplicity and bookmarkability.
- Rating form is **collapsible** (collapsed by default) in the sidebar.
- User's own review is a **separate section** above the feed (server-rendered), not part of the paginated feed.
- Game detail page links to Community Hub via a button below the header action bar.

## Integration Points

- [Concept Model](../architecture/concept-model.md): `Concept.absorb()` updated to handle Reviews, CTGs, and DLC ratings. CTGs merged before reviews so FK references survive.
- [Token Keeper](../architecture/token-keeper.md): Sync hook at ~line 1374 calls `sync_for_concept()` after trophy sync
- [Notification System](../architecture/notification-system.md): `review_reply` and `review_milestone` types
- [Comment System](comment-system.md): ReviewService follows CommentService patterns (CRUD, voting, reporting, sanitization)
- [Badge System](../architecture/badge-system.md): Recommendation stats cached at `review:recommend:{concept_id}:{group_id}` (30min TTL)

## Gotchas and Pitfalls

- **Base game rating filter**: Use `concept_trophy_group__isnull=True`, NOT FK lookup by trophy_group_id. Existing ratings have NULL for backward compatibility.
- **Do NOT backfill existing ratings**: Old `UserConceptRating` rows have `concept_trophy_group=NULL` for base game by design. Backfilling would break the filter logic.
- **Markdown for reviews, plain text for replies**: Reviews use `ChecklistService.process_markdown()` + `render_markdown` filter. Replies use `CommentService.sanitize_text()`.
- **Shovelware exclusion**: All games in a concept must be non-shovelware for the hub to be accessible. Enforced at both view and API level.
- **CTG deduplication across stacks**: `sync_for_concept()` uses a `seen` dict keyed by `trophy_group_id`. First name/icon found wins. The mismatch checker (`detect_mismatches()`) flags structural issues.
- **Reply count decrement**: Uses `Greatest(F('reply_count') - 1, Value(0))` for safe clamping (prevents negative counts).
- **Client-side rendering**: Review feed is JSON from API, not server-rendered partials. This is intentional for infinite scroll performance.

## Management Commands

| Command | Purpose | Usage |
|---------|---------|-------|
| `backfill_concept_slugs` | Generate URL slugs for all Concepts | `python manage.py backfill_concept_slugs [--dry-run] [--batch-size 100]` |
| `backfill_concept_trophy_groups` | Sync CTGs + audit mismatches | `python manage.py backfill_concept_trophy_groups [--dry-run] [--check-mismatches] [--collections-only]` |
| `redis_admin --flush-community` | Clear review/rating caches | `python manage.py redis_admin --flush-community` |

## Cache Keys

| Key Pattern | TTL | Purpose |
|-------------|-----|---------|
| `review:recommend:{concept_id}:{group_id}` | 30m | Recommendation percentage stats |
| `concept:averages:{concept_id}:group:{group_id}` | varies | DLC-specific rating averages |

## Related Docs

- [Concept Model](../architecture/concept-model.md): Concept.absorb() handles review/CTG migration
- [Comment System](comment-system.md): Pattern reference for CRUD, voting, reporting
- [Data Model](../architecture/data-model.md): Core model relationships
