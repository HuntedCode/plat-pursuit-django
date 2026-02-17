/**
 * PlatPursuit.Games.Driver.Scenes.MenuScene
 *
 * Pre-race menu scene for Stellar Circuit.
 * Allows the player to:
 * - Enter a track seed (or use "Today's Track" / "Random")
 * - Preview the generated track
 * - Select CC tier (50cc / 100cc / 200cc)
 * - Select game mode (3-Lap Race / Time Trial)
 * - Launch the race
 *
 * Layout (1280x720 design resolution):
 *   Title top-center, track preview left, settings right,
 *   seed controls middle, LAUNCH button bottom-center.
 *
 * Scene flow: MenuScene -> RaceScene (via LAUNCH)
 */

window.PlatPursuit = window.PlatPursuit || {};
window.PlatPursuit.Games = window.PlatPursuit.Games || {};
window.PlatPursuit.Games.Driver = window.PlatPursuit.Games.Driver || {};
window.PlatPursuit.Games.Driver.Scenes = window.PlatPursuit.Games.Driver.Scenes || {};

(function() {
    'use strict';

    // Aliases
    const Shell = PlatPursuit.Games.Shell;
    const TrackGen = PlatPursuit.Games.Driver.TrackGenerator;
    const UI = PlatPursuit.Games.Driver.UI;
    const Ghost = PlatPursuit.Games.Driver.Ghost;
    const { DESIGN_WIDTH, DESIGN_HEIGHT } = Shell;
    const { COLOR, CSS, createButton, formatTime } = UI;

    // ===================================================================
    // LAYOUT CONSTANTS
    // ===================================================================

    // Track preview (left column)
    const PREVIEW_X = 180;
    const PREVIEW_Y = 210;
    const PREVIEW_SIZE = 250;

    // Settings panel (right column)
    const SETTINGS_X = 700;
    const SETTINGS_Y = 220;

    // Seed controls (center-bottom area)
    const SEED_ROW_Y = 510;

    // Launch button
    const LAUNCH_Y = 620;

    // Starfield
    const NUM_STARS = 150;
    const STAR_SPEED = 15; // px/s downward drift

    // ===================================================================
    // MENU SCENE
    // ===================================================================

    class MenuScene extends Phaser.Scene {
        constructor() {
            super({ key: 'MenuScene' });
        }

        // ---------------------------------------------------------------
        // Phaser Lifecycle
        // ---------------------------------------------------------------

        create() {
            // Remove the HTML loading indicator (first scene to load)
            const loadingEl = document.getElementById('game-loading');
            if (loadingEl) loadingEl.remove();

            // Camera fade in
            this.cameras.main.fadeIn(200, 0, 0, 0);

            // State
            this.currentSeed = this.todaysSeed();
            this.selectedCCTier = '50cc';
            this.selectedMode = 'race';
            this.launching = false;

            // Build UI
            this.createStarfield();
            this.createTitle();
            this.createTrackPreview();
            this.createSettings();
            this.createSeedControls();
            this.createLaunchButton();

            // Generate initial preview
            this.updatePreview(this.currentSeed);

            // Cleanup handler
            this.events.once('shutdown', () => {
                this.removeSeedInput();
                this.input.setDefaultCursor('default');
            });

            console.log('[MenuScene] Created');
        }

        update(time, delta) {
            const dt = delta / 1000;
            this.updateStarfield(dt);
            this.updateLaunchPulse(time);
        }

        // ---------------------------------------------------------------
        // Starfield Background
        // ---------------------------------------------------------------

        createStarfield() {
            this.starGfx = this.add.graphics().setScrollFactor(0);
            this.stars = [];

            for (let i = 0; i < NUM_STARS; i++) {
                this.stars.push({
                    x: Math.random() * DESIGN_WIDTH,
                    y: Math.random() * DESIGN_HEIGHT,
                    size: 0.5 + Math.random() * 1.5,
                    alpha: 0.2 + Math.random() * 0.6,
                });
            }
        }

        updateStarfield(dt) {
            const g = this.starGfx;
            g.clear();

            for (let i = 0; i < this.stars.length; i++) {
                const star = this.stars[i];
                star.y += STAR_SPEED * dt;

                // Wrap around when leaving the bottom
                if (star.y > DESIGN_HEIGHT) {
                    star.y -= DESIGN_HEIGHT;
                    star.x = Math.random() * DESIGN_WIDTH;
                }

                g.fillStyle(0xffffff, star.alpha);
                g.fillCircle(star.x, star.y, star.size);
            }
        }

        // ---------------------------------------------------------------
        // Title
        // ---------------------------------------------------------------

        createTitle() {
            this.add.text(
                DESIGN_WIDTH / 2, 50,
                'S T E L L A R   C I R C U I T',
                {
                    fontFamily: 'Poppins, sans-serif',
                    fontSize: '36px',
                    fontStyle: '700',
                    color: CSS.CYAN_GLOW,
                }
            ).setOrigin(0.5).setScrollFactor(0);

            // Subtitle
            this.add.text(
                DESIGN_WIDTH / 2, 95,
                'Neon Vector Space Racing',
                {
                    fontFamily: 'Inter, sans-serif',
                    fontSize: '14px',
                    color: CSS.NEUTRAL_MID,
                }
            ).setOrigin(0.5).setScrollFactor(0);
        }

        // ---------------------------------------------------------------
        // Track Preview (left column)
        // ---------------------------------------------------------------

        createTrackPreview() {
            // Background panel for the preview area
            this.previewBg = this.add.graphics().setScrollFactor(0);
            this.previewBg.fillStyle(COLOR.DEEP_SPACE_2, 0.6);
            this.previewBg.fillRoundedRect(
                PREVIEW_X - PREVIEW_SIZE / 2 - 15,
                PREVIEW_Y - PREVIEW_SIZE / 2 - 15,
                PREVIEW_SIZE + 30,
                PREVIEW_SIZE + 30,
                6
            );
            this.previewBg.lineStyle(1, COLOR.NEUTRAL_DARK, 0.5);
            this.previewBg.strokeRoundedRect(
                PREVIEW_X - PREVIEW_SIZE / 2 - 15,
                PREVIEW_Y - PREVIEW_SIZE / 2 - 15,
                PREVIEW_SIZE + 30,
                PREVIEW_SIZE + 30,
                6
            );

            // "Track Preview" label
            this.add.text(
                PREVIEW_X, PREVIEW_Y - PREVIEW_SIZE / 2 - 25,
                'TRACK PREVIEW',
                {
                    fontFamily: 'Poppins, sans-serif',
                    fontSize: '12px',
                    fontStyle: '600',
                    color: CSS.NEUTRAL_MID,
                }
            ).setOrigin(0.5).setScrollFactor(0);

            // Seed label (below preview, updated dynamically)
            this.seedLabel = this.add.text(
                PREVIEW_X, PREVIEW_Y + PREVIEW_SIZE / 2 + 25,
                '',
                {
                    fontFamily: 'monospace',
                    fontSize: '12px',
                    color: CSS.STEEL_DARK,
                }
            ).setOrigin(0.5).setScrollFactor(0);

            // Placeholder for the preview graphics (created in updatePreview)
            this.previewGfx = null;
        }

        // ---------------------------------------------------------------
        // Settings Panel (right column)
        // ---------------------------------------------------------------

        createSettings() {
            const sx = SETTINGS_X;
            let sy = SETTINGS_Y;

            // CC Tier
            this.add.text(sx, sy, 'CC TIER', {
                fontFamily: 'Poppins, sans-serif',
                fontSize: '12px',
                fontStyle: '600',
                color: CSS.NEUTRAL_MID,
            }).setOrigin(0.5, 0).setScrollFactor(0);

            sy += 40;

            const tierNames = ['50cc', '100cc', '200cc'];
            this.tierButtons = [];

            for (let i = 0; i < tierNames.length; i++) {
                const tierName = tierNames[i];
                const bx = sx - 100 + i * 100;
                const btn = createButton(this, bx, sy, tierName, {
                    width: 80,
                    height: 32,
                    fontSize: 13,
                    onClick: () => this.selectCCTier(tierName),
                });
                this.tierButtons.push({ btn, tier: tierName });
            }

            // Highlight default (50cc)
            this.tierButtons[0].btn.setSelected(true);

            // Mode selector
            sy += 60;

            this.add.text(sx, sy, 'MODE', {
                fontFamily: 'Poppins, sans-serif',
                fontSize: '12px',
                fontStyle: '600',
                color: CSS.NEUTRAL_MID,
            }).setOrigin(0.5, 0).setScrollFactor(0);

            sy += 40;

            this.raceModeBtn = createButton(this, sx - 80, sy, '3-Lap Race', {
                width: 130,
                height: 32,
                fontSize: 13,
                onClick: () => this.selectMode('race'),
            });
            this.raceModeBtn.setSelected(true);

            this.ttModeBtn = createButton(this, sx + 80, sy, 'Time Trial', {
                width: 130,
                height: 32,
                fontSize: 13,
                onClick: () => this.selectMode('timetrial'),
            });

            // Best time display (below mode buttons, updated dynamically)
            sy += 30;

            this.bestTimeText = this.add.text(sx, sy, '', {
                fontFamily: 'monospace',
                fontSize: '11px',
                color: CSS.STEEL_DARK,
            }).setOrigin(0.5, 0).setScrollFactor(0);

            // Ghost toggle
            sy += 40;

            this.add.text(sx, sy, 'GHOST', {
                fontFamily: 'Poppins, sans-serif',
                fontSize: '12px',
                fontStyle: '600',
                color: CSS.NEUTRAL_MID,
            }).setOrigin(0.5, 0).setScrollFactor(0);

            sy += 38;

            this.ghostEnabled = true;
            this.ghostToggleBtn = createButton(this, sx, sy, 'ON', {
                width: 80,
                height: 32,
                fontSize: 13,
                onClick: () => this.toggleGhost(),
            });
            this.ghostToggleBtn.setSelected(true);

            // Show initial best time
            this.updateBestTimeDisplay();
        }

        selectCCTier(tier) {
            this.selectedCCTier = tier;
            for (const { btn, tier: t } of this.tierButtons) {
                btn.setSelected(t === tier);
            }
            this.updateBestTimeDisplay();
        }

        selectMode(mode) {
            this.selectedMode = mode;
            this.raceModeBtn.setSelected(mode === 'race');
            this.ttModeBtn.setSelected(mode === 'timetrial');
            this.updateBestTimeDisplay();
        }

        toggleGhost() {
            this.ghostEnabled = !this.ghostEnabled;
            this.ghostToggleBtn.text.setText(this.ghostEnabled ? 'ON' : 'OFF');
            this.ghostToggleBtn.setSelected(this.ghostEnabled);
        }

        /**
         * Updates the best time display below the mode buttons.
         * Shows "BEST RACE: X:XX.XXX" for race mode,
         * "BEST LAP: X:XX.XXX" for time trial mode.
         */
        updateBestTimeDisplay() {
            const tier = this.selectedCCTier;
            if (this.selectedMode === 'timetrial') {
                const bestLapMs = Ghost.GhostStorage.getBestLapTime(this.currentSeed, 'timetrial', tier);
                if (bestLapMs) {
                    this.bestTimeText.setText(`Best Lap: ${formatTime(bestLapMs / 1000)}`);
                } else {
                    this.bestTimeText.setText('No data');
                }
            } else {
                const bestTimeMs = Ghost.GhostStorage.getBestTime(this.currentSeed, 'race', tier);
                if (bestTimeMs) {
                    this.bestTimeText.setText(`Best Race: ${formatTime(bestTimeMs / 1000)}`);
                } else {
                    this.bestTimeText.setText('No data');
                }
            }
        }

        // ---------------------------------------------------------------
        // Seed Controls (center row)
        // ---------------------------------------------------------------

        createSeedControls() {
            const y = SEED_ROW_Y;
            const baseX = DESIGN_WIDTH / 2;

            // "Today's Track" button
            this.todayBtn = createButton(this, baseX - 200, y, "Today's Track", {
                width: 140,
                height: 34,
                fontSize: 13,
                onClick: () => this.setSeed(this.todaysSeed()),
            });

            // "Random" button
            this.randomBtn = createButton(this, baseX - 40, y, 'Random', {
                width: 100,
                height: 34,
                fontSize: 13,
                onClick: () => this.setSeed('random-' + Date.now()),
            });

            // DOM seed input + Go button
            this.createSeedInput(baseX + 100, y);

            // "Go" button (Phaser, next to DOM input)
            this.goBtn = createButton(this, baseX + 250, y, 'Go', {
                width: 50,
                height: 34,
                fontSize: 13,
                onClick: () => {
                    if (this.seedInput && this.seedInput.value.trim()) {
                        this.setSeed(this.seedInput.value.trim());
                    }
                },
            });
        }

        createSeedInput(designX, designY) {
            // Create a real DOM <input> element for native text editing.
            // Positioned over the Phaser canvas using Scale Manager metrics.
            this.seedInput = document.createElement('input');
            this.seedInput.type = 'text';
            this.seedInput.placeholder = 'Enter seed...';
            this.seedInput.maxLength = 40;

            // Style to match game aesthetic
            Object.assign(this.seedInput.style, {
                position: 'absolute',
                background: '#141428',
                border: '1px solid #3a3a5c',
                color: '#e8e8f0',
                fontFamily: 'monospace',
                fontSize: '13px',
                padding: '6px 10px',
                borderRadius: '4px',
                outline: 'none',
                boxSizing: 'border-box',
                zIndex: '10',
            });

            // Focus style via event listeners
            this.seedInput.addEventListener('focus', () => {
                this.seedInput.style.borderColor = '#2ce8f5';
            });
            this.seedInput.addEventListener('blur', () => {
                this.seedInput.style.borderColor = '#3a3a5c';
            });

            // ENTER key in input triggers "Go"
            this.seedInput.addEventListener('keydown', (e) => {
                if (e.key === 'Enter' && this.seedInput.value.trim()) {
                    this.setSeed(this.seedInput.value.trim());
                }
                // Prevent Phaser from capturing keys while input is focused
                e.stopPropagation();
            });

            // Find the game canvas parent and append the input
            const parent = document.getElementById('phaser-canvas');
            if (parent) {
                parent.style.position = 'relative';
                parent.appendChild(this.seedInput);
            }

            // Store design coordinates for repositioning
            this._seedInputDesignX = designX;
            this._seedInputDesignY = designY;

            // Position based on current canvas scale
            this.repositionSeedInput();

            // Reposition on resize
            this._resizeHandler = () => this.repositionSeedInput();
            this.scale.on('resize', this._resizeHandler);
        }

        /**
         * Positions the DOM seed input to align with the Phaser canvas.
         *
         * Phaser's Scale Manager maps the 1280x720 design space onto
         * the actual canvas size. We need to convert our design
         * coordinates to actual pixel positions relative to the
         * canvas container.
         */
        repositionSeedInput() {
            if (!this.seedInput) return;

            const canvas = this.game.canvas;
            if (!canvas) return;

            // Scale factor from design resolution to actual canvas
            const scaleX = canvas.width / DESIGN_WIDTH;
            const scaleY = canvas.height / DESIGN_HEIGHT;

            // The canvas may be offset within its parent due to centering
            const canvasRect = canvas.getBoundingClientRect();
            const parentRect = canvas.parentElement.getBoundingClientRect();
            const offsetX = canvasRect.left - parentRect.left;
            const offsetY = canvasRect.top - parentRect.top;

            // Convert design coordinates to actual pixels
            // Also account for CSS scaling (canvas.clientWidth vs canvas.width)
            const cssScale = canvas.clientWidth / canvas.width;

            const inputW = 130;
            const inputH = 34;

            this.seedInput.style.left = `${offsetX + (this._seedInputDesignX - inputW / 2) * scaleX * cssScale}px`;
            this.seedInput.style.top = `${offsetY + (this._seedInputDesignY - inputH / 2) * scaleY * cssScale}px`;
            this.seedInput.style.width = `${inputW * scaleX * cssScale}px`;
            this.seedInput.style.height = `${inputH * scaleY * cssScale}px`;
        }

        removeSeedInput() {
            if (this.seedInput) {
                this.seedInput.remove();
                this.seedInput = null;
            }
            if (this._resizeHandler) {
                this.scale.off('resize', this._resizeHandler);
                this._resizeHandler = null;
            }
        }

        // ---------------------------------------------------------------
        // Seed + Preview Management
        // ---------------------------------------------------------------

        todaysSeed() {
            return new Date().toISOString().slice(0, 10);
        }

        setSeed(seed) {
            this.currentSeed = seed;
            this.updatePreview(seed);
            this.updateBestTimeDisplay();
        }

        updatePreview(seed) {
            // Destroy previous preview graphics
            if (this.previewGfx) {
                this.previewGfx.destroy();
                this.previewGfx = null;
            }

            // Generate track and render minimap at preview size
            const trackData = TrackGen.generate(seed);
            const result = TrackGen.renderMinimap(
                this, trackData,
                PREVIEW_X - PREVIEW_SIZE / 2,
                PREVIEW_Y - PREVIEW_SIZE / 2,
                PREVIEW_SIZE, PREVIEW_SIZE
            );

            this.previewGfx = result.graphics;
            this.previewGfx.setScrollFactor(0);

            // Update seed label
            const displaySeed = seed.length > 25
                ? seed.slice(0, 22) + '...'
                : seed;
            this.seedLabel.setText(`Seed: "${displaySeed}"`);
        }

        // ---------------------------------------------------------------
        // Launch Button
        // ---------------------------------------------------------------

        createLaunchButton() {
            const x = DESIGN_WIDTH / 2;
            const y = LAUNCH_Y;
            const w = 220;
            const h = 50;

            // Glow effect (drawn behind the main button)
            this.launchGlow = this.add.graphics().setScrollFactor(0);

            // Main button
            this.launchBtn = createButton(this, x, y, 'L A U N C H', {
                width: w,
                height: h,
                fontSize: 20,
                onClick: () => this.launch(),
            });
        }

        updateLaunchPulse(time) {
            // Gentle pulsing glow around the launch button
            const g = this.launchGlow;
            g.clear();

            const x = DESIGN_WIDTH / 2;
            const y = LAUNCH_Y;
            const w = 220;
            const h = 50;

            const pulse = 0.15 + Math.sin(time * 0.003) * 0.1;

            g.lineStyle(2, COLOR.CYAN_GLOW, pulse);
            g.strokeRoundedRect(x - w / 2 - 4, y - h / 2 - 4, w + 8, h + 8, 6);
        }

        // ---------------------------------------------------------------
        // Launch Action
        // ---------------------------------------------------------------

        launch() {
            // Prevent double-launch
            if (this.launching) return;
            this.launching = true;

            const seed = this.currentSeed || this.todaysSeed();
            const ccTier = this.selectedCCTier;
            const mode = this.selectedMode;
            const ghostEnabled = this.ghostEnabled;

            this.cameras.main.fadeOut(200, 0, 0, 0);
            this.cameras.main.once('camerafadeoutcomplete', () => {
                this.scene.start('RaceScene', {
                    seed: seed,
                    mode: mode,
                    ccTier: ccTier,
                    ghostEnabled: ghostEnabled,
                });
            });
        }
    }

    // ===================================================================
    // PUBLIC API
    // ===================================================================

    PlatPursuit.Games.Driver.Scenes.MenuScene = MenuScene;

    console.log('[Stellar Circuit] MenuScene loaded');
})();
