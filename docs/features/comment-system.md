# Comment System (Legacy / Read-Only)

> **Status: Legacy.** The comment system no longer accepts new comments. Historical data is preserved in the database, the moderation pipeline is still wired for staff cleanup, and a small set of API endpoints remain so existing comments can be voted on, reported, edited, or deleted by their owners. There is no creation path on any current page.

The comment system was originally Platinum Pursuit's discussion layer. It supported concept-level discussion, then trophy-level, then checklist-level. Each surface was deprecated in turn:

- **Game-level and trophy-level comments** were removed when the discussion focus shifted to concept-scoped checklist threads.
- **Checklist-level comments** were removed when the entire checklist system was replaced by the staff-authored Roadmap system. See [Roadmap System](roadmap-system.md).

Comments on the new Roadmap system were intentionally not built. The [Review Hub](review-hub.md)'s Reviews & Replies flow is the supported path for user discussion going forward.

## What Still Exists

- The `Comment`, `CommentVote`, `CommentReport`, `ModerationLog`, and `BannedWord` model tables remain populated with historical data.
- `CommentService` still exposes vote, report, edit, and soft-delete operations for existing comments.
- The staff moderation dashboard at `/staff/moderation/` still surfaces pending `CommentReport` rows so historical content can be cleaned up.
- `Concept.absorb()` still migrates legacy comments during concept reassignment so historical rows survive concept merges (see [Concept Model](../architecture/concept-model.md)).

## What Was Removed

- Comment **list** and **create** endpoints (no surface in the UI calls them).
- The `comments.js` client and the comment composer UI.
- The cache key `comments:concept:{id}:checklist:{checklist_id}` (no fresh writes; reads are no longer happening).
- The `BannedWord` filter is still wired into `CommentService.create_comment()`, but since no creation path exists, the filter primarily survives for the markdown filters used by the review system.

## Surviving API Endpoints

| Method | Path | Auth | Rate Limit | Purpose |
|--------|------|------|------------|---------|
| GET/PUT/DELETE | `/api/v1/comments/<comment_id>/` | Login | 20/min | Detail/edit/delete a historical comment (owner only for edit/delete) |
| POST | `/api/v1/comments/<comment_id>/vote/` | Login | 30/min | Toggle upvote on an existing comment |
| POST | `/api/v1/comments/<comment_id>/report/` | Login | 5/hour | Flag an existing comment for staff review |
| POST | `/api/v1/guidelines/agree/` | Login | None | Accept community guidelines (still used by other systems for `profile.guidelines_agreed`) |

The list and create endpoints (`/api/v1/comments/concept/<id>/checklist/<id>/`) are no longer registered in `api/urls.py`.

## Data Model (Reference Only)

The model tables are preserved as-is. See `trophies/models.py` for the canonical definitions:

- `Comment` (with `concept` FK, optional `checklist_id` and `trophy_id` legacy fields, threading via `parent`, `depth`, `upvote_count`, `is_deleted`, `body`)
- `CommentVote` (unique on `(comment, profile)`)
- `CommentReport` (status: `pending` / `reviewed` / `dismissed` / `action_taken`)
- `ModerationLog` (audit trail; preserves the original body and reporter context even if the comment is later hard-deleted)
- `BannedWord` (still consulted by `ChecklistService.process_markdown()`, which the review system uses)

## Moderation Flow (Still Active)

1. A user POSTs to `/api/v1/comments/<id>/report/` with a `reason` and optional `details`. Duplicate reports per profile are blocked.
2. The `CommentReport` row appears in the staff moderation queue at `/staff/moderation/` (rendered by `CommentModerationView` in `trophies/views/admin_views.py`).
3. Staff acts via `ModerationActionView`: delete (soft-delete the comment), dismiss (close the report), or review.
4. Every action records a `ModerationLog` entry preserving the original body and the moderator's reason.

## Gotchas and Pitfalls

- **Do not build new comment surfaces against this code.** The pattern is intentionally retired. New discussion features should plug into the Review Hub's Reviews & Replies pipeline (`api/review_views.py`, `ReviewReplyListView` / `ReviewReplyDetailView`) instead.
- **`ChecklistService.process_markdown()` is the only thing in `CommentService` that current code still calls outside of moderation.** The review system uses it for markdown rendering. Do not delete the helper while the review system depends on it.
- **`Concept.absorb()` still walks comment relationships.** When concepts merge, comments, votes, reports, and moderation logs follow the surviving concept. If you delete the comment tables in the future, also strip the corresponding step from `absorb()`.
- **The `trophy_id` and `checklist_id` columns on `Comment` are dead but still indexed.** Leave them; future migrations can drop them if/when the historical data is fully retired.
- **`profile.guidelines_agreed`** is still read by the moderation flow and by review creation (not just comments). Do not remove the guidelines model when retiring comments.

## Related Docs

- [Roadmap System](roadmap-system.md): the user-facing replacement for checklists; intentionally has no comment surface
- [Review Hub](review-hub.md): the supported pattern for new discussion features (Reviews & Replies)
- [Concept Model](../architecture/concept-model.md): how `absorb()` keeps legacy comment rows attached to surviving concepts
