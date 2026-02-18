/**
 * SoundManager: Procedural audio for Stellar Circuit
 *
 * All sounds are generated via Web Audio API oscillators (no audio files).
 * AudioContext is lazily initialized on user gesture (LAUNCH button click).
 *
 * Registered as: PlatPursuit.Games.Audio.SoundManager
 */

(function () {
    'use strict';

    window.PlatPursuit = window.PlatPursuit || {};
    window.PlatPursuit.Games = window.PlatPursuit.Games || {};
    window.PlatPursuit.Games.Audio = window.PlatPursuit.Games.Audio || {};

    class SoundManager {
        constructor() {
            this.ctx = null;
            this.masterGain = null;
            this.muted = false;
            this.volume = 0.5;

            // Persistent nodes for continuous sounds
            this.engineNoise = null;
            this.engineFilter = null;
            this.engineGain = null;
            this.engineOsc = null;
            this.engineOscGain = null;
            this._engineNoiseGain = null;
            this._engineRush = null;
            this._engineRushFilter = null;
            this._engineRushGain = null;
            this._engineStopTimer = null;
            this.offTrackOsc = null;
            this.offTrackNoise = null;
            this.offTrackGain = null;
            this._offTrackNoiseGain = null;
            this._offTrackStopTimer = null;
            this._noiseBuffer = null;
        }

        // ---------------------------------------------------------------
        // Lifecycle
        // ---------------------------------------------------------------

        /**
         * Initialize AudioContext. Must be called from a user gesture
         * handler (e.g. LAUNCH button click) to comply with browser
         * autoplay policies.
         */
        init() {
            if (this.ctx) return;

            const AudioCtx = window.AudioContext || window.webkitAudioContext;
            if (!AudioCtx) {
                console.warn('[SoundManager] Web Audio API not available');
                return;
            }

            this.ctx = new AudioCtx();

            // Some browsers create the context in a suspended state even
            // when called from a gesture handler. Explicitly resume to
            // ensure audio playback works immediately.
            if (this.ctx.state === 'suspended') {
                this.ctx.resume();
            }

            this.masterGain = this.ctx.createGain();
            this.masterGain.gain.value = this.muted ? 0 : this.volume;
            this.masterGain.connect(this.ctx.destination);
        }

        /**
         * Clean up all audio resources.
         */
        destroy() {
            this.stopEngine();
            this.stopOffTrack();
            if (this.ctx) {
                this.ctx.close().catch(() => {});
                this.ctx = null;
                this.masterGain = null;
            }
            this._noiseBuffer = null;
        }

        // ---------------------------------------------------------------
        // Volume Control
        // ---------------------------------------------------------------

        /**
         * Set master volume (0 to 1).
         */
        setVolume(v) {
            this.volume = Math.max(0, Math.min(1, v));
            if (this.masterGain && !this.muted) {
                this.masterGain.gain.value = this.volume;
            }
        }

        /**
         * Toggle mute on/off.
         * @returns {boolean} New muted state
         */
        toggleMute() {
            this.muted = !this.muted;
            if (this.masterGain) {
                this.masterGain.gain.value = this.muted ? 0 : this.volume;
            }
            // Stop persistent sounds when muting
            if (this.muted) {
                this.stopEngine();
                this.stopOffTrack();
            }
            return this.muted;
        }

        // ---------------------------------------------------------------
        // One-Shot Sounds
        // ---------------------------------------------------------------

        /**
         * Internal helper: play a simple tone with exponential decay.
         */
        _playTone(type, freq, durationMs) {
            if (!this.ctx || this.muted) return;

            const now = this.ctx.currentTime;
            const dur = durationMs / 1000;

            const osc = this.ctx.createOscillator();
            const gain = this.ctx.createGain();

            osc.type = type;
            osc.frequency.value = freq;
            gain.gain.setValueAtTime(0.3, now);
            gain.gain.exponentialRampToValueAtTime(0.001, now + dur);

            osc.connect(gain);
            gain.connect(this.masterGain);
            osc.start(now);
            osc.stop(now + dur + 0.01);
        }

        /**
         * Countdown beep for "3", "2", "1".
         * Square wave at 220Hz, 150ms with exponential decay.
         */
        playCountdownBeep() {
            this._playTone('square', 220, 150);
        }

        /**
         * Countdown "GO!" sound.
         * Square wave at 440Hz, 200ms.
         */
        playCountdownGo() {
            this._playTone('square', 440, 200);
        }

        /**
         * Checkpoint crossing ding.
         * Sine wave sweep from 660Hz to 880Hz, 100ms.
         */
        playCheckpointDing() {
            if (!this.ctx || this.muted) return;

            const now = this.ctx.currentTime;
            const osc = this.ctx.createOscillator();
            const gain = this.ctx.createGain();

            osc.type = 'sine';
            osc.frequency.setValueAtTime(660, now);
            osc.frequency.linearRampToValueAtTime(880, now + 0.1);

            gain.gain.setValueAtTime(0.25, now);
            gain.gain.exponentialRampToValueAtTime(0.001, now + 0.15);

            osc.connect(gain);
            gain.connect(this.masterGain);
            osc.start(now);
            osc.stop(now + 0.16);
        }

        /**
         * Race finish fanfare.
         * C5-E5-G5 arpeggio, staggered 100ms apart, 500ms total.
         */
        playFinishFanfare() {
            if (!this.ctx || this.muted) return;

            const now = this.ctx.currentTime;
            const notes = [523.25, 659.25, 783.99]; // C5, E5, G5

            for (let i = 0; i < notes.length; i++) {
                const osc = this.ctx.createOscillator();
                const gain = this.ctx.createGain();

                osc.type = 'sine';
                osc.frequency.value = notes[i];

                const start = now + i * 0.1;
                gain.gain.setValueAtTime(0, start);
                gain.gain.linearRampToValueAtTime(0.3, start + 0.02);
                gain.gain.exponentialRampToValueAtTime(0.001, now + 0.5);

                osc.connect(gain);
                gain.connect(this.masterGain);
                osc.start(start);
                osc.stop(now + 0.55);
            }
        }

        // ---------------------------------------------------------------
        // Persistent: Engine Thruster
        // ---------------------------------------------------------------

        /**
         * Start the engine thruster sound.
         * Three layers blended for a deep, building "woooohhhhh" thruster:
         *   1. Low rumble: sine oscillator (55-90Hz) for the deep body
         *   2. Mid whoosh: low-pass filtered noise for the rushing air feel
         *   3. High rush: band-pass filtered noise that fades in at speed
         * Each layer has its own gain node, all feeding into engineGain.
         *
         * If a previous engine is still fading out (stopEngine's 150ms
         * timer), the pending stop is cancelled and the old nodes are
         * cleaned up immediately before creating new ones.
         */
        startEngine() {
            if (!this.ctx || this.muted || this.engineNoise) return;

            // Cancel any pending fade-out from a previous stopEngine() call
            if (this._engineStopTimer) {
                clearTimeout(this._engineStopTimer);
                this._engineStopTimer = null;
            }

            // Master engine gain (all layers feed into this)
            this.engineGain = this.ctx.createGain();
            this.engineGain.gain.value = 0;
            this.engineGain.connect(this.masterGain);

            // Layer 1: Low rumble oscillator (deep body "wooooh")
            this.engineOsc = this.ctx.createOscillator();
            this.engineOsc.type = 'sine';
            this.engineOsc.frequency.value = 55;
            this.engineOscGain = this.ctx.createGain();
            this.engineOscGain.gain.value = 0;
            this.engineOsc.connect(this.engineOscGain);
            this.engineOscGain.connect(this.engineGain);

            // Layer 2: Mid whoosh (low-pass filtered noise for rushing air)
            this.engineNoise = this.ctx.createBufferSource();
            this.engineNoise.buffer = this._getNoiseBuffer();
            this.engineNoise.loop = true;

            this.engineFilter = this.ctx.createBiquadFilter();
            this.engineFilter.type = 'lowpass';
            this.engineFilter.frequency.value = 200;
            this.engineFilter.Q.value = 1.0;

            this._engineNoiseGain = this.ctx.createGain();
            this._engineNoiseGain.gain.value = 0;
            this.engineNoise.connect(this.engineFilter);
            this.engineFilter.connect(this._engineNoiseGain);
            this._engineNoiseGain.connect(this.engineGain);

            // Layer 3: High rush (band-pass noise, fades in at higher speeds)
            this._engineRush = this.ctx.createBufferSource();
            this._engineRush.buffer = this._getNoiseBuffer();
            this._engineRush.loop = true;

            this._engineRushFilter = this.ctx.createBiquadFilter();
            this._engineRushFilter.type = 'bandpass';
            this._engineRushFilter.frequency.value = 1200;
            this._engineRushFilter.Q.value = 0.5;

            this._engineRushGain = this.ctx.createGain();
            this._engineRushGain.gain.value = 0;
            this._engineRush.connect(this._engineRushFilter);
            this._engineRushFilter.connect(this._engineRushGain);
            this._engineRushGain.connect(this.engineGain);

            // Start all sources
            this.engineOsc.start();
            this.engineNoise.start();
            this._engineRush.start();
        }

        /**
         * Update engine thruster character based on speed.
         * Low rumble is always present when thrusting, whoosh and rush
         * layers fade in as speed builds for an accelerating feel.
         * @param {number} speedRatio - Current speed / max speed (0 to 1)
         */
        updateEngine(speedRatio) {
            if (!this.engineNoise) return;
            const r = speedRatio;

            // Master gain: quiet idle hum, builds to full power
            this.engineGain.gain.value = 0.04 + r * 0.14;

            // Layer 1: Low rumble. Pitch rises 55->90Hz with speed.
            this.engineOsc.frequency.value = 55 + r * 35;
            this.engineOscGain.gain.value = 0.5 + r * 0.5;

            // Layer 2: Mid whoosh. Cutoff opens 200->600Hz with speed.
            this.engineFilter.frequency.value = 200 + r * 400;
            this._engineNoiseGain.gain.value = r * 0.4;

            // Layer 3: High rush. Only audible above ~40% speed.
            const rushAmount = Math.max(0, (r - 0.4) / 0.6);
            this._engineRushFilter.frequency.value = 1200 + rushAmount * 800;
            this._engineRushGain.gain.value = rushAmount * 0.15;
        }

        /**
         * Stop the engine thruster with a brief fade-out.
         */
        stopEngine() {
            if (!this.engineNoise) return;

            const noise = this.engineNoise;
            const osc = this.engineOsc;
            const gain = this.engineGain;
            const filter = this.engineFilter;
            const oscGain = this.engineOscGain;
            const noiseGain = this._engineNoiseGain;
            const rush = this._engineRush;
            const rushFilter = this._engineRushFilter;
            const rushGain = this._engineRushGain;

            this.engineNoise = null;
            this.engineOsc = null;
            this.engineGain = null;
            this.engineFilter = null;
            this.engineOscGain = null;
            this._engineNoiseGain = null;
            this._engineRush = null;
            this._engineRushFilter = null;
            this._engineRushGain = null;

            try {
                gain.gain.linearRampToValueAtTime(0, this.ctx.currentTime + 0.15);
                this._engineStopTimer = setTimeout(() => {
                    this._engineStopTimer = null;
                    try { noise.stop(); } catch (_) {}
                    try { osc.stop(); } catch (_) {}
                    try { rush.stop(); } catch (_) {}
                    noise.disconnect();
                    osc.disconnect();
                    rush.disconnect();
                    filter.disconnect();
                    gain.disconnect();
                    oscGain.disconnect();
                    noiseGain.disconnect();
                    rushFilter.disconnect();
                    rushGain.disconnect();
                }, 200);
            } catch (_) {
                // Context may be closed
            }
        }

        // ---------------------------------------------------------------
        // Persistent: Off-Track Rumble
        // ---------------------------------------------------------------

        /**
         * Lazily create a white noise AudioBuffer (0.5s, loopable).
         */
        _getNoiseBuffer() {
            if (this._noiseBuffer) return this._noiseBuffer;

            const size = Math.floor(this.ctx.sampleRate * 0.5);
            const buffer = this.ctx.createBuffer(1, size, this.ctx.sampleRate);
            const data = buffer.getChannelData(0);
            for (let i = 0; i < size; i++) {
                data[i] = Math.random() * 2 - 1;
            }
            this._noiseBuffer = buffer;
            return buffer;
        }

        /**
         * Start off-track rumble: 40Hz sine + white noise, low volume.
         */
        startOffTrack() {
            if (!this.ctx || this.muted || this.offTrackOsc) return;

            // Cancel any pending fade-out from a previous stopOffTrack() call
            if (this._offTrackStopTimer) {
                clearTimeout(this._offTrackStopTimer);
                this._offTrackStopTimer = null;
            }

            this.offTrackGain = this.ctx.createGain();
            this.offTrackGain.gain.value = 0.08;
            this.offTrackGain.connect(this.masterGain);

            // Low sine rumble
            this.offTrackOsc = this.ctx.createOscillator();
            this.offTrackOsc.type = 'sine';
            this.offTrackOsc.frequency.value = 40;
            this.offTrackOsc.connect(this.offTrackGain);
            this.offTrackOsc.start();

            // White noise layer
            this.offTrackNoise = this.ctx.createBufferSource();
            this.offTrackNoise.buffer = this._getNoiseBuffer();
            this.offTrackNoise.loop = true;

            const noiseGain = this.ctx.createGain();
            noiseGain.gain.value = 0.3; // Relative to offTrackGain
            this.offTrackNoise.connect(noiseGain);
            noiseGain.connect(this.offTrackGain);
            this.offTrackNoise.start();
            this._offTrackNoiseGain = noiseGain;
        }

        /**
         * Stop off-track rumble with a brief fade-out.
         */
        stopOffTrack() {
            if (!this.offTrackOsc) return;

            const osc = this.offTrackOsc;
            const noise = this.offTrackNoise;
            const gain = this.offTrackGain;
            const noiseGain = this._offTrackNoiseGain;

            this.offTrackOsc = null;
            this.offTrackNoise = null;
            this.offTrackGain = null;
            this._offTrackNoiseGain = null;

            try {
                gain.gain.linearRampToValueAtTime(0, this.ctx.currentTime + 0.1);
                this._offTrackStopTimer = setTimeout(() => {
                    this._offTrackStopTimer = null;
                    try { osc.stop(); } catch (_) {}
                    try { noise.stop(); } catch (_) {}
                    osc.disconnect();
                    noise.disconnect();
                    gain.disconnect();
                    if (noiseGain) noiseGain.disconnect();
                }, 150);
            } catch (_) {
                // Context may be closed
            }
        }
    }

    window.PlatPursuit.Games.Audio.SoundManager = SoundManager;

    console.log('[PlatPursuit] Games.Audio.SoundManager loaded');
})();
