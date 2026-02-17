/**
 * PlatPursuit.Games.Driver.Scenes.ResultsScene
 *
 * Post-race results screen for Stellar Circuit.
 * Supports two display modes:
 *
 * Race Mode ("RACE COMPLETE"):
 * - Total time as main stat
 * - Per-lap breakdown with best lap highlighted
 * - NEW RECORD indicator if personal best
 *
 * Time Trial ("SESSION SUMMARY"):
 * - Best lap as main stat (large gold text)
 * - Total laps completed, total session time
 * - Last 5 laps breakdown with BEST marker
 *
 * Common:
 * - Faint track outline background
 * - Retry / New Track / Menu buttons
 * - ENTER: Retry, ESC: Menu keyboard shortcuts
 *
 * Data received via init(data):
 *   totalTime, lapTimes, bestLapTime, bestLapIndex,
 *   seed, ccTier, mode, trackData, isNewRecord
 *   (TT only): ttTotalLapsCompleted, ttSessionBestLapTime, ttSessionBestLapIndex
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
            this.mode = data.mode || 'race';
        }

        create() {
            // Camera fade in
            this.cameras.main.fadeIn(200, 0, 0, 0);

            // Prevent navigation while transitioning
            this.transitioning = false;

            const d = this.raceData;

            // Background: faint track outline
            this.createBackground(d.trackData);

            // Fork display based on mode
            if (this.mode === 'timetrial') {
                this.createTimeTrialResults(d);
            } else {
                this.createRaceResults(d);
            }

            // Seed / tier info
            this.createInfoLine(d.seed, d.ccTier);

            // Action buttons
            this.createButtons(d);

            // Keyboard shortcuts
            this.createKeyboardShortcuts();

            // Cleanup
            this.events.once('shutdown', () => {
                this.input.setDefaultCursor('default');
                const KeyCodes = Phaser.Input.Keyboard.KeyCodes;
                this.input.keyboard.removeCapture([
                    KeyCodes.ENTER, KeyCodes.ESC,
                ]);
            });

            console.log(`[ResultsScene] Created (${this.mode}): ${formatTime(d.totalTime)}`);
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
        // Race Mode Results
        // ---------------------------------------------------------------

        createRaceResults(d) {
            // "RACE COMPLETE" title with scale-in animation
            this.createTitle('R A C E   C O M P L E T E');

            // Total time as main stat
            this.add.text(
                DESIGN_WIDTH / 2, 190,
                formatTime(d.totalTime),
                {
                    fontFamily: 'monospace',
                    fontSize: '48px',
                    color: CSS.CYAN_GLOW,
                }
            ).setOrigin(0.5).setScrollFactor(0);

            // "NEW RECORD" indicator
            if (d.isNewRecord) {
                this.createNewRecordIndicator(235);
            }

            // Per-lap breakdown
            this.createLapBreakdown(d.lapTimes, d.bestLapIndex, 260);
        }

        // ---------------------------------------------------------------
        // Time Trial Results
        // ---------------------------------------------------------------

        createTimeTrialResults(d) {
            // "SESSION SUMMARY" title
            this.createTitle('S E S S I O N   S U M M A R Y');

            // Best lap as main stat (large gold)
            const bestLap = d.ttSessionBestLapTime || d.bestLapTime;
            if (bestLap !== null) {
                this.add.text(
                    DESIGN_WIDTH / 2, 175,
                    formatTime(bestLap),
                    {
                        fontFamily: 'monospace',
                        fontSize: '48px',
                        color: CSS.GOLD,
                    }
                ).setOrigin(0.5).setScrollFactor(0);

                this.add.text(
                    DESIGN_WIDTH / 2, 220,
                    'BEST LAP',
                    {
                        fontFamily: 'Poppins, sans-serif',
                        fontSize: '14px',
                        fontStyle: '600',
                        color: CSS.NEUTRAL_MID,
                    }
                ).setOrigin(0.5).setScrollFactor(0);
            }

            // Session stats row
            const totalLaps = d.ttTotalLapsCompleted || d.lapTimes.length;
            const statsY = 255;

            this.add.text(
                DESIGN_WIDTH / 2, statsY,
                `${totalLaps} Laps Completed  |  Session Time: ${formatTime(d.totalTime)}`,
                {
                    fontFamily: 'Inter, sans-serif',
                    fontSize: '13px',
                    color: CSS.NEUTRAL_MID,
                }
            ).setOrigin(0.5).setScrollFactor(0);

            // Last N laps breakdown (max 5 to avoid overflow)
            const maxShown = 5;
            const laps = d.lapTimes;
            const showFrom = Math.max(0, laps.length - maxShown);
            const shownLaps = laps.slice(showFrom);
            const startY = 290;
            const lineHeight = 26;

            if (laps.length > maxShown) {
                this.add.text(
                    DESIGN_WIDTH / 2, startY - 20,
                    `(showing last ${maxShown} of ${laps.length})`,
                    {
                        fontFamily: 'Inter, sans-serif',
                        fontSize: '10px',
                        color: CSS.STEEL_DARK,
                    }
                ).setOrigin(0.5).setScrollFactor(0);
            }

            for (let i = 0; i < shownLaps.length; i++) {
                const actualIndex = showFrom + i;
                const isBest = (d.bestLapIndex !== undefined && actualIndex === d.bestLapIndex && laps.length > 1);
                const color = isBest ? CSS.GOLD : CSS.NEUTRAL_MID;
                const marker = isBest ? '   BEST' : '';

                this.add.text(
                    DESIGN_WIDTH / 2, startY + i * lineHeight,
                    `Lap ${actualIndex + 1}:  ${formatTime(shownLaps[i])}${marker}`,
                    {
                        fontFamily: 'monospace',
                        fontSize: '14px',
                        color: color,
                    }
                ).setOrigin(0.5).setScrollFactor(0);
            }
        }

        // ---------------------------------------------------------------
        // Shared UI Elements
        // ---------------------------------------------------------------

        createTitle(text) {
            const title = this.add.text(
                DESIGN_WIDTH / 2, 100,
                text,
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

        createNewRecordIndicator(y) {
            const record = this.add.text(
                DESIGN_WIDTH / 2, y,
                'N E W   R E C O R D',
                {
                    fontFamily: 'Poppins, sans-serif',
                    fontSize: '16px',
                    fontStyle: '700',
                    color: CSS.GOLD,
                }
            ).setOrigin(0.5).setScrollFactor(0);

            // Scale-in animation (delayed slightly after title)
            record.setScale(0);
            this.tweens.add({
                targets: record,
                scaleX: 1,
                scaleY: 1,
                delay: 300,
                duration: 400,
                ease: 'Back.easeOut',
            });
        }

        createLapBreakdown(lapTimes, bestLapIndex, startY) {
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

        createInfoLine(seed, ccTier) {
            const displaySeed = seed.length > 20
                ? seed.slice(0, 17) + '...'
                : seed;

            const modeLabel = this.mode === 'timetrial' ? 'Time Trial' : '3-Lap Race';

            this.add.text(
                DESIGN_WIDTH / 2, 430,
                `Seed: "${displaySeed}"  |  ${ccTier}  |  ${modeLabel}`,
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

        /**
         * Fades out and transitions to another scene.
         * Guards against double-navigation during the fade.
         *
         * @param {string} sceneKey - Target scene key
         * @param {Object} [data] - Data to pass to the target scene's init()
         */
        transitionTo(sceneKey, data) {
            if (this.transitioning) return;
            this.transitioning = true;

            this.cameras.main.fadeOut(200, 0, 0, 0);
            this.cameras.main.once('camerafadeoutcomplete', () => {
                this.scene.start(sceneKey, data);
            });
        }

        retry() {
            this.transitionTo('RaceScene', {
                seed: this.raceData.seed,
                mode: this.mode,
                ccTier: this.raceData.ccTier,
                ghostEnabled: this.raceData.ghostEnabled,
            });
        }

        newTrack() {
            this.transitionTo('RaceScene', {
                seed: 'random-' + Date.now(),
                mode: this.mode,
                ccTier: this.raceData.ccTier,
                ghostEnabled: this.raceData.ghostEnabled,
            });
        }

        goToMenu() {
            this.transitionTo('MenuScene');
        }

    }

    // ===================================================================
    // PUBLIC API
    // ===================================================================

    PlatPursuit.Games.Driver.Scenes.ResultsScene = ResultsScene;

    console.log('[Stellar Circuit] ResultsScene loaded');
})();
