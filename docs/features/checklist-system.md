# Checklist System (Guides)

The checklist system is Platinum Pursuit's community-driven guide authoring platform. Users create structured, interactive checklists (publicly branded as "Guides") tied to game Concepts, organizing trophy-hunting steps into sections and items that other users can track progress against. The system enforces a draft/published lifecycle: authors build checklists freely in draft mode, and once published, structural changes are locked to protect the progress data of anyone already tracking against the guide. Checklists support five item types (regular items, sub-headers, images, text areas, and trophy links), per-item and per-section progress tracking with earned-trophy auto-detection, upvote-based community ranking, markdown-rendered text areas, image uploads at every level (checklist thumbnail, section thumbnail, inline images), and a full moderation pipeline with reporting, banned-word filtering, and soft deletion.

---

## Architecture Overview

The system follows a three-layer architecture consistent with the rest of the codebase:

1. **Service Layer** (`ChecklistService`): All business logic lives here. Permission checks, text sanitization, markdown processing, image validation, CRUD operations, voting, progress tracking, reporting, and cache invalidation. Every mutating operation returns a `(result, error)` tuple, letting callers handle success/failure uniformly.

2. **API Layer** (DRF `APIView` classes): Thin REST endpoints that deserialize requests, delegate to `ChecklistService`, and serialize responses. Rate-limited via `django-ratelimit`. Supports both JSON and server-side-rendered HTML card responses for the concept listing endpoint.

3. **Page Views** (Django class-based views): Traditional server-rendered pages for detail, edit, browse, "My Guides", and "My Shareables". These views build rich context (trophy maps, earned-trophy detection, section completion counts) and delegate to the service layer for data retrieval.

The data model is Concept-centric: a Checklist belongs to a Concept (not a specific Game), so it applies across all platform variants of a title. An optional `selected_game` FK allows trophy-type items to link to specific trophies within one of the Concept's games.

### Terminology Note

Internally the codebase uses "checklist" everywhere (models, services, API paths). The user-facing brand is "Guide" (URL paths use `/guides/`, page titles say "Guide"). Both terms refer to the same entity.

---

## File Map

| File | Purpose |
|---|---|
| `trophies/models.py` (lines ~2412-2854) | Checklist, ChecklistSection, ChecklistItem, ChecklistVote, UserChecklistProgress, ChecklistReport models |
| `trophies/managers.py` (lines ~623-852) | ChecklistQuerySet and ChecklistManager with chainable filters, sorts, and annotation helpers |
| `trophies/services/checklist_service.py` (~1,935 lines) | All business logic: permissions, sanitization, markdown, images, CRUD, voting, progress, reporting, caching |
| `trophies/views/checklist_views.py` (~590 lines) | Page views: ChecklistDetailView, ChecklistCreateView, ChecklistEditView, MyChecklistsView, MyShareablesView, BrowseGuidesView |
| `api/checklist_views.py` (~1,491 lines) | REST API endpoints for all checklist operations |
| `api/serializers.py` (Checklist* classes) | DRF serializers for request validation and response formatting |
| `api/urls.py` (lines ~143-178) | URL routing for all `/api/v1/checklists/` endpoints |
| `plat_pursuit/urls.py` (lines ~68-75) | Page URL routing for `/guides/`, `/my-guides/`, etc. |
| `static/js/checklist.js` (~3,400 lines) | Client-side logic for the edit and detail views |
| `templates/trophies/checklist_detail.html` | Detail/reader view template |
| `templates/trophies/checklist_edit.html` | Author edit view template |
| `templates/trophies/my_checklists.html` | "My Guides" hub with tabs for drafts, published, in-progress |
| `templates/trophies/guides_browse.html` | Public browse/search page |
| `templates/partials/checklist_card.html` | Reusable card partial for listing pages |

---

## Data Model

### Checklist

The root entity. Belongs to a Concept, authored by a Profile.

| Field | Type | Notes |
|---|---|---|
| `concept` | FK to Concept | CASCADE. The game concept this checklist covers |
| `selected_game` | FK to Game (nullable) | SET_NULL. Specific game for trophy-type items |
| `profile` | FK to Profile | CASCADE. The author |
| `title` | CharField(200) | Sanitized plain text |
| `description` | TextField(2000) | Sanitized plain text |
| `thumbnail` | ImageField | Upload path: `checklists/thumbnails/`, max 5MB |
| `status` | CharField | `'draft'` or `'published'` |
| `upvote_count` | PositiveIntegerField | Denormalized. Updated via `F()` expressions on vote toggle |
| `progress_save_count` | PositiveIntegerField | Denormalized. Incremented when a new user starts tracking |
| `view_count` | PositiveIntegerField | Denormalized. Populated by `track_page_view()` |
| `published_at` | DateTimeField (nullable) | Set on first publish |
| `is_deleted` / `deleted_at` | Soft delete fields | `soft_delete()` method preserves data |

**Key property:** `total_items` counts only trackable items (`item_type__in=['item', 'trophy']`), excluding sub-headers, images, and text areas.

**Indexes:** Composite indexes on `(concept, status, -upvote_count)`, `(profile, -created_at)`, `(profile, status)`, and `(is_deleted, created_at)`.

### ChecklistSection

Groups items within a checklist. Ordered by the `order` field.

| Field | Type | Notes |
|---|---|---|
| `checklist` | FK to Checklist | CASCADE |
| `subtitle` | CharField(200) | Section header text |
| `description` | TextField(1000) | Optional description |
| `thumbnail` | ImageField (nullable) | Upload path: `checklists/sections/`, max 5MB |
| `order` | PositiveIntegerField | Display order within checklist |

**Properties:** `item_count` (trackable items only), `total_entry_count` (all entries including sub-headers).

### ChecklistItem

Individual entry within a section. Five types with different behaviors.

| Field | Type | Notes |
|---|---|---|
| `section` | FK to ChecklistSection | CASCADE |
| `text` | CharField(2000) | Item description, caption, or text area content |
| `item_type` | CharField | One of: `'item'`, `'sub_header'`, `'image'`, `'text_area'`, `'trophy'` |
| `trophy_id` | IntegerField (nullable) | Links to a Trophy record. Required for `'trophy'` type |
| `image` | ImageField (nullable) | Upload path: `checklists/items/`, required for `'image'` type |
| `order` | PositiveIntegerField | Display order within section |

**Item type behaviors:**

| Type | Checkable | Text Required | Trophy Link | Image |
|---|---|---|---|---|
| `item` | Yes | Yes | Optional | No |
| `sub_header` | No | Yes | No | No |
| `image` | No | Optional (caption) | No | Required |
| `text_area` | No | Yes (markdown) | No | No |
| `trophy` | Yes | Auto-filled from trophy name | Required | No |

**Model-level validation** in `save()`: image items must have an image file, trophy items must have a `trophy_id`, text area items must have non-empty text. Non-image types have their `image` field cleared automatically. On `delete()`, associated image files are cleaned up.

### ChecklistVote

One upvote per profile per checklist. Follows the CommentVote pattern.

| Field | Type | Notes |
|---|---|---|
| `checklist` | FK to Checklist | CASCADE |
| `profile` | FK to Profile | CASCADE |
| `created_at` | DateTimeField | Auto-set |

**Constraint:** `unique_together = ['checklist', 'profile']`.

### UserChecklistProgress

Tracks which items a user has completed on a checklist.

| Field | Type | Notes |
|---|---|---|
| `profile` | FK to Profile | CASCADE |
| `checklist` | FK to Checklist | CASCADE |
| `completed_items` | JSONField (list) | Array of completed ChecklistItem IDs (integers) |
| `items_completed` | PositiveIntegerField | Denormalized count |
| `total_items` | PositiveIntegerField | Denormalized snapshot |
| `progress_percentage` | FloatField | Denormalized percentage |
| `last_activity` | DateTimeField | Auto-updated |

**Constraint:** `unique_together = ['profile', 'checklist']`.

**Methods:** `update_progress()` recalculates all denormalized fields and saves. `mark_item_complete(item_id)` / `mark_item_incomplete(item_id)` append/remove from `completed_items` and call `update_progress()`.

**Important:** Item IDs in `completed_items` are always integers (coerced via `int(item_id)` in the service layer) for consistent JSONField comparison.

### ChecklistReport

Moderation report. One report per profile per checklist.

| Field | Type | Notes |
|---|---|---|
| `checklist` | FK to Checklist | CASCADE |
| `reporter` | FK to Profile | CASCADE |
| `reason` | CharField | One of: `spam`, `inappropriate`, `misinformation`, `plagiarism`, `other` |
| `details` | TextField(500) | Additional context |
| `status` | CharField | `pending`, `reviewed`, `dismissed`, `action_taken` |
| `reviewed_at` / `reviewed_by` / `admin_notes` | Moderation audit fields |

**Constraint:** `unique_together = ['checklist', 'reporter']`.

---

## Key Flows

### 1. Create

1. User navigates to a game detail page and clicks "Create Guide".
2. `ChecklistCreateView` (page view) validates permissions (logged in, linked PSN, guidelines agreed).
3. Calls `ChecklistService.create_checklist()` which sanitizes inputs, checks banned words, and creates a draft Checklist.
4. The view sets `selected_game` to the game the user came from, then redirects to the edit page.
5. The checklist starts in `status='draft'` with no sections.

### 2. Edit (Draft Mode)

All structural editing happens through the API while the checklist is in draft status.

**Metadata editing** (title, description): Allowed on both draft and published checklists via `ChecklistService.update_checklist()`.

**Structural editing** (sections, items): Only allowed on drafts. `can_edit_checklist_structure()` returns false for published checklists, protecting progress data.

**Section operations:**
- `add_section()`: Creates with auto-calculated order (appends to end).
- `update_section()`: Updates subtitle, description, or order.
- `delete_section()`: Hard-deletes section and cascades to items. Calls `_cleanup_progress_for_deleted_items()` to remove orphaned item IDs from all user progress records.
- `reorder_sections()`: Accepts an ordered list of section IDs, validates they match existing sections, updates order values.

**Item operations:**
- `add_item()`: Supports all five item types with type-specific validation. Images are optimized via `optimize_image()` (max 1200x1200).
- `add_trophy_item()`: Validates the trophy belongs to the checklist's `selected_game`, auto-fills text from trophy name.
- `bulk_add_items()`: Atomic batch creation with progressive success (creates valid items even if some fail validation). Returns both created items and failure details.
- `bulk_update_items()`: Batch text/type updates for multiple items in a single transaction.
- `delete_item()`: Hard-deletes and cleans up progress records.
- `reorder_items()`: Same pattern as section reordering.

**Trophy integration:**
- `set_checklist_game()`: Selects which Game (within the Concept) provides trophies. Cannot change if trophy items already exist with a different game.
- `get_available_trophies_for_checklist()`: Returns all trophies for the selected game, annotated with `is_used` (already in the checklist) and `is_base_game` (base game vs DLC).
- `validate_trophy_for_checklist()`: Ensures the trophy belongs to the selected game.

**Image uploads** (allowed on both draft and published):
- `update_checklist_thumbnail()`: Validates format/size/dimensions, optimizes, replaces existing.
- `update_section_thumbnail()`: Same pattern at section level.
- Inline image items are created via `add_item(item_type='image')`.

### 3. Publish

1. Author calls the publish endpoint.
2. `ChecklistService.publish_checklist()` validates:
   - Checklist is in draft status.
   - At least one section exists.
   - Every section has at least one item.
3. Status changes to `'published'`, `published_at` is set.
4. Concept cache is invalidated.
5. Once published, structural changes are blocked. Title, description, and images can still be edited.

**Unpublish:** Reverts status to `'draft'`, re-enabling structural editing. The `get_tracker_count()` method lets the UI warn the author how many users are currently tracking progress before they unpublish.

### 4. Voting

- Toggle-based: `ChecklistService.toggle_vote()` creates or deletes a `ChecklistVote` record.
- `upvote_count` on the Checklist is updated via `F()` expression (race-safe).
- After refresh from DB, the new count is returned to the client.
- Self-voting is blocked. Only published, non-deleted checklists can be voted on.
- On new upvote, `check_all_milestones_for_user()` is called for the checklist author (milestone integration for upvote count thresholds).

### 5. Progress Tracking

**Single item toggle:**
1. `toggle_item_progress()` validates permissions and item existence.
2. Only `'item'` and `'trophy'` types can be marked complete; sub-headers, images, and text areas are excluded.
3. Gets or creates a `UserChecklistProgress` record. On first creation, increments `progress_save_count` on the checklist.
4. Toggles the item ID in/out of `completed_items`, calls `update_progress()`.
5. **Trophy sibling sync:** If the toggled item is a trophy type, finds all other items in the checklist with the same `trophy_id` and syncs their completion state. This handles the case where the same trophy appears in multiple sections.

**Section bulk toggle:**
1. `bulk_update_section_progress()` marks all checkable items in a section as complete or incomplete.
2. Also syncs trophy siblings across sections.

**Earned trophy auto-detection:**
Both the detail page view and the API progress responses calculate "adjusted progress" that merges manual completions with earned trophies. The flow:
1. Query all trophy-type items in the checklist to build an `{item_id: trophy_id}` mapping.
2. Query `EarnedTrophy` for the viewing user to find which trophies they have earned.
3. Map earned trophy IDs back to checklist item IDs.
4. Union the manually completed set with the earned-trophy set for the final count.

This means trophies the user has already earned on PSN automatically appear checked, even if the user never manually toggled them.

**Progress cleanup on item deletion:**
`_cleanup_progress_for_deleted_items()` iterates all `UserChecklistProgress` records for the checklist, removes any deleted item IDs from `completed_items`, and recalculates percentages. This prevents stale references.

### 6. Sharing and Browse

**Browse page** (`BrowseGuidesView`): Public `ListView` with search (title, description, author username), filtering by game, and three sort modes (popular/recent/oldest). Paginated at 24 per page.

**Concept listing** (`ChecklistListView` API): Returns published checklists for a concept. Supports JSON or HTML output. The HTML mode renders `checklist_card.html` partials server-side with batched queries for vote status, section counts, and author platinum status to avoid N+1.

**My Guides** (`MyChecklistsView`): Three tabs showing drafts, published, and in-progress (other people's checklists the user is tracking).

**My Shareables** (`MyShareablesView`): Hub for generating share images for platinum trophies, grouped by year.

### 7. Markdown Processing

`ChecklistService.process_markdown()` converts text-area item content to styled HTML:

1. **Pre-processing:** Converts `__text__` to `<u>text</u>` for underline support (avoids conflict with markdown bold).
2. **Markdown rendering:** Uses `markdown2` with extras: `strike`, `fenced-code-blocks`, `cuddled-lists`, `break-on-newline`.
3. **HTML sanitization:** `bleach.clean()` with a strict allowlist of tags (`p`, `br`, `strong`, `em`, `u`, `del`, `s`, `ul`, `ol`, `li`, `blockquote`, `code`, `pre`, `a`) and attributes. Only `http`/`https` link protocols are allowed.
4. **Style injection:** Regex post-processing adds Tailwind classes to links (opens in new tab with `noopener noreferrer`), blockquotes, lists, and paragraphs.
5. **Fallback:** If `markdown2` is unavailable or processing fails, returns HTML-escaped text in a `<p>` tag.

A dedicated `MarkdownPreviewView` API endpoint lets the editor show a live preview before saving.

---

## API Endpoints

All endpoints are under `/api/v1/`. Authentication is via SessionAuthentication or TokenAuthentication unless noted.

### Checklist CRUD

| Method | Path | Auth | Rate Limit | Description |
|---|---|---|---|---|
| GET | `/checklists/concept/<concept_id>/` | Optional | None | List published checklists for a concept. Supports `sort`, `limit`, `offset`, `output` (json/html) params |
| POST | `/checklists/concept/<concept_id>/create/` | Required | 10/min | Create a new draft checklist |
| GET | `/checklists/<checklist_id>/` | Optional | None | Get checklist detail (drafts: author-only) |
| PATCH | `/checklists/<checklist_id>/` | Required | None | Update title/description |
| DELETE | `/checklists/<checklist_id>/` | Required | None | Soft delete (author or admin) |

### Publish / Unpublish

| Method | Path | Auth | Description |
|---|---|---|---|
| GET | `/checklists/<checklist_id>/publish/` | Required | Get publish status and tracker count |
| POST | `/checklists/<checklist_id>/publish/` | Required | Publish a draft |
| DELETE | `/checklists/<checklist_id>/publish/` | Required | Unpublish (revert to draft) |

### Voting and Reporting

| Method | Path | Auth | Rate Limit | Description |
|---|---|---|---|---|
| POST | `/checklists/<checklist_id>/vote/` | Required | 60/min | Toggle upvote |
| POST | `/checklists/<checklist_id>/report/` | Required | 5/hr | Submit moderation report |

### Progress

| Method | Path | Auth | Rate Limit | Description |
|---|---|---|---|---|
| GET | `/checklists/<checklist_id>/progress/` | Required | None | Get user's progress on a checklist |
| POST | `/checklists/<checklist_id>/progress/toggle/<item_id>/` | Required | None | Toggle single item completion |
| POST | `/checklists/<checklist_id>/sections/<section_id>/bulk-progress/` | Required | 30/min | Bulk check/uncheck all items in a section |

### Sections

| Method | Path | Auth | Description |
|---|---|---|---|
| GET | `/checklists/<checklist_id>/sections/` | Required | List sections with items |
| POST | `/checklists/<checklist_id>/sections/` | Required | Add a section |
| PATCH | `/checklists/<checklist_id>/sections/<section_id>/` | Required | Update section subtitle/description/order |
| DELETE | `/checklists/<checklist_id>/sections/<section_id>/` | Required | Delete section (cascades to items) |
| POST | `/checklists/<checklist_id>/sections/reorder/` | Required | Reorder sections by ID list |

### Items

| Method | Path | Auth | Rate Limit | Description |
|---|---|---|---|---|
| GET | `/checklists/sections/<section_id>/items/` | Required | None | List items in a section |
| POST | `/checklists/sections/<section_id>/items/` | Required | None | Add a single item (any type) |
| POST | `/checklists/sections/<section_id>/items/bulk/` | Required | 10/min | Bulk create items (progressive success) |
| POST | `/checklists/sections/<section_id>/items/image/` | Required | 60/hr | Create inline image item |
| POST | `/checklists/sections/<section_id>/items/reorder/` | Required | None | Reorder items by ID list |
| POST | `/checklists/<checklist_id>/items/bulk-update/` | Required | 10/min | Bulk update item text/types (max 200 per request) |
| PATCH | `/checklists/items/<item_id>/` | Required | None | Update single item |
| DELETE | `/checklists/items/<item_id>/` | Required | None | Delete single item |

### Images

| Method | Path | Auth | Rate Limit | Description |
|---|---|---|---|---|
| POST | `/checklists/<checklist_id>/image/` | Required | 10/hr | Upload/replace checklist thumbnail |
| DELETE | `/checklists/<checklist_id>/image/` | Required | None | Remove checklist thumbnail |
| POST | `/checklists/sections/<section_id>/image/` | Required | 30/hr | Upload/replace section thumbnail |
| DELETE | `/checklists/sections/<section_id>/image/` | Required | None | Remove section thumbnail |

### Trophy Integration

| Method | Path | Auth | Description |
|---|---|---|---|
| POST | `/checklists/<checklist_id>/select-game/` | Required | Set the selected game for trophy items |
| GET | `/checklists/<checklist_id>/available-trophies/` | Required | Get available trophies with usage/group info |

### User-Specific

| Method | Path | Auth | Description |
|---|---|---|---|
| GET | `/checklists/my-drafts/` | Required | Get user's draft checklists |
| GET | `/checklists/my-published/` | Required | Get user's published checklists |
| GET | `/checklists/my-progress/` | Required | Get checklists user is tracking |

### Markdown

| Method | Path | Auth | Description |
|---|---|---|---|
| POST | `/markdown/preview/` | Required | Render markdown to HTML for live preview |

---

## Integration Points

### Concept System
- Checklists belong to Concepts (FK), not Games. This means a single checklist covers all regional/platform variants of a title.
- `Concept.absorb()` migrates checklists (along with their sections, items, votes, reports, and user progress) when concepts are merged. This is critical to update whenever new Concept relationships are added.
- Cache invalidation uses the key pattern `checklists:concept:{concept_id}`.

### Comment System
- Checklists have their own comment threads. Comments are filtered by `concept + checklist_id` in the Comment model.
- The `comment_count` is fetched directly in `ChecklistDetailView.get_context_data()`.
- Banned-word checking delegates to `CommentService.check_banned_words()` for shared wordlist.

### Trophy System
- Trophy-type items link to `Trophy` records via `trophy_id` (integer FK, not a formal Django FK).
- The detail view pre-fetches Trophy and TrophyGroup objects into context maps to avoid N+1 queries in templates.
- Earned trophy auto-detection queries `EarnedTrophy` to auto-check items for trophies the user has already earned on PSN.

### Milestone System
- When a user receives an upvote on their checklist, `check_all_milestones_for_user()` is called with `criteria_type='checklist_upvotes'` for the checklist author.

### Page View Tracking
- Published checklist detail views are tracked via `track_page_view('guide', checklist.id, request)`.
- Edit page views tracked as `'guide_edit'`.
- Browse page tracked as `'guides_browse'`.

### Image Pipeline
- All uploaded images go through `ChecklistService.validate_image()` (format whitelist: JPEG, PNG, WEBP, GIF; max 3840px per side; configurable size limit per context).
- Images are optimized via `trophies.image_utils.optimize_image()` before storage.
- Checklist thumbnails: max 1200x1200. Section thumbnails: max 600x600. Inline images: max 1200x1200.
- Old images are deleted from storage before replacement.

### Homepage Showcase
- The homepage's checklists showcase section (from `featured_checklists.py` service) pulls from published checklists.

---

## Gotchas and Pitfalls

### Structural Lock on Published Checklists
Once a checklist is published, `can_edit_checklist_structure()` returns `False`. Sections and items cannot be added, edited, reordered, or deleted. This is by design: users may be tracking progress, and structural changes would corrupt their `completed_items` arrays. Authors must unpublish first to make structural changes, which the UI warns about with a tracker count.

### Trophy ID is an Integer, Not a FK
`ChecklistItem.trophy_id` is a plain `IntegerField`, not a Django ForeignKey. This means there are no cascade protections or automatic joins. The detail view manually builds trophy/group maps with explicit queries. If a trophy is deleted from the database, the checklist item will retain a dangling `trophy_id`.

### Progress Completed Items Are Integer IDs in a JSONField
The `completed_items` JSONField stores a list of integers. The service layer coerces `item_id` to `int()` before comparison or storage to avoid type mismatches (JavaScript may send string IDs). Any code that reads or writes this field must ensure consistent integer types.

### Trophy Sibling Syncing
When the same `trophy_id` appears in multiple sections of a checklist (e.g., a trophy listed under both "Missable" and "Story"), toggling completion on one auto-syncs all siblings. Both `toggle_item_progress()` and `bulk_update_section_progress()` handle this. Missing this sync would cause inconsistent checkbox states across sections.

### Adjusted Progress vs. Raw Progress
There are two progress calculations:
1. **Raw progress** (`UserChecklistProgress` fields): Only counts manually checked items.
2. **Adjusted progress** (`_calculate_adjusted_progress()`): Merges manual checks with earned trophies from PSN sync data.

The detail view and API toggle/bulk-progress endpoints return adjusted progress. The raw progress endpoint (`/progress/ GET`) returns raw values. Any new code displaying progress should use the adjusted calculation.

### Bulk Create Uses Progressive Success
`bulk_add_items()` does not follow all-or-nothing semantics. If some items in a batch fail validation (banned words, empty text, etc.), the valid items are still created. The response includes both the created items and the failure details. Callers must handle partial success.

### Cache Invalidation is Concept-Scoped
`_invalidate_cache()` deletes the cache key `checklists:concept:{concept_id}`. This is called on checklist update, publish, unpublish, and delete. It is NOT called on section/item changes or progress updates because those do not affect the concept listing. If a new feature surfaces checklist data in a concept-level cache, it must be added to the invalidation path.

### Soft Delete Does Not Cascade
`Checklist.soft_delete()` only sets `is_deleted=True`. It does not touch sections, items, votes, or progress records. All queries filter with `is_deleted=False` to hide soft-deleted checklists. The data is preserved for audit purposes.

### Image Cleanup on Item Deletion
`ChecklistItem.delete()` is overridden to delete the associated image file from storage. If items are deleted via `QuerySet.delete()` (bulk), this override is bypassed and images will be orphaned. The service layer uses instance-level `delete()` to avoid this.

### Rate Limits by Endpoint
Several endpoints have rate limits. Key ones to be aware of:
- Checklist creation: 10/min per user
- Voting: 60/min per user
- Reporting: 5/hr per user
- Bulk item create/update: 10/min per user
- Checklist image upload: 10/hr per user
- Section image upload: 30/hr per user
- Inline image create: 60/hr per user

### Concept.absorb() Must Be Updated for New Relationships
Per the project-wide rule: any new model with a relationship to Checklist (or its children) must have its migration logic added to `Concept.absorb()`. Currently, absorb handles checklists, sections, items, votes, reports, and user progress.
