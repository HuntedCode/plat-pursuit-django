/**
 * PlatPursuit.Games.Driver.Scenes.RaceScene
 *
 * Core gameplay scene for Stellar Circuit.
 * Manages the race state machine: COUNTDOWN -> RACING -> FINISHED
 *
 * Responsibilities:
 * - Generate and render the track from seed
 * - Place ship at start position
 * - Run countdown overlay (3-2-1-GO with scale/fade tweens)
 * - Game loop: input -> physics -> checkpoints -> HUD -> camera
 * - Detect checkpoint crossings (sequential, with wrong-way warnings)
 * - Track lap completion and race finish
 * - Display finish overlay with per-lap breakdown
 *
 * Architecture:
 * - Receives config via init(data): { seed, laps, ccTier }
 * - Ship.update() runs in ALL states (keeps particles alive)
 * - Input is frozen (FROZEN_INPUT) during COUNTDOWN and FINISHED
 * - Camera uses velocity look-ahead (GDD 8.2)
 * - scene.restart() handles all restarts cleanly
 *
 * Controls:
 *   WASD / Arrows: Fly the ship
 *   1/2/3: Switch CC tier (dev testing)
 *   ENTER: Restart same track (FINISHED state only)
 *   ESC: New random track (any state)
 */

window.PlatPursuit = window.PlatPursuit || {};
window.PlatPursuit.Games = window.PlatPursuit.Games || {};
window.PlatPursuit.Games.Driver = window.PlatPursuit.Games.Driver || {};
window.PlatPursuit.Games.Driver.Scenes = window.PlatPursuit.Games.Driver.Scenes || {};

(function() {
    'use strict';

    // Aliases
    const Shell = PlatPursuit.Games.Shell;
    const InputManager = PlatPursuit.Games.Input.InputManager;
    const TrackGen = PlatPursuit.Games.Driver.TrackGenerator;
    const Ship = PlatPursuit.Games.Driver.Ship.Ship;
    const HUD = PlatPursuit.Games.Driver.HUD;

    const { DESIGN_WIDTH, DESIGN_HEIGHT } = Shell;

    // ===================================================================
    // CONSTANTS
    // ===================================================================

    /**
     * Race state machine states.
     * COUNTDOWN: Ship visible but frozen, countdown overlay playing
     * RACING: Ship controllable, timer running, checkpoints active
     * FINISHED: Ship coasts on momentum, finish overlay displayed
     */
    const RACE_STATE = {
        COUNTDOWN: 'COUNTDOWN',
        RACING: 'RACING',
        FINISHED: 'FINISHED',
    };

    // Countdown timing (GDD 4.6)
    const COUNTDOWN_STEP_MS = 800;       // Duration per number (3, 2, 1)
    const COUNTDOWN_GO_LINGER_MS = 500;  // How long "GO" stays visible

    // Camera (GDD 8.2): velocity look-ahead
    const CAMERA_SMOOTHING = 0.06;       // Lerp factor (lower = smoother trail)
    const LOOK_AHEAD_DISTANCE = 120;     // Max pixels ahead in velocity direction

    /**
     * Depth layers for RaceScene's own UI elements (countdown, warnings,
     * checkpoint/lap flashes, finish overlay). The HUD module defines its
     * own depth constants for minimap and HUD text elements.
     */
    const UI_DEPTH = {
        HUD_TEXT: 95,
        OVERLAY: 100,
    };

    /**
     * Frozen input object: passed to Ship.update() during COUNTDOWN
     * and FINISHED states so the ship renders but cannot move.
     */
    const FROZEN_INPUT = Object.freeze({
        up: false, down: false, left: false, right: false,
    });

    /** Default number of laps per race */
    const DEFAULT_LAPS = 3;

    /** How long the "WRONG WAY" warning stays visible (seconds) */
    const WRONG_WAY_DURATION = 2.0;

    /** How long the lap completion flash stays visible (seconds) */
    const LAP_FLASH_DURATION = 1.5;

    // ===================================================================
    // RACE SCENE
    // ===================================================================

    class RaceScene extends Phaser.Scene {
        constructor() {
            super({ key: 'RaceScene' });
        }

        // ---------------------------------------------------------------
        // Phaser Lifecycle: init
        // ---------------------------------------------------------------

        /**
         * Called before create(). Receives data from scene.start() or
         * scene.restart(). Sets up race configuration.
         *
         * @param {Object} data - Race configuration
         * @param {string} [data.seed] - Track seed (default: today's date)
         * @param {number} [data.laps] - Number of laps (default: 3)
         * @param {string} [data.ccTier] - CC tier name (default: '50cc')
         */
        init(data) {
            this.raceSeed = data.seed || new Date().toISOString().slice(0, 10);
            this.totalLaps = data.laps || DEFAULT_LAPS;
            this.ccTier = data.ccTier || '50cc';
        }

        // ---------------------------------------------------------------
        // Phaser Lifecycle: create
        // ---------------------------------------------------------------

        create() {
            // Remove HTML loading indicator (only exists on first load)
            const loadingEl = document.getElementById('game-loading');
            if (loadingEl) loadingEl.remove();

            // ----- Track Generation -----
            this.trackData = TrackGen.generate(this.raceSeed);
            const td = this.trackData;

            // Set camera world bounds from track extents
            const bounds = td.bounds;
            this.cameras.main.setBounds(
                bounds.minX, bounds.minY,
                bounds.maxX - bounds.minX,
                bounds.maxY - bounds.minY
            );
            this.cameras.main.setBackgroundColor('#0a0a14');

            // Render track at default depth (0)
            this.trackGraphics = TrackGen.renderTrack(this, td);

            // ----- Ship -----
            this.ship = new Ship(
                this,
                td.startPosition.x,
                td.startPosition.y,
                td.startAngle,
                {}
            );
            this.ship.setCCTier(this.ccTier);

            // Center camera on ship immediately (no lerp on first frame)
            this.cameras.main.setScroll(
                this.ship.x - DESIGN_WIDTH / 2,
                this.ship.y - DESIGN_HEIGHT / 2
            );

            // ----- Input -----
            this.inputManager = new InputManager(this);

            // ----- HUD (speed bar, timers, minimap, etc.) -----
            this.hud = new HUD(this, this.trackData);

            // ----- Race State -----
            this.raceState = RACE_STATE.COUNTDOWN;
            this.nextCheckpoint = 1;  // Ship starts ON cp 0; first target is cp 1
            this.currentLap = 0;
            this.raceTime = 0;
            this.lapTimes = [];
            this.bestLapTime = null;
            this.bestLapIndex = -1;
            this.lapStartTime = 0;
            this.allCheckpointsPassed = false;
            this.warningTimer = 0;
            this.prevShipX = this.ship.x;
            this.prevShipY = this.ship.y;

            // Pre-allocated objects reused every frame (avoid GC pressure)
            this._hudState = {};
            this._prevPos = { x: 0, y: 0 };
            this._currPos = { x: 0, y: 0 };

            // ----- Countdown -----
            this.startCountdown();

            // ----- Dev Keys: CC Tier Switching (1/2/3) -----
            const KeyCodes = Phaser.Input.Keyboard.KeyCodes;
            this.key1 = this.input.keyboard.addKey(KeyCodes.ONE, true, false);
            this.key2 = this.input.keyboard.addKey(KeyCodes.TWO, true, false);
            this.key3 = this.input.keyboard.addKey(KeyCodes.THREE, true, false);

            // ----- ENTER Key (restart same track in FINISHED state) -----
            this.enterKey = this.input.keyboard.addKey(KeyCodes.ENTER, true, false);

            // ----- Cleanup Handler -----
            this.events.once('shutdown', () => {
                if (this.ship) this.ship.destroy();
                if (this.inputManager) this.inputManager.destroy();
                if (this.hud) this.hud.destroy();
                if (this.trackGraphics) this.trackGraphics.destroy();
                if (this.warningText) this.warningText.destroy();
                this.input.keyboard.removeCapture([
                    KeyCodes.ONE, KeyCodes.TWO, KeyCodes.THREE,
                    KeyCodes.ENTER,
                ]);
            });

            console.log(`[RaceScene] Created: seed="${this.raceSeed}", laps=${this.totalLaps}, cc=${this.ccTier}`);
            console.log(`[RaceScene] Track: ${td.checkpoints.length} checkpoints, arc=${td.totalArcLength.toFixed(0)}px`);
        }

        // ---------------------------------------------------------------
        // Phaser Lifecycle: update
        // ---------------------------------------------------------------

        update(time, delta) {
            // Clamp dt to 100ms (10fps equivalent) to prevent timer
            // corruption and ship teleportation on tab-switch spikes.
            const dt = Math.min(delta / 1000, 0.1);

            // --- ESC: New track (all states) ---
            if (this.inputManager.isPausePressed()) {
                this.handleEscape();
                return;
            }

            // --- Dev: CC tier switching ---
            const JustDown = Phaser.Input.Keyboard.JustDown;
            if (JustDown(this.key1)) { this.ccTier = '50cc';  this.ship.setCCTier('50cc'); }
            if (JustDown(this.key2)) { this.ccTier = '100cc'; this.ship.setCCTier('100cc'); }
            if (JustDown(this.key3)) { this.ccTier = '200cc'; this.ship.setCCTier('200cc'); }

            // --- Capture previous position BEFORE ship update ---
            this.prevShipX = this.ship.x;
            this.prevShipY = this.ship.y;

            // --- Determine input based on state ---
            const input = (this.raceState === RACE_STATE.RACING)
                ? this.inputManager.getState()
                : FROZEN_INPUT;

            // --- Ship physics + rendering (runs in ALL states) ---
            this.ship.update(dt, input, this.trackData);

            // --- State-specific logic ---
            if (this.raceState === RACE_STATE.RACING) {
                this.raceTime += dt;
                this.checkCheckpointCrossing();
                this.updateWarning(dt);
            }

            if (this.raceState === RACE_STATE.FINISHED) {
                if (JustDown(this.enterKey)) {
                    // Restart same track
                    this.scene.restart({
                        seed: this.raceSeed,
                        laps: this.totalLaps,
                        ccTier: this.ccTier,
                    });
                    return;
                }
            }

            // --- Camera (all states) ---
            this.updateCamera(dt);

            // --- HUD (all states) ---
            const hs = this._hudState;
            hs.speed = this.ship.speed;
            hs.shipX = this.ship.x;
            hs.shipY = this.ship.y;
            hs.onTrack = this.ship.onTrack;
            hs.raceState = this.raceState;
            hs.currentLap = this.currentLap;
            hs.totalLaps = this.totalLaps;
            hs.raceTime = this.raceTime;
            hs.currentLapTime = this.raceTime - this.lapStartTime;
            hs.bestLapTime = this.bestLapTime;
            hs.nextCheckpoint = this.nextCheckpoint;
            hs.allCheckpointsPassed = this.allCheckpointsPassed;
            hs.ccTier = this.ccTier;
            this.hud.update(hs);
        }

        // ---------------------------------------------------------------
        // Countdown
        // ---------------------------------------------------------------

        /**
         * Runs the 3-2-1-GO countdown sequence using delayed calls
         * and tweens. Ship is visible but frozen during countdown.
         *
         * Each number scales from 1.5x down to 1x while fading out.
         * "GO" uses green color and a larger initial scale.
         */
        startCountdown() {
            this.countdownText = this.add.text(
                DESIGN_WIDTH / 2, DESIGN_HEIGHT / 2, '', {
                    fontFamily: 'Poppins, sans-serif',
                    fontSize: '120px',
                    fontStyle: '700',
                    color: '#2ce8f5',
                }
            ).setOrigin(0.5).setScrollFactor(0).setDepth(UI_DEPTH.OVERLAY).setAlpha(0);

            // Screen flash overlay (brief white flash on each beat)
            this.flashOverlay = this.add.graphics();
            this.flashOverlay.setScrollFactor(0);
            this.flashOverlay.setDepth(UI_DEPTH.OVERLAY - 1);
            this.flashOverlay.setAlpha(0);

            const steps = [
                { text: '3', color: '#ffffff', scale: 1.5 },
                { text: '2', color: '#ffffff', scale: 1.5 },
                { text: '1', color: '#ffffff', scale: 1.5 },
                { text: 'GO', color: '#40e850', scale: 2.0 },
            ];

            let delay = 0;

            for (let i = 0; i < steps.length; i++) {
                const step = steps[i];
                const isGo = step.text === 'GO';
                const duration = isGo ? COUNTDOWN_GO_LINGER_MS : COUNTDOWN_STEP_MS;

                this.time.delayedCall(delay, () => {
                    // Kill any lingering tweens from the previous step.
                    // Without this, the previous fade-out tween fights the
                    // new alpha: 1, making the text invisible for steps 2/1/GO.
                    this.tweens.killTweensOf(this.countdownText);

                    // Set text content and style
                    this.countdownText.setText(step.text);
                    this.countdownText.setColor(step.color);
                    this.countdownText.setAlpha(1);
                    this.countdownText.setScale(step.scale);

                    // Scale down to 1.0
                    this.tweens.add({
                        targets: this.countdownText,
                        scaleX: 1.0,
                        scaleY: 1.0,
                        duration: duration * 0.5,
                        ease: 'Power2',
                    });

                    // Fade out
                    this.tweens.add({
                        targets: this.countdownText,
                        alpha: 0,
                        delay: duration * 0.6,
                        duration: duration * 0.4,
                        ease: 'Linear',
                    });

                    // Brief screen flash
                    this.flashOverlay.clear();
                    this.flashOverlay.fillStyle(0xffffff, 0.05);
                    this.flashOverlay.fillRect(0, 0, DESIGN_WIDTH, DESIGN_HEIGHT);
                    this.flashOverlay.setAlpha(1);
                    this.tweens.add({
                        targets: this.flashOverlay,
                        alpha: 0,
                        duration: 150,
                        ease: 'Linear',
                    });
                });

                delay += duration;
            }

            // Transition to RACING after countdown completes
            this.time.delayedCall(delay, () => {
                this.raceState = RACE_STATE.RACING;
                if (this.countdownText) {
                    this.countdownText.destroy();
                    this.countdownText = null;
                }
                if (this.flashOverlay) {
                    this.flashOverlay.destroy();
                    this.flashOverlay = null;
                }
            });
        }

        // ---------------------------------------------------------------
        // Checkpoint Detection
        // ---------------------------------------------------------------

        /**
         * Tests the ship's movement ray (prevPosition -> currentPosition)
         * against all checkpoint gate segments.
         *
         * For each crossing detected:
         * - Backward crossings (dot product <= 0): show "WRONG WAY"
         * - Correct next checkpoint: advances the sequence
         * - Skipped checkpoint (forward but not next): show "MISSED CHECKPOINT"
         */
        checkCheckpointCrossing() {
            const checkpoints = this.trackData.checkpoints;
            const segInt = TrackGen.segmentIntersection;

            const prevPos = this._prevPos;
            const currPos = this._currPos;
            prevPos.x = this.prevShipX;
            prevPos.y = this.prevShipY;
            currPos.x = this.ship.x;
            currPos.y = this.ship.y;

            for (let i = 0; i < checkpoints.length; i++) {
                const cp = checkpoints[i];
                const hit = segInt(prevPos, currPos, cp.leftPoint, cp.rightPoint);

                if (!hit) continue;

                // Direction check: dot product of movement against
                // checkpoint direction. Positive = correct direction.
                const moveX = currPos.x - prevPos.x;
                const moveY = currPos.y - prevPos.y;
                const dot = moveX * cp.direction.x + moveY * cp.direction.y;

                if (dot <= 0) {
                    // Crossed a gate backwards: traveling the wrong way
                    this.showWarning('WRONG WAY');
                    continue;
                }

                if (i === this.nextCheckpoint) {
                    this.onCheckpointCrossed(i);
                } else if (i < this.nextCheckpoint && i !== 0) {
                    // Crossed a checkpoint behind the current target: skipped it
                    this.showWarning('MISSED CHECKPOINT');
                }
                // Checkpoints ahead of nextCheckpoint or cp 0 mid-lap:
                // ignore silently (player hasn't reached them yet)
            }
        }

        /**
         * Handles a correctly-crossed checkpoint.
         *
         * When all intermediate checkpoints (1 through N-1) are crossed,
         * `allCheckpointsPassed` flips to true and `nextCheckpoint` wraps
         * to 0 (start/finish line). Crossing checkpoint 0 with that flag
         * set completes a lap.
         *
         * @param {number} index - The checkpoint index that was crossed
         */
        onCheckpointCrossed(index) {
            const checkpoints = this.trackData.checkpoints;

            // Current lap elapsed time (used for split tracking)
            const currentLapTime = this.raceTime - this.lapStartTime;

            // Notify HUD for minimap checkpoint dot tracking + split times
            this.hud.onCheckpointCrossed(index, currentLapTime);

            if (index === 0) {
                // Start/finish line
                if (this.allCheckpointsPassed) {
                    // Lap complete
                    this.currentLap++;
                    const lapTime = this.raceTime - this.lapStartTime;

                    // Determine best BEFORE pushing (otherwise isBest is always true)
                    const isBest = this.bestLapTime === null || lapTime <= this.bestLapTime;
                    if (isBest) {
                        this.bestLapTime = lapTime;
                        this.bestLapIndex = this.lapTimes.length; // index of this lap
                    }

                    this.lapTimes.push(lapTime);
                    this.lapStartTime = this.raceTime;
                    this.allCheckpointsPassed = false;

                    // Notify HUD of lap completion (triggers animation, clears CP state)
                    this.hud.onLapComplete(this.currentLap, lapTime, isBest);

                    // Show lap completion flash
                    this.showLapFlash(this.currentLap, lapTime);

                    if (this.currentLap >= this.totalLaps) {
                        this.onRaceFinished();
                        return;
                    }

                    // Reset for next lap
                    this.nextCheckpoint = 1;
                }
                // If not all checkpoints passed, ignore crossing
            } else {
                // Regular checkpoint: show brief flash
                this.showCheckpointFlash(index);

                // Advance to next
                this.nextCheckpoint = index + 1;

                if (this.nextCheckpoint >= checkpoints.length) {
                    // All intermediate checkpoints crossed
                    this.nextCheckpoint = 0;
                    this.allCheckpointsPassed = true;
                }
            }
        }

        // ---------------------------------------------------------------
        // Visual Feedback
        // ---------------------------------------------------------------

        /**
         * Brief green flash when crossing a checkpoint correctly.
         * Shows "CP X" briefly near the top of the screen.
         */
        showCheckpointFlash(index) {
            const cpFlash = this.add.text(
                DESIGN_WIDTH / 2, 105, `CP ${index}`, {
                    fontFamily: 'Poppins, sans-serif',
                    fontSize: '18px',
                    fontStyle: '700',
                    color: '#40e850',
                }
            ).setOrigin(0.5).setScrollFactor(0).setDepth(UI_DEPTH.HUD_TEXT);

            // Fade out and destroy
            this.tweens.add({
                targets: cpFlash,
                alpha: 0,
                y: 95,
                duration: 500,
                ease: 'Power2',
                onComplete: () => cpFlash.destroy(),
            });
        }

        /**
         * Shows "Lap X: M:SS.mmm" centered on screen for 1.5 seconds.
         * Uses the ship's trail color for visual consistency.
         */
        showLapFlash(lapNumber, lapTime) {
            // Convert ship's trail color to CSS hex
            const trailHex = '#' + this.ship.config.trailColor.toString(16).padStart(6, '0');

            const lapFlash = this.add.text(
                DESIGN_WIDTH / 2, DESIGN_HEIGHT / 2 - 100,
                `Lap ${lapNumber}: ${this.formatTime(lapTime)}`, {
                    fontFamily: 'monospace',
                    fontSize: '28px',
                    fontStyle: 'bold',
                    color: trailHex,
                }
            ).setOrigin(0.5).setScrollFactor(0).setDepth(UI_DEPTH.OVERLAY);

            // Hold briefly, then fade out
            this.tweens.add({
                targets: lapFlash,
                alpha: 0,
                delay: LAP_FLASH_DURATION * 1000 * 0.6,
                duration: LAP_FLASH_DURATION * 1000 * 0.4,
                ease: 'Linear',
                onComplete: () => lapFlash.destroy(),
            });
        }

        /**
         * Shows a warning message (e.g. "WRONG WAY", "MISSED CHECKPOINT")
         * for WARNING_DURATION seconds. Reuses a single text object.
         *
         * @param {string} message - The warning text to display
         */
        showWarning(message) {
            this.warningTimer = WRONG_WAY_DURATION;

            if (!this.warningText) {
                this.warningText = this.add.text(
                    DESIGN_WIDTH / 2, 120, '', {
                        fontFamily: 'Poppins, sans-serif',
                        fontSize: '28px',
                        fontStyle: '700',
                        color: '#e43b44',
                    }
                ).setOrigin(0.5).setScrollFactor(0).setDepth(UI_DEPTH.OVERLAY);
            }
            this.warningText.setText(message);
            this.warningText.setAlpha(1);
        }

        /**
         * Ticks down the warning timer and hides it when expired.
         */
        updateWarning(dt) {
            if (this.warningTimer > 0) {
                this.warningTimer -= dt;
                if (this.warningTimer <= 0) {
                    this.warningTimer = 0;
                    if (this.warningText) this.warningText.setAlpha(0);
                }
            }
        }

        // ---------------------------------------------------------------
        // Race Finish
        // ---------------------------------------------------------------

        /**
         * Transitions to FINISHED state. Creates a semi-transparent
         * overlay with race results: total time, per-lap breakdown,
         * and restart instructions.
         */
        onRaceFinished() {
            this.raceState = RACE_STATE.FINISHED;

            // Semi-transparent dark overlay
            const overlayBg = this.add.graphics();
            overlayBg.setScrollFactor(0);
            overlayBg.setDepth(UI_DEPTH.OVERLAY - 1);
            overlayBg.fillStyle(0x0a0a14, 0.75);
            overlayBg.fillRect(0, 0, DESIGN_WIDTH, DESIGN_HEIGHT);

            // "RACE COMPLETE" title
            this.add.text(
                DESIGN_WIDTH / 2, DESIGN_HEIGHT / 2 - 80, 'RACE COMPLETE', {
                    fontFamily: 'Poppins, sans-serif',
                    fontSize: '48px',
                    fontStyle: '700',
                    color: '#d4a017',
                }
            ).setOrigin(0.5).setScrollFactor(0).setDepth(UI_DEPTH.OVERLAY);

            // Total time
            this.add.text(
                DESIGN_WIDTH / 2, DESIGN_HEIGHT / 2 - 20, this.formatTime(this.raceTime), {
                    fontFamily: 'monospace',
                    fontSize: '36px',
                    color: '#2ce8f5',
                }
            ).setOrigin(0.5).setScrollFactor(0).setDepth(UI_DEPTH.OVERLAY);

            // Per-lap breakdown
            const lapLines = this.lapTimes.map((t, i) => {
                const marker = (i === this.bestLapIndex && this.lapTimes.length > 1)
                    ? '  *best' : '';
                return `Lap ${i + 1}: ${this.formatTime(t)}${marker}`;
            });

            this.add.text(
                DESIGN_WIDTH / 2, DESIGN_HEIGHT / 2 + 30, lapLines.join('\n'), {
                    fontFamily: 'monospace',
                    fontSize: '16px',
                    color: '#6b6b8d',
                    align: 'center',
                    lineSpacing: 6,
                }
            ).setOrigin(0.5, 0).setScrollFactor(0).setDepth(UI_DEPTH.OVERLAY);

            // Seed info
            this.add.text(
                DESIGN_WIDTH / 2, DESIGN_HEIGHT / 2 + 130,
                `Seed: "${this.raceSeed}"  |  ${this.ccTier}`, {
                    fontFamily: 'Inter, sans-serif',
                    fontSize: '12px',
                    color: '#4a5568',
                }
            ).setOrigin(0.5).setScrollFactor(0).setDepth(UI_DEPTH.OVERLAY);

            // Restart prompt
            this.add.text(
                DESIGN_WIDTH / 2, DESIGN_HEIGHT / 2 + 170,
                'ENTER: Retry  |  ESC: New Track', {
                    fontFamily: 'Inter, sans-serif',
                    fontSize: '14px',
                    color: '#6b6b8d',
                }
            ).setOrigin(0.5).setScrollFactor(0).setDepth(UI_DEPTH.OVERLAY);

            console.log(`[RaceScene] Race finished: ${this.formatTime(this.raceTime)}`);
        }

        // ---------------------------------------------------------------
        // Camera
        // ---------------------------------------------------------------

        /**
         * Velocity look-ahead camera (GDD 8.2).
         *
         * The camera target is offset from the ship position in the
         * direction of the ship's velocity. The offset magnitude is
         * proportional to speed (at max speed, offset = LOOK_AHEAD_DISTANCE).
         *
         * Frame-rate independent lerp: the smoothing factor is computed
         * as 1 - (1 - CAMERA_SMOOTHING)^(dt * 60), ensuring the camera
         * behaves identically at 30fps and 144fps.
         */
        updateCamera(dt) {
            const cam = this.cameras.main;

            // Calculate look-ahead offset from velocity
            let lookX = 0;
            let lookY = 0;
            if (this.ship.speed > 10) {
                const speedRatio = Math.min(this.ship.speed / this.ship.tier.maxSpeed, 1);
                const velAngle = Math.atan2(this.ship.vy, this.ship.vx);
                lookX = Math.cos(velAngle) * LOOK_AHEAD_DISTANCE * speedRatio;
                lookY = Math.sin(velAngle) * LOOK_AHEAD_DISTANCE * speedRatio;
            }

            const targetX = this.ship.x + lookX - DESIGN_WIDTH / 2;
            const targetY = this.ship.y + lookY - DESIGN_HEIGHT / 2;

            // Frame-rate independent lerp
            const lerpFactor = 1 - Math.pow(1 - CAMERA_SMOOTHING, dt * 60);
            cam.setScroll(
                cam.scrollX + (targetX - cam.scrollX) * lerpFactor,
                cam.scrollY + (targetY - cam.scrollY) * lerpFactor
            );
        }

        // ---------------------------------------------------------------
        // ESC Handling
        // ---------------------------------------------------------------

        /**
         * ESC restarts with a new random track in all states.
         * This is a testing convenience; will become a pause menu later.
         */
        handleEscape() {
            this.scene.restart({
                seed: 'random-' + Date.now(),
                laps: this.totalLaps,
                ccTier: this.ccTier,
            });
        }

        // ---------------------------------------------------------------
        // Utility
        // ---------------------------------------------------------------

        /**
         * Formats a time in seconds to M:SS.mmm display string.
         * @param {number} seconds - Time in seconds
         * @returns {string} Formatted time string
         */
        formatTime(seconds) {
            const mins = Math.floor(seconds / 60);
            const secs = seconds % 60;
            const wholeSecs = Math.floor(secs);
            const ms = Math.floor((secs - wholeSecs) * 1000);
            return `${mins}:${String(wholeSecs).padStart(2, '0')}.${String(ms).padStart(3, '0')}`;
        }
    }

    // ===================================================================
    // PUBLIC API
    // ===================================================================

    PlatPursuit.Games.Driver.Scenes.RaceScene = RaceScene;

    console.log('[Stellar Circuit] RaceScene loaded');
})();
