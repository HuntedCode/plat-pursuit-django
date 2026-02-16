/**
 * Stellar Circuit - Entry Point
 *
 * This is the last script loaded. It creates the Phaser game instance,
 * registers all scenes, and starts the game.
 *
 * Step 3 Version: Track generation test scene. Generates a procedural
 * track from a seed, renders it with neon edges, places the ship at
 * the start line, and lets you fly around with on/off-track drag.
 *
 * What's new from Step 2:
 * - TrackGenerator creates full track geometry from a seed string
 * - TrackRenderer draws the track to a RenderTexture (drawn once, not per-frame)
 * - Camera follows the ship with smooth lerp (world is larger than screen)
 * - On/off-track boundary detection changes drag coefficient
 * - ESC generates a new random track
 * - Minimap shows track overview in the corner
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
    const TrackGen = PlatPursuit.Games.Driver.TrackGenerator;

    // -----------------------------------------------------------------------
    // Physics Constants
    // -----------------------------------------------------------------------
    //
    // Same values from Step 2, with the addition of off-track drag.
    // These will move to the Ship class in Step 4.

    const ROTATION_SPEED = 4.0;       // rad/s
    const THRUST_FORCE = 500;         // px/s^2
    const BRAKE_FORCE = THRUST_FORCE * 0.6;
    const DRAG_ON_TRACK = 0.015;      // Very low: space feel
    const DRAG_OFF_TRACK = 0.08;      // ~5x on-track: penalty for leaving track
    const MAX_SPEED = 650;            // px/s (soft cap)
    const MIN_SPEED = 2;             // Below this, snap to zero

    // Camera follow smoothing factor.
    // 0 = camera doesn't move, 1 = camera instantly snaps to ship.
    // 0.08 gives a smooth trailing feel: the camera takes about 12 frames
    // (~200ms at 60fps) to catch up to the ship's position. This is
    // calculated as: 1 - (1-0.08)^12 = ~0.63, so after 12 frames the
    // camera has closed 63% of the gap. It never fully catches up
    // (asymptotic), which gives a natural "elastic" feel.
    const CAMERA_LERP = 0.08;

    // Ship visual scale and pre-computed vertices (avoids allocating a
    // new array every frame in drawShip). These will move to the Ship
    // class in Step 4.
    const SHIP_SCALE = 1.5;
    const SHIP_VERTICES = [
        { x:  16 * SHIP_SCALE, y:   0 },
        { x: -12 * SHIP_SCALE, y:  10 * SHIP_SCALE },
        { x:  -6 * SHIP_SCALE, y:   4 * SHIP_SCALE },
        { x:  -6 * SHIP_SCALE, y:  -4 * SHIP_SCALE },
        { x: -12 * SHIP_SCALE, y: -10 * SHIP_SCALE },
    ];

    // -----------------------------------------------------------------------
    // Step 3 Test Scene: Track Generation
    // -----------------------------------------------------------------------

    /**
     * TrackTestScene: Generates and renders a procedural track, then lets
     * you fly the ship around it with camera follow and on/off-track drag.
     *
     * What it demonstrates:
     * 1. SeededRandom produces deterministic tracks (same seed = same track)
     * 2. Track geometry: control points, splines, curvature, width variation
     * 3. RenderTexture: track drawn once, displayed as static sprite
     * 4. isOnTrack() boundary test: different drag on/off track
     * 5. Smooth camera follow with lerp
     * 6. Minimap with player position dot
     *
     * Controls:
     *   WASD / Arrows: Fly the ship (same as Step 2)
     *   ESC: Generate a new random track
     */
    class TrackTestScene extends Phaser.Scene {
        constructor() {
            super({ key: 'TrackTestScene' });
        }

        create() {
            // Remove the HTML loading indicator
            const loadingEl = document.getElementById('game-loading');
            if (loadingEl) loadingEl.remove();

            // ---------------------------------------------------------------
            // Generate Initial Track
            // ---------------------------------------------------------------
            // Use today's date as the default seed. This means everyone who
            // opens the game today sees the same track (daily challenge concept).
            this.currentSeed = new Date().toISOString().slice(0, 10);
            this.buildTrack(this.currentSeed);

            // ---------------------------------------------------------------
            // Input Manager
            // ---------------------------------------------------------------
            this.inputManager = new InputManager(this);

            // Clean up InputManager on both shutdown (scene stops but stays
            // resident) and destroy (scene fully removed from memory, e.g.,
            // game.destroy()). Without the destroy listener, keyboard event
            // handlers could leak if the game is hard-destroyed.
            const cleanup = () => {
                this.inputManager.destroy();
            };
            this.events.on('shutdown', cleanup);
            this.events.on('destroy', cleanup);

            console.log('[Stellar Circuit] Step 3 track test scene created');
            console.log(`[Stellar Circuit] Track seed: "${this.currentSeed}"`);
            console.log('[Stellar Circuit] Press ESC to generate a new random track');
        }

        /**
         * Generates a track from a seed and sets up all scene objects.
         *
         * This is extracted as a method so ESC can call it to regenerate.
         * It destroys any existing track objects first, then creates fresh
         * ones from the new seed.
         *
         * @param {string} seed - The track seed string
         */
        buildTrack(seed) {
            // Clean up previous track objects if they exist.
            // This allows re-calling buildTrack() for regeneration.
            if (this.trackGraphics) this.trackGraphics.destroy();
            if (this.velIndicator) this.velIndicator.destroy();
            if (this.shipGraphics) this.shipGraphics.destroy();
            if (this.minimap) this.minimap.destroy();
            if (this.minimapDot) this.minimapDot.destroy();
            if (this.hudContainer) this.hudContainer.destroy();

            // ----- Step 1: Generate Track Data -----
            this.trackData = TrackGen.generate(seed);
            const td = this.trackData;

            console.log(`[Stellar Circuit] Track generated:`,
                `${td.totalSamples} samples,`,
                `${td.checkpoints.length} checkpoints,`,
                `${td.boostPads.length} boost pads,`,
                `arc length: ${td.totalArcLength.toFixed(0)}px`
            );

            // ----- Step 2: Set World Bounds -----
            // Tell Phaser's camera system how big the world is.
            // The camera will be constrained to these bounds.
            const bounds = td.bounds;
            this.cameras.main.setBounds(
                bounds.minX, bounds.minY,
                bounds.maxX - bounds.minX,
                bounds.maxY - bounds.minY
            );

            // Set the background color for the entire world area
            this.cameras.main.setBackgroundColor('#0a0a14');

            // ----- Step 3: Render Track -----
            // This creates a Graphics object with all track visuals drawn
            // in world coordinates. It must be created FIRST so it renders
            // behind the ship (Phaser creation order = render order).
            this.trackGraphics = TrackGen.renderTrack(this, td);

            // ----- Step 4: Create Ship Graphics -----
            // Velocity indicator first (renders behind ship body)
            this.velIndicator = this.add.graphics();
            // Ship graphics on top
            this.shipGraphics = this.add.graphics();

            // ----- Step 5: Initialize Ship State -----
            this.ship = {
                x: td.startPosition.x,
                y: td.startPosition.y,
                vx: 0,
                vy: 0,
                rotation: td.startAngle,
                speed: 0,
                onTrack: true,
            };

            // ----- Step 6: Set Up Camera -----
            // Start the camera centered on the ship. After this, the
            // update loop handles smooth follow.
            this.cameras.main.scrollX = this.ship.x - Shell.DESIGN_WIDTH / 2;
            this.cameras.main.scrollY = this.ship.y - Shell.DESIGN_HEIGHT / 2;

            // ----- Step 7: Create HUD (fixed to camera, not world) -----
            // HUD elements need to stay on screen regardless of camera
            // position. We create a separate container and use
            // setScrollFactor(0) which makes it ignore camera scrolling.
            this.createHUD(seed);
        }

        /**
         * Creates the HUD overlay: title, telemetry, minimap.
         *
         * setScrollFactor(0) is the key concept here: it tells Phaser
         * "this object should NOT move when the camera scrolls." A scroll
         * factor of 1 (default) means the object moves 1:1 with the world.
         * A factor of 0 means it's pinned to the screen (like a HUD).
         * Values in between create parallax effects (used for starfield later).
         *
         * @param {string} seed - Current seed (displayed in HUD)
         */
        createHUD(seed) {
            const { DESIGN_WIDTH, DESIGN_HEIGHT } = Shell;

            // Container for all HUD elements. Setting scrollFactor on
            // the container applies to all children.
            this.hudContainer = this.add.container(0, 0);
            this.hudContainer.setScrollFactor(0);

            // Title
            const title = this.add.text(DESIGN_WIDTH / 2, 12, 'S T E L L A R   C I R C U I T', {
                fontFamily: 'Poppins, sans-serif',
                fontSize: '20px',
                fontStyle: 'bold',
                color: '#2ce8f5',
            }).setOrigin(0.5, 0);

            // Step + seed info
            const seedInfo = this.add.text(DESIGN_WIDTH / 2, 36, `Step 3: Track Generator  |  Seed: "${seed}"`, {
                fontFamily: 'Inter, sans-serif',
                fontSize: '13px',
                color: '#40e850',
            }).setOrigin(0.5, 0);

            // Controls
            const controlsText = this.add.text(12, 60, [
                'W/Up: Thrust    A/Left: Rotate Left',
                'S/Down: Brake   D/Right: Rotate Right',
                'ESC: New Random Track',
            ].join('\n'), {
                fontFamily: 'Inter, sans-serif',
                fontSize: '11px',
                color: '#6b6b8d',
                lineSpacing: 3,
            });

            // Telemetry (bottom-left, updated each frame)
            this.telemetryText = this.add.text(12, DESIGN_HEIGHT - 12, '', {
                fontFamily: 'monospace',
                fontSize: '11px',
                color: '#6b6b8d',
                lineSpacing: 2,
            }).setOrigin(0, 1);

            // Input display (bottom-right, updated each frame)
            this.inputText = this.add.text(DESIGN_WIDTH - 12, DESIGN_HEIGHT - 12, '', {
                fontFamily: 'monospace',
                fontSize: '11px',
                color: '#6b6b8d',
                align: 'right',
            }).setOrigin(1, 1);

            // Add all to the HUD container
            this.hudContainer.add([title, seedInfo, controlsText, this.telemetryText, this.inputText]);

            // ----- Minimap -----
            // The minimap is a separate Graphics object with scrollFactor(0).
            // We don't put it in the container because the renderMinimap
            // function creates its own Graphics object.
            const minimapSize = 140;
            const minimapX = DESIGN_WIDTH - minimapSize - 12;
            const minimapY = 12;

            const minimapResult = TrackGen.renderMinimap(
                this, this.trackData, minimapX, minimapY, minimapSize, minimapSize
            );
            this.minimap = minimapResult.graphics;
            this.minimapMapData = minimapResult.mapData;
            this.minimap.setScrollFactor(0);

            // Player dot on the minimap (updated each frame)
            this.minimapDot = this.add.graphics();
            this.minimapDot.setScrollFactor(0);
        }

        /**
         * Game loop: physics, rendering, camera follow.
         */
        update(time, delta) {
            const dt = delta / 1000;
            const input = this.inputManager.getState();

            // ---------------------------------------------------------------
            // ESC: Generate New Track
            // ---------------------------------------------------------------
            if (this.inputManager.isPausePressed()) {
                // Generate a random seed by combining timestamp with a counter.
                // This ensures each press gives a different track.
                this.currentSeed = 'random-' + Date.now();
                this.buildTrack(this.currentSeed);
                console.log(`[Stellar Circuit] New track: "${this.currentSeed}"`);
                return; // Skip this frame (everything was just rebuilt)
            }

            // ---------------------------------------------------------------
            // Physics Update (same as Step 2, with on/off-track drag)
            // ---------------------------------------------------------------

            // 1. ROTATION
            if (input.left)  this.ship.rotation -= ROTATION_SPEED * dt;
            if (input.right) this.ship.rotation += ROTATION_SPEED * dt;

            // 2. THRUST
            if (input.up) {
                const speedRatio = this.ship.speed / MAX_SPEED;
                const thrustMultiplier = Math.max(0, 1 - speedRatio * speedRatio);
                this.ship.vx += Math.cos(this.ship.rotation) * THRUST_FORCE * thrustMultiplier * dt;
                this.ship.vy += Math.sin(this.ship.rotation) * THRUST_FORCE * thrustMultiplier * dt;
            }

            // 3. BRAKE
            if (input.down && this.ship.speed > 1) {
                const velAngle = Math.atan2(this.ship.vy, this.ship.vx);
                const clampedBrake = Math.min(BRAKE_FORCE * dt, this.ship.speed);
                this.ship.vx -= Math.cos(velAngle) * clampedBrake;
                this.ship.vy -= Math.sin(velAngle) * clampedBrake;
            }

            // 4. ON/OFF-TRACK BOUNDARY TEST
            // This is the new part from Step 3. We check the ship's position
            // against the track geometry every frame to determine which drag
            // coefficient to apply.
            const trackInfo = TrackGen.isOnTrack(
                this.ship.x, this.ship.y, this.trackData
            );
            this.ship.onTrack = trackInfo.onTrack;

            // 5. DRAG (now varies based on track position)
            // On-track: very low drag, space feel preserved.
            // Off-track: heavy drag, the ship slows rapidly. This is the
            // penalty for missing the track (no hard walls in space).
            //
            // Frame-rate independence: multiplicative drag must use
            // exponential decay scaled by dt. Without this, the ship
            // would lose more speed at lower frame rates (unfair for
            // competitive leaderboards). Math.pow(1 - drag, dt * 60)
            // normalizes the decay to a 60fps baseline.
            const drag = this.ship.onTrack ? DRAG_ON_TRACK : DRAG_OFF_TRACK;
            const dragFactor = Math.pow(1 - drag, dt * 60);
            this.ship.vx *= dragFactor;
            this.ship.vy *= dragFactor;

            // 6. UPDATE POSITION
            this.ship.x += this.ship.vx * dt;
            this.ship.y += this.ship.vy * dt;

            // 7. CACHE SPEED + snap-to-zero
            this.ship.speed = Math.sqrt(
                this.ship.vx * this.ship.vx + this.ship.vy * this.ship.vy
            );
            if (this.ship.speed < MIN_SPEED) {
                this.ship.vx = 0;
                this.ship.vy = 0;
                this.ship.speed = 0;
            }

            // ---------------------------------------------------------------
            // Camera Follow (smooth lerp)
            // ---------------------------------------------------------------
            // Instead of hard-locking the camera to the ship, we interpolate
            // (lerp) toward the ship's position each frame. This creates a
            // smooth trailing effect where the camera gently follows.
            //
            // The formula: camera = camera + (target - camera) * lerpFactor
            //
            // This is "exponential ease-out": the camera moves quickly when
            // far from the target and slows as it approaches. The ship is
            // always slightly ahead of screen center, which feels natural
            // when driving at speed.
            //
            // Frame-rate independence: like drag, the lerp factor must be
            // dt-corrected so the camera follow speed is consistent across
            // frame rates. 1 - Math.pow(1 - LERP, dt * 60) gives the
            // same catch-up rate whether running at 30fps or 144fps.
            const cam = this.cameras.main;
            const targetX = this.ship.x - Shell.DESIGN_WIDTH / 2;
            const targetY = this.ship.y - Shell.DESIGN_HEIGHT / 2;
            const lerpFactor = 1 - Math.pow(1 - CAMERA_LERP, dt * 60);
            cam.scrollX += (targetX - cam.scrollX) * lerpFactor;
            cam.scrollY += (targetY - cam.scrollY) * lerpFactor;

            // ---------------------------------------------------------------
            // Rendering
            // ---------------------------------------------------------------
            this.drawShip(input);
            this.drawVelocityIndicator();
            this.updateMinimapDot();
            this.updateTelemetry(input, trackInfo);
        }

        // ===================================================================
        // Ship Drawing (same as Step 2, carried forward)
        // ===================================================================

        drawShip(input) {
            const g = this.shipGraphics;
            g.clear();
            g.setPosition(this.ship.x, this.ship.y);
            g.setRotation(this.ship.rotation);

            const s = SHIP_SCALE;

            // Glow layer
            this.drawShipPolygon(g, SHIP_VERTICES, null, { width: 3, color: 0x2ce8f5, alpha: 0.3 });
            // Body fill: tint slightly red when off-track as visual feedback
            const bodyColor = this.ship.onTrack ? 0xe8e8ff : 0xffcccc;
            this.drawShipPolygon(g, SHIP_VERTICES, { color: bodyColor, alpha: 0.9 }, null);
            // Edge stroke: changes color when off-track
            const edgeColor = this.ship.onTrack ? 0x2ce8f5 : 0xe84040;
            this.drawShipPolygon(g, SHIP_VERTICES, null, { width: 2, color: edgeColor, alpha: 1.0 });

            // Thrust flame
            if (input.up) {
                const flameLength = (8 + Math.random() * 12) * s;
                const flameWidth = 4 * s;
                g.fillStyle(0x2ce8f5, 0.6);
                g.beginPath();
                g.moveTo(-6 * s,  flameWidth);
                g.lineTo(-6 * s, -flameWidth);
                g.lineTo(-6 * s - flameLength, 0);
                g.closePath();
                g.fillPath();

                g.fillStyle(0xffffff, 0.4);
                g.beginPath();
                g.moveTo(-6 * s,  flameWidth * 0.5);
                g.lineTo(-6 * s, -flameWidth * 0.5);
                g.lineTo(-6 * s - flameLength * 0.6, 0);
                g.closePath();
                g.fillPath();
            }

            // Brake indicator
            if (input.down && this.ship.speed > 1) {
                const brakeLength = (4 + Math.random() * 6) * s;
                const brakeWidth = 2.5 * s;
                g.fillStyle(0xf5602c, 0.5);
                g.beginPath();
                g.moveTo(16 * s,  brakeWidth);
                g.lineTo(16 * s, -brakeWidth);
                g.lineTo(16 * s + brakeLength, 0);
                g.closePath();
                g.fillPath();
            }
        }

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
        // Velocity Indicator (same as Step 2)
        // ===================================================================

        drawVelocityIndicator() {
            const g = this.velIndicator;
            g.clear();
            if (this.ship.speed < 5) return;

            const velAngle = Math.atan2(this.ship.vy, this.ship.vx);
            const startOffset = 25;
            const startX = this.ship.x + Math.cos(velAngle) * startOffset;
            const startY = this.ship.y + Math.sin(velAngle) * startOffset;
            const indicatorLength = Math.min(55, this.ship.speed / MAX_SPEED * 55);

            let driftAngle = velAngle - this.ship.rotation;
            while (driftAngle > Math.PI) driftAngle -= 2 * Math.PI;
            while (driftAngle < -Math.PI) driftAngle += 2 * Math.PI;

            const absDrift = Math.abs(driftAngle);
            let color;
            if (absDrift < Math.PI / 6) {
                color = 0x40e850;
            } else if (absDrift < Math.PI / 2) {
                color = 0xe8d040;
            } else {
                color = 0xe84040;
            }

            const endX = startX + Math.cos(velAngle) * indicatorLength;
            const endY = startY + Math.sin(velAngle) * indicatorLength;

            g.lineStyle(2, color, 0.7);
            g.beginPath();
            g.moveTo(startX, startY);
            g.lineTo(endX, endY);
            g.strokePath();

            const headLength = 6;
            const headAngle = 0.5;
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
        // Minimap Player Dot
        // ===================================================================

        /**
         * Updates the player position dot on the minimap.
         *
         * The minimap coordinate mapping data (scale, offset) is stored
         * separately in this.minimapMapData. We use this to convert the
         * ship's world position into minimap screen coordinates.
         */
        updateMinimapDot() {
            const g = this.minimapDot;
            g.clear();

            const map = this.minimapMapData;
            if (!map) return;

            // Convert world position to minimap coordinates
            const dotX = map.centerX + (this.ship.x - map.trackCenterX) * map.mapScale;
            const dotY = map.centerY + (this.ship.y - map.trackCenterY) * map.mapScale;

            // Player dot: white, slightly larger than the start marker
            g.fillStyle(0xffffff, 1.0);
            g.fillCircle(dotX, dotY, 3);

            // If off-track, add a red ring around the dot
            if (!this.ship.onTrack) {
                g.lineStyle(1, 0xe84040, 0.8);
                g.strokeCircle(dotX, dotY, 5);
            }
        }

        // ===================================================================
        // Telemetry (updated from Step 2 to show track info)
        // ===================================================================

        updateTelemetry(input, trackInfo) {
            const ship = this.ship;
            const speedRatio = ship.speed / MAX_SPEED;
            const thrustMult = Math.max(0, 1 - speedRatio * speedRatio);

            const rotDeg = ((ship.rotation * 180 / Math.PI) % 360 + 360) % 360;
            const velDeg = ship.speed > 1
                ? ((Math.atan2(ship.vy, ship.vx) * 180 / Math.PI) % 360 + 360) % 360
                : 0;
            let driftDeg = velDeg - rotDeg;
            if (driftDeg > 180) driftDeg -= 360;
            if (driftDeg < -180) driftDeg += 360;

            // Current drag coefficient (changes with on/off track)
            const currentDrag = ship.onTrack ? DRAG_ON_TRACK : DRAG_OFF_TRACK;

            this.telemetryText.setText([
                `Position: (${ship.x.toFixed(0)}, ${ship.y.toFixed(0)})`,
                `Speed:    ${ship.speed.toFixed(1)} / ${MAX_SPEED} px/s`,
                `Drift:    ${driftDeg.toFixed(1)} deg`,
                `Thrust:   ${(thrustMult * 100).toFixed(0)}%`,
                `On Track: ${ship.onTrack ? 'YES' : 'NO'}`,
                `Drag:     ${currentDrag} (${ship.onTrack ? 'on-track' : 'off-track'})`,
                `Track Dist: ${trackInfo.distFromCenter.toFixed(1)} / ${trackInfo.halfWidth.toFixed(1)}`,
            ].join('\n'));

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

    const config = Shell.createConfig({
        scene: [TrackTestScene],
    });

    const game = new Phaser.Game(config);

    PlatPursuit.Games.Driver.gameInstance = game;

    console.log('[Stellar Circuit] Game instance created via Shell.createConfig()');
})();
