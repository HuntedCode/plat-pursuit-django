/**
 * PlatPursuit.Games.Shell
 *
 * Shared game shell providing Phaser configuration factory and constants.
 * This module is used by ALL mini-games, not just Stellar Circuit.
 *
 * Responsibilities:
 * - Provide a createConfig() factory that returns a Phaser config object
 *   with sensible defaults (scaling, background, render settings)
 * - Define shared constants (design resolution)
 * - Ensure consistent game setup across all 25 future mini-games
 */

window.PlatPursuit = window.PlatPursuit || {};
window.PlatPursuit.Games = window.PlatPursuit.Games || {};

// Placeholder: full implementation in Step 2
PlatPursuit.Games.Shell = {
    DESIGN_WIDTH: 1280,
    DESIGN_HEIGHT: 720,
};

console.log('[Stellar Circuit] Shell loaded');
