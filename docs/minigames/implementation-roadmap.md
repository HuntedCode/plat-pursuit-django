# Stellar Circuit: Implementation Roadmap

**Status**: Planning
**Game**: Stellar Circuit (Driver Job Mini-Game)
**Last Updated**: 2026-02-17

---

## Context

Stellar Circuit is a fully playable top-down space racer built with Phaser 3 (~7,000 lines JS), but it exists as a standalone frontend demo with zero backend infrastructure. This roadmap takes it from "fun tech demo" to a fully integrated PlatPursuit feature with server-backed competition, daily challenges, leaderboards, and eventually gamification rewards.

The game is served at `/arcade/stellar-circuit/` via a simple TemplateView. There is no `minigames` Django app, no database models, no API endpoints, and no Arcade Hub page. The gamification system (Jobs, Stats, XP, PlatCoins) that the Arcade is designed to integrate with also does not exist yet.

**Key design decision**: Build the Arcade backend to function independently of gamification. Design reward hooks now, wire them when gamification ships.

---

## Phase 1: Prototype (Gameplay Polish)

**Goal**: Make the standalone game feel complete on any device. All frontend work, no server needed.

**User experience at this phase**: A player on desktop or mobile can pick a difficulty, CC tier, and mode, hear procedural audio throughout the race, use touch controls on mobile, and have a polished single-player racing experience.

---

### 1.1 Audio System

Create `static/js/games/core/sound-manager.js` registered as `PlatPursuit.Games.Audio.SoundManager`.

**Architecture:**
- Lazy AudioContext initialization (Web Audio API requires user gesture)
- AudioContext created on LAUNCH button click in MenuScene
- Master volume control with mute toggle
- Each sound creates short-lived oscillator nodes (no audio files)

**Sound inventory (GDD Section 9):**

| Sound | Method | Trigger | Implementation |
|-------|--------|---------|----------------|
| Countdown beep (low) | `playCountdownBeep()` | "3", "2", "1" | Square wave 220Hz, 150ms, exponential decay |
| Countdown beep (high) | `playCountdownGo()` | "GO!" | Square wave 440Hz, 200ms |
| Checkpoint ding | `playCheckpointDing()` | Crossing checkpoint | Sine sweep 660-880Hz, 100ms |
| Finish fanfare | `playFinishFanfare()` | Race complete | C5-E5-G5 arpeggio, staggered 100ms, 500ms total |
| Engine hum | `startEngine()` / `updateEngine(speedRatio)` / `stopEngine()` | While thrusting | Persistent sawtooth 80-160Hz, gain scales with speed |
| Off-track rumble | `startOffTrack()` / `stopOffTrack()` | While off-track | 40Hz sine + white noise, low volume |

**Integration points:**
- `menu-scene.js`: `soundManager.init()` on LAUNCH click
- `race-scene.js`: countdown sounds, checkpoint/finish triggers
- `ship.js`: engine hum start/update/stop tied to thrust, off-track sound on state change

**Files to create:**
- `static/js/games/core/sound-manager.js` (~200 lines)

**Files to modify:**
- `static/js/games/driver/scenes/race-scene.js`
- `static/js/games/driver/scenes/menu-scene.js`
- `static/js/games/driver/ship.js`
- `static/js/games/driver/stellar-circuit.js` (pass soundManager to scenes)
- `templates/minigames/stellar-circuit.html` (add script tag)

---

### 1.2 Mobile Touch Controls

Extend `static/js/games/core/game-input.js` with touch support. The InputManager already returns generic `{ up, down, left, right }` state. Touch controls produce the same output.

**Virtual joystick (GDD Section 11.2):**
- Left half of screen: touch-and-drag creates virtual joystick
  - Any touch on left half = thrust active (`up: true`)
  - Horizontal drag offset maps to rotation: left of origin = `left: true`, right = `right: true`
  - Dead zone (20px) before rotation activates
- Right half (bottom-right quadrant): brake zone
  - Touch/hold = `down: true` (retro-thrust)
- Visual feedback: faint circle (origin) + dot (current position) on UI layer (`scrollFactor(0)`)

**Implementation notes:**
- Use Phaser's `input.on('pointerdown/move/up')` (handles both mouse and touch)
- Track active pointer IDs for multi-touch (simultaneous joystick + brake)
- Phaser's `Scale.FIT` at 1280x720 handles canvas scaling: touch coordinates map to design space automatically. No conflict with ZoomScaler (not used on game pages).
- Mobile pause button: 44px touch target, top-left corner (replaces ESC key)

**Files to modify:**
- `static/js/games/core/game-input.js` (add TouchController, integrate with InputManager.getState())

---

### 1.3 Difficulty Tiers

Wire up the `difficulty` parameter in `track-generator.js`. The `generate(seed, difficulty)` function already accepts the parameter but ignores it.

**Difficulty presets (GDD Section 3.8):**

| Parameter | Easy | Medium (default) | Hard |
|-----------|------|-------------------|------|
| Control points | 8-9 | 10-11 | 12-13 |
| Radius variance | 0.85-1.15 | 0.7-1.3 | 0.6-1.4 |
| Angular perturbation | +/-0.15 rad | +/-0.25 rad | +/-0.35 rad |
| Track width min | 180 px | 140 px | 100 px |
| Track width max | 360 px | 320 px | 260 px |

**Implementation:**
- `DIFFICULTY_PRESETS` object mapping `'easy'`/`'medium'`/`'hard'` to parameter overrides
- `generateControlPoints(rng)` becomes `generateControlPoints(rng, params)`
- `calculateWidths(curvatures)` uses params for width min/max instead of module constants
- Default `'medium'` when omitted (backward compatible)

**Menu UI:**
- Add "DIFFICULTY" selector in MenuScene (same button pattern as CC tier)
- Track preview updates on difficulty change (already debounced)
- Difficulty passed to RaceScene, then to `TrackGen.generate(seed, difficulty)`

**Files to modify:**
- `static/js/games/driver/track-generator.js` (difficulty presets, parameterize generation)
- `static/js/games/driver/scenes/menu-scene.js` (difficulty selector UI)
- `static/js/games/driver/scenes/race-scene.js` (pass difficulty to generator)
- `static/js/games/driver/scenes/results-scene.js` (show difficulty in results)

---

### 1.4 Visual Polish

All items below are gaps between the GDD and the current implementation. Organized by the files they touch.

#### Race Scene Effects (`race-scene.js`)

**Finish line celebration (GDD Section 4.7, currently missing entirely):**
- Screen flash: white overlay at 10% opacity, fades over 500ms
- Gold particle burst at ship position: 40 particles, all directions, `#d4a017`, 600ms lifespan (use existing particle emitter pattern from `ship.js`)
- HUD timer flash: total time text pulses (alpha oscillation or scale bounce) to highlight the final score
- Ship continues on momentum with input locked for ~2 seconds before results transition

**Checkpoint gate animation (GDD Section 4.5, currently just "CP X" text):**
- On crossing: two short lines expand outward from the checkpoint gate endpoints
- Color: `#40e850`, duration 200ms
- Use tweened Graphics objects (draw line, tween scaleX/alpha, then destroy)
- Particle burst: small burst of 8-12 green particles from each gate endpoint, 150ms lifespan (adds satisfying "punch" to each checkpoint)
- Keep the "CP X" text as well (all effects together)

**Countdown fade-in (GDD Section 4.6, partially implemented):**
- Currently: numbers pop to full alpha instantly and scale down
- Fix: numbers should fade IN (alpha 0 to 1) while scaling down (150% to 100%) simultaneously
- Small change to the existing countdown tween config

**Screen vignette (GDD Section 4.11, not implemented):**
- Use Phaser's built-in `VignettePostFX` pipeline (one line, proper radial gradient, GPU-accelerated)
- `this.cameras.main.postFX.addVignette(0.5, 0.5, 0.3)` (center x, center y, radius)
- Much better than manual Graphics rectangle approximation

**Lap completion celebration (refinement):**
- Current: "Lap X: time" text fades out
- Add: brief scale animation on the text (zoom in slightly, then fade), brief screen flash (5% opacity, 150ms, same as countdown beat)

**Pause behavior improvement:**
- ESC during race: show pause overlay (dark 50% overlay, "PAUSED" text, RESUME + QUIT buttons)
- Currently ESC goes straight to menu. Should pause first, with quit as an option.
- Use existing `createButton()` from `driver-ui.js`

#### Ghost Rendering (`race-scene.js` ghost section)

**Ghost ship pulsing opacity (GDD Section 4.8, currently static alpha):**
- GDD specifies 35-45% opacity oscillation via sine wave
- Add per-frame alpha update: `alpha = 0.25 + 0.10 * Math.sin(time * 0.003)` (pulsing at ~2s period)
- Apply to the ghost ship Graphics object during playback rendering

#### HUD and Minimap (`hud.js`)

**Checkpoint gate pulse on minimap (GDD Section 4.9, currently static dots):**
- GDD: uncrossed checkpoint dots oscillate opacity 0.3 to 0.7 on a 2-second sine wave
- GDD: faint connecting line between gate endpoint dots (1px, 20% opacity)
- Add time-based alpha modulation to the minimap checkpoint dot rendering
- After crossing: dots shrink to 2px, opacity drops to 0.15

#### Track Rendering (`track-generator.js`)

**Start/finish line dashed pattern (GDD Section 3.7, currently solid line):**
- Add a dashed center line overlay on the start/finish gate
- Gold `#d4a017` dashes, 4px dash / 4px gap pattern
- Makes the start/finish instantly recognizable vs regular checkpoints

#### Menu Scene (`menu-scene.js`)

**Title neon glow effect (GDD Section 6.2, currently plain colored text):**
- Add a second, slightly larger text object behind the title with lower alpha and a blurred stroke
- Creates a "neon bloom" effect: dim glow behind bright core text
- Or: use Phaser's `setShadow()` with a colored shadow matching the text color

#### Results Scene (`results-scene.js`)

**Ghost path visualization (GDD Section 6.5, not implemented):**
- Render the player's ghost recording as a faint polyline on top of a zoomed-out track view
- Sample every 5th-10th ghost frame position to create a smooth path
- Render at low opacity (20-30%) in the ship's trail color
- Shows the player their racing line, which is both informative and visually appealing

**NEW RECORD celebration polish:**
- Currently: scale tween on "NEW RECORD" text
- Add: brief starburst particle effect (or expanding ring graphic) behind the text
- Color: gold `#d4a017` to match the achievement feel

**Files to modify:**
- `static/js/games/driver/scenes/race-scene.js` (finish effects, checkpoint animation, countdown fix, vignette, lap flash, pause, ghost pulse)
- `static/js/games/driver/hud.js` (minimap checkpoint pulse)
- `static/js/games/driver/track-generator.js` (start/finish dashed line)
- `static/js/games/driver/scenes/menu-scene.js` (title glow)
- `static/js/games/driver/scenes/results-scene.js` (ghost path overlay, NEW RECORD polish)

---

### 1.5 Atmosphere and Post-Processing

The race scene currently has a flat `#0a0a14` background with zero depth or atmosphere. The 3-layer parallax starfield and nebula blobs from the GDD (Section 4.4) are not implemented. Phaser's built-in PostFX pipelines (available since 3.60, WebGL required) are not used anywhere despite WebGL being enabled.

These additions transform the game from "neon lines on black" to "atmospheric neon space racing with depth and glow."

#### 3-Layer Parallax Starfield (GDD Section 4.4, not implemented)

The single biggest atmospheric improvement. Currently only the menu has a basic single-layer starfield. The race scene has nothing.

**Implementation**: Create three layers of stars that scroll at different fractions of the camera's movement, creating parallax depth.

| Layer | Count | Size | Opacity | Scroll Speed | Color |
|-------|-------|------|---------|--------------|-------|
| Far | 200 | 0.5-1px | 0.2-0.4 | 0.1x camera | `#3a3a5c` |
| Mid | 100 | 1-2px | 0.3-0.6 | 0.3x camera | `#6b6b8d` |
| Near | 50 | 2-3px | 0.5-0.8 | 0.6x camera | `#e8e8ff` |

**Approach**: Pre-render each layer to a tileable RenderTexture. Set each as a tileSprite with `scrollFactor` matching its parallax speed. Stars are generated deterministically from the track seed (same seed = same starfield).

**Performance**: Three static tileSprites with different scroll factors. Zero per-frame draw cost after initial render. Phaser handles the scrolling internally.

**~100 lines of code.** Placed in `race-scene.js` at the beginning of `create()`, behind everything else.

#### Camera Bloom Post-Processing

One line of code, massive visual impact. Makes every bright element (neon track edges, ship glow, particles, checkpoint gates) bloom authentically.

```javascript
// In RaceScene.create(), after camera setup:
this.cameras.main.postFX.addBloom(0xffffff, 1, 1, 1, 1.2);
```

**Parameters to tune**: strength and blur radius. Start subtle: a soft halo around bright edges, not an overwhelming glow. The neon aesthetic benefits from bloom more than almost any other visual style.

**Fallback**: If WebGL isn't available (Canvas fallback), PostFX silently does nothing. No compatibility concerns.

#### Nebula Accent Blobs (GDD Section 4.4, not implemented)

3-5 large, semi-transparent colored blobs placed behind the stars and track. They break up the void, add color variety, and create visual landmarks ("I'm near the purple nebula, sharp turn ahead").

**Implementation**:
- Generate 3-5 positions deterministically from the track seed (use the same PRNG)
- Each blob: radial gradient circle, 200-600px radius, 3-5% opacity
- Colors from the gamification palette: cyan `#2ce8f5`, magenta `#b55088`, orange `#f77622`, void purple `#6b2fa0`
- Render to the starfield RenderTexture or as separate GameObjects behind the track

**~50 lines of code.** Subtle but effective for atmosphere.

#### Additional Particle Opportunities

The game already uses particles well (engine trail, retro-thrust, off-track sparks). Here are natural extensions of the same system:

**Speed lines (near-max speed):**
- When ship exceeds 85% max speed, emit faint elongated particles from the edges of the screen
- Streaking past the camera at high speed, colored `#e8e8ff` at 15% opacity
- Reinforces the sense of velocity. Complementary to the speed bar.
- Implementation: a dedicated emitter on the UI layer, emitting from screen edges, moving across the screen. ~30 lines.

**Checkpoint approach particles:**
- As the ship nears the next checkpoint (within ~200px), emit a faint trail of green particles (`#40e850` at 20%) from the checkpoint gate, pulling toward the ship like a magnetic effect
- Subtle navigational aid that feels organic, not like a UI arrow
- ~20 lines added to the checkpoint proximity check in `race-scene.js`

**Lap completion particle ring:**
- On lap complete: expanding ring of particles from the ship's position (similar to finish burst but smaller, white instead of gold)
- 20 particles, ring pattern, 400ms lifespan
- Makes each lap feel like a milestone, not just a counter increment

**Start line particle shimmer:**
- Before countdown starts, the start/finish line emits a gentle shimmer of gold particles (`#d4a017` at 30%)
- Draws the eye to the start position. Stops when countdown begins.
- ~15 lines in the countdown setup

**Files to modify/create:**
- `static/js/games/driver/scenes/race-scene.js` (starfield, bloom, nebula, speed lines, checkpoint approach particles, lap ring, start shimmer)

---

### 1.6 Later-Phase Visual Polish

Some visual enhancements make more sense after competitive features exist. These are noted here but scoped for later phases.

**Phase 3 (Beta) visual additions:**
- **Leaderboard placement badge particles**: When the results scene shows your daily ranking (Gold/Silver/Bronze), emit tier-colored particles around the placement badge
- **Personal best confetti**: When beating a personal best, the NEW RECORD burst uses more particles and a longer duration than the current plan
- **Community ghost visual distinction**: Different ghost slot colors (white/pink/blue) should have subtle trail particles (same tint, 20% opacity, very sparse) so you can distinguish ghosts at a glance

**Phase 4 (Production) visual additions:**
- **Ship skin-specific trail effects**: Each purchasable ship skin can define custom `trailColor` and `brakeColor`, but could also define unique trail patterns (wider spread, different particle sizes, dual-color trails)
- **Track environment variety**: Different nebula color themes per difficulty (easy = cool blues, medium = mixed, hard = warm reds/oranges) to give visual feedback about difficulty
- **Spectating camera effects**: When watching a replay or ghost, subtle depth-of-field blur on background elements

### Phase 1 Acceptance Criteria

**Core features:**
- [ ] Player on desktop (keyboard) can select difficulty + CC tier + mode, hear all 6 sound types, and complete a race
- [ ] Player on mobile (touch) can play with virtual joystick + brake zone, including pause button
- [ ] Easy/Medium/Hard difficulty produces noticeably different track complexity
- [ ] ESC pauses instead of immediately quitting

**Visual polish:**
- [ ] Finish line triggers gold particle burst, screen flash, and HUD timer pulse
- [ ] Checkpoint crossings show expanding gate lines with particle bursts
- [ ] Countdown numbers fade in while scaling down
- [ ] Ghost ships pulse in opacity (not static)
- [ ] Minimap checkpoint dots pulse gently
- [ ] Start/finish line has dashed center pattern
- [ ] Results scene shows ghost path overlay on track
- [ ] NEW RECORD has starburst particle celebration

**Atmosphere:**
- [ ] 3-layer parallax starfield visible behind the track during races (depth through motion)
- [ ] Camera bloom post-processing makes neon elements glow authentically
- [ ] Nebula accent blobs add color and landmarks to the background
- [ ] Screen vignette (PostFX) visible during races
- [ ] Speed line particles appear near max speed
- [ ] Lap completion triggers expanding particle ring

---

## Phase 2: Alpha (Server-Backed Single Player)

**Goal**: Backend infrastructure. Sessions tracked, ghosts server-side, daily challenges, premium gate.

**User experience at this phase**: An authenticated premium user logs in, sees today's daily challenge with its seed/difficulty/CC tier, races, and their ghost + session are saved to the server. Non-premium users see the game but can't play. Unauthenticated users play in offline mode (localStorage only).

---

### 2.1 Django `minigames` App

**New app structure:**
```
minigames/
  __init__.py
  models.py
  admin.py
  urls.py
  views.py                          (page views)
  services/
    __init__.py
    session_service.py              (session lifecycle)
    challenge_service.py            (daily challenge generation)
    ghost_service.py                (ghost CRUD, compression)
    validation_service.py           (basic anti-cheat)
  management/
    commands/
      generate_daily_challenges.py  (cron job)
  migrations/
    __init__.py
```

**Models:**

**MiniGameType**: Registry of available mini-games (one per Job).
- `job_slug`, `name`, `description`, `score_direction` (asc = lower is better), `is_active`, `config` (JSONField)

**DailyChallenge**: One per game per day. Stores the seed for deterministic generation.
- `game_type` FK, `date`, `seed`, `difficulty`, `cc_tier`, `mode`, `config` (JSONField), `is_archived`
- Unique on (game_type, date)

**GameSession**: A single play attempt.
- `profile` FK, `game_type` FK, `daily_challenge` FK (nullable), `session_token` (UUID, unique)
- `status` (active/completed/abandoned/invalidated), `seed`, `difficulty`, `cc_tier`, `mode`
- `started_at`, `completed_at`, `score` (race time in ms), `best_lap_ms`, `metadata` (JSONField: lap_times, checkpoint_count)
- `is_validated`, `validation_notes`

**GhostData**: Server-side ghost recording.
- `session` OneToOne, `profile` FK, `seed`, `mode`, `cc_tier`, `difficulty`
- `total_time_ms`, `best_lap_ms`, `frames` (BinaryField, zlib-compressed flat float array), `frame_count`
- `is_personal_best` (bool, indexed)

**PlayerGameStats**: Aggregate stats per player per game.
- `profile` FK, `game_type` FK, `total_plays`, `current_streak`, `longest_streak`, `last_played_date`
- Unique on (profile, game_type)

---

### 2.2 API Endpoints

Create `api/minigame_views.py` following existing API patterns.

| Method | Endpoint | Purpose | Auth |
|--------|----------|---------|------|
| GET | `/api/v1/arcade/daily/` | Today's daily challenge | Premium |
| POST | `/api/v1/arcade/sessions/start/` | Start session, get token | Premium |
| POST | `/api/v1/arcade/sessions/{token}/complete/` | Submit results | Premium |
| POST | `/api/v1/arcade/ghosts/upload/` | Upload ghost data | Premium |
| GET | `/api/v1/arcade/ghosts/{seed}/{mode}/{cc_tier}/personal/` | User's personal best ghost | Premium |

**Session lifecycle:**
1. Client calls `sessions/start/` with `{ seed, mode, cc_tier, difficulty, is_daily }`
2. Server creates GameSession, returns `{ session_token }`
3. Client plays the race
4. Client calls `sessions/{token}/complete/` with `{ score_ms, best_lap_ms, lap_times, checkpoint_count }`
5. Server validates (time bounds, checkpoint count), marks complete
6. Client calls `ghosts/upload/` with `{ session_token, frames: base64 }`
7. Server compresses and stores ghost, marks personal best if applicable

**Files to create:**
- `api/minigame_views.py` (~300 lines)

**Files to modify:**
- `api/urls.py` (add minigame endpoints)

---

### 2.3 Daily Challenge System

**Management command**: `generate_daily_challenges.py`
- Generates tomorrow's DailyChallenge for each active MiniGameType
- Seed formula: `hash(game_slug + date_str)` (matches client's `todaysSeed()` pattern)
- Difficulty cycle: Mon/Tue = easy, Wed/Thu = medium, Fri/Sat = hard, Sun = random
- CC tier rotation: weekly cycle through 50cc, 100cc, 200cc

**Cron**: Run daily at midnight UTC.

---

### 2.4 Server-Side Ghost Storage

**Ghost service** (`ghost_service.py`):
- `save_ghost(session, frames_array)`: compress with zlib, create GhostData, mark personal best
- `get_personal_best(profile, seed, mode, cc_tier)`: returns decompressed frames or None
- `is_personal_best(profile, seed, mode, cc_tier, total_time_ms)`: check before saving

**localStorage as offline fallback**: Existing `GhostStorage` in `ghost.js` continues unchanged. Fresh start on server side (no migration). When authenticated, client tries server first, falls back to localStorage.

---

### 2.5 Premium Gate

**View**: Change `/arcade/stellar-circuit/` from raw TemplateView to `StellarCircuitView`:
- Injects `is_premium`, `is_authenticated`, `profile_id` into template context
- Template passes these as `window.STELLAR_CIRCUIT_CONFIG` JSON

**Frontend gate**: MenuScene checks config. Non-premium users see menu + track preview but LAUNCH button shows "Premium Required" with link to subscription page.

**Files to modify:**
- `plat_pursuit/urls.py` (change TemplateView to StellarCircuitView)
- `templates/minigames/stellar-circuit.html` (inject config JSON)
- `static/js/games/driver/scenes/menu-scene.js` (premium gate on LAUNCH)

---

### 2.6 Basic Anti-Cheat (Session Validation)

**Validation service** (`validation_service.py`):
- **Session token**: UUID from server, single-use, required for completion
- **Time bounds**: `completed_at - started_at` must be >= `score + 5s margin`. Flag impossibly fast times.
- **Checkpoint count**: Client reports count. Server knows expected count from seed PRNG. Mismatch = invalidated.
- **Rate limiting**: Max 20 session starts per hour per profile

---

### 2.7 Frontend API Integration

Create `static/js/games/core/game-session.js` under `PlatPursuit.Games.Session`:
- `startSession(config)`: calls `PlatPursuit.API.post()`
- `completeSession(token, results)`: submits race results
- `uploadGhost(token, frames)`: base64-encodes flat array, uploads
- `loadGhost(seed, mode, ccTier)`: server first, localStorage fallback
- `getDailyChallenge()`: fetches today's daily

**Scene integration:**
- MenuScene: "DAILY CHALLENGE" button (server-provided seed), "CUSTOM SEED" option
- RaceScene.create: `startSession()`, store token
- RaceScene finish: `completeSession()` then `uploadGhost()`
- ResultsScene: "Saved to Server" confirmation or "Offline Mode" indicator

**Files to create:**
- `static/js/games/core/game-session.js` (~200 lines)

**Files to modify:**
- `static/js/games/driver/scenes/menu-scene.js`
- `static/js/games/driver/scenes/race-scene.js`
- `static/js/games/driver/scenes/results-scene.js`
- `static/js/games/driver/ghost.js` (server loading)
- `templates/minigames/stellar-circuit.html` (add script tag)

---

### 2.8 Settings and Configuration

**Files to modify:**
- `plat_pursuit/settings.py` (add `'minigames'` to INSTALLED_APPS)
- `plat_pursuit/urls.py` (include minigame URL patterns)

---

### Phase 2 Acceptance Criteria

- [ ] Authenticated premium user sees daily challenge, races, ghost saved to server, session in DB
- [ ] Non-premium user sees game menu but cannot launch (with subscription CTA)
- [ ] Unauthenticated user plays in offline mode (localStorage ghosts, no session tracking)
- [ ] Daily challenge generation cron works and produces correct seeds
- [ ] Session validation catches impossibly fast times and wrong checkpoint counts
- [ ] `minigames` admin shows all models in Django admin

---

## Phase 3: Beta (Competitive Features)

**Goal**: Async multiplayer competition. Leaderboards, community ghosts, placeholder rewards.

**User experience at this phase**: Players compete on daily challenges, see their rank on daily and all-time leaderboards, race against other players' ghosts, and see placeholder reward amounts. A competitive loop exists.

---

### 3.1 Dual Leaderboard System

**New model: LeaderboardEntry**
- `profile` FK, `daily_challenge` FK, `board_type` (daily/alltime)
- `seed`, `best_score` (ms), `best_session` FK, `achieved_at`
- Unique on (profile, daily_challenge, board_type)

**Leaderboard service** (`leaderboard_service.py`):
- `update_leaderboard(session)`: called on completion, updates daily + all-time entries
- `get_daily_leaderboard(challenge, limit=50)`: ranked entries for today
- `get_alltime_leaderboard(seed, mode, cc_tier, limit=50)`: best-ever times
- `close_daily_leaderboard(challenge)`: freezes daily, assigns tier placements (Gold/Silver/Bronze/Top10/Top25%)

**API endpoints:**

| Method | Endpoint | Purpose |
|--------|----------|---------|
| GET | `/api/v1/arcade/leaderboard/daily/` | Today's daily leaderboard |
| GET | `/api/v1/arcade/leaderboard/{seed}/{mode}/{cc_tier}/` | All-time leaderboard |

**Management command**: `close_daily_leaderboards.py` (cron at midnight UTC).

**Frontend**: ResultsScene shows player's rank. MenuScene shows daily top 3 + player's position.

---

### 3.2 Community Ghosts

**New API endpoint:**

| Method | Endpoint | Purpose |
|--------|----------|---------|
| GET | `/api/v1/arcade/ghosts/{seed}/{mode}/{cc_tier}/top/` | Top 3 community ghosts |

**Menu ghost panel:**
- Fetch top 3 ghosts after loading daily challenge
- Display with checkboxes: rank, username, time (max 3 active)
- Ghost colors: white (personal best), pink (ghost 2), blue (ghost 3)

**Files to modify:**
- `static/js/games/driver/scenes/menu-scene.js` (ghost selection with server data)
- `static/js/games/driver/scenes/race-scene.js` (load multiple server ghosts)
- `static/js/games/driver/ghost.js` (support server-loaded frames)

---

### 3.3 Placeholder Reward System

Build reward calculation infrastructure that stores values without crediting them anywhere (no gamification system yet).

**Add to GameSession model:**
- `xp_awarded` (IntegerField, default=0)
- `coins_awarded` (IntegerField, default=0)

**Reward service** (`reward_service.py`):
- `calculate_rewards(session)` returns `{ xp, coins, bonuses: [] }`
- Base completion: `{ xp: 10, coins: 5 }`
- Personal best bonus: `{ xp: 25, coins: 15 }`
- Daily placement multiplier: Gold=3x, Silver=2x, Bronze=1.5x
- Streak bonus: days 2-7 = 1.1x to 1.5x
- Daily cap: max 10 rewarded sessions

**Frontend**: ResultsScene shows "10 XP + 5 Coins" with placeholder icons. When gamification ships, these become real credits.

---

### 3.4 Track Archive and Player Stats

**API endpoints:**

| Method | Endpoint | Purpose |
|--------|----------|---------|
| GET | `/api/v1/arcade/archive/` | Past daily challenges (paginated) |
| GET | `/api/v1/arcade/stats/` | Current user's aggregate stats |

**Menu additions:**
- "ARCHIVE" button: scrollable list of past daily challenges with dates, your best time, top 3 times
- "STATS" panel: total races, current streak, longest streak, best daily placement

---

### Phase 3 Acceptance Criteria

- [ ] Daily leaderboard shows ranked players, freezes at midnight, assigns tier placements
- [ ] All-time leaderboard shows best-ever times per seed/mode/tier
- [ ] Players can race against top 3 community ghosts (fetched from server)
- [ ] Placeholder rewards calculated and displayed on results screen
- [ ] Track archive browsable, past challenges replayable
- [ ] Player stats visible in menu

---

## Phase 4: Production (Full Integration and Polish)

**Goal**: Gamification integration, hardened anti-cheat, analytics, mobile polish, preparation for the next mini-game.

---

### 4.1 Gamification Integration

When the gamification system ships:
- Wire `reward_service.apply_rewards(session)` to gamification service (credit XP/PlatCoins)
- Award Driver Job XP for completed races
- Daily challenge completion feeds quest system (quest trigger signals)
- Streak tracking feeds into daily login/engagement metrics

**No code changes needed until gamification ships.** The Phase 3 reward system already calculates and stores values.

---

### 4.2 Arcade Hub Page (DEFERRED)

Build `/arcade/` landing page ONLY when 2+ games exist or gamification goes live. Not scoped in any phase. When built: game cards, daily challenge preview, player stats summary.

---

### 4.3 Enhanced Anti-Cheat

- **Server-side track regeneration**: Port track generation (or simplified version) to Python for checkpoint count verification
- **Statistical anomaly detection**: Flag times below theoretical minimum (arc length / max speed)
- **Replay validation**: Check ghost frame data against physics model for plausibility
- **Tightened rate limits**: Max 10 session starts per hour

---

### 4.4 Ship Customization UI

The config-driven color system already works (6 presets via keys 4-9). In Production:
- Expose color presets as a "SHIP" selector in the menu
- Persist selection via server (PlayerGameState or profile preference)
- Later: purchasable skins from PlatCoin store

---

### 4.5 Analytics and Monitoring

Track via SiteEvent model (exists in `core/models.py`):
- Daily active players (unique profiles with completed sessions)
- Session counts by mode/tier/difficulty
- Completion rates (completed vs abandoned)
- Average race times per seed
- Device breakdown (touch vs keyboard from session metadata)

---

### 4.6 Mobile Polish

Based on Beta feedback:
- Touch control dead zone tuning
- Haptic feedback via `navigator.vibrate()` on checkpoints and off-track
- Touch visual refinements (opacity, size, position)
- Performance: reduce particle counts if frame rate drops below 30fps

---

### 4.7 Performance Optimization

- Consider concatenating game JS files for production (currently 12 separate `<script defer>` tags)
- Lazy loading: only load Phaser + game scripts when navigating to `/arcade/stellar-circuit/`
- CDN considerations for `phaser.min.js` (~1MB vendored file)

---

### Phase 4 Acceptance Criteria

- [ ] Rewards credited to gamification system when it exists
- [ ] Anti-cheat catches statistical anomalies and impossible replays
- [ ] Ship customization exposed in menu UI
- [ ] Analytics dashboard shows player engagement metrics
- [ ] Mobile touch controls refined based on real-device feedback
- [ ] Infrastructure supports adding the next mini-game with minimal new code

---

## Cross-Cutting Concerns

### Gamification Integration Strategy

**Design now, wire later:**
- Phase 3 builds a complete reward calculation system that stores XP/coin amounts per session
- Values are calculated and stored but not credited to any system
- When gamification ships, add one integration point: `reward_service.apply_rewards(session)` calls gamification service
- Reward formulas, streak logic, and daily caps are battle-tested by then

**What changes when gamification ships:**
- `xp_awarded` / `coins_awarded` become meaningful (credited to accounts)
- Driver Job XP awarded alongside general XP
- Daily challenge completion may satisfy quest requirements
- Ship skins become purchasable with PlatCoins

### Testing Strategy

| Phase | Testing Approach |
|-------|-----------------|
| Prototype | Manual playtesting: desktop + mobile, all 18 combinations (3 difficulties x 3 CC tiers x 2 modes), audio on all browsers, touch on real devices |
| Alpha | Django unit tests for models/services/validation, API endpoint tests, integration test for full race flow |
| Beta | Leaderboard accuracy tests (concurrent submissions, ties), ghost integrity (upload/download/playback), load test (50 concurrent completions) |
| Production | Anti-cheat validation tests, mobile performance benchmarks, analytics pipeline verification |

### Rollout Strategy

- **Phase 1**: Deploy immediately (purely client-side, no server dependency)
- **Phase 2**: Feature flag `STELLAR_CIRCUIT_API_ENABLED`. Staged: deploy disabled, enable for staff, then all premium users
- **Phase 3**: Additive (no breaking changes). Progressive deployment.
- **Phase 4**: Gamification integration ships independently

---

## File Summary

### Phase 1: New Files
- `static/js/games/core/sound-manager.js`

### Phase 1: Modified Files
- `static/js/games/core/game-input.js` (touch controls)
- `static/js/games/driver/track-generator.js` (difficulty presets, start/finish dashed line, nebula blobs)
- `static/js/games/driver/scenes/menu-scene.js` (difficulty selector, audio init, title glow)
- `static/js/games/driver/scenes/race-scene.js` (audio, PostFX bloom + vignette, parallax starfield, pause, finish effects, checkpoint animation + particles, countdown fix, ghost pulse, lap flash + particle ring, speed line particles, start shimmer)
- `static/js/games/driver/scenes/results-scene.js` (show difficulty, ghost path overlay, NEW RECORD particle celebration)
- `static/js/games/driver/hud.js` (minimap checkpoint pulse)
- `static/js/games/driver/ship.js` (audio hooks)
- `static/js/games/driver/stellar-circuit.js` (pass soundManager)
- `templates/minigames/stellar-circuit.html` (script tag)

### Phase 2: New Files
- Entire `minigames/` Django app (models, services, management commands, admin)
- `api/minigame_views.py`
- `static/js/games/core/game-session.js`

### Phase 2: Modified Files
- `plat_pursuit/settings.py`, `plat_pursuit/urls.py`, `api/urls.py`
- `templates/minigames/stellar-circuit.html`
- Menu, race, results scenes + `ghost.js`

### Phase 3: New Files
- `minigames/services/leaderboard_service.py`
- `minigames/services/reward_service.py`
- `minigames/management/commands/close_daily_leaderboards.py`
- `static/js/games/core/game-leaderboard.js`

### Phase 3: Modified Files
- `minigames/models.py` (LeaderboardEntry, reward fields)
- `api/minigame_views.py`, `api/urls.py`
- Menu, results scenes + `ghost.js`

### Critical Files (most complex modifications)
- `static/js/games/core/game-input.js`: Touch controls, shared by all future games
- `static/js/games/driver/track-generator.js`: Difficulty tiers, start/finish rendering, nebula blobs, core generation
- `static/js/games/driver/scenes/race-scene.js`: Audio, atmosphere (starfield, bloom, vignette, nebula), visual effects (finish, checkpoint, speed lines, lap ring, ghost pulse), session API, pause. **Heaviest modification target across all phases.**
- `static/js/games/driver/scenes/menu-scene.js`: All new UI (difficulty, premium gate, daily challenge, ghosts, title glow)
- `static/js/games/driver/scenes/results-scene.js`: Ghost path overlay, NEW RECORD celebration, leaderboard display
- `static/js/games/driver/hud.js`: Minimap checkpoint pulse animation
- `minigames/models.py`: Foundation for all server-side features
