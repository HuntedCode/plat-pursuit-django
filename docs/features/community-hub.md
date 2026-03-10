# Community Reviews & Ratings Hub

A Steam-inspired community review system for PlatPursuit. Users can write thumbs-up/thumbs-down reviews with markdown text, vote reviews as helpful/funny, reply to reviews, and rate DLC packs independently. The system spans three pages: a discovery landing at `/reviews/`, per-game detail pages at `/reviews/<slug>/`, and a Rate My Games wizard at `/reviews/rate-my-games/`.

**Status**: All phases complete (models, services, management commands, API views, page views + templates, admin moderation). Currently staff-only gated via `StaffRequiredMixin` for testing; remove when ready to go public.

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

| `api/review_views.py` | 10 view classes covering 14 API endpoints |
| `trophies/views/review_hub_views.py` | `ReviewHubLandingView`, `ReviewHubDetailView`, `RateMyGamesView` |
| `static/js/review-hub.js` | Detail page logic (ReviewHub class) |
| `static/js/review-hub-landing.js` | Landing page logic (recent feed with infinite scroll) |
| `static/js/rate-my-games.js` | Wizard logic (RateMyGamesWizard class) |
| `templates/trophies/review_hub.html` | Landing page template |
| `templates/trophies/review_hub_detail.html` | Detail page template |
| `templates/trophies/rate_my_games.html` | Rate My Games wizard template |
| `templates/trophies/partials/reviews/` | 8 partial templates (header, tabs, banner, ratings, form, your review, feed, trophy strip, user rating panel) |

| `trophies/views/game_views.py` | `_build_concept_context()` provides review/recommendation context for game detail community section |
| `templates/trophies/partials/game_detail/community_section.html` | Unified community card: recommendation banner, ratings grid, user review, Review Hub CTA |
| `trophies/views/admin_views.py` | `ReviewModerationView`, `ReviewModerationActionView`, `ReviewModerationLogView` |
| `templates/trophies/moderation/review_moderation.html` | Review moderation dashboard |
| `templates/trophies/moderation/review_moderation_log.html` | Review moderation action log |

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
- `ReviewModerationLog` includes `internal_notes` (private staff notes) and `ip_address` fields
- When a review is deleted via moderation, all other pending reports for that review are auto-resolved to `action_taken`

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

## API Endpoints

| Method | Path | Auth | Purpose |
|--------|------|------|---------|
| GET | `/api/v1/reviews/recent/` | No | Recent reviews feed (paginated, for landing page) |
| GET | `/api/v1/reviews/wizard/queue/` | Yes | Rate My Games queue (filters: unrated/unreviewed/both, queue_type: base/dlc) |
| GET | `/api/v1/reviews/<concept_id>/group/<group_id>/` | No | List reviews (paginated, sortable) |
| POST | `/api/v1/reviews/<concept_id>/group/<group_id>/create/` | Yes | Create review |
| POST | `/api/v1/reviews/<concept_id>/group/<group_id>/rate/` | Yes | Submit/update rating |
| GET | `/api/v1/reviews/<concept_id>/group/<group_id>/trophies/` | No | Condensed trophy list with earned status |
| GET | `/api/v1/reviews/<review_id>/` | No | Single review detail |
| PUT | `/api/v1/reviews/<review_id>/` | Yes | Edit own review |
| DELETE | `/api/v1/reviews/<review_id>/` | Yes | Delete own review |
| POST | `/api/v1/reviews/<review_id>/vote/` | Yes | Toggle helpful/funny |
| POST | `/api/v1/reviews/<review_id>/report/` | Yes | Report review |
| GET | `/api/v1/reviews/<review_id>/replies/` | No | List replies |
| POST | `/api/v1/reviews/<review_id>/replies/` | Yes | Create reply |
| PUT | `/api/v1/reviews/replies/<reply_id>/` | Yes | Edit reply |
| DELETE | `/api/v1/reviews/replies/<reply_id>/` | Yes | Delete reply |

## Page Views (Phase 5, Implemented)

All three page views are currently gated behind `StaffRequiredMixin` for testing. When ready to go public: remove `StaffRequiredMixin`, restore `LoginRequiredMixin` on `RateMyGamesView`, and remove `user.is_staff` checks from navbar.html and game_detail.html.

### Landing Page

**Route**: `/reviews/`

**ReviewHubLandingView** extends `StaffRequiredMixin` + `ProfileHotbarMixin` + `TemplateView`. Shows community stats (total reviews, ratings, reviewers), most-reviewed games grid, and an infinite-scroll recent reviews feed via `RecentReviewsView` API.

### Detail Page

**Route**: `/reviews/<slug>/` with optional `?group=<trophy_group_id>` query param.

**ReviewHubDetailView** extends `StaffRequiredMixin` + `ProfileHotbarMixin` + `BackgroundContextMixin` + `DetailView`. Uses Concept slug for URL lookup. Shovelware gate returns 404 if all games in concept are flagged.

**Layout**: Two-column on desktop (sidebar 33%, main 67%), stacked on tablet. Tab bar for trophy groups (hidden if only base game).

**Sidebar**: Recommendation banner (Steam-style percentage) + community ratings panel + collapsible user rating form + collapsible trophy list strip.

**Main content**: Review form (if user can review), "Your Review" section (server-rendered with `render_markdown`), review feed (client-rendered from JSON API via ZoomAwareObserver-based infinite scroll).

### Rate My Games Wizard

**Route**: `/reviews/rate-my-games/`

**RateMyGamesView** extends `StaffRequiredMixin` + `ProfileHotbarMixin` + `TemplateView`. Side-by-side wizard for quickly rating and reviewing platinumed games. Left panel shows game queue (base games and 100%-completed DLC), right panel shows rating form + review form with progress bar. Fetches game queue via `WizardQueueView` API.

**DLC filtering**: Only DLC groups where the user has 100% trophy completion appear in the wizard queue. This is enforced server-side in `_get_dlc_queue()` via bulk Trophy/EarnedTrophy aggregate queries.

### Key Design Decisions

- Review feed is **client-rendered** from JSON. API returns `body_html` (server-rendered markdown via `ChecklistService.process_markdown()`), so no client-side markdown library needed.
- Tab switching uses **full page reload** with `?group=<id>` query param for simplicity and bookmarkability.
- Rating form is **collapsible** (collapsed by default) in the sidebar.
- User's own review is a **separate section** above the feed (server-rendered), not part of the paginated feed.
- Game detail page shows a unified "Community" card with recommendation stats, ratings grid, user review preview, and Review Hub CTA (staff-only during testing).
- Review word-count progress bars use the shared `ReviewProgressTiers.updateWordProgress()` utility from `utils.js` across all three locations (new review form, edit review, inline edit).

## Integration Points

- [Concept Model](../architecture/concept-model.md): `Concept.absorb()` updated to handle Reviews, CTGs, and DLC ratings. CTGs merged before reviews so FK references survive.
- [Token Keeper](../architecture/token-keeper.md): Sync hook at ~line 1374 calls `sync_for_concept()` after trophy sync
- [Notification System](../architecture/notification-system.md): `review_reply` and `review_milestone` types
- [Comment System](comment-system.md): ReviewService follows CommentService patterns (CRUD, voting, reporting, sanitization)
- [Badge System](../architecture/badge-system.md): Recommendation stats cached at `review:recommend:{concept_id}:{group_id}` (30min TTL)

## Admin Moderation (Phase 6)

Three staff-only views mirror the comment moderation system:

| Route | View | Purpose |
|-------|------|---------|
| `/staff/review-moderation/` | `ReviewModerationView` | Dashboard with pending reports, status tabs, search/filter |
| `/staff/review-moderation/action/<report_id>/` | `ReviewModerationActionView` | POST handler for delete/dismiss/review actions |
| `/staff/review-moderation/log/` | `ReviewModerationLogView` | Full audit log with moderator/action/date filters |

### Moderation Actions
- **Delete Review**: Calls `ReviewService.delete_review()` (soft delete + cache invalidation + milestone recheck). Auto-resolves all sibling pending reports for the same review.
- **Dismiss Report**: Creates `ReviewModerationLog` entry, marks report as dismissed.
- **Mark Reviewed**: Creates `ReviewModerationLog` entry, marks report as reviewed (no action on the review itself).

All actions record `reason` and optional `internal_notes` (staff-only, not visible to users).

### Cross-links
Comment and review moderation pages cross-link to each other via header buttons.

## Game Detail Integration

The game detail page shows a single unified "Community" card (`community_section.html`) containing recommendation stats, ratings grid, user review preview, and a Review Hub CTA.

| File | Purpose |
|------|---------|
| `trophies/views/game_views.py` | `_build_concept_context()` fetches `recommendation_stats`, `review_count`, `user_review`, `can_review` for the base game CTG (staff-only) |
| `templates/trophies/partials/game_detail/community_section.html` | Unified community card with all sections |

**Sections within the card:**
1. **Recommendation banner** (staff-only): Thumbs up/down percentage with visual bar. Content inlined from `recommendation_banner.html` pattern to avoid card-within-card nesting.
2. **Ratings grid** (visible to everyone): 2x3 grid showing Difficulty, Grindiness, Hours, Fun, Overall with color-coded progress bars.
3. **User review** (staff-only): Truncated preview of user's review with recommendation badge, or CTA to write a review if eligible.
4. **Review Hub CTA** (staff-only): Full-width button linking to the Review Hub detail page.

**Rating form**: Removed from the game detail page. Users submit ratings via the Review Hub's rating panel instead. The `post()` method on `GameDetailView` has been removed.

When going public: remove the `user.is_staff` checks in the template and the `if self.request.user.is_staff` guard in `_build_concept_context()`.

## Gotchas and Pitfalls

- **Base game rating filter**: Use `concept_trophy_group__isnull=True`, NOT FK lookup by trophy_group_id. Existing ratings have NULL for backward compatibility.
- **Do NOT backfill existing ratings**: Old `UserConceptRating` rows have `concept_trophy_group=NULL` for base game by design. Backfilling would break the filter logic.
- **Markdown for reviews, plain text for replies**: Reviews use `ChecklistService.process_markdown()` + `render_markdown` filter. Replies use `CommentService.sanitize_text()`.
- **Shovelware exclusion**: All games in a concept must be non-shovelware for the hub to be accessible. Enforced at both view and API level.
- **CTG deduplication across stacks**: `sync_for_concept()` uses a `seen` dict keyed by `trophy_group_id`. First name/icon found wins. The mismatch checker (`detect_mismatches()`) flags structural issues.
- **Reply count decrement**: Uses `Greatest(F('reply_count') - 1, Value(0))` for safe clamping (prevents negative counts).
- **Client-side rendering**: Review feed is JSON from API, not server-rendered partials. This is intentional for infinite scroll performance.
- **ZoomAwareObserver**: Infinite scroll sentinels use `ZoomAwareObserver` (not raw `IntersectionObserver`) to work correctly when the page is scaled via `ZoomScaler` on sub-768px screens.
- **Staff-only gating**: All three page views (`ReviewHubLandingView`, `ReviewHubDetailView`, `RateMyGamesView`) use `StaffRequiredMixin`. The navbar link and game detail community section review portions are wrapped in `{% if user.is_staff %}`. Remove these when going public.
- **Rating form removed from game detail**: The `GameDetailView.post()` method and `UserConceptRatingForm` usage were removed. Ratings are submitted exclusively via the Review Hub's rating panel.

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
