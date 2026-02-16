/**
 * Stellar Circuit - Entry Point
 *
 * This is the last script loaded. It creates the Phaser game instance,
 * registers all scenes, and starts the game.
 *
 * Step 2 Version: Interactive test scene demonstrating the Shell config
 * factory, InputManager, and basic thrust-based space physics. A ship
 * shape responds to keyboard input with momentum-based movement.
 *
 * This test scene will be replaced with proper scene registration
 * (MenuScene, RaceScene, ResultsScene) in Step 5+.
 */

window.PlatPursuit = window.PlatPursuit || {};
window.PlatPursuit.Games = window.PlatPursuit.Games || {};
window.PlatPursuit.Games.Driver = window.PlatPursuit.Games.Driver || {};

(function() {
    'use strict';

    // Aliases for readability
    const Shell = PlatPursuit.Games.Shell;
    const InputManager = PlatPursuit.Games.Input.InputManager;

    // -----------------------------------------------------------------------
    // Physics Constants (simplified for Step 2 test)
    // -----------------------------------------------------------------------
    //
    // These are tuning values that control how the ship feels to fly.
    // In Step 4, these will move into the Ship class with the full physics
    // model from the GDD. For now, we use a simplified subset to prove
    // the concept works.
    //
    // All values are in "design pixels per second" (px/s) or per-second-squared
    // (px/s^2). Using per-second units makes them frame-rate independent when
    // multiplied by dt (delta time in seconds).

    /** How fast the ship rotates, in radians per second.
     *  At 4.0, a full 360-degree rotation takes ~1.57 seconds (2*PI / 4.0).
     *  This feels responsive without being twitchy. */
    const ROTATION_SPEED = 4.0;

    /** Acceleration applied in the ship's facing direction when thrusting.
     *  500 px/s^2 means after 1 second of continuous thrust from a standstill,
     *  the ship moves at 500 px/s (fast, but drag limits the actual top speed). */
    const THRUST_FORCE = 500;

    /** Deceleration applied opposite to velocity when braking.
     *  60% of thrust force: braking is effective but not instant.
     *  Players need to plan ahead for turns, not just slam the brakes. */
    const BRAKE_FORCE = THRUST_FORCE * 0.6;

    /** Drag coefficient applied every frame.
     *  velocity *= (1 - DRAG) each frame means velocity decays exponentially.
     *  0.015 is very low: the ship barely slows down on its own, preserving
     *  the "floating in space" feel. Over ~3 seconds of no thrust, the ship
     *  loses about 60% of its speed (0.985^180 for 60fps * 3s = ~0.065). */
    const DRAG = 0.015;

    /** Soft speed cap in px/s. Thrust becomes less effective as the ship
     *  approaches this speed. The diminishing returns formula:
     *  effectiveThrust = thrust * max(0, 1 - (speed/MAX_SPEED)^2)
     *  At 90% of max speed, thrust is reduced to ~19%. At max speed, it's 0. */
    const MAX_SPEED = 650;

    // -----------------------------------------------------------------------
    // Step 2 Test Scene
    // -----------------------------------------------------------------------

    /**
     * InputTestScene: Verifies Shell + InputManager + basic thrust physics.
     *
     * What it demonstrates:
     * 1. Shell.createConfig() produces a working Phaser config
     * 2. InputManager correctly reads keyboard input
     * 3. Thrust-based movement feels "spacey" (momentum, drift, rotation)
     * 4. The ship's facing direction is independent of its movement direction
     *
     * Controls displayed on screen. Press W to thrust, A/D to rotate.
     * The ship should drift in the direction of its velocity, not the
     * direction it's facing. This is the core feel of space physics.
     */
    class InputTestScene extends Phaser.Scene {
        constructor() {
            super({ key: 'InputTestScene' });
        }

        create() {
            const { width, height } = this.scale;

            // Remove the HTML loading indicator
            const loadingEl = document.getElementById('game-loading');
            if (loadingEl) loadingEl.remove();

            // ---------------------------------------------------------------
            // Ship State
            // ---------------------------------------------------------------
            // In Step 4, this becomes the Ship class. For now, a plain object
            // holding position, velocity, and rotation.

            this.ship = {
                x: width / 2,       // Start at center
                y: height / 2,
                vx: 0,              // Velocity components (px/s)
                vy: 0,
                rotation: -Math.PI / 2,  // Facing UP (Phaser's 0 is right/east)
                speed: 0,           // Cached magnitude of velocity vector
            };

            // ---------------------------------------------------------------
            // Draw boundary markers (FIRST so they render behind everything)
            // ---------------------------------------------------------------
            // Phaser draws objects in creation order: first created = drawn
            // first = rendered behind later objects. Grid dots need to be
            // behind the ship, so we create them before the ship Graphics.

            const gridGraphics = this.add.graphics();
            gridGraphics.fillStyle(0x1a1a2e, 0.8);
            const gridSpacing = 80;
            for (let gx = 0; gx <= width; gx += gridSpacing) {
                for (let gy = 0; gy <= height; gy += gridSpacing) {
                    gridGraphics.fillCircle(gx, gy, 1.5);
                }
            }

            // ---------------------------------------------------------------
            // Graphics Objects (created after grid so they render on top)
            // ---------------------------------------------------------------

            // Velocity vector indicator: a line showing the direction the
            // ship is actually moving (as opposed to where it's facing).
            // This is crucial for space physics. The angle between the ship's
            // nose and this line IS the "drift angle."
            // Created before ship so it renders behind the ship body.
            this.velIndicator = this.add.graphics();

            // Ship graphics: drawn at local origin, positioned each frame.
            // We learned in Step 1: draw at (0,0) on the Graphics object,
            // then set the object's position to the ship's world coordinates.
            // This way rotation tweens/transforms pivot around the ship center.
            // Created last of the game objects so it renders on top.
            this.shipGraphics = this.add.graphics();

            // ---------------------------------------------------------------
            // HUD Text (on top of everything)
            // ---------------------------------------------------------------

            // Title
            this.add.text(width / 2, 20, 'S T E L L A R   C I R C U I T', {
                fontFamily: 'Poppins, sans-serif',
                fontSize: '24px',
                fontStyle: 'bold',
                color: '#2ce8f5',
            }).setOrigin(0.5, 0);

            // Step indicator
            this.add.text(width / 2, 50, 'Step 2: Shell + Input + Physics Test', {
                fontFamily: 'Inter, sans-serif',
                fontSize: '14px',
                color: '#40e850',
            }).setOrigin(0.5, 0);

            // Controls help
            const controlsText = [
                'W / Up Arrow: Thrust',
                'S / Down Arrow: Brake',
                'A / Left Arrow: Rotate Left',
                'D / Right Arrow: Rotate Right',
                'ESC: Reset Ship Position',
            ].join('\n');

            this.add.text(16, 80, controlsText, {
                fontFamily: 'Inter, sans-serif',
                fontSize: '13px',
                color: '#6b6b8d',
                lineSpacing: 4,
            });

            // Dynamic telemetry display (updated each frame)
            this.telemetryText = this.add.text(16, height - 16, '', {
                fontFamily: 'monospace',
                fontSize: '12px',
                color: '#6b6b8d',
                lineSpacing: 2,
            }).setOrigin(0, 1);

            // Input state display (updated each frame)
            this.inputText = this.add.text(width - 16, height - 16, '', {
                fontFamily: 'monospace',
                fontSize: '12px',
                color: '#6b6b8d',
                lineSpacing: 2,
                align: 'right',
            }).setOrigin(1, 1);

            // ---------------------------------------------------------------
            // Input Manager
            // ---------------------------------------------------------------

            this.inputManager = new InputManager(this);

            // Clean up when scene shuts down
            this.events.on('shutdown', () => {
                this.inputManager.destroy();
            });

            console.log('[Stellar Circuit] Step 2 test scene created');
            console.log('[Stellar Circuit] Use WASD or Arrow keys to fly the ship');
        }

        /**
         * Game loop: called every frame by Phaser.
         *
         * @param {number} time  - Total elapsed time in milliseconds
         * @param {number} delta - Time since last frame in milliseconds
         *
         * Why delta matters:
         * If the game runs at 60fps, delta is ~16.67ms. At 30fps, it's ~33.33ms.
         * By multiplying all physics values by dt (delta in seconds), the ship
         * moves the same distance per real-time second regardless of frame rate.
         * This is "frame-rate independence": the game feels the same whether
         * the player gets 30fps or 144fps.
         */
        update(time, delta) {
            // Convert milliseconds to seconds for physics calculations.
            // All our constants (THRUST_FORCE, ROTATION_SPEED, etc.) are
            // defined in per-second units, so we need dt in seconds.
            const dt = delta / 1000;

            // Poll the input manager for current state
            const input = this.inputManager.getState();

            // Check for reset (using pause key temporarily for the test)
            if (this.inputManager.isPausePressed()) {
                this.ship.x = this.scale.width / 2;
                this.ship.y = this.scale.height / 2;
                this.ship.vx = 0;
                this.ship.vy = 0;
                this.ship.rotation = -Math.PI / 2;
            }

            // ---------------------------------------------------------------
            // Physics Update
            // ---------------------------------------------------------------

            // 1. ROTATION
            // Rotation is immediate and always available, even without thrust.
            // In space, you can spin freely (no friction on rotation). Rotation
            // does NOT change velocity: it only changes which direction the
            // thruster will push when activated.
            if (input.left)  this.ship.rotation -= ROTATION_SPEED * dt;
            if (input.right) this.ship.rotation += ROTATION_SPEED * dt;

            // 2. THRUST (input.up = thrust in Stellar Circuit)
            // Apply acceleration in the ship's facing direction.
            // cos/sin convert the rotation angle into x/y components:
            //   cos(rotation) = x-component of the facing direction
            //   sin(rotation) = y-component of the facing direction
            //
            // The diminishing returns formula prevents infinite acceleration:
            // as speed approaches MAX_SPEED, thrust effectiveness drops to 0.
            // This creates a natural "soft cap" that feels better than a hard
            // velocity clamp (which would feel like hitting a wall).
            if (input.up) {
                const speedRatio = this.ship.speed / MAX_SPEED;
                const thrustMultiplier = Math.max(0, 1 - speedRatio * speedRatio);

                this.ship.vx += Math.cos(this.ship.rotation) * THRUST_FORCE * thrustMultiplier * dt;
                this.ship.vy += Math.sin(this.ship.rotation) * THRUST_FORCE * thrustMultiplier * dt;
            }

            // 3. BRAKE / Retro-thrust (input.down = brake in Stellar Circuit)
            // Unlike thrust (which pushes in the ship's FACING direction),
            // braking pushes AGAINST the ship's VELOCITY direction. This means
            // braking always slows you down, regardless of which way you're
            // facing. Think of it as "auto-retrograde" thrust.
            //
            // Without this, the only way to slow down would be to rotate 180
            // degrees and thrust. That's realistic but frustrating for gameplay.
            //
            // We also clamp to prevent reversing: if the brake force would
            // push velocity past zero (reversing direction), we just stop.
            if (input.down && this.ship.speed > 1) {
                const velAngle = Math.atan2(this.ship.vy, this.ship.vx);
                const brakeDelta = BRAKE_FORCE * dt;

                // Don't apply more braking than needed to reach zero.
                // Without this, a slow-moving ship would start moving backward.
                const clampedBrake = Math.min(brakeDelta, this.ship.speed);

                this.ship.vx -= Math.cos(velAngle) * clampedBrake;
                this.ship.vy -= Math.sin(velAngle) * clampedBrake;
            }

            // 4. DRAG
            // Multiply velocity by (1 - drag) each frame. This causes
            // exponential decay: fast ships slow faster in absolute terms,
            // but all ships lose the same PERCENTAGE per frame.
            //
            // Why not just subtract a constant? Because subtraction would
            // slow the ship linearly, meaning a barely-moving ship would
            // decelerate at the same rate as a fast one (feels wrong).
            // Multiplicative drag scales naturally with speed.
            this.ship.vx *= (1 - DRAG);
            this.ship.vy *= (1 - DRAG);

            // 5. UPDATE POSITION
            this.ship.x += this.ship.vx * dt;
            this.ship.y += this.ship.vy * dt;

            // 6. WRAP AROUND SCREEN (test scene only)
            // In the real game, the track provides boundaries. For this test,
            // wrapping the ship around the edges lets us fly freely without
            // disappearing forever.
            const { width, height } = this.scale;
            const margin = 30;
            if (this.ship.x < -margin) this.ship.x = width + margin;
            if (this.ship.x > width + margin) this.ship.x = -margin;
            if (this.ship.y < -margin) this.ship.y = height + margin;
            if (this.ship.y > height + margin) this.ship.y = -margin;

            // 7. CACHE SPEED (magnitude of velocity vector)
            // Used by the diminishing returns formula and telemetry display.
            // sqrt(vx^2 + vy^2) = Pythagorean theorem for vector magnitude.
            this.ship.speed = Math.sqrt(
                this.ship.vx * this.ship.vx + this.ship.vy * this.ship.vy
            );

            // ---------------------------------------------------------------
            // Rendering
            // ---------------------------------------------------------------

            this.drawShip(input);
            this.drawVelocityIndicator();
            this.updateTelemetry(input);
        }

        /**
         * Draws the ship at its current position and rotation.
         *
         * The ship is a 5-vertex arrow shape (same as the GDD spec).
         * It's drawn on a Graphics object at local origin (0,0), then
         * the Graphics object is positioned and rotated in world space.
         *
         * Visual layers (drawn back to front):
         * 1. Glow: thick, semi-transparent cyan stroke (simulates bloom)
         * 2. Body fill: near-white with slight blue tint
         * 3. Edge stroke: bright cyan outline
         * 4. Thrust indicator: small flame particles when thrusting
         *
         * @param {InputState} input - Current input state (for thrust visual)
         */
        drawShip(input) {
            const g = this.shipGraphics;
            g.clear();

            // Position the Graphics object at the ship's world position.
            // All drawing happens at local (0,0), which maps to this world pos.
            g.setPosition(this.ship.x, this.ship.y);

            // Set rotation on the Graphics object. Phaser's `rotation` property
            // is in radians. This rotates the entire Graphics object (and
            // everything drawn on it) around its origin.
            g.setRotation(this.ship.rotation);

            // Ship vertices in local space (from GDD Section 4.2).
            // The ship points RIGHT (+x direction) by default because
            // Phaser's 0-degree angle points right. Our rotation math
            // uses cos/sin which also assume 0 = right.
            //
            // Vertex layout (facing right):
            //           (-12, -10) ---- (-6, -4) ---- (16, 0) NOSE
            //           (-12,  10) ---- (-6,  4) ----/
            //                LEFT WING           RIGHT WING
            const scale = 1.5;
            const vertices = [
                { x:  16 * scale, y:   0 },           // Nose (front tip)
                { x: -12 * scale, y:  10 * scale },   // Bottom wing tip
                { x:  -6 * scale, y:   4 * scale },   // Bottom notch (inner)
                { x:  -6 * scale, y:  -4 * scale },   // Top notch (inner)
                { x: -12 * scale, y: -10 * scale },   // Top wing tip
            ];

            // Layer 1: Glow (wider, semi-transparent)
            this.drawShipPolygon(g, vertices, null, { width: 3, color: 0x2ce8f5, alpha: 0.3 });

            // Layer 2: Body fill
            this.drawShipPolygon(g, vertices, { color: 0xe8e8ff, alpha: 0.9 }, null);

            // Layer 3: Edge stroke
            this.drawShipPolygon(g, vertices, null, { width: 2, color: 0x2ce8f5, alpha: 1.0 });

            // Layer 4: Thrust flame (only when thrusting)
            // A small triangle behind the ship to show engine fire.
            // In Step 4, this becomes proper particle effects.
            if (input.up) {
                // Flame base is at the ship's rear center, flame tip extends behind.
                // We randomize the tip length each frame for a flickering effect.
                const flameLength = (8 + Math.random() * 12) * scale;
                const flameWidth = 4 * scale;

                g.fillStyle(0x2ce8f5, 0.6);
                g.beginPath();
                g.moveTo(-6 * scale,  flameWidth);   // Bottom of flame base
                g.lineTo(-6 * scale, -flameWidth);   // Top of flame base
                g.lineTo(-6 * scale - flameLength, 0); // Flame tip
                g.closePath();
                g.fillPath();

                // Inner bright core of the flame
                g.fillStyle(0xffffff, 0.4);
                g.beginPath();
                g.moveTo(-6 * scale,  flameWidth * 0.5);
                g.lineTo(-6 * scale, -flameWidth * 0.5);
                g.lineTo(-6 * scale - flameLength * 0.6, 0);
                g.closePath();
                g.fillPath();
            }

            // Layer 5: Brake indicator (thrust from the front when braking)
            if (input.down && this.ship.speed > 1) {
                const brakeLength = (4 + Math.random() * 6) * scale;
                const brakeWidth = 2.5 * scale;

                g.fillStyle(0xf5602c, 0.5);
                g.beginPath();
                g.moveTo(16 * scale,  brakeWidth);
                g.lineTo(16 * scale, -brakeWidth);
                g.lineTo(16 * scale + brakeLength, 0);
                g.closePath();
                g.fillPath();
            }
        }

        /**
         * Helper: draws the ship polygon with optional fill and/or stroke.
         *
         * Extracted to avoid repeating the moveTo/lineTo loop 3 times
         * (once per visual layer). DRY principle in action.
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

        /**
         * Draws a line showing the ship's actual movement direction.
         *
         * This is the VELOCITY VECTOR indicator from the GDD (Section 5.2).
         * It's a critical element for space physics games because the ship's
         * facing direction is often very different from its movement direction.
         *
         * Visual: A small arrow extending from the ship in the direction of
         * its velocity vector. Length is proportional to speed. Color changes
         * based on the "drift angle" (difference between facing and velocity).
         *
         * Why this matters for gameplay:
         * Without this indicator, players can't tell where they're drifting.
         * With it, they can see "I'm facing north but moving northeast" and
         * intuitively understand they need to thrust to correct their course.
         */
        drawVelocityIndicator() {
            const g = this.velIndicator;
            g.clear();

            // Don't show when barely moving (avoids jittery arrow at near-zero velocity)
            if (this.ship.speed < 5) return;

            // Direction of actual movement
            const velAngle = Math.atan2(this.ship.vy, this.ship.vx);

            // Offset the start point outward from the ship center so the
            // indicator clears the ship body and is always visible. The ship's
            // widest point is ~18px from center (wing tips at scale 1.5), so
            // 25px gives comfortable clearance.
            const startOffset = 25;
            const startX = this.ship.x + Math.cos(velAngle) * startOffset;
            const startY = this.ship.y + Math.sin(velAngle) * startOffset;

            // Arrow length scales with speed, capped for visual clarity.
            // At max speed, the arrow extends 55px from the start point.
            // Combined with the 25px offset, the total reach from ship center
            // is up to 80px, making it clearly visible at all speeds.
            const indicatorLength = Math.min(55, this.ship.speed / MAX_SPEED * 55);

            // Calculate drift angle: difference between facing and velocity.
            // Normalize to [-PI, PI] range for proper comparison.
            let driftAngle = velAngle - this.ship.rotation;
            while (driftAngle > Math.PI) driftAngle -= 2 * Math.PI;
            while (driftAngle < -Math.PI) driftAngle += 2 * Math.PI;

            // Color based on drift severity:
            // Small drift (< 30 degrees): green (you're going where you're pointing)
            // Medium drift (30-90 degrees): yellow (noticeable sideways drift)
            // Large drift (> 90 degrees): red (going opposite to where you're facing!)
            const absDrift = Math.abs(driftAngle);
            let color;
            if (absDrift < Math.PI / 6) {
                color = 0x40e850;  // Green: aligned
            } else if (absDrift < Math.PI / 2) {
                color = 0xe8d040;  // Yellow: drifting
            } else {
                color = 0xe84040;  // Red: severe drift
            }

            // Draw from the offset start point outward
            const endX = startX + Math.cos(velAngle) * indicatorLength;
            const endY = startY + Math.sin(velAngle) * indicatorLength;

            // Main line
            g.lineStyle(2, color, 0.7);
            g.beginPath();
            g.moveTo(startX, startY);
            g.lineTo(endX, endY);
            g.strokePath();

            // Arrowhead (small triangle at the tip)
            const headLength = 6;
            const headAngle = 0.5; // radians (~28 degrees)

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

        /**
         * Updates the telemetry display with real-time physics data.
         *
         * This is a debugging/learning tool, not part of the final HUD.
         * It shows the raw numbers behind the physics so you can see exactly
         * what's happening: velocity components, speed, rotation, thrust
         * multiplier, and how the diminishing returns formula affects thrust.
         */
        updateTelemetry(input) {
            const ship = this.ship;
            const speedRatio = ship.speed / MAX_SPEED;
            const thrustMult = Math.max(0, 1 - speedRatio * speedRatio);

            // Convert rotation to degrees for human readability
            const rotDeg = ((ship.rotation * 180 / Math.PI) % 360 + 360) % 360;

            // Velocity direction in degrees
            const velDeg = ship.speed > 1
                ? ((Math.atan2(ship.vy, ship.vx) * 180 / Math.PI) % 360 + 360) % 360
                : 0;

            // Drift angle
            let driftDeg = velDeg - rotDeg;
            if (driftDeg > 180) driftDeg -= 360;
            if (driftDeg < -180) driftDeg += 360;

            this.telemetryText.setText([
                `Position:  (${ship.x.toFixed(0)}, ${ship.y.toFixed(0)})`,
                `Velocity:  (${ship.vx.toFixed(1)}, ${ship.vy.toFixed(1)})`,
                `Speed:     ${ship.speed.toFixed(1)} / ${MAX_SPEED} px/s`,
                `Facing:    ${rotDeg.toFixed(1)} deg`,
                `Moving:    ${velDeg.toFixed(1)} deg`,
                `Drift:     ${driftDeg.toFixed(1)} deg`,
                `Thrust x:  ${(thrustMult * 100).toFixed(0)}%`,
            ].join('\n'));

            // Show active inputs on the right side.
            // We display game-specific names (THRUST/BRAKE) here since this
            // is Stellar Circuit code, even though InputManager uses generic
            // names (up/down). This is the interpretation layer.
            const activeInputs = [];
            if (input.up) activeInputs.push('THRUST');
            if (input.down) activeInputs.push('BRAKE');
            if (input.left) activeInputs.push('LEFT');
            if (input.right) activeInputs.push('RIGHT');

            this.inputText.setText(
                activeInputs.length > 0
                    ? 'Active: ' + activeInputs.join(' + ')
                    : 'No input'
            );
        }
    }

    // -----------------------------------------------------------------------
    // Create Game Instance
    // -----------------------------------------------------------------------

    // Use Shell.createConfig() instead of hardcoding the config.
    // We only need to provide the scene list; everything else uses defaults.
    const config = Shell.createConfig({
        scene: [InputTestScene],
    });

    const game = new Phaser.Game(config);

    // Store reference for debugging
    PlatPursuit.Games.Driver.gameInstance = game;

    console.log('[Stellar Circuit] Game instance created via Shell.createConfig()');
})();
