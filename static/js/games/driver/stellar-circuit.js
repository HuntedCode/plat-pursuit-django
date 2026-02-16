/**
 * Stellar Circuit - Entry Point
 *
 * This is the last script loaded. It creates the Phaser game instance,
 * registers all scenes, and starts the game.
 *
 * For Step 1 (setup verification), this creates a minimal Phaser game
 * that displays a confirmation message, proving the entire pipeline works:
 * template -> Phaser loaded -> config correct -> canvas renders.
 */

window.PlatPursuit = window.PlatPursuit || {};
window.PlatPursuit.Games = window.PlatPursuit.Games || {};
window.PlatPursuit.Games.Driver = window.PlatPursuit.Games.Driver || {};

(function() {
    'use strict';

    /**
     * BootScene: A minimal test scene to verify Phaser is working.
     * This will be replaced with the real scene registration in Step 2+.
     */
    class BootScene extends Phaser.Scene {
        constructor() {
            super({ key: 'BootScene' });
        }

        create() {
            const { width, height } = this.scale;

            // Remove the HTML loading indicator now that Phaser has booted.
            // Phaser appends its <canvas> as a sibling inside #phaser-canvas,
            // but doesn't remove existing children, so we clean up manually.
            const loadingEl = document.getElementById('game-loading');
            if (loadingEl) loadingEl.remove();

            // Title text with neon glow feel
            this.add.text(width / 2, height / 2 - 60, 'S T E L L A R   C I R C U I T', {
                fontFamily: 'Poppins, sans-serif',
                fontSize: '36px',
                fontStyle: 'bold',
                color: '#2ce8f5',
            }).setOrigin(0.5);

            // Version/status info
            this.add.text(width / 2, height / 2, 'Phaser ' + Phaser.VERSION + ' initialized', {
                fontFamily: 'Inter, sans-serif',
                fontSize: '16px',
                color: '#6b6b8d',
            }).setOrigin(0.5);

            this.add.text(width / 2, height / 2 + 30, 'Design Resolution: ' + width + 'x' + height, {
                fontFamily: 'Inter, sans-serif',
                fontSize: '14px',
                color: '#6b6b8d',
            }).setOrigin(0.5);

            this.add.text(width / 2, height / 2 + 55, 'Step 1 Setup: Complete', {
                fontFamily: 'Inter, sans-serif',
                fontSize: '14px',
                color: '#40e850',
            }).setOrigin(0.5);

            // Decorative: a small rotating ship to prove rendering works.
            //
            // Key Phaser concept: a Graphics object has its own local coordinate
            // system. When you draw at (0, 0) on a Graphics object, that's the
            // object's origin. If you then set the object's position to (640, 480),
            // the drawing appears at (640, 480) on screen.
            //
            // When you tween `angle` on a Graphics object, it rotates around its
            // origin. So we draw the ship at (0, 0) on the Graphics, then position
            // the Graphics object where we want the ship to appear. This way the
            // rotation pivots around the ship's center, not around (0, 0) world space.
            const graphics = this.add.graphics();
            graphics.setPosition(width / 2, height / 2 + 120);
            this.drawShipPreview(graphics, 0, 0);

            // Animate the ship preview rotating in place
            this.tweens.add({
                targets: graphics,
                angle: 360,
                duration: 4000,
                repeat: -1,
                ease: 'Linear',
            });

            console.log('[Stellar Circuit] Boot scene created successfully');
            console.log('[Stellar Circuit] Phaser version:', Phaser.VERSION);
            console.log('[Stellar Circuit] Canvas size:', width, 'x', height);
            console.log('[Stellar Circuit] Renderer:', this.sys.game.renderer.type === 1 ? 'Canvas' : 'WebGL');
        }

        /**
         * Draws a small preview of the ship shape.
         * Same vertex pattern from the GDD (Section 4.2),
         * scaled down for the boot screen.
         */
        drawShipPreview(graphics, cx, cy) {
            const scale = 1.5;
            const vertices = [
                { x:  16 * scale, y:   0 },  // Nose
                { x: -12 * scale, y:  10 * scale },  // Right wing
                { x:  -6 * scale, y:   4 * scale },  // Right notch
                { x:  -6 * scale, y:  -4 * scale },  // Left notch
                { x: -12 * scale, y: -10 * scale },  // Left wing
            ];

            // Glow layer (ADD blend won't work on Graphics directly in boot,
            // so we just draw a slightly larger shape at lower opacity)
            graphics.lineStyle(3, 0x2ce8f5, 0.3);
            graphics.beginPath();
            graphics.moveTo(cx + vertices[0].x, cy + vertices[0].y);
            for (let i = 1; i < vertices.length; i++) {
                graphics.lineTo(cx + vertices[i].x, cy + vertices[i].y);
            }
            graphics.closePath();
            graphics.strokePath();

            // Body fill
            graphics.fillStyle(0xe8e8ff, 0.9);
            graphics.beginPath();
            graphics.moveTo(cx + vertices[0].x, cy + vertices[0].y);
            for (let i = 1; i < vertices.length; i++) {
                graphics.lineTo(cx + vertices[i].x, cy + vertices[i].y);
            }
            graphics.closePath();
            graphics.fillPath();

            // Edge stroke
            graphics.lineStyle(2, 0x2ce8f5, 1);
            graphics.beginPath();
            graphics.moveTo(cx + vertices[0].x, cy + vertices[0].y);
            for (let i = 1; i < vertices.length; i++) {
                graphics.lineTo(cx + vertices[i].x, cy + vertices[i].y);
            }
            graphics.closePath();
            graphics.strokePath();
        }
    }

    // Create the Phaser game configuration
    const config = {
        type: Phaser.AUTO,
        parent: 'phaser-canvas',
        backgroundColor: '#0a0a14',
        scale: {
            mode: Phaser.Scale.FIT,
            autoCenter: Phaser.Scale.CENTER_BOTH,
            width: PlatPursuit.Games.Shell.DESIGN_WIDTH,
            height: PlatPursuit.Games.Shell.DESIGN_HEIGHT,
        },
        render: {
            pixelArt: false,
            antialias: true,
        },
        scene: [BootScene],
    };

    // Create the game instance
    const game = new Phaser.Game(config);

    // Store reference for debugging and future access
    PlatPursuit.Games.Driver.gameInstance = game;

    console.log('[Stellar Circuit] Game instance created');
})();
