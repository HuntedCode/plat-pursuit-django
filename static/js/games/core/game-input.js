/**
 * PlatPursuit.Games.Input
 *
 * Shared input abstraction layer for all mini-games.
 * Normalizes keyboard and touch input into a unified state object
 * so game code doesn't need to care about the input method.
 *
 * Responsibilities:
 * - Detect input method (keyboard vs touch)
 * - Provide InputManager class that returns normalized state:
 *   { thrust, brake, left, right, steerAmount }
 * - Handle virtual joystick for mobile (future)
 */

window.PlatPursuit = window.PlatPursuit || {};
window.PlatPursuit.Games = window.PlatPursuit.Games || {};

// Placeholder: full implementation in Step 2
PlatPursuit.Games.Input = {};

console.log('[Stellar Circuit] Input loaded');
