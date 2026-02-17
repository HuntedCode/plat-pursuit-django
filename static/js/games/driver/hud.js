/**
 * PlatPursuit.Games.Driver.HUD
 *
 * Heads-up display for Stellar Circuit.
 * All elements are rendered with scrollFactor(0) so they stay fixed on
 * screen regardless of the game world camera position.
 *
 * Elements (GDD Section 5):
 * - Speed bar: horizontal fill bar with color gradient (cyan -> green)
 * - Lap counter: "LAP 2/3" (race) or "LAP 5" (time trial)
 * - Timer display: total time, current lap time, best lap time, ghost delta
 * - Minimap: track outline, checkpoint dots, player dot, ghost dots
 * - CC tier label: current speed class
 *
 * Ghost Delta System:
 * The delta is pre-computed by RaceScene at each checkpoint crossing.
 * RaceScene compares the player's time at a checkpoint against the
 * ghost's pre-computed time at the same checkpoint, then passes the
 * result as `ghostDelta` in the HUD state. The HUD simply displays
 * whatever delta value it receives. Positive = behind, negative = ahead.
 *
 * Architecture:
 * - RaceScene creates one HUD instance in create(), passing (scene, trackData)
 * - RaceScene calls hud.update(stateSnapshot) every frame
 * - RaceScene calls hud.onCheckpointCrossed(index) when a checkpoint is passed
 * - RaceScene calls hud.onLapComplete(lapNumber, lapTime) on lap finish
 * - RaceScene calls hud.destroy() in the shutdown handler
 *
 * Color Palette: All colors from gamification-design.md.
 */

window.PlatPursuit = window.PlatPursuit || {};
window.PlatPursuit.Games = window.PlatPursuit.Games || {};
window.PlatPursuit.Games.Driver = window.PlatPursuit.Games.Driver || {};

(function() {
    'use strict';

    // Aliases
    const Shell = PlatPursuit.Games.Shell;
    const TrackGen = PlatPursuit.Games.Driver.TrackGenerator;
    const UI = PlatPursuit.Games.Driver.UI;

    const { DESIGN_WIDTH, DESIGN_HEIGHT } = Shell;
    const { COLOR, CSS, DEPTH, RACE_STATE, formatTime } = UI;

    // ===================================================================
    // LAYOUT CONSTANTS
    // ===================================================================

    // Speed bar (bottom-left per GDD 5.6)
    const SPEED_BAR_X = 20;
    const SPEED_BAR_Y = 690;
    const SPEED_BAR_W = 180;
    const SPEED_BAR_H = 12;
    const SPEED_BAR_RADIUS = 3;

    // Speed bar color stops: bar color shifts from cyan to green as
    // speed increases. We draw the fill in small segments, each tinted
    // by interpolating between these color stops.
    const SPEED_COLOR_STOPS = [
        { at: 0.0,  r: 0x0a, g: 0x8e, b: 0xa0 },  // Cyan Shadow
        { at: 0.5,  r: 0x2c, g: 0xe8, b: 0xf5 },  // Cyan Glow
        { at: 1.0,  r: 0x40, g: 0xe8, b: 0x50 },  // Neon Green
    ];

    // Minimap (bottom-right per GDD 5.5)
    const MINIMAP_SIZE = 150;
    const MINIMAP_X = DESIGN_WIDTH - MINIMAP_SIZE - 12;
    const MINIMAP_Y = DESIGN_HEIGHT - MINIMAP_SIZE - 12;

    // ===================================================================
    // HUD CLASS
    // ===================================================================

    class HUD {
        /**
         * @param {Phaser.Scene} scene - The active scene
         * @param {Object} trackData - Track data from TrackGenerator.generate()
         */
        constructor(scene, trackData) {
            this.scene = scene;
            this.trackData = trackData;

            // Checkpoint crossing state for minimap dot rendering.
            // Tracks which checkpoints have been crossed this lap.
            this.crossedCheckpoints = new Set();

            // Completed lap times for display under lap counter
            this.completedLapTimes = [];

            // Peak speed observed this race. The quadratic thrust soft
            // cap means the ship never reaches maxSpeed (equilibrium
            // varies by tier: ~57% for 50cc, ~68% for 200cc). Rather
            // than hardcoding a ratio, we track the actual peak and use
            // it as the bar's "full" reference. Starts at 1 to avoid
            // division by zero; ramps up naturally in the first seconds.
            this.peakSpeed = 1;

            this.createSpeedBar();
            this.createLapCounter();
            this.createTimerDisplay();
            this.createMinimap();
            this.createCCTierLabel();
        }

        // ---------------------------------------------------------------
        // Speed Bar
        // ---------------------------------------------------------------

        createSpeedBar() {
            // "SPD" label above the bar
            this.spdLabel = this.scene.add.text(
                SPEED_BAR_X, SPEED_BAR_Y - 16, 'SPD', {
                    fontFamily: 'Inter, sans-serif',
                    fontSize: '10px',
                    color: CSS.NEUTRAL_MID,
                }
            ).setScrollFactor(0).setDepth(DEPTH.HUD);

            // Graphics object for the bar (background + fill)
            this.speedBarGfx = this.scene.add.graphics();
            this.speedBarGfx.setScrollFactor(0);
            this.speedBarGfx.setDepth(DEPTH.HUD);

            // Numeric speed readout to the right of the bar
            this.speedText = this.scene.add.text(
                SPEED_BAR_X + SPEED_BAR_W + 8, SPEED_BAR_Y - 1, '0', {
                    fontFamily: 'monospace',
                    fontSize: '14px',
                    color: CSS.STAR_WHITE,
                }
            ).setScrollFactor(0).setDepth(DEPTH.HUD);
        }

        updateSpeedBar(speed) {
            // Dirty check: skip redraw if the visual hasn't changed.
            // The bar's pixel width depends on fillRatio, which depends
            // on speed relative to peakSpeed. Round to avoid sub-pixel churn.
            const roundedSpeed = Math.round(speed);
            if (roundedSpeed === this._lastDrawnSpeed && speed <= this.peakSpeed) return;
            this._lastDrawnSpeed = roundedSpeed;

            const g = this.speedBarGfx;
            g.clear();

            // Track peak speed observed this race. The bar scales so
            // peak speed = full bar. This self-calibrates within seconds
            // and works correctly for all CC tiers without hardcoded ratios.
            if (speed > this.peakSpeed) {
                this.peakSpeed = speed;
            }
            const fillRatio = Math.min(speed / this.peakSpeed, 1);

            // Background bar (rounded rect)
            g.fillStyle(COLOR.DEEP_SPACE_2, 1.0);
            g.fillRoundedRect(
                SPEED_BAR_X, SPEED_BAR_Y,
                SPEED_BAR_W, SPEED_BAR_H,
                SPEED_BAR_RADIUS
            );
            g.lineStyle(1, COLOR.NEUTRAL_DARK, 1.0);
            g.strokeRoundedRect(
                SPEED_BAR_X, SPEED_BAR_Y,
                SPEED_BAR_W, SPEED_BAR_H,
                SPEED_BAR_RADIUS
            );

            // Fill bar with color gradient (drawn as small segments).
            // Phaser Graphics doesn't support linear gradients natively,
            // so we approximate by drawing narrow rect slices, each
            // tinted by interpolating between SPEED_COLOR_STOPS.
            if (fillRatio > 0.01) {
                const fillW = Math.max(SPEED_BAR_RADIUS * 2, SPEED_BAR_W * fillRatio);
                const segments = Math.ceil(fillW / 3); // ~3px per segment

                for (let i = 0; i < segments; i++) {
                    const segX = SPEED_BAR_X + (fillW * i / segments);
                    const segW = fillW / segments + 0.5; // slight overlap to avoid gaps
                    const t = i / Math.max(segments - 1, 1); // 0..1 across fill

                    // Find the two color stops to lerp between
                    const color = this.lerpColorStops(t);
                    g.fillStyle(color, 0.9);
                    g.fillRect(segX, SPEED_BAR_Y, segW, SPEED_BAR_H);
                }

                // Round the left edge by redrawing the background's left
                // rounded corner on top, then filling the first few pixels
                // with the start color as a rounded rect.
                const cornerW = SPEED_BAR_RADIUS * 2 + 2;
                if (fillW > cornerW) {
                    // Clean start: draw a small rounded rect at the left
                    const startColor = this.lerpColorStops(0);
                    g.fillStyle(startColor, 0.9);
                    g.fillRoundedRect(
                        SPEED_BAR_X, SPEED_BAR_Y,
                        cornerW, SPEED_BAR_H,
                        { tl: SPEED_BAR_RADIUS, bl: SPEED_BAR_RADIUS, tr: 0, br: 0 }
                    );
                }
            }

            // Re-stroke the border so it's on top of the fill
            g.lineStyle(1, COLOR.NEUTRAL_DARK, 1.0);
            g.strokeRoundedRect(
                SPEED_BAR_X, SPEED_BAR_Y,
                SPEED_BAR_W, SPEED_BAR_H,
                SPEED_BAR_RADIUS
            );

            // Numeric readout
            this.speedText.setText(Math.round(speed).toString());
        }

        /**
         * Interpolates between SPEED_COLOR_STOPS at position t (0..1).
         * Returns a 0xRRGGBB integer.
         */
        lerpColorStops(t) {
            const stops = SPEED_COLOR_STOPS;

            // Find the two stops that bracket t
            let lower = stops[0];
            let upper = stops[stops.length - 1];
            for (let i = 0; i < stops.length - 1; i++) {
                if (t >= stops[i].at && t <= stops[i + 1].at) {
                    lower = stops[i];
                    upper = stops[i + 1];
                    break;
                }
            }

            // Normalize t within this segment
            const range = upper.at - lower.at;
            const localT = range > 0 ? (t - lower.at) / range : 0;

            const r = Math.round(lower.r + (upper.r - lower.r) * localT);
            const g = Math.round(lower.g + (upper.g - lower.g) * localT);
            const b = Math.round(lower.b + (upper.b - lower.b) * localT);

            return (r << 16) | (g << 8) | b;
        }

        // ---------------------------------------------------------------
        // Lap Counter
        // ---------------------------------------------------------------

        createLapCounter() {
            this.lapText = this.scene.add.text(
                DESIGN_WIDTH - 20, 20, '', {
                    fontFamily: 'Poppins, sans-serif',
                    fontSize: '22px',
                    fontStyle: '600',
                    color: CSS.STAR_WHITE,
                }
            ).setOrigin(1, 0).setScrollFactor(0).setDepth(DEPTH.HUD);

            // Completed lap times listed below the lap counter.
            // Each entry is one line, right-aligned under "LAP X/Y".
            this.lapTimesText = this.scene.add.text(
                DESIGN_WIDTH - 20, 46, '', {
                    fontFamily: 'monospace',
                    fontSize: '12px',
                    color: CSS.NEUTRAL_MID,
                    align: 'right',
                    lineSpacing: 2,
                }
            ).setOrigin(1, 0).setScrollFactor(0).setDepth(DEPTH.HUD);
        }

        /**
         * Updates the lap counter display.
         * Race mode: "LAP 2/3"
         * Time Trial (totalLaps === null): "LAP 5"
         */
        updateLapCounter(currentLap, totalLaps, bestLapTime) {
            if (totalLaps === null) {
                // Time Trial: no denominator
                this.lapText.setText(`LAP ${currentLap + 1}`);
            } else {
                const displayLap = Math.min(currentLap + 1, totalLaps);
                this.lapText.setText(`LAP ${displayLap}/${totalLaps}`);
            }

            // Build the completed lap times list
            if (this.completedLapTimes.length > 0) {
                const lines = this.completedLapTimes.map((t, i) => {
                    const timeStr = formatTime(t);
                    const isBest = (bestLapTime !== null && t === bestLapTime);
                    // Best lap gets a star marker
                    return isBest ? `L${i + 1} ${timeStr} *` : `L${i + 1} ${timeStr}`;
                });
                this.lapTimesText.setText(lines.join('\n'));
                this.lapTimesText.setColor(CSS.NEUTRAL_MID);
            } else {
                this.lapTimesText.setText('');
            }
        }

        // ---------------------------------------------------------------
        // Timer Display
        // ---------------------------------------------------------------

        createTimerDisplay() {
            // Total race time (top-center)
            this.totalTimeText = this.scene.add.text(
                DESIGN_WIDTH / 2, 16, '0:00.000', {
                    fontFamily: 'Poppins, sans-serif',
                    fontSize: '20px',
                    fontStyle: '600',
                    color: CSS.STAR_WHITE,
                }
            ).setOrigin(0.5, 0).setScrollFactor(0).setDepth(DEPTH.HUD);

            // Current lap time (below total)
            this.lapTimeText = this.scene.add.text(
                DESIGN_WIDTH / 2, 42, '', {
                    fontFamily: 'monospace',
                    fontSize: '14px',
                    color: CSS.NEUTRAL_MID,
                }
            ).setOrigin(0.5, 0).setScrollFactor(0).setDepth(DEPTH.HUD);

            // Best lap time (below current lap, hidden until first lap done)
            this.bestLapText = this.scene.add.text(
                DESIGN_WIDTH / 2, 60, '', {
                    fontFamily: 'monospace',
                    fontSize: '14px',
                    color: CSS.GOLD,
                }
            ).setOrigin(0.5, 0).setScrollFactor(0).setDepth(DEPTH.HUD);

            // Continuous ghost delta display (below best lap time).
            // Shows the running time difference vs ghost: green when ahead, red behind.
            // Updated every frame based on elapsed time comparison.
            this.deltaText = this.scene.add.text(
                DESIGN_WIDTH / 2, 78, '', {
                    fontFamily: 'monospace',
                    fontSize: '14px',
                    fontStyle: 'bold',
                    color: CSS.NEON_GREEN,
                }
            ).setOrigin(0.5, 0).setScrollFactor(0).setDepth(DEPTH.HUD);
        }

        /**
         * Updates the timer display every frame.
         * Accepts the full state object from RaceScene.
         *
         * Ghost delta is checkpoint-based: computed by RaceScene when the
         * player crosses a checkpoint, passed as `ghostDelta` in state.
         * Negative = player is ahead (green). Positive = behind (red).
         *
         * @param {Object} state - Full HUD state snapshot
         */
        updateTimerDisplay(state) {
            const { raceState, raceTime, currentLapTime, bestLapTime, ghostDelta } = state;

            // Total time
            this.totalTimeText.setText(formatTime(raceTime));

            // Current lap time
            if (raceState === RACE_STATE.RACING) {
                this.lapTimeText.setText(`LAP  ${formatTime(currentLapTime)}`);
            } else if (raceState === RACE_STATE.COUNTDOWN) {
                this.lapTimeText.setText('');
            }

            // Best lap time display
            if (bestLapTime !== null) {
                this.bestLapText.setText(`BEST ${formatTime(bestLapTime)}`);
            } else {
                this.bestLapText.setText('');
            }

            // Ghost delta (updated at checkpoint crossings by RaceScene)
            if (raceState === RACE_STATE.RACING && ghostDelta !== null && ghostDelta !== undefined) {
                const ahead = ghostDelta < 0;
                const sign = ahead ? '-' : '+';
                const color = ahead ? CSS.NEON_GREEN : '#e43b44';

                this.deltaText.setText(`${sign}${Math.abs(ghostDelta).toFixed(3)}`);
                this.deltaText.setColor(color);

                // Pulse best lap text when player is ahead of ghost
                if (ahead && bestLapTime !== null) {
                    const pulse = 0.85 + Math.sin(raceTime * 4) * 0.15;
                    this.bestLapText.setAlpha(pulse);
                } else {
                    this.bestLapText.setAlpha(1.0);
                }
            } else {
                this.deltaText.setText('');
                this.bestLapText.setAlpha(1.0);
            }
        }

        // ---------------------------------------------------------------
        // Minimap
        // ---------------------------------------------------------------

        createMinimap() {
            // Track outline (static, created once via TrackGenerator)
            const result = TrackGen.renderMinimap(
                this.scene, this.trackData,
                MINIMAP_X, MINIMAP_Y, MINIMAP_SIZE, MINIMAP_SIZE
            );
            this.minimapGfx = result.graphics;
            this.minimapMapData = result.mapData;
            this.minimapGfx.setScrollFactor(0);
            this.minimapGfx.setDepth(DEPTH.MINIMAP);

            // Dynamic overlay for checkpoint dots and player dot.
            // Redrawn each frame. Separate from the static track so we
            // don't redraw 100+ line segments every frame.
            this.minimapOverlay = this.scene.add.graphics();
            this.minimapOverlay.setScrollFactor(0);
            this.minimapOverlay.setDepth(DEPTH.MINIMAP_OVERLAY);
        }

        updateMinimap(shipX, shipY, onTrack, nextCheckpoint, allCheckpointsPassed, ghosts, raceTime) {
            const g = this.minimapOverlay;
            g.clear();

            const map = this.minimapMapData;
            if (!map) return;

            const checkpoints = this.trackData.checkpoints;

            // --- Checkpoint dots ---
            for (let i = 1; i < checkpoints.length; i++) {
                const cp = checkpoints[i];
                const cx = map.centerX + (cp.position.x - map.trackCenterX) * map.mapScale;
                const cy = map.centerY + (cp.position.y - map.trackCenterY) * map.mapScale;

                let alpha, radius;
                if (i === nextCheckpoint && !allCheckpointsPassed) {
                    // Next target: brightest and largest
                    alpha = 1.0;
                    radius = 3;
                } else if (this.crossedCheckpoints.has(i)) {
                    // Already crossed this lap: very dim
                    alpha = 0.1;
                    radius = 2;
                } else {
                    // Uncrossed (not yet reached): medium brightness
                    alpha = 0.3;
                    radius = 2;
                }

                g.fillStyle(COLOR.NEON_GREEN, alpha);
                g.fillCircle(cx, cy, radius);
            }

            // --- Start/finish highlight when it's the next target ---
            // The static gold dot is drawn by renderMinimap. When all
            // intermediate checkpoints are crossed and the player needs
            // to reach the start/finish to complete the lap, we draw a
            // pulsing gold ring around it as a visual cue.
            if (allCheckpointsPassed && nextCheckpoint === 0) {
                const startCP = checkpoints[0];
                const sx = map.centerX + (startCP.position.x - map.trackCenterX) * map.mapScale;
                const sy = map.centerY + (startCP.position.y - map.trackCenterY) * map.mapScale;

                // Gentle pulse: ring radius oscillates between 5 and 7
                const pulse = 6 + Math.sin(raceTime * 5) * 1;
                g.lineStyle(1.5, COLOR.GOLD, 0.8);
                g.strokeCircle(sx, sy, pulse);
            }

            // --- Ghost dots (drawn before player so player is always on top) ---
            if (ghosts) {
                for (let gi = 0; gi < ghosts.length; gi++) {
                    const ghost = ghosts[gi];
                    const gx = map.centerX + (ghost.x - map.trackCenterX) * map.mapScale;
                    const gy = map.centerY + (ghost.y - map.trackCenterY) * map.mapScale;
                    g.fillStyle(ghost.color, 0.6);
                    g.fillCircle(gx, gy, 3);
                }
            }

            // --- Player dot ---
            const dotX = map.centerX + (shipX - map.trackCenterX) * map.mapScale;
            const dotY = map.centerY + (shipY - map.trackCenterY) * map.mapScale;

            g.fillStyle(0xffffff, 1.0);
            g.fillCircle(dotX, dotY, 4);

            // Off-track warning ring
            if (!onTrack) {
                g.lineStyle(1, COLOR.RED, 0.8);
                g.strokeCircle(dotX, dotY, 6);
            }
        }

        // ---------------------------------------------------------------
        // CC Tier Label
        // ---------------------------------------------------------------

        createCCTierLabel() {
            this.tierText = this.scene.add.text(
                SPEED_BAR_X, SPEED_BAR_Y + SPEED_BAR_H + 4, '', {
                    fontFamily: 'Inter, sans-serif',
                    fontSize: '12px',
                    color: CSS.STEEL_DARK,
                }
            ).setScrollFactor(0).setDepth(DEPTH.HUD);
        }

        // ---------------------------------------------------------------
        // Public API: Frame Update
        // ---------------------------------------------------------------

        /**
         * Updates all HUD elements for the current frame.
         * Called by RaceScene.update() with a plain state snapshot.
         *
         * @param {Object} state - Current game state
         * @param {number} state.speed - Ship speed in px/s
         * @param {number} state.shipX - Ship world X position
         * @param {number} state.shipY - Ship world Y position
         * @param {boolean} state.onTrack - Whether ship is on the track
         * @param {string} state.raceState - 'COUNTDOWN' | 'RACING' | 'FINISHED'
         * @param {number} state.currentLap - Current lap number (0-based during race)
         * @param {number|null} state.totalLaps - Total laps in race (null for TT)
         * @param {number} state.raceTime - Total elapsed race time in seconds
         * @param {number} state.currentLapTime - Current lap elapsed time in seconds
         * @param {number|null} state.bestLapTime - Best completed lap time, or null
         * @param {number} state.nextCheckpoint - Index of next expected checkpoint
         * @param {boolean} state.allCheckpointsPassed - Whether all CPs are crossed
         * @param {string} state.ccTier - Current CC tier name
         * @param {Array<{x:number,y:number,color:number}>|null} state.ghosts - Ghost positions for minimap
         * @param {number|null} state.ghostDelta - Checkpoint-based ghost delta (positive=behind, negative=ahead), or null
         */
        update(state) {
            this.updateSpeedBar(state.speed);
            this.updateLapCounter(state.currentLap, state.totalLaps, state.bestLapTime);
            this.updateTimerDisplay(state);
            this.updateMinimap(
                state.shipX, state.shipY, state.onTrack,
                state.nextCheckpoint, state.allCheckpointsPassed,
                state.ghosts, state.raceTime
            );
            this.tierText.setText(state.ccTier);
        }

        // ---------------------------------------------------------------
        // Public API: Events
        // ---------------------------------------------------------------

        /**
         * Called when a checkpoint is correctly crossed.
         * Updates the minimap dot state.
         *
         * @param {number} index - Checkpoint index that was crossed
         */
        onCheckpointCrossed(index) {
            this.crossedCheckpoints.add(index);
        }

        /**
         * Called when a lap is completed.
         * Triggers the lap counter animation and clears checkpoint state.
         *
         * @param {number} lapNumber - The lap that was just completed (1-based)
         * @param {number} lapTime - The lap's time in seconds
         */
        onLapComplete(lapNumber, lapTime) {
            // Track completed lap time for display
            this.completedLapTimes.push(lapTime);

            // Clear crossed checkpoints for the new lap
            this.crossedCheckpoints.clear();

            // Lap counter animation: scale up 120% and flash cyan
            // Kill any existing tweens first (Step 5 lesson)
            this.scene.tweens.killTweensOf(this.lapText);

            // Flash the lap text cyan
            this.lapText.setColor(CSS.CYAN_GLOW);
            this.lapText.setScale(1.2);

            this.scene.tweens.add({
                targets: this.lapText,
                scaleX: 1.0,
                scaleY: 1.0,
                duration: 500,
                ease: 'Power2',
                onComplete: () => {
                    if (this.lapText) this.lapText.setColor(CSS.STAR_WHITE);
                },
            });
        }

        // ---------------------------------------------------------------
        // Cleanup
        // ---------------------------------------------------------------

        /**
         * Destroys all Phaser objects owned by the HUD.
         * Must be called when the scene shuts down.
         */
        destroy() {
            // Text objects
            if (this.spdLabel) { this.spdLabel.destroy(); this.spdLabel = null; }
            if (this.speedText) { this.speedText.destroy(); this.speedText = null; }
            if (this.lapText) { this.lapText.destroy(); this.lapText = null; }
            if (this.lapTimesText) { this.lapTimesText.destroy(); this.lapTimesText = null; }
            if (this.totalTimeText) { this.totalTimeText.destroy(); this.totalTimeText = null; }
            if (this.lapTimeText) { this.lapTimeText.destroy(); this.lapTimeText = null; }
            if (this.bestLapText) { this.bestLapText.destroy(); this.bestLapText = null; }
            if (this.tierText) { this.tierText.destroy(); this.tierText = null; }
            if (this.deltaText) { this.deltaText.destroy(); this.deltaText = null; }

            // Graphics objects
            if (this.speedBarGfx) { this.speedBarGfx.destroy(); this.speedBarGfx = null; }
            if (this.minimapGfx) { this.minimapGfx.destroy(); this.minimapGfx = null; }
            if (this.minimapOverlay) { this.minimapOverlay.destroy(); this.minimapOverlay = null; }

            this.scene = null;
        }
    }

    // ===================================================================
    // PUBLIC API
    // ===================================================================

    PlatPursuit.Games.Driver.HUD = HUD;

    console.log('[Stellar Circuit] HUD loaded');
})();
