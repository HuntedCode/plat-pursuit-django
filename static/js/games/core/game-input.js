/**
 * PlatPursuit.Games.Input
 *
 * Shared input abstraction layer for all mini-games.
 *
 * Why this exists:
 * Game logic should ask "is the player pressing up?" not "is the W key down?"
 * This decoupling lets us:
 * 1. Support multiple input methods (keyboard + touch)
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
 *
 * Touch mapping (auto-enabled on touch devices):
 *   Left half: Virtual joystick (touch = thrust, drag = steer)
 *   Right bottom: Brake zone (touch/hold = brake)
 *   Top-right: Pause button (44x44 tap target)
 */

window.PlatPursuit = window.PlatPursuit || {};
window.PlatPursuit.Games = window.PlatPursuit.Games || {};

(function() {
    'use strict';

    // -----------------------------------------------------------------------
    // Touch Controller (Mobile Virtual Controls)
    // -----------------------------------------------------------------------

    /**
     * Virtual joystick + brake zone for touch devices.
     *
     * Layout (1280x720 design space, Phaser Scale.FIT handles mapping):
     *   Left half of screen: Virtual joystick area
     *     - Touch = thrust (up: true)
     *     - Horizontal drag from touch origin: left/right steering
     *     - 20px dead zone before steering activates
     *   Right bottom quadrant: Brake zone
     *     - Touch/hold = brake (down: true)
     *   Top-right: Pause button (44x44 touch target)
     *     - Single tap = edge-detected pause press
     *
     * Multi-touch: tracks separate pointer IDs for joystick + brake.
     * Visual feedback: faint origin circle + position dot on joystick.
     */
    class TouchController {
        /**
         * @param {Phaser.Scene} scene - The active scene
         */
        constructor(scene) {
            this.scene = scene;

            const Shell = PlatPursuit.Games.Shell;
            this.designW = Shell.DESIGN_WIDTH;
            this.designH = Shell.DESIGN_HEIGHT;

            // Touch state
            this.joystickPointerId = null;
            this.joystickOrigin = null;  // { x, y } in design space
            this.joystickCurrent = null; // { x, y } in design space
            this.brakePointerId = null;
            this._pausePressed = false;

            // Dead zone for steering (pixels in design space)
            this.deadZone = 20;

            // Visual feedback graphics (UI layer, not affected by camera)
            this.gfx = scene.add.graphics();
            this.gfx.setScrollFactor(0);
            this.gfx.setDepth(85); // Below HUD (95) but above track

            // Register pointer events
            scene.input.on('pointerdown', this.onPointerDown, this);
            scene.input.on('pointermove', this.onPointerMove, this);
            scene.input.on('pointerup', this.onPointerUp, this);
            scene.input.on('pointerupoutside', this.onPointerUp, this);
        }

        /**
         * Converts a Phaser pointer to design-space coordinates.
         * Phaser's Scale.FIT handles the canvas-to-design mapping for us
         * via pointer.x / pointer.y (already in game coordinates).
         */
        _toDesign(pointer) {
            return { x: pointer.x, y: pointer.y };
        }

        /**
         * Checks if a design-space point is in the pause button zone.
         * 52x52 touch target at top-right corner (exceeds 44px minimum).
         */
        _isPauseZone(x, y) {
            return x >= this.designW - 52 && y <= 52;
        }

        /**
         * Checks if a design-space point is in the brake zone.
         * Right half, bottom 60% of screen.
         */
        _isBrakeZone(x, y) {
            return x >= this.designW / 2 && y >= this.designH * 0.4;
        }

        onPointerDown(pointer) {
            const pos = this._toDesign(pointer);

            // Pause button: top-right corner
            if (this._isPauseZone(pos.x, pos.y)) {
                this._pausePressed = true;
                return;
            }

            // Left half: joystick (thrust + steering)
            if (pos.x < this.designW / 2 && this.joystickPointerId === null) {
                this.joystickPointerId = pointer.id;
                this.joystickOrigin = { x: pos.x, y: pos.y };
                this.joystickCurrent = { x: pos.x, y: pos.y };
                return;
            }

            // Right half (non-pause): brake
            if (pos.x >= this.designW / 2 && this.brakePointerId === null) {
                if (!this._isPauseZone(pos.x, pos.y)) {
                    this.brakePointerId = pointer.id;
                }
            }
        }

        onPointerMove(pointer) {
            if (pointer.id === this.joystickPointerId) {
                const pos = this._toDesign(pointer);
                this.joystickCurrent = { x: pos.x, y: pos.y };
            }
        }

        onPointerUp(pointer) {
            if (pointer.id === this.joystickPointerId) {
                this.joystickPointerId = null;
                this.joystickOrigin = null;
                this.joystickCurrent = null;
            }
            if (pointer.id === this.brakePointerId) {
                this.brakePointerId = null;
            }
        }

        /**
         * Returns the current touch input state.
         * OR'd with keyboard state in InputManager.getState().
         */
        getState() {
            const state = { up: false, down: false, left: false, right: false };

            // Joystick: thrust + steering
            if (this.joystickOrigin && this.joystickCurrent) {
                state.up = true; // Any joystick touch = thrust

                const dx = this.joystickCurrent.x - this.joystickOrigin.x;
                if (dx < -this.deadZone) state.left = true;
                if (dx > this.deadZone) state.right = true;
            }

            // Brake zone
            if (this.brakePointerId !== null) {
                state.down = true;
            }

            return state;
        }

        /**
         * Consumes the pause pressed flag. Returns true once per press.
         */
        consumePause() {
            if (this._pausePressed) {
                this._pausePressed = false;
                return true;
            }
            return false;
        }

        /**
         * Draws visual feedback for the virtual joystick.
         * Called from InputManager or externally each frame.
         */
        draw() {
            this.gfx.clear();

            if (!this.joystickOrigin || !this.joystickCurrent) return;

            // Origin circle: faint ring at touch start
            this.gfx.lineStyle(1, 0xffffff, 0.15);
            this.gfx.strokeCircle(this.joystickOrigin.x, this.joystickOrigin.y, 40);

            // Current position dot
            this.gfx.fillStyle(0xffffff, 0.20);
            this.gfx.fillCircle(this.joystickCurrent.x, this.joystickCurrent.y, 8);
        }

        /**
         * Cleans up event listeners and graphics.
         */
        destroy() {
            if (this.scene && this.scene.input) {
                this.scene.input.off('pointerdown', this.onPointerDown, this);
                this.scene.input.off('pointermove', this.onPointerMove, this);
                this.scene.input.off('pointerup', this.onPointerUp, this);
                this.scene.input.off('pointerupoutside', this.onPointerUp, this);
            }
            if (this.gfx) {
                this.gfx.destroy();
                this.gfx = null;
            }
            this.scene = null;
        }
    }

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

            // Touch controller: created on touch-capable devices
            this.touch = null;
            if (scene.sys.game.device.input.touch) {
                this.touch = new TouchController(scene);
            }
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
            const kb = {
                up:    this.keyW.isDown || this.keyUp.isDown,
                down:  this.keyS.isDown || this.keyDown.isDown,
                left:  this.keyA.isDown || this.keyLeft.isDown,
                right: this.keyD.isDown || this.keyRight.isDown,
            };

            // OR touch state on top of keyboard
            if (this.touch) {
                const ts = this.touch.getState();
                kb.up    = kb.up    || ts.up;
                kb.down  = kb.down  || ts.down;
                kb.left  = kb.left  || ts.left;
                kb.right = kb.right || ts.right;
                this.touch.draw();
            }

            return kb;
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
            const kbPause = Phaser.Input.Keyboard.JustDown(this.keyEsc);
            const touchPause = this.touch ? this.touch.consumePause() : false;
            return kbPause || touchPause;
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
            // Clean up touch controller first (has its own scene references)
            if (this.touch) {
                this.touch.destroy();
                this.touch = null;
            }

            if (!this.scene || !this.scene.input || !this.scene.input.keyboard) {
                this.scene = null;
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
