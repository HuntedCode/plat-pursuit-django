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

        const button = field.closest('.pp-input-wrap')?.querySelector('[data-password-toggle]');
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

    // Index = score - 1. The tone per score lives in field.css (.pp-strength[data-score]).
    const STRENGTH_LABELS = ['Weak', 'Fair', 'Good', 'Strong'];
    const STRENGTH_EMPTY = 'Use 8 or more characters';

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

        // The meter sits inside the field, under the box (field.css .pp-strength): four bars and one line.
        // Before anything is typed the line is the rule ("Use 8 or more characters"), not "strength: --".
        const meter = document.createElement('div');
        meter.className = 'pp-strength';
        meter.dataset.score = '0';

        const bars = document.createElement('div');
        bars.className = 'pp-strength__bars';
        bars.setAttribute('aria-hidden', 'true');
        for (let i = 0; i < 4; i++) bars.appendChild(document.createElement('span'));

        // Only the words are live, and only when they change, so typing is not read aloud per key.
        const label = document.createElement('p');
        label.className = 'pp-strength__label';
        label.setAttribute('aria-live', 'polite');

        meter.append(bars, label);
        (input.closest('.pp-input-wrap') || input).after(meter);

        function update() {
            const score = input.value ? evaluateStrength(input.value, emailField?.value) : 0;
            meter.dataset.score = String(score);
            const text = score ? `Strength: ${STRENGTH_LABELS[score - 1]}` : STRENGTH_EMPTY;
            if (label.textContent !== text) label.textContent = text;
        }

        input.addEventListener('input', update);
        if (emailField) {
            emailField.addEventListener('input', update);
        }

        update();
    }

    // Attach strength indicators
    document.querySelectorAll('[data-password-strength]').forEach(initStrengthIndicator);

    // ── Resend countdown ("Check your inbox", FV-08) ────────────────
    // The server renders the button live with the seconds left on allauth's per-address limit in
    // data-resend-wait; this holds it disabled while they count down and hands it back at zero. (Live
    // by default, so without JS it still submits and the view explains an early press.)
    document.querySelectorAll('[data-resend-wait]').forEach(button => {
        let left = parseInt(button.dataset.resendWait, 10) || 0;
        const label = button.querySelector('[data-resend-label]');
        if (left <= 0 || !label) return;

        button.disabled = true;
        label.textContent = `Send another in ${left}s`;
        const timer = setInterval(() => {
            left -= 1;
            if (left > 0) {
                label.textContent = `Send another in ${left}s`;
                return;
            }
            clearInterval(timer);
            button.disabled = false;
            label.textContent = 'Send another link';
        }, 1000);
    });
});
