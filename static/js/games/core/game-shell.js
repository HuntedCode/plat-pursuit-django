/**
 * PlatPursuit.Games.Shell
 *
 * Shared game shell providing Phaser configuration factory and constants.
 * This module is used by ALL mini-games, not just Stellar Circuit.
 *
 * Why this exists:
 * Every mini-game needs a Phaser config object with the same design resolution,
 * scaling mode, render settings, and background color. Rather than duplicate
 * this boilerplate 25 times, each game calls Shell.createConfig() with only
 * the properties it needs to override (typically just `scene`).
 *
 * Responsibilities:
 * - Define the shared design resolution (1280x720, 16:9)
 * - Provide createConfig() factory that returns a fresh Phaser config object
 * - Deep-merge game-specific overrides into sensible defaults
 */

window.PlatPursuit = window.PlatPursuit || {};
window.PlatPursuit.Games = window.PlatPursuit.Games || {};

(function() {
    'use strict';

    // -----------------------------------------------------------------------
    // Constants
    // -----------------------------------------------------------------------

    /**
     * Design resolution: the "virtual" coordinate space for all game logic.
     *
     * Every position, size, and distance in any mini-game is expressed in
     * these coordinates. Phaser's Scale Manager maps this virtual space onto
     * whatever physical screen size the player has, using Scale.FIT mode
     * (scale up/down to fill the container while keeping 16:9 aspect ratio).
     *
     * Why 1280x720?
     * - 16:9 aspect ratio matches most modern displays
     * - Large enough for detailed UIs without being wastefully large
     * - Standard HD resolution, easy to reason about
     * - Scales cleanly to 1920x1080 (1.5x) and 2560x1440 (2x)
     */
    const DESIGN_WIDTH = 1280;
    const DESIGN_HEIGHT = 720;

    /**
     * Default background color for all mini-games.
     * Deep space blue-black that matches the neon/space-faring aesthetic.
     * Individual games can override this in their createConfig() call.
     */
    const DEFAULT_BG_COLOR = '#0a0a14';

    /**
     * Default parent element ID where Phaser injects its <canvas>.
     * This should match the div ID in the game template.
     */
    const DEFAULT_PARENT = 'phaser-canvas';

    // -----------------------------------------------------------------------
    // Utility: Deep Merge
    // -----------------------------------------------------------------------

    /**
     * Recursively merges `source` into `target`, returning `target`.
     *
     * Only handles plain objects (not arrays, Dates, etc.) because Phaser
     * configs are simple nested key-value structures. If a key exists in
     * both objects and both values are plain objects, they're recursively
     * merged. Otherwise the source value overwrites the target value.
     *
     * Why not use a library? This is ~15 lines and we don't need lodash
     * for one utility function. Keeping dependencies minimal.
     *
     * @param {Object} target - The default config object (mutated in place)
     * @param {Object} source - The overrides to apply
     * @returns {Object} The merged target object
     */
    function deepMerge(target, source) {
        for (const key of Object.keys(source)) {
            const targetVal = target[key];
            const sourceVal = source[key];

            // If both are plain objects, recurse. Otherwise overwrite.
            if (isPlainObject(targetVal) && isPlainObject(sourceVal)) {
                deepMerge(targetVal, sourceVal);
            } else {
                target[key] = sourceVal;
            }
        }
        return target;
    }

    /**
     * Checks if a value is a plain object (not null, not an array, not a
     * class instance like Phaser.Scene).
     *
     * We use the constructor check because Phaser scenes, game objects, etc.
     * have Object-like prototypes but shouldn't be deep-merged. Only raw
     * config objects ({ key: value }) should be recursively traversed.
     */
    function isPlainObject(val) {
        return val !== null
            && typeof val === 'object'
            && !Array.isArray(val)
            && val.constructor === Object;
    }

    // -----------------------------------------------------------------------
    // Config Factory
    // -----------------------------------------------------------------------

    /**
     * Creates a Phaser game configuration object with PlatPursuit defaults.
     *
     * Usage in a mini-game entry point:
     *
     *   const config = PlatPursuit.Games.Shell.createConfig({
     *       scene: [MenuScene, RaceScene, ResultsScene],
     *   });
     *   const game = new Phaser.Game(config);
     *
     * The overrides object supports any valid Phaser config property.
     * Nested objects (like `scale` or `render`) are deep-merged, so you
     * can override individual sub-properties without losing the defaults:
     *
     *   // Only changes the background, keeps all other defaults:
     *   Shell.createConfig({ backgroundColor: '#1a0a2e', scene: [...] });
     *
     *   // Overrides just the design width, keeps height/mode/autoCenter:
     *   Shell.createConfig({ scale: { width: 800 }, scene: [...] });
     *
     * @param {Object} [overrides={}] - Game-specific config overrides
     * @returns {Object} Complete Phaser game configuration
     */
    function createConfig(overrides) {
        // Build defaults as a fresh object each call. If we reused a single
        // object, deep-merging into it would mutate the defaults for all
        // subsequent calls. Fresh object = no cross-contamination.
        const defaults = {
            // Renderer selection: AUTO tries WebGL first (GPU-accelerated,
            // faster for particles and effects), falls back to Canvas 2D if
            // WebGL isn't available. This covers old browsers gracefully.
            type: Phaser.AUTO,

            // The DOM element ID where Phaser creates and inserts its <canvas>.
            parent: DEFAULT_PARENT,

            // Canvas clear color. Every frame, Phaser fills the entire canvas
            // with this before drawing any game objects. Sets the visual tone.
            backgroundColor: DEFAULT_BG_COLOR,

            // Scale Manager: how the game canvas maps to the physical screen.
            scale: {
                // FIT: Scale the canvas uniformly (maintaining aspect ratio) to
                // fill as much of the parent container as possible. If the
                // container's aspect ratio doesn't match 16:9, you get
                // letterboxing (bars on the sides or top/bottom).
                mode: Phaser.Scale.FIT,

                // CENTER_BOTH: Center the canvas both horizontally and
                // vertically within its parent container. Without this, the
                // canvas would be anchored to the top-left corner.
                autoCenter: Phaser.Scale.CENTER_BOTH,

                // The "virtual" resolution. All game coordinates use this
                // system. A ship at (640, 360) is always dead center
                // regardless of the player's actual screen size.
                width: DESIGN_WIDTH,
                height: DESIGN_HEIGHT,
            },

            // Render settings: control visual quality.
            render: {
                // pixelArt: false means textures are smoothly interpolated
                // when scaled. If true, Phaser uses nearest-neighbor sampling
                // for crisp pixel art. We want smooth vector graphics.
                pixelArt: false,

                // antialias: smooths jagged "staircase" edges on diagonal and
                // curved lines. Essential for our geometric neon aesthetic
                // where clean lines are the entire visual style.
                antialias: true,
            },

            // No Phaser physics engine. We implement our own thrust-based
            // physics model for Stellar Circuit. Phaser's built-in engines
            // (Arcade, Matter.js) are designed for platformers and general
            // collision: overkill for our custom space movement.
            physics: false,

            // scenes are provided by each game via overrides.
            // If none provided, Phaser will create an empty default scene.
        };

        // Deep-merge overrides into the fresh defaults object.
        // This mutates `defaults` which is fine since it's a local variable.
        if (overrides) {
            deepMerge(defaults, overrides);
        }

        return defaults;
    }

    // -----------------------------------------------------------------------
    // Public API
    // -----------------------------------------------------------------------

    PlatPursuit.Games.Shell = {
        /** Design width in virtual pixels. Used by games for layout math. */
        DESIGN_WIDTH: DESIGN_WIDTH,

        /** Design height in virtual pixels. */
        DESIGN_HEIGHT: DESIGN_HEIGHT,

        /** Creates a Phaser config with PlatPursuit defaults + overrides. */
        createConfig: createConfig,
    };

    console.log('[PlatPursuit] Game Shell loaded');
})();
