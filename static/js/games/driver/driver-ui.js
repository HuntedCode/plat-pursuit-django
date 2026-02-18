/**
 * PlatPursuit.Games.Driver.UI
 *
 * Shared UI constants and utilities for Stellar Circuit scenes.
 * Centralizes the color palette, depth layers, race state enum,
 * time formatting, and interactive button factory that were
 * previously duplicated across menu-scene, race-scene, results-scene,
 * and the HUD module.
 *
 * All colors come from gamification-design.md (neon vector / space-faring
 * aesthetic). The button factory produces Phaser Graphics + Text + Zone
 * composites with built-in hover, selected, and disabled states.
 *
 * Loaded before scene files; scenes alias what they need:
 *   const { COLOR, CSS, createButton } = PlatPursuit.Games.Driver.UI;
 */

window.PlatPursuit = window.PlatPursuit || {};
window.PlatPursuit.Games = window.PlatPursuit.Games || {};
window.PlatPursuit.Games.Driver = window.PlatPursuit.Games.Driver || {};

(function() {
    'use strict';

    // ===================================================================
    // COLOR PALETTE (gamification-design.md)
    // ===================================================================

    /** Phaser-native hex integers for Graphics API calls */
    const COLOR = {
        CYAN_GLOW:    0x2ce8f5,
        STAR_WHITE:   0xe8e8f0,
        NEUTRAL_MID:  0x6b6b8d,
        NEUTRAL_DARK: 0x3a3a5c,
        STEEL_DARK:   0x4a5568,
        GOLD:         0xd4a017,
        DEEP_SPACE_1: 0x0a0a14,
        DEEP_SPACE_2: 0x141428,
        NEON_GREEN:   0x40e850,
        RED:          0xe43b44,
    };

    /** CSS hex strings for Phaser Text objects (which need string colors) */
    const CSS = {
        CYAN_GLOW:    '#2ce8f5',
        STAR_WHITE:   '#e8e8f0',
        NEUTRAL_MID:  '#6b6b8d',
        STEEL_DARK:   '#4a5568',
        GOLD:         '#d4a017',
        NEON_GREEN:   '#40e850',
        RED:          '#e43b44',
    };

    // ===================================================================
    // DEPTH LAYERS
    // ===================================================================
    // Shared depth values for UI elements. Used by HUD for minimap and
    // speed bar, and by RaceScene for countdown/warning overlays.

    const DEPTH = {
        MINIMAP:         90,
        MINIMAP_OVERLAY: 91,
        HUD:             95,
        HUD_TEXT:         96,
        OVERLAY:         100,
    };

    // ===================================================================
    // RACE STATE MACHINE
    // ===================================================================
    // Single definition used by both RaceScene and HUD to stay in sync.

    const RACE_STATE = {
        COUNTDOWN: 'COUNTDOWN',
        RACING:    'RACING',
        PAUSED:    'PAUSED',
        FINISHED:  'FINISHED',
    };

    // ===================================================================
    // TIME FORMATTING
    // ===================================================================

    /**
     * Formats a time in seconds to M:SS.mmm display string.
     *
     * Uses Math.floor for milliseconds to avoid the edge case where
     * Math.round would produce "1000" ms (e.g., 59.9995s displaying
     * as "0:59.1000" instead of "1:00.000").
     *
     * @param {number} seconds - Time in seconds
     * @returns {string} Formatted time string (e.g., "1:23.456")
     */
    function formatTime(seconds) {
        const mins = Math.floor(seconds / 60);
        const secs = seconds % 60;
        const wholeSecs = Math.floor(secs);
        const ms = Math.floor((secs - wholeSecs) * 1000);
        return `${mins}:${String(wholeSecs).padStart(2, '0')}.${String(ms).padStart(3, '0')}`;
    }

    // ===================================================================
    // BUTTON FACTORY
    // ===================================================================

    /**
     * Creates an interactive button from Phaser Graphics + Text + Zone.
     *
     * Returns a control object with methods to update visual state.
     * The Zone provides the hit area; Graphics draws the visual;
     * Text shows the label. Hover and click effects are built in.
     *
     * @param {Phaser.Scene} scene - The scene to add objects to
     * @param {number} x - Center X position
     * @param {number} y - Center Y position
     * @param {string} label - Button text
     * @param {Object} opts - Options
     * @param {number} [opts.width=140] - Button width
     * @param {number} [opts.height=36] - Button height
     * @param {number} [opts.fontSize=14] - Font size in px
     * @param {boolean} [opts.disabled=false] - Whether button starts disabled
     * @param {Function} [opts.onClick] - Click handler
     * @returns {Object} Button control: { gfx, text, zone, setSelected, setDisabled, destroy }
     */
    function createButton(scene, x, y, label, opts = {}) {
        const w = opts.width || 140;
        const h = opts.height || 36;
        const fontSize = opts.fontSize || 14;
        let disabled = opts.disabled || false;
        let selected = false;
        let hovered = false;

        // Graphics for background + border
        const gfx = scene.add.graphics();
        gfx.setScrollFactor(0);

        // Text label
        const text = scene.add.text(x, y, label, {
            fontFamily: 'Poppins, sans-serif',
            fontSize: `${fontSize}px`,
            fontStyle: '600',
            color: disabled ? CSS.STEEL_DARK : CSS.STAR_WHITE,
        }).setOrigin(0.5).setScrollFactor(0);

        // Invisible interactive zone for pointer events
        const zone = scene.add.zone(x, y, w, h)
            .setInteractive({ useHandCursor: !disabled });

        // Draw the button in its current state
        function draw() {
            gfx.clear();

            let fillColor = COLOR.DEEP_SPACE_2;
            let borderColor = COLOR.NEUTRAL_DARK;
            let borderAlpha = 1.0;

            if (disabled) {
                fillColor = COLOR.DEEP_SPACE_2;
                borderColor = COLOR.STEEL_DARK;
                borderAlpha = 0.5;
            } else if (selected || hovered) {
                borderColor = COLOR.CYAN_GLOW;
            }

            // Fill
            gfx.fillStyle(fillColor, 1.0);
            gfx.fillRoundedRect(x - w / 2, y - h / 2, w, h, 4);

            // Border
            gfx.lineStyle(1.5, borderColor, borderAlpha);
            gfx.strokeRoundedRect(x - w / 2, y - h / 2, w, h, 4);
        }

        draw();

        // Pointer events
        zone.on('pointerover', () => {
            if (disabled) return;
            hovered = true;
            draw();
            scene.input.setDefaultCursor('pointer');
        });

        zone.on('pointerout', () => {
            hovered = false;
            draw();
            scene.input.setDefaultCursor('default');
        });

        zone.on('pointerdown', () => {
            if (disabled) return;
            if (opts.onClick) opts.onClick();
        });

        return {
            gfx,
            text,
            zone,
            setSelected(val) {
                selected = val;
                draw();
            },
            setDisabled(val) {
                disabled = val;
                zone.setInteractive({ useHandCursor: !disabled });
                text.setColor(disabled ? CSS.STEEL_DARK : CSS.STAR_WHITE);
                draw();
            },
            destroy() {
                gfx.destroy();
                text.destroy();
                zone.destroy();
            },
        };
    }

    // ===================================================================
    // PUBLIC API
    // ===================================================================

    PlatPursuit.Games.Driver.UI = {
        COLOR,
        CSS,
        DEPTH,
        RACE_STATE,
        formatTime,
        createButton,
    };

    console.log('[Stellar Circuit] Driver UI loaded');
})();
