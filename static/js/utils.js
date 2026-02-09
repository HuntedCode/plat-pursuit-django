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
    },

    /**
     * Format seconds as countdown timer (HH:MM:SS)
     * @param {number} totalSeconds - Total seconds to format
     * @returns {string} Formatted countdown string
     */
    countdown(totalSeconds) {
        const h = Math.floor(totalSeconds / 3600).toString().padStart(2, '0');
        const m = Math.floor((totalSeconds % 3600) / 60).toString().padStart(2, '0');
        const s = Math.floor(totalSeconds % 60).toString().padStart(2, '0');
        return `${h}:${m}:${s}`;
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
        // Merge headers separately to ensure CSRF token is always included
        const mergedHeaders = {
            ...options.headers,
            'X-CSRFToken': CSRFToken.get()
        };

        const finalOptions = {
            credentials: 'same-origin',
            ...options,
            headers: mergedHeaders
        };

        const response = await fetch(url, finalOptions);

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
        return this.request(url, {
            ...options,
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
                ...options.headers
            },
            body: JSON.stringify(data)
        });
    },

    async put(url, data, options = {}) {
        return this.request(url, {
            ...options,
            method: 'PUT',
            headers: {
                'Content-Type': 'application/json',
                ...options.headers
            },
            body: JSON.stringify(data)
        });
    },

    async delete(url, options = {}) {
        return this.request(url, { ...options, method: 'DELETE' });
    },

    /**
     * POST with FormData (for file uploads)
     * Does NOT set Content-Type - browser sets multipart boundary automatically
     * @param {string} url - API endpoint URL
     * @param {FormData} formData - FormData object to send
     * @param {Object} options - Additional fetch options
     * @returns {Promise} Response data or throws error
     */
    async postFormData(url, formData, options = {}) {
        return this.request(url, {
            ...options,
            method: 'POST',
            body: formData
        });
    },

    /**
     * Fetch HTML content (for partial templates, infinite scroll, etc.)
     * @param {string} url - URL to fetch
     * @param {Object} options - Additional fetch options
     * @returns {Promise<string>} HTML string
     */
    async fetchHTML(url, options = {}) {
        return this.request(url, {
            ...options,
            method: 'GET',
            headers: {
                'X-Requested-With': 'XMLHttpRequest',
                ...options.headers
            }
        });
    }
};

/**
 * Unsaved Changes Warning Manager
 * Reusable system for warning users before navigating away with unsaved changes
 */
const UnsavedChangesManager = {
    _config: null,
    _pendingNavigation: null,
    _isNavigatingAway: false,
    _initialized: false,
    _boundHandlers: null,

    /**
     * Initialize the unsaved changes warning system
     * @param {Object} config Configuration object
     * @param {Function} config.hasUnsavedChanges - REQUIRED: Returns true if there are unsaved changes
     * @param {Function} config.onSaveAndLeave - OPTIONAL: Async function to save before navigating
     * @param {boolean} config.showSaveButton - OPTIONAL: Whether to show "Save & Leave" button (default: false)
     * @param {string} config.modalId - OPTIONAL: Custom modal element ID (default: 'unsaved-changes-modal')
     */
    init(config) {
        if (this._initialized) {
            this.destroy();
        }

        if (!config || typeof config.hasUnsavedChanges !== 'function') {
            console.error('UnsavedChangesManager: hasUnsavedChanges function is required');
            return;
        }

        this._config = {
            hasUnsavedChanges: config.hasUnsavedChanges,
            onSaveAndLeave: config.onSaveAndLeave || null,
            showSaveButton: config.showSaveButton || false,
            modalId: config.modalId || 'unsaved-changes-modal'
        };

        this._pendingNavigation = null;
        this._isNavigatingAway = false;

        this._setupModal();
        this._setupEventListeners();
        this._initialized = true;
    },

    /**
     * Setup modal button handlers
     */
    _setupModal() {
        const modal = document.getElementById(this._config.modalId);
        if (!modal) return;

        const stayBtn = document.getElementById('unsaved-stay-btn');
        const discardBtn = document.getElementById('unsaved-discard-btn');
        const saveBtn = document.getElementById('unsaved-save-btn');

        // Show/hide save button based on config
        if (saveBtn) {
            if (this._config.showSaveButton && this._config.onSaveAndLeave) {
                saveBtn.classList.remove('hidden');
            } else {
                saveBtn.classList.add('hidden');
            }
        }

        // Store bound handlers for cleanup
        this._boundHandlers = {
            stay: () => {
                this._pendingNavigation = null;
                modal.close();
            },
            discard: () => {
                this._isNavigatingAway = true;
                modal.close();
                if (this._pendingNavigation) {
                    window.location.href = this._pendingNavigation;
                }
            },
            save: async () => {
                if (!this._config.onSaveAndLeave) return;

                try {
                    saveBtn.classList.add('loading');
                    await this._config.onSaveAndLeave(this._pendingNavigation);
                    ToastManager.show('Saved!', 'success');
                    this._isNavigatingAway = true;
                    modal.close();
                    if (this._pendingNavigation) {
                        window.location.href = this._pendingNavigation;
                    }
                } catch (error) {
                    ToastManager.show(error.message || 'Failed to save', 'error');
                } finally {
                    saveBtn.classList.remove('loading');
                }
            }
        };

        if (stayBtn) {
            stayBtn.addEventListener('click', this._boundHandlers.stay);
        }
        if (discardBtn) {
            discardBtn.addEventListener('click', this._boundHandlers.discard);
        }
        if (saveBtn && this._config.showSaveButton) {
            saveBtn.addEventListener('click', this._boundHandlers.save);
        }
    },

    /**
     * Setup navigation event listeners
     */
    _setupEventListeners() {
        // Bound handlers for cleanup
        this._boundHandlers.click = (e) => {
            const link = e.target.closest('a');
            if (!link) return;

            const href = link.getAttribute('href');
            if (!href || href.startsWith('#') || href.startsWith('javascript:')) return;
            if (link.target === '_blank') return;

            if (this._isNavigatingAway || !this._config.hasUnsavedChanges()) return;

            e.preventDefault();
            this._showModal(href);
        };

        this._boundHandlers.beforeunload = (e) => {
            if (this._isNavigatingAway || !this._config.hasUnsavedChanges()) return;

            e.preventDefault();
            e.returnValue = '';
            return '';
        };

        this._boundHandlers.popstate = () => {
            if (this._isNavigatingAway || !this._config.hasUnsavedChanges()) return;

            history.pushState(null, '', window.location.href);
            this._showModal(document.referrer || '/');
        };

        // Allow form submissions to proceed without warning
        this._boundHandlers.submit = () => {
            this._isNavigatingAway = true;
        };

        document.addEventListener('click', this._boundHandlers.click);
        window.addEventListener('beforeunload', this._boundHandlers.beforeunload);
        window.addEventListener('popstate', this._boundHandlers.popstate);
        document.addEventListener('submit', this._boundHandlers.submit);

        // Push initial state for popstate handling
        history.pushState(null, '', window.location.href);
    },

    /**
     * Show the unsaved changes modal
     * @param {string} targetUrl - URL the user is trying to navigate to
     */
    _showModal(targetUrl) {
        const modal = document.getElementById(this._config.modalId);
        if (!modal) {
            // Fallback if modal doesn't exist
            if (confirm('You have unsaved changes. Leave anyway?')) {
                this._isNavigatingAway = true;
                window.location.href = targetUrl;
            }
            return;
        }

        this._pendingNavigation = targetUrl;
        modal.showModal();
    },

    /**
     * Mark form as clean (call after successful save)
     * This allows navigation without warning
     */
    markAsClean() {
        // The hasUnsavedChanges callback should return false after save
        // This method is a no-op but provides semantic clarity
    },

    /**
     * Force navigation without checking for changes
     * @param {string} url - URL to navigate to
     */
    forceNavigate(url) {
        this._isNavigatingAway = true;
        window.location.href = url;
    },

    /**
     * Check if the manager is currently active
     * @returns {boolean}
     */
    isActive() {
        return this._initialized;
    },

    /**
     * Cleanup and destroy the manager
     */
    destroy() {
        if (!this._initialized) return;

        // Remove event listeners
        if (this._boundHandlers) {
            document.removeEventListener('click', this._boundHandlers.click);
            window.removeEventListener('beforeunload', this._boundHandlers.beforeunload);
            window.removeEventListener('popstate', this._boundHandlers.popstate);
            document.removeEventListener('submit', this._boundHandlers.submit);

            // Remove modal button listeners
            const stayBtn = document.getElementById('unsaved-stay-btn');
            const discardBtn = document.getElementById('unsaved-discard-btn');
            const saveBtn = document.getElementById('unsaved-save-btn');

            if (stayBtn) stayBtn.removeEventListener('click', this._boundHandlers.stay);
            if (discardBtn) discardBtn.removeEventListener('click', this._boundHandlers.discard);
            if (saveBtn) saveBtn.removeEventListener('click', this._boundHandlers.save);
        }

        this._config = null;
        this._pendingNavigation = null;
        this._isNavigatingAway = false;
        this._boundHandlers = null;
        this._initialized = false;
    }
};

/**
 * HTML Utilities
 * Safe HTML escaping to prevent XSS in string interpolation
 */
const HTMLUtils = {
    /**
     * Escape HTML special characters to prevent XSS
     * @param {string} text - Raw text to escape
     * @returns {string} HTML-safe string
     */
    escape(text) {
        const div = document.createElement('div');
        div.textContent = text;
        return div.innerHTML;
    }
};

/**
 * Create a debounced version of a function
 * @param {Function} fn - Function to debounce
 * @param {number} delay - Delay in milliseconds (default: 300)
 * @returns {Function} Debounced function
 */
function debounce(fn, delay = 300) {
    let timeout;
    return function(...args) {
        clearTimeout(timeout);
        timeout = setTimeout(() => fn.apply(this, args), delay);
    };
}

/**
 * Infinite Scroller Factory
 * Creates reusable infinite scroll behavior with IntersectionObserver
 */
const InfiniteScroller = {
    /**
     * Create an infinite scroller instance
     * @param {Object} config - Configuration object
     * @param {string} config.gridId - ID of the grid/container element
     * @param {string} config.sentinelId - ID of the sentinel element to observe
     * @param {string} config.loadingId - ID of the loading indicator element
     * @param {number} config.paginateBy - Number of items per page (used to determine if more pages exist)
     * @param {string} [config.formSelector] - CSS selector for filter form (resets page on submit)
     * @param {string} [config.scrollKey] - localStorage key for preserving scroll position
     * @param {string} [config.cardSelector='.card'] - CSS selector for cards in fetched HTML
     * @param {Function} [config.onTabChange] - Callback for tab change behavior
     * @returns {Object} Controller with destroy() method
     */
    create(config) {
        const grid = document.getElementById(config.gridId);
        const sentinel = document.getElementById(config.sentinelId);
        const loading = document.getElementById(config.loadingId);
        if (!grid || !sentinel || !loading) return null;

        const cardSelector = config.cardSelector || '.card';
        let page = 2;
        const baseUrl = window.location.pathname;
        const queryParams = new URLSearchParams(window.location.search);
        queryParams.delete('page');
        let nextPageUrl = `${baseUrl}?page=${page}&${queryParams.toString()}`;
        let isLoading = false;

        const loadMore = async () => {
            if (!nextPageUrl || isLoading) return;
            isLoading = true;
            loading.classList.remove('hidden');

            try {
                const response = await fetch(nextPageUrl, {
                    headers: { 'X-Requested-With': 'XMLHttpRequest' }
                });
                if (!response.ok) {
                    if (response.status === 404) {
                        nextPageUrl = null;
                    }
                    return;
                }
                const html = await response.text();
                const parser = new DOMParser();
                const doc = parser.parseFromString(html, 'text/html');
                const newCards = doc.querySelectorAll(cardSelector);
                if (newCards.length === 0) {
                    nextPageUrl = null;
                } else {
                    newCards.forEach(card => grid.appendChild(card.cloneNode(true)));
                    page++;
                    nextPageUrl = `${baseUrl}?page=${page}&${queryParams.toString()}`;
                }
            } catch (error) {
                nextPageUrl = null;
            } finally {
                isLoading = false;
                loading.classList.add('hidden');
            }
        };

        // Form submit resets pagination
        if (config.formSelector) {
            const form = document.querySelector(config.formSelector);
            if (form) {
                form.addEventListener('submit', () => {
                    if (config.scrollKey) {
                        localStorage.setItem(config.scrollKey, window.scrollY);
                    }
                    page = 2;
                    queryParams.delete('page');
                    nextPageUrl = `${baseUrl}?page=${page}&${queryParams.toString()}`;
                    if (!config.scrollKey) {
                        grid.innerHTML = '';
                    }
                });
            }
        }

        // Restore scroll position if configured
        if (config.scrollKey) {
            const savedScroll = localStorage.getItem(config.scrollKey);
            if (savedScroll) {
                window.scrollTo({ top: parseInt(savedScroll), behavior: 'smooth' });
                localStorage.removeItem(config.scrollKey);
            }
        }

        const observer = new IntersectionObserver(entries => {
            if (entries[0].isIntersecting) {
                loadMore();
            }
        }, { threshold: 1.0 });

        if (grid.children.length >= config.paginateBy) {
            observer.observe(sentinel);
        }

        return {
            destroy() {
                observer.disconnect();
            }
        };
    }
};

// Export for use in other modules
window.PlatPursuit = window.PlatPursuit || {};
window.PlatPursuit.ToastManager = ToastManager;
window.PlatPursuit.CSRFToken = CSRFToken;
window.PlatPursuit.TimeFormatter = TimeFormatter;
window.PlatPursuit.API = API;
window.PlatPursuit.UnsavedChangesManager = UnsavedChangesManager;
window.PlatPursuit.HTMLUtils = HTMLUtils;
window.PlatPursuit.debounce = debounce;
window.PlatPursuit.InfiniteScroller = InfiniteScroller;
