# Roadmap Roles, Locks & Revisions

A wiki-style authoring layer on top of the existing Roadmap (staff-authored
platinum guide) system. Adds three orthogonal capabilities: a role-based
permission tier, an **advisory** single-writer-at-a-time edit lock that
preserves work even after long idle gaps, and a permanent revision history
that captures a full JSON snapshot every time a guide is saved, published,
or has its lock taken over.

## Architecture Overview

The editor never mutates live records directly. While a writer is active, all
changes accumulate in a draft "branch" attached to a `RoadmapEditLock`.
Autosaves push the branch to the server every ~1.5s; an explicit Save button
atomically merges the branch into live records, creates a `RoadmapRevision`,
and releases the lock. Closing the editor without saving discards the branch.

```
            +- Live records (RoadmapTab/Step/TrophyGuide) ---- public detail
            |
Editor --> RoadmapEditLock (advisory: 15min idle / 1hr cap soft-thresholds)
            |     +- branch_payload (JSONField, autosaved)
            |     |
            |     v explicit Save merges into v
            |
            +- RoadmapRevision (full snapshot per save / takeover) ---- admin restore
```

### Advisory lock semantics

The 15-min idle threshold and the 1-hour cap are **soft signals**, not hard
deadlines:

- A **stale lock still held by you** resumes cleanly when you return —
  re-acquiring or heartbeating reactivates it without losing your branch.
- A **stale lock held by someone else** can be auto-taken-over the next time
  another writer opens the editor. The displaced writer's `branch_payload`
  is archived as an `auto_taken_over` revision (recoverable from admin).
- An **active lock held by someone else** still blocks (409 conflict);
  publishers can hostile-takeover via force-break (`force_unlocked` revision).

This means writers don't lose work for stepping away during an emergency.
Locks only become "loose" when there's actually contention. A daily
`prune_stale_roadmap_locks` cron reaps locks idle longer than 7 days,
archiving their branches as recovery revisions before deleting.

### Key invariants
- Live records change only inside an atomic merge from a held lock's branch.
- One lock per roadmap. Stale-but-mine resumes; stale-by-other gets archived on takeover.
- Every change to live records produces a revision; revisions are kept forever.
- Every takeover (auto or hostile) archives the displaced branch as a revision.
- Roles are independent of `is_staff`. Manual assignment via Profile admin.

### Role hierarchy
| Role | Edit own sections | Edit any section | Delete / reorder | Edit *published* guide | Toggle publish | Force-break lock |
|------|---|---|---|---|---|---|
| `none` | – | – | – | – | – | – |
| `writer` | ✓ (unpublished) | – | – | – | – | – |
| `editor` | ✓ (unpublished) | ✓ (unpublished) | ✓ (unpublished) | – | – | – |
| `publisher` | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |

**Published guides are publisher-only.** Writers and editors are redirected
out of the editor with an explanatory flash message; the lock-acquire API
returns 403; the merge service refuses on the server side as well. The
expected workflow for editors who need to change a live guide: ask a
publisher to unpublish first.

### Per-field role gates (within an unpublished guide)

Beyond the broad role hierarchy above, individual fields have specific gates
enforced by the merge service. The constants live in
`trophies/services/roadmap_merge_service.py`:

| Field group | Required role |
|---|---|
| `RoadmapStep`, `TrophyGuide` content (title, body, etc.) | writer-if-owner / editor-or-higher |
| `RoadmapTab.general_tips` | writer-if-tab-owner / editor-or-higher (`WRITER_TAB_FIELDS`) |
| `RoadmapTab` metadata (difficulty, estimated_hours, min_playthroughs) | editor-or-higher (`EDITOR_TAB_FIELDS`) |
| `RoadmapTab.missable_count`, `online_required` | not editable — derived from per-trophy `is_missable` / `is_online` flags |
| `RoadmapTab.youtube_url` | publisher-only (`PUBLISHER_TAB_FIELDS`) — reserved for curated official PlatPursuit YouTube guides |
| `Roadmap.status` | publisher-only |

Tab metadata is **not** locked to a single editor — any editor or publisher
can update it. If you ever need first-editor-wins semantics here, the
constants list is the place to extend.

A "section" is a `RoadmapStep` or `TrophyGuide` (both carry a `created_by`
that the writer-scoping rule keys off of). Tabs are auto-created from
`ConceptTrophyGroup` and follow the same first-author-wins rule for content
fields (`general_tips`, `youtube_url`); tab metadata fields (`difficulty`,
`estimated_hours`, `min_playthroughs`) are editor+ only. `missable_count`
and `online_required` are derived from per-trophy flags and not directly
editable.

## File Map

| File | Purpose |
|------|---------|
| `trophies/models.py` (Roadmap section) | `Roadmap`, `RoadmapTab`, `RoadmapStep`, `RoadmapStepTrophy`, `TrophyGuide`, `RoadmapEditLock`, `RoadmapRevision` |
| `trophies/permissions/roadmap_permissions.py` | Role hierarchy + per-action permission helpers |
| `trophies/services/roadmap_service.py` | `snapshot_roadmap()` canonical JSON serializer |
| `trophies/services/roadmap_merge_service.py` | `merge_branch`, `restore_revision`, `force_unlock` |
| `api/permissions.py` | `IsRoadmapAuthor` DRF permission class (reads `view.min_roadmap_role`) |
| `api/roadmap_lock_views.py` | acquire / heartbeat / branch / release / break / merge endpoints |
| `api/roadmap_views.py` | Legacy per-field endpoints (still in place for backwards compat) + publish endpoint |
| `static/js/roadmap_editor.js` | `LockController` + `BranchProxy` (in-memory branch state, debounced push, conflict UI) |
| `templates/trophies/roadmap_edit.html` | Editor template with lock banner + Save/Cancel buttons |
| `templates/trophies/partials/roadmap/authors_block.html` | Reusable contributor-credit block (full + compact variants) |
| `trophies/templatetags/roadmap_tags.py` | `{% roadmap_authors roadmap variant='full|compact' %}` |
| `trophies/migrations/0211_profile_roadmap_role.py` | Adds `Profile.roadmap_role` |
| `trophies/migrations/0212_roadmap_lock_revision_attribution.py` | Adds lock + revision models + `created_by`/`last_edited_by` on Tab/Step/TrophyGuide |

## Data Model

### `RoadmapEditLock`
- One-to-one with `Roadmap`. Fields: `holder` (FK Profile), `acquired_at`, `last_heartbeat`, `expires_at`, `branch_payload` (JSONField), `payload_version` (uint).
- `expires_at` = `min(last_heartbeat + IDLE_TIMEOUT, acquired_at + HARD_CAP)`. Defaults: 15 min idle, 1 hour absolute cap.
- `is_held_by(profile)`, `is_expired()`, `heartbeat()`, `seconds_until_expiry()`, `hard_cap_seconds_remaining()` helpers.

### `RoadmapRevision`
- FK `Roadmap`, FK `Profile` (author, nullable on author deletion), `action_type` (`created`/`edited`/`published`/`unpublished`/`restored`/`force_unlocked`), `snapshot` (JSONField), `summary` (varchar 200), `created_at`.
- Indexes: `(roadmap, -created_at)`, `(author, -created_at)`.

### Branch payload shape (`payload_version: 1`)
```json
{
  "payload_version": 1,
  "roadmap_id": 123,
  "status": "draft",
  "tabs": [
    {
      "id": 12, "concept_trophy_group_id": 456,
      "general_tips": "...", "youtube_url": "...",
      "difficulty": 6, "estimated_hours": 40, "missable_count": 0,
      "online_required": false, "min_playthroughs": 1,
      "created_by_id": 789, "last_edited_by_id": 789,
      "steps": [
        {"id": 33, "title": "...", "description": "...", "youtube_url": "...",
         "order": 0, "created_by_id": 789, "last_edited_by_id": 789,
         "trophy_ids": [1001, 1002]}
      ],
      "trophy_guides": [
        {"id": 44, "trophy_id": 1003, "body": "...", "order": 0,
         "is_missable": false, "is_online": false, "is_unobtainable": false,
         "created_by_id": 789, "last_edited_by_id": 789}
      ]
    }
  ]
}
```

New rows have `id: null` in the wire payload. The client uses negative
temporary ids internally for DOM tracking; `BranchProxy.toWirePayload()`
converts them to null on send.

## Key Flows

### Acquire → edit → save
1. User opens editor → JS calls `POST /api/v1/roadmap/<id>/lock/acquire/`.
2. Server `select_for_update` on lock; if held by self, refresh and return current branch_payload. If held by someone else and not expired, return 409 with holder info. Otherwise create new lock and seed `branch_payload` with `RoadmapService.snapshot_roadmap(roadmap)`.
3. Client renders editor and starts a 2-min heartbeat (`POST /lock/heartbeat/`).
4. Every UI mutation goes through `BranchProxy.handle()` (legacy URL pattern shim) which mutates an in-memory state and schedules a debounced `PATCH /lock/branch/` (1.5s).
5. User clicks Save → `POST /lock/merge/`.
6. Server validates the lock is still held + not expired, applies the branch_payload to live records inside a transaction, enforces per-action role rules, generates a summary, creates a `RoadmapRevision`, deletes the lock.
7. Client reloads to re-sync against the new live state.

### Lock timer & warnings (advisory)
- A live timer chip in the editor header shows the more-imminent of `idle_remaining` and `hard_cap_remaining`. Color tier: green > 5min, amber 1-5min, red < 1min.
- Heartbeat is **activity-aware**: fires on user input (keystroke / click / drag), debounced to 60s, plus a 2-min timer keep-alive that only sends if there's been activity in the last 5 min. When the writer steps away, heartbeats stop and the lock naturally goes stale.
- Three informational warnings (no blocking modals — work is preserved either way):
  - **Idle T-3 min**: gentle toast — "Session going idle in 3 min — others can claim the guide if you don't return."
  - **Idle T-30 s**: banner — "Session about to go stale. Your branch is safe — others can take over the lock unless you interact."
  - **Hard cap T-5 min**: banner — "You've been editing for nearly an hour. Consider clicking Save to checkpoint your progress."
- On lock-loss (heartbeat returns 404/409): editor enters read-only mode with a banner explaining the displaced branch is archived in revisions.
- On welcome-back (acquire returns `resumed_stale: true`): banner — "Welcome back, your previous session was idle and is now resumed. Your branch is intact."

### Force-break (publisher-only)
1. Publisher views editor while held by someone else; conflict banner shows holder + Force-unlock button.
2. Publisher clicks button → confirm modal → `POST /lock/break/`.
3. Server (`force_unlock` service) archives `branch_payload` as a `force_unlocked` revision (recoverable from admin) and deletes the lock.
4. Page reloads; publisher can now acquire.

### Restore (admin only)
1. From `Roadmap Revision` admin → select exactly one row → action "Restore live roadmap to selected revision".
2. Server (`restore_revision` service) refuses to run while a non-expired lock exists. Then clears all live Steps + TrophyGuides on this roadmap, resets Tab fields from snapshot, recreates Steps + TrophyGuides from snapshot.
3. Creates a new `restored` revision so the restore itself is auditable.

## Author notes

A back-channel comment system layered onto the editor for cross-author
discussion. Notes live entirely outside the lock + branch + revision flow:
posting one never requires holding the edit lock, and they're not serialized
into revision snapshots. Any writer+ can comment any time, including while
another author is mid-session.

### Targets and scoping
- **Section-anchored**: a note attaches to a specific `RoadmapStep` or
  `TrophyGuide` (`target_kind='step'` / `'trophy_guide'`). Discussion lives
  next to the content it's about.
- **Guide-level**: `target_kind='guide'` for cross-cutting feedback ("ready
  for review", "needs trophy guides for chapter 3"). Surfaced in the editor's
  General Notes drawer.
- **No tab-level**: tabs are auto-generated structural shells, no real
  surface for discussion there.
- **No ownership scoping**: a writer can comment on any section regardless
  of who created it. Authors edit/delete their own notes; editor+ can delete
  anyone's; same for resolving.

### Resolved/Open
Each note has a status. Resolved notes hide from the heads-up unread count
(if someone closed a loop before you saw it, treat it as handled), but stay
accessible via "Show resolved" toggle in the General Notes drawer.

### Lifecycle
- Notes survive saves, publishes, restores.
- When a `RoadmapStep` or `TrophyGuide` is deleted, attached notes
  cascade-delete with it. Discussion goes with the deleted thing.
- Notes are stored in `RoadmapNote`; per-profile last-read state in
  `RoadmapNoteRead`.

### Heads-up banner
When a writer opens the editor, if there are open notes (by other authors)
posted since their last `RoadmapNoteRead.last_read_at`, the editor shows a
top-of-page banner: "X new notes from your team since you were last here.
[Review notes]" → opens the General Notes drawer.

`mark_read` is fired ~5s after editor open (gives the writer a moment to
actually see the heads-up before it counts as "read").

### @mentions
`@psn_username` in a note body is parsed via regex
(`(?<![A-Za-z0-9_\-/@])@([A-Za-z0-9_\-]{3,16})`), resolved against existing
Profiles, and rendered as profile links at display time. New mentions fire
a `roadmap_note_mention` notification through the existing notification
system (immediate, not deferred — mentions are 1-to-1, no batching value).

**Role gate**: only Profiles with `roadmap_role >= writer` get pinged. If a
note mentions a regular user's PSN handle (accidentally or otherwise), no
notification fires for that user — they're not on the roadmap team. This
keeps random users from being paged because someone else owns their handle
or because of typos.

**Notification content**: the title shows author + game; the message shows
the target section + a 160-char excerpt of the note body; the `detail`
(markdown, 2.5KB) carries the full note body alongside the game / section
/ timestamp metadata so the recipient can read the whole note without
clicking into the editor. The action button deep-links to the editor with
`?note=<id>` for direct navigation.

Editing a note re-fires notifications only for newly-mentioned profiles, so
re-saving the same body doesn't re-spam already-notified users. Unknown
handles silently drop. Self-mentions DO fire — if a writer types their own
handle they meant it (self-reminder, testing, etc.).

## API Endpoints

| Method | Path | Min Role | Purpose |
|--------|------|----------|---------|
| POST | `/api/v1/roadmap/<id>/lock/acquire/` | writer | Claim or refresh lock; seed branch_payload from live snapshot. 409 if held by another. |
| POST | `/api/v1/roadmap/<id>/lock/heartbeat/` | writer | Extend idle timer. Returns `lock_lost: true` if stale. |
| PATCH | `/api/v1/roadmap/<id>/lock/branch/` | writer | Replace branch_payload (autosave target). Validates `payload_version`. |
| POST | `/api/v1/roadmap/<id>/lock/release/` | writer | Voluntarily release the lock. |
| POST | `/api/v1/roadmap/<id>/lock/break/` | publisher | Force-break someone else's lock; archives branch as `force_unlocked` revision. |
| POST | `/api/v1/roadmap/<id>/lock/merge/` | writer | Apply branch_payload → live records, create revision, release lock. Per-action role rules enforced inside merge service. |
| POST | `/api/v1/roadmap/<id>/publish/` | publisher | Toggle status. Creates `published`/`unpublished` revision. |
| GET | `/api/v1/roadmap/<id>/notes/` | writer | List notes (filter by `status`, `target_kind`, `target_step_id`, `target_trophy_guide_id`). |
| POST | `/api/v1/roadmap/<id>/notes/` | writer | Create a note. Lock-independent — anyone can comment any time. |
| PATCH | `/api/v1/roadmap/<id>/notes/<note_id>/` | writer | Edit own note body. |
| DELETE | `/api/v1/roadmap/<id>/notes/<note_id>/` | writer | Delete own; editor+ can delete anyone's. |
| POST | `/api/v1/roadmap/<id>/notes/<note_id>/resolve/` | writer | Toggle status (own; editor+ for anyone's). |
| POST | `/api/v1/roadmap/<id>/notes/mark-read/` | writer | Bump `RoadmapNoteRead.last_read_at` for the heads-up banner. |

The legacy per-field endpoints (`/tab/<id>/`, `/steps/`, etc.) are still
mounted but the new editor doesn't use them. They remain functional for
backwards compat and direct API access; consider removing in a follow-up.

## Integration Points

- [Roadmap System (base)](roadmap-system.md): the underlying authoring data model the lock + revision layer wraps.
- [Concept absorb()](../architecture/data-model.md): when two concepts merge and both have roadmaps, the source concept's lock + revisions cascade-delete with its roadmap (target's roadmap survives). See gotcha below.

## Gotchas and Pitfalls

- **Admin shell bypasses locks.** `Roadmap.objects.update(...)` or any direct ORM write skips the merge service entirely. No revision is created, no role check runs, no lock interaction. Reserve admin shell edits for emergencies and follow up with a manual `RoadmapRevision` capture if the change is significant.
- **Concept.absorb() and roadmap history.** When a concept absorption fires and both source and target have a roadmap, the existing absorb logic keeps the target's roadmap and lets the source's cascade-delete (taking its `RoadmapEditLock` and `RoadmapRevision` rows with it). This is intentional for v1: re-pointing source revisions to target produces snapshots that no longer match target's structure. If a writer is actively editing the source roadmap when an absorption fires, their session terminates without warning. Vanishingly rare in practice; document if it ever bites.
- **Revision snapshot growth.** A guide with 200+ trophy guides + extensive step descriptions can produce a large JSON snapshot. Postgres TOAST handles per-row compression transparently, so this is a non-issue at current scale, but the snapshot field is unindexed so a future "search revisions by content" feature would need to be designed carefully.
- **Writer deletes intentionally gated.** Writers can never delete sections, even ones they themselves created. This is by design: in a wiki workflow, deletion of authored content requires editor approval. If you find yourself wanting writers to delete their own work, consider making them an editor instead.
- **`payload_version` is the upgrade lever.** If the branch payload schema ever changes (e.g. adding a new attribution field, restructuring tabs), bump `RoadmapEditLock.PAYLOAD_VERSION`. The merge service refuses unknown versions; the acquire endpoint always seeds the current version. Older `RoadmapRevision` snapshots can be migrated lazily by a one-shot management command.
- **Heartbeat death on browser crash (advisory model).** If the writer's tab crashes or they close the laptop without a graceful close, heartbeats stop and the lock soft-expires after 15 min of idle. The lock RECORD persists, holding the in-progress `branch_payload`. The writer can reopen the editor at any point before another writer takes over, and resume cleanly. If another writer opens the editor first, the displaced branch is archived as an `auto_taken_over` revision (recoverable from admin). Permanent loss only happens after the 7-day `prune_stale_roadmap_locks` sweep, and even then the branch becomes a revision rather than vanishing.
- **Editors can edit on published guides; writers cannot.** `can_view_editor` lets editors+ open the editor on a published guide. Writers get redirected. The expectation is editors will unpublish before doing anything visible to the public.

## Management Commands

| Command | Purpose | Typical Usage |
|---------|---------|---------------|
| `prune_stale_roadmap_locks` | Archive and delete locks idle past `--days` (default 7). Each archived lock's `branch_payload` becomes an `auto_taken_over` revision so the displaced work is recoverable. | `python manage.py prune_stale_roadmap_locks --days 7` (daily cron). Add `--dry-run` to preview. |

## Cache Keys

None for this layer. Author-stats helpers (Phase 4 future work) may add Redis-backed caches keyed by `roadmap_author_stats:{profile_id}`.

## Related Docs

- [Roadmap System (base)](roadmap-system.md): the underlying authoring data model and editor.
- [Data Model](../architecture/data-model.md): broader Concept/Roadmap relationship overview.
