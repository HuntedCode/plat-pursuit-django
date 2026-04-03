# Community Game Flags

Community game flags let users report data quality issues on individual games. Each flag is reviewed by staff via Django admin before any changes are applied. This helps catch delisted games, unobtainable platinums, shovelware misclassifications, missing VR tags, online trophy requirements, buggy trophies, and regional errors that automated systems miss.

## Architecture Overview

The system follows a simple submit-then-review pattern. Users submit flags through a modal on game detail pages, which creates `GameFlag` rows with `status='pending'`. Staff review flags in Django admin using bulk approve/dismiss actions. Approving a flag automatically applies the corresponding Game field change (where applicable).

**Why Game-level, not Concept-level**: Flags target specific Game rows because some issues are variant-specific (a regional version may be delisted while others aren't). This also avoids needing `Concept.absorb()` updates since games are never deleted during concept absorption.

**Duplicate prevention**: Rather than a database constraint, the service layer silently succeeds when a user submits a flag identical to one they already have pending. This prevents DB errors while giving the user a seamless experience. Once a flag is approved or dismissed, the user can submit the same flag type again (the situation may have changed).

## File Map

| File | Purpose |
|------|---------|
| `trophies/models.py` (GameFlag) | Model with 12 flag types and pending/approved/dismissed status |
| `trophies/services/game_flag_service.py` | submit_flag, approve_flag, dismiss_flag logic |
| `api/game_flag_views.py` | POST endpoint for flag submission |
| `api/urls.py` | URL registration |
| `trophies/admin.py` (GameFlagAdmin) | Staff review with bulk approve/dismiss actions |
| `static/js/game-flag.js` | Frontend modal and API submission |
| `templates/trophies/partials/game_detail/game_detail_header.html` | Flag button placement |
| `templates/trophies/game_detail.html` | Script include |

## Data Model

### GameFlag

| Field | Type | Notes |
|-------|------|-------|
| game | FK(Game) | CASCADE. The specific game being flagged |
| reporter | FK(Profile) | CASCADE. The user who submitted the flag |
| flag_type | CharField(30) | One of 12 types (see Flag Types below) |
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

## Key Flows

### User Submits a Flag

1. User clicks "Flag" button on game detail page
2. Modal opens with flag type dropdown and optional details textarea
3. User selects flag type, optionally adds details, clicks "Submit Flag"
4. JS calls `POST /api/v1/games/<game_id>/flag/`
5. Service checks for existing pending flag (same user, game, type)
6. If pending exists: returns existing flag (silent success)
7. Otherwise: creates new GameFlag with `status='pending'`
8. User sees success toast

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
| POST | `/api/v1/games/<game_id>/flag/` | User (linked PSN) | 5/min | Submit a game flag |

**Request body**: `{ "flag_type": "delisted", "details": "Removed from store in March 2026" }`

**Response**: `{ "success": true, "message": "..." }`

## Integration Points

- **Game detail page**: Flag button in the action buttons row (same row as Share Card and Add to List)
- **Django admin**: GameFlagAdmin with approve/dismiss bulk actions
- **Shovelware system**: Approving `is_shovelware`/`not_shovelware` flags sets `shovelware_lock=True`, preventing auto-detection from overwriting the manual decision

## Gotchas and Pitfalls

- **No unique constraint**: Duplicate prevention is in the service layer, not the database. This is intentional: the "allow re-flagging after approval/dismissal" requirement means a DB unique constraint would be too restrictive.
- **`missing_vr` and `region_incorrect` require manual action**: Approving these flags marks them as approved but does NOT automatically change any Game fields. Staff must manually edit the Game in admin to add VR platforms or fix region data.
- **Shovelware lock**: Approving shovelware flags sets `shovelware_lock=True`, which prevents the automated shovelware detection service from overwriting the manual decision. This is the correct behavior.
- **Rate limit is per-minute, not per-hour**: Flags use 5/min (vs 5/hour for comment reports) because users may flag multiple games in one session.

## Related Docs

- [Shovelware Detection](../reference/shovelware-detection.md): how automated shovelware detection works and interacts with manual flags
- [Data Model](../architecture/data-model.md): Game and Concept model relationships
