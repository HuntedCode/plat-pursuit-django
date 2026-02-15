/**
 * PlatPursuit Easter Eggs
 * Hidden fun throughout the site. If you're reading this, you're already on the right track.
 * Depends on: PlatPursuit.ToastManager, PlatPursuit.CelebrationManager
 */
const EasterEggs = {
    init() {
        this._initKonamiCode();
        this._initConsoleArt();
        this._initLateNightToast();
        this._initFooterLogoSpin();
    },

    // ==========================================
    // Easter Egg 1: Konami Code (Well-Hidden)
    // Full sequence: Up Up Down Down Left Right Left Right B A Enter
    // ==========================================
    _initKonamiCode() {
        const sequence = [
            'ArrowUp', 'ArrowUp', 'ArrowDown', 'ArrowDown',
            'ArrowLeft', 'ArrowRight', 'ArrowLeft', 'ArrowRight',
            'KeyB', 'KeyA', 'Enter'
        ];
        let position = 0;
        let resetTimer = null;

        document.addEventListener('keydown', (e) => {
            // Don't track if user is typing in an input/textarea
            const tag = e.target.tagName;
            if (tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT') return;

            clearTimeout(resetTimer);
            resetTimer = setTimeout(() => { position = 0; }, 3000);

            if (e.code === sequence[position]) {
                position++;
                if (position === sequence.length) {
                    position = 0;
                    this._triggerKonamiCelebration();
                }
            } else {
                position = 0;
            }
        });
    },

    async _triggerKonamiCelebration() {
        try {
            await PlatPursuit.CelebrationManager.loadConfetti();
            PlatPursuit.CelebrationManager.fireSideConfetti(5000);
        } catch (error) {
            // Confetti failed to load, still show the trophy rain + toast
        }

        // Rain platinum trophy images
        const trophyCount = 15;
        for (let i = 0; i < trophyCount; i++) {
            const img = document.createElement('img');
            img.src = '/static/images/plat.png';
            img.className = 'plat-rain-drop';
            img.style.left = `${Math.random() * 95}vw`;
            img.style.animationDelay = `${Math.random() * 1.5}s`;
            img.setAttribute('aria-hidden', 'true');
            document.body.appendChild(img);

            img.addEventListener('animationend', () => img.remove());
        }

        PlatPursuit.ToastManager.show(
            'You found the secret code. A true trophy hunter.',
            'success',
            8000
        );

        this._trackEvent('konami_code');
    },

    // ==========================================
    // Easter Egg 2: Dev Tools Console Art (Subtle)
    // Prints a styled ASCII trophy on every page load
    // ==========================================
    _initConsoleArt() {
        const trophy = [
            '        ___________',
            "       '._==_==_=_.'",
            '       .-\\:      /-.',
            '      | (|:.     |) |',
            "       '-|:.     |-'",
            '         \\::.    /',
            "          '::. .'",
            '            ) (',
            "          _.' '._",
            '         `"""""""`'
        ].join('\n');

        console.log(
            '%c' + trophy,
            'color: #67d1f8; font-family: monospace; font-size: 14px; line-height: 1.2;'
        );
        console.log(
            '%cPlatinum Pursuit %c// No trophy can hide from us.',
            'color: #67d1f8; font-weight: bold; font-size: 16px;',
            'color: #a855f7; font-style: italic; font-size: 14px;'
        );
        console.log(
            '%cCurious? We like that. Join the hunt: https://discord.gg/platpursuit',
            'color: #22c55e; font-size: 12px;'
        );
    },

    // ==========================================
    // Easter Egg 3: Late Night Toast (Subtle)
    // Shows a fun message between 2:00-4:59 AM local time, once per session
    // ==========================================
    _initLateNightToast() {
        if (sessionStorage.getItem('pp_late_night_shown')) return;

        const hour = new Date().getHours();
        if (hour < 2 || hour >= 5) return;

        // Delay to avoid colliding with page-load toasts (platinum celebrations, etc.)
        setTimeout(() => {
            const messages = [
                'Still hunting at this hour? Respect. \u{1F3C6}',
                'The grind never sleeps. Neither do you, apparently.',
                'Late night plat session? We see you, legend.',
                'Most people are asleep right now. Not trophy hunters.',
                'Fun fact: Trophies earned after midnight count double. (Just kidding.)',
            ];
            const message = messages[Math.floor(Math.random() * messages.length)];
            PlatPursuit.ToastManager.show(message, 'info', 7000);
            sessionStorage.setItem('pp_late_night_shown', 'true');
        }, 3000);
    },

    // ==========================================
    // Easter Egg 4: Footer Logo Spin (Well-Hidden)
    // Clicking the footer logo triggers a satisfying spin animation
    // ==========================================
    _initFooterLogoSpin() {
        const logo = document.getElementById('pp-footer-logo');
        if (!logo) return;

        logo.addEventListener('click', () => {
            if (logo.classList.contains('logo-spin-active')) return;

            logo.classList.add('logo-spin-active');
            this._trackEvent('logo_spin');
            logo.addEventListener('animationend', () => {
                logo.classList.remove('logo-spin-active');
            }, { once: true });
        });
    },

    // ==========================================
    // Tracking: Fire-and-forget event logging for authenticated users
    // ==========================================
    _trackEvent(objectId) {
        if (!document.body.dataset.authenticated) return;
        PlatPursuit.API.post('/api/v1/tracking/site-event/', {
            event_type: 'easter_egg',
            object_id: objectId
        }).catch(() => {});
    },
};

// Export to PlatPursuit namespace
window.PlatPursuit = window.PlatPursuit || {};
window.PlatPursuit.EasterEggs = EasterEggs;

// Auto-initialize on DOMContentLoaded
document.addEventListener('DOMContentLoaded', () => {
    PlatPursuit.EasterEggs.init();
});
