# Easter Eggs

Hidden discoveries throughout the site that reward observant and lucky users with a rare find and bragging rights.

> **Changed 2026-08:** easter eggs no longer grant a title. The award rode on the legacy milestone
> engine's `manual` criteria type, which was retired (Lane 2 Step 3) with no replacement mechanism.
> The **find itself is unchanged** — the server-side roll and the client celebration both remain —
> and hunters who already earned "Unboxed!" keep the title as a historical award. The claim endpoint
> and its one-time cache token are gone.

## Architecture Overview

Easter eggs use a two-layer architecture with server-side probability enforcement:

1. **Roll API**: `POST /api/v1/easter-eggs/roll/` performs the probability roll server-side, keeping the odds off the client. Returns `{appears, landed}` booleans for the client to animate.
2. **Frontend animation**: JS uses the roll response to build the visual (e.g. knife tile placement in the reel spinner) and celebrates a landing.

Adding a new easter egg requires: (1) an entry in `EASTER_EGG_ROLL_CHANCES`, and (2) a frontend trigger that calls roll and animates the result.

## File Map

| File | Purpose |
|------|---------|
| `api/easter_egg_views.py` | Roll API endpoint + server-side probability config |
| `api/urls.py` | URL registration for `/api/v1/easter-eggs/roll/` |
| `static/js/reel-spinner.js` | Knife easter egg frontend trigger + animation |

## Current Easter Eggs

### Knife Landing (Reel Spinner)

| Property | Value |
|----------|-------|
| Easter egg ID | `knife_landed` |
| Land probability | 0.1% (1 in 1,000) per spin, server-side |
| Appear probability | 1% (1 in 100) per spin when not landing |
| Location | Reel spinner (Badge Art Reveal event page) |

The reel spinner calls the roll endpoint before each spin. The server determines if the knife appears in the reel and/or lands as the winner. On a knife landing, the user sees a gold confetti celebration, dramatic fanfare, and the "KNIFE!" result card.

## API Endpoints

| Method | Path | Auth | Rate Limit | Purpose |
|--------|------|------|------------|---------|
| POST | `/api/v1/easter-eggs/roll/` | Login | 20/min | Server-side probability roll |

### Roll Endpoint

**Request body**: `{"easter_egg_id": "knife_landed"}`

**Responses**:
- `200 {"appears": true, "landed": true}`: knife lands
- `200 {"appears": true, "landed": false}`: knife visible but doesn't land (near-miss)
- `200 {"appears": false, "landed": false}`: normal spin
- `400`: unknown `easter_egg_id`
- `403`: no linked PSN profile

## Related Documentation

- [Badge Art Reveal](badge-art-reveal.md): the event page hosting the reel spinner
- [Milestones Revamp](../design/milestones-revamp.md): why the `manual` award mechanism was retired

## Adding a New Easter Egg

1. Add the odds to `EASTER_EGG_ROLL_CHANCES` in `api/easter_egg_views.py`:
   ```python
   'my_new_egg': {'land_chance': 0.001, 'appear_chance': 0.01},
   ```
2. Add the frontend trigger: call roll, then animate on `landed`:
   ```js
   PlatPursuit.API.post('/api/v1/easter-eggs/roll/', {
       easter_egg_id: 'my_new_egg'
   }).then(function(data) { if (data.landed) { /* celebrate */ } });
   ```

## Gotchas and Pitfalls

- **Server-side probability enforcement.** Probabilities live in `EASTER_EGG_ROLL_CHANCES` on the server; the client never rolls its own odds. This is the whole point of the roll endpoint — don't move odds back to JS.
- **The find is the reward.** There is no persistent award anymore, so nothing is lost if a user closes the page mid-celebration. If a future easter egg needs a durable reward, it needs a new mechanism (the legacy milestone engine is gone).
- **Graceful degradation.** If the roll API call fails (network error, rate limit), the reel spinner proceeds with a normal spin (no knife) and shows no error.
