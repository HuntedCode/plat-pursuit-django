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
        // If a dialog is open, use its toast container (dialogs use the browser
        // top layer which renders above all z-indices).
        // Note: querySelector returns the first open dialog. If multiple dialogs
        // are stacked, toasts go to the first one with a container.
        const openDialog = document.querySelector('dialog[open]');
        const modalContainer = openDialog?.querySelector('.modal-toast-container');
        const container = modalContainer || document.getElementById('toast-container');
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

        const wrapper = document.createElement('div');
        wrapper.className = 'flex items-center gap-2';
        wrapper.innerHTML = icon;
        const span = document.createElement('span');
        span.textContent = message;
        wrapper.appendChild(span);
        toast.appendChild(wrapper);

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
               document.querySelector('meta[name="csrf-token"]')?.content ||
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

    async patch(url, data, options = {}) {
        return this.request(url, {
            ...options,
            method: 'PATCH',
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
 * Count a number to a target with easeOutCubic. Reads the target from the element's `data-countup`
 * attribute (falling back to its current text), honours `data-countup-decimals` for fixed-decimal
 * values, and prefers-reduced-motion (jumps straight to the target). Starts from 0 by default, or
 * from `opts.from` -- pass the previous value to tick a live-updating counter up OR down to its new
 * value (e.g. a filtered result count) instead of resetting to 0. This is the canonical shared
 * count-up; career.html + home-motion.js still hand-roll their own -- new callers should use this.
 * @param {HTMLElement} el
 * @param {number} [dur=750] duration in ms
 * @param {{from?: number}} [opts] start value (default 0); pass the old value for old->new ticking
 */
function countUp(el, dur = 750, opts = {}) {
    if (!el) return;
    const dec = parseInt(el.dataset.countupDecimals || '0', 10);
    const raw = el.dataset.countup != null ? el.dataset.countup : (el.textContent || '').replace(/,/g, '');
    const target = parseFloat(raw);
    if (isNaN(target)) return;
    const from = (opts.from != null && !isNaN(opts.from)) ? opts.from : 0;
    const fmt = (v) => (dec ? v.toFixed(dec) : Math.round(v).toLocaleString());
    const reduce = window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches;
    if (reduce || target === from) { el.textContent = fmt(target); return; }
    el.textContent = fmt(from);
    let start = null;
    function step(ts) {
        if (start === null) start = ts;
        const p = Math.min(1, (ts - start) / dur);
        el.textContent = fmt(from + (target - from) * (1 - Math.pow(1 - p, 3)));
        if (p < 1) requestAnimationFrame(step);
        else el.textContent = fmt(target);
    }
    requestAnimationFrame(step);
}

/**
 * Animate a collapsible panel open/closed (height + opacity), toggling its `hidden` attribute. Used by
 * the career / collection / badge filter panels (the games browse panel runs its own copy because it also
 * drives chip-hide + scroll-fades off the same toggle). The panel MUST have `overflow: hidden` and a
 * `height`/`opacity` CSS transition, and it MUST be able to collapse to a true 0 -- put any
 * padding/border/gap on an INNER wrapper, since with box-sizing:border-box padding+border would clamp
 * the collapsed height and snap away when `hidden` lands. Callers own the toggle's aria/is-open state.
 * @param {HTMLElement} panel
 * @param {boolean} open
 * @param {boolean} [animate=true] pass false (or under reduced-motion) to toggle instantly
 */
function animatePanel(panel, open, animate) {
    if (!panel) return;
    if (panel._panelAnim) { panel.removeEventListener('transitionend', panel._panelAnim); panel._panelAnim = null; }
    var reduce = window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches;
    if (animate === false || reduce) {
        panel.style.height = ''; panel.style.opacity = '';
        if (open) { panel.removeAttribute('hidden'); } else { panel.setAttribute('hidden', ''); }
        return;
    }
    if (open) {
        // Collapse before revealing so removing `hidden` doesn't paint a full-height panel for a frame;
        // scrollHeight measures the true target while collapsed (overflow-hidden).
        panel.style.height = '0px'; panel.style.opacity = '0';
        panel.removeAttribute('hidden');
        var target = panel.scrollHeight;
        void panel.offsetHeight;
        panel.style.height = target + 'px'; panel.style.opacity = '1';
        panel._panelAnim = function (ev) {
            if (ev.target !== panel || ev.propertyName !== 'height') { return; }
            panel.removeEventListener('transitionend', panel._panelAnim); panel._panelAnim = null;
            panel.style.height = ''; panel.style.opacity = '';   // release to auto so content reflows
        };
    } else {
        panel.style.height = panel.scrollHeight + 'px'; panel.style.opacity = '1';
        void panel.offsetHeight;
        panel.style.height = '0px'; panel.style.opacity = '0';
        panel._panelAnim = function (ev) {
            if (ev.target !== panel || ev.propertyName !== 'height') { return; }
            panel.removeEventListener('transitionend', panel._panelAnim); panel._panelAnim = null;
            panel.setAttribute('hidden', ''); panel.style.height = ''; panel.style.opacity = '';
        };
    }
    panel.addEventListener('transitionend', panel._panelAnim);
}

/**
 * Drag Reorder Manager
 * Wraps SortableJS for smooth, touch-friendly drag-and-drop reordering.
 * Drop-in replacement: same constructor API, same onReorder callback signature.
 */
class DragReorderManager {
    /**
     * Initialize drag reordering on a container
     * @param {Object} config Configuration object
     * @param {HTMLElement} config.container - Container element
     * @param {string} config.itemSelector - CSS selector for draggable items
     * @param {Function} config.onReorder - Callback when drop completes: (itemId, newPosition, allItemIds)
     * @param {string} [config.handleSelector] - Optional selector for drag handle (defaults to item itself)
     * @param {string} [config.placeholderClass] - Custom ghost CSS class (mapped to SortableJS ghostClass)
     * @param {Function} [config.onPlaceholderCreate] - Called on drag start with (ghostEl, draggedEl)
     * @param {boolean} [config.useXY] - Ignored (kept for backward compatibility; SortableJS handles 2D natively)
     * @param {Function} [config.onStart] - Optional callback when drag starts
     * @param {Function} [config.onEnd] - Optional callback when drag ends
     * @param {string|object} [config.group] - SortableJS group (string or {name, put, pull}). When two managers share a group, items can be dragged between their containers.
     * @param {Function} [config.onMove] - Callback when an item is dropped into THIS container from ANOTHER (cross-container drop). Signature: (itemId, evt) => Promise. evt.from/evt.to/evt.newIndex available. Replaces the onReorder call for that drop.
     * @param {Function} [config.canAccept] - Predicate (draggedEl, toContainer, fromContainer) => bool. Return false to reject the drop. Mirrors SortableJS onMove.
     */
    constructor(config) {
        this.container = config.container;
        this.itemSelector = config.itemSelector;
        this.onReorder = config.onReorder;
        this.handleSelector = config.handleSelector || null;
        this.onPlaceholderCreate = config.onPlaceholderCreate || null;
        this._onStartCallback = config.onStart || null;
        this._onEndCallback = config.onEnd || null;
        this.group = config.group || null;
        this.onMove = config.onMove || null;
        this.canAccept = config.canAccept || null;
        this.sortable = null;

        this._initSortable();
    }

    _initSortable() {
        if (typeof Sortable === 'undefined') {
            console.warn('DragReorderManager: SortableJS not loaded, drag reordering disabled.');
            return;
        }

        const sortableConfig = {
            draggable: this.itemSelector,
            animation: 200,
            easing: 'cubic-bezier(0.25, 1, 0.5, 1)',
            ghostClass: 'sortable-ghost',
            chosenClass: 'sortable-chosen',
            dragClass: 'sortable-drag',
            forceFallback: true,
            fallbackClass: 'sortable-fallback',
            fallbackOnBody: true,
            fallbackTolerance: 3,
            // Prevent flicker: require cursor to cross 65% of an item before swapping.
            // This stops the rapid back-and-forth when hovering between two items
            // in a CSS Grid layout (grid reflow moves items under the cursor).
            swapThreshold: 0.65,
            invertSwap: true,
            // Generous empty-container hit area so cross-bucket drops
            // into an empty sub-area / area don't require pixel-perfect
            // targeting. SortableJS default is 5px; bump to 20 so a
            // ~32px min-height empty bucket is comfortably hittable.
            emptyInsertThreshold: 20,

            onStart: (evt) => {
                if (this.onPlaceholderCreate) {
                    // SortableJS creates the ghost automatically; let callers customize it
                    // The ghost is a clone placed in the DOM where the item was
                    requestAnimationFrame(() => {
                        const ghost = this.container.querySelector('.sortable-ghost');
                        if (ghost) this.onPlaceholderCreate(ghost, evt.item);
                    });
                }
                if (this._onStartCallback) this._onStartCallback(evt);
            },

            onEnd: (evt) => {
                if (this._onEndCallback) this._onEndCallback(evt);

                // Cross-container drop: fire onMove instead of onReorder.
                // The destination manager owns the post-drop sync (it
                // knows the new bucket's identity); the source manager
                // doesn't see this event (SortableJS routes onEnd to the
                // manager whose container the drop landed in).
                if (evt.from !== evt.to) {
                    if (this.onMove) {
                        const itemId = evt.item.dataset.itemId;
                        this.onMove(itemId, evt);
                    }
                    return;
                }

                if (evt.oldIndex === evt.newIndex) return;

                const items = [...this.container.querySelectorAll(this.itemSelector)];
                const itemId = evt.item.dataset.itemId;
                const allItemIds = items.map(item => item.dataset.itemId);

                if (this.onReorder) {
                    this.onReorder(itemId, evt.newIndex, allItemIds);
                }
            },
        };

        if (this.handleSelector) {
            sortableConfig.handle = this.handleSelector;
        }
        if (this.group) {
            sortableConfig.group = this.group;
        }
        // SortableJS onMove fires during the drag; returning false rejects
        // the drop. canAccept is the manager's hook for that, used e.g. to
        // refuse cross-area marker moves where the merge service doesn't
        // support area migration.
        if (this.canAccept) {
            sortableConfig.onMove = (evt) => {
                return this.canAccept(evt.dragged, evt.to, evt.from);
            };
        }

        this.sortable = Sortable.create(this.container, sortableConfig);
    }

    destroy() {
        if (this.sortable) {
            this.sortable.destroy();
            this.sortable = null;
        }
    }
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
     * @param {Function} [config.onAppend] - Called with the array of freshly-appended card nodes after each page load
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
                    const appended = [];
                    newCards.forEach(card => { const clone = card.cloneNode(true); grid.appendChild(clone); appended.push(clone); });
                    // Optional hook so callers can wire freshly-appended cards (e.g. a scroll-reveal observer).
                    if (typeof config.onAppend === 'function') { try { config.onAppend(appended); } catch (e) { /* non-fatal */ } }
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

        const observer = new ZoomAwareObserver(entries => {
            if (entries[0].isIntersecting) {
                loadMore();
            }
        }, { threshold: 0.1, scrollBuffer: 100 });

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

/**
 * ZoomAwareObserver - Drop-in IntersectionObserver replacement.
 *
 * Historically paired with the retired ZoomScaler: when a page scaled itself sub-768px via
 * transform: scale() + overflow:hidden on #zoom-container, native IntersectionObserver clipping
 * broke, so this fell back to a scroll-event + getBoundingClientRect() path. ZoomScaler is gone,
 * so `_isZoomActive()` is always false and this delegates 100% to native IntersectionObserver --
 * kept as a drop-in so its several callers don't need touching; the fallback path is now dead-but-inert.
 *
 * On desktop (no zoom), delegates 100% to native IntersectionObserver with zero overhead.
 *
 * Options: all standard IntersectionObserver options, plus:
 *   scrollBuffer {number} - pixels beyond viewport to trigger in scroll mode (default: 100)
 *
 * Usage: new PlatPursuit.ZoomAwareObserver(callback, { threshold: 0.1, scrollBuffer: 100 })
 */
class ZoomAwareObserver {
    constructor(callback, options = {}) {
        this._callback = callback;
        this._targets = new Set();
        this._scrollBuffer = options.scrollBuffer ?? 100;
        this._ticking = false;
        this._nativeOptions = { ...options };
        delete this._nativeOptions.scrollBuffer;

        this._useScroll = this._isZoomActive();
        if (this._useScroll) {
            this._initScrollMode();
        } else {
            this._observer = new IntersectionObserver(callback, this._nativeOptions);
        }

        this._onResize = () => {
            const shouldUseScroll = this._isZoomActive();
            if (shouldUseScroll !== this._useScroll) {
                this._switchMode(shouldUseScroll);
            }
        };
        window.addEventListener('resize', this._onResize);
    }

    _isZoomActive() {
        const container = document.getElementById('zoom-container');
        return container?.classList.contains('zoom-active') && window.innerWidth < 768;
    }

    _initScrollMode() {
        this._onScroll = this._checkIntersections.bind(this);
        window.addEventListener('scroll', this._onScroll, { passive: true });
    }

    _checkIntersections() {
        if (this._ticking) return;
        this._ticking = true;
        requestAnimationFrame(() => {
            this._ticking = false;
            for (const target of this._targets) {
                const rect = target.getBoundingClientRect();
                const isIntersecting = rect.top < window.innerHeight + this._scrollBuffer
                    && rect.bottom > -this._scrollBuffer;
                if (isIntersecting) {
                    this._callback([{ isIntersecting: true, target }], this);
                }
            }
        });
    }

    _switchMode(useScroll) {
        const savedTargets = [...this._targets];
        if (this._observer) this._observer.disconnect();
        if (this._onScroll) window.removeEventListener('scroll', this._onScroll);
        this._observer = null;
        this._onScroll = null;
        this._targets = new Set();
        this._useScroll = useScroll;
        if (useScroll) {
            this._initScrollMode();
        } else {
            this._observer = new IntersectionObserver(this._callback, this._nativeOptions);
        }
        savedTargets.forEach(t => this.observe(t));
    }

    observe(target) {
        this._targets.add(target);
        if (this._observer) this._observer.observe(target);
    }

    unobserve(target) {
        this._targets.delete(target);
        if (this._observer) this._observer.unobserve(target);
    }

    disconnect() {
        this._targets.clear();
        if (this._observer) this._observer.disconnect();
        if (this._onScroll) window.removeEventListener('scroll', this._onScroll);
        window.removeEventListener('resize', this._onResize);
        this._observer = null;
        this._onScroll = null;
    }
}

/**
 * Leaderboard Utilities
 * Shared helpers for leaderboard page interactions
 */
const LeaderboardUtils = {
    /**
     * Navigate to a specific leaderboard page via form submission
     * @param {HTMLFormElement} form - The page jump form
     * @param {string} paramName - The query parameter name for the page number
     */
    updatePage(form, paramName) {
        const input = form.querySelector('input[type="number"]');
        if (!input) return;
        const page = parseInt(input.value, 10);
        const maxPage = parseInt(input.max, 10);
        if (isNaN(page) || page < 1 || (!isNaN(maxPage) && maxPage > 0 && page > maxPage)) {
            const maxLabel = !isNaN(maxPage) && maxPage > 0 ? ` between 1 and ${maxPage}` : '';
            ToastManager.warning(`Please enter a valid page number${maxLabel}.`);
            return;
        }

        const url = new URL(window.location);
        url.searchParams.set(paramName, page);
        window.location.href = url.toString();
    }
};

/**
 * ReviewProgressTiers: Shared word-count tier data for review progress bars.
 * Used by both review-hub.js and rate-my-games.js to drive trophy icon
 * colors and progress bar styling as the reviewer writes.
 */
const ReviewProgressTiers = {
    iconTiers: [
        { words: 0,   cssColor: 'var(--color-base-content)', opacity: '0.25' },
        { words: 25,  cssColor: 'var(--color-trophy-bronze)', opacity: '1' },
        { words: 75,  cssColor: 'var(--color-trophy-silver)', opacity: '1' },
        { words: 100, cssColor: 'var(--color-trophy-gold)',   opacity: '1' },
    ],
    progressTiers: [
        { words: 0,   pct: 0,   color: 'bg-error',   nextWords: 10,  nextLabel: 'getting started' },
        { words: 10,  pct: 15,  color: 'bg-error',   nextWords: 25,  nextLabel: 'a solid start' },
        { words: 25,  pct: 35,  color: 'bg-warning',  nextWords: 50,  nextLabel: 'a good review' },
        { words: 50,  pct: 55,  color: 'bg-warning',  nextWords: 75,  nextLabel: 'a great review' },
        { words: 75,  pct: 75,  color: 'bg-info',     nextWords: 100, nextLabel: 'an excellent review' },
        { words: 100, pct: 90,  color: 'bg-success',  nextWords: 150, nextLabel: 'an outstanding review' },
        { words: 150, pct: 100, color: 'bg-success',  nextWords: null, nextLabel: null },
    ],

    /** Trophy cup SVG for dynamic HTML (mirrors partials/icons/trophy_cup.html). */
    trophyCupSvg: '<svg class="w-5 h-5 inline-block flex-shrink-0" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 512 512" fill="currentColor" aria-hidden="true"><path d="M102.49,0c0,27.414,0,104.166,0,137.062c0,112.391,99.33,156.25,153.51,156.25c54.18,0,153.51-43.859,153.51-156.25c0-32.896,0-109.648,0-137.062H102.49z M256.289,50.551l-68.164,29.768v98.474l-0.049,19.53c-0.526-0.112-47.274-10.112-47.274-78.391c0-28.17,0-69.6,0-69.6h60.385L256.289,50.551z"/><polygon points="315.473,400.717 291.681,367.482 279.791,318.506 256,322.004 232.209,318.506 220.314,367.482 205.347,388.394 196.527,400.476 196.699,400.476 196.527,400.717"/><polygon points="366.93,432.24 366.93,432 145.07,432 145.07,511.598 145.07,511.76 145.07,511.76 145.07,512 366.93,512 366.93,432.402 366.93,432.24"/><path d="M511.638,96.668c-0.033-1.268-0.068-2.336-0.068-3.174V45.1h-73.889v38.736h35.152v9.658c0,1.127,0.037,2.557,0.086,4.258c0.389,13.976,1.303,46.707-21.545,70.203c-5.121,5.266-11.221,9.787-18.219,13.613c-3.883,17.635-10.109,33.564-18.104,47.814c26.561-6.406,48.026-17.898,64.096-34.422C513.402,159.734,512.121,113.918,511.638,96.668z"/><path d="M60.625,167.955c-22.848-23.496-21.934-56.227-21.541-70.203c0.047-1.701,0.082-3.131,0.082-4.258v-9.658h34.842h0.07l0,0h0.24V45.1H0.43v48.394c0,0.838-0.032,1.906-0.068,3.174c-0.482,17.25-1.76,63.066,32.494,98.293c16.068,16.524,37.531,28.014,64.092,34.422c-7.996-14.25-14.22-30.182-18.103-47.816C71.846,177.74,65.746,173.221,60.625,167.955z"/></svg>',

    /**
     * Update a word-count progress bar, text label, and trophy icon.
     * Shared by review creation, wizard, and both edit forms.
     *
     * @param {Object} els - DOM elements: { bar, text, icon } (all optional)
     * @param {number} wordCount - Current word count
     */
    updateWordProgress(els, wordCount) {
        const { bar, text, icon } = els;

        // Find current tier
        let tier = this.progressTiers[0];
        for (let i = this.progressTiers.length - 1; i >= 0; i--) {
            if (wordCount >= this.progressTiers[i].words) {
                tier = this.progressTiers[i];
                break;
            }
        }

        // Smooth width interpolation between tiers
        if (bar) {
            let barWidth = tier.pct;
            const tierIdx = this.progressTiers.indexOf(tier);
            if (tierIdx < this.progressTiers.length - 1) {
                const next = this.progressTiers[tierIdx + 1];
                const progress = (wordCount - tier.words) / (next.words - tier.words);
                barWidth = tier.pct + (next.pct - tier.pct) * Math.min(progress, 1);
            }
            bar.style.width = `${barWidth}%`;
            bar.className = `h-full rounded-full transition-all duration-300 ease-out ${tier.color}`;
        }

        // Countdown text
        if (text) {
            if (wordCount === 0) {
                text.textContent = 'Write at least 10 words to get started...';
            } else if (tier.nextWords) {
                const remaining = tier.nextWords - wordCount;
                text.textContent = `${remaining} word${remaining === 1 ? '' : 's'} until ${tier.nextLabel}!`;
            } else {
                text.textContent = 'Outstanding review! The community thanks you.';
            }
        }

        // Trophy icon color
        if (icon) {
            let iconTier = this.iconTiers[0];
            for (let i = this.iconTiers.length - 1; i >= 0; i--) {
                if (wordCount >= this.iconTiers[i].words) {
                    iconTier = this.iconTiers[i];
                    break;
                }
            }
            icon.style.color = iconTier.cssColor;
            icon.style.opacity = iconTier.opacity;
        }
    },
};

/**
 * TrophyListRenderer: Builds condensed trophy list HTML for review hub sidebar
 * and Rate My Games wizard. Renders compact trophy cards with earned status,
 * type badges, and a summary header showing counts by type.
 */
const TrophyListRenderer = {
    /** Tailwind classes for each trophy type (already safeguarded in the app theme). */
    TROPHY_STYLES: {
        platinum: { cls: 'text-trophy-platinum', label: 'platinum' },
        gold:     { cls: 'text-trophy-gold',     label: 'gold' },
        silver:   { cls: 'text-trophy-silver',   label: 'silver' },
        bronze:   { cls: 'text-trophy-bronze',   label: 'bronze' },
    },

    /**
     * Build HTML for a condensed trophy list.
     * @param {Array} trophies - Array of trophy objects from API
     * @param {Object} [options]
     * @param {boolean} [options.showEarned=true] - Whether to show earned indicators
     * @returns {string} HTML string
     */
    buildList(trophies, options = {}) {
        const { showEarned = true } = options;
        const esc = HTMLUtils.escape;

        if (!trophies || trophies.length === 0) {
            return '<p class="text-sm text-base-content/50 italic py-2 pr-1">No trophy data available.</p>';
        }

        // Count by type for summary header
        const counts = { platinum: 0, gold: 0, silver: 0, bronze: 0 };
        trophies.forEach(t => {
            if (counts[t.trophy_type] !== undefined) counts[t.trophy_type]++;
        });

        let html = '<div class="flex flex-wrap items-center gap-2 mb-2 text-xs">';
        for (const [type, count] of Object.entries(counts)) {
            if (count > 0) {
                const style = this.TROPHY_STYLES[type];
                html += `<span class="badge badge-xs font-bold ${style.cls}">${count} ${style.label}</span>`;
            }
        }
        html += `<span class="text-base-content/40 ml-auto">${trophies.length} total</span></div>`;

        html += '<div class="space-y-1 max-h-64 lg:max-h-96 overflow-y-auto pr-1">';
        for (const t of trophies) {
            const style = this.TROPHY_STYLES[t.trophy_type] || {};
            const isEarned = showEarned && t.earned;
            const earnedClasses = isEarned ? 'bg-success/10 border-l-2 border-success' : '';
            const earnedIcon = isEarned
                ? '<svg xmlns="http://www.w3.org/2000/svg" class="w-3.5 h-3.5 text-success shrink-0" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2"><path stroke-linecap="round" stroke-linejoin="round" d="M5 13l4 4L19 7"/></svg>'
                : '';

            html += `<div class="flex items-center gap-2 p-1.5 rounded ${earnedClasses} hover:bg-base-200/50 transition-colors">`;
            html += `<img src="${esc(t.trophy_icon_url || '')}" alt="" class="w-8 h-8 object-cover rounded shrink-0" loading="lazy" />`;
            html += '<div class="flex-1 min-w-0">';
            html += `<p class="text-xs font-semibold line-clamp-1 pr-1">${esc(t.trophy_name)}</p>`;
            html += `<p class="text-xs text-base-content/50 line-clamp-1 italic pr-1">${esc(t.trophy_detail || '')}</p>`;
            html += '</div>';
            html += `<span class="badge badge-xs font-bold shrink-0 ${style.cls || ''}">${esc(t.trophy_type)}</span>`;
            html += earnedIcon;
            html += '</div>';
        }
        html += '</div>';

        return html;
    },
};

/* ---------------------------------------------------------------------------
 * SpoilerToggle
 *
 * Click/keyboard handler for ||spoiler|| spans rendered server-side by
 * checklist_service.process_markdown(enable_spoilers=True). One delegated
 * listener pair on document.body covers every spoiler on every page (roadmap
 * detail, game detail's inline roadmap section, editor preview), and stays
 * idempotent so re-init after dynamic re-renders is a no-op.
 *
 * Reveal state is in-memory only: no localStorage, no per-trophy persistence.
 * Refreshing the page re-hides everything, matching PSNProfiles/Discord. The
 * `aria-pressed` attribute mirrors the visible state for screen readers.
 * --------------------------------------------------------------------------- */
const SpoilerToggle = (() => {
    let initialized = false;

    function toggle(el) {
        const revealed = el.classList.toggle('revealed');
        el.setAttribute('aria-pressed', revealed ? 'true' : 'false');
        el.setAttribute('title', revealed ? 'Click to hide' : 'Click to reveal');
    }

    return {
        init() {
            if (initialized) return;
            initialized = true;
            document.body.addEventListener('click', (e) => {
                const sp = e.target.closest('.spoiler');
                if (sp) toggle(sp);
            });
            document.body.addEventListener('keydown', (e) => {
                if (e.key !== 'Enter' && e.key !== ' ') return;
                const sp = e.target.closest?.('.spoiler');
                if (!sp) return;
                e.preventDefault();
                toggle(sp);
            });
        },
    };
})();

if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', () => SpoilerToggle.init());
} else {
    SpoilerToggle.init();
}

/* ---------------------------------------------------------------------------
 * Lightbox
 *
 * Click-to-zoom for images inside roadmap content (.prose-roadmap inline
 * markdown images and .roadmap-gallery step/guide attachments). Single
 * delegated handler on document.body — overlay is built lazily on first use,
 * then reused. Esc or click on the overlay background closes.
 *
 * Skipped intentionally:
 *   - Controller-icon shortcodes (.ps-icon) — they're inline glyphs, not
 *     content images.
 *   - Anything inside .spoiler — clicking those should toggle the spoiler
 *     reveal/re-hide instead of opening a zoomed view. If we lightboxed
 *     them, both handlers would fire and the UX would fight itself.
 * --------------------------------------------------------------------------- */
const Lightbox = (() => {
    let overlay = null;
    let initialized = false;

    function buildOverlay() {
        overlay = document.createElement('div');
        overlay.className = 'lightbox-overlay hidden';
        overlay.setAttribute('role', 'dialog');
        overlay.setAttribute('aria-modal', 'true');
        overlay.setAttribute('aria-label', 'Image lightbox');
        overlay.innerHTML = `
            <button type="button" class="lightbox-close" aria-label="Close lightbox">
                <svg xmlns="http://www.w3.org/2000/svg" class="w-6 h-6" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2">
                    <path stroke-linecap="round" stroke-linejoin="round" d="M6 18L18 6M6 6l12 12"/>
                </svg>
            </button>
            <img class="lightbox-image" alt="">
        `;
        overlay.addEventListener('click', (e) => {
            // Close on overlay background or close button. Don't close if
            // the click landed on the image itself — readers expect the
            // image to be safe to interact with (zoom, drag, save).
            if (e.target === overlay || e.target.closest('.lightbox-close')) {
                close();
            }
        });
        document.body.appendChild(overlay);
    }

    function open(src, alt) {
        if (!overlay) buildOverlay();
        const img = overlay.querySelector('.lightbox-image');
        img.src = src;
        img.alt = alt || '';
        overlay.classList.remove('hidden');
        // Lock body scroll while open so the page doesn't drift behind the
        // overlay on touch devices.
        document.body.style.overflow = 'hidden';
        // Move focus to the close button for keyboard users.
        overlay.querySelector('.lightbox-close')?.focus();
    }

    function close() {
        if (!overlay || overlay.classList.contains('hidden')) return;
        overlay.classList.add('hidden');
        document.body.style.overflow = '';
        const img = overlay.querySelector('.lightbox-image');
        if (img) img.src = '';
    }

    return {
        init() {
            if (initialized) return;
            initialized = true;
            document.body.addEventListener('click', (e) => {
                const img = e.target.closest('img');
                if (!img) return;
                // Only inline content images inside roadmap surfaces.
                if (!img.closest('.prose-roadmap, .roadmap-gallery')) return;
                // Skip inline glyphs and spoilered images (see module comment).
                if (img.classList.contains('ps-icon')) return;
                if (img.closest('.spoiler')) return;
                e.preventDefault();
                open(img.src, img.alt);
            });
            document.addEventListener('keydown', (e) => {
                if (e.key === 'Escape') close();
            });
        },
        open,
        close,
    };
})();

if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', () => Lightbox.init());
} else {
    Lightbox.init();
}

/**
 * StickyReveal - reveal/pin a condensed proxy element when its sentinel scrolls under the sticky chrome.
 *
 * Markup: a target [data-sticky-reveal] (fixed-positioned, hidden until pinned via the .is-pinned class)
 * plus a sentinel [data-sticky-sentinel="#selector"] placed where pinning should begin (e.g. at the bottom
 * of the full page header). When the sentinel scrolls above the chrome bottom (the --sticky-top offset the
 * navbar/sub-nav publish), the target gets .is-pinned; scrolling back up removes it.
 *
 * init() is idempotent + re-runnable: it skips already-wired targets and drops observers whose target has
 * left the DOM (e.g. replaced by an HTMX swap), so callers can re-init after a partial-page swap.
 */
const StickyReveal = {
    _entries: [],
    _bound: false,
    _chromeH() {
        return parseInt(getComputedStyle(document.documentElement).getPropertyValue('--sticky-top'), 10) || 0;
    },
    // (Re)create an entry's observer with the CURRENT chrome height and adopt the correct state now. Called
    // on init AND on refresh (resize / font swap), because main.js rewrites --sticky-top after our init --
    // a baked-in height would pin the bar a few px early/late once the navbar height changes.
    _observe(entry) {
        if (entry.obs) entry.obs.disconnect();
        const chromeH = this._chromeH();
        const target = entry.target, sentinel = entry.sentinel;
        entry.obs = new IntersectionObserver((es) => {
            target.classList.toggle('is-pinned', es[0].boundingClientRect.top < chromeH);
        }, { rootMargin: `-${chromeH}px 0px 0px 0px`, threshold: [0, 1] });
        entry.obs.observe(sentinel);
        // Adopt the correct state immediately, WITHOUT animating, so a target inserted while already scrolled
        // past (e.g. re-rendered by an HTMX swap) appears in place instead of replaying the reveal slide. Only
        // touch the class when it actually changes, so a resize/refresh doesn't flash an already-correct bar.
        const pin = sentinel.getBoundingClientRect().top < chromeH;
        if (pin !== target.classList.contains('is-pinned')) {
            const prev = target.style.transition;
            target.style.transition = 'none';
            target.classList.toggle('is-pinned', pin);
            void target.offsetWidth;            // flush the un-animated state
            target.style.transition = prev;
        }
    },
    init(root) {
        root = root || document;
        // Drop entries whose target left the DOM (e.g. an HTMX swap replaced it).
        this._entries = this._entries.filter((e) => {
            if (!document.contains(e.target)) { if (e.obs) e.obs.disconnect(); return false; }
            return true;
        });
        root.querySelectorAll('[data-sticky-reveal]').forEach((target) => {
            if (target._stickyReveal) return;   // already wired
            const sel = target.getAttribute('data-sticky-sentinel');
            const sentinel = sel ? document.querySelector(sel) : target.previousElementSibling;
            if (!sentinel) return;
            target._stickyReveal = true;
            const entry = { target, sentinel, obs: null };
            this._entries.push(entry);
            this._observe(entry);
        });
        // Re-measure when the chrome height can change (main.js updates --sticky-top on resize + fonts.ready).
        if (!this._bound) {
            this._bound = true;
            let raf = null;
            const refresh = () => {
                if (raf) return;
                raf = requestAnimationFrame(() => {
                    raf = null;
                    this._entries.forEach((e) => { if (document.contains(e.target)) this._observe(e); });
                });
            };
            window.addEventListener('resize', refresh);
            if (document.fonts && document.fonts.ready) { document.fonts.ready.then(refresh); }
        }
    }
};

/**
 * Directional view switch (Material "shared axis"): slide the incoming panel in from the side it lives
 * on -- forward in the tab order enters from the right, backward from the left. Applies the shared
 * .pp-view-in-* class (components/motion.css) to `panel`, picking the direction from `order`.
 *
 * @param {HTMLElement} panel   the element now being shown (a toggled panel, or an HTMX-swapped root)
 * @param {string} fromName     the view we're leaving (falsy on first paint -> treated as forward)
 * @param {string} toName       the view we're entering
 * @param {string[]} order      the view names in tab order, e.g. ['jobs','radar','contracts']
 */
function slideViewIn(panel, fromName, toName, order) {
    if (!panel || fromName === toName) { return; }
    // Reduced motion is also gated in CSS; short-circuit here to skip the forced reflow below.
    if (window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches) { return; }
    var forward = !fromName || order.indexOf(toName) >= order.indexOf(fromName);
    panel.classList.remove('pp-view-in-right', 'pp-view-in-left');
    void panel.offsetWidth;   // restart the animation from scratch on a re-toggle
    panel.classList.add(forward ? 'pp-view-in-right' : 'pp-view-in-left');
}

/**
 * One-shot "ignite" glow bloom on the chip that just became active (the shared .pp-tab-ignite in
 * components/motion.css). Restart-safe (removes + reflows before re-adding); reduced-motion = no-op.
 * @param {HTMLElement} tab
 */
function igniteTab(tab) {
    if (!tab) { return; }
    if (window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches) { return; }
    tab.classList.remove('pp-tab-ignite');
    void tab.offsetWidth;   // restart the animation on a re-activation
    tab.classList.add('pp-tab-ignite');
}

/**
 * Wire a WAI-ARIA tablist: roving tabindex (only the active tab is Tab-reachable) + Arrow/Home/End
 * keyboard nav. Markup/class-agnostic -- pass the tab elements and a select callback; the page owns what
 * "select" does (show a panel, HTMX swap, etc). Two activation models:
 *   - automatic (default): clicking OR arrowing to a tab activates it -- for cheap client-side switches.
 *   - manual (`opts.manual`): arrows move focus only; the tab's own click/Enter activates -- for
 *     expensive swaps (HTMX links) where auto-activating on every arrow keypress would fire a request.
 *
 * @param {NodeList|Array} tabs  the tab elements, in visual order
 * @param {Object} opts
 * @param {Function} [opts.onSelect]  (tabEl) -> void, called on activation (auto model only)
 * @param {Function} [opts.isActive]  (tabEl) -> bool; default checks .is-active
 * @param {boolean} [opts.manual]     manual activation (arrows move focus only)
 * @param {boolean} [opts.ignite]     bloom .pp-tab-ignite on the activated tab (auto model)
 * @returns {{ syncTabindex: Function }}  call syncTabindex() after the active tab changes elsewhere
 */
function wireTablist(tabs, opts) {
    tabs = Array.prototype.slice.call(tabs || []);
    var noop = function () {};
    if (!tabs.length) { return { syncTabindex: noop }; }
    opts = opts || {};
    var onSelect = opts.onSelect || noop;
    var isActive = opts.isActive || function (t) { return t.classList.contains('is-active'); };
    function syncTabindex() { tabs.forEach(function (t) { t.tabIndex = isActive(t) ? 0 : -1; }); }
    function select(tab) { if (opts.ignite) { igniteTab(tab); } onSelect(tab); }
    var STEP = { ArrowRight: 1, ArrowDown: 1, ArrowLeft: -1, ArrowUp: -1 };
    tabs.forEach(function (tab) {
        if (!opts.manual) { tab.addEventListener('click', function () { select(tab); }); }
        tab.addEventListener('keydown', function (e) {
            var i = tabs.indexOf(tab), next;
            if (Object.prototype.hasOwnProperty.call(STEP, e.key)) { next = tabs[(i + STEP[e.key] + tabs.length) % tabs.length]; }
            else if (e.key === 'Home') { next = tabs[0]; }
            else if (e.key === 'End') { next = tabs[tabs.length - 1]; }
            else { return; }
            e.preventDefault();
            next.focus();
            if (!opts.manual) { select(next); }   // automatic activation
        });
    });
    syncTabindex();
    return { syncTabindex: syncTabindex };
}

/**
 * Reflect the active view in the URL (`?view=`), keeping the default view's URL clean, and strip a set of
 * view-scoped params when you leave the view that owns them (so a shared link stays clean). Shareable +
 * reload-safe; no-op without History. Shared by the Career tabs and Collection view toggle.
 * @param {string} view       the now-active view name
 * @param {Object} opts
 * @param {string} opts.default        the default view -- its URL drops `?view=`
 * @param {string} [opts.paramView]    the view that owns `opts.params`
 * @param {string[]} [opts.params]     params stripped unless `view === opts.paramView`
 */
function syncViewParam(view, opts) {
    if (!window.history || !history.replaceState) { return; }
    opts = opts || {};
    var qp = new URLSearchParams(location.search);
    if (view === opts.default) { qp.delete('view'); } else { qp.set('view', view); }
    if (opts.params && view !== opts.paramView) { opts.params.forEach(function (k) { qp.delete(k); }); }
    var qs = qp.toString();
    history.replaceState(null, '', location.pathname + (qs ? '?' + qs : '') + location.hash);
}

/**
 * Staggered grid reveal for HTMX-swapped / infinite-scroll card grids (the Badges browse pattern; the
 * standard for any rebuilt browse grid). Hides the grid's cards, reveals those already present in ONE
 * DOM-order batch, and returns an observer that reveals infinite-scroll-APPENDED cards as they scroll in.
 * The page supplies the per-card animation via `reveal(el, delayMs)` (use WAAPI `el.animate` so arrivals
 * restart reliably on freshly HTMX-swapped nodes); the engine owns the reduced-motion gate, the batch
 * stagger, and the observer. Reveals each card ONCE (marks `.is-revealed`).
 *
 * NOTE: this is for grids that swap/append (WAAPI + observer). A BOUNDED, all-client grid that just wants
 * a replay-on-show stagger is simpler as a CSS container class (see the Collection gallery's
 * `.is-revealing` nth-child); and content-specific per-card reveals (Career's contract rows) stay bespoke.
 * Don't force those onto this engine -- different tools for different contexts.
 *
 * @param {Object} o
 * @param {HTMLElement} o.grid          the grid container
 * @param {string} o.cardSelector       selects the cards within the grid
 * @param {function(HTMLElement, number)} o.reveal   plays one card's arrival, given (el, delayMs)
 * @param {number} [o.step=24]          per-card stagger step (ms)
 * @param {number} [o.batchCap=560]     max delay for the initial in-grid batch
 * @param {number} [o.appendCap=200]    max delay within a scroll-appended batch
 * @param {string} [o.hideClass='pp-reveal']   class added to the grid to hide un-revealed cards
 * @returns {{ observe: function, disconnect: function } | null}  null if motion is off / no cards / no IO
 */
function staggerReveal(o) {
    if (!o || !o.grid || typeof o.reveal !== 'function' || !o.cardSelector) { return null; }
    if (!window.IntersectionObserver) { return null; }
    var rm = (PlatPursuit.Medallion && PlatPursuit.Medallion.prefersReducedMotion && PlatPursuit.Medallion.prefersReducedMotion())
        || (window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches);
    if (rm) { return null; }
    var grid = o.grid, sel = o.cardSelector;
    if (!grid.querySelector(sel)) { return null; }
    var step = o.step || 24;
    var batchCap = o.batchCap != null ? o.batchCap : 560;
    var appendCap = o.appendCap != null ? o.appendCap : 200;
    grid.classList.add(o.hideClass || 'pp-reveal');   // hides the cards until each is revealed
    function play(el, delay) { el.classList.add('is-revealed'); o.reveal(el, delay); }
    // Reveal cards already present in ONE synchronous DOM-order batch. DOM order == visual reading order for
    // a row-major grid, independent of the (possibly transitional) column count during a view swap.
    grid.querySelectorAll(sel + ':not(.is-revealed)').forEach(function (el, i) { play(el, Math.min(i * step, batchCap)); });
    // The observer ONLY scroll-reveals infinite-scroll-appended cards (call observe() on newly-added nodes).
    var io = new IntersectionObserver(function (entries) {
        var shown = entries.filter(function (e) { return e.isIntersecting; }).map(function (e) { return e.target; });
        shown.sort(function (a, b) { return (a.compareDocumentPosition(b) & Node.DOCUMENT_POSITION_FOLLOWING) ? -1 : 1; });
        shown.forEach(function (el, j) { play(el, Math.min(j * step, appendCap)); io.unobserve(el); });
    }, { rootMargin: '0px 0px -8% 0px', threshold: 0.08 });
    return {
        observe: function (nodes) { Array.prototype.forEach.call(nodes, function (nd) { if (nd.matches && nd.matches(sel)) { io.observe(nd); } }); },
        disconnect: function () { io.disconnect(); }
    };
}

/**
 * iOS-sheet "swipe down to close" for a modal/sheet on touch: flick the dialog downward to dismiss it.
 * The PAGE owns closing -- pass `onClose` (the same thing the close button runs); the helper only handles
 * the drag, the follow transform, the scrim fade, and the snap-back. Drag only starts from the top of the
 * scroll (the dialog OR any inner scroll container, e.g. the peek's capped info column), so scrolling isn't
 * hijacked. The helper adds `.pp-dismissable` to the
 * dialog, which surfaces the shared touch-only grabber handle (`.pp-dismissable::before`, "pull to close").
 *
 * @param {HTMLElement} dialog     the scrollable dialog/sheet element
 * @param {Object} opts
 * @param {Function} opts.onClose  called when the drag passes the threshold (do the real close here)
 * @param {HTMLElement} [opts.scrim]     backdrop element to fade while dragging
 * @param {number} [opts.threshold=90]   px of downward drag past which it dismisses
 */
function dismissableSheet(dialog, opts) {
    if (!dialog) { return; }
    opts = opts || {};
    var onClose = opts.onClose || function () {};
    var scrim = opts.scrim || null;
    var threshold = opts.threshold || 90;
    var startY = null, dragging = false;
    dialog.classList.add('pp-dismissable');   // surfaces the touch-only grabber handle (.pp-dismissable::before)
    function resetStyles() {
        dialog.style.transition = ''; dialog.style.transform = ''; dialog.style.opacity = ''; dialog.style.animation = '';
        if (scrim) { scrim.style.transition = ''; scrim.style.opacity = ''; }
    }
    dialog.addEventListener('touchstart', function (e) {
        // Only a drag from the very TOP of the scroll dismisses. Walk from the touched element up to the
        // dialog: if anything along the way is scrolled (the dialog itself, or an INNER scroll container like
        // the peek's capped info column), let it scroll instead of hijacking the gesture.
        for (var el = e.target; el; el = el.parentNode) {
            if (el.scrollTop > 0) { startY = null; return; }
            if (el === dialog) { break; }
        }
        startY = e.touches[0].clientY; dragging = false;
    }, { passive: true });
    dialog.addEventListener('touchmove', function (e) {
        if (startY === null) { return; }
        var dy = e.touches[0].clientY - startY;
        if (dy > 0) {   // downward only -- follow the finger
            dragging = true;
            e.preventDefault();
            dialog.style.animation = 'none'; dialog.style.transition = 'none';
            dialog.style.transform = 'translateY(' + dy + 'px)';
            if (scrim) { scrim.style.opacity = String(Math.max(0.15, 1 - dy / 450)); }
        }
    }, { passive: false });
    dialog.addEventListener('touchend', function () {
        if (startY === null) { return; }
        var m = /translateY\(([0-9.]+)px\)/.exec(dialog.style.transform);
        var dy = m ? parseFloat(m[1]) : 0;
        startY = null;
        if (dragging && dy > threshold) {   // past the threshold -> slide off + close
            dialog.style.transition = 'transform 0.2s ease, opacity 0.2s ease';
            dialog.style.transform = 'translateY(100vh)'; dialog.style.opacity = '0';
            if (scrim) { scrim.style.transition = 'opacity 0.2s ease'; scrim.style.opacity = '0'; }
            setTimeout(function () { resetStyles(); onClose(); }, 200);   // reset first so the next open is clean
        } else if (dragging) {   // snap back
            dialog.style.transition = 'transform 0.25s ease';
            dialog.style.transform = 'none';
            if (scrim) { scrim.style.transition = 'opacity 0.25s ease'; scrim.style.opacity = ''; }
        }
    });
}

// Export for use in other modules
window.PlatPursuit = window.PlatPursuit || {};
window.PlatPursuit.ToastManager = ToastManager;
window.PlatPursuit.CSRFToken = CSRFToken;
window.PlatPursuit.TimeFormatter = TimeFormatter;
window.PlatPursuit.API = API;
window.PlatPursuit.UnsavedChangesManager = UnsavedChangesManager;
window.PlatPursuit.HTMLUtils = HTMLUtils;
window.PlatPursuit.debounce = debounce;
window.PlatPursuit.countUp = countUp;
window.PlatPursuit.animatePanel = animatePanel;
window.PlatPursuit.InfiniteScroller = InfiniteScroller;
window.PlatPursuit.DragReorderManager = DragReorderManager;
window.PlatPursuit.ZoomAwareObserver = ZoomAwareObserver;
window.PlatPursuit.LeaderboardUtils = LeaderboardUtils;
window.PlatPursuit.ReviewProgressTiers = ReviewProgressTiers;
window.PlatPursuit.TrophyListRenderer = TrophyListRenderer;
window.PlatPursuit.SpoilerToggle = SpoilerToggle;
window.PlatPursuit.Lightbox = Lightbox;
window.PlatPursuit.StickyReveal = StickyReveal;
window.PlatPursuit.slideViewIn = slideViewIn;
window.PlatPursuit.igniteTab = igniteTab;
window.PlatPursuit.wireTablist = wireTablist;
window.PlatPursuit.syncViewParam = syncViewParam;
window.PlatPursuit.staggerReveal = staggerReveal;
window.PlatPursuit.dismissableSheet = dismissableSheet;

/**
 * discPopovers -- the OPEN/CLOSE mechanics for a `.rp-disc` discipline-dropdown group (the shared look
 * from elements.css, used by the Career contracts board + Browse Games). Owns ONLY the popover behavior:
 * a `.rp-disc__trigger` click toggles its sibling `.rp-pop` (one open at a time), viewport-edge flip
 * (`.rp-pop--left`), `aria-expanded`, and click-outside / Escape to close. SELECTION is the caller's --
 * wire your own handlers on the `.rp-pop__item`s. Delegates one click listener on `root`.
 * @param {HTMLElement} root  the `.rp-discs` container
 * @returns {{closeAll: function}}  call closeAll() after your own actions (e.g. a "clear" button)
 */
function discPopovers(root) {
    if (!root) { return { closeAll: function () {} }; }
    function closeAll() {
        root.querySelectorAll('.rp-pop').forEach(function (p) { p.hidden = true; });
        root.querySelectorAll('.rp-disc__trigger').forEach(function (t) { t.setAttribute('aria-expanded', 'false'); });
    }
    root.addEventListener('click', function (e) {
        var trig = e.target.closest && e.target.closest('.rp-disc__trigger');
        if (!trig || !root.contains(trig)) { return; }
        var pop = trig.parentElement.querySelector('.rp-pop');
        if (!pop) { return; }
        var isOpen = !pop.hidden;
        closeAll();
        if (!isOpen) {
            pop.classList.remove('rp-pop--left');
            pop.hidden = false;
            trig.setAttribute('aria-expanded', 'true');
            // Flip to the chip's right edge if a left-anchored popover would overflow the viewport (mobile).
            if (pop.getBoundingClientRect().right > document.documentElement.clientWidth - 8) { pop.classList.add('rp-pop--left'); }
        }
    });
    document.addEventListener('click', function (e) { if (!e.target.closest || !e.target.closest('.rp-disc')) { closeAll(); } });
    document.addEventListener('keydown', function (e) { if (e.key === 'Escape') { closeAll(); } });
    return { closeAll: closeAll };
}
window.PlatPursuit.discPopovers = discPopovers;

/**
 * wireSearchField -- shared search-field affordances for ANY search input (the browse-filters.js controller
 * AND bespoke per-page controllers): a `.has-value` class toggle (drives the clear button + `/` hint), a
 * [data-search-clear] clear button, and Escape-to-clear. Returns { setBusy } so the caller toggles
 * `.is-searching` (the in-flight spinner) around its request. The wrapper is `input.closest('[data-search-wrap]')`
 * (or the input's parent). Visuals come from the shared CSS keyed on [data-search-wrap] + .has-value/.is-searching.
 * @param {HTMLInputElement} input
 * @param {{onClear?: function}} [opts]  onClear runs after the field is emptied (clear button / Escape)
 * @returns {{wrap: HTMLElement, setBusy: function(boolean), sync: function}}
 */
function wireSearchField(input, opts) {
    opts = opts || {};
    var wrap = (input.closest && input.closest('[data-search-wrap]')) || input.parentElement;
    function sync() { if (wrap) { wrap.classList.toggle('has-value', !!input.value); } }
    function clear() {
        input.value = ''; sync(); input.focus();
        if (opts.onClear) { opts.onClear(); }
    }
    sync();
    input.addEventListener('input', sync);
    input.addEventListener('keydown', function (e) {
        if (e.key === 'Escape' && input.value) { e.preventDefault(); clear(); }
    });
    var clearBtn = wrap && wrap.querySelector('[data-search-clear]');
    if (clearBtn) { clearBtn.addEventListener('click', function (e) { e.preventDefault(); clear(); }); }
    return {
        wrap: wrap,
        setBusy: function (on) { if (wrap) { wrap.classList.toggle('is-searching', !!on); } },
        sync: sync,
    };
}
window.PlatPursuit.wireSearchField = wireSearchField;

// Global `/` + Cmd/Ctrl+K -> focus the page's primary search field ([data-page-search]). Bound ONCE here so
// every page (browse or bespoke) gets the shortcut just by marking its search input. `/` is skipped while
// typing in another field; Cmd/Ctrl+K always fires (a deliberate override, like GitHub/Linear).
document.addEventListener('keydown', function (e) {
    var cmdK = (e.metaKey || e.ctrlKey) && (e.key === 'k' || e.key === 'K');
    var slash = e.key === '/' && !e.metaKey && !e.ctrlKey && !e.altKey;
    if (!cmdK && !slash) { return; }
    var t = e.target;
    if (slash && t && (t.tagName === 'INPUT' || t.tagName === 'TEXTAREA' || t.tagName === 'SELECT' || t.isContentEditable)) { return; }
    // Pick the first VISIBLE data-page-search (a page may have hidden ones, e.g. an inactive tab/view);
    // fall back to a [data-browse-form] search input (the ~18 browse pages don't all carry data-page-search).
    var input = null;
    var candidates = document.querySelectorAll('[data-page-search], [data-browse-form] input[type="text"], [data-browse-form] input[type="search"]');
    for (var i = 0; i < candidates.length; i++) {
        if (candidates[i].offsetParent !== null) { input = candidates[i]; break; }
    }
    if (!input) { return; }
    e.preventDefault(); input.focus(); if (input.select) { input.select(); }
});
