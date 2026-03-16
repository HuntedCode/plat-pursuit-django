# Challenge Systems

Comprehensive documentation for Platinum Pursuit's three challenge types: A-Z Platinum Challenge, Platinum Calendar Challenge, and Genre Challenge. All three share a common `Challenge` base model and follow the same lifecycle (create, fill slots, sync-driven progress, completion, Hall of Fame).

---

## Architecture Overview

### Single-Table Inheritance via Discriminator

All challenges share the `Challenge` model, with `challenge_type` acting as a discriminator (`'az'`, `'calendar'`, `'genre'`). Each type has its own slot/day model linked via FK:

```
Challenge (challenge_type)
  ├── AZChallengeSlot        (26 rows, FK to Game)
  ├── CalendarChallengeDay   (365 rows, FK to Game)
  ├── GenreChallengeSlot     (N rows, FK to Concept)
  └── GenreBonusSlot         (variable, FK to Concept)
```

### Ownership Model

- One active (non-deleted, non-complete) challenge per type per user.
- Completed challenges remain visible in the Hall of Fame and user history.
- Soft-delete (`is_deleted=True`) hides from public views; owner can still see.

### Entity Level: Game vs. Concept

A-Z and Calendar challenges operate at the **Game** level (individual PSN title entries). Genre challenges operate at the **Concept** level (unified cross-platform grouping). This distinction matters for progress checking and search: A-Z checks `ProfileGame.has_plat` per game; Genre checks if *any* game under a Concept is platted.

---

## File Map

### Service Layer
| File | Description |
|------|-------------|
| `trophies/services/challenge_service.py` (~960 lines) | Core service: create, progress check, backfill, stats, subgenre resolution |
| `trophies/services/holiday_service.py` | Holiday data for calendar day highlights |

### Page Views
| File | Description |
|------|-------------|
| `trophies/views/challenge_views.py` (~1,150 lines) | All page-level views: hub, my challenges, create/setup/detail/edit for all types |

### API Views
| File | Description |
|------|-------------|
| `api/az_challenge_views.py` | A-Z CRUD, slot assign/clear, game search |
| `api/calendar_challenge_views.py` | Calendar CRUD, day detail (all platinums for a day) |
| `api/genre_challenge_views.py` | Genre CRUD, slot assign/clear, bonus add/clear, move, concept search |
| `api/az_challenge_share_views.py` | A-Z share card HTML rendering + PNG generation |
| `api/calendar_challenge_share_views.py` | Calendar share card HTML rendering + PNG generation |
| `api/genre_challenge_share_views.py` | Genre share card HTML rendering + PNG generation |

### Frontend JS
| File | Description |
|------|-------------|
| `static/js/az-challenge.js` | A-Z setup wizard, detail page, edit page, spinner picker |
| `static/js/calendar-challenge.js` | Calendar detail page, day click modal |
| `static/js/genre-challenge.js` | Genre setup wizard, detail page, edit page, subgenre tracker |
| `static/js/az-challenge-share.js` | A-Z share card manager (extends ShareImageManager) |
| `static/js/calendar-challenge-share.js` | Calendar share card manager |
| `static/js/genre-challenge-share.js` | Genre share card manager |

### Templates
| File | Description |
|------|-------------|
| `templates/trophies/challenge_hub.html` | Public hub with type tabs, sub-tabs, search, sort |
| `templates/trophies/my_challenges.html` | User's dashboard: active challenges, create CTAs, history |
| `templates/trophies/az_challenge_create.html` | A-Z create form |
| `templates/trophies/az_challenge_setup.html` | A-Z guided wizard |
| `templates/trophies/az_challenge_detail.html` | A-Z public progress view |
| `templates/trophies/az_challenge_edit.html` | A-Z owner edit page |
| `templates/trophies/calendar_challenge_create.html` | Calendar create form |
| `templates/trophies/calendar_challenge_detail.html` | Calendar 12-month grid view |
| `templates/trophies/genre_challenge_create.html` | Genre create form with genre grid preview |
| `templates/trophies/genre_challenge_setup.html` | Genre guided wizard |
| `templates/trophies/genre_challenge_detail.html` | Genre public progress view with subgenre tracker |
| `templates/trophies/genre_challenge_edit.html` | Genre owner edit page with move/bonus management |
| `templates/partials/challenge_hub_cards.html` | AJAX partial for infinite scroll hub cards |
| `templates/partials/challenge_hub_panel.html` | Hub panel layout partial |
| `templates/trophies/partials/az_challenge_share_card.html` | A-Z share card template |
| `templates/trophies/partials/calendar_challenge_share_card.html` | Calendar share card template |
| `templates/trophies/partials/genre_challenge_share_card.html` | Genre share card template |

### Models & Constants
| File | Description |
|------|-------------|
| `trophies/models.py` (lines ~3131-3340) | Challenge, AZChallengeSlot, CalendarChallengeDay, GenreChallengeSlot, GenreBonusSlot, CALENDAR_DAYS_PER_MONTH |
| `trophies/util_modules/constants.py` (lines ~94-234) | GENRE_CHALLENGE_GENRES, GENRE_DISPLAY_NAMES, GENRE_MERGE_MAP, GENRE_CHALLENGE_SUBGENRES, SUBGENRE_DISPLAY_NAMES, SUBGENRE_MERGE_MAP |

### Sync Integration
| File | Description |
|------|-------------|
| `trophies/token_keeper.py` (line ~1148) | Calls all three `check_*_challenge_progress()` in `_job_sync_complete()` |

---

## Data Model

### Challenge (base)

```
Challenge
  profile             FK -> Profile (CASCADE)
  challenge_type      'az' | 'calendar' | 'genre'
  name                CharField(75)
  description         TextField (optional, max 2000 via API)
  total_items         26 (az) | 365 (calendar) | len(GENRE_CHALLENGE_GENRES) (genre)
  filled_count        Slots with a game/concept assigned
  completed_count     Slots marked complete (plat earned)
  view_count          Atomic increment on detail page visit
  cover_letter        A-Z only: letter whose game icon is the challenge thumbnail
  cover_genre         Genre only: genre whose concept icon is the challenge thumbnail
  subgenre_count      Genre only: unique curated subgenres across all assigned concepts
  platted_subgenre_count  Genre only: subgenres where at least one contributing slot is completed
  bonus_count         Genre only: number of bonus slots with a concept assigned
  is_complete         True when completed_count == total_items
  completed_at        Timestamp of completion
  is_deleted          Soft delete flag
  deleted_at          Soft delete timestamp
  created_at          Auto
  updated_at          Auto
```

**Indexes:**
- `(profile, is_deleted, challenge_type)` for per-user queries
- `(challenge_type, is_deleted, is_complete)` for hub browsing
- `(challenge_type, is_deleted, -completed_count)` for progress sorting

**Computed property:** `progress_percentage` = `int((completed_count / total_items) * 100)`

### AZChallengeSlot

```
AZChallengeSlot
  challenge       FK -> Challenge (CASCADE), related_name='az_slots'
  letter          CharField(1), e.g. 'A'
  game            FK -> Game (SET_NULL, nullable)
  is_completed    Boolean
  completed_at    DateTime (nullable)
  assigned_at     DateTime (nullable)

  unique_together: (challenge, letter)
  ordering: ['letter']
```

### CalendarChallengeDay

```
CalendarChallengeDay
  challenge           FK -> Challenge (CASCADE), related_name='calendar_days'
  month               PositiveSmallIntegerField (1-12)
  day                 PositiveSmallIntegerField (1-31)
  game                FK -> Game (SET_NULL, nullable) -- first plat that filled this day
  is_filled           Boolean
  filled_at           DateTime (nullable)
  platinum_earned_at  DateTime (nullable) -- actual earn timestamp of the first matching plat
  plat_count          PositiveSmallIntegerField -- total plats across all years for this day

  unique_together: (challenge, month, day)
  ordering: ['month', 'day']
  index: (challenge, is_filled)
```

**CALENDAR_DAYS_PER_MONTH:** Standard non-leap-year counts. Feb always has 28 days. Feb 29 is universally excluded (even in leap years).

### GenreChallengeSlot

```
GenreChallengeSlot
  challenge       FK -> Challenge (CASCADE), related_name='genre_slots'
  genre           CharField(50), e.g. 'ACTION'
  genre_display   CharField(100), e.g. 'Action'
  concept         FK -> Concept (SET_NULL, nullable)
  is_completed    Boolean
  completed_at    DateTime (nullable)
  assigned_at     DateTime (nullable)

  unique_together: (challenge, genre)
  ordering: ['genre']
  index: (challenge, is_completed)
```

### GenreBonusSlot

```
GenreBonusSlot
  challenge       FK -> Challenge (CASCADE), related_name='bonus_slots'
  concept         FK -> Concept (SET_NULL, nullable)
  is_completed    Boolean
  completed_at    DateTime (nullable)
  assigned_at     DateTime (auto_now_add)

  unique_together: (challenge, concept)
  ordering: ['assigned_at']
  index: (challenge, is_completed)
```

---

## Key Flows

### 1. A-Z Platinum Challenge

#### Creation

1. User navigates to `/challenges/az/create/` (or `POST /api/v1/challenges/az/`).
2. `dispatch()` guard: redirects if no linked PSN profile, or if an active A-Z challenge already exists.
3. `create_az_challenge(profile, name)`:
   - Validates no active AZ challenge exists (raises `ValueError` otherwise).
   - Creates a `Challenge` row with `challenge_type='az'`, `total_items=26`.
   - `bulk_create`s 26 `AZChallengeSlot` rows (A through Z), all empty.
4. Redirects to the setup wizard (`/challenges/az/<id>/setup/`).

#### Setup Wizard

Owner-only. Presents a letter-by-letter interface where the user searches for games and assigns one per letter.

**Game Search** (`GET /api/v1/challenges/az/game-search/`):
- Required: `letter` (single A-Z character).
- Optional: `q` (text search within letter), `platform` (comma-separated), `region` (comma-separated), `sort` (popular|alpha|plat_earners), `in_badge` (boolean), `my_backlog` (boolean), `challenge_id` (exclude already-used games), pagination via `offset`/`limit`.
- Filters: must start with the letter, must have a platinum trophy, `is_obtainable=True`, excludes shovelware.
- **Exclusion logic** (`get_excluded_game_ids`): games where user has earned >= 50% of base-game trophies (which also covers platinumed games at 100%). Expands via Concept siblings and GameFamily siblings to prevent picking a different regional/platform version of an already-progressed game.
- Annotates plat earner count and batch-fetches community ratings per result.

**Slot Assignment** (`POST /api/v1/challenges/az/<id>/slots/<letter>/assign/`):
- Validates: letter is A-Z, slot exists, slot not completed, game exists, game starts with the letter, game not excluded, game not already in another slot.
- Sets `slot.game`, `slot.assigned_at`.
- Recalculates `filled_count`/`completed_count` on the challenge.
- Auto-sets `cover_letter` on first assignment (random from assigned slots).

**Slot Clearing** (`DELETE /api/v1/challenges/az/<id>/slots/<letter>/clear/`):
- Validates slot not completed.
- Nulls `slot.game` and `slot.assigned_at`.
- If the cleared slot was the `cover_letter`, picks a new random cover.

#### Sync-Driven Progress

Called in `_job_sync_complete()` via `check_az_challenge_progress(profile)`:

1. Fetches all active, non-complete A-Z challenges for the profile.
2. For each challenge, gets pending (assigned but not completed) slots.
3. Batch-queries `ProfileGame.has_plat` for all pending game IDs.
4. `bulk_update` marks newly-platted slots as `is_completed=True` with timestamp.
5. Recalculates challenge counts from DB.
6. If all 26 completed: sets `is_complete=True`, creates completion notification, enters Hall of Fame.
7. Triggers `check_all_milestones_for_user(profile, criteria_type='az_progress')`.

#### Completion

- `is_complete=True`, `completed_at` timestamped.
- Notification: type `'challenge_completed'`, links to `/challenges/az/<id>/`.
- Challenge appears in Hall of Fame tab on the Challenge Hub.

#### Detail Page

Public. Shows 26-slot grid with game icons, platform badges, completion status. Batch-fetches owner's `ProfileGame` progress for progress bars on each slot. Owner sees edit button, theme picker for share card, and "Pick My Next Game" spinner (randomizer for unfinished assigned slots). View count incremented atomically.

#### Edit Page

Owner-only. Same 26-slot grid with swap/clear actions. Completed slots are locked and cannot be changed.

---

### 2. Platinum Calendar Challenge

#### Creation

1. User navigates to `/challenges/calendar/create/` (or `POST /api/v1/challenges/calendar/`).
2. Same guard pattern: linked profile required, no existing active calendar challenge.
3. `create_calendar_challenge(profile, name)`:
   - Creates `Challenge` with `challenge_type='calendar'`, `total_items=365`.
   - `bulk_create`s 365 `CalendarChallengeDay` rows (Jan 1 through Dec 31, skipping Feb 29).
   - **Auto-backfills** from existing platinum history immediately.
4. Redirects directly to the detail page (no wizard needed).

#### Reconciliation Engine (`_reconcile_calendar_days`)

Both creation/backfill and sync-driven progress use a shared reconciliation helper. This function performs a **bidirectional** reconciliation: filling new days AND unfilling phantom days.

1. Resolves user timezone via `profile.user.user_timezone` (falls back to UTC).
2. Fetches all earned platinum trophies with dates, excluding shovelware and hidden trophies, ordered oldest first.
3. Converts each earn timestamp to the user's local timezone.
4. For each (month, day) pair (skipping Feb 29):
   - **Has qualifying platinums + not filled:** Fills the day with the **earliest** platinum.
   - **Has qualifying platinums + already filled:** Updates `platinum_earned_at`/`game_id` if the earliest platinum changed.
   - **No qualifying platinums + filled:** **Unfills** the phantom day (clears `is_filled`, `filled_at`, `platinum_earned_at`, `game_id`).
   - Always reconciles `plat_count`.
5. Returns fill/unfill counts and changed rows for the caller to `bulk_update`.

#### Auto-Backfill (`backfill_calendar_from_history`)

Runs at creation time and on timezone changes:

1. Calls `_reconcile_calendar_days()` for full bidirectional reconciliation.
2. `bulk_update` all changed day rows.
3. Recalculates challenge counts. If already 365/365, marks complete immediately.
4. If unfills reverted a completed calendar, clears `is_complete`.
5. Checks calendar milestones.

#### Sync-Driven Progress (`check_calendar_challenge_progress`)

1. For each active calendar challenge:
   - Calls `_reconcile_calendar_days()` for full bidirectional reconciliation.
   - `bulk_update` changed rows.
   - If days were filled or unfilled: recalculate counts from the DB, check for completion.
   - If only `plat_count` changed (no fill/unfill changes): advances `updated_at` watermark.
2. Triggers calendar milestones via `_check_calendar_milestones(profile)`.

#### Detail Page

Public. Renders a 12-month calendar grid using `get_calendar_month_data()`:
- Each month shows day cells with proper weekday alignment (Sunday-start grid).
- Filled days show the game icon; clicking opens a modal (JS-driven) showing ALL platinums earned on that calendar day.
- Holiday highlights (international + US) via `holiday_service.get_holidays_for_js()`.
- Stats panel: total filled, best/worst month, longest streak, busiest day.
- Owner sees theme picker for share card.

**Day Detail API** (`GET /api/v1/challenges/calendar/<id>/day/<month>/<day>/`):
- Public endpoint (not auth-required).
- Returns all platinums matching that (month, day) in the owner's timezone, with game info, earn date, year, and game page URL.
- Validates month/day ranges; explicitly rejects Feb 29.

#### No Edit Page

Calendar challenges have no edit or setup wizard. Days are filled automatically by the system based on platinum earn dates. The only user actions are create, rename, describe, and delete.

---

### 3. Genre Challenge

#### Creation

1. User navigates to `/challenges/genre/create/` (or `POST /api/v1/challenges/genre/`).
2. Same guards: linked profile, no active genre challenge.
3. `create_genre_challenge(profile, name)`:
   - Creates `Challenge` with `challenge_type='genre'`, `total_items=len(GENRE_CHALLENGE_GENRES)` (currently 16).
   - `bulk_create`s one `GenreChallengeSlot` per curated genre, with display name.
4. Redirects to the setup wizard.

#### Genre List (16 genres)

Action, Adventure, Arcade, Casual, Family, Fighting, Horror, Music / Rhythm, Puzzle, Racing, RPG, Shooter, Simulation, Sports, Strategy, Unique.

**Genre Merge Map:** Raw PSN tags are normalized: `SIMULATOR` -> `SIMULATION`, `MUSIC/RHYTHM` -> `MUSIC_RHYTHM`, `PARTY` -> `FAMILY`.

#### Subgenre Tracker (32 subgenres)

A secondary collection mechanic. Each assigned concept contributes its subgenres to the challenge's total. Subgenres have three states: **uncollected**, **assigned** (contributed by an assigned-but-not-completed slot), **platted** (contributed by a completed slot). Raw PSN subgenre strings are resolved through `SUBGENRE_MERGE_MAP` (e.g., `2D_FIGHTING`/`3D_FIGHTING`/`TEAM_FIGHTING` all map to `FIGHTING`).

Subgenre counts are persisted on the Challenge model (`subgenre_count`, `platted_subgenre_count`) and recalculated on every slot assignment, clear, or sync-driven completion.

#### Setup Wizard

Owner-only. Presents a genre-by-genre interface where the user searches for concepts.

**Concept Search** (`GET /api/v1/challenges/genre/concept-search/`):
- Required: `genre` (one of 16 keys) in `mode=genre`; omittable in `mode=bonus`.
- Optional: `q`, `subgenre` (comma-separated, AND logic), `platform`, `region`, `sort` (popular|alpha|plat_earners), `in_badge`, `my_backlog`, `new_subgenres_only`, `challenge_id`, pagination.
- Filters: excludes PP_ stub concepts, must have PS4/PS5 game with platinum, excludes shovelware, excludes already-assigned concepts (both genre slots and bonus slots).
- **Exclusion logic** (`get_genre_excluded_concept_ids`): concepts where user has platted any game under the concept, or has >50% progress. Expands via GameFamily siblings to prevent cross-gen duplicates.
- `new_subgenres_only` filter: excludes concepts whose resolved subgenres are all already collected in the challenge. Useful for subgenre hunting.
- Returns subgenre facet counts from the filtered result set (before pagination) so the UI can show per-subgenre availability.
- Batch-fetches community ratings and platform info per result.

**Slot Assignment** (`POST /api/v1/challenges/genre/<id>/slots/<genre>/assign/`):
- Validates: genre is valid, slot exists, slot not completed, concept exists, concept is not a PP_ stub, concept has PS4/PS5 game, concept has the target genre (handling merge map), concept has no shovelware-flagged games, concept not excluded, concept not already in another genre slot or bonus slot.
- Sets `slot.concept`, `slot.assigned_at`.
- Recalculates all counts (filled, completed, subgenre, platted_subgenre, bonus).
- Auto-sets `cover_genre` on first assignment.
- Returns updated subgenre status for live tracker updates.

**Slot Clearing** (`DELETE /api/v1/challenges/genre/<id>/slots/<genre>/clear/`):
- Validates slot not completed.
- Nulls concept and assigned_at.
- Recalculates all counts. Re-picks cover if needed.

#### Bonus Slots

Bonus slots allow adding extra games for subgenre hunting without a genre restriction. They do not count toward the core genre completion (16/16) but do contribute subgenres to the tracker and are checked for platinum progress during sync.

**Add Bonus** (`POST /api/v1/challenges/genre/<id>/bonus/add/`):
- Same validation as genre slot (PP_ stub, PS4/PS5, shovelware, exclusion, not already used).
- Creates a new `GenreBonusSlot` row.

**Clear Bonus** (`DELETE /api/v1/challenges/genre/<id>/bonus/<bonus_slot_id>/clear/`):
- Hard-deletes the bonus slot row (not soft-delete, since bonus slots are lightweight).

#### Move System

Concepts can be moved between genre slots and bonus slots:

**Move** (`POST /api/v1/challenges/genre/<id>/move/`):
- Takes `source_type` (genre|bonus), `source_id`, `dest_type` (genre|bonus), `dest_id`.
- Genre-to-bonus: clears genre slot, creates bonus slot. No genre restriction needed for bonus.
- Bonus-to-genre: deletes bonus slot, assigns to genre slot. Validates concept has the destination genre.
- Genre-to-genre: clears source slot, assigns to destination slot. Validates genre compatibility.
- Completed slots cannot be moved.
- Destination genre slots must be empty (no swap; clear first).

**Move Targets** (`GET /api/v1/challenges/genre/<id>/move-targets/`):
- Given a source slot, returns valid destination genre slots (empty, not completed, concept has that genre) plus bonus as an option.

#### Sync-Driven Progress (`check_genre_challenge_progress`)

1. Fetches all active genre challenges for the profile, prefetching genre_slots and bonus_slots.
2. Collects all pending (assigned, not completed) slots across genre and bonus.
3. Builds a concept-to-game-IDs map, then batch-checks `ProfileGame.has_plat`.
4. A concept is "platted" if ANY of its games are platted by the user.
5. `bulk_update` marks newly-completed genre slots and bonus slots.
6. Recalculates all counts. If all genre slots are completed, marks challenge complete.
7. Triggers genre and subgenre milestone checks.

#### Detail Page

Public. Shows genre slot grid with concept icons, progress percentages, completion state, and resolved subgenres per slot. Also shows bonus slots section and the full subgenre tracker (32 subgenres, three-state). Owner sees edit button, theme picker, and "Pick My Next Game" spinner.

---

## API Endpoints

### A-Z Challenge

| Method | Path | Auth | Rate Limit | Description |
|--------|------|------|------------|-------------|
| POST | `/api/v1/challenges/az/` | User | 10/h | Create new A-Z challenge |
| GET | `/api/v1/challenges/az/<id>/` | Public | 60/m (IP) | Get challenge with slots |
| PATCH | `/api/v1/challenges/az/<id>/update/` | Owner | 30/m | Update name/description/cover_letter |
| DELETE | `/api/v1/challenges/az/<id>/delete/` | Owner | 10/m | Soft delete |
| POST | `/api/v1/challenges/az/<id>/slots/<letter>/assign/` | Owner | 60/m | Assign game to letter |
| DELETE | `/api/v1/challenges/az/<id>/slots/<letter>/clear/` | Owner | 60/m | Clear game from letter |
| GET | `/api/v1/challenges/az/game-search/` | User | 120/m | Search games by letter |
| GET | `/api/v1/challenges/az/<id>/share/html/` | Owner | - | Share card HTML preview |
| GET | `/api/v1/challenges/az/<id>/share/png/` | Owner | - | Share card PNG download |

### Calendar Challenge

| Method | Path | Auth | Rate Limit | Description |
|--------|------|------|------------|-------------|
| POST | `/api/v1/challenges/calendar/` | User | 10/h | Create with auto-backfill |
| GET | `/api/v1/challenges/calendar/<id>/` | Public | 60/m (IP) | Get challenge details |
| PATCH | `/api/v1/challenges/calendar/<id>/update/` | Owner | 30/m | Update name/description |
| DELETE | `/api/v1/challenges/calendar/<id>/delete/` | Owner | 10/m | Soft delete |
| GET | `/api/v1/challenges/calendar/<id>/day/<month>/<day>/` | Public | 120/m (IP) | All platinums for a day |
| GET | `/api/v1/challenges/calendar/<id>/share/html/` | Owner | - | Share card HTML preview |
| GET | `/api/v1/challenges/calendar/<id>/share/png/` | Owner | - | Share card PNG download |

### Genre Challenge

| Method | Path | Auth | Rate Limit | Description |
|--------|------|------|------------|-------------|
| POST | `/api/v1/challenges/genre/` | User | 10/h | Create new genre challenge |
| GET | `/api/v1/challenges/genre/<id>/` | Public | 60/m (IP) | Get challenge with slots + subgenres |
| PATCH | `/api/v1/challenges/genre/<id>/update/` | Owner | 30/m | Update name/description/cover_genre |
| DELETE | `/api/v1/challenges/genre/<id>/delete/` | Owner | 10/m | Soft delete |
| POST | `/api/v1/challenges/genre/<id>/slots/<genre>/assign/` | Owner | 60/m | Assign concept to genre |
| DELETE | `/api/v1/challenges/genre/<id>/slots/<genre>/clear/` | Owner | 60/m | Clear concept from genre |
| GET | `/api/v1/challenges/genre/concept-search/` | User | 120/m | Search concepts by genre |
| POST | `/api/v1/challenges/genre/<id>/bonus/add/` | Owner | 60/m | Add bonus concept |
| DELETE | `/api/v1/challenges/genre/<id>/bonus/<slot_id>/clear/` | Owner | 60/m | Remove bonus concept |
| POST | `/api/v1/challenges/genre/<id>/move/` | Owner | 60/m | Move concept between slots |
| GET | `/api/v1/challenges/genre/<id>/move-targets/` | Owner | 120/m | Get valid move destinations |
| GET | `/api/v1/challenges/genre/<id>/share/html/` | Owner | - | Share card HTML preview |
| GET | `/api/v1/challenges/genre/<id>/share/png/` | Owner | - | Share card PNG download |

### Page Routes

| Path | View | Auth | Description |
|------|------|------|-------------|
| `/challenges/` | ChallengeHubView | Public | Hub with type tabs + sub-tabs |
| `/my-challenges/` | MyChallengesView | Login | User's active + history |
| `/challenges/az/create/` | AZChallengeCreateView | Login | A-Z create form |
| `/challenges/az/<id>/` | AZChallengeDetailView | Public | A-Z detail |
| `/challenges/az/<id>/setup/` | AZChallengeSetupView | Owner | A-Z wizard |
| `/challenges/az/<id>/edit/` | AZChallengeEditView | Owner | A-Z edit |
| `/challenges/calendar/create/` | CalendarChallengeCreateView | Login | Calendar create form |
| `/challenges/calendar/<id>/` | CalendarChallengeDetailView | Public | Calendar detail |
| `/challenges/genre/create/` | GenreChallengeCreateView | Login | Genre create form |
| `/challenges/genre/<id>/` | GenreChallengeDetailView | Public | Genre detail |
| `/challenges/genre/<id>/setup/` | GenreChallengeSetupView | Owner | Genre wizard |
| `/challenges/genre/<id>/edit/` | GenreChallengeEditView | Owner | Genre edit |

---

## Integration Points

### Sync Pipeline (TokenKeeper)

In `_job_sync_complete()` (around line 1148 of `token_keeper.py`), after ProfileGame stats are updated and general milestones are checked:

```python
check_az_challenge_progress(profile)
check_calendar_challenge_progress(profile)
check_genre_challenge_progress(profile)
```

Each wrapped in its own try/except so one failure does not block the others or the rest of sync completion. Challenge-specific milestone types (`az_progress`, `genre_progress`, `subgenre_progress`, all calendar types) are excluded from the general milestone check earlier in the pipeline to avoid duplicate work.

### Milestone System

Challenge progress feeds into milestones:
- **A-Z:** `check_all_milestones_for_user(profile, criteria_type='az_progress')` after any AZ slot completion.
- **Calendar:** `_check_calendar_milestones(profile)` checks all calendar-related milestone types in one batch via `ALL_CALENDAR_TYPES`.
- **Genre:** `check_all_milestones_for_user(profile, criteria_types=['genre_progress', 'subgenre_progress'])` after genre slot completion.

### Notifications

- **Completion notification** (`_create_completion_notification`): creates a `challenge_completed` notification with challenge metadata. Dispatched for all three types when `is_complete` flips to True.

### Concept.absorb()

The `Concept.absorb()` method handles `Genre challenge slots` and `bonus slots` via the `genre_challenge_slots` and `genre_bonus_slots` reverse relations. When concepts are reassigned during sync, any genre challenge slots pointing to the absorbed concept are migrated to the surviving concept.

### Share Cards

Each challenge type has HTML + PNG share card generation endpoints. The HTML view renders a styled card template; the PNG view captures it as an image. Share card managers on the frontend extend `ShareImageManager` from `utils.js` and provide theme selection.

### Page View Tracking

All challenge page views are tracked via `track_page_view()` from `core/services/tracking.py`. Challenge creation events tracked via `track_site_event('challenge_create', ...)`.

---

## Gotchas

### Feb 29 is Always Excluded
The calendar challenge uses 365 days with no Feb 29, even in leap years. `CALENDAR_DAYS_PER_MONTH` hardcodes February at 28 days. Platinums earned on Feb 29 are silently skipped during both backfill and sync. The day detail API explicitly rejects month=2, day=29 with a 400 error.

### Calendar Weekday Alignment Uses Current Year
The `_get_calendar_year()` function returns `timezone.now().year`. This means the visual grid layout (which day of the week each month starts on) changes on January 1st. The data does not change, only the visual alignment.

### Timezone Sensitivity
Calendar day matching converts platinum `earned_date_time` to the user's timezone (`profile.user.user_timezone`). A platinum earned at 11:30 PM EST on Dec 31 would fill Jan 1 for a UTC user. When a user changes their timezone, both `UpdateTimezoneAPIView` and `UpdateQuickSettingsAPIView` trigger `backfill_calendar_from_history()` which runs a full bidirectional reconciliation: old phantom days are unfilled and platinums are re-mapped to their correct (month, day) under the new timezone.

### Calendar Day Unfill Behavior
Calendar days CAN be unfilled when the qualifying platinum set changes. This happens when games are flagged as shovelware, games are hidden by the user, or timezone changes shift a platinum to a different (month, day). The reconciliation engine (`_reconcile_calendar_days`) handles this on every sync and on timezone changes. If unfilling reverts a completed (365/365) calendar, `is_complete` is cleared. Use `python manage.py recalculate_calendars` to force a full reconciliation across all calendars. Use `python manage.py audit_calendar --username <name>` to diagnose why specific platinums are missing from a user's calendar (shows every filter check per platinum and cross-references with actual calendar day state).

### Game vs. Concept Level
A-Z and Calendar challenges use `Game` (individual PSN title). Genre challenges use `Concept` (cross-platform grouping). This means:
- A-Z: the user picks a specific game version; completion checks `ProfileGame.has_plat` for that exact game.
- Genre: the user picks a concept; completion checks if ANY game under that concept has `has_plat=True`. This naturally handles users who plat a different version than expected.

### Exclusion Expansion
Both A-Z and Genre challenges expand their exclusion sets through related entities:
- **A-Z** (`get_excluded_game_ids`): Game -> Concept siblings -> GameFamily siblings. Prevents picking e.g. the EU version of a game you already platted the NA version of.
- **Genre** (`get_genre_excluded_concept_ids`): Concept -> GameFamily siblings. Prevents picking the PS4 concept when you already platted the PS5 concept of the same family.

### Genre Merge Map
Some PSN genre tags need normalization: `SIMULATOR` -> `SIMULATION`, `MUSIC/RHYTHM` -> `MUSIC_RHYTHM`, `PARTY` -> `FAMILY`. The assignment endpoint accepts concepts tagged with either the raw or curated key. The search endpoint handles the reverse mapping when filtering.

### Subgenre Merge Map
Multiple raw PSN subgenre strings collapse to single curated keys. For example, `2D_FIGHTING`, `3D_FIGHTING`, and `TEAM_FIGHTING` all map to `FIGHTING`. `resolve_subgenres()` handles this mapping and filters out uncurated values (N/A, MMORPG, etc.).

### Completed Slots Are Locked
Once a slot's `is_completed` is True (platinum earned), it cannot be cleared, reassigned, or moved. This is enforced at the API level for all three types. The rationale: the achievement is permanent, and allowing removal would undermine challenge integrity.

### Soft Delete vs. Hard Delete
- Challenges use soft delete (`is_deleted=True`). Deleted challenges are visible only to the owner.
- Genre bonus slots use hard delete (the row is removed from the database). This is intentional because bonus slots are lightweight and have no completion history worth preserving.

### Cover Image Auto-Selection
- A-Z: `auto_set_cover_letter` picks a random letter from assigned slots. Re-picks when the cover slot is cleared.
- Genre: `auto_set_cover_genre` picks a random genre from assigned slots. Same re-pick behavior.
- Both only auto-set on first assignment (when cover is empty). Manual override via the update API persists.

### One Active Challenge Per Type
The system enforces at most one active (non-deleted, non-complete) challenge per type per user. This is checked at both the service level (`create_*_challenge` raises `ValueError`) and the view level (`dispatch()` redirects to the existing challenge). A user can have all three types active simultaneously.

### Hub Performance
The Challenge Hub loads lightweight badge counts for all three type tabs, then queries only the active type in full. AJAX pagination returns only card partials. Calendar hub cards use `_get_mini_calendar_data()`, which runs a single aggregate query per calendar instead of fetching all 365 day objects.

### Bulk Operations
All three progress checkers use `bulk_update` rather than per-row saves to minimize database writes during sync. The A-Z checker processes all pending slots in one batch; the calendar checker processes all 365 days in one batch; the genre checker processes both genre and bonus slots in separate batches.
