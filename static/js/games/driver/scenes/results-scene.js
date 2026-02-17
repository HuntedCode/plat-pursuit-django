/**
 * PlatPursuit.Games.Driver.Scenes.ResultsScene
 *
 * Post-race results screen for Stellar Circuit.
 * Shows the player's performance and allows them to:
 * - View total time and per-lap breakdown
 * - See which lap was the best (highlighted in gold)
 * - Retry the same track, start a new track, or return to menu
 *
 * Data received via init(data):
 *   totalTime, lapTimes, bestLapTime, bestLapIndex,
 *   seed, ccTier, trackData
 *
 * Scene flow: ResultsScene -> RaceScene (retry/new) or MenuScene (menu)
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
    const { DESIGN_WIDTH, DESIGN_HEIGHT } = Shell;
    const { COLOR, CSS, createButton, formatTime } = UI;

    // ===================================================================
    // RESULTS SCENE
    // ===================================================================

    class ResultsScene extends Phaser.Scene {
        constructor() {
            super({ key: 'ResultsScene' });
        }

        // ---------------------------------------------------------------
        // Phaser Lifecycle
        // ---------------------------------------------------------------

        init(data) {
            this.raceData = data;
        }

        create() {
            // Camera fade in
            this.cameras.main.fadeIn(200, 0, 0, 0);

            // Prevent navigation while transitioning
            this.transitioning = false;

            const d = this.raceData;

            // Background: faint track outline
            this.createBackground(d.trackData);

            // "RACE COMPLETE" title with scale-in animation
            this.createTitle();

            // Total time
            this.createTotalTime(d.totalTime);

            // Per-lap breakdown
            this.createLapBreakdown(d.lapTimes, d.bestLapIndex);

            // Seed / tier info
            this.createInfoLine(d.seed, d.ccTier, d.lapTimes.length);

            // Action buttons
            this.createButtons(d);

            // Keyboard shortcuts
            this.createKeyboardShortcuts(d);

            // Cleanup
            this.events.once('shutdown', () => {
                this.input.setDefaultCursor('default');
                const KeyCodes = Phaser.Input.Keyboard.KeyCodes;
                this.input.keyboard.removeCapture([
                    KeyCodes.ENTER, KeyCodes.ESC,
                ]);
            });

            console.log(`[ResultsScene] Created: ${formatTime(d.totalTime)}`);
        }

        update() {
            if (this.transitioning) return;

            const JustDown = Phaser.Input.Keyboard.JustDown;
            if (JustDown(this.enterKey)) {
                this.retry();
            } else if (JustDown(this.escKey)) {
                this.goToMenu();
            }
        }

        // ---------------------------------------------------------------
        // Background
        // ---------------------------------------------------------------

        createBackground(trackData) {
            // Semi-transparent overlay
            const bg = this.add.graphics().setScrollFactor(0);
            bg.fillStyle(COLOR.DEEP_SPACE_1, 1.0);
            bg.fillRect(0, 0, DESIGN_WIDTH, DESIGN_HEIGHT);

            // Faint track outline centered in the background
            if (trackData) {
                const result = TrackGen.renderMinimap(
                    this, trackData,
                    DESIGN_WIDTH / 2 - 200,
                    DESIGN_HEIGHT / 2 - 200,
                    400, 400
                );
                result.graphics.setScrollFactor(0);
                result.graphics.setAlpha(0.15);
            }
        }

        // ---------------------------------------------------------------
        // Title
        // ---------------------------------------------------------------

        createTitle() {
            const title = this.add.text(
                DESIGN_WIDTH / 2, 100,
                'R A C E   C O M P L E T E',
                {
                    fontFamily: 'Poppins, sans-serif',
                    fontSize: '42px',
                    fontStyle: '700',
                    color: CSS.GOLD,
                }
            ).setOrigin(0.5).setScrollFactor(0);

            // Scale-in animation
            title.setScale(0);
            this.tweens.add({
                targets: title,
                scaleX: 1,
                scaleY: 1,
                duration: 400,
                ease: 'Back.easeOut',
            });
        }

        // ---------------------------------------------------------------
        // Total Time
        // ---------------------------------------------------------------

        createTotalTime(totalTime) {
            this.add.text(
                DESIGN_WIDTH / 2, 190,
                formatTime(totalTime),
                {
                    fontFamily: 'monospace',
                    fontSize: '48px',
                    color: CSS.CYAN_GLOW,
                }
            ).setOrigin(0.5).setScrollFactor(0);
        }

        // ---------------------------------------------------------------
        // Lap Breakdown
        // ---------------------------------------------------------------

        createLapBreakdown(lapTimes, bestLapIndex) {
            const startY = 260;
            const lineHeight = 30;

            for (let i = 0; i < lapTimes.length; i++) {
                const isBest = (i === bestLapIndex && lapTimes.length > 1);
                const color = isBest ? CSS.GOLD : CSS.NEUTRAL_MID;
                const marker = isBest ? '   BEST' : '';

                this.add.text(
                    DESIGN_WIDTH / 2, startY + i * lineHeight,
                    `Lap ${i + 1}:  ${formatTime(lapTimes[i])}${marker}`,
                    {
                        fontFamily: 'monospace',
                        fontSize: '16px',
                        color: color,
                    }
                ).setOrigin(0.5).setScrollFactor(0);
            }
        }

        // ---------------------------------------------------------------
        // Info Line
        // ---------------------------------------------------------------

        createInfoLine(seed, ccTier, lapCount) {
            const displaySeed = seed.length > 20
                ? seed.slice(0, 17) + '...'
                : seed;

            this.add.text(
                DESIGN_WIDTH / 2, 400,
                `Seed: "${displaySeed}"  |  ${ccTier}  |  ${lapCount} Laps`,
                {
                    fontFamily: 'Inter, sans-serif',
                    fontSize: '12px',
                    color: CSS.STEEL_DARK,
                }
            ).setOrigin(0.5).setScrollFactor(0);
        }

        // ---------------------------------------------------------------
        // Action Buttons
        // ---------------------------------------------------------------

        createButtons(data) {
            const y = 500;
            const spacing = 180;

            createButton(this, DESIGN_WIDTH / 2 - spacing, y, 'RETRY', {
                width: 140,
                height: 40,
                fontSize: 16,
                onClick: () => this.retry(),
            });

            createButton(this, DESIGN_WIDTH / 2, y, 'NEW TRACK', {
                width: 140,
                height: 40,
                fontSize: 16,
                onClick: () => this.newTrack(),
            });

            createButton(this, DESIGN_WIDTH / 2 + spacing, y, 'MENU', {
                width: 140,
                height: 40,
                fontSize: 16,
                onClick: () => this.goToMenu(),
            });

            // Keyboard hint
            this.add.text(
                DESIGN_WIDTH / 2, y + 40,
                'ENTER: Retry  |  ESC: Menu',
                {
                    fontFamily: 'Inter, sans-serif',
                    fontSize: '11px',
                    color: CSS.STEEL_DARK,
                }
            ).setOrigin(0.5).setScrollFactor(0);
        }

        // ---------------------------------------------------------------
        // Keyboard Shortcuts
        // ---------------------------------------------------------------

        createKeyboardShortcuts() {
            const KeyCodes = Phaser.Input.Keyboard.KeyCodes;
            this.enterKey = this.input.keyboard.addKey(KeyCodes.ENTER, true, false);
            this.escKey = this.input.keyboard.addKey(KeyCodes.ESC, true, false);
        }

        // ---------------------------------------------------------------
        // Navigation Actions
        // ---------------------------------------------------------------

        retry() {
            if (this.transitioning) return;
            this.transitioning = true;

            this.cameras.main.fadeOut(200, 0, 0, 0);
            this.cameras.main.once('camerafadeoutcomplete', () => {
                this.scene.start('RaceScene', {
                    seed: this.raceData.seed,
                    laps: this.raceData.lapTimes.length,
                    ccTier: this.raceData.ccTier,
                });
            });
        }

        newTrack() {
            if (this.transitioning) return;
            this.transitioning = true;

            this.cameras.main.fadeOut(200, 0, 0, 0);
            this.cameras.main.once('camerafadeoutcomplete', () => {
                this.scene.start('RaceScene', {
                    seed: 'random-' + Date.now(),
                    laps: this.raceData.lapTimes.length,
                    ccTier: this.raceData.ccTier,
                });
            });
        }

        goToMenu() {
            if (this.transitioning) return;
            this.transitioning = true;

            this.cameras.main.fadeOut(200, 0, 0, 0);
            this.cameras.main.once('camerafadeoutcomplete', () => {
                this.scene.start('MenuScene');
            });
        }

    }

    // ===================================================================
    // PUBLIC API
    // ===================================================================

    PlatPursuit.Games.Driver.Scenes.ResultsScene = ResultsScene;

    console.log('[Stellar Circuit] ResultsScene loaded');
})();
