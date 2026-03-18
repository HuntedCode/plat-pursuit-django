# Easter Eggs

Hidden discoveries throughout the site that reward observant and lucky users with milestones, titles, and bragging rights. Easter egg milestones use the `manual` criteria type and are only visible on the milestones page once earned.

## Architecture Overview

Easter eggs are decoupled into three layers:

1. **Frontend trigger**: JS detects the easter egg event and calls both the tracking API (analytics) and the claim API (milestone award).
2. **Claim API**: `POST /api/v1/easter-eggs/claim/` with a server-side `EASTER_EGG_MILESTONES` mapping that translates `easter_egg_id` strings to milestone names. The client never specifies which milestone to award.
3. **Milestone service**: `award_manual_milestone()` handles all side effects (UserMilestone, UserTitle, Discord role, notification, dashboard cache).

Adding a new easter egg requires: (1) a new milestone definition in `populate_milestones.py`, (2) one new entry in the `EASTER_EGG_MILESTONES` dict, and (3) a frontend trigger that calls the claim endpoint.

## File Map

| File | Purpose |
|------|---------|
| `api/easter_egg_views.py` | Claim API endpoint with server-side easter egg -> milestone mapping |
| `api/urls.py` | URL registration for `/api/v1/easter-eggs/claim/` |
| `trophies/services/milestone_service.py` | `award_manual_milestone()` and `award_milestone_directly()` |
| `trophies/management/commands/populate_milestones.py` | Milestone definitions (manual section) |
| `static/js/reel-spinner.js` | Knife easter egg frontend trigger |

## Current Easter Eggs

### Knife Landing (Reel Spinner)

| Property | Value |
|----------|-------|
| Easter egg ID | `knife_landed` |
| Milestone name | Unboxed! |
| Title awarded | Case Hardened |
| Probability | 1 in 1,000 per spin |
| Location | Reel spinner (A-Z Challenge, Genre Challenge) |

The reel spinner has a 0.1% chance per spin to land on a CS:GO-style knife tile. When this happens, the user sees a gold confetti celebration, dramatic fanfare, and the "KNIFE!" result card. The frontend then calls the claim endpoint to award the "Unboxed!" milestone and "Case Hardened" title.

On repeat knife landings (milestone already earned), the celebration still plays but no toast notification appears.

## API Endpoints

| Method | Path | Auth | Purpose |
|--------|------|------|---------|
| POST | `/api/v1/easter-eggs/claim/` | Login | Claim easter egg milestone |

**Request body**: `{"easter_egg_id": "knife_landed"}`

**Responses**:
- `200 {"awarded": true, "milestone_name": "...", "title_name": "..."}`: newly awarded
- `200 {"awarded": false, "already_earned": true}`: idempotent repeat
- `400`: unknown easter_egg_id
- `403`: no linked PSN profile
- `429`: rate limited (10/min per user)

## Integration Points

- [Badge System](../architecture/badge-system.md): milestone service, title system, milestone display
- [Fundraiser](fundraiser.md): also uses `award_manual_milestone()` for the "Badge Artwork Patron" milestone

## Adding a New Easter Egg

1. Define the milestone in `populate_milestones.py` under the manual section:
   ```python
   {'name': 'My Easter Egg', 'criteria_type': 'manual', 'criteria_details': {'target': 1},
    'description': 'Flavor text here.', 'title_name': 'Optional Title'},
   ```
2. Run `python manage.py populate_milestones` to create it.
3. Add the mapping in `api/easter_egg_views.py`:
   ```python
   EASTER_EGG_MILESTONES = {
       'knife_landed': 'Unboxed!',
       'my_new_egg': 'My Easter Egg',  # new
   }
   ```
4. Add the frontend trigger that calls:
   ```javascript
   PlatPursuit.API.post('/api/v1/easter-eggs/claim/', {
       easter_egg_id: 'my_new_egg'
   }).then(function(data) { ... });
   ```

## Gotchas and Pitfalls

- **Server-side mapping is the security boundary.** The client sends an opaque `easter_egg_id`, not a milestone name. Users cannot award arbitrary milestones through this endpoint.
- **Rate limiting**: 10 requests/min per user. Legitimate easter eggs are rare events, so this is generous but prevents scripted abuse.
- **Idempotency**: `award_manual_milestone()` uses `get_or_create()` internally. Duplicate calls are harmless (no errors, no double-awards, no duplicate notifications).
- **Manual milestone visibility**: Manual milestones only appear on the Special tab of the milestones page when earned. Unearned manual milestones are hidden. This is handled in `badge_views.py::_build_category_data()`.
- **Manual handler quirk**: The `handle_manual()` handler always returns `achieved=False` for unevaluated milestones. This is by design: manual milestones are awarded through `award_milestone_directly()`, not through the handler pipeline.
