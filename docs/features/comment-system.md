# Comment System

The comment system lets users discuss games, trophies, and checklists across the platform. Comments are organized at the Concept level so discussions carry across regional and platform stacks of the same game. Users can upvote comments, report rule violations, and staff can moderate through a dedicated dashboard. All user-facing text is sanitized with bleach and checked against a database-managed banned word list before being stored.

## Architecture Overview

Comments are Concept-scoped rather than Game-scoped. This means a discussion about "Elden Ring" is shared across the PS4, PS5, and regional editions rather than being fragmented. Within a Concept, comments can target three scopes: the Concept itself (general game discussion), a specific trophy (identified by `trophy_id`), or a specific checklist (identified by `checklist_id`). A comment can only belong to one scope.

Threading is implemented via a self-referential foreign key (`parent`). Depth is denormalized on each comment (0 = top-level, 1 = first reply, etc.) and capped at 10 levels to prevent deeply nested threads that are hard to read and expensive to render. Upvote counts are also denormalized on the Comment model for efficient sorting without JOINs.

The moderation pipeline is separate from the CRUD path. Reports go into a queue (`CommentReport`), staff review them through the moderation dashboard (`/staff/moderation/`), and every moderator action is recorded in `ModerationLog` with the original comment body preserved for accountability and appeals.

Sanitization happens in two layers. First, bleach strips all HTML tags (zero-tolerance: no tags allowed at all). Second, the banned word filter runs a regex-based check with configurable word boundary matching. Both layers run on create and edit, so existing comments cannot be edited to bypass the filter.

## File Map

| File | Purpose |
|------|---------|
| `trophies/services/comment_service.py` | All business logic: CRUD, voting, reporting, sanitization, banned word checking |
| `trophies/models.py` (Comment) | Comment model with threading, soft delete, denormalized depth/upvotes |
| `trophies/models.py` (CommentVote) | One-vote-per-profile-per-comment tracking |
| `trophies/models.py` (CommentReport) | User-submitted reports with reason codes and status tracking |
| `trophies/models.py` (ModerationLog) | Audit trail for all moderator actions |
| `trophies/models.py` (BannedWord) | Staff-managed word filter list |
| `api/views.py` | REST endpoints: CommentListView, CommentCreateView, CommentDetailView, CommentVoteView, CommentReportView |
| `trophies/views/admin_views.py` | CommentModerationView (dashboard), ModerationActionView (action handler) |
| `static/js/comments.js` | Client-side comment UI and TrophyCommentManager |

## Data Model

### Comment

| Field | Type | Notes |
|-------|------|-------|
| `concept` | FK to Concept | CASCADE. The game concept this comment belongs to |
| `trophy_id` | IntegerField (nullable) | Trophy position within concept. NULL = concept-level comment |
| `checklist_id` | IntegerField (nullable) | Checklist ID within concept. NULL = concept-level comment |
| `profile` | FK to Profile | CASCADE. Author |
| `parent` | FK to self (nullable) | CASCADE. Enables threading |
| `depth` | PositiveIntegerField | Denormalized: auto-calculated from parent chain on save |
| `body` | TextField (max 2000) | Sanitized plain text |
| `upvote_count` | PositiveIntegerField | Denormalized vote tally for sort performance |
| `is_edited` | BooleanField | Set to True on any edit |
| `is_deleted` | BooleanField | Soft delete flag |
| `deleted_at` | DateTimeField (nullable) | When soft deletion occurred |

Validation constraint: `trophy_id` and `checklist_id` are mutually exclusive (enforced in `clean()`).

### CommentVote

| Field | Type | Notes |
|-------|------|-------|
| `comment` | FK to Comment | CASCADE |
| `profile` | FK to Profile | CASCADE |

Unique constraint on `(comment, profile)`.

### CommentReport

| Field | Type | Notes |
|-------|------|-------|
| `comment` | FK to Comment | CASCADE |
| `reporter` | FK to Profile | CASCADE |
| `reason` | CharField | One of: spam, harassment, inappropriate, misinformation, other |
| `status` | CharField | One of: pending, reviewed, dismissed, action_taken |
| `details` | TextField | Capped at 500 characters |
| `reviewed_by` | FK to CustomUser (nullable) | Staff member who handled the report |

### ModerationLog

| Field | Type | Notes |
|-------|------|-------|
| `moderator` | FK to CustomUser | PROTECT. Never delete moderator history |
| `action_type` | CharField | delete, restore, dismiss_report, approve_comment, warning_issued, bulk_delete, report_reviewed |
| `comment` | FK to Comment (nullable) | SET_NULL. Preserved via snapshot fields even if comment is hard-deleted |
| `comment_id_snapshot` | IntegerField | Preserves comment ID regardless of deletion |
| `comment_author` | FK to Profile (nullable) | SET_NULL |
| `original_body` | TextField | Full original text at time of moderation |
| `concept` | FK to Concept (nullable) | SET_NULL |
| `trophy_id` | IntegerField (nullable) | Trophy context if applicable |
| `related_report` | FK to CommentReport (nullable) | SET_NULL |
| `reason` | TextField | Moderator's stated reason |
| `ip_address` | GenericIPAddressField (nullable) | Captured from request for audit |

### BannedWord

| Field | Type | Notes |
|-------|------|-------|
| `word` | CharField (unique) | Case-insensitive match target |
| `is_active` | BooleanField | Toggle without deleting |
| `use_word_boundaries` | BooleanField | True: regex `\bword\b` matching. False: substring matching |
| `added_by` | FK to CustomUser (nullable) | Staff member who added it |

## Key Flows

### Creating a Comment

1. Client POSTs to `/api/v1/comments/concept/<concept_id>/create/` (or trophy/checklist variant) with `body` and optional `parent_id`
2. `CommentService.can_comment(profile)` checks: logged in, PSN linked, community guidelines agreed
3. `sanitize_text()` strips all HTML via bleach, then unescapes entities back to plain text
4. Body length validated (0 < length <= 2000)
5. `check_banned_words()` loads active words from cache (5-minute TTL), checks each against the body using word boundaries or substring matching
6. If replying: validate parent exists, belongs to same concept/trophy/checklist, and depth < 10
7. Comment created in a transaction. Depth auto-calculated in `save()` from parent
8. Cache invalidated for the concept/trophy/checklist scope
9. API returns rendered HTML partial for the new comment

### Voting on a Comment

1. Client POSTs to `/api/v1/comments/<comment_id>/vote/`
2. `CommentService.can_interact(profile)` checks: logged in, PSN linked
3. Cannot vote on own comments or deleted comments
4. Toggle logic: if existing `CommentVote` found, delete it and decrement `upvote_count` via `F()` expression. Otherwise create vote and increment
5. On new vote: triggers `check_all_milestones_for_user()` for the comment author's `comment_upvotes` milestones
6. Uses `refresh_from_db()` after F-expression update to return accurate count

### Soft Deleting a Comment

1. Owner can delete own comment. Staff can delete any comment
2. `Comment.soft_delete()` preserves original body in `ModerationLog` (if moderator action), then overwrites body with `[deleted]`
3. Sets `is_deleted=True` and `deleted_at=now()`
4. Thread structure preserved: replies still reference the deleted parent. UI shows "[deleted]" placeholder
5. If moderator: creates ModerationLog entry with original body, IP address, and reason

### Reporting a Comment

1. Client POSTs to `/api/v1/comments/<comment_id>/report/` with `reason` and optional `details`
2. Duplicate report check: one report per profile per comment
3. `CommentReport` created with status `pending`
4. Report appears in staff moderation dashboard at `/staff/moderation/`

### Moderating a Report

1. Staff navigates to CommentModerationView, which shows pending reports with full context
2. Staff chooses an action via POST to ModerationActionView: delete (soft-deletes comment), dismiss (closes report without action), or review
3. All actions create a ModerationLog entry
4. Report status updated (action_taken, dismissed, reviewed)

## API Endpoints

| Method | Path | Auth | Rate Limit | Purpose |
|--------|------|------|------------|---------|
| GET | `/api/v1/comments/concept/<concept_id>/` | No | None | List concept-level comments |
| GET | `/api/v1/comments/concept/<concept_id>/trophy/<trophy_id>/` | No | None | List trophy-level comments |
| GET | `/api/v1/comments/concept/<concept_id>/checklist/<checklist_id>/` | No | None | List checklist-level comments |
| POST | `/api/v1/comments/concept/<concept_id>/create/` | Yes | 10/min | Create concept-level comment |
| POST | `/api/v1/comments/concept/<concept_id>/trophy/<trophy_id>/create/` | Yes | 10/min | Create trophy-level comment |
| POST | `/api/v1/comments/concept/<concept_id>/checklist/<checklist_id>/create/` | Yes | 10/min | Create checklist-level comment |
| PUT | `/api/v1/comments/<comment_id>/` | Yes | 20/min | Edit a comment |
| DELETE | `/api/v1/comments/<comment_id>/` | Yes | 20/min | Soft-delete a comment |
| POST | `/api/v1/comments/<comment_id>/vote/` | Yes | 30/min | Toggle upvote |
| POST | `/api/v1/comments/<comment_id>/report/` | Yes | 5/hour | Report a comment |

## Integration Points

- **Concept.absorb()**: When concepts merge, all comments (and their votes and reports) are migrated. This is handled in `trophies/models.py`. Any new FK to Concept must be added to `absorb()`.
- **Concept.comment_count**: Denormalized count of concept-level comments, updated by signal. Recalculated during `absorb()`.
- **Milestone system**: Upvote milestones (`comment_upvotes` criteria type) are checked when a vote is cast on a comment author's post.
- **Community guidelines**: `profile.guidelines_agreed` must be True before commenting. This is set through the guidelines agreement flow on the frontend.

## Cache Keys

| Key Pattern | TTL | Purpose |
|-------------|-----|---------|
| `comments:concept:{id}` | 5 min | Cached concept-level comment list |
| `comments:concept:{id}:trophy:{trophy_id}` | 5 min | Cached trophy-level comment list |
| `comments:concept:{id}:checklist:{checklist_id}` | 5 min | Cached checklist-level comment list |
| `banned_words:active` | 5 min | Cached list of active banned words |

## Gotchas and Pitfalls

- **Soft delete preserves thread structure**: Deleted comments are NOT removed from the database. They remain as `[deleted]` placeholders so replies are not orphaned. The `comment_count` denormalization does not decrement on delete.
- **Banned word boundaries**: Setting `use_word_boundaries=True` prevents false positives (e.g., "assassin" will not match "ass"). Setting it to False enables substring matching for phrases that should be caught regardless of surrounding text.
- **Sanitization order matters**: bleach strips HTML first, then `html.unescape()` converts entities back to plain text. This prevents double-encoding (e.g., `&amp;` becoming `&amp;amp;`).
- **trophy_id is a position, not a PK**: `trophy_id` on Comment is the trophy's position identifier within a concept, not the database primary key. This allows comments to span across game stacks that share the same trophy layout.
- **Mutual exclusivity**: A comment's `trophy_id` and `checklist_id` cannot both be set. The `clean()` method enforces this, but the API views should also validate.
- **F-expression race safety**: Upvote counts use `F('upvote_count') + 1` / `F('upvote_count') - 1` to prevent race conditions from concurrent votes. Always `refresh_from_db()` after to read the actual value.

## Related Docs

- [Concept Model](../architecture/concept-model.md): Core Concept model and the `absorb()` migration pattern
- [Community Hub](../community-hub.md): Reviews and ratings system that coexists alongside comments
