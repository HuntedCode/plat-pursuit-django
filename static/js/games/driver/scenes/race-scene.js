/**
 * PlatPursuit.Games.Driver.Scenes.RaceScene
 *
 * Core gameplay scene for Stellar Circuit.
 * Manages the race state machine: COUNTDOWN -> RACING -> FINISHED
 *
 * Supports two modes:
 * - Race (3 laps): Ghost plays full 3-lap recording.
 * - Time Trial (infinite laps): Ghost plays best single lap, resets each lap.
 *   Saves new best lap automatically. ESC exits to session summary.
 *
 * Ghost delta is checkpoint-based: computed at each checkpoint crossing as
 * playerTime - ghostTime at that same checkpoint.
 *
 * Responsibilities:
 * - Generate and render the track from seed
 * - Place ship at start position
 * - Run countdown overlay (3-2-1-GO with scale/fade tweens)
 * - Game loop: input -> physics -> checkpoints -> HUD -> camera
 * - Detect checkpoint crossings (sequential, with wrong-way warnings)
 * - Track lap completion and race finish
 * - Ghost recording, playback, and auto-save
 * - On finish: fade transition to ResultsScene with race data
 *
 * Architecture:
 * - Receives config via init(data): { seed, mode, ccTier }
 * - Ship.update() runs in ALL states (keeps particles alive)
 * - Input is frozen (FROZEN_INPUT) during COUNTDOWN and FINISHED
 * - Camera uses velocity look-ahead (GDD 8.2)
 * - ESC: Race mode -> MenuScene; TT mode -> ResultsScene (session summary)
 * - Ghost recording runs during RACING; auto-saves on new personal best
 * - Ghost playback renders a semi-transparent ship with "Personal Best" label
 *
 * Controls:
 *   WASD / Arrows: Fly the ship
 *   G: Toggle ghost visibility
 *   1/2/3: Switch CC tier (dev testing)
 *   ESC: Return to menu (race) or show session summary (TT)
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
    const ShipModule = PlatPursuit.Games.Driver.Ship;
    const Ship = ShipModule.Ship;
    const HUD = PlatPursuit.Games.Driver.HUD;
    const UI = PlatPursuit.Games.Driver.UI;
    const Ghost = PlatPursuit.Games.Driver.Ghost;

    const { DESIGN_WIDTH, DESIGN_HEIGHT } = Shell;
    const { RACE_STATE, DEPTH, CSS, formatTime } = UI;

    // ===================================================================
    // CONSTANTS
    // ===================================================================

    // Countdown timing (GDD 4.6)
    const COUNTDOWN_STEP_MS = 800;       // Duration per number (3, 2, 1)
    const COUNTDOWN_GO_LINGER_MS = 500;  // How long "GO" stays visible

    // Camera (GDD 8.2): velocity look-ahead
    const CAMERA_SMOOTHING = 0.06;       // Lerp factor (lower = smoother trail)
    const LOOK_AHEAD_DISTANCE = 120;     // Max pixels ahead in velocity direction

    /**
     * Frozen input object: passed to Ship.update() during COUNTDOWN
     * and FINISHED states so the ship renders but cannot move.
     */
    const FROZEN_INPUT = Object.freeze({
        up: false, down: false, left: false, right: false,
    });

    /** Default number of laps for race mode */
    const DEFAULT_LAPS = 3;

    /** How long warning messages stay visible (seconds) */
    const WARNING_DURATION = 2.0;

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
         * Called before create(). Receives data from MenuScene or
         * ResultsScene via scene.start(). Sets up race configuration.
         *
         * @param {Object} data - Race configuration
         * @param {string} [data.seed] - Track seed (default: today's date)
         * @param {string} [data.mode] - 'race' or 'timetrial' (default: 'race')
         * @param {string} [data.ccTier] - CC tier name (default: '50cc')
         * @param {boolean} [data.ghostEnabled] - Whether ghost is visible (default: true)
         */
        init(data) {
            this.raceSeed = data.seed || new Date().toISOString().slice(0, 10);
            this.mode = data.mode || 'race';
            this.ccTier = data.ccTier || '50cc';
            this.ghostVisible = data.ghostEnabled !== false; // default true

            // Race mode: fixed 3 laps. Time Trial: infinite (null for HUD display).
            this.totalLaps = this.mode === 'timetrial' ? null : DEFAULT_LAPS;
        }

        // ---------------------------------------------------------------
        // Phaser Lifecycle: create
        // ---------------------------------------------------------------

        create() {
            // Remove HTML loading indicator (only exists on first load)
            const loadingEl = document.getElementById('game-loading');
            if (loadingEl) loadingEl.remove();

            // Camera fade in from menu/results transition
            this.cameras.main.fadeIn(200, 0, 0, 0);

            // Prevents double-navigation during fade transitions
            this.transitioning = false;

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

            // ----- Ghost System -----
            this.ghostRecorder = new Ghost.GhostRecorder();
            this.ghostPlayback = null;
            this.ghostGfx = null;
            this.ghostLabel = null;

            // Load ghost data for this seed + mode + tier
            const ghostData = Ghost.GhostStorage.load(this.raceSeed, this.mode, this.ccTier);

            if (this.mode === 'timetrial') {
                // Time Trial: ghost stores a single best lap
                this.storedBestLapMs = ghostData ? ghostData.bestLapMs : null;
                this.storedBestTimeMs = null; // Not used in TT
            } else {
                // Race mode: ghost stores full 3-lap race
                this.storedBestTimeMs = ghostData ? ghostData.totalTimeMs : null;
                this.storedBestLapMs = null; // Not used in race mode
            }

            // Total recording time in seconds (used for TT ghost replacement tracking)
            this.ghostTotalTimeSec = null;

            // Pre-computed ghost checkpoint crossing times (for delta display).
            // Ordered array of { cpIndex, elapsed } records.
            this.ghostCheckpointTimes = null;

            // Cursor into ghostCheckpointTimes: the next crossing to match
            this.ghostCrossingCursor = 0;

            // Current ghost delta displayed in HUD (updated at checkpoint crossings).
            // Positive = player is behind ghost, negative = player is ahead.
            this.currentGhostDelta = null;

            // Create playback visuals if ghost data exists
            if (ghostData && ghostData.frames && ghostData.frames.length > 0) {
                this.ghostPlayback = new Ghost.GhostPlayback(
                    ghostData.frames, Ghost.GHOST_COLORS[0]
                );
                this.createGhostVisuals();

                this.ghostTotalTimeSec = ghostData.totalTimeMs / 1000;

                // Pre-compute when the ghost crosses each checkpoint
                this.ghostCheckpointTimes = this.computeGhostCheckpointTimes(
                    ghostData.frames, this.trackData.checkpoints
                );
            }

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

            // ----- Time Trial Session Tracking -----
            this.ttSessionBestLapTime = null;
            this.ttSessionBestLapIndex = -1;
            this.ttTotalLapsCompleted = 0;
            this.ttSessionStartTime = 0; // Set when racing starts

            // Pre-allocated objects reused every frame (avoid GC pressure)
            this._hudState = {};

            // Checkpoint crossing uses 3 test points (nose + wingtips)
            // so any part of the ship touching a gate triggers the crossing.
            // Store previous rotation for transforming old extremity positions.
            this.prevShipRotation = this.ship.rotation;
            this._cpTestPrev = [{ x: 0, y: 0 }, { x: 0, y: 0 }, { x: 0, y: 0 }];
            this._cpTestCurr = [{ x: 0, y: 0 }, { x: 0, y: 0 }, { x: 0, y: 0 }];

            // Last correctly-crossed checkpoint index. Used to suppress false
            // "MISSED CHECKPOINT" warnings from trailing wingtip rays crossing
            // the same gate on subsequent frames after the nose already triggered it.
            this._lastCrossedCP = -1;

            // ----- Countdown -----
            this.startCountdown();

            // ----- Dev Keys: CC Tier Switching (1/2/3) -----
            const KeyCodes = Phaser.Input.Keyboard.KeyCodes;
            this.key1 = this.input.keyboard.addKey(KeyCodes.ONE, true, false);
            this.key2 = this.input.keyboard.addKey(KeyCodes.TWO, true, false);
            this.key3 = this.input.keyboard.addKey(KeyCodes.THREE, true, false);

            // ----- Ghost Toggle (G key) -----
            this.keyG = this.input.keyboard.addKey(KeyCodes.G, true, false);

            // ----- Cleanup Handler -----
            this.events.once('shutdown', () => {
                this.input.setDefaultCursor('default');

                // Cancel any in-flight countdown timers (prevents callbacks
                // firing after shutdown when ESC is pressed during countdown)
                if (this.countdownTimers) {
                    this.countdownTimers.forEach(t => t.remove(false));
                    this.countdownTimers = null;
                }
                if (this.finishTimer) {
                    this.finishTimer.remove(false);
                    this.finishTimer = null;
                }
                if (this.countdownText) {
                    this.countdownText.destroy();
                    this.countdownText = null;
                }
                if (this.flashOverlay) {
                    this.flashOverlay.destroy();
                    this.flashOverlay = null;
                }
                if (this.ship) this.ship.destroy();
                if (this.inputManager) this.inputManager.destroy();
                if (this.hud) this.hud.destroy();
                if (this.trackGraphics) this.trackGraphics.destroy();
                if (this.warningText) { this.warningText.destroy(); this.warningText = null; }
                if (this.ghostGfx) { this.ghostGfx.destroy(); this.ghostGfx = null; }
                if (this.ghostLabel) { this.ghostLabel.destroy(); this.ghostLabel = null; }
                this.input.keyboard.removeCapture([
                    KeyCodes.ONE, KeyCodes.TWO, KeyCodes.THREE, KeyCodes.G,
                ]);
            });

            const modeLabel = this.mode === 'timetrial' ? 'Time Trial' : `${this.totalLaps}-Lap Race`;
            console.log(`[RaceScene] Created: seed="${this.raceSeed}", mode=${modeLabel}, cc=${this.ccTier}`);
            console.log(`[RaceScene] Track: ${td.checkpoints.length} checkpoints, arc=${td.totalArcLength.toFixed(0)}px`);
        }

        // ---------------------------------------------------------------
        // Phaser Lifecycle: update
        // ---------------------------------------------------------------

        update(time, delta) {
            // Clamp dt to 100ms (10fps equivalent) to prevent timer
            // corruption and ship teleportation on tab-switch spikes.
            const dt = Math.min(delta / 1000, 0.1);

            // --- ESC handling ---
            if (!this.transitioning
                && this.raceState !== RACE_STATE.FINISHED
                && this.inputManager.isPausePressed()) {
                this.handleEscape();
                return;
            }

            // --- Dev: CC tier switching ---
            const JustDown = Phaser.Input.Keyboard.JustDown;
            if (JustDown(this.key1)) { this.ccTier = '50cc';  this.ship.setCCTier('50cc'); }
            if (JustDown(this.key2)) { this.ccTier = '100cc'; this.ship.setCCTier('100cc'); }
            if (JustDown(this.key3)) { this.ccTier = '200cc'; this.ship.setCCTier('200cc'); }

            // --- Ghost visibility toggle (G key) ---
            if (JustDown(this.keyG)) {
                this.ghostVisible = !this.ghostVisible;
                if (!this.ghostVisible) {
                    // Hide ghost visuals immediately
                    if (this.ghostGfx) this.ghostGfx.clear();
                    if (this.ghostLabel) this.ghostLabel.setAlpha(0);
                }
            }

            // --- Capture previous position/rotation BEFORE ship update ---
            this.prevShipX = this.ship.x;
            this.prevShipY = this.ship.y;
            this.prevShipRotation = this.ship.rotation;

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
                this.ghostRecorder.update(dt, this.ship);
            }

            // --- Ghost playback (RACING state only) ---
            // Always advance ghost playback (needed for delta computation),
            // but only render visuals when ghostVisible is true.
            if (this.ghostPlayback && this.raceState === RACE_STATE.RACING) {
                this.ghostPlayback.update(dt);

                if (this.ghostVisible) {
                    Ghost.drawGhost(
                        this.ghostGfx, this.ghostPlayback,
                        ShipModule.SHIP_VERTICES
                    );

                    // Position "Personal Best" label above ghost ship
                    if (this.ghostLabel) {
                        if (this.ghostPlayback.finished || this.ghostPlayback.alpha <= 0) {
                            this.ghostLabel.setAlpha(0);
                        } else {
                            this.ghostLabel.setAlpha(0.4);
                            this.ghostLabel.setPosition(
                                this.ghostPlayback.x,
                                this.ghostPlayback.y - 25
                            );
                        }
                    }
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
            hs.mode = this.mode;

            // Ghost data for HUD minimap dots and delta display.
            // Minimap dots only shown when ghostVisible is true.
            // Delta is checkpoint-based (updated in updateGhostDelta).
            if (this.ghostPlayback && !this.ghostPlayback.finished) {
                hs.ghosts = this.ghostVisible
                    ? [{ x: this.ghostPlayback.x, y: this.ghostPlayback.y, color: this.ghostPlayback.color }]
                    : null;
            } else {
                hs.ghosts = null;
            }
            hs.ghostDelta = this.currentGhostDelta;

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
            // Store delayed call references so the shutdown handler can
            // cancel them if ESC is pressed during the countdown.
            this.countdownTimers = [];

            this.countdownText = this.add.text(
                DESIGN_WIDTH / 2, DESIGN_HEIGHT / 2, '', {
                    fontFamily: 'Poppins, sans-serif',
                    fontSize: '120px',
                    fontStyle: '700',
                    color: '#2ce8f5',
                }
            ).setOrigin(0.5).setScrollFactor(0).setDepth(DEPTH.OVERLAY).setAlpha(0);

            // Screen flash overlay (brief white flash on each beat)
            this.flashOverlay = this.add.graphics();
            this.flashOverlay.setScrollFactor(0);
            this.flashOverlay.setDepth(DEPTH.OVERLAY - 1);
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

                this.countdownTimers.push(this.time.delayedCall(delay, () => {
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
                }));

                delay += duration;
            }

            // Transition to RACING after countdown completes
            this.countdownTimers.push(this.time.delayedCall(delay, () => {
                this.raceState = RACE_STATE.RACING;
                this.ttSessionStartTime = this.raceTime; // TT: mark session start
                this.countdownTimers = null;
                if (this.countdownText) {
                    this.countdownText.destroy();
                    this.countdownText = null;
                }
                if (this.flashOverlay) {
                    this.flashOverlay.destroy();
                    this.flashOverlay = null;
                }
            }));
        }

        // ---------------------------------------------------------------
        // Checkpoint Detection
        // ---------------------------------------------------------------

        /**
         * Tests the ship against all checkpoint gate segments.
         *
         * Uses 3 test points (nose + both wingtips) so that any part
         * of the ship touching a checkpoint gate triggers detection.
         * Each test point's movement ray (previous -> current world
         * position) is tested against every checkpoint gate segment.
         *
         * For each crossing detected:
         * - Backward crossings (dot product <= 0): show "WRONG WAY"
         * - Correct next checkpoint: advances the sequence
         * - Skipped checkpoint (forward but not next): show "MISSED CHECKPOINT"
         */
        checkCheckpointCrossing() {
            const checkpoints = this.trackData.checkpoints;
            const segInt = TrackGen.segmentIntersection;
            const vertices = ShipModule.SHIP_VERTICES;

            // Ship-local test points: nose (vertex 0), right wing (1), left wing (4)
            const localPoints = [vertices[0], vertices[1], vertices[4]];

            // Transform test points to world space for previous and current frames
            const prevCos = Math.cos(this.prevShipRotation);
            const prevSin = Math.sin(this.prevShipRotation);
            const currCos = Math.cos(this.ship.rotation);
            const currSin = Math.sin(this.ship.rotation);

            for (let p = 0; p < 3; p++) {
                const lx = localPoints[p].x;
                const ly = localPoints[p].y;

                this._cpTestPrev[p].x = this.prevShipX + lx * prevCos - ly * prevSin;
                this._cpTestPrev[p].y = this.prevShipY + lx * prevSin + ly * prevCos;
                this._cpTestCurr[p].x = this.ship.x + lx * currCos - ly * currSin;
                this._cpTestCurr[p].y = this.ship.y + lx * currSin + ly * currCos;
            }

            for (let i = 0; i < checkpoints.length; i++) {
                const cp = checkpoints[i];

                // Test each of the 3 rays against this checkpoint gate.
                // Break on first hit (one crossing per checkpoint per frame).
                let crossed = false;
                let moveX = 0;
                let moveY = 0;

                for (let p = 0; p < 3; p++) {
                    const prev = this._cpTestPrev[p];
                    const curr = this._cpTestCurr[p];
                    const hit = segInt(prev, curr, cp.leftPoint, cp.rightPoint);

                    if (hit) {
                        moveX = curr.x - prev.x;
                        moveY = curr.y - prev.y;
                        crossed = true;
                        break;
                    }
                }

                if (!crossed) continue;

                // Direction check: dot product of movement against
                // checkpoint direction. Positive = correct direction.
                const dot = moveX * cp.direction.x + moveY * cp.direction.y;

                if (dot <= 0) {
                    this.showWarning('WRONG WAY');
                    continue;
                }

                if (i === this.nextCheckpoint) {
                    this._lastCrossedCP = i;
                    this.onCheckpointCrossed(i);
                    return; // One correct crossing per frame; remaining hits are trailing wingtip artifacts
                } else if (i !== 0 && i !== this._lastCrossedCP) {
                    // Crossed a checkpoint that isn't the current target
                    // and isn't the one we just handled (trailing wingtip).
                    this.showWarning('MISSED CHECKPOINT');
                }
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
         * Lap completion behavior differs by mode:
         * - Race: increment lap counter, check for race finish
         * - Time Trial: save best lap, reset ghost, never finish
         *
         * @param {number} index - The checkpoint index that was crossed
         */
        onCheckpointCrossed(index) {
            const checkpoints = this.trackData.checkpoints;

            // Notify HUD for minimap checkpoint dot tracking
            this.hud.onCheckpointCrossed(index);

            // Update ghost delta at this checkpoint crossing
            this.updateGhostDelta(index);

            if (index === 0) {
                // Start/finish line
                if (this.allCheckpointsPassed) {
                    // Mark lap boundary in ghost recorder
                    this.ghostRecorder.markLapBoundary();

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
                    this.hud.onLapComplete(this.currentLap, lapTime);

                    // Show lap completion flash
                    this.showLapFlash(this.currentLap, lapTime);

                    if (this.mode === 'timetrial') {
                        this.onTimeTrialLapComplete(lapTime);
                    } else {
                        // Race mode: check for finish
                        if (this.currentLap >= this.totalLaps) {
                            this.onRaceFinished();
                            return;
                        }

                        // Advance ghost cursor to the next lap's first crossing.
                        // The ghost checkpoint times array contains ALL crossings
                        // across all laps. Find the next checkpoint-1 crossing
                        // (start of the ghost's next lap) so delta comparisons
                        // stay aligned with the correct lap.
                        if (this.ghostCheckpointTimes) {
                            const crossings = this.ghostCheckpointTimes;
                            for (let i = this.ghostCrossingCursor; i < crossings.length; i++) {
                                if (crossings[i].cpIndex === 1) {
                                    this.ghostCrossingCursor = i;
                                    break;
                                }
                            }
                        }
                        this.currentGhostDelta = null;
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
        // Time Trial Lap Completion
        // ---------------------------------------------------------------

        /**
         * Handles Time Trial-specific logic when a lap is completed.
         * Saves new best lap ghost, replaces playback, resets ghost position.
         *
         * @param {number} lapTime - The completed lap's time in seconds
         */
        onTimeTrialLapComplete(lapTime) {
            this.ttTotalLapsCompleted++;

            // Track session best
            if (this.ttSessionBestLapTime === null || lapTime <= this.ttSessionBestLapTime) {
                this.ttSessionBestLapTime = lapTime;
                this.ttSessionBestLapIndex = this.ttTotalLapsCompleted;
            }

            // Check against stored best (from localStorage)
            const lapTimeMs = Math.round(lapTime * 1000);
            const isNewStoredBest = !this.storedBestLapMs || lapTimeMs < this.storedBestLapMs;

            if (isNewStoredBest) {
                // Extract this lap's frames from the recorder
                const lapIndex = this.lapTimes.length - 1; // Just pushed above
                const lapFrames = this.ghostRecorder.getLapFrames(lapIndex);

                // Save to localStorage
                Ghost.GhostStorage.save(this.raceSeed, 'timetrial', this.ccTier, {
                    frames: lapFrames,
                    totalTimeMs: lapTimeMs,
                    bestLapMs: lapTimeMs,
                    lapTimes: [lapTime],
                    recordedAt: new Date().toISOString(),
                    version: 1,
                });

                this.storedBestLapMs = lapTimeMs;
                this.ghostTotalTimeSec = lapTime;

                // Replace ghost playback with the new best lap
                this.ghostPlayback = new Ghost.GhostPlayback(
                    lapFrames, Ghost.GHOST_COLORS[0]
                );
                this.createGhostVisuals();

                // Recompute ghost checkpoint times for the new recording
                this.ghostCheckpointTimes = this.computeGhostCheckpointTimes(
                    lapFrames, this.trackData.checkpoints
                );

                console.log(`[RaceScene] TT new best: ${formatTime(lapTime)} (${lapFrames.length / Ghost.VALUES_PER_FRAME} frames)`);
            } else if (this.ghostPlayback) {
                // Not a new best: just reset ghost playback for the next lap
                this.ghostPlayback.reset();
            }

            // Reset delta tracking for the new lap
            this.ghostCrossingCursor = 0;
            this.currentGhostDelta = null;
        }

        // ---------------------------------------------------------------
        // Ghost Checkpoint Times
        // ---------------------------------------------------------------

        /**
         * Creates the ghost ship's Graphics and label objects.
         * Called when ghost data first loads (create) or when a new
         * TT best lap replaces the ghost (onTimeTrialLapComplete).
         * Idempotent: skips creation if objects already exist.
         */
        createGhostVisuals() {
            if (!this.ghostGfx) {
                this.ghostGfx = this.add.graphics();
                this.ghostGfx.setDepth(9); // Below player ship (depth 11)
            }
            if (!this.ghostLabel) {
                this.ghostLabel = this.add.text(0, 0, 'Personal Best', {
                    fontFamily: 'Inter, sans-serif',
                    fontSize: '10px',
                    color: CSS.STAR_WHITE,
                }).setOrigin(0.5, 1).setDepth(9).setAlpha(0.4);
            }
        }

        // ---------------------------------------------------------------
        // Ghost Checkpoint Times
        // ---------------------------------------------------------------

        /**
         * Pre-computes when the ghost crosses each checkpoint by ray-testing
         * consecutive ghost frames against checkpoint gate segments.
         *
         * Uses the same 3-ray polygon approach as player checkpoint detection:
         * nose, right wing, and left wing extremity points are tested against
         * each checkpoint gate. This ensures ghost crossing times match what
         * the player would experience on the same racing line.
         *
         * Returns a flat array of crossing records sorted by elapsed time.
         * Each record: { cpIndex, elapsed } where elapsed is in seconds.
         *
         * For a 3-lap race, checkpoints are crossed multiple times. The
         * array contains ALL crossings in order (e.g., cp1 appears 3 times).
         *
         * @param {number[]} frames - Flat ghost frame array [x,y,r,t,s,...]
         * @param {Object[]} checkpoints - Track checkpoint data
         * @returns {Array<{cpIndex: number, elapsed: number}>} Ordered crossing records
         */
        computeGhostCheckpointTimes(frames, checkpoints) {
            const segInt = TrackGen.segmentIntersection;
            const vpf = Ghost.VALUES_PER_FRAME;
            const interval = Ghost.SAMPLE_INTERVAL;
            const totalFrames = frames.length / vpf;
            const crossings = [];
            const vertices = ShipModule.SHIP_VERTICES;

            // Same 3 test points as player checkpoint detection (nose, right wing, left wing)
            const localPoints = [vertices[0], vertices[1], vertices[4]];

            // Temporary point objects to avoid allocations in the loop
            const prev = { x: 0, y: 0 };
            const curr = { x: 0, y: 0 };

            for (let f = 0; f < totalFrames - 1; f++) {
                const a = f * vpf;
                const b = (f + 1) * vpf;

                // Ghost position and rotation at frames f and f+1
                const ax = frames[a], ay = frames[a + 1], aRot = frames[a + 2];
                const bx = frames[b], by = frames[b + 1], bRot = frames[b + 2];

                const prevCos = Math.cos(aRot), prevSin = Math.sin(aRot);
                const currCos = Math.cos(bRot), currSin = Math.sin(bRot);

                for (let ci = 0; ci < checkpoints.length; ci++) {
                    const cp = checkpoints[ci];
                    let hit = null;

                    // Test each of the 3 extremity rays (same as player detection)
                    for (let p = 0; p < 3; p++) {
                        const lx = localPoints[p].x;
                        const ly = localPoints[p].y;

                        prev.x = ax + lx * prevCos - ly * prevSin;
                        prev.y = ay + lx * prevSin + ly * prevCos;
                        curr.x = bx + lx * currCos - ly * currSin;
                        curr.y = by + lx * currSin + ly * currCos;

                        hit = segInt(prev, curr, cp.leftPoint, cp.rightPoint);
                        if (hit) break;
                    }

                    if (!hit) continue;

                    // Direction check (same as player checkpoint detection)
                    const moveX = curr.x - prev.x;
                    const moveY = curr.y - prev.y;
                    const dot = moveX * cp.direction.x + moveY * cp.direction.y;
                    if (dot <= 0) continue; // Wrong direction

                    // Sub-frame precision: use intersection t parameter for exact crossing time
                    const elapsed = (f + hit.t) * interval;
                    crossings.push({ cpIndex: ci, elapsed });
                }
            }

            console.log(`[RaceScene] Ghost checkpoint crossings: ${crossings.length}`);
            return crossings;
        }

        /**
         * Updates the ghost delta when the player crosses a checkpoint.
         *
         * Searches forward from the cursor in ghostCheckpointTimes for
         * the next crossing record matching this checkpoint index.
         * Computes delta as playerTime - ghostTime at that checkpoint.
         *
         * For Time Trial, uses current lap time instead of total race time.
         *
         * @param {number} cpIndex - The checkpoint index the player just crossed
         */
        updateGhostDelta(cpIndex) {
            if (!this.ghostCheckpointTimes || this.ghostCheckpointTimes.length === 0) return;

            const crossings = this.ghostCheckpointTimes;

            // Find the next ghost crossing matching this checkpoint index
            for (let i = this.ghostCrossingCursor; i < crossings.length; i++) {
                if (crossings[i].cpIndex === cpIndex) {
                    const ghostTime = crossings[i].elapsed;
                    const playerTime = (this.mode === 'timetrial')
                        ? (this.raceTime - this.lapStartTime) // Current lap time for TT
                        : this.raceTime;                       // Total race time for race mode

                    this.currentGhostDelta = playerTime - ghostTime;
                    this.ghostCrossingCursor = i + 1;
                    return;
                }
            }

            // No matching crossing found (ghost may have ended earlier)
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
            ).setOrigin(0.5).setScrollFactor(0).setDepth(DEPTH.HUD_TEXT);

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
                `Lap ${lapNumber}: ${formatTime(lapTime)}`, {
                    fontFamily: 'monospace',
                    fontSize: '28px',
                    fontStyle: 'bold',
                    color: trailHex,
                }
            ).setOrigin(0.5).setScrollFactor(0).setDepth(DEPTH.OVERLAY);

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
            this.warningTimer = WARNING_DURATION;

            if (!this.warningText) {
                this.warningText = this.add.text(
                    DESIGN_WIDTH / 2, 120, '', {
                        fontFamily: 'Poppins, sans-serif',
                        fontSize: '28px',
                        fontStyle: '700',
                        color: '#e43b44',
                    }
                ).setOrigin(0.5).setScrollFactor(0).setDepth(DEPTH.OVERLAY);
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
        // Results Payload
        // ---------------------------------------------------------------

        /**
         * Builds the data object passed to ResultsScene.
         * Core fields are always included; callers pass mode-specific overrides.
         *
         * Only passes the trackData fields that renderMinimap needs, not the
         * full trackData (avoids carrying 6 large arrays through the transition).
         *
         * @param {Object} [overrides={}] - Additional/override fields (isNewRecord, tt* fields, etc.)
         * @returns {Object} Complete ResultsScene init data
         */
        buildResultsPayload(overrides = {}) {
            return {
                totalTime: this.raceTime,
                lapTimes: this.lapTimes,
                bestLapTime: this.bestLapTime,
                bestLapIndex: this.bestLapIndex,
                seed: this.raceSeed,
                ccTier: this.ccTier,
                mode: this.mode,
                ghostEnabled: this.ghostVisible,
                trackData: {
                    centerPoints: this.trackData.centerPoints,
                    bounds: this.trackData.bounds,
                    checkpoints: this.trackData.checkpoints,
                },
                ...overrides,
            };
        }

        // ---------------------------------------------------------------
        // Race Finish (Race Mode Only)
        // ---------------------------------------------------------------

        /**
         * Transitions to FINISHED state. After a brief delay (so the
         * player sees the finish line crossing), fades to the
         * ResultsScene with the race data.
         */
        onRaceFinished() {
            this.raceState = RACE_STATE.FINISHED;
            this.transitioning = true;

            console.log(`[RaceScene] Race finished: ${formatTime(this.raceTime)}`);

            // --- Ghost auto-save ---
            const raceTimeMs = Math.round(this.raceTime * 1000);
            const isNewRecord = !this.storedBestTimeMs || raceTimeMs < this.storedBestTimeMs;

            if (isNewRecord) {
                Ghost.GhostStorage.save(this.raceSeed, 'race', this.ccTier, {
                    frames: this.ghostRecorder.getFrames(),
                    totalTimeMs: raceTimeMs,
                    lapTimes: this.lapTimes,
                    bestLapMs: this.bestLapTime ? Math.round(this.bestLapTime * 1000) : null,
                    recordedAt: new Date().toISOString(),
                    version: 1,
                });
                console.log(`[RaceScene] Ghost saved: ${raceTimeMs}ms (${this.ghostRecorder.getFrameCount()} frames)`);
            }

            // Brief delay so the player sees the finish, then transition
            this.finishTimer = this.time.delayedCall(1500, () => {
                this.cameras.main.fadeOut(200, 0, 0, 0);
                this.cameras.main.once('camerafadeoutcomplete', () => {
                    this.scene.start('ResultsScene', this.buildResultsPayload({
                        isNewRecord: isNewRecord,
                    }));
                });
            });
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
         * ESC behavior depends on mode and state:
         * - Race mode (any state): fade to MenuScene
         * - Time Trial (COUNTDOWN): fade to MenuScene
         * - Time Trial (RACING): fade to ResultsScene with session summary
         */
        handleEscape() {
            if (this.transitioning) return;
            this.transitioning = true;

            // Time Trial during RACING: show session summary
            if (this.mode === 'timetrial' && this.raceState === RACE_STATE.RACING && this.ttTotalLapsCompleted > 0) {
                this.cameras.main.fadeOut(200, 0, 0, 0);
                this.cameras.main.once('camerafadeoutcomplete', () => {
                    this.scene.start('ResultsScene', this.buildResultsPayload({
                        isNewRecord: false, // TT saves incrementally, no single "record" moment
                        ttTotalLapsCompleted: this.ttTotalLapsCompleted,
                        ttSessionBestLapTime: this.ttSessionBestLapTime,
                        ttSessionBestLapIndex: this.ttSessionBestLapIndex,
                    }));
                });
            } else {
                // Race mode or TT during countdown: go to menu
                this.cameras.main.fadeOut(200, 0, 0, 0);
                this.cameras.main.once('camerafadeoutcomplete', () => {
                    this.scene.start('MenuScene');
                });
            }
        }

    }

    // ===================================================================
    // PUBLIC API
    // ===================================================================

    PlatPursuit.Games.Driver.Scenes.RaceScene = RaceScene;

    console.log('[Stellar Circuit] RaceScene loaded');
})();
