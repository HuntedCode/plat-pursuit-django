/**
 * Stellar Circuit - Entry Point
 *
 * This is the last script loaded. It creates the Phaser game instance,
 * registers all scenes, and starts the game.
 *
 * Scene registration:
 * - RaceScene (default): Core gameplay with countdown, checkpoints, laps
 * - TrackTestScene: Dev sandbox for testing physics, visuals, track gen
 *
 * RaceScene is listed first so Phaser auto-starts it. TrackTestScene
 * remains registered and accessible via the browser console:
 *   PlatPursuit.Games.Driver.gameInstance.scene.start('TrackTestScene')
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
    const Ship = PlatPursuit.Games.Driver.Ship.Ship;

    // -----------------------------------------------------------------------
    // Scene Constants
    // -----------------------------------------------------------------------

    // Camera follow smoothing factor.
    // 0 = camera doesn't move, 1 = camera instantly snaps to ship.
    // 0.08 gives a smooth trailing feel: the camera takes about 12 frames
    // (~200ms at 60fps) to catch up to the ship's position. This is
    // calculated as: 1 - (1-0.08)^12 = ~0.63, so after 12 frames the
    // camera has closed 63% of the gap. It never fully catches up
    // (asymptotic), which gives a natural "elastic" feel.
    const CAMERA_LERP = 0.08;

    // Design dimensions (from Shell, cached for readability)
    const { DESIGN_WIDTH, DESIGN_HEIGHT } = Shell;

    // -----------------------------------------------------------------------
    // Step 3/4 Test Scene: Track + Ship
    // -----------------------------------------------------------------------

    /**
     * TrackTestScene: Generates a procedural track, creates a Ship,
     * and lets you fly around with camera follow and on/off-track drag.
     *
     * What it demonstrates:
     * 1. Ship class handles its own physics and rendering
     * 2. Scene manages camera, HUD, minimap, and track lifecycle
     * 3. Clean separation: scene calls ship.update() once per frame
     *
     * Controls:
     *   WASD / Arrows: Fly the ship
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

            // ---------------------------------------------------------------
            // CC Tier Switching (number keys 1/2/3)
            // ---------------------------------------------------------------
            // These are dev/test controls for switching physics presets
            // on the fly. In the final game, CC tier is chosen in the
            // menu before the race starts.
            const KeyCodes = Phaser.Input.Keyboard.KeyCodes;
            this.key1 = this.input.keyboard.addKey(KeyCodes.ONE, true, false);
            this.key2 = this.input.keyboard.addKey(KeyCodes.TWO, true, false);
            this.key3 = this.input.keyboard.addKey(KeyCodes.THREE, true, false);

            // ---------------------------------------------------------------
            // Color Preset Switching (number keys 4-9)
            // ---------------------------------------------------------------
            // Dev/test controls for experimenting with outline/glow and
            // body color combinations. Will be removed when the skin
            // system is built.
            this.key4 = this.input.keyboard.addKey(KeyCodes.FOUR, true, false);
            this.key5 = this.input.keyboard.addKey(KeyCodes.FIVE, true, false);
            this.key6 = this.input.keyboard.addKey(KeyCodes.SIX, true, false);
            this.key7 = this.input.keyboard.addKey(KeyCodes.SEVEN, true, false);
            this.key8 = this.input.keyboard.addKey(KeyCodes.EIGHT, true, false);
            this.key9 = this.input.keyboard.addKey(KeyCodes.NINE, true, false);
            this.activeColorPreset = 'Cyan (Default)';

            // Clean up on scene shutdown (once, not on both shutdown + destroy,
            // since Phaser fires shutdown before destroy and double-cleanup
            // would attempt to destroy already-destroyed objects).
            const cleanup = () => {
                this.inputManager.destroy();
                // Release all dev key captures
                this.input.keyboard.removeCapture([
                    KeyCodes.ONE, KeyCodes.TWO, KeyCodes.THREE,
                    KeyCodes.FOUR, KeyCodes.FIVE, KeyCodes.SIX,
                    KeyCodes.SEVEN, KeyCodes.EIGHT, KeyCodes.NINE,
                ]);
            };
            this.events.once('shutdown', cleanup);
            this.events.once('destroy', cleanup);

            console.log('[Stellar Circuit] Step 4F test scene created');
            console.log(`[Stellar Circuit] Track seed: "${this.currentSeed}"`);
            console.log('[Stellar Circuit] 1/2/3: CC tier | 4-9: Color preset | ESC: New track');
        }

        /**
         * Generates a track from a seed and sets up all scene objects.
         *
         * This is extracted as a method so ESC can call it to regenerate.
         * It destroys any existing objects first, then creates fresh
         * ones from the new seed.
         *
         * @param {string} seed - The track seed string
         */
        buildTrack(seed) {
            // Clean up previous objects if they exist
            if (this.trackGraphics) this.trackGraphics.destroy();
            if (this.minimap) this.minimap.destroy();
            if (this.minimapDot) this.minimapDot.destroy();
            if (this.hudContainer) this.hudContainer.destroy();

            // ----- Step 1: Generate Track Data -----
            this.trackData = TrackGen.generate(seed);
            const td = this.trackData;

            console.log(`[Stellar Circuit] Track generated:`,
                `${td.totalSamples} samples,`,
                `${td.checkpoints.length} checkpoints,`,
                `arc length: ${td.totalArcLength.toFixed(0)}px`
            );

            // ----- Step 2: Set World Bounds -----
            const bounds = td.bounds;
            this.cameras.main.setBounds(
                bounds.minX, bounds.minY,
                bounds.maxX - bounds.minX,
                bounds.maxY - bounds.minY
            );
            this.cameras.main.setBackgroundColor('#0a0a14');

            // ----- Step 3: Render Track -----
            // Created FIRST so it renders behind the ship
            this.trackGraphics = TrackGen.renderTrack(this, td);

            // ----- Step 4: Create or Reset Ship -----
            // On first call, create the ship. On subsequent calls (ESC
            // rebuild), reuse the existing ship via reset() to avoid
            // destroying and recreating Graphics/emitter objects.
            if (this.ship) {
                this.ship.reset(td.startPosition.x, td.startPosition.y, td.startAngle);
            } else {
                this.ship = new Ship(
                    this,
                    td.startPosition.x,
                    td.startPosition.y,
                    td.startAngle,
                    {}
                );
            }

            // ----- Step 5: Set Up Camera -----
            // Start the camera centered on the ship
            this.cameras.main.setScroll(
                this.ship.x - DESIGN_WIDTH / 2,
                this.ship.y - DESIGN_HEIGHT / 2
            );

            // ----- Step 6: Create HUD -----
            this.createHUD(seed);
        }

        /**
         * Creates the HUD overlay: title, telemetry, minimap.
         *
         * setScrollFactor(0) pins elements to the screen (they don't
         * move when the camera scrolls).
         *
         * @param {string} seed - Current seed (displayed in HUD)
         */
        createHUD(seed) {
            // Container for all HUD text elements
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
            const seedInfo = this.add.text(DESIGN_WIDTH / 2, 36, `Step 4F: Particles  |  Seed: "${seed}"`, {
                fontFamily: 'Inter, sans-serif',
                fontSize: '13px',
                color: '#40e850',
            }).setOrigin(0.5, 0);

            // Controls
            const controlsText = this.add.text(12, 60, [
                'W/Up: Thrust    A/Left: Rotate Left',
                'S/Down: Brake   D/Right: Rotate Right',
                '1: 50cc  2: 100cc  3: 200cc  ESC: New Track',
                '4: Cyan  5: Orange  6: Purple  7: Green  8: Pink  9: Gold',
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
            const minimapSize = 140;
            const minimapX = DESIGN_WIDTH - minimapSize - 12;
            const minimapY = 12;

            const minimapResult = TrackGen.renderMinimap(
                this, this.trackData, minimapX, minimapY, minimapSize, minimapSize
            );
            this.minimap = minimapResult.graphics;
            this.minimapMapData = minimapResult.mapData;
            this.minimap.setScrollFactor(0);

            // Player dot on the minimap (updated each frame).
            // Lives outside hudContainer because it is cleared and redrawn
            // every frame, while the container's other elements are static.
            this.minimapDot = this.add.graphics();
            this.minimapDot.setScrollFactor(0);
        }

        /**
         * Game loop: ship update, camera follow, HUD refresh.
         */
        update(time, delta) {
            const dt = delta / 1000;
            const input = this.inputManager.getState();

            // ---------------------------------------------------------------
            // ESC: Generate New Track
            // ---------------------------------------------------------------
            if (this.inputManager.isPausePressed()) {
                this.currentSeed = 'random-' + Date.now();
                this.buildTrack(this.currentSeed);
                console.log(`[Stellar Circuit] New track: "${this.currentSeed}"`);
                return;
            }

            // ---------------------------------------------------------------
            // CC Tier Switching (1/2/3 keys)
            // ---------------------------------------------------------------
            // JustDown for edge detection: one press = one switch
            const JustDown = Phaser.Input.Keyboard.JustDown;
            if (JustDown(this.key1)) this.ship.setCCTier('50cc');
            if (JustDown(this.key2)) this.ship.setCCTier('100cc');
            if (JustDown(this.key3)) this.ship.setCCTier('200cc');

            // ---------------------------------------------------------------
            // Color Preset Switching (4-9 keys)
            // ---------------------------------------------------------------
            for (let k = 4; k <= 9; k++) {
                if (JustDown(this['key' + k])) {
                    const name = this.ship.setColorPreset(String(k));
                    if (name) this.activeColorPreset = name;
                }
            }

            // ---------------------------------------------------------------
            // Ship Update (physics + rendering in one call)
            // ---------------------------------------------------------------
            this.ship.update(dt, input, this.trackData);

            // ---------------------------------------------------------------
            // Camera Follow (smooth lerp)
            // ---------------------------------------------------------------
            // Exponential ease-out: camera moves quickly when far from the
            // ship and slows as it approaches. Frame-rate independent via
            // the same Math.pow pattern used for drag.
            const cam = this.cameras.main;
            const targetX = this.ship.x - DESIGN_WIDTH / 2;
            const targetY = this.ship.y - DESIGN_HEIGHT / 2;
            const lerpFactor = 1 - Math.pow(1 - CAMERA_LERP, dt * 60);
            const newScrollX = cam.scrollX + (targetX - cam.scrollX) * lerpFactor;
            const newScrollY = cam.scrollY + (targetY - cam.scrollY) * lerpFactor;
            cam.setScroll(newScrollX, newScrollY);

            // ---------------------------------------------------------------
            // HUD Updates
            // ---------------------------------------------------------------
            this.updateMinimapDot();
            this.updateTelemetry(input);
        }

        // ===================================================================
        // Minimap Player Dot
        // ===================================================================

        updateMinimapDot() {
            const g = this.minimapDot;
            g.clear();

            const map = this.minimapMapData;
            if (!map) return;

            // Convert world position to minimap coordinates
            const dotX = map.centerX + (this.ship.x - map.trackCenterX) * map.mapScale;
            const dotY = map.centerY + (this.ship.y - map.trackCenterY) * map.mapScale;

            // Player dot: white
            g.fillStyle(0xffffff, 1.0);
            g.fillCircle(dotX, dotY, 3);

            // Red ring when off-track
            if (!this.ship.onTrack) {
                g.lineStyle(1, 0xe84040, 0.8);
                g.strokeCircle(dotX, dotY, 5);
            }
        }

        // ===================================================================
        // Telemetry Display
        // ===================================================================

        updateTelemetry(input) {
            const ship = this.ship;
            const tier = ship.tier;
            const trackInfo = ship.trackInfo;

            const speedRatio = ship.speed / tier.maxSpeed;
            const thrustMult = Math.max(0, 1 - speedRatio * speedRatio);

            const rotDeg = ((ship.rotation * 180 / Math.PI) % 360 + 360) % 360;
            const velDeg = ship.speed > 1
                ? ((Math.atan2(ship.vy, ship.vx) * 180 / Math.PI) % 360 + 360) % 360
                : 0;
            let driftDeg = velDeg - rotDeg;
            if (driftDeg > 180) driftDeg -= 360;
            if (driftDeg < -180) driftDeg += 360;

            const currentDrag = ship.onTrack ? tier.dragOnTrack : tier.dragOffTrack;

            // Build telemetry lines. Angular velocity shown only when
            // the tier has angular momentum (100cc/200cc).
            const lines = [
                `CC Tier:  ${tier.name}  |  Skin: ${this.activeColorPreset}`,
                `Position: (${ship.x.toFixed(0)}, ${ship.y.toFixed(0)})`,
                `Speed:    ${ship.speed.toFixed(1)} / ${tier.maxSpeed} px/s`,
                `Drift:    ${driftDeg.toFixed(1)} deg`,
                `Thrust:   ${(thrustMult * 100).toFixed(0)}%`,
                `On Track: ${ship.onTrack ? 'YES' : 'NO'}`,
                `Drag:     ${currentDrag} (${ship.onTrack ? 'on-track' : 'off-track'})`,
                `Track Dist: ${trackInfo.distFromCenter.toFixed(1)} / ${trackInfo.halfWidth.toFixed(1)}`,
            ];
            if (tier.angularMomentum) {
                lines.push(`Ang. Vel: ${ship.angularVelocity.toFixed(2)} rad/s`);
            }

            this.telemetryText.setText(lines.join('\n'));

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

    // RaceScene is listed first so Phaser auto-starts it.
    // TrackTestScene stays registered for dev testing via console.
    const RaceScene = PlatPursuit.Games.Driver.Scenes.RaceScene;

    const config = Shell.createConfig({
        scene: [RaceScene, TrackTestScene],
    });

    const game = new Phaser.Game(config);

    PlatPursuit.Games.Driver.gameInstance = game;

    console.log('[Stellar Circuit] Game instance created. RaceScene starting.');
})();
