# Easter Eggs

Hidden discoveries throughout the site that reward observant and lucky users with milestones, titles, and bragging rights. Easter egg milestones use the `manual` criteria type and are only visible on the milestones page once earned.

## Architecture Overview

Easter eggs use a four-layer architecture with server-side probability enforcement:

1. **Roll API**: `POST /api/v1/easter-eggs/roll/` performs the probability roll server-side. If the easter egg "lands," a one-time claim token is stored in Django's cache framework. Returns `{appears, landed}` booleans for the client to animate.
2. **Frontend animation**: JS uses the roll response to build the visual (e.g., knife tile placement in the reel spinner). On a successful landing, calls the claim API.
3. **Claim API**: `POST /api/v1/easter-eggs/claim/` verifies and consumes the one-time cache token, then awards the milestone via `award_manual_milestone()`. Rejects claims without a valid token.
4. **Milestone service**: `award_manual_milestone()` handles all side effects (UserMilestone, UserTitle, Discord role, notification, dashboard cache).

Adding a new easter egg requires: (1) a new milestone definition in `populate_milestones.py`, (2) entries in both `EASTER_EGG_MILESTONES` and `EASTER_EGG_ROLL_CHANCES` dicts, and (3) a frontend trigger that calls roll then claim.

## File Map

| File | Purpose |
|------|---------|
| `api/easter_egg_views.py` | Roll + Claim API endpoints, server-side probability config, cache token logic |
| `api/urls.py` | URL registration for `/api/v1/easter-eggs/roll/` and `/claim/` |
| `trophies/services/milestone_service.py` | `award_manual_milestone()` and `award_milestone_directly()` |
| `trophies/management/commands/populate_milestones.py` | Milestone definitions (manual section) |
| `static/js/reel-spinner.js` | Knife easter egg frontend trigger + animation |

## Current Easter Eggs

### Knife Landing (Reel Spinner)

| Property | Value |
|----------|-------|
| Easter egg ID | `knife_landed` |
| Milestone name | Unboxed! |
| Title awarded | Case Hardened |
| Land probability | 0.1% (1 in 1,000) per spin, server-side |
| Appear probability | 1% (1 in 100) per spin when not landing |
| Location | Reel spinner (A-Z Challenge, Genre Challenge) |

The reel spinner calls the roll endpoint before each spin. The server determines if the knife appears in the reel and/or lands as the winner. On a knife landing, the user sees a gold confetti celebration, dramatic fanfare, and the "KNIFE!" result card. The frontend then calls the claim endpoint (which verifies the cached token) to award the "Unboxed!" milestone and "Case Hardened" title.

On repeat knife landings (milestone already earned), the celebration still plays but no toast notification appears.

## API Endpoints

| Method | Path | Auth | Rate Limit | Purpose |
|--------|------|------|------------|---------|
| POST | `/api/v1/easter-eggs/roll/` | Login | 20/min | Server-side probability roll |
| POST | `/api/v1/easter-eggs/claim/` | Login | 10/min | Claim milestone (requires valid roll token) |

### Roll Endpoint

**Request body**: `{"easter_egg_id": "knife_landed"}`

**Responses**:
- `200 {"appears": true, "landed": true}`: knife lands (claim token cached)
- `200 {"appears": true, "landed": false}`: knife visible but doesn't land (near-miss)
- `200 {"appears": false, "landed": false}`: normal spin, no knife
- `400`: unknown easter_egg_id
- `403`: no linked PSN profile
- `429`: rate limited

### Claim Endpoint

**Request body**: `{"easter_egg_id": "knife_landed"}`

**Responses**:
- `200 {"awarded": true, "milestone_name": "...", "title_name": "..."}`: newly awarded
- `200 {"awarded": false, "already_earned": true}`: idempotent repeat
- `400`: unknown easter_egg_id
- `403`: no linked PSN profile or no valid roll token
- `429`: rate limited

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
3. Add mappings in `api/easter_egg_views.py`:
   ```python
   EASTER_EGG_MILESTONES = {
       'knife_landed': 'Unboxed!',
       'my_new_egg': 'My Easter Egg',  # new
   }
   EASTER_EGG_ROLL_CHANCES = {
       'knife_landed': {'land_chance': 0.001, 'appear_chance': 0.01},
       'my_new_egg': {'land_chance': 0.01, 'appear_chance': 0.05},  # new
   }
   ```
4. Add the frontend trigger: call roll first, then claim on success:
   ```javascript
   var roll = await PlatPursuit.API.post('/api/v1/easter-eggs/roll/', {
       easter_egg_id: 'my_new_egg'
   });
   if (roll.landed) {
       PlatPursuit.API.post('/api/v1/easter-eggs/claim/', {
           easter_egg_id: 'my_new_egg'
       }).then(function(data) { ... });
   }
   ```

## Gotchas and Pitfalls

- **Server-side probability enforcement.** Probabilities live in `EASTER_EGG_ROLL_CHANCES` on the server. The client never rolls its own odds. Calling `/claim/` directly without a valid roll token returns 403.
- **One-time claim tokens.** Each successful roll caches a token (`easter_roll:{egg_id}:{user_id}`) with a 5-minute TTL. The claim endpoint verifies and deletes it atomically. A token can only be used once.
- **Graceful degradation.** If the roll API call fails (network error, rate limit), the reel spinner proceeds with a normal spin (no knife). No error is shown to the user. Claim failures are logged to `console.warn`.
- **Redis required.** Roll tokens are stored in Django's cache (Redis). If Redis is not running locally, `IGNORE_EXCEPTIONS: True` silently drops all cache ops, so tokens are never stored and claims always return 403. Start Redis before testing easter eggs in dev.
- **Rate limiting**: Roll at 20/min, Claim at 10/min per user. A normal spin takes ~5 seconds, so these limits are generous for legitimate use while preventing scripted abuse.
- **Idempotency**: `award_manual_milestone()` uses `get_or_create()` internally. Duplicate claims are harmless (no errors, no double-awards, no duplicate notifications).
- **Manual milestone visibility**: Manual milestones only appear on the Special tab of the milestones page when earned. Unearned manual milestones are hidden. This is handled in `badge_views.py::_build_category_data()`.
- **Manual handler quirk**: The `handle_manual()` handler always returns `achieved=False` for unevaluated milestones. This is by design: manual milestones are awarded through `award_milestone_directly()`, not through the handler pipeline.
