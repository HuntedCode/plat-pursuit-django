/**
 * PlatPursuit.Games.Input
 *
 * Shared input abstraction layer for all mini-games.
 *
 * Why this exists:
 * Game logic should ask "is the player pressing up?" not "is the W key down?"
 * This decoupling lets us:
 * 1. Support multiple input methods (keyboard now, touch in Step 10)
 * 2. Change key bindings without touching game logic
 * 3. Keep all input-related code in one place
 *
 * Design philosophy: GENERIC directional names.
 * The shared InputManager returns { up, down, left, right } since these
 * are universal across all game types. Each game interprets them:
 *   - Stellar Circuit: up = thrust, down = brake
 *   - A puzzle game:   up/down/left/right = move cursor
 *   - A platformer:    up = jump, left/right = move
 *
 * The game-specific meaning lives in the game code, not here.
 *
 * Architecture:
 * - InputManager is created PER SCENE (each scene gets its own instance)
 * - It registers Phaser Key objects for the directional keys
 * - Each frame, the scene calls getState() to get normalized input
 * - The game logic only sees { up, down, left, right }
 * - When the scene shuts down, destroy() releases key captures
 *
 * Keyboard mapping:
 *   Up:    W  or  Up Arrow
 *   Down:  S  or  Down Arrow
 *   Left:  A  or  Left Arrow
 *   Right: D  or  Right Arrow
 *   Pause: Escape (edge-detected via isPausePressed())
 */

window.PlatPursuit = window.PlatPursuit || {};
window.PlatPursuit.Games = window.PlatPursuit.Games || {};

(function() {
    'use strict';

    // -----------------------------------------------------------------------
    // InputManager Class
    // -----------------------------------------------------------------------

    /**
     * Manages keyboard (and eventually touch) input for a single Phaser scene.
     *
     * Returns generic directional state: { up, down, left, right }.
     * Each game decides what these directions mean for its gameplay.
     *
     * Lifecycle:
     *   1. Scene creates InputManager in its create() method
     *   2. Scene calls getState() every frame in update()
     *   3. Scene calls destroy() in its shutdown/destroy handler
     *
     * Why per-scene?
     * Phaser's input plugin belongs to each scene. When scenes sleep or stop,
     * their input plugins deactivate. By tying our InputManager to a scene,
     * cleanup happens naturally with scene lifecycle.
     *
     * @example
     *   class RaceScene extends Phaser.Scene {
     *       create() {
     *           this.inputManager = new PlatPursuit.Games.Input.InputManager(this);
     *       }
     *       update(time, delta) {
     *           const input = this.inputManager.getState();
     *           // Stellar Circuit interprets: up = thrust, down = brake
     *           this.ship.update(delta / 1000, input, trackData);
     *       }
     *       shutdown() {
     *           this.inputManager.destroy();
     *       }
     *   }
     */
    class InputManager {
        /**
         * @param {Phaser.Scene} scene - The scene this input manager belongs to.
         *   We need the scene reference to access its keyboard plugin and to
         *   register key captures.
         */
        constructor(scene) {
            this.scene = scene;

            // Grab Phaser's key code constants now that Phaser is loaded.
            const KeyCodes = Phaser.Input.Keyboard.KeyCodes;

            // Store the key codes we use, so destroy() can release captures.
            // This array is the single source of truth for "which keys does
            // the game intercept from the browser."
            this.capturedKeyCodes = [
                KeyCodes.W, KeyCodes.A, KeyCodes.S, KeyCodes.D,
                KeyCodes.UP, KeyCodes.DOWN, KeyCodes.LEFT, KeyCodes.RIGHT,
                KeyCodes.ESC,
            ];

            // ---------------------------------------------------------------
            // Register Key Objects
            // ---------------------------------------------------------------
            //
            // scene.input.keyboard.addKey(keyCode, enableCapture, emitOnRepeat)
            //
            // - keyCode: Which physical key to track
            // - enableCapture (true): Calls preventDefault() on the browser
            //   event, preventing default behavior. Without this:
            //   * Arrow keys would scroll the page
            //   * Space would scroll the page
            //   * Tab would move focus away from the game
            // - emitOnRepeat (false): Whether to fire 'keydown' events
            //   repeatedly when the key is held. We don't use events (we poll),
            //   so this doesn't matter, but false is the sensible default.
            //
            // Each addKey() call returns a Phaser.Input.Keyboard.Key object
            // with an .isDown property that we poll each frame.

            const kb = scene.input.keyboard;

            // Up: W or Up Arrow
            // Both keys map to the same action. In getState(), we OR them
            // together: if EITHER is held, 'up' is active.
            this.keyW = kb.addKey(KeyCodes.W, true, false);
            this.keyUp = kb.addKey(KeyCodes.UP, true, false);

            // Down: S or Down Arrow
            this.keyS = kb.addKey(KeyCodes.S, true, false);
            this.keyDown = kb.addKey(KeyCodes.DOWN, true, false);

            // Left: A or Left Arrow
            this.keyA = kb.addKey(KeyCodes.A, true, false);
            this.keyLeft = kb.addKey(KeyCodes.LEFT, true, false);

            // Right: D or Right Arrow
            this.keyD = kb.addKey(KeyCodes.D, true, false);
            this.keyRight = kb.addKey(KeyCodes.RIGHT, true, false);

            // Pause: Escape
            // Unlike directional keys (polled continuously), pause is an
            // event-style action: press once to toggle. We still register
            // it as a Key object but handle it via JustDown (edge detection)
            // rather than isDown (level detection).
            this.keyEsc = kb.addKey(KeyCodes.ESC, true, false);
        }

        // -------------------------------------------------------------------
        // Polling
        // -------------------------------------------------------------------

        /**
         * Returns the current normalized input state.
         *
         * Called once per frame by the scene's update() method. Each property
         * is a boolean representing whether that direction is currently active.
         * Names are intentionally generic so any game type can use them.
         *
         * Implementation note: we OR together both key alternatives for each
         * direction. If the player holds W and Up Arrow simultaneously, it's
         * the same as holding just one. No "double input" bug.
         *
         * @returns {InputState} Current input state
         *
         * @typedef {Object} InputState
         * @property {boolean} up    - True if up key is held (W or Up Arrow)
         * @property {boolean} down  - True if down key is held (S or Down Arrow)
         * @property {boolean} left  - True if left key is held (A or Left Arrow)
         * @property {boolean} right - True if right key is held (D or Right Arrow)
         */
        getState() {
            return {
                up:    this.keyW.isDown || this.keyUp.isDown,
                down:  this.keyS.isDown || this.keyDown.isDown,
                left:  this.keyA.isDown || this.keyLeft.isDown,
                right: this.keyD.isDown || this.keyRight.isDown,
            };
        }

        /**
         * Checks if the pause key was pressed THIS FRAME (edge detection).
         *
         * Why JustDown instead of isDown?
         * isDown returns true every frame the key is held. If we used isDown
         * for pause, holding Escape for 2 frames would toggle pause on then
         * immediately off. JustDown only returns true on the first frame the
         * key transitions from "up" to "down": one press = one toggle.
         *
         * Phaser's JustDown works by checking if the key's _justDown flag is
         * set, then immediately clearing it. So the first call in a frame
         * returns true, subsequent calls return false. This means we should
         * only call isPausePressed() once per frame.
         *
         * @returns {boolean} True if Escape was pressed this frame
         */
        isPausePressed() {
            return Phaser.Input.Keyboard.JustDown(this.keyEsc);
        }

        // -------------------------------------------------------------------
        // Cleanup
        // -------------------------------------------------------------------

        /**
         * Releases all captured keys and clears references.
         *
         * Why this matters:
         * When Phaser captures a key (preventDefault), the browser can't
         * handle it normally. If we transition to the Menu scene which has
         * a seed input text field, we need arrow keys and letters to work
         * in that text field. Releasing captures restores normal behavior.
         *
         * When to call:
         * In the scene's 'shutdown' event handler. Phaser emits this when
         * a scene stops or transitions to another scene.
         *
         * @example
         *   // In scene's create():
         *   this.events.on('shutdown', () => {
         *       this.inputManager.destroy();
         *   });
         */
        destroy() {
            if (!this.scene || !this.scene.input || !this.scene.input.keyboard) {
                return;
            }

            // Release captures for all our keys so the browser handles
            // them normally again.
            this.scene.input.keyboard.removeCapture(this.capturedKeyCodes);

            // Clear references to help garbage collection and prevent
            // accidental use after destruction.
            this.scene = null;
        }
    }

    // -----------------------------------------------------------------------
    // Public API
    // -----------------------------------------------------------------------

    PlatPursuit.Games.Input = {
        /** The InputManager class. Create instances per-scene. */
        InputManager: InputManager,
    };

    console.log('[PlatPursuit] Game Input loaded');
})();
