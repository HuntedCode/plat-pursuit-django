/**
 * PlatPursuit.Games.Driver.Ship
 *
 * Ship physics and rendering for Stellar Circuit.
 *
 * The ship uses thrust-based space physics: it has a rear thruster and
 * rotates to aim where thrust is applied. Momentum carries the ship
 * forward regardless of facing direction. This creates a natural
 * "drifting" feel where the player manages both facing and velocity.
 *
 * Responsibilities:
 * - CC tier system (50cc/100cc/200cc physics presets)
 * - Thrust-based movement (velocity vector, not steering)
 * - Rotation control (instant at 50cc, angular momentum at 100cc/200cc)
 * - Drag model (on-track vs off-track, frame-rate independent)
 * - Visual effects (off-track flicker, thrust flame, brake indicator)
 * - Ship rendering (6-layer geometric polygon: glow, body, cockpit,
 *   edge stroke, thrust flame, brake indicator)
 * - Velocity direction indicator
 * - Particle systems (engine trail, retro-thrust, off-track sparks)
 *
 * Architecture:
 * - Ship is created PER RACE (each race gets a fresh instance)
 * - The scene calls ship.update(dt, input, trackData) each frame
 * - Ship handles its own physics, rendering, and track interaction
 * - Scene reads ship.x, ship.y, ship.speed, etc. for camera and HUD
 * - Ship.destroy() cleans up all Phaser objects
 *
 * The Ship does NOT own:
 * - Camera follow (scene manages this, reads ship position)
 * - HUD/telemetry (scene reads ship state)
 * - Track generation/rendering (separate module)
 * - Input management (scene passes input state each frame)
 */

window.PlatPursuit = window.PlatPursuit || {};
window.PlatPursuit.Games = window.PlatPursuit.Games || {};
window.PlatPursuit.Games.Driver = window.PlatPursuit.Games.Driver || {};

(function() {
    'use strict';

    // Alias for track boundary testing
    const TrackGen = PlatPursuit.Games.Driver.TrackGenerator;

    // Shared particle texture key. Generated once per Phaser game instance,
    // reused across scene restarts and Ship recreations. The texture is a
    // tiny white circle that each emitter tints to its own color.
    const PARTICLE_TEXTURE_KEY = 'shipParticle';

    // ===================================================================
    // CC TIER SYSTEM
    // ===================================================================
    //
    // Three difficulty tiers that change the physics feel, inspired by
    // Mario Kart's CC system. Each tier is a complete set of physics
    // parameters. The ship reads all physics values from its active tier.
    //
    // Design philosophy:
    //   50cc:  Current physics, tuned during Steps 2-3. Forgiving and
    //          accessible. Hold thrust through most corners. Instant
    //          rotation (no angular momentum).
    //   100cc: ~30% faster top speed. Less on-track drag means more
    //          drift/slide. Harsher off-track penalty. Need to brake
    //          on tight turns. Mild angular momentum on rotation.
    //   200cc: ~60% faster. Lowest on-track drag (most drift). Harshest
    //          off-track penalty. Full angular momentum makes rotation
    //          a skill in itself. Counter-steering required.

    const CC_TIERS = {
        '50cc': {
            name: '50cc',
            thrust: 500,             // px/s^2
            maxSpeed: 650,           // px/s (soft cap via quadratic)
            rotationSpeed: 4.0,      // rad/s (instant, no angular momentum)
            brakeRatio: 0.6,         // Brake force = thrust * brakeRatio
            dragOnTrack: 0.015,      // Very low: preserves space feel
            dragOffTrack: 0.08,      // ~5x on-track: penalty for leaving
            // Angular momentum: off (instant rotation)
            angularMomentum: false,
        },
        '100cc': {
            name: '100cc',
            thrust: 650,
            maxSpeed: 850,
            rotationSpeed: 4.5,
            brakeRatio: 0.55,
            dragOnTrack: 0.012,      // Less drag = more slide
            dragOffTrack: 0.10,      // Harsher off-track
            // Angular momentum: mild (responsive, forgiving)
            angularMomentum: true,
            angularAccel: 12.0,      // rad/s^2 (builds fast)
            angularDrag: 0.08,       // Decays quickly when released
            maxAngularSpeed: 5.0,    // rad/s cap
        },
        '200cc': {
            name: '200cc',
            thrust: 800,
            maxSpeed: 1050,
            rotationSpeed: 5.0,
            brakeRatio: 0.5,
            dragOnTrack: 0.010,      // Lowest drag = most drift
            dragOffTrack: 0.12,      // Harshest off-track penalty
            // Angular momentum: full (requires counter-steering)
            angularMomentum: true,
            angularAccel: 10.0,      // rad/s^2 (builds slower than 100cc)
            angularDrag: 0.06,       // Decays slowly (must counter-steer)
            maxAngularSpeed: 5.5,    // rad/s cap (slightly higher ceiling)
        },
    };

    // Speed below which velocity snaps to zero (prevents infinitesimal crawl)
    const MIN_SPEED = 2;

    // ===================================================================
    // SHIP VISUAL CONFIG
    // ===================================================================
    //
    // Config-driven color system for future ship customization. Each
    // color serves a specific visual role. The constructor merges any
    // overrides from the config param with these defaults.
    //
    // The plan: players earn PlatCoins and unlock ship skins that
    // override these colors. The rendering code reads from this.config
    // so swapping a skin is just passing a different config object.

    const DEFAULT_SHIP_CONFIG = {
        primaryColor: 0x2ce8f5,    // Cyan: edge stroke, glow
        accentColor: 0x505080,     // Purple-slate: body fill (contrasts with track bg #0a0a14)
        cockpitColor: 0x2ce8f5,    // Cyan: cockpit window
        trailColor: 0x2ce8f5,      // Cyan: engine trail particles + thrust flame
        brakeColor: 0xf5602c,      // Orange-red: brake particles + brake indicator
    };

    // ===================================================================
    // DEV COLOR PRESETS (keys 4-9 in test scene)
    // ===================================================================
    //
    // Temporary presets for experimenting with outline/glow and body
    // colors. Uses colors from the gamification design palette. These
    // will be removed once we settle on defaults and build the skin
    // system. The number indicates which key activates it.

    const COLOR_PRESETS = {
        '4': {
            name: 'Cyan (Default)',
            primaryColor: 0x2ce8f5,
            accentColor: 0x505080,
            cockpitColor: 0x2ce8f5,
            trailColor: 0x2ce8f5,      // Cyan thrust
            brakeColor: 0xf5602c,      // Orange-red brake (complementary)
        },
        '5': {
            name: 'Orange (Driver)',
            primaryColor: 0xf77622,
            accentColor: 0x604030,
            cockpitColor: 0xfeae34,
            trailColor: 0xf77622,      // Orange thrust
            brakeColor: 0x2ce8f5,      // Cyan brake (inverted from default)
        },
        '6': {
            name: 'Void Purple',
            primaryColor: 0x6b2fa0,
            accentColor: 0x3a1850,
            cockpitColor: 0xb070e8,
            trailColor: 0xb070e8,      // Light purple thrust
            brakeColor: 0xe84040,      // Red brake
        },
        '7': {
            name: 'Neon Green',
            primaryColor: 0x40e850,
            accentColor: 0x305040,
            cockpitColor: 0x90ff90,
            trailColor: 0x40e850,      // Green thrust
            brakeColor: 0xfedc5a,      // Yellow brake (analogous)
        },
        '8': {
            name: 'Hot Pink',
            primaryColor: 0xff0066,
            accentColor: 0x502040,
            cockpitColor: 0xff80b0,
            trailColor: 0xff0066,      // Pink thrust
            brakeColor: 0xff8040,      // Peach-orange brake
        },
        '9': {
            name: 'Gold',
            primaryColor: 0xd4a017,
            accentColor: 0x504020,
            cockpitColor: 0xfedc5a,
            trailColor: 0xfedc5a,      // Light gold thrust
            brakeColor: 0xf77622,      // Deep orange brake
        },
    };

    // ===================================================================
    // SHIP VISUAL CONSTANTS
    // ===================================================================

    // Scale multiplier for all ship vertex coordinates.
    // 1.5 makes the ship visible without being oversized relative to
    // the track width (140-320px). At scale 1.5, the ship is ~48px
    // nose-to-tail and ~30px wingtip-to-wingtip.
    const SHIP_SCALE = 1.5;

    // Pre-computed vertex positions (avoids allocating a new array every
    // frame in drawShip). All coordinates are in ship-local space with
    // the origin at the ship's center. The ship points RIGHT at angle 0.
    //
    // Shape: a 5-point arrow/chevron with a notch at the back for the
    // engine bay. The notch is where thrust flame originates.
    //
    //       Nose (16, 0)
    //      /    \
    //     /      \
    //   Left     Right wing (-12, +/-10)
    //     \      /
    //      Notch (-6, +/-4)
    //
    const SHIP_VERTICES = [
        { x:  16 * SHIP_SCALE, y:   0 },             // Nose
        { x: -12 * SHIP_SCALE, y:  10 * SHIP_SCALE }, // Right wing
        { x:  -6 * SHIP_SCALE, y:   4 * SHIP_SCALE }, // Right engine notch
        { x:  -6 * SHIP_SCALE, y:  -4 * SHIP_SCALE }, // Left engine notch
        { x: -12 * SHIP_SCALE, y: -10 * SHIP_SCALE }, // Left wing
    ];

    // Maximum length of the velocity direction indicator arrow (in pixels).
    // The arrow scales with speed: at MAX_SPEED it reaches this full length.
    const VEL_INDICATOR_MAX_LENGTH = 70;

    // Minimum ship speed before the velocity indicator is drawn.
    // Below this threshold, the arrow would be too short to be useful
    // and would flicker near zero speed.
    const VEL_INDICATOR_MIN_SPEED = 5;

    // ===================================================================
    // SHIP CLASS
    // ===================================================================

    /**
     * The player's ship: physics state, update logic, and rendering.
     *
     * Lifecycle:
     *   1. Scene creates Ship in buildTrack() or create()
     *   2. Scene calls ship.update(dt, input, trackData) every frame
     *   3. Scene reads ship.x, ship.y, ship.speed, etc. for camera/HUD
     *   4. On track change, scene calls ship.reset() or creates a new Ship
     *   5. On scene shutdown, scene calls ship.destroy()
     *
     * @example
     *   const ship = new Ship(this, startX, startY, startAngle, {});
     *   // In update():
     *   ship.update(dt, inputState, trackData);
     *   // Camera follow:
     *   cam.scrollX += (ship.x - cam.scrollX) * lerp;
     */
    class Ship {
        /**
         * @param {Phaser.Scene} scene - The scene this ship belongs to
         * @param {number} x - Initial world X position
         * @param {number} y - Initial world Y position
         * @param {number} rotation - Initial facing angle in radians
         * @param {Object} [config={}] - Visual configuration overrides (used in Step 4D)
         * @param {string} [ccTier='50cc'] - Starting CC tier ('50cc', '100cc', '200cc')
         */
        constructor(scene, x, y, rotation, config = {}, ccTier = '50cc') {
            this.scene = scene;

            // ---------------------------------------------------------------
            // Visual Config (color customization)
            // ---------------------------------------------------------------
            // Merge passed overrides with defaults. Spread order matters:
            // defaults first, then overrides replace matching keys.
            this.config = { ...DEFAULT_SHIP_CONFIG, ...config };

            // ---------------------------------------------------------------
            // CC Tier (physics preset)
            // ---------------------------------------------------------------
            // The tier object holds all physics parameters. Every physics
            // calculation reads from this.tier instead of module constants.
            this.tier = CC_TIERS[ccTier] || CC_TIERS['50cc'];

            // ---------------------------------------------------------------
            // Physics State
            // ---------------------------------------------------------------
            this.x = x;
            this.y = y;
            this.vx = 0;
            this.vy = 0;
            this.rotation = rotation;
            this.speed = 0;       // Cached magnitude of (vx, vy)
            this.onTrack = true;  // Cache alias for trackInfo.onTrack (updated each frame)

            // Angular velocity for angular momentum (100cc/200cc).
            // At 50cc this stays 0 because rotation is instant.
            this.angularVelocity = 0;

            // Internal time accumulator for visual effects (flicker, etc.)
            // Incremented by dt each frame. Used instead of passing scene
            // time through the API to keep update() signature clean.
            this.elapsed = 0;

            // Track info from the most recent isOnTrack() call.
            // Stored so the scene can read it for telemetry without
            // calling isOnTrack() a second time.
            this.trackInfo = {
                onTrack: true,
                distFromCenter: 0,
                halfWidth: 0,
                nearestIndex: 0,
            };

            // ---------------------------------------------------------------
            // Graphics Objects
            // ---------------------------------------------------------------
            // Velocity indicator renders behind ship body, ship body
            // renders behind particles. Depth values enforce this
            // regardless of creation order (important for ESC track
            // regeneration, where the new track Graphics is created
            // after these objects already exist).
            this.velGraphics = scene.add.graphics();
            this.velGraphics.setDepth(10);
            this.shipGraphics = scene.add.graphics();
            this.shipGraphics.setDepth(11);

            // ---------------------------------------------------------------
            // Particle Emitters
            // ---------------------------------------------------------------
            // Three emitters for visual feedback: engine trail (thrust),
            // retro-thrust (brake), and off-track sparks. All share a
            // single white circle texture that each emitter tints.
            // Depth 12 places them above the ship body.
            this.createParticleEmitters();
        }

        // ===================================================================
        // PARTICLE EMITTERS
        // ===================================================================

        /**
         * Creates the shared particle texture (if needed) and three emitters.
         *
         * Texture: a tiny 8x8 white filled circle. Generated once per Phaser
         * game instance via generateTexture(), then reused across scene
         * restarts. Each emitter tints this white circle to its own color
         * using tintFill mode (vertex color replaces texture color, respects
         * texture alpha so the circle shape is preserved).
         *
         * Emitters:
         *   1. Engine trail: streams from engine bay when thrusting
         *   2. Retro-thrust: streams from nose when braking
         *   3. Off-track sparks: bursts from ship edges when off-track
         *
         * All emitters start with emitting: false. The update() method
         * enables/disables them based on input and ship state each frame.
         */
        createParticleEmitters() {
            const scene = this.scene;
            const cfg = this.config;
            const ship = this;  // Capture for onEmit callbacks

            // Generate the shared particle texture once. Phaser's texture
            // manager persists across scene restarts, so checking textures.exists()
            // avoids recreating it on every new Ship instance.
            if (!scene.textures.exists(PARTICLE_TEXTURE_KEY)) {
                const gfx = scene.add.graphics();
                gfx.fillStyle(0xffffff, 1);
                gfx.fillCircle(4, 4, 4);
                gfx.generateTexture(PARTICLE_TEXTURE_KEY, 8, 8);
                gfx.destroy();
            }

            // ----- Engine trail -----
            // Cyan (or trailColor) particles streaming behind the ship
            // when thrusting. ADD blend mode creates a glowing neon effect
            // against the dark track background.
            //
            // The angle uses an onEmit callback so each particle gets the
            // ship's current facing direction (opposite, +/-15 deg spread)
            // at the moment it's emitted. This is the correct Phaser 3.60+
            // pattern for dynamic particle directions: static assignment
            // via particleAngle doesn't reliably update the EmitterOp.
            this.engineEmitter = scene.add.particles(0, 0, PARTICLE_TEXTURE_KEY, {
                speed: { min: 50, max: 100 },
                angle: { onEmit: () => {
                    const backDeg = (ship.rotation * 180 / Math.PI) + 180;
                    return backDeg - 15 + Math.random() * 30;
                }},
                lifespan: { min: 300, max: 500 },
                scale: { start: 0.4, end: 0 },
                alpha: { start: 0.8, end: 0 },
                tint: cfg.trailColor,
                tintFill: true,
                blendMode: 'ADD',
                frequency: 33,        // ~30 particles/sec (1000ms / 30)
                maxParticles: 60,
                emitting: false,
            });

            // ----- Retro-thrust -----
            // Orange-red (or brakeColor) particles at the nose when braking.
            // Smaller and shorter-lived than engine trail to match the
            // subtler visual of the brake indicator flame.
            this.brakeEmitter = scene.add.particles(0, 0, PARTICLE_TEXTURE_KEY, {
                speed: { min: 30, max: 60 },
                angle: { onEmit: () => {
                    const fwdDeg = ship.rotation * 180 / Math.PI;
                    return fwdDeg - 20 + Math.random() * 40;
                }},
                lifespan: { min: 200, max: 300 },
                scale: { start: 0.3, end: 0 },
                alpha: { start: 0.7, end: 0 },
                tint: cfg.brakeColor,
                tintFill: true,
                blendMode: 'ADD',
                frequency: 50,        // ~20 particles/sec
                maxParticles: 30,
                emitting: false,
            });

            // ----- Off-track sparks -----
            // Red sparks that fly off the ship when it's off-track and
            // moving. NORMAL blend mode (not ADD) so sparks look solid
            // and gritty rather than glowing. Rate is controlled manually
            // in update() based on speed.
            //
            // Spark direction: 120-degree cone opposite to velocity.
            // When speed is very low, defaults to random (full 360).
            this.sparkEmitter = scene.add.particles(0, 0, PARTICLE_TEXTURE_KEY, {
                speed: { min: 80, max: 150 },
                angle: { onEmit: () => {
                    if (ship.speed > 5) {
                        const backVelDeg = Math.atan2(ship.vy, ship.vx) * 180 / Math.PI + 180;
                        return backVelDeg - 60 + Math.random() * 120;
                    }
                    return Math.random() * 360;
                }},
                lifespan: { min: 150, max: 250 },
                scale: { start: 0.3, end: 0.3 },   // No shrink: sharp cutoff
                alpha: { start: 0.9, end: 0.3 },
                tint: 0xe43b44,
                tintFill: true,
                blendMode: 'NORMAL',
                frequency: 50,        // Base rate, adjusted dynamically
                maxParticles: 40,
                emitting: false,
            });

            // Set depth on all emitters so they render above the ship body
            // regardless of creation order (same reason as Graphics depth).
            this.engineEmitter.setDepth(12);
            this.brakeEmitter.setDepth(12);
            this.sparkEmitter.setDepth(12);
        }

        // ===================================================================
        // CC TIER MANAGEMENT
        // ===================================================================

        /**
         * Switches the ship's physics preset mid-game.
         *
         * Resets angular velocity to 0 to prevent weirdness: if you're
         * spinning at 200cc's angular momentum and switch to 50cc (instant
         * rotation), the leftover angular velocity would be meaningless.
         *
         * @param {string} tierName - '50cc', '100cc', or '200cc'
         */
        setCCTier(tierName) {
            const newTier = CC_TIERS[tierName];
            if (!newTier) {
                console.warn(`[Ship] Unknown CC tier: "${tierName}"`);
                return;
            }
            this.tier = newTier;
            this.angularVelocity = 0;
            console.log(`[Ship] Switched to ${newTier.name}`);
        }

        /**
         * Switches the ship's color preset (dev/test feature).
         *
         * Applies a named color preset from COLOR_PRESETS, merging it
         * over the current config. This changes outline/glow, body fill,
         * and cockpit colors instantly.
         *
         * @param {string} presetKey - Key in COLOR_PRESETS ('4'-'9')
         * @returns {string|null} Preset name if applied, null if invalid
         */
        setColorPreset(presetKey) {
            const preset = COLOR_PRESETS[presetKey];
            if (!preset) return null;
            this.config = { ...this.config, ...preset };
            this.updateParticleColors();
            console.log(`[Ship] Color preset: ${preset.name}`);
            return preset.name;
        }

        // ===================================================================
        // PHYSICS UPDATE
        // ===================================================================

        /**
         * Full physics and rendering update for one frame.
         *
         * This is the main method called by the scene each frame. It
         * handles the complete ship lifecycle: physics simulation,
         * track boundary testing, and visual rendering.
         *
         * @param {number} dt - Delta time in seconds (delta / 1000)
         * @param {InputState} input - Current input state { up, down, left, right }
         * @param {Object} trackData - The track data object from TrackGenerator
         */
        update(dt, input, trackData) {
            const tier = this.tier;

            // ---------------------------------------------------------------
            // 1. ROTATION
            // ---------------------------------------------------------------
            // Behavior depends on whether the tier has angular momentum.
            //
            // Without angular momentum (50cc): pressing left/right rotates
            // at a fixed rate. Instant response, instant stop. Simple.
            //
            // With angular momentum (100cc/200cc): input accelerates an
            // angular velocity that persists after release. The ship keeps
            // spinning until angular drag slows it or the player counter-
            // steers. This creates a skill layer where precise rotation
            // management separates good players from great ones.
            if (tier.angularMomentum) {
                // Accelerate angular velocity from input
                if (input.left)  this.angularVelocity -= tier.angularAccel * dt;
                if (input.right) this.angularVelocity += tier.angularAccel * dt;

                // Clamp angular speed to the tier's ceiling
                if (this.angularVelocity > tier.maxAngularSpeed) {
                    this.angularVelocity = tier.maxAngularSpeed;
                } else if (this.angularVelocity < -tier.maxAngularSpeed) {
                    this.angularVelocity = -tier.maxAngularSpeed;
                }

                // Angular drag: frame-rate independent decay (same pattern
                // as linear drag). Only applies when no rotational input,
                // so the player has full control while pressing keys.
                if (!input.left && !input.right) {
                    this.angularVelocity *= Math.pow(1 - tier.angularDrag, dt * 60);
                    // Snap to zero below threshold to prevent infinitesimal spin
                    if (Math.abs(this.angularVelocity) < 0.05) {
                        this.angularVelocity = 0;
                    }
                }

                // Apply angular velocity to rotation
                this.rotation += this.angularVelocity * dt;
            } else {
                // No angular momentum: instant rotation, no decay
                if (input.left)  this.rotation -= tier.rotationSpeed * dt;
                if (input.right) this.rotation += tier.rotationSpeed * dt;
            }

            // ---------------------------------------------------------------
            // 2. THRUST
            // ---------------------------------------------------------------
            // Force is applied in the ship's facing direction. The soft cap
            // uses a quadratic curve: thrust effectiveness drops as speed
            // approaches maxSpeed. At 50% max speed you get 75% thrust,
            // at 90% you get 19%, at 100% you get 0%. This creates a natural
            // speed ceiling without a jarring hard stop.
            if (input.up) {
                const speedRatio = this.speed / tier.maxSpeed;
                const thrustMultiplier = Math.max(0, 1 - speedRatio * speedRatio);
                this.vx += Math.cos(this.rotation) * tier.thrust * thrustMultiplier * dt;
                this.vy += Math.sin(this.rotation) * tier.thrust * thrustMultiplier * dt;
            }

            // ---------------------------------------------------------------
            // 3. BRAKE (retro-thrust)
            // ---------------------------------------------------------------
            // Braking applies force OPPOSITE to the current velocity vector,
            // not the facing direction. This is retro-thrust: it slows you
            // down regardless of which way you're pointing.
            //
            // Speed is recomputed from (vx, vy) after thrust so the brake
            // uses this frame's actual velocity, not last frame's cached
            // value. The clampedBrake prevents overshoot: without it, a
            // strong brake force on a slow-moving ship could reverse the
            // velocity direction (ship suddenly moves backward).
            {
                const currentSpeed = Math.sqrt(this.vx * this.vx + this.vy * this.vy);
                if (input.down && currentSpeed > 1) {
                    const brakeForce = tier.thrust * tier.brakeRatio;
                    const velAngle = Math.atan2(this.vy, this.vx);
                    const clampedBrake = Math.min(brakeForce * dt, currentSpeed);
                    this.vx -= Math.cos(velAngle) * clampedBrake;
                    this.vy -= Math.sin(velAngle) * clampedBrake;
                }
            }

            // ---------------------------------------------------------------
            // 4. DRAG (frame-rate independent)
            // ---------------------------------------------------------------
            // On-track: very low drag, space feel preserved.
            // Off-track: heavy drag, the ship slows rapidly.
            //
            // Uses last frame's on/off-track state for drag selection.
            // This is acceptable because the boundary test (step 5) needs
            // the NEW position, and drag must be applied before integration.
            //
            // Frame-rate independence: multiplicative drag must use
            // exponential decay scaled by dt. Math.pow(1 - drag, dt * 60)
            // normalizes the decay to a 60fps baseline. Without this, the
            // ship would lose more speed at lower frame rates.
            const drag = this.onTrack ? tier.dragOnTrack : tier.dragOffTrack;
            const dragFactor = Math.pow(1 - drag, dt * 60);
            this.vx *= dragFactor;
            this.vy *= dragFactor;

            // ---------------------------------------------------------------
            // 5. UPDATE POSITION
            // ---------------------------------------------------------------
            // Standard Euler integration: position += velocity * time
            this.x += this.vx * dt;
            this.y += this.vy * dt;

            // ---------------------------------------------------------------
            // 6. ON/OFF-TRACK BOUNDARY TEST
            // ---------------------------------------------------------------
            // Check the ship's NEW position against the track geometry.
            // Runs after position update so the test reflects where the
            // ship actually is this frame, not where it was last frame.
            // The result is stored for next frame's drag selection and
            // for the scene to read (HUD, sparks, etc.).
            this.trackInfo = TrackGen.isOnTrack(this.x, this.y, trackData);
            this.onTrack = this.trackInfo.onTrack;

            // ---------------------------------------------------------------
            // 7. CACHE SPEED + SNAP-TO-ZERO
            // ---------------------------------------------------------------
            // Speed is cached so other systems (thrust multiplier, brake,
            // telemetry, velocity indicator) don't each recompute sqrt.
            // Below MIN_SPEED, snap to zero to prevent infinitesimal crawl.
            this.speed = Math.sqrt(this.vx * this.vx + this.vy * this.vy);
            if (this.speed < MIN_SPEED) {
                this.vx = 0;
                this.vy = 0;
                this.speed = 0;
            }

            // ---------------------------------------------------------------
            // 8. ACCUMULATE TIME (for visual effects)
            // ---------------------------------------------------------------
            this.elapsed += dt;

            // ---------------------------------------------------------------
            // RENDERING
            // ---------------------------------------------------------------
            this.drawShip(input);
            this.drawVelocityIndicator();
            this.updateParticles(input);
        }

        // ===================================================================
        // SHIP RENDERING
        // ===================================================================

        /**
         * Draws the ship with layered visual effects optimized for
         * readability at the ship's small screen size (~48px).
         *
         * All colors are read from this.config, enabling future skin
         * customization by passing different config objects.
         *
         * Rendering order (back to front on the same Graphics object):
         * 1. Glow: soft bloom around the ship for neon presence
         * 2. Body fill: semi-transparent, lighter than track background
         * 3. Cockpit window: bright diamond near the nose
         * 4. Edge stroke: crisp neon outline
         * 5. Thrust flame: animated triangle at engine bay (when thrusting)
         * 6. Brake indicator: small triangle at nose (when braking)
         *
         * Design note: we tried finer details (wing accent lines, engine
         * nacelles) but at ~48px they were invisible. The current layers
         * are chosen so each one is clearly distinguishable at game scale.
         *
         * @param {InputState} input - For conditional flame/brake rendering
         */
        drawShip(input) {
            const g = this.shipGraphics;
            g.clear();
            g.setPosition(this.x, this.y);
            g.setRotation(this.rotation);

            const s = SHIP_SCALE;
            const cfg = this.config;

            // Off-track flicker: when off-track, the edge stroke and glow
            // rapidly oscillate in alpha, creating a "signal interference"
            // effect. This is palette-independent (works with any color)
            // because it modulates brightness, not hue.
            //
            // The flicker uses a fast sine wave (~8Hz) that oscillates
            // the edge alpha between 0.3 and 1.0. The glow alpha oscillates
            // between 0.1 and 0.5. This creates an urgent, unstable feel
            // without changing the ship's colors.
            let edgeAlpha = 1.0;
            let glowAlpha = 0.3;
            if (!this.onTrack) {
                // 8Hz sine wave: fast enough to feel urgent, slow enough
                // to be clearly visible (not a seizure-inducing strobe)
                const flicker = Math.sin(this.elapsed * 8 * 2 * Math.PI);
                // Remap sine [-1, 1] to alpha range
                edgeAlpha = 0.3 + (1 + flicker) * 0.35;   // 0.3 to 1.0
                glowAlpha = 0.1 + (1 + flicker) * 0.2;    // 0.1 to 0.5
            }

            const edgeColor = cfg.primaryColor;
            const glowColor = cfg.primaryColor;
            const bodyColor = cfg.accentColor;

            // ----- Layer 1: Glow -----
            // Soft bloom around the ship outline for neon presence.
            // Alpha modulated by off-track flicker.
            this.drawShipPolygon(g, SHIP_VERTICES, null, {
                width: 3, color: glowColor, alpha: glowAlpha,
            });

            // ----- Layer 2: Body fill -----
            // Solid fill, lighter than the track background so the ship
            // silhouette is always visible. Full opacity ensures the body
            // reads clearly against the dark track.
            this.drawShipPolygon(g, SHIP_VERTICES, {
                color: bodyColor, alpha: 1.0,
            }, null);

            // ----- Layer 3: Cockpit window -----
            // A bright diamond near the nose. This is the ship's most
            // distinctive interior detail: large enough to read at
            // game scale, and the bright fill contrasts against the
            // darker body.
            g.fillStyle(cfg.cockpitColor, 0.5);
            g.beginPath();
            g.moveTo(12 * s,  0);           // Front point (near nose)
            g.lineTo(7 * s,   2.5 * s);     // Right
            g.lineTo(4 * s,   0);           // Rear point
            g.lineTo(7 * s,  -2.5 * s);     // Left
            g.closePath();
            g.fillPath();
            // Cockpit edge: bright outline to define the window shape
            g.lineStyle(1, cfg.cockpitColor, 0.9);
            g.strokePath();

            // ----- Layer 4: Edge stroke -----
            // Crisp neon outline on top of everything. This is what
            // defines the ship's shape. Alpha modulated by off-track
            // flicker to create an urgent "signal interference" effect.
            this.drawShipPolygon(g, SHIP_VERTICES, null, {
                width: 2, color: edgeColor, alpha: edgeAlpha,
            });

            // ----- Layer 5: Thrust flame -----
            // A flickering triangle behind the ship at the engine bay.
            // The random length creates a natural flame animation without
            // needing a sprite sheet or tween. Uses config trailColor.
            if (input.up) {
                const flameLength = (8 + Math.random() * 12) * s;
                const flameWidth = 4 * s;

                // Outer flame: trail color, semi-transparent
                g.fillStyle(cfg.trailColor, 0.6);
                g.beginPath();
                g.moveTo(-6 * s,  flameWidth);
                g.lineTo(-6 * s, -flameWidth);
                g.lineTo(-6 * s - flameLength, 0);
                g.closePath();
                g.fillPath();

                // Inner core: white, more transparent (bright center)
                g.fillStyle(0xffffff, 0.4);
                g.beginPath();
                g.moveTo(-6 * s,  flameWidth * 0.5);
                g.lineTo(-6 * s, -flameWidth * 0.5);
                g.lineTo(-6 * s - flameLength * 0.6, 0);
                g.closePath();
                g.fillPath();
            }

            // ----- Layer 6: Brake indicator -----
            // Small triangle at nose when braking, suggesting forward-facing
            // retro-thrusters that oppose the current velocity. Uses config
            // brakeColor so it matches the brake particle trail.
            if (input.down && this.speed > 1) {
                const brakeLength = (4 + Math.random() * 6) * s;
                const brakeWidth = 2.5 * s;
                g.fillStyle(cfg.brakeColor, 0.5);
                g.beginPath();
                g.moveTo(16 * s,  brakeWidth);
                g.lineTo(16 * s, -brakeWidth);
                g.lineTo(16 * s + brakeLength, 0);
                g.closePath();
                g.fillPath();
            }
        }

        /**
         * Helper: draws a closed polygon with optional fill and stroke.
         *
         * Abstracts the Phaser Graphics polygon API so each rendering
         * layer (glow, body, edge) can be drawn with a single call.
         *
         * @param {Phaser.GameObjects.Graphics} graphics - Target graphics object
         * @param {Array<{x,y}>} vertices - Polygon vertices in local space
         * @param {Object|null} fill - { color, alpha } or null to skip fill
         * @param {Object|null} stroke - { width, color, alpha } or null to skip
         */
        drawShipPolygon(graphics, vertices, fill, stroke) {
            if (fill) graphics.fillStyle(fill.color, fill.alpha);
            if (stroke) graphics.lineStyle(stroke.width, stroke.color, stroke.alpha);
            graphics.beginPath();
            graphics.moveTo(vertices[0].x, vertices[0].y);
            for (let i = 1; i < vertices.length; i++) {
                graphics.lineTo(vertices[i].x, vertices[i].y);
            }
            graphics.closePath();
            if (fill) graphics.fillPath();
            if (stroke) graphics.strokePath();
        }

        // ===================================================================
        // VELOCITY INDICATOR
        // ===================================================================

        /**
         * Draws an arrow showing the ship's actual direction of travel.
         *
         * This is the key visual tool for understanding space physics.
         * The ship's facing direction (where the nose points) and its
         * velocity direction (where it's actually moving) are often
         * different. The arrow makes this difference visible.
         *
         * The arrow starts from the ship's center and extends in the
         * velocity direction. Its length scales with speed. Color
         * changes based on "drift angle" (difference between facing
         * and velocity directions):
         *   - Green: drift < 30 degrees (well aligned)
         *   - Yellow: drift 30-90 degrees (moderate drift)
         *   - Red: drift > 90 degrees (high drift, moving sideways/backward)
         *
         * Renders BEHIND the ship body (velGraphics is created before
         * shipGraphics so Phaser draws it first in the render order).
         */
        drawVelocityIndicator() {
            const g = this.velGraphics;
            g.clear();

            // Don't draw at very low speeds (arrow would be tiny and flickery)
            if (this.speed < VEL_INDICATOR_MIN_SPEED) return;

            // Arrow direction: the velocity vector's angle
            const velAngle = Math.atan2(this.vy, this.vx);

            // Arrow starts from ship center (no offset)
            const startX = this.x;
            const startY = this.y;

            // Arrow length scales with speed, capped at max length.
            // Uses the tier's maxSpeed so the indicator scales correctly
            // at all CC tiers (a full-length arrow always means "at max speed").
            const indicatorLength = Math.min(
                VEL_INDICATOR_MAX_LENGTH,
                this.speed / this.tier.maxSpeed * VEL_INDICATOR_MAX_LENGTH
            );

            // Calculate drift angle: difference between velocity and facing.
            // Normalized to [-PI, PI] via modulo (safe for any input,
            // including NaN/Infinity, unlike a while-loop approach).
            const rawDrift = velAngle - this.rotation;
            const driftAngle = ((rawDrift + Math.PI) % (2 * Math.PI) + 2 * Math.PI) % (2 * Math.PI) - Math.PI;

            // Color based on drift magnitude
            const absDrift = Math.abs(driftAngle);
            let color;
            if (absDrift < Math.PI / 6) {
                // < 30 degrees: well aligned, green
                color = 0x40e850;
            } else if (absDrift < Math.PI / 2) {
                // 30-90 degrees: moderate drift, yellow
                color = 0xe8d040;
            } else {
                // > 90 degrees: high drift (sideways/backward), red
                color = 0xe84040;
            }

            // Arrow shaft
            const endX = startX + Math.cos(velAngle) * indicatorLength;
            const endY = startY + Math.sin(velAngle) * indicatorLength;

            g.lineStyle(2, color, 0.7);
            g.beginPath();
            g.moveTo(startX, startY);
            g.lineTo(endX, endY);
            g.strokePath();

            // Arrow head (filled triangle)
            const headLength = 6;
            const headAngle = 0.5;  // radians, controls spread
            g.fillStyle(color, 0.7);
            g.beginPath();
            g.moveTo(endX, endY);
            g.lineTo(
                endX - Math.cos(velAngle - headAngle) * headLength,
                endY - Math.sin(velAngle - headAngle) * headLength
            );
            g.lineTo(
                endX - Math.cos(velAngle + headAngle) * headLength,
                endY - Math.sin(velAngle + headAngle) * headLength
            );
            g.closePath();
            g.fillPath();
        }

        // ===================================================================
        // PARTICLE MANAGEMENT
        // ===================================================================

        /**
         * Positions and enables/disables particle emitters each frame.
         *
         * Each emitter needs its spawn position updated to follow the ship,
         * and its emitting state toggled based on input and ship status.
         *
         * Particle DIRECTION is handled by onEmit callbacks defined in
         * createParticleEmitters(). Each callback reads the ship's current
         * rotation/velocity at emission time, so direction is always correct
         * without needing per-frame updates here.
         *
         * @param {InputState} input - Current input state
         */
        updateParticles(input) {
            const s = SHIP_SCALE;
            const cos = Math.cos(this.rotation);
            const sin = Math.sin(this.rotation);

            // ----- Engine trail -----
            // Spawn point: engine bay (ship-local -6*s, 0), transformed
            // to world space.
            if (this.engineEmitter) {
                const engineLocalX = -6 * s;
                const engineWorldX = this.x + engineLocalX * cos;
                const engineWorldY = this.y + engineLocalX * sin;
                this.engineEmitter.setPosition(engineWorldX, engineWorldY);
                if (input.up && !this.engineEmitter.emitting) {
                    this.engineEmitter.start();
                } else if (!input.up && this.engineEmitter.emitting) {
                    this.engineEmitter.stop();
                }
            }

            // ----- Retro-thrust -----
            // Spawn point: nose (ship-local 16*s, 0). Only emits when
            // braking AND moving (speed > 1).
            if (this.brakeEmitter) {
                const noseLocalX = 16 * s;
                const noseWorldX = this.x + noseLocalX * cos;
                const noseWorldY = this.y + noseLocalX * sin;
                this.brakeEmitter.setPosition(noseWorldX, noseWorldY);
                const shouldBrake = input.down && this.speed > 1;
                if (shouldBrake && !this.brakeEmitter.emitting) {
                    this.brakeEmitter.start();
                } else if (!shouldBrake && this.brakeEmitter.emitting) {
                    this.brakeEmitter.stop();
                }
            }

            // ----- Off-track sparks -----
            // Spawn at ship center. Rate scales with speed: faster = more
            // sparks (frequency interpolates from 50ms to 25ms).
            if (this.sparkEmitter) {
                this.sparkEmitter.setPosition(this.x, this.y);

                const shouldSpark = !this.onTrack && this.speed > 50;
                if (shouldSpark && !this.sparkEmitter.emitting) {
                    this.sparkEmitter.start();
                } else if (!shouldSpark && this.sparkEmitter.emitting) {
                    this.sparkEmitter.stop();
                }

                if (shouldSpark) {
                    const speedRatio = Math.min(this.speed / this.tier.maxSpeed, 1);
                    this.sparkEmitter.frequency = 50 - speedRatio * 25;
                }
            }
        }

        /**
         * Destroys all particle emitters. Used before recreating them
         * with new colors (Phaser 3.60+ EmitterOps are immutable after
         * creation, so the only reliable way to change tint is to
         * recreate the emitter).
         */
        destroyParticleEmitters() {
            if (this.engineEmitter) { this.engineEmitter.destroy(); this.engineEmitter = null; }
            if (this.brakeEmitter) { this.brakeEmitter.destroy(); this.brakeEmitter = null; }
            if (this.sparkEmitter) { this.sparkEmitter.destroy(); this.sparkEmitter = null; }
        }

        /**
         * Recreates particle emitters to match the current config colors.
         *
         * Called when the color preset changes so particles immediately
         * use the new trail/brake colors. Off-track sparks stay red
         * regardless of skin (they're a track-state indicator, not identity).
         */
        updateParticleColors() {
            this.destroyParticleEmitters();
            this.createParticleEmitters();
        }

        // ===================================================================
        // LIFECYCLE
        // ===================================================================

        /**
         * Repositions the ship for a new track without destroying objects.
         *
         * Used when regenerating tracks (ESC key). Cheaper than creating
         * a new Ship because it reuses the existing Graphics objects.
         *
         * @param {number} x - New world X position
         * @param {number} y - New world Y position
         * @param {number} rotation - New facing angle in radians
         */
        reset(x, y, rotation) {
            this.x = x;
            this.y = y;
            this.vx = 0;
            this.vy = 0;
            this.rotation = rotation;
            this.speed = 0;
            this.onTrack = true;
            this.angularVelocity = 0;
            this.elapsed = 0;

            // Stop all particle emission so leftover particles from the
            // previous track don't linger at the old position.
            if (this.engineEmitter) this.engineEmitter.stop();
            if (this.brakeEmitter) this.brakeEmitter.stop();
            if (this.sparkEmitter) this.sparkEmitter.stop();
        }

        /**
         * Cleans up all Phaser objects owned by this ship.
         *
         * Must be called when the scene shuts down or when the ship
         * is no longer needed. Without this, Graphics objects would
         * leak in the Phaser scene's display list.
         */
        destroy() {
            if (this.shipGraphics) {
                this.shipGraphics.destroy();
                this.shipGraphics = null;
            }
            if (this.velGraphics) {
                this.velGraphics.destroy();
                this.velGraphics = null;
            }
            this.destroyParticleEmitters();
            this.scene = null;
        }
    }

    // ===================================================================
    // PUBLIC API
    // ===================================================================

    PlatPursuit.Games.Driver.Ship = {
        /** The Ship class. Create one instance per race. */
        Ship,
        /** Vertex coordinates for the ship polygon (shared with Ghost rendering) */
        SHIP_VERTICES,
        /** Scale multiplier for ship vertex coordinates */
        SHIP_SCALE,
    };

    console.log('[Stellar Circuit] Ship loaded');
})();
