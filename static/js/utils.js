/**
 * PlatPursuit Shared Utilities
 * Common functions used across multiple JavaScript modules
 */

/**
 * Toast Notification System
 * Shows temporary alert messages in the toast container
 */
const ToastManager = {
    /**
     * Show a toast notification
     * @param {string} message - The message to display
     * @param {string} type - Type of toast: 'info', 'success', 'error', 'warning'
     * @param {number} duration - How long to show toast in ms (default: 5000)
     */
    show(message, type = 'info', duration = 5000) {
        const container = document.getElementById('toast-container');
        if (!container) {
            console.log(`[${type.toUpperCase()}]`, message);
            return;
        }

        // Create toast element
        const toast = document.createElement('div');
        toast.className = 'alert shadow-lg mb-2 max-w-md';

        // Set alert type styling
        switch(type) {
            case 'success':
                toast.classList.add('alert-success');
                break;
            case 'error':
                toast.classList.add('alert-error');
                break;
            case 'warning':
                toast.classList.add('alert-warning');
                break;
            default:
                toast.classList.add('alert-info');
        }

        // Create icon based on type
        let icon = '';
        switch(type) {
            case 'success':
                icon = `<svg xmlns="http://www.w3.org/2000/svg" class="stroke-current shrink-0 h-6 w-6" fill="none" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 12l2 2 4-4m6 2a9 9 0 11-18 0 9 9 0 0118 0z" /></svg>`;
                break;
            case 'error':
                icon = `<svg xmlns="http://www.w3.org/2000/svg" class="stroke-current shrink-0 h-6 w-6" fill="none" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M10 14l2-2m0 0l2-2m-2 2l-2-2m2 2l2 2m7-2a9 9 0 11-18 0 9 9 0 0118 0z" /></svg>`;
                break;
            case 'warning':
                icon = `<svg xmlns="http://www.w3.org/2000/svg" class="stroke-current shrink-0 h-6 w-6" fill="none" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-3L13.732 4c-.77-1.333-2.694-1.333-3.464 0L3.34 16c-.77 1.333.192 3 1.732 3z" /></svg>`;
                break;
            default:
                icon = `<svg xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" class="stroke-current shrink-0 w-6 h-6"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M13 16h-1v-4h-1m1-4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z"></path></svg>`;
        }

        toast.innerHTML = `
            <div class="flex items-center gap-2">
                ${icon}
                <span>${message}</span>
            </div>
        `;

        // Add to container
        container.appendChild(toast);

        // Auto-remove after duration (longer for errors)
        const autoRemoveDuration = type === 'error' ? Math.max(duration, 7000) : duration;
        setTimeout(() => {
            toast.style.opacity = '0';
            toast.style.transform = 'translateX(100%)';
            toast.style.transition = 'all 0.3s ease-in-out';
            setTimeout(() => toast.remove(), 300);
        }, autoRemoveDuration);
    },

    /**
     * Convenience methods for different toast types
     */
    success(message, duration = 5000) {
        this.show(message, 'success', duration);
    },

    error(message, duration = 7000) {
        this.show(message, 'error', duration);
    },

    warning(message, duration = 5000) {
        this.show(message, 'warning', duration);
    },

    info(message, duration = 5000) {
        this.show(message, 'info', duration);
    }
};

/**
 * CSRF Token Utilities
 * Retrieves CSRF token for Django requests
 */
const CSRFToken = {
    /**
     * Get CSRF token from page
     * Checks both hidden input field and cookies
     */
    get() {
        return document.querySelector('[name=csrfmiddlewaretoken]')?.value ||
               document.cookie.split('; ').find(row => row.startsWith('csrftoken='))?.split('=')[1] || '';
    },

    /**
     * Get headers object with CSRF token for fetch requests
     * @param {Object} additionalHeaders - Additional headers to include
     * @returns {Object} Headers object including CSRF token
     */
    getHeaders(additionalHeaders = {}) {
        return {
            'X-CSRFToken': this.get(),
            ...additionalHeaders
        };
    }
};

/**
 * Time Formatting Utilities
 * Format timestamps for display
 */
const TimeFormatter = {
    /**
     * Format timestamp as relative time (e.g., "5m ago", "2h ago")
     * @param {string|Date} timestamp - Timestamp to format
     * @returns {string} Relative time string
     */
    relative(timestamp) {
        const date = new Date(timestamp);
        const now = new Date();
        const diff = Math.floor((now - date) / 1000);

        if (diff < 60) return 'Just now';
        if (diff < 3600) return `${Math.floor(diff / 60)}m ago`;
        if (diff < 86400) return `${Math.floor(diff / 3600)}h ago`;
        if (diff < 604800) return `${Math.floor(diff / 86400)}d ago`;
        if (diff < 2592000) return `${Math.floor(diff / 604800)}w ago`;
        return date.toLocaleDateString();
    },

    /**
     * Format timestamp as absolute date/time
     * @param {string|Date} timestamp - Timestamp to format
     * @param {Object} options - Intl.DateTimeFormat options
     * @returns {string} Formatted date string
     */
    absolute(timestamp, options = {}) {
        const date = new Date(timestamp);
        return date.toLocaleString(undefined, options);
    }
};

/**
 * API Request Utilities
 * Helper functions for making API requests
 */
const API = {
    /**
     * Make a fetch request with CSRF token and error handling
     * @param {string} url - API endpoint URL
     * @param {Object} options - Fetch options
     * @returns {Promise} Response data or throws error
     */
    async request(url, options = {}) {
        const defaultOptions = {
            credentials: 'same-origin',
            headers: {
                'X-CSRFToken': CSRFToken.get(),
                ...options.headers
            }
        };

        const response = await fetch(url, { ...defaultOptions, ...options });

        if (!response.ok) {
            const error = new Error(`API request failed: ${response.status}`);
            error.response = response;
            throw error;
        }

        // Return response directly if no content
        if (response.status === 204) {
            return null;
        }

        const contentType = response.headers.get('content-type');
        if (contentType && contentType.includes('application/json')) {
            return await response.json();
        }

        return await response.text();
    },

    /**
     * Convenience methods for different HTTP verbs
     */
    async get(url, options = {}) {
        return this.request(url, { ...options, method: 'GET' });
    },

    async post(url, data, options = {}) {
        const headers = { 'Content-Type': 'application/json', ...options.headers };
        return this.request(url, {
            ...options,
            method: 'POST',
            headers,
            body: JSON.stringify(data)
        });
    },

    async put(url, data, options = {}) {
        const headers = { 'Content-Type': 'application/json', ...options.headers };
        return this.request(url, {
            ...options,
            method: 'PUT',
            headers,
            body: JSON.stringify(data)
        });
    },

    async delete(url, options = {}) {
        return this.request(url, { ...options, method: 'DELETE' });
    }
};

// Export for use in other modules
window.PlatPursuit = window.PlatPursuit || {};
window.PlatPursuit.ToastManager = ToastManager;
window.PlatPursuit.CSRFToken = CSRFToken;
window.PlatPursuit.TimeFormatter = TimeFormatter;
window.PlatPursuit.API = API;
