/**
 * Authentication Forms JavaScript
 * Handles password visibility toggle, password strength indicator,
 * and form enhancements for auth pages.
 */

document.addEventListener('DOMContentLoaded', () => {

    // ── Password Visibility Toggle ──────────────────────────────────

    /**
     * Toggles password field visibility
     * @param {string} fieldId - The ID of the password input field
     */
    function togglePassword(fieldId) {
        const field = document.getElementById(fieldId);
        if (!field) return;

        const button = field.closest('.form-control')?.querySelector('[data-password-toggle]')
            || field.parentElement.querySelector('[data-password-toggle]');
        if (!button) return;

        const icon = button.querySelector('svg');
        if (!icon) return;

        if (field.type === 'password') {
            field.type = 'text';
            button.setAttribute('aria-pressed', 'true');
            button.setAttribute('aria-label', 'Hide password');
            icon.innerHTML = '<path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z"></path><circle cx="12" cy="12" r="3"></circle>';
        } else {
            field.type = 'password';
            button.setAttribute('aria-pressed', 'false');
            button.setAttribute('aria-label', 'Show password');
            icon.innerHTML = '<path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z"></path><circle cx="12" cy="12" r="3"></circle><line x1="3" y1="3" x2="21" y2="21"></line>';
        }
    }

    const passwordToggles = document.querySelectorAll('[data-password-toggle]');
    passwordToggles.forEach(button => {
        const fieldId = button.getAttribute('data-password-toggle');
        button.setAttribute('aria-pressed', 'false');
        button.setAttribute('type', 'button');
        button.addEventListener('click', () => togglePassword(fieldId));
    });

    // ── Password Strength Indicator ─────────────────────────────────

    const COMMON_PASSWORDS = new Set([
        'password', '123456', '12345678', '1234', 'qwerty', '12345', 'dragon',
        'pussy', 'baseball', 'football', 'letmein', 'monkey', 'abc123',
        'mustang', 'michael', 'shadow', 'master', 'jennifer', '111111',
        'jordan', 'superman', 'harley', '1234567', 'fuckme', 'hunter',
        'fuckyou', 'trustno1', 'ranger', 'buster', 'thomas', 'tigger',
        'robert', 'soccer', 'fuck', 'batman', 'test', 'pass', 'killer',
        'hockey', 'charlie', 'love', 'sunshine', 'asshole', '6969',
        'pepper', 'access', '123456789', '654321', 'maggie', 'starwars',
        'silver', 'dallas', 'yankees', 'hello', 'amanda', 'orange',
        'biteme', 'freedom', 'computer', 'sexy', 'thunder', 'ginger',
        'hammer', 'summer', 'corvette', 'eagle1', 'shield', 'warrior',
        'princess', 'platinum', 'trophy', 'playstation', 'welcome',
        'password1', 'password123', 'admin', 'login', 'passw0rd',
        'iloveyou', 'qwerty123', 'admin123', 'welcome1', 'monkey123',
        'dragon1', 'master1', 'changeme', 'qweasd', 'zxcvbnm',
        '1q2w3e4r', '1qaz2wsx', 'trustno1', 'abcdef', 'google',
        'whatever', 'nothing', 'secret', 'please', 'starcraft',
        'blizzard', 'nintendo', 'pokemon', 'minecraft', 'gaming',
        'gamer', 'noob', 'letmein1', 'qazwsx', 'security',
    ]);

    const STRENGTH_LEVELS = [
        { label: 'Weak',   color: 'bg-error'   },
        { label: 'Fair',   color: 'bg-warning'  },
        { label: 'Good',   color: 'bg-warning'  },
        { label: 'Strong', color: 'bg-success'  },
    ];

    /**
     * Evaluates password strength against Django's default validators.
     * Returns a score from 0-4 (number of checks passed).
     */
    /**
     * Detects runs of 4+ sequential ascending/descending characters (abc, 321, etc.)
     */
    function hasSequentialChars(password, minRun) {
        const lower = password.toLowerCase();
        let ascending = 1;
        let descending = 1;

        for (let i = 1; i < lower.length; i++) {
            const diff = lower.charCodeAt(i) - lower.charCodeAt(i - 1);

            ascending = (diff === 1) ? ascending + 1 : 1;
            if (ascending >= minRun) return true;

            descending = (diff === -1) ? descending + 1 : 1;
            if (descending >= minRun) return true;
        }
        return false;
    }

    /**
     * Evaluates password strength using two layers:
     * 1. Django validator checks (length, common, numeric, email similarity)
     *    Failing any = Weak (score 1).
     * 2. Quality checks (character diversity, sequential/repeated patterns, length bonus)
     *    These determine Fair (2), Good (3), or Strong (4).
     *
     * Returns 0 (empty), 1 (Weak), 2 (Fair), 3 (Good), or 4 (Strong).
     */
    function evaluateStrength(password, emailValue) {
        if (!password) return 0;

        // Layer 1: Django validators (any failure = Weak)
        const meetsLength = password.length >= 8;
        const isCommon = COMMON_PASSWORDS.has(password.toLowerCase());
        const allNumeric = /^\d+$/.test(password);

        let emailSimilar = false;
        if (emailValue) {
            const emailLocal = emailValue.split('@')[0].toLowerCase();
            const pwLower = password.toLowerCase();
            emailSimilar = emailLocal.length >= 3
                && (pwLower.includes(emailLocal) || emailLocal.includes(pwLower));
        }

        if (!meetsLength || isCommon || allNumeric || emailSimilar) return 1;

        // Layer 2: Quality scoring
        const hasUpper = /[A-Z]/.test(password);
        const hasLower = /[a-z]/.test(password);
        const hasDigit = /\d/.test(password);
        const hasSpecial = /[^A-Za-z0-9]/.test(password);
        const diversity = [hasUpper, hasLower, hasDigit, hasSpecial].filter(Boolean).length;

        const hasRepeats = /(.)\1{2,}/.test(password);
        const hasSequential = hasSequentialChars(password, 4);

        // Fair: passes Django but low diversity or has patterns
        if (diversity < 2 || hasRepeats || hasSequential) return 2;

        // Good: 2+ char types, no bad patterns
        if (diversity < 3 || password.length < 12) return 3;

        // Strong: 3+ char types, 12+ chars, clean patterns
        return 4;
    }

    /**
     * Creates and attaches a password strength indicator below the target input.
     */
    function initStrengthIndicator(input) {
        const emailFieldId = input.getAttribute('data-strength-email');
        const emailField = emailFieldId ? document.getElementById(emailFieldId) : null;

        // Build the indicator DOM
        const container = document.createElement('div');
        container.className = 'mt-2 bg-white/[0.03] border border-base-content/5 rounded-lg p-2';
        container.setAttribute('aria-live', 'polite');

        const barRow = document.createElement('div');
        barRow.className = 'flex gap-1 mb-1';

        const segments = [];
        for (let i = 0; i < 4; i++) {
            const seg = document.createElement('div');
            seg.className = 'h-1.5 flex-1 rounded-full bg-base-content/10 transition-colors duration-200';
            barRow.appendChild(seg);
            segments.push(seg);
        }

        const label = document.createElement('p');
        label.className = 'text-xs text-base-content/50';
        label.innerHTML = 'Password strength: <span class="font-semibold">--</span>';

        container.appendChild(barRow);
        container.appendChild(label);

        // Insert after the input's parent .form-control
        const formControl = input.closest('.form-control');
        if (formControl) {
            formControl.after(container);
        } else {
            input.parentElement.after(container);
        }

        const labelSpan = label.querySelector('span');

        function update() {
            const score = evaluateStrength(input.value, emailField?.value);
            const displayScore = input.value ? score : 0;

            segments.forEach((seg, i) => {
                seg.className = 'h-1.5 flex-1 rounded-full transition-colors duration-200';
                if (input.value && i < displayScore) {
                    seg.classList.add(STRENGTH_LEVELS[Math.max(0, displayScore - 1)].color);
                } else {
                    seg.classList.add('bg-base-content/10');
                }
            });

            if (input.value) {
                const level = STRENGTH_LEVELS[Math.max(0, displayScore - 1)];
                labelSpan.textContent = level.label;
            } else {
                labelSpan.textContent = '--';
            }
        }

        input.addEventListener('input', update);
        if (emailField) {
            emailField.addEventListener('input', update);
        }

        update();
    }

    // Attach strength indicators
    document.querySelectorAll('[data-password-strength]').forEach(initStrengthIndicator);
});
