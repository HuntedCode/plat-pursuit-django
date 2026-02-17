/**
 * PlatPursuit.Games.Driver.Ghost
 *
 * Ghost recording and playback system for Stellar Circuit.
 * Records the player's run at 10fps and plays it back as a
 * semi-transparent ship for future races on the same track.
 *
 * Classes:
 * - GhostRecorder: Samples ship state during a race
 * - GhostPlayback: Interpolates recorded data for smooth playback
 * - GhostStorage: localStorage persistence (prototype only)
 *
 * Visual spec:
 * - Same 5-vertex arrow shape as player ship
 * - Fill only (no stroke, no glow, no particles)
 * - White (#ffffff) at 30% opacity, pulsing 25-35%
 * - Minimap dot: 3px, white, 50% opacity
 */

window.PlatPursuit = window.PlatPursuit || {};
window.PlatPursuit.Games = window.PlatPursuit.Games || {};
window.PlatPursuit.Games.Driver = window.PlatPursuit.Games.Driver || {};

(function() {
    'use strict';

    // ===================================================================
    // CONSTANTS
    // ===================================================================

    /** Recording sample rate: one frame every 100ms (10fps) */
    const SAMPLE_INTERVAL = 0.1;

    /** Values stored per frame in the flat array: x, y, rotation, thrusting, speed */
    const VALUES_PER_FRAME = 5;

    /** Ghost ship colors. Index 0 = personal best. */
    const GHOST_COLORS = [
        0xffffff,  // White (Ghost 1 / personal best)
        0xb55088,  // Pink (Ghost 2, future)
        0x0099db,  // Blue (Ghost 3, future)
    ];

    // ===================================================================
    // ANGLE INTERPOLATION
    // ===================================================================

    /**
     * Linearly interpolates between two angles using the shortest arc.
     * Handles wrapping around PI/-PI correctly.
     *
     * @param {number} a - Start angle (radians)
     * @param {number} b - End angle (radians)
     * @param {number} t - Interpolation factor (0-1)
     * @returns {number} Interpolated angle
     */
    function lerpAngle(a, b, t) {
        const raw = b - a;
        const diff = ((raw + Math.PI) % (2 * Math.PI) + 2 * Math.PI) % (2 * Math.PI) - Math.PI;
        return a + diff * t;
    }

    // ===================================================================
    // GHOST RECORDER
    // ===================================================================

    /**
     * Records ship state at 10fps into a flat array.
     *
     * Called every frame by RaceScene during the RACING state.
     * Accumulates time and only samples when the interval is reached,
     * so it works correctly at any frame rate.
     *
     * Flat array format: [x, y, rotation, thrusting, normalizedSpeed, ...]
     * where each group of 5 values is one frame.
     */
    class GhostRecorder {
        constructor() {
            this.frames = [];
            this.elapsed = 0;
            /** Frame indices where each lap starts. Index 0 = start of lap 1. */
            this.lapBoundaries = [0];
        }

        /**
         * Accumulates time and records a frame when the interval is reached.
         *
         * @param {number} dt - Delta time in seconds
         * @param {Object} ship - Ship instance (reads x, y, rotation, speed, tier.maxSpeed)
         */
        update(dt, ship) {
            this.elapsed += dt;

            if (this.elapsed >= SAMPLE_INTERVAL) {
                this.elapsed -= SAMPLE_INTERVAL;
                this.frames.push(
                    ship.x,
                    ship.y,
                    ship.rotation,
                    ship.speed > 10 ? 1 : 0,
                    Math.min(ship.speed / ship.tier.maxSpeed, 1)
                );
            }
        }

        /**
         * Marks the current frame as the start of a new lap.
         * Called by RaceScene when the player crosses the start/finish line.
         */
        markLapBoundary() {
            this.lapBoundaries.push(this.getFrameCount());
        }

        /**
         * Extracts the flat frame array for a single lap.
         *
         * @param {number} lapIndex - Zero-based lap index
         * @returns {number[]} Flat array slice for that lap
         */
        getLapFrames(lapIndex) {
            const startFrame = this.lapBoundaries[lapIndex] || 0;
            const endFrame = (lapIndex + 1 < this.lapBoundaries.length)
                ? this.lapBoundaries[lapIndex + 1]
                : this.getFrameCount();
            return this.frames.slice(
                startFrame * VALUES_PER_FRAME,
                endFrame * VALUES_PER_FRAME
            );
        }

        /** Returns the flat frame array */
        getFrames() {
            return this.frames;
        }

        /** Returns the number of recorded frames */
        getFrameCount() {
            return this.frames.length / VALUES_PER_FRAME;
        }
    }

    // ===================================================================
    // GHOST PLAYBACK
    // ===================================================================

    /**
     * Plays back recorded ghost data with linear interpolation.
     *
     * Interpolates between 10fps recorded frames to produce smooth
     * positions at any frame rate. Handles angle wrapping via lerpAngle.
     *
     * When the ghost's recording ends, it snaps to its final position
     * and fades out (alpha = 0).
     */
    class GhostPlayback {
        /**
         * @param {number[]} frames - Flat array of recorded frames
         * @param {number} color - Phaser hex color for rendering
         */
        constructor(frames, color) {
            this.frames = frames;
            this.totalFrames = frames.length / VALUES_PER_FRAME;
            this.color = color;
            this._initState();
        }

        /** Resets interpolation state to frame 0. Called from constructor and reset(). */
        _initState() {
            this.elapsed = 0;
            this.finished = false;
            this.x = this.frames[0] || 0;
            this.y = this.frames[1] || 0;
            this.rotation = this.frames[2] || 0;
            this.speed = this.frames[4] || 0;
            this.alpha = 0.3;
        }

        /**
         * Advances playback and interpolates position.
         *
         * @param {number} dt - Delta time in seconds
         */
        update(dt) {
            if (this.finished) return;

            this.elapsed += dt;
            const frameIndex = this.elapsed / SAMPLE_INTERVAL;
            const i = Math.floor(frameIndex);
            const t = frameIndex - i;

            if (i >= this.totalFrames - 1) {
                // Ghost recording has ended
                this.finished = true;
                const last = (this.totalFrames - 1) * VALUES_PER_FRAME;
                this.x = this.frames[last];
                this.y = this.frames[last + 1];
                this.rotation = this.frames[last + 2];
                this.speed = this.frames[last + 4];
                this.alpha = 0;
                return;
            }

            // Interpolate between frame i and frame i+1
            const a = i * VALUES_PER_FRAME;
            const b = (i + 1) * VALUES_PER_FRAME;

            this.x = this.frames[a] + (this.frames[b] - this.frames[a]) * t;
            this.y = this.frames[a + 1] + (this.frames[b + 1] - this.frames[a + 1]) * t;
            this.rotation = lerpAngle(this.frames[a + 2], this.frames[b + 2], t);
            this.speed = this.frames[a + 4] + (this.frames[b + 4] - this.frames[a + 4]) * t;

            // Pulsing opacity: 25-35% via sine wave
            this.alpha = 0.30 + Math.sin(this.elapsed * 3) * 0.05;
        }

        /** Resets playback to the beginning (used for retry / new lap in TT) */
        reset() {
            this._initState();
        }
    }

    // ===================================================================
    // GHOST STORAGE (localStorage)
    // ===================================================================

    /**
     * Persistence layer for ghost data using localStorage.
     *
     * Key pattern: stellar_circuit_{seed}_{mode}_{ccTier}
     * Stores one ghost per seed+mode+tier combination. Each CC tier
     * gets its own ghost since speeds differ dramatically.
     *
     * Stored data format:
     *   frames: Number[]     - Flat array [x,y,r,t,s, ...]
     *   totalTimeMs: Number  - Total race time in milliseconds
     *   lapTimes: Number[]   - Per-lap times in seconds
     *   bestLapMs: Number    - Best lap time in milliseconds
     *   recordedAt: String   - ISO 8601 date
     *   version: Number      - Format version (for future migration)
     */
    const GhostStorage = {
        /**
         * Builds the localStorage key for a seed+mode+tier combination.
         *
         * @param {string} seed - Track seed
         * @param {string} mode - Game mode ('race' or 'timetrial')
         * @param {string} ccTier - CC tier ('50cc', '100cc', '200cc')
         * @returns {string} localStorage key
         */
        _key(seed, mode, ccTier) {
            return `stellar_circuit_${seed}_${mode}_${ccTier}`;
        },

        /**
         * Saves ghost data to localStorage.
         * Silently fails if localStorage is full or unavailable.
         *
         * @param {string} seed - Track seed
         * @param {string} mode - Game mode
         * @param {string} ccTier - CC tier
         * @param {Object} data - Ghost data to store
         */
        save(seed, mode, ccTier, data) {
            try {
                localStorage.setItem(this._key(seed, mode, ccTier), JSON.stringify(data));
            } catch (e) {
                console.warn('[Ghost] localStorage save failed:', e.message);
            }
        },

        /**
         * Loads ghost data from localStorage.
         *
         * @param {string} seed - Track seed
         * @param {string} mode - Game mode
         * @param {string} ccTier - CC tier
         * @returns {Object|null} Ghost data or null if not found
         */
        load(seed, mode, ccTier) {
            try {
                const raw = localStorage.getItem(this._key(seed, mode, ccTier));
                return raw ? JSON.parse(raw) : null;
            } catch (e) {
                console.warn('[Ghost] localStorage load failed:', e.message);
                return null;
            }
        },

        /**
         * Gets the best time for a seed+mode+tier without loading full data.
         *
         * @param {string} seed - Track seed
         * @param {string} mode - Game mode
         * @param {string} ccTier - CC tier
         * @returns {number|null} Total time in ms, or null
         */
        getBestTime(seed, mode, ccTier) {
            const data = this.load(seed, mode, ccTier);
            return data ? data.totalTimeMs : null;
        },

        /**
         * Gets the best lap time for a seed+mode+tier.
         * Primarily used for Time Trial display in the menu.
         *
         * @param {string} seed - Track seed
         * @param {string} mode - Game mode
         * @param {string} ccTier - CC tier
         * @returns {number|null} Best lap time in ms, or null
         */
        getBestLapTime(seed, mode, ccTier) {
            const data = this.load(seed, mode, ccTier);
            return data ? data.bestLapMs : null;
        },

        /**
         * Removes all Stellar Circuit ghost data from localStorage.
         */
        clear() {
            const keys = [];
            for (let i = 0; i < localStorage.length; i++) {
                const key = localStorage.key(i);
                if (key && key.startsWith('stellar_circuit_')) {
                    keys.push(key);
                }
            }
            keys.forEach(k => localStorage.removeItem(k));
        },
    };

    // ===================================================================
    // GHOST RENDERER
    // ===================================================================

    /**
     * Draws a ghost ship on a Phaser Graphics object.
     *
     * Uses the same 5-vertex arrow polygon as the player ship but
     * with simplified rendering: fill only, no stroke, no glow,
     * no particles. The ghost color and alpha come from the
     * GhostPlayback instance.
     *
     * @param {Phaser.GameObjects.Graphics} graphics - Target graphics
     * @param {GhostPlayback} playback - Ghost playback instance
     * @param {Array<{x:number, y:number}>} vertices - Ship polygon vertices
     */
    function drawGhost(graphics, playback, vertices) {
        graphics.clear();

        if (!playback || playback.finished || playback.alpha <= 0) return;

        graphics.setPosition(playback.x, playback.y);
        graphics.setRotation(playback.rotation);

        // Fill only: no stroke, no glow (GDD 4.8)
        graphics.fillStyle(playback.color, playback.alpha);
        graphics.beginPath();
        graphics.moveTo(vertices[0].x, vertices[0].y);
        for (let i = 1; i < vertices.length; i++) {
            graphics.lineTo(vertices[i].x, vertices[i].y);
        }
        graphics.closePath();
        graphics.fillPath();
    }

    // ===================================================================
    // PUBLIC API
    // ===================================================================

    PlatPursuit.Games.Driver.Ghost = {
        GhostRecorder,
        GhostPlayback,
        GhostStorage,
        drawGhost,
        lerpAngle,
        GHOST_COLORS,
        VALUES_PER_FRAME,
        SAMPLE_INTERVAL,
    };

    console.log('[Stellar Circuit] Ghost loaded');
})();
