# Community Game Flags

Community game flags let users report data quality issues on individual games. A hunter [restricted from reports](admin-hub.md#restrictions) cannot file one, enforced in `GameFlagService.submit_flag` (the only writer) as well as at the view. Each flag is reviewed by a moderator in the [Moderation Center](moderation-center.md) before any changes are applied (Django admin still works, and is the only route for bulk review). This helps catch delisted games, unobtainable platinums, shovelware misclassifications, missing VR tags, online trophy requirements, buggy trophies, regional errors, and other issues that automated systems miss.

## Architecture Overview

The system follows a simple submit-then-review pattern. Users submit flags through a modal on game detail pages, which creates `GameFlag` rows with `status='pending'`. Moderators review flags one at a time in the [Moderation Center](moderation-center.md)'s Game Flags queue; Django admin keeps its bulk approve/dismiss actions for sweeps. Approving a flag automatically applies the corresponding Game field change (where applicable).

Both routes now record the same thing. Django admin's bulk actions were rerouted through `moderation_service` in 2026-09 behind a confirmation page that requires a reason, so a sweep writes one `ModerationAction` per flag with the before/after of every Game field it touched, and skips (with a message) any a colleague already decided. Before that they called `GameFlagService` directly and wrote nothing. Django admin is also superusers only now, so most admins reach the queue and not the changelist.

**Why Game-level, not Concept-level**: Flags target specific Game rows because some issues are variant-specific (a regional version may be delisted while others aren't). This also avoids needing `Concept.absorb()` updates since games are never deleted during concept absorption.

**Duplicate prevention**: Rather than a database constraint, the service layer silently succeeds when a user submits a flag identical to one they already have pending. This prevents DB errors while giving the user a seamless experience. Once a flag is approved or dismissed, the user can submit the same flag type again (the situation may have changed).

## File Map

| File | Purpose |
|------|---------|
| `trophies/models.py` (GameFlag) | Model with 13 flag types and pending/approved/dismissed status |
| `trophies/services/game_flag_service.py` | submit_flag, approve_flag, dismiss_flag logic |
| `api/game_flag_views.py` | Two POST endpoints: one game, and one flag across several versions |
| `trophies/services/game_grouping_service.py` (`version_peer_qs`) | THE same-work rule. Bounds which versions a bulk submission may name |
| `api/urls.py` | URL registration |
| `trophies/admin.py` (GameFlagAdmin) | Bulk review, superuser-only, routed through `moderation_service` behind a reason prompt |
| `trophies/views/moderation_views.py` | The Game Flags queue: one flag at a time, reason required, audited |
| `static/js/game-flag.js` | Modal wiring + submission. Picks its endpoint from the rendered markup, not from the page |
| `templates/trophies/partials/game_detail/report_modal.html` | The shared modal. Renders the version selector only when the includer passes `flag_versions` |
| `templates/trophies/partials/game_detail/hero.html` | Report button on the trophy list page |
| `templates/trophies/partials/game_page/header.html` | Report button on the Game page |
| `templates/trophies/game_detail.html`, `templates/trophies/game_page.html` | Modal include + script |

## Data Model

### GameFlag

| Field | Type | Notes |
|-------|------|-------|
| game | FK(Game) | CASCADE. The specific game being flagged |
| reporter | FK(Profile) | CASCADE. The user who submitted the flag |
| flag_type | CharField(30) | One of 13 types (see Flag Types below) |
| details | TextField(500) | Optional user-provided context |
| status | CharField(20) | pending, approved, dismissed |
| reviewed_by | FK(CustomUser) | SET_NULL. Staff who reviewed |
| reviewed_at | DateTimeField | When the review happened |
| admin_notes | TextField | Internal staff notes |

### Flag Types and Their Effects

| Flag Type | Description | Auto-applied on approval |
|-----------|-------------|--------------------------|
| `delisted` | Game has been delisted | `Game.is_delisted = True` |
| `not_delisted` | Game is NOT delisted | `Game.is_delisted = False` |
| `unobtainable` | Trophies are unobtainable | `Game.is_obtainable = False` |
| `obtainable` | Trophies ARE obtainable | `Game.is_obtainable = True` |
| `is_shovelware` | Game is shovelware | `shovelware_status='manually_flagged'`, `shovelware_lock=True` |
| `not_shovelware` | Game is NOT shovelware | `shovelware_status='manually_cleared'`, `shovelware_lock=True` |
| `missing_vr` | Game supports VR, not tagged | None (staff adds VR platform manually) |
| `has_online_trophies` | Has online-required trophies | `Game.has_online_trophies = True` |
| `no_online_trophies` | No online trophies | `Game.has_online_trophies = False` |
| `has_buggy_trophies` | Buggy/broken trophies | `Game.has_buggy_trophies = True` |
| `buggy_trophies_resolved` | Buggy trophies fixed | `Game.has_buggy_trophies = False` |
| `region_incorrect` | Regional info is wrong | None (staff fixes manually) |
| `other` | Other issue (details required) | None (staff handles manually) |

## Key Flows

### User Submits a Flag (one trophy list)

From the **trophy list page** (`/games/<np_communication_id>/`), which is one list by definition.

1. User clicks "Report" in the hero
2. Modal opens with flag type dropdown and optional details textarea
3. JS calls `POST /api/v1/games/<game_id>/flag/`
4. Service checks for existing pending flag (same user, game, type)
5. If pending exists: returns existing flag (silent success)
6. Otherwise: creates new GameFlag with `status='pending'`
7. User sees success toast

### User Submits a Flag (several versions at once)

From the **Game page** (`/games/<igdb_id>/`), which is concept-level and wraps every trophy list
resolving to one IGDB id. A data problem is usually true of more than one of them.

1. User clicks "Report an issue" in the hero
2. The same modal opens, plus a version selector: one checkbox per trophy list, with the version
   they are currently viewing pre-checked
3. JS calls `POST /api/v1/games/<anchor_game_id>/flag/versions/` with `game_ids`
4. The endpoint intersects the submitted ids against `version_peer_qs(anchor)` and drops anything
   that is not a version of the anchor
5. `GameFlagService.submit_flags` files one row per surviving version, in ONE transaction, each
   through `submit_flag` so the reporting restriction is enforced per row
6. The response carries `created` and `duplicates`, and the toast says which happened

**Before this, the Game page's only report route filed against the HOST list** — whichever version
won platform priority — regardless of what the reader was looking at.

### Staff Reviews a Flag

1. Staff opens Django admin at `/admin/trophies/gameflag/`
2. Filters by status = "Pending Review"
3. Selects one or more flags
4. Chooses "Approve selected flags" or "Dismiss selected flags" action
5. On approve: `GameFlagService.approve_flag()` applies the Game field change and marks the flag as approved
6. On dismiss: flag is marked as dismissed with no data change

## API Endpoints

| Method | Path | Auth | Rate Limit | Purpose |
|--------|------|------|------------|---------|
| POST | `/api/v1/games/<game_id>/flag/` | User (linked PSN) | 5/min (shared) | Submit a game flag |
| POST | `/api/v1/games/<game_id>/flag/versions/` | User (linked PSN) | 5/min (shared) | One flag across several versions of that game |

Both endpoints share ONE bucket (`FLAG_RATELIMIT_GROUP`), so 5/min is the budget across both shapes.
A tripped limit returns **403** with a `detail` key, not 429.

**Request body**: `{ "flag_type": "delisted", "details": "Removed from store in March 2026" }`,
plus `"game_ids": [12, 34]` on the versions endpoint.

**Response**: `{ "success": true, "message": "..." }`; the versions endpoint adds
`"created"` and `"duplicates"` counts.

## Integration Points

- **Trophy list page** (`/games/<np_communication_id>/`): Report button in the hero, single-list submit
- **Game page** (`/games/<igdb_id>/`): Report button in the hero's extras rail, version selector, multi-version submit
- **Django admin**: GameFlagAdmin with approve/dismiss bulk actions
- **Shovelware system**: Approving `is_shovelware`/`not_shovelware` flags sets `shovelware_lock=True`, preventing auto-detection from overwriting the manual decision

## Gotchas and Pitfalls

- **No unique constraint**: Duplicate prevention is in the service layer, not the database. This is intentional: the "allow re-flagging after approval/dismissal" requirement means a DB unique constraint would be too restrictive.
- **`missing_vr`, `region_incorrect`, and `other` require manual action**: Approving these flags marks them as approved but does NOT automatically change any Game fields. Staff must manually handle the reported issue. The `other` flag type requires the user to provide details describing the issue.
- **Shovelware lock**: Approving shovelware flags sets `shovelware_lock=True`, which prevents the automated shovelware detection service from overwriting the manual decision. This is the correct behavior.
- **Rate limit is per-minute, not per-hour**: Flags use 5/min (vs 5/hour for comment reports) because users may flag multiple games in one session.
- **The rate limit is WHY the versions endpoint exists.** It counts requests, so a client loop
  filing one version at a time would be refused part way through a six-list concept, having already
  filed some, with nothing in the UI able to say which. The fan-out is server-side so one deliberate
  act costs one unit of budget. `GameFlagService.MAX_BULK_VERSIONS` (25) is what stops that from
  meaning "unlimited rows per unit".
- **Both flag endpoints share ONE rate-limit bucket, and that has to be explicit.** `django_ratelimit`
  derives the bucket from the decorated function unless `group` is named, so the two endpoints were
  two budgets: 5 single-game writes AND 5 bulk writes a minute, the bulk ones carrying up to 25 rows
  each. Both now pass `group=FLAG_RATELIMIT_GROUP`. Pinned by a test that exhausts one endpoint and
  asserts the other is refused.
- **A tripped flag rate limit is a 403, not a 429.** `Ratelimited` subclasses `PermissionDenied`,
  which DRF renders as 403 with a **`detail`** key — not the `error` key every hand-written response
  in these views uses. `game-flag.js` reads both; reading only `error` told a rate-limited reporter
  "Failed to submit report." and nothing else.
- **`details` is coerced to a string before it is capped.** `(details or '')[:500]` assumed a string:
  slicing a list is a no-op that returns the list, and `TextField` `str()`s it on save, so
  `{"details": ["A" * 20000]}` stored 20 KB per row — 25 rows per request here — past a cap that
  looked like it enforced 500. A dict was worse: slicing one raises `TypeError` inside the
  transaction and surfaced as a 500. `max_length=500` on a `TextField` is a form hint, not a DB
  constraint, so `GameFlagService._clean_details` is the only thing between a payload and the column.
- **`version_peer_qs` is a security boundary, not a convenience.** The versions endpoint accepts a
  game id only if it is a version of the anchor. Skip that intersection and the endpoint takes
  arbitrary ids, and 5 requests/min x 25 becomes a mass-flagging tool aimed anywhere in the
  catalog. The client's list is exactly what an attacker controls, so it is never trusted — and the
  rule has ONE definition, shared with the About tab's versions card, so the two cannot drift.
  The boundary is the same-WORK relation, not "whatever the switcher rendered": on the
  `/games/c/<concept_id>/` route the page shows one concept's lists while the endpoint admits every
  list sharing the IGDB id. Deliberate — those are the same game by the only identity the catalog
  has, and the About tab already surfaces them to that same reader.
- **A bulk submission is atomic.** A restriction hit mid-loop raises `GameFlagSubmissionError` and
  rolls the whole batch back rather than leaving a partial set the reporter can neither see nor
  amend.

## Related Docs

- [Shovelware Detection](../reference/shovelware-detection.md): how automated shovelware detection works and interacts with manual flags
- [Data Model](../architecture/data-model.md): Game and Concept model relationships
