/**
 * Authentication Forms JavaScript
 * Handles password visibility toggle and form enhancements for auth pages
 */

document.addEventListener('DOMContentLoaded', () => {
    /**
     * Toggles password field visibility
     * @param {string} fieldId - The ID of the password input field
     */
    function togglePassword(fieldId) {
        const field = document.getElementById(fieldId);
        if (!field) return;

        const button = field.parentElement.querySelector('[data-password-toggle]');
        if (!button) return;

        const icon = button.querySelector('svg');
        if (!icon) return;

        if (field.type === 'password') {
            // Show password
            field.type = 'text';
            button.setAttribute('aria-pressed', 'true');
            button.setAttribute('aria-label', 'Hide password');

            // Update icon to "visible" (eye without slash)
            icon.innerHTML = '<path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z"></path><circle cx="12" cy="12" r="3"></circle>';
        } else {
            // Hide password
            field.type = 'password';
            button.setAttribute('aria-pressed', 'false');
            button.setAttribute('aria-label', 'Show password');

            // Update icon to "hidden" (eye with slash)
            icon.innerHTML = '<path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z"></path><circle cx="12" cy="12" r="3"></circle><line x1="3" y1="3" x2="21" y2="21"></line>';
        }
    }

    // Attach event listeners to all password toggle buttons
    const passwordToggles = document.querySelectorAll('[data-password-toggle]');
    passwordToggles.forEach(button => {
        const fieldId = button.getAttribute('data-password-toggle');

        // Set initial ARIA attributes
        button.setAttribute('aria-pressed', 'false');
        button.setAttribute('type', 'button');

        button.addEventListener('click', () => {
            togglePassword(fieldId);
        });
    });
});
