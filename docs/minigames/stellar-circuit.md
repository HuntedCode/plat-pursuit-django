# Stellar Circuit: Game Design Document

**Game**: Stellar Circuit
**Job**: Driver (Job #1)
**Archetype**: Competitive
**Status**: Prototype (frontend-only)
**Last Updated**: 2026-02-16

---

## 1. Executive Summary

Stellar Circuit is a top-down space racer where players pilot a spacecraft around procedurally generated circuit tracks. Unlike traditional top-down racers that use car-style physics (steering + acceleration = car turns), Stellar Circuit uses **thrust-based space physics**: the ship has a single rear thruster, and the player rotates the ship to aim where thrust is applied. Momentum carries the ship forward regardless of which direction it faces.

This creates a unique gameplay feel where "drifting" is the natural state of motion. Turning a corner means rotating to face against your momentum and thrusting to redirect. Skilled players can execute beautiful sweeping turns by feathering thrust, while beginners will find themselves sliding wide into the void.

**Core loop**: Select a track seed, race 3 laps (or 1 in time trial), compete against your own ghost and other players' replays.

**Visual style**: Neon/synthwave + space-faring. Programmatic vector/geometric graphics. Cyan/magenta/orange palette on deep space backgrounds. All rendering is code: no art assets.

**Tech**: Phaser 3 on HTML5 Canvas. Custom physics (no Phaser physics engine). 1280x720 design resolution with Phaser Scale.FIT.

---

## 2. Ship Physics & Handling

### 2.1 Thrust-Based Movement Model

The ship exists in a 2D space with minimal friction. It has:
- A **position** (x, y) in world coordinates
- A **velocity vector** (vx, vy) representing current momentum
- A **rotation** (angle in radians) representing which direction the ship faces
- A **thruster** that applies force in the ship's facing direction

The critical distinction from car physics: **rotation does not change the velocity vector**. When the player turns the ship, the ship's nose points in a new direction, but the ship continues moving in its original direction until thrust is applied. This means:

- Rotating alone = the ship spins in place while continuing its current trajectory
- Thrusting after rotating = force is applied in the new facing direction, gradually bending the trajectory
- Braking = retro-thrust applied opposite to the current velocity direction (not the facing direction)

**Frame update order:**
1. Apply rotation from input
2. Apply thrust force (if thrusting) in facing direction
3. Apply retro-thrust (if braking) opposite to velocity direction
4. Apply drag (on-track or off-track coefficient, using previous frame's boundary state)
5. Update position from velocity
6. Check track boundaries, update on/off-track status (using new position)
7. Cache speed, snap-to-zero

### 2.2 Core Parameters

| Parameter | Symbol | Value | Unit | Notes |
|-----------|--------|-------|------|-------|
| Max Thrust Force | `THRUST_FORCE` | 500 | px/s^2 | Raw acceleration per second when thrusting |
| Rotation Speed | `ROTATION_SPEED` | 4.0 | rad/s | Full rotation in ~1.6 seconds |
| On-Track Drag | `DRAG_ON_TRACK` | 0.015 | coefficient | Very low: maintains space feel |
| Off-Track Drag | `DRAG_OFF_TRACK` | 0.08 | coefficient | ~5x on-track: meaningful penalty |
| Max Speed (soft cap) | `MAX_SPEED` | 650 | px/s | Thrust effectiveness diminishes approaching this |
| Brake Force Ratio | `BRAKE_RATIO` | 0.6 | multiplier | Braking force = THRUST_FORCE * 0.6 |
| Min Speed Threshold | `MIN_SPEED` | 2 | px/s | Below this, velocity snaps to zero |

**Tuning notes**: These values are starting points. The key relationships to maintain:
- `THRUST_FORCE / MAX_SPEED` ratio determines how quickly the ship reaches top speed (~1.3 seconds from standstill, not counting drag)
- `DRAG_ON_TRACK` should be low enough that the ship feels "floaty" but not so low that you can never slow down
- `DRAG_OFF_TRACK` should be harsh enough that going off-track costs time but not so harsh that it feels like hitting a wall
- `ROTATION_SPEED` of 4.0 rad/s means a full 360 in ~1.6 seconds: fast enough to be responsive, slow enough that rotation matters as a resource

### 2.3 Rotation & Steering

Rotation is always available, even when not thrusting. This is critical: the player should be able to pre-aim their ship before firing the thruster.

```
if (input.left)  ship.rotation -= ROTATION_SPEED * dt;
if (input.right) ship.rotation += ROTATION_SPEED * dt;
```

Rotation wraps naturally (radians, -PI to PI or 0 to 2*PI).

**Design rationale**: Constant rotation speed (not speed-dependent) keeps controls predictable. In real spacecraft, rotation speed doesn't change with translational velocity. This also means players can execute precise maneuvers at any speed: the challenge is choosing when to rotate and when to thrust, not fighting a variable turn rate.

### 2.4 Drag Model

Drag is applied per-frame as a velocity multiplier:

```
dragCoefficient = isOnTrack ? DRAG_ON_TRACK : DRAG_OFF_TRACK;
ship.vx *= (1 - dragCoefficient);
ship.vy *= (1 - dragCoefficient);
```

This is exponential decay, not linear. At 60fps with `DRAG_ON_TRACK = 0.015`:
- After 1 second: speed is ~40% of original (0.985^60 = 0.405)
- After 2 seconds: speed is ~16% (0.985^120 = 0.164)
- Ship never fully stops but approaches zero asymptotically

With `DRAG_OFF_TRACK = 0.08`:
- After 1 second: speed is ~0.6% of original (0.92^60 = 0.006)
- Effectively halted within a second: harsh but not instant

**Why exponential, not linear**: Linear drag (subtract N px/s per frame) creates a hard stop where the ship jerks to zero. Exponential drag creates a smooth deceleration that feels natural. It also means faster ships experience more absolute drag (same percentage of a larger number), which helps balance high-speed gameplay.

**Snap-to-zero**: When speed drops below `MIN_SPEED` (2 px/s), snap velocity to zero. Prevents infinitesimal crawling.

### 2.5 Ship Hitbox & Track Collision

The ship uses a **center-point hitbox**: the ship's origin (center of mass) is the sole point tested against track boundaries. The ship's visual body can hang over the track edge without triggering off-track.

**Why center-point, not radius-based**: A radius-based hitbox (where `distance + radius > trackHalfWidth`) felt too punishing: a wingtip clipping the edge would trigger the full off-track penalty. Center-point collision is more forgiving, makes edge-scrubbing viable, and is ship-shape agnostic: different skin body types don't affect the off-track boundary. This keeps skin choice purely cosmetic.

**On/off-track detection**:
1. Find the nearest point on the track centerline to the ship's center position
2. Calculate the perpendicular distance from ship center to centerline
3. Get the track half-width at that point
4. If `distance > trackHalfWidth`: ship is off-track

**No hard walls**: Going off-track does NOT stop the ship or bounce it. The penalty is heavier drag. This feels more "spacey" (there are no walls in space) and is more forgiving than hard collision. Visual feedback (sparks, flicker) signals to the player they've left the track.

**Edge-scrubbing**: A skilled technique where players intentionally clip the track edge to take a tighter line. The brief off-track drag costs some speed but the shorter path may be worth it. Center-point collision makes this viable: the ship's wings can overhang the edge while the center stays on-track.

### 2.6 Speed Limiting (Soft Cap)

The max speed is a soft cap, not a hard wall. As the ship approaches `MAX_SPEED`, thrust becomes less effective:

```
speedRatio = currentSpeed / MAX_SPEED;
thrustMultiplier = Math.max(0, 1 - speedRatio * speedRatio);

// Applied thrust is reduced near max speed:
effectiveThrust = THRUST_FORCE * thrustMultiplier;
```

The squared term (`speedRatio^2`) means:
- At 50% max speed: 75% thrust effectiveness
- At 70% max speed: 51% thrust effectiveness
- At 90% max speed: 19% thrust effectiveness
- At 100% max speed: 0% thrust effectiveness

This creates a natural speed ceiling without a jarring hard stop. Players feel the ship becoming "heavier" as it approaches top speed.

---

## 3. Track Generation Algorithm

### 3.1 Deterministic Seeded PRNG

All track generation uses a **Mulberry32** PRNG seeded from the track seed string. This guarantees:
- Same seed = same track, every time, on every device
- Different seeds = different tracks
- No dependency on `Math.random()` (which is not seedable)

**Seed derivation**: The seed string (e.g., "2026-02-16-stellar-circuit") is hashed to a 32-bit integer using a simple string hash function:

```javascript
function hashString(str) {
    let hash = 0;
    for (let i = 0; i < str.length; i++) {
        const char = str.charCodeAt(i);
        hash = ((hash << 5) - hash) + char;
        hash = hash & hash; // Convert to 32-bit integer
    }
    return hash >>> 0;  // Unsigned right-shift: converts to uint32, avoids Math.abs edge cases
}
```

**Mulberry32 PRNG**:

```javascript
class SeededRandom {
    constructor(seed) {
        this.state = seed;
    }

    // Returns float in [0, 1)
    next() {
        this.state |= 0;
        this.state = (this.state + 0x6D2B79F5) | 0;
        let t = Math.imul(this.state ^ (this.state >>> 15), 1 | this.state);
        t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
        return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
    }

    // Returns float in [min, max)
    range(min, max) {
        return min + this.next() * (max - min);
    }

    // Returns integer in [min, max] (inclusive)
    intRange(min, max) {
        return Math.floor(this.range(min, max + 1));
    }
}
```

### 3.2 Control Point Generation

The track is defined by a set of control points arranged in a rough loop. The algorithm:

1. **Determine point count**: `numPoints = rng.intRange(8, 13)`. More points = more complex track.

2. **Base ellipse**: Points are distributed around an ellipse with semi-axes:
   - `radiusX = rng.range(1600, 2400)` pixels
   - `radiusY = rng.range(1200, 2000)` pixels

3. **Angular distribution**: Each point gets a base angle evenly spaced around the ellipse, with random perturbation:
   ```
   for i in 0..numPoints:
       baseAngle = (2 * PI * i) / numPoints
       perturbation = rng.range(-0.25, 0.25)  // radians
       angle = baseAngle + perturbation
   ```

4. **Radial perturbation**: Each point's distance from center varies:
   ```
   radiusMult = rng.range(0.7, 1.3)
   x = cos(angle) * radiusX * radiusMult
   y = sin(angle) * radiusY * radiusMult
   ```

5. **Minimum distance enforcement**: After generating all points, check each adjacent pair. If two points are closer than `MIN_POINT_DISTANCE` (200px), nudge the closer one outward. This prevents pinch points where the track would be too tight.

6. **Centroid normalization**: Shift all points so the centroid is at (0, 0).

### 3.3 Catmull-Rom Spline (Closed Loop)

The control points are connected by a Catmull-Rom spline to create smooth curves. For a closed loop, the first/last points wrap around.

**Catmull-Rom interpolation** between points P1 and P2, with tangent points P0 and P3:

```javascript
function catmullRom(p0, p1, p2, p3, t) {
    // Uniform Catmull-Rom spline (standard basis matrix formulation)
    const t2 = t * t;
    const t3 = t2 * t;

    // Standard Catmull-Rom basis with 0.5 tension (uniform parameterization):
    return {
        x: 0.5 * (
            (-t3 + 2*t2 - t) * p0.x +
            (3*t3 - 5*t2 + 2) * p1.x +
            (-3*t3 + 4*t2 + t) * p2.x +
            (t3 - t2) * p3.x
        ),
        y: 0.5 * (
            (-t3 + 2*t2 - t) * p0.y +
            (3*t3 - 5*t2 + 2) * p1.y +
            (-3*t3 + 4*t2 + t) * p2.y +
            (t3 - t2) * p3.y
        )
    };
}
```

**Closed loop handling**: When interpolating segment `i`, the four points are:
- P0 = `controlPoints[(i - 1 + N) % N]`
- P1 = `controlPoints[i]`
- P2 = `controlPoints[(i + 1) % N]`
- P3 = `controlPoints[(i + 2) % N]`

This wraps around seamlessly.

### 3.4 Spline Sampling & Curvature Calculation

**Sampling**: The spline is sampled at high resolution to create the actual track polyline:
```
SAMPLES_PER_SEGMENT = 40
totalSamples = numPoints * SAMPLES_PER_SEGMENT
```

For a 10-point track: 400 sample points. Typical range: 320-520. Each sample stores `{ x, y }`.

**Curvature calculation**: At each sample point, curvature is the rate of change of the tangent direction. Approximated using three consecutive points:

```javascript
function calculateCurvature(prev, curr, next) {
    // Vectors from curr to neighbors
    const dx1 = curr.x - prev.x;
    const dy1 = curr.y - prev.y;
    const dx2 = next.x - curr.x;
    const dy2 = next.y - curr.y;

    // Cross product magnitude / (distance product) = curvature
    const cross = dx1 * dy2 - dy1 * dx2;
    const dot = dx1 * dx2 + dy1 * dy2;
    const len1 = Math.sqrt(dx1*dx1 + dy1*dy1);
    const len2 = Math.sqrt(dx2*dx2 + dy2*dy2);

    if (len1 < 0.001 || len2 < 0.001) return 0;

    return Math.abs(cross) / (len1 * len2);
}
```

Curvature values are normalized to [0, 1] range by dividing by the maximum curvature found on the track.

### 3.5 Track Width Variation

Track width varies based on curvature: wider on straights, narrower on curves.

```javascript
const TRACK_WIDTH_MIN = 140;   // Tightest curves
const TRACK_WIDTH_MAX = 320;  // Long straights

for (let i = 0; i < totalSamples; i++) {
    const curvature = normalizedCurvatures[i];
    // Smoothstep for pleasing transition
    const t = curvature * curvature * (3 - 2 * curvature);
    trackWidths[i] = TRACK_WIDTH_MAX - (TRACK_WIDTH_MAX - TRACK_WIDTH_MIN) * t;
}
```

**Smoothing pass**: After initial width assignment, run a moving average (window size 10) to prevent abrupt width changes. The track should flow smoothly between wide and narrow sections.

**Edge point generation**: At each sample, calculate the perpendicular direction (normal to the track direction), then offset left and right by half the track width:

```javascript
for (let i = 0; i < totalSamples; i++) {
    const next = (i + 1) % totalSamples;
    const dx = centerPoints[next].x - centerPoints[i].x;
    const dy = centerPoints[next].y - centerPoints[i].y;
    const len = Math.sqrt(dx*dx + dy*dy);
    const nx = -dy / len;  // Perpendicular (normal)
    const ny = dx / len;
    const halfWidth = trackWidths[i] / 2;

    leftEdge[i]  = { x: centerPoints[i].x + nx * halfWidth, y: centerPoints[i].y + ny * halfWidth };
    rightEdge[i] = { x: centerPoints[i].x - nx * halfWidth, y: centerPoints[i].y - ny * halfWidth };
}
```

### 3.6 Checkpoint Placement

Checkpoints are gates placed across the track at even arc-length intervals.

**Arc length calculation**:
```javascript
let arcLengths = [0];
let totalArcLength = 0;
for (let i = 1; i < totalSamples; i++) {
    const dx = centerPoints[i].x - centerPoints[i-1].x;
    const dy = centerPoints[i].y - centerPoints[i-1].y;
    totalArcLength += Math.sqrt(dx*dx + dy*dy);
    arcLengths[i] = totalArcLength;
}
// Add final segment (close the loop)
const dx = centerPoints[0].x - centerPoints[totalSamples-1].x;
const dy = centerPoints[0].y - centerPoints[totalSamples-1].y;
totalArcLength += Math.sqrt(dx*dx + dy*dy);
```

**Checkpoint count**: `numCheckpoints = rng.intRange(8, 12)`. The start/finish line is always checkpoint 0.

**Checkpoint placement**:
```javascript
const checkpointSpacing = totalArcLength / numCheckpoints;
for (let c = 0; c < numCheckpoints; c++) {
    const targetArc = c * checkpointSpacing;
    // Find the sample index closest to this arc length
    const sampleIndex = findNearestArcLength(arcLengths, targetArc);

    checkpoints.push({
        index: sampleIndex,
        position: centerPoints[sampleIndex],
        leftPoint: leftEdge[sampleIndex],
        rightPoint: rightEdge[sampleIndex],
        direction: trackDirections[sampleIndex]  // tangent direction
    });
}
```

**Checkpoint crossing detection** (used during race): A ray from the ship's previous position to current position is tested against each checkpoint's gate line segment. If the ray crosses the gate, the checkpoint is triggered.

### 3.7 Start/Finish Line

The start/finish line is placed at checkpoint 0. The ship starts here, positioned on the track centerline, facing the track direction at this point.

```javascript
const startCheckpoint = checkpoints[0];
startPosition = {
    x: startCheckpoint.position.x,
    y: startCheckpoint.position.y
};
startAngle = Math.atan2(
    trackDirections[startCheckpoint.index].y,
    trackDirections[startCheckpoint.index].x
);
```

The start/finish line is visually distinct from regular checkpoints (brighter, wider, different color).

### 3.8 Difficulty Parameters *(Planned)*

The `difficulty` parameter is accepted by `generate()` but not yet wired up. All tracks currently use the values documented in sections 3.2 and 3.5. The planned difficulty tiers:

| Parameter | Easy | Medium | Hard |
|-----------|------|--------|------|
| Control points | 8-9 | 10-11 | 12-13 |
| Radius variance | 0.85-1.15 | 0.7-1.3 | 0.6-1.4 |
| Angular perturbation | +/-0.15 rad | +/-0.25 rad | +/-0.35 rad |
| Track width min | 180 px | 140 px | 100 px |
| Track width max | 360 px | 320 px | 260 px |

For the prototype, difficulty defaults to "Medium". The daily challenge system (future backend) will cycle difficulty.

### 3.9 Track Bounds & Validation

**Bounding box**: After generating all edge points, calculate:
```javascript
bounds = {
    minX: Math.min(...allEdgePoints.map(p => p.x)) - PADDING,
    minY: Math.min(...allEdgePoints.map(p => p.y)) - PADDING,
    maxX: Math.max(...allEdgePoints.map(p => p.x)) + PADDING,
    maxY: Math.max(...allEdgePoints.map(p => p.y)) + PADDING
};
```

`PADDING = 300px` gives room for the starfield background beyond track edges.

**Track validation**: After generation, verify:
1. No self-intersection (track centerline doesn't cross itself) -- **Implemented**
2. *(Planned)* Minimum track width never drops below 60px (safety floor)
3. *(Planned)* Total track length is within reasonable range
4. *(Planned)* All checkpoints are reachable in order

If validation fails, regenerate with a modified seed (`seed + '_retry' + attempt`).

### 3.10 TrackData Object

The generator returns this object, consumed by all other systems:

```javascript
TrackData = {
    centerPoints: [{x, y}],      // Spline samples (320-520)
    leftEdge: [{x, y}],          // Left boundary points
    rightEdge: [{x, y}],         // Right boundary points
    widths: [Number],            // Track width at each sample
    curvatures: [Number],        // Normalized curvature at each sample
    directions: [{x, y}],       // Tangent direction at each sample
    checkpoints: [{
        index: Number,
        position: {x, y},
        leftPoint: {x, y},
        rightPoint: {x, y},
        direction: {x, y}
    }],
    startPosition: {x, y},
    startAngle: Number,
    bounds: {minX, minY, maxX, maxY},
    totalArcLength: Number,
    totalSamples: Number,         // centerPoints.length
    seed: String
}
```

---

## 4. Visual Effects

### 4.1 Color Palette

All colors drawn from the gamification system's space theme palette:

| Element | Color Name | Hex | Usage |
|---------|-----------|-----|-------|
| Track edge (primary) | Cyan Glow | `#2ce8f5` | Inner neon edge line |
| Track edge (outer glow) | Cyan Glow dim | `#0a8ea0` | Outer wider glow line |
| Track surface | Deep Space | `#141428` at 70% opacity | Semi-transparent track fill |
| Ship body (default) | Purple-slate | `#505080` | Config-driven body fill (see 4.10) |
| Ship edge (default) | Cyan Glow | `#2ce8f5` | Config-driven outline + glow (see 4.10) |
| Ship cockpit (default) | Cyan Glow | `#2ce8f5` at 50% | Config-driven cockpit window (see 4.10) |
| Engine trail | Config `trailColor` | Default `#2ce8f5` | Config-driven, fading particle trail |
| Retro-thrust trail | Config `brakeColor` | Default `#f5602c` | Config-driven, paired with trailColor per skin |
| Off-track sparks | Red | `#e43b44` | Short-lived sparks |
| Ghost ship | Void Purple | `#6b2fa0` at 40% opacity | Semi-transparent |
| Checkpoint gate | Neon Green | `#40e850` | Pulsing gate line |
| Checkpoint (crossed) | Star White dim | `#e8e8ff` at 20% | Faded after crossing |
| Start/finish line | Gold | `#d4a017` | Brighter, wider than checkpoints |
| Background base | Deepest Space | `#0a0a14` | Canvas background color |
| Starfield (far) | Neutral dim | `#3a3a5c` at 20-40% | Tiny dots, slow parallax |
| Starfield (mid) | Neutral | `#6b6b8d` at 30-60% | Medium dots |
| Starfield (near) | Star White | `#e8e8ff` at 50-80% | Larger dots, fast parallax |
| HUD text | Star White | `#e8e8f0` | Primary HUD text |
| HUD accent | Cyan Glow | `#2ce8f5` | Speed bar, active elements |
| Countdown numbers | Star White | `#ffffff` | Large countdown text |
| "GO!" text | Neon Green | `#40e850` | Final countdown flash |
| Minimap track | Cyan dim | `#2ce8f5` at 40% | Track outline |
| Minimap player | Star White | `#ffffff` | Player dot |
| Minimap ghost | Void Purple | `#6b2fa0` | Ghost dots |

### 4.2 Ship Rendering

The ship is a geometric arrow shape drawn with Phaser Graphics:

```
Ship vertices (pointing right, angle = 0):
  Nose:         ( 16,   0)
  Right wing:   (-12,  10)
  Right notch:  ( -6,   4)
  Left notch:   ( -6,  -4)
  Left wing:    (-12, -10)
```

These form a sleek arrow/chevron shape. The notch at the back represents the engine bay.

**Rendering layers** (back to front, all on the same Graphics object):
1. **Glow**: Outline stroke at width 3, primaryColor at 30% alpha. Soft bloom for visual presence.
2. **Body fill**: Solid polygon fill with accentColor at 100% alpha. Must contrast with track background (`#0a0a14`).
3. **Cockpit window**: Diamond shape near the nose, cockpitColor at 50% fill + 90% edge. The ship's most distinctive interior detail.
4. **Edge stroke**: Outline at width 2, primaryColor at 100% alpha. Defines the ship's shape.
5. **Thrust flame** (when thrusting): Flickering triangle at engine bay with random length. Outer flame in primaryColor, inner core in white.
6. **Brake indicator** (when braking): Small triangle at nose, orange (`#f5602c`).

All colors are config-driven (see Section 4.10 for customization points).

The ship's Graphics object is rotated to match `ship.rotation` each frame.

**Off-track feedback**: When the ship leaves the track, the edge stroke and glow alpha oscillate at 8Hz (sine wave), creating a "signal interference" flicker. This is palette-independent: it modulates brightness, not hue, so it works with every color preset. The flicker is complemented by off-track spark particles (Section 4.5) for strong multi-layered feedback.

### 4.3 Track Surface & Neon Edge Lines

The track is rendered once to a **RenderTexture** for performance, then displayed as a static sprite that scrolls with the camera.

**Track surface**: A filled polygon connecting all left-edge points (forward), then all right-edge points (reverse), creating a closed shape. Filled with the track surface color.

**Neon edge lines** (the signature visual): Two passes per edge:
1. **Outer glow**: 6px wide line in `#0a8ea0` at 40% opacity. Creates the wide, dim glow.
2. **Inner line**: 2px wide line in `#2ce8f5` at 100% opacity. The bright core.

This two-pass technique creates a convincing neon glow effect without shaders.

**Start/finish line**: A bright line across the track at checkpoint 0, colored gold (`#d4a017`), with a dashed center line.

### 4.4 Background Starfield (3-Layer Parallax)

Three layers of randomly placed dots create depth:

| Layer | Count | Size | Opacity | Scroll Speed | Color |
|-------|-------|------|---------|--------------|-------|
| Far | 200 | 0.5-1px | 0.2-0.4 | 0.1x camera | `#3a3a5c` |
| Mid | 100 | 1-2px | 0.3-0.6 | 0.3x camera | `#6b6b8d` |
| Near | 50 | 2-3px | 0.5-0.8 | 0.6x camera | `#e8e8ff` |

Star positions are generated from the track seed (deterministic). Each layer is rendered to its own RenderTexture that tiles seamlessly. The parallax effect is achieved by scrolling each layer at a fraction of the camera's movement.

**Optional accent**: 2-3 very faint colored nebula blobs (radial gradients, 3-5% opacity) placed in the background using purple and blue tones. These are purely decorative.

### 4.5 Particle Systems

All particles use Phaser 3.60+'s built-in `ParticleEmitter` system. A single 8x8 white circle texture (`shipParticle`) is generated programmatically once and shared by all three emitters. Each emitter uses `tintFill: true` to replace the white with its color while preserving the circle's alpha shape.

**Engine trail** (emitted when thrusting):
- Spawn point: ship's engine bay position (ship-local -6*s, 0)
- Direction: opposite to ship facing (+/- 15 degrees spread)
- Speed: 50-100 px/s
- Lifespan: 300-500ms
- Scale: 0.4 start, 0 end (shrink to nothing)
- Color: config `trailColor` (default cyan `#2ce8f5`)
- Blend mode: ADD
- Frequency: 33ms (~30 particles/second while thrusting)
- Max particles: 60

**Retro-thrust particles** (emitted when braking):
- Spawn point: ship's nose (ship-local 16*s, 0)
- Direction: ship's facing direction (+/- 20 degrees)
- Speed: 30-60 px/s
- Lifespan: 200-300ms
- Scale: 0.3 start, 0 end
- Color: config `brakeColor` (default orange-red `#f5602c`)
- Blend mode: ADD
- Frequency: 50ms (~20 particles/second while braking)
- Max particles: 30

**Off-track sparks** (emitted while off-track and speed > 50 px/s):
- Spawn point: ship center
- Direction: 120-degree cone opposite to velocity
- Speed: 80-150 px/s
- Lifespan: 150-250ms
- Scale: 0.3, no shrink (sharp cutoff)
- Color: `#e43b44` (red, palette-independent)
- Blend mode: NORMAL
- Rate: proportional to speed (frequency 50ms at low speed, 25ms at max speed)
- Max particles: 40

**Checkpoint crossing flash** (burst when crossing a checkpoint):
- Two short lines expanding outward from the checkpoint gate endpoints
- Color: `#40e850` (green)
- Duration: 200ms
- This is done with tweened Graphics objects, not particles

### 4.6 Countdown Animation

3-2-1-GO sequence before race start:

| Time | Display | Font Size | Color | Effect |
|------|---------|-----------|-------|--------|
| T-3s | "3" | 120px | `#ffffff` | Fade in + scale from 150% to 100% |
| T-2s | "2" | 120px | `#ffffff` | Fade in + scale from 150% to 100% |
| T-1s | "1" | 120px | `#ffffff` | Fade in + scale from 150% to 100% |
| T-0s | "GO!" | 140px | `#40e850` | Flash + scale to 200% + fade out |

Each number is displayed for 800ms, then fades for 200ms. "GO!" lingers for 300ms before fading. Total countdown duration: 3.3 seconds.

Font: Poppins 700 (matches site typography). Text is centered on screen. A subtle screen flash (white overlay at 5% opacity, 100ms) accompanies each beat.

Audio: countdown beep on each number (see Section 9).

### 4.7 Finish Line Effect

When crossing the finish line for the final time:
- Screen flash: white overlay, 10% opacity, fades over 500ms
- Particle burst at ship position: 40 particles, all directions, gold color (`#d4a017`), 600ms lifespan
- Ship continues moving for 1-2 seconds (momentum) while input is locked
- HUD timer flashes/pulses to indicate final time
- Transition to results scene after 2 seconds

### 4.8 Ghost Ship Rendering

Ghost ships are semi-transparent versions of the player ship:
- Same 5-vertex arrow shape
- Fill: Void Purple (`#6b2fa0`) at 40% opacity
- No edge stroke (keeps it ghostly)
- No glow layer
- Subtle pulsing opacity (35-45%) via sine wave
- No particle trail (performance + visual clarity: only the player has particles)

When multiple ghosts are present, each gets a slightly different shade:
- Ghost 1: `#6b2fa0` (Void Purple)
- Ghost 2: `#b55088` (Pink)
- Ghost 3: `#0099db` (Blue)

### 4.9 Checkpoint Gate Pulse

Uncrossed checkpoints pulse gently to draw attention:
- Two dots at each checkpoint gate endpoint, size 4px
- Color: `#40e850` (Neon Green)
- Opacity oscillates between 0.3 and 0.7 on a sine wave (period: 2 seconds)
- A faint line connects the two dots across the track (1px, same color, 20% opacity)

After crossing: dots shrink to 2px, opacity drops to 0.15, line disappears. The checkpoint is "used up."

### 4.10 Ship Customization Points

The ship's visual identity is broken into four customization categories. Each category maps to one or more config properties that a skin overrides. The rendering code reads all colors from `this.config`, so applying a skin is just passing a different config object to the Ship constructor.

| # | Category | Config Properties | What It Controls |
|---|----------|-------------------|------------------|
| 1 | **Ship Body** | `accentColor` + vertex shape | Body fill color AND ship silhouette shape. These are paired because a body type defines both its polygon vertices and its base color. Different body types can have different cockpit/detail geometry built into their vertex array. |
| 2 | **Outline / Glow** | `primaryColor` | The neon edge stroke and the glow bloom around the ship. This is the ship's most visible color at a distance. |
| 3 | **Cockpit Detail** | `cockpitColor` | The diamond window near the nose. Paired with the body type since different ship shapes may have different cockpit geometry. |
| 4 | **Engine Trail** | `trailColor` | Thrust flame color and engine trail particle tint. Visible when thrusting. |
| 5 | **Brake Trail** | `brakeColor` | Brake indicator flame color and retro-thrust particle tint. Paired with `trailColor` per skin so thrust and brake are visually distinct but harmonious. |

**Off-track warning** uses alpha flicker (see Section 4.2), not color changes. This is intentionally palette-independent so it works with every skin.

**Off-track sparks** use hardcoded red (`#e43b44`) regardless of skin. They're a track-state indicator, not ship identity.

**Skin structure** (future):
```javascript
{
    name: 'Nebula Runner',
    body: {
        vertices: SHIP_VERTICES_ALT_1,  // Different silhouette
        accentColor: 0x604080,
        cockpitColor: 0xb060ff,
        cockpitVertices: [...],          // Optional custom cockpit shape
    },
    outline: {
        primaryColor: 0xb060ff,
    },
    trail: {
        trailColor: 0xb060ff,            // Engine trail + thrust flame
        brakeColor: 0xe84040,            // Brake trail + brake indicator
    },
}
```

### 4.11 Screen Vignette

A subtle darkening around the screen edges:
- Radial gradient overlay on the UI layer
- Center: fully transparent
- Edges: `#000000` at 30% opacity
- Creates a "cockpit view" framing effect
- Does not obscure HUD elements (HUD is drawn after vignette)

---

## 5. HUD Design

### 5.1 Speed/Thrust Indicator

**Type**: Horizontal bar, bottom-left corner.

**Dimensions**: 180px wide, 12px tall, rounded corners (3px radius).

**Structure**:
- Background: `#141428` (Deep Space 2) with 1px border of `#3a3a5c` (Neutral Dark)
- Fill: Linear gradient from `#0a8ea0` (left) to `#2ce8f5` (right)
- Fill width proportional to `currentSpeed / MAX_SPEED`

**Label**: "SPD" text above the bar, 10px, `#6b6b8d` (Neutral Mid)

**Numeric readout**: Current speed value to the right of the bar, 14px monospace, `#e8e8f0`

### 5.2 Velocity Vector Indicator

**Unique to space physics.** A small arrow near the ship (or on the HUD) showing the actual direction of travel vs. the direction the ship faces.

**Implementation**: A small arrow icon on the HUD, bottom-left area (below speed bar):
- Arrow rotates to show velocity direction relative to screen
- Color: `#2ce8f5` when velocity aligns with facing (within 30 degrees)
- Color: `#f77622` when velocity diverges significantly (> 60 degrees)
- Color gradient between these based on angle difference
- Size: 30x30px
- Label: "VEL" text, 10px

This helps new players understand the space physics. When the arrow and ship disagree strongly, the player knows they need to correct their trajectory.

### 5.3 Lap Counter

**Position**: Top-right corner, 20px from edges.

**Format**: "LAP 2/3" (race mode) or "TIME TRIAL" (time trial mode)

**Font**: Poppins 600, 22px, `#e8e8f0`

**Animation on lap complete**: Text scales up 120% and flashes cyan for 500ms, then returns to normal.

### 5.4 Timer Display

**Position**: Top-center.

**Layout** (stacked):
```
  01:23.456        <- Total time (20px, Poppins 600, #e8e8f0)
  LAP  00:28.123   <- Current lap time (14px, monospace, #6b6b8d)
  BEST 00:27.891   <- Best lap time (14px, monospace, #d4a017 gold)
```

**Format**: `MM:SS.mmm` (minutes, seconds, milliseconds)

**Best lap**: Only shown after completing the first lap. Highlighted in gold. If current lap is on pace to beat best, current lap time pulses gently.

**On race complete**: Total time flashes and scales up. Final time is emphasized.

### 5.5 Minimap

**Position**: Bottom-right corner.

**Size**: 150x150px.

**Elements**:
- Background: `#0a0a14` at 80% opacity (semi-transparent dark overlay)
- Border: 1px `#3a3a5c`
- Track outline: simplified polyline (every 10th center point), 1px, `#2ce8f5` at 40%
- Player dot: 4px circle, `#ffffff`, solid
- Ghost dots: 3px circles, ghost colors (see 4.8), 60% opacity
- Checkpoint markers: 2px dots, `#40e850` at 30% (uncrossed) / 10% (crossed)
- Next checkpoint: brightest green dot (helps player know where to go)

**Coordinate mapping**: Track bounds are scaled to fit the 150x150 minimap. Ship and ghost positions are transformed to minimap coordinates each frame.

### 5.6 Layout at 1280x720

```
+------------------------------------------------------------------+
|  [SPD label]                    01:23.456                LAP 2/3 |
|  [====Speed Bar====]          LAP  00:28.123                      |
|  [Velocity Arrow]             BEST 00:27.891                      |
|                                                                    |
|                                                                    |
|                         (Game World)                               |
|                                                                    |
|                                                                    |
|                                                                    |
|  [SPD label]                                        +----------+  |
|  [====Speed Bar====]                                | Minimap  |  |
|  [Velocity Arrow]                                   |          |  |
|                                                     +----------+  |
+------------------------------------------------------------------+
```

All HUD elements are on a fixed camera layer (Phaser's UI camera) so they don't move with the game world camera.

---

## 6. Scene Flow & UX

### 6.1 Scene Architecture

```
MenuScene ──[START]──> RaceScene ──[FINISH]──> ResultsScene
    ^                      |                        |
    |                      |[QUIT/ESC]              |[NEW TRACK]
    +----------------------+------------------------+
                           |
                           |[RACE AGAIN]
                           +-----> RaceScene (same seed)
```

Three Phaser scenes. Only one active at a time (Phaser's scene manager handles transitions).

### 6.2 Menu Scene

**Visual layout**:
```
+------------------------------------------------------------------+
|                                                                    |
|              S T E L L A R   C I R C U I T                        |
|                    [neon glow text]                                |
|                                                                    |
|    Seed: [_______________] [Today's Track]                        |
|                                                                    |
|    +--------------------+     Mode: [3-Lap Race] [Time Trial]     |
|    |                    |                                          |
|    |   Track Preview    |     Ghosts:                              |
|    |   (minimap-style)  |     [x] Personal Best  00:45.231       |
|    |                    |     [ ] Ghost #2        00:47.892       |
|    +--------------------+                                          |
|                                                                    |
|                  [ L A U N C H ]                                   |
|                  [neon pulse btn]                                   |
|                                                                    |
+------------------------------------------------------------------+
```

**Seed input**: DOM text input element positioned via CSS over the Phaser canvas. "Today's Track" button generates seed from current date: `hashString(new Date().toISOString().slice(0, 10) + '-stellar-circuit')`.

**Track preview**: Small rendering of the generated track (reuses TrackRenderer at reduced scale). Updates live as the seed changes (debounced, 300ms).

**Mode select**: Toggle between "3-Lap Race" and "Time Trial". Affects `TrackData.numLaps` passed to RaceScene.

**Ghost panel**: Lists available ghosts from localStorage for the current seed + mode. Checkbox to enable/disable each. Max 3 active ghosts.

**Background**: Animated starfield (same as race background, slower speed).

**LAUNCH button**: Neon-bordered rectangle with pulse animation. Starts the race.

### 6.3 Countdown Overlay

Not a separate scene. It's a state within RaceScene.

During countdown:
- Ship is visible at start position but input is locked
- Camera is positioned looking at the ship
- Countdown numbers display center-screen (see Section 4.6)
- Audio beeps play (see Section 9)
- Ghost ships are visible at start position (not moving)

On "GO!":
- Input unlocks
- Timer starts
- Ghost playback begins
- State transitions to RACING

### 6.4 Race Scene Game Loop

**State machine**:
```
COUNTDOWN -> RACING -> FINISHED
```

**COUNTDOWN state** (3.3 seconds):
- Render track, ship, ghosts, HUD
- Display countdown overlay
- No input processed for ship
- On countdown complete: transition to RACING

**RACING state** (core loop):
```
update(time, delta):
  1. dt = delta / 1000  (convert ms to seconds)
  2. inputState = InputManager.getState()
  3. Ship.update(dt, inputState, trackData)
  4. checkCheckpointCrossings()
  5. checkLapComplete()
  6. updateGhostPlayback(elapsedRaceTime)
  7. GhostRecorder.recordFrame(ship.getState())
  8. HUD.update(ship, timer, lapData)
  9. updateCamera(ship)
  10. // Particles update automatically via Phaser
```

**Checkpoint crossing**: For each uncrossed checkpoint, test if the line segment from `ship.previousPosition` to `ship.position` intersects the checkpoint gate line. If yes, mark checkpoint as crossed.

**Lap completion**: When all checkpoints are crossed AND the start/finish line is crossed:
1. Increment lap count
2. Record lap time
3. Reset checkpoint crossing flags
4. If lap count >= target laps: transition to FINISHED

**FINISHED state** (2 seconds):
- Input locked
- Ship continues on momentum (drag naturally slows it)
- Finish line effect plays (Section 4.7)
- After 2 seconds: transition to ResultsScene with race data

### 6.5 Results Scene

**Data passed from RaceScene**:
```javascript
{
    totalTimeMs: Number,
    lapTimes: [Number],         // ms per lap
    bestLapMs: Number,
    ghostData: Object,          // Recorded ghost frames
    seed: String,
    mode: 'race' | 'timetrial',
    trackData: TrackData        // For background rendering
}
```

**Visual layout**:
```
+------------------------------------------------------------------+
|                                                                    |
|                      R A C E   C O M P L E T E                    |
|                                                                    |
|                        01:23.456                                   |
|                     [large neon time]                              |
|                                                                    |
|     Lap 1:  00:28.123                                             |
|     Lap 2:  00:27.891  BEST                                       |
|     Lap 3:  00:27.442  BEST  [gold highlight]                    |
|                                                                    |
|     Personal Best: 01:25.102                                       |
|     NEW RECORD!  [animated, if beaten]                             |
|                                                                    |
|   [RACE AGAIN]    [TIME TRIAL]    [NEW TRACK]                    |
|                                                                    |
+------------------------------------------------------------------+
```

**Personal best**: Loaded from localStorage. If total time < stored best, show "NEW RECORD!" with celebration animation and auto-save ghost.

**Background**: Static render of the track, zoomed out to show full circuit, with the player's ghost path overlaid as a faint line.

### 6.6 Pause Behavior

**Trigger**: ESC key (desktop) or a pause button on the HUD (mobile).

**During pause**:
- Game update loop stops (`scene.scene.pause()` is NOT used: we manage our own state to freeze physics while keeping rendering)
- Dark overlay (50% opacity black) covers the game
- "PAUSED" text displayed center-screen
- Options: "RESUME" (ESC again or click), "QUIT TO MENU"
- Timer stops counting
- Ghost playback freezes

### 6.7 Scene Transitions

All transitions use a brief fade-to-black:
- 200ms fade to black
- Scene swap
- 200ms fade from black

Phaser's camera fade methods: `camera.fadeOut(200)` then `camera.fadeIn(200)` on the new scene.

---

## 7. Ghost System

### 7.1 Recording Format

Ghost data is recorded at 10fps (one frame every 100ms):

```javascript
ghostFrame = {
    x: Number,        // World position X
    y: Number,        // World position Y
    r: Number,        // Rotation (radians)
    t: Boolean,       // Is thrusting (for visual trail)
    s: Number         // Speed (0-1 normalized)
}
```

Stored as a flat array for compactness:
```javascript
// [x, y, r, t_as_0or1, s, x, y, r, t, s, ...]
ghostFrames = [120.5, 340.2, 1.57, 1, 0.85, 121.1, 339.8, 1.55, 1, 0.86, ...]
```

5 values per frame.

### 7.2 Data Size Estimation

- 3-lap race at ~45 seconds = 450 frames at 10fps
- 5 values per frame at ~8 bytes each (JS float) = 40 bytes/frame
- Total: ~18KB per ghost (uncompressed JSON)
- Time trial (1 lap, ~15 seconds) = 150 frames = ~6KB

localStorage limit is ~5MB per origin. At 18KB per ghost, that's ~280 race ghosts before hitting the limit. More than sufficient for a prototype.

### 7.3 Playback & Interpolation

Ghost playback uses linear interpolation between recorded frames:

```javascript
playback(elapsedMs) {
    const frameIndex = elapsedMs / SAMPLE_INTERVAL_MS;  // e.g., 2350ms / 100 = 23.5
    const i = Math.floor(frameIndex);
    const t = frameIndex - i;  // 0.5

    if (i >= totalFrames - 1) {
        // Ghost has finished: show at final position, faded out
        return lastFrame;
    }

    // Lerp between frame i and frame i+1
    return {
        x: frames[i].x + (frames[i+1].x - frames[i].x) * t,
        y: frames[i].y + (frames[i+1].y - frames[i].y) * t,
        r: lerpAngle(frames[i].r, frames[i+1].r, t),
        thrusting: frames[i].t,  // No lerp for boolean
        speed: frames[i].s + (frames[i+1].s - frames[i].s) * t
    };
}
```

**Angle interpolation** (`lerpAngle`): Must handle wrapping around PI/-PI. Use the shortest-arc approach:

```javascript
function lerpAngle(a, b, t) {
    const raw = b - a;
    const diff = ((raw + Math.PI) % (2 * Math.PI) + 2 * Math.PI) % (2 * Math.PI) - Math.PI;
    return a + diff * t;
}
```

### 7.4 Multiple Ghost Rendering

Up to 3 ghosts can play simultaneously. Each gets:
- Its own `GhostPlayback` instance
- Its own color (see Section 4.8)
- Its own minimap dot

All ghosts start playback at race start (when "GO!" fires). If a ghost's recording ends before the player finishes, the ghost fades out at its final position.

**Performance**: 3 ghosts at 10fps playback = 30 interpolation calculations per second. Negligible CPU cost. The visual cost is 3 additional ship renderings per frame, also negligible.

### 7.5 localStorage Persistence (Prototype)

**Key pattern**: `stellar_circuit_{seed}_{mode}`

**Stored data**:
```javascript
{
    frames: [Number],      // Flat array [x,y,r,t,s, x,y,r,t,s, ...]
    totalTimeMs: Number,
    lapTimes: [Number],
    bestLapMs: Number,
    recordedAt: String,    // ISO 8601 date
    version: 1             // For future migration if format changes
}
```

**Operations**:
- `save(seed, mode, ghostData)`: Overwrites any existing ghost for this seed/mode (only stores the best)
- `load(seed, mode)`: Returns ghost data or null
- `getBestTime(seed, mode)`: Returns totalTimeMs or null
- `listAll()`: Returns all stored ghosts with seed, mode, time, date
- `clear()`: Removes all stored ghosts
- `getStorageUsed()`: Returns approximate bytes used

**Duplicate handling**: Only the best time per seed/mode is stored. If the player beats their time, the old ghost is replaced.

---

## 8. Camera System

### 8.1 Smooth Follow with Look-Ahead

The camera follows the ship but leads slightly in the direction of travel. Critically, the look-ahead follows the **velocity direction**, not the ship's facing direction. This means the camera shows where you're actually heading, not where the nose is pointed.

### 8.2 Velocity-Based Look-Ahead

```javascript
const LOOK_AHEAD_DISTANCE = 120;  // pixels
const CAMERA_SMOOTHING = 0.06;    // 0-1, lower = smoother

// Calculate look-ahead point based on velocity direction
const speed = Math.sqrt(ship.vx * ship.vx + ship.vy * ship.vy);
const speedRatio = Math.min(speed / MAX_SPEED, 1);

let targetX, targetY;
if (speed > MIN_SPEED) {
    // Look ahead in velocity direction
    const velAngle = Math.atan2(ship.vy, ship.vx);
    targetX = ship.x + Math.cos(velAngle) * LOOK_AHEAD_DISTANCE * speedRatio;
    targetY = ship.y + Math.sin(velAngle) * LOOK_AHEAD_DISTANCE * speedRatio;
} else {
    // When nearly stationary, look ahead in facing direction
    targetX = ship.x + Math.cos(ship.rotation) * LOOK_AHEAD_DISTANCE * 0.3;
    targetY = ship.y + Math.sin(ship.rotation) * LOOK_AHEAD_DISTANCE * 0.3;
}

// Smooth interpolation (use setScroll for Phaser's internal dirty-flag system)
const newX = camera.scrollX + (targetX - camera.scrollX - DESIGN_WIDTH/2) * CAMERA_SMOOTHING;
const newY = camera.scrollY + (targetY - camera.scrollY - DESIGN_HEIGHT/2) * CAMERA_SMOOTHING;
camera.setScroll(newX, newY);
```

**Why velocity-based**: With space physics, the ship often faces a very different direction than it's moving. A facing-based camera would swing wildly as the player rotates. Velocity-based look-ahead shows what's actually coming up, making gameplay much more readable.

**Speed-proportional look-ahead**: At low speeds, the camera barely leads. At high speeds, it looks further ahead, giving the player more time to react.

### 8.3 Zoom Level

**Fixed zoom**: The camera zoom stays constant during normal gameplay. The zoom level should show approximately 3-4x the track width around the ship.

```javascript
camera.setZoom(1.0);  // At 1280x720, this shows 1280x720 pixels of world space
```

The default zoom of 1.0 should work well with the track width (90-220px). The ship sees plenty of track ahead and around.

**Future consideration**: Dynamic zoom that pulls out on long straights and pushes in on tight curves. Not for prototype.

---

## 9. Audio Design

### 9.1 Sound Inventory

Minimal procedural sounds for the prototype:

| Sound | Trigger | Duration | Type |
|-------|---------|----------|------|
| Countdown beep (low) | "3", "2", "1" | 150ms | Square wave, 220Hz |
| Countdown beep (high) | "GO!" | 200ms | Square wave, 440Hz |
| Checkpoint ding | Crossing a checkpoint | 100ms | Sine wave, ascending 660->880Hz |
| Finish fanfare | Race complete | 500ms | Chord: C5-E5-G5 arpeggio |
| Engine hum | While thrusting | Continuous | Sawtooth oscillator, 80-160Hz (pitch = speed) |
| Off-track crunch | While off-track | Continuous (looped) | Low rumble, 40Hz sine + white noise at low volume |

### 9.2 Web Audio API Procedural Sounds

All sounds generated with the Web Audio API (no audio files):

```javascript
class SoundManager {
    constructor() {
        this.ctx = null;  // Lazy-init AudioContext (requires user gesture)
    }

    init() {
        if (!this.ctx) {
            this.ctx = new (window.AudioContext || window.webkitAudioContext)();
        }
    }

    playTone(frequency, duration, type = 'sine', volume = 0.3) {
        const osc = this.ctx.createOscillator();
        const gain = this.ctx.createGain();
        osc.type = type;
        osc.frequency.value = frequency;
        gain.gain.value = volume;
        gain.gain.exponentialRampToValueAtTime(0.001, this.ctx.currentTime + duration);
        osc.connect(gain);
        gain.connect(this.ctx.destination);
        osc.start();
        osc.stop(this.ctx.currentTime + duration);
    }
}
```

**AudioContext lazy initialization**: The Web Audio API requires a user gesture to start. We initialize the AudioContext on the first user interaction (click on LAUNCH button in menu scene).

**Engine hum**: A continuous oscillator whose frequency scales with ship speed. Starts when thrusting, stops when not:

```javascript
startEngine() {
    this.engineOsc = this.ctx.createOscillator();
    this.engineGain = this.ctx.createGain();
    this.engineOsc.type = 'sawtooth';
    this.engineOsc.frequency.value = 80;
    this.engineGain.gain.value = 0.1;
    this.engineOsc.connect(this.engineGain);
    this.engineGain.connect(this.ctx.destination);
    this.engineOsc.start();
}

updateEngineSound(speedRatio) {
    if (this.engineOsc) {
        this.engineOsc.frequency.value = 80 + speedRatio * 80;  // 80-160Hz
        this.engineGain.gain.value = 0.05 + speedRatio * 0.1;   // Louder at speed
    }
}
```

---

## 10. Difficulty & Track Variety

### 10.1 Difficulty Knobs

| Knob | Effect | Range |
|------|--------|-------|
| Control point count | More points = more complex track | 8-13 |
| Radius variance | Higher = more irregular shape | 0.6-1.4 multiplier |
| Angular perturbation | Higher = sharper unexpected turns | 0.15-0.35 rad |
| Track width range | Narrower min = tighter corners | 70-120 px min, 180-240 px max |
| Base radius | Larger = longer track = longer race | 600-1200 px |

### 10.2 Track Personality Types

The seed deterministically creates a "personality" for each track. While not explicitly categorized, tracks naturally fall into types based on the random parameters:

- **The Oval**: Low control point count, low variance. Fast, high-speed, few sharp turns. Time is won on smooth racing lines and momentum management.
- **The Pretzel**: High point count, high angular perturbation. Lots of twists and hairpins. Rewards rotation management and braking discipline.
- **The Squeeze**: Normal shape but very narrow sections. Technical, punishes going off-track. Precision over speed.
- **The Highway**: Wide track with gentle curves. Very fast, few tight spots. Tests top-speed management and racing line discipline.
- **The Maze**: High point count, high variance, narrow. The hardest type. Requires mastery of space physics to navigate.

These emerge naturally from the parametric generation, not explicit selection.

---

## 11. Controls

### 11.1 Desktop Keyboard Mapping

| Action | Primary Key | Secondary Key | Description |
|--------|-------------|--------------|-------------|
| Thrust | W | Up Arrow | Apply thrust in facing direction |
| Brake / Retro-thrust | S | Down Arrow | Apply force opposite to velocity |
| Rotate Left | A | Left Arrow | Rotate ship counter-clockwise |
| Rotate Right | D | Right Arrow | Rotate ship clockwise |
| Pause | Escape | - | Toggle pause overlay |

**No analog input**: Keyboard controls are binary (on/off). Rotation is at constant ROTATION_SPEED. Thrust is at full THRUST_FORCE. This keeps the keyboard experience simple and predictable.

### 11.2 Mobile Touch Controls

**Virtual joystick** (left half of screen):
- Touch-and-drag creates a virtual joystick
- Drag direction maps to rotation: left of center = rotate left, right of center = rotate right
- Drag distance maps to rotation speed: small drag = slow rotation, large drag = full speed rotation
- Any touch on the left half = thrust active (hold to thrust, release to coast)

**Brake button** (right half of screen):
- A dedicated brake zone in the bottom-right area
- Tap/hold = retro-thrust active

**Visual indicators**: The joystick area shows a faint circle (touch zone) and a small dot (current touch position) when active. These are on the UI layer, not in game world.

### 11.3 Control Scheme Alternatives

For future consideration (not in prototype):
- **Tap-to-thrust**: Touch anywhere = thrust toward touch point (auto-rotates). More casual-friendly.
- **Tilt steering**: Gyroscope controls rotation. Tap = thrust.
- **Dual-stick**: Left stick = rotate, right stick = thrust direction (independent aiming).

---

## 12. Technical Specifications

### 12.1 Phaser Configuration

```javascript
{
    type: Phaser.AUTO,          // WebGL with Canvas fallback
    parent: 'phaser-canvas',    // DOM element ID
    backgroundColor: '#0a0a14', // Deepest space
    scale: {
        mode: Phaser.Scale.FIT,
        autoCenter: Phaser.Scale.CENTER_BOTH,
        width: 1280,
        height: 720
    },
    // NO physics engine: custom physics in Ship.update()
    render: {
        pixelArt: false,        // Smooth rendering for vector graphics
        antialias: true
    }
}
```

### 12.2 Performance Budget

| Component | Budget | Notes |
|-----------|--------|-------|
| Track rendering | 0ms/frame | Pre-rendered to RenderTexture |
| Ship physics | < 0.5ms/frame | Simple math operations |
| Ghost interpolation (3) | < 0.1ms/frame | 3 lerp calculations |
| Particle updates | < 1ms/frame | ~150 max particles |
| HUD update | < 0.5ms/frame | Text + minimap redraw |
| Camera update | < 0.1ms/frame | Simple smoothing math |
| **Total game logic** | **< 2.5ms/frame** | Leaves plenty of room for rendering |

At 60fps, each frame has ~16.7ms. Game logic takes ~2.5ms, leaving ~14ms for Phaser's rendering pipeline.

### 12.3 Target Frame Rate

- **Desktop**: 60fps (vsync, consistent)
- **Mobile**: 60fps target, 30fps acceptable minimum
- **Delta time**: All physics use `delta` from Phaser's update loop, so the game runs correctly even if frames are dropped

If frame rate drops below 30fps: reduce particle count, simplify starfield layers.

---

## Appendix A: Naming Conventions

| Game concept | In-code name | Display name |
|-------------|-------------|-------------|
| The game | `stellar-circuit` / `stellarCircuit` | Stellar Circuit |
| The ship | `ship` | (no display name) |
| Track seed | `seed` | Seed / Track Code |
| Time trial | `timetrial` | Time Trial |
| 3-lap race | `race` | Race |
| Ghost replay | `ghost` | Ghost |
| Personal best | `personalBest` / `pb` | Personal Best / PB |
| Checkpoint | `checkpoint` | Sector |
| Off-track | `offTrack` | Off Course |

## Appendix B: Future Enhancements (Not in Prototype)

These are documented for future reference but are NOT part of the prototype scope:

1. **Obstacles** (asteroids, debris): Placed on wider track sections. Circular collision with the ship. Hitting one = speed penalty + knockback.
2. **Ship customization**: Skin system driven by the customization points defined in Section 4.10.
3. **Power-ups**: Shield (absorbs one obstacle hit), nitro (extended boost), magnet (pulls toward track center).
4. **Dynamic weather**: Solar flares (screen effects + drift increase), nebula (reduced visibility), asteroid belt (moving obstacles).
5. **Lap records by sector**: Track each sector time separately for detailed analysis.
6. **Real-time multiplayer**: WebSocket-based. 2-4 players racing simultaneously. Requires server infrastructure.
7. **Track editor**: Player-created tracks with approval system.
8. **Achievement system**: "Drift for 5 seconds straight", "Complete a lap without leaving the track", "Beat a ghost by >2 seconds".
