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
        // A modal toast host is a top-layer popover: show it (above the modal, anchored to the viewport) so
        // toasts rest at the screen's bottom-end instead of being trapped in the dialog's containing block.
        if (container.matches?.('[popover]') && !container.matches(':popover-open')) {
            try { container.showPopover(); } catch (e) { /* not connected / unsupported */ }
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
            setTimeout(() => {
                toast.remove();
                // Tidy the top-layer popover once its last toast clears, so it stops shadowing the modal.
                if (container.matches?.('[popover]') && container.matches(':popover-open') && !container.children.length) {
                    try { container.hidePopover(); } catch (e) { /* already hidden */ }
                }
            }, 300);
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
     * Escape for a TEXT context (between tags). NOT safe for attribute values -- see escapeAttr.
     *
     * This round-trips through textContent/innerHTML, which runs the HTML fragment-serialization
     * algorithm. That escapes & < > and U+00A0 and DELIBERATELY LEAVES QUOTES ALONE, because a text
     * node has no need of them. Correct here, wrong one character later inside quotes.
     *
     * @param {string} text - Raw text to escape
     * @returns {string} Safe to interpolate between tags
     */
    escape(text) {
        const div = document.createElement('div');
        div.textContent = text;
        return div.innerHTML;
    },

    /**
     * Escape for an ATTRIBUTE value (inside quotes in an HTML string you are about to inject).
     *
     * Use this whenever the interpolation sits inside quotes -- `title="${...}"`, `data-x="${...}"`,
     * `src="${...}"`. `escape()` does not encode " or ', so a value containing a quote closes the
     * attribute early and the rest is parsed as markup: one `onerror=` later and it is stored XSS.
     *
     * Prefer building nodes and assigning `.textContent` / `.setAttribute()` over string-concatenating
     * HTML at all; this exists for the places that already do the latter.
     *
     * NOTE for URL attributes (href/src): this makes the value safe to SIT in the attribute, but does
     * not make the URL itself safe. A `javascript:` href still executes on click. Validate the scheme
     * separately when the URL is user-controlled.
     *
     * @param {string} text - Raw value to escape
     * @returns {string} Safe to interpolate inside a quoted attribute
     */
    escapeAttr(text) {
        return String(text ?? '')
            .replace(/&/g, '&amp;')
            .replace(/</g, '&lt;')
            .replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;')
            .replace(/'/g, '&#39;');
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
 * Animate a collapsible panel open/closed (height + opacity), toggling its `hidden` attribute. The
 * animation primitive under `filterPanel` below; call it directly only for a panel that is not a browse
 * filter drawer (the career / collection ones). The panel MUST have `overflow: hidden` and a
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
        panel.style.overflow = open ? 'visible' : '';   // open -> let inner popovers/dropdowns escape the panel
        if (open) { panel.removeAttribute('hidden'); } else { panel.setAttribute('hidden', ''); }
        return;
    }
    panel.style.overflow = '';   // clip during the height tween (reverts to the CSS overflow:hidden)
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
            panel.style.overflow = 'visible';                    // fully open -> popovers may overflow the panel
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
 * filterPanel -- the browse toolbar's advanced-filter drawer, in one place.
 *
 * Every browse surface had grown its own copy of the same ~80 lines: toggle the panel, keep the toggle's
 * aria/is-open in step, count the active filters onto a badge, pop that badge when it changes, dim the
 * results while the swap is in flight, open the drawer on load if something is already filtering, and
 * keep the edge-fades on the scrolling chip lists honest. Six copies (game list, badge list, company
 * list, recently added, tag detail, and the profile's Games tab) is five too many -- and they had already
 * drifted, each with a different SKIP set and only some of them wiring the fades at all.
 *
 * `animatePanel` above stays the animation primitive; this owns everything around it.
 *
 *   PlatPursuit.filterPanel({
 *       form, toggle, panel,      // required -- element or selector
 *       countEl,                  // optional badge for the active-filter count
 *       skip: {sort: 1, ...},     // params that do NOT count as a filter (display/sort/search state)
 *       count,                    // optional () => number, replacing the FormData counter entirely
 *       chipsHost,                // optional selector: gets `is-filters-open` while the drawer is open
 *       dimTarget,                // defaults to '#browse-results'; gets `is-swapping` on a filter change
 *       openOnLoad,               // defaults to "a filter is already active"
 *   })  ->  { refresh, setOpen, destroy }
 *
 * Returns a handle so a page that re-inits after an HTMX swap can tear the old one down first.
 */
function filterPanel(opts) {
    var o = opts || {};
    function pick(v) { return typeof v === 'string' ? document.querySelector(v) : v; }

    var form = pick(o.form);
    var toggle = pick(o.toggle);
    var panel = pick(o.panel);
    if (!form || !toggle || !panel) { return null; }

    var countEl = pick(o.countEl) || null;
    var skip = o.skip || {};
    var chipsHost = o.chipsHost ? (panel.closest(o.chipsHost) || document.querySelector(o.chipsHost)) : null;
    var dimSel = o.dimTarget === undefined ? '#browse-results' : o.dimTarget;
    var prevN = null;
    var listeners = [];
    // The pending chips-reveal. Held so it can be CANCELLED: the old inline copy hung this on a
    // `transitionend` that every setPanel() call cleared, so re-opening within the tween cancelled it.
    // An uncancelled timer fires while the drawer is open again and strips the class from under it.
    var chipsTimer = null;

    function on(target, type, fn, listenOpts) {
        target.addEventListener(type, fn, listenOpts);
        listeners.push([target, type, fn]);
    }

    // Counts SET values, minus the ones that are display state rather than filtering (sort, view, the
    // search box, pagination) and minus any scope selector the page pre-fills -- Browse Games always
    // carries a platform default from its saved-defaults redirect, and counting that would show a
    // permanent "2" and auto-open the drawer on every landing. A range parked at its own min/max is not
    // a filter either.
    function defaultCount() {
        var n = 0;
        new FormData(form).forEach(function (value, key) {
            if (skip[key] || !value) { return; }
            if (key === 'letter' && value === '') { return; }
            var input = form.querySelector('[name="' + key + '"]');
            if (input && input.type === 'range') {
                if (input.dataset.dualRangeLo !== undefined && input.value === input.min) { return; }
                if (input.dataset.dualRangeHi !== undefined && input.value === input.max) { return; }
            }
            n += 1;
        });
        return n;
    }
    var activeCount = typeof o.count === 'function' ? o.count : defaultCount;

    function refresh() {
        if (!countEl) { return; }
        var n = activeCount();
        countEl.textContent = n;
        countEl.hidden = (n === 0);
        // Pop on CHANGE, never on the first paint -- otherwise every landing with a saved filter opens
        // with an animation acknowledging something the visitor did not just do.
        if (n > 0 && prevN !== null && n !== prevN) {
            countEl.classList.remove('is-pop'); void countEl.offsetWidth; countEl.classList.add('is-pop');
        }
        prevN = n;
    }

    // Scroll-aware edge fades on the long chip lists. A fade only appears on an edge with content hidden
    // past it, so a list that fits is never clipped. Measured lazily: these containers are 0-size while
    // the panel is collapsed, which is why the open path re-runs it.
    function setFade(node, axis) {
        if (axis === 'y') {
            node.style.setProperty('--fade-t', node.scrollTop > 1 ? '16px' : '0px');
            node.style.setProperty('--fade-b', (node.scrollTop + node.clientHeight < node.scrollHeight - 1) ? '16px' : '0px');
        } else {
            node.style.setProperty('--fade-l', node.scrollLeft > 1 ? '20px' : '0px');
            node.style.setProperty('--fade-r', (node.scrollLeft + node.clientWidth < node.scrollWidth - 1) ? '20px' : '0px');
        }
    }
    function fadeGroups() {
        return [
            [panel.querySelectorAll('.pp-gbrowse__fchips--scroll'), 'y'],
            [panel.querySelectorAll('.pp-gbrowse__fchips--letters'), 'x'],
        ];
    }
    function refreshFades() {
        fadeGroups().forEach(function (pair) {
            pair[0].forEach(function (node) { setFade(node, pair[1]); });
        });
    }
    fadeGroups().forEach(function (pair) {
        pair[0].forEach(function (node) {
            if (node._fadeWired) { return; }
            node._fadeWired = true;
            node.addEventListener('scroll', function () { setFade(node, pair[1]); }, { passive: true });
        });
    });

    function setOpen(open, animate) {
        // An ANIMATED call that asks for the state it is already in is a no-op. Without this, a "reach the
        // filters" affordance (the sticky mini-bar's Filters button) fired on an already-open drawer
        // collapses it to 0 and re-expands over the full transition -- the panel visibly slams shut and
        // reopens while the page is still scrolling to it. The instant form is NOT guarded: the initial
        // `setOpen(false, false)` has to run to put `hidden` on a panel that renders open for no-JS.
        if (animate && open === (toggle.getAttribute('aria-expanded') === 'true')) { return; }
        if (chipsTimer) { window.clearTimeout(chipsTimer); chipsTimer = null; }
        toggle.classList.toggle('is-open', open);
        toggle.setAttribute('aria-expanded', open ? 'true' : 'false');
        // Active-filter chips belong OUTSIDE the open drawer: adding one while the box is open shoves the
        // whole toolbar down. Hide them the moment it opens, and bring them back only once it has
        // finished animating CLOSED. The class goes on a stable host so the chips' own OOB swaps keep it.
        if (open && chipsHost) { chipsHost.classList.add('is-filters-open'); }
        if (window.PlatPursuit && PlatPursuit.animatePanel) {
            PlatPursuit.animatePanel(panel, open, animate);
        } else if (open) {
            panel.removeAttribute('hidden');
        } else {
            panel.setAttribute('hidden', '');
        }
        if (open) {
            refreshFades();   // the containers only have real dimensions once revealed
        } else if (chipsHost) {
            var reduce = window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches;
            if (animate === false || reduce) { chipsHost.classList.remove('is-filters-open'); }
            // 340ms clears `.pp-bgal__advanced`'s own `0.32s` height transition. A shorter timer brings the
            // chips back mid-collapse and shoves the still-open drawer down -- exactly the layout jump the
            // class exists to prevent.
            else { chipsTimer = window.setTimeout(function () { chipsHost.classList.remove('is-filters-open'); }, 340); }
        }
    }

    on(toggle, 'click', function () { setOpen(toggle.getAttribute('aria-expanded') !== 'true', true); });
    on(form, 'change', refresh);
    on(form, 'input', refresh);
    if (dimSel) {
        // Settle the results the INSTANT a filter changes: this spans the hx-trigger debounce, which is
        // most of the felt wait on a fast server, so the dim is perceptible rather than a few-ms flash.
        // Text inputs are excluded -- they submit on their own debounce, not on `change`.
        on(form, 'change', function (e) {
            var t = e.target;
            if (t && (t.type === 'text' || t.type === 'search')) { return; }
            var results = document.querySelector(dimSel);
            if (results) { results.classList.add('is-swapping'); }
        });
    }
    on(window, 'resize', function () {
        if (toggle.getAttribute('aria-expanded') === 'true') { refreshFades(); }
    });

    refresh();
    setOpen(o.openOnLoad === undefined ? activeCount() > 0 : !!o.openOnLoad, false);

    return {
        refresh: refresh,
        setOpen: setOpen,
        destroy: function () {
            if (chipsTimer) { window.clearTimeout(chipsTimer); chipsTimer = null; }
            listeners.forEach(function (l) { l[0].removeEventListener(l[1], l[2]); });
            listeners = [];
        },
    };
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
     * @returns {Object|null} Controller with a destroy() method, or NULL when the grid, sentinel or
     *   loading element is missing -- callers keeping the handle must null-check before destroying.
     */
    create(config) {
        const grid = document.getElementById(config.gridId);
        const sentinel = document.getElementById(config.sentinelId);
        const loading = document.getElementById(config.loadingId);
        if (!grid || !sentinel || !loading) return null;

        const cardSelector = config.cardSelector || '.card';
        // Resume AFTER the pages already in the grid rather than hard-coding page 2. On a fresh load that's one
        // page -> 2 (unchanged); but an HTMX Back/Forward history restore brings back a snapshot that already
        // contains the previously fetch-appended pages, so starting at 2 would re-fetch + append duplicates.
        // ceil handles a partial last page (its next page 404s and stops). The observe guard below still gates
        // the first fetch on a full first page, so under-a-page grids never fetch regardless of this value.
        const loadedCards = grid.querySelectorAll(cardSelector).length;
        let page = config.paginateBy ? Math.max(2, Math.ceil(loadedCards / config.paginateBy) + 1) : 2;
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

        // Form submit resets pagination. Both the form and the handler are kept so `destroy()` can take
        // the listener back off -- on a page that swaps its results through HTMX the FORM often outlives
        // the grid, so each re-created scroller would otherwise stack another listener on the same node,
        // each closing over a detached grid and a stale nextPageUrl.
        let boundForm = null;
        let onFormSubmit = null;
        if (config.formSelector) {
            const form = document.querySelector(config.formSelector);
            if (form) {
                boundForm = form;
                onFormSubmit = () => {
                    if (config.scrollKey) {
                        localStorage.setItem(config.scrollKey, window.scrollY);
                    }
                    page = 2;
                    queryParams.delete('page');
                    nextPageUrl = `${baseUrl}?page=${page}&${queryParams.toString()}`;
                    if (!config.scrollKey) {
                        grid.innerHTML = '';
                    }
                };
                form.addEventListener('submit', onFormSubmit);
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
                if (boundForm && onFormSubmit) {
                    boundForm.removeEventListener('submit', onFormSubmit);
                }
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
 * TrophyListRenderer -- the condensed "here are the trophies, briefly" list.
 *
 * Built by the Rate My Games wizard's reference panel, which is what it exists for: a hunter rating a game
 * they finished months ago needs the trophy list beside the form to jog the memory.
 *
 * Emits the shared `.pp-trolist` primitive (components/rate-wizard.css) rather than page classes, so any
 * surface that wants the same list gets the same look. Earned rows carry a tier-tinted rail; the tier
 * colours come from the site-wide --tier-* set via the row's `data-tier`.
 */
const TrophyListRenderer = {
    TIERS: ['platinum', 'gold', 'silver', 'bronze'],

    /**
     * Build HTML for a condensed trophy list.
     * @param {Array} trophies - trophy objects from /api/v1/ratings/<c>/group/<g>/trophies/
     * @param {Object} [options]
     * @param {boolean} [options.showEarned=true] - mark the ones this hunter has
     * @returns {string} HTML string
     */
    buildList(trophies, options = {}) {
        const { showEarned = true } = options;
        const esc = HTMLUtils.escape;

        if (!trophies || trophies.length === 0) {
            return '<p class="pp-trolist__empty">No trophy data for this one.</p>';
        }

        const counts = {};
        trophies.forEach(t => { counts[t.trophy_type] = (counts[t.trophy_type] || 0) + 1; });

        // Summary, in tier order rather than whatever order the trophies happen to arrive in. No grand
        // total: the host's own panel header carries the count, and printing it twice an inch apart is
        // noise -- a host that needs one can render it beside this.
        let html = '<div class="pp-trolist__sum">';
        for (const tier of this.TIERS) {
            if (counts[tier]) {
                html += `<span class="pp-trolist__tier" data-tier="${tier}">${counts[tier]} ${tier}</span>`;
            }
        }
        html += '</div>';

        html += '<div class="pp-trolist__rows">';
        for (const t of trophies) {
            const tier = this.TIERS.includes(t.trophy_type) ? t.trophy_type : '';
            const earned = showEarned && t.earned;
            html += `<div class="pp-trolist__row${earned ? ' is-earned' : ''}"${tier ? ` data-tier="${tier}"` : ''}>`;
            html += `<img class="pp-trolist__icon" src="${esc(t.trophy_icon_url || '')}" alt="" loading="lazy" />`;
            html += '<span class="pp-trolist__txt">';
            html += `<span class="pp-trolist__name">${esc(t.trophy_name)}</span>`;
            html += `<span class="pp-trolist__desc">${esc(t.trophy_detail || '')}</span>`;
            html += '</span>';
            if (earned) {
                html += '<svg class="pp-trolist__check" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.6" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><polyline points="20 6 9 17 4 12"/></svg>';
            }
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
 * @param {string} opts.default        the default view -- its URL drops the param entirely
 * @param {string} [opts.param]        the query param to write (default 'view'); use the one the SERVER
 *                                    reads, or the URL will disagree with the page on reload
 * @param {string} [opts.paramView]    the view that owns `opts.params`
 * @param {string[]} [opts.params]     params stripped unless `view === opts.paramView`
 */
function syncViewParam(view, opts) {
    if (!window.history || !history.replaceState) { return; }
    opts = opts || {};
    // `param` defaults to 'view' -- the five existing callers all use that. Job detail passes 'tab',
    // because its SERVER reads `?tab=` (old bookmarks and the board's pager links carry it) and a page
    // that writes one param while reading another produces a URL that lies about what is on screen.
    var key = opts.param || 'view';
    var qp = new URLSearchParams(location.search);
    if (view === opts.default) { qp.delete(key); } else { qp.set(key, view); }
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
    function play(el, delay) {
        // Idempotent: a card reveals exactly once even if re-selected by a re-entrant batch (e.g. a filter
        // swap that also fires a paired OOB afterSwap) or re-observed. `pp-revealing` is set synchronously so
        // the guard holds within the same frame. The VISIBLE resting state (`.is-revealed` -> opacity 1) is
        // flipped one frame LATER: a freshly created WAAPI animation doesn't apply its 0-opacity backwards
        // fill until the next frame, so setting opacity:1 synchronously paints one full-opacity frame before
        // the reveal takes hold -- the "flash to black" (the card shows, then the animation yanks it to 0 and
        // fades up). Deferring keeps the base style hidden (pp-reveal) for that first paint; the running
        // animation then masks the resting state, and at its end the backwards fill reverts to .is-revealed.
        if (el.classList.contains('pp-revealing') || el.classList.contains('is-revealed')) { return; }
        el.classList.add('pp-revealing');
        o.reveal(el, delay);
        if (window.requestAnimationFrame) { requestAnimationFrame(function () { el.classList.add('is-revealed'); }); }
        else { el.classList.add('is-revealed'); }
    }
    // Reveal cards already present in ONE DOM-order batch. DOM order == visual reading order for a row-major
    // grid, independent of the (possibly transitional) column count during a view swap.
    grid.querySelectorAll(sel + ':not(.pp-revealing):not(.is-revealed)').forEach(function (el, i) { play(el, Math.min(i * step, batchCap)); });
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
 * @param {string} [opts.handle]   selector the touch must start inside for the drag to arm at all. Omit on
 *   a sheet you READ; pass one on a sheet you OPERATE, where an accidental dismiss costs unsaved work.
 *
 * A drag starting on `input`/`textarea`/`select`/`[role="slider"]` never arms, on every sheet -- those own
 * their own gestures and this preventDefault()s the movement out from under them. Links and buttons do NOT
 * count: they have no drag gesture, and excluding them would leave a wall-of-cards sheet undraggable.
 */
function dismissableSheet(dialog, opts) {
    if (!dialog) { return; }
    opts = opts || {};
    var onClose = opts.onClose || function () {};
    var scrim = opts.scrim || null;
    var threshold = opts.threshold || 90;
    // `handle`: a selector the touch must START inside for the drag to arm at all. Without it any downward
    // touch anywhere in the sheet drags it, which is fine for a sheet you only read and wrong for one you
    // OPERATE -- see the control guard below.
    var handle = opts.handle || null;
    var startY = null, dragging = false;
    dialog.classList.add('pp-dismissable');   // surfaces the touch-only grabber handle (.pp-dismissable::before)
    function resetStyles() {
        dialog.style.transition = ''; dialog.style.transform = ''; dialog.style.opacity = ''; dialog.style.animation = '';
        if (scrim) { scrim.style.transition = ''; scrim.style.opacity = ''; }
    }
    dialog.addEventListener('touchstart', function (e) {
        // A second finger mid-drag is IGNORED rather than allowed to abort. Clearing `startY` here would
        // strand the sheet at whatever translateY the drag had reached: `touchend` bails on a null startY
        // and never reaches `resetStyles()`, so the dialog keeps its inline transform AND its
        // `animation: none` into the next open.
        if (startY !== null) { return; }
        // A drag that starts on a DRAGGABLE CONTROL belongs to the control. `touchmove` below
        // preventDefault()s any downward movement and translates the whole sheet, so a finger sliding a
        // range input -- which never travels perfectly horizontally -- was dragging the dialog instead of
        // the thumb, and past the threshold threw the form away. Never a dismissal gesture on any sheet,
        // which is why this is unconditional rather than an option.
        //
        // Links and buttons are deliberately NOT in this list. They have no drag gesture of their own to
        // protect (a tap and a drag are told apart by movement), and excluding them would gut the sheets
        // whose bodies are a wall of them -- the badges grid is cards at an 8px gutter, so the only
        // draggable pixels left would be that gutter. Those sheets show a grabber; it has to mean something.
        if (e.target.closest && e.target.closest('input, textarea, select, [role="slider"]')) {
            return;
        }
        // And where a `handle` is named, only that region drags at all.
        if (handle && !(e.target.closest && e.target.closest(handle))) { return; }
        // Only a drag from the very TOP of the scroll dismisses. Walk from the touched element up to the
        // dialog: if anything along the way is scrolled (the dialog itself, or an INNER scroll container like
        // the peek's capped info column), let it scroll instead of hijacking the gesture.
        for (var el = e.target; el; el = el.parentNode) {
            if (el.scrollTop > 0) { return; }
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
    // A gesture the browser takes away (an incoming call, a system edge-swipe) fires touchcancel and NOT
    // touchend, so without this the sheet keeps both its inline transform and an armed `startY` -- visibly
    // stuck, and undraggable from then on because a new touchstart sees a non-null startY.
    dialog.addEventListener('touchcancel', function () {
        if (startY === null) { return; }
        startY = null; dragging = false;
        resetStyles();
    });
}

/**
 * onPageReady -- run a page's wiring on first load AND on HTMX Back/Forward history restore.
 *
 * HTMX restores a pushed-URL page by replacing the history element's innerHTML from a snapshot; it does NOT
 * re-fire DOMContentLoaded or htmx:afterSwap, so the restored DOM is all fresh, unwired nodes. But
 * `document.body` itself persists, so body/document/window listeners survive -- naively re-running init would
 * DOUBLE-bind them. `fn(first)` solves that: `first` is true on the initial load, false on each restore.
 *   - Element wiring (query nodes, bind their listeners, init reveals/scrollers) runs EVERY time -- on a
 *     restore those nodes are new, so the old bindings died with the old nodes (no leak).
 *   - Guard body/document/window listeners with `if (first)` so they bind exactly once and keep working
 *     across restores.
 * This is the shared contract for every HTMX view-swap page (see rebuild-playbook section 7).
 *
 * @param {function(boolean)} fn  called as fn(true) on load, fn(false) on each history restore
 */
function onPageReady(fn) {
    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', function () { fn(true); }, { once: true });
    } else {
        fn(true);   // script ran after DOMContentLoaded already fired (deferred / end-of-body)
    }
    // body persists across a history restore, so this listener (added once) keeps firing.
    document.body.addEventListener('htmx:historyRestore', function () { fn(false); });
}

/**
 * CardDownload: the three-state button for a server-rendered share card.
 *
 * Every share card on the site is a PNG composed by headless Chromium on the server, which is slow
 * enough that a plain click reads as a dead one -- so `busy` is the load-bearing state here, not `done`.
 * And because the file lands in a folder the page cannot see, the save has to be confirmed from the
 * button itself or it looks like nothing happened at all.
 *
 * Extracted from the plat card modal, which is where the states were worked out, once the recap needed
 * the same thing. The recap's ceremony button was a bare `window.location.href` -- no busy state, no
 * confirmation, and a failed render navigated the hunter off the stage into a JSON error.
 *
 * The blob-and-anchor dance is not ceremony either: a direct navigation to the PNG endpoint cannot
 * report a failure (the browser has already left), cannot name the file, and on the recap would have
 * torn down the ceremony to do it.
 *
 * @param {HTMLElement} button    the button; it holds the state classes and gets disabled
 * @param {Object} opts
 * @param {function(): string} opts.url        resolved at CLICK time -- the ground can change between
 *                                             presses, and a URL captured at bind time downloads the
 *                                             card the hunter was looking at a minute ago
 * @param {function(): string} opts.filename   likewise; the name should describe what was rendered
 * @param {string|false} [opts.toast]          success toast text, or false for none
 * @param {function(string, Error)} [opts.onError]  given a ready-to-show message; defaults to a toast
 * @param {function()} [opts.onStart]          before the fetch -- somewhere to clear a stale error line
 * @param {Object} [opts.labels]               overrides for {idle, busy, done}
 * @param {boolean} [opts.autoBind=true]       false when the press is not a plain download: the plat
 *                                             card may open a rating prompt first and only reach the
 *                                             save through its callback, so it calls run() itself
 * @returns {{run: function(), setBlocked: function(boolean), state: function(): string}}
 */
const CardDownload = {
    LABELS: { idle: 'Download', busy: 'Processing...', done: 'Saved' },
    // Long enough to be read, short enough that the button is ready again before anyone reaches for it.
    DONE_MS: 2400,

    attach(button, opts) {
        if (!button || button._ppDownload) { return button && button._ppDownload; }

        const labels = Object.assign({}, this.LABELS, opts.labels || {});
        const labelEl = button.querySelector('[data-dl-label]');
        // The idle label belongs to the CALLER, not to us: the plat card names the variant it is about
        // to save ("Download 100% card"), and a fixed idle string overwrote that the first time the
        // button was used -- so a saved card silently demoted its own button to a generic "Download".
        // Unless one is passed explicitly, idle means "whatever it said before we borrowed it", captured
        // at press time because the caller may not know the label until its payload lands.
        const fixedIdle = opts.labels && opts.labels.idle;
        let idleText = labelEl ? labelEl.textContent : labels.idle;
        const doneMs = this.DONE_MS;
        // Two independent reasons to be disabled -- the caller's (a preview still loading, say) and a
        // download already in flight -- so `disabled` is DERIVED from both. Setting it directly from
        // either one is how a finished download re-enables a button its owner wanted kept shut.
        let blocked = false, busy = false, revertTimer = null, state = 'idle';

        const sync = () => { button.disabled = blocked || busy; };

        const setState = (next) => {
            clearTimeout(revertTimer);
            state = next;
            busy = next === 'busy';
            button.classList.toggle('is-busy', busy);
            button.classList.toggle('is-done', next === 'done');
            sync();
            if (labelEl) {
                labelEl.textContent = next === 'idle' ? (fixedIdle || idleText) : labels[next];
            }
            // Release the pin with the label that needed it. Surfaces that reuse one button across
            // cards get a different idle label each time ("...100% card" / "...platinum card"), and a
            // pin held from the previous one would size the button to the wrong word.
            if (next === 'idle') { button.style.minWidth = ''; }
            // Swapping the label mid-press is a change a screen reader should hear; the button is the
            // only progress indicator, so it has to announce like one.
            button.setAttribute('aria-busy', busy ? 'true' : 'false');
            if (next === 'done') { revertTimer = setTimeout(() => setState('idle'), doneMs); }
        };

        const fail = (message, err) => {
            setState('idle');
            if (opts.onError) { opts.onError(message, err); }
            else if (window.PlatPursuit && window.PlatPursuit.ToastManager) {
                window.PlatPursuit.ToastManager.show(message, 'error', 4000);
            }
        };

        const run = () => {
            if (busy) { return; }
            if (labelEl && !fixedIdle && state === 'idle') { idleText = labelEl.textContent; }
            // Pin the width before the label changes. The stylesheet's min-width sizes the button to the
            // longest of OUR three labels, which is only the whole story when the caller uses the default
            // idle text -- the plat card's "Download 100% card" is wider than all three, so pressing it
            // shrank the button by 50px and shuffled the action row it sits in. Measured, not guessed,
            // because the width depends on the font that actually loaded.
            if (state === 'idle') { button.style.minWidth = `${Math.ceil(button.offsetWidth)}px`; }
            setState('busy');
            if (opts.onStart) { opts.onStart(); }
            fetch(opts.url(), { credentials: 'same-origin' })
                .then((res) => {
                    if (!res.ok) { throw new Error(String(res.status)); }
                    return res.blob();
                })
                .then((blob) => {
                    const href = URL.createObjectURL(blob);
                    const a = document.createElement('a');
                    a.href = href;
                    a.download = opts.filename();
                    document.body.appendChild(a);
                    a.click();
                    document.body.removeChild(a);
                    // Revoking immediately races the download in Safari, which has not necessarily
                    // read the blob by the time the click handler returns.
                    setTimeout(() => URL.revokeObjectURL(href), 1000);
                    setState('done');
                    if (opts.toast !== false && window.PlatPursuit && window.PlatPursuit.ToastManager) {
                        window.PlatPursuit.ToastManager.show(
                            opts.toast || 'Card saved to your downloads.', 'success', 3200);
                    }
                })
                .catch((err) => fail(
                    // The renderer is rate-limited per user, and "try again" is bad advice for the one
                    // failure where trying again is exactly what caused it.
                    err && err.message === '403'
                        ? 'Too many cards at once. Give it a minute.'
                        : "Couldn't render that card. Try again in a moment.",
                    err));
        };

        if (opts.autoBind !== false) {
            button.addEventListener('click', () => { if (!button.disabled) { run(); } });
        }

        const handle = {
            run,
            setBlocked(on) { blocked = !!on; sync(); },
            // For surfaces that reuse one button across several cards: a new card must never be greeted
            // with the previous one's "Saved", and the pending revert timer has to be cancelled with it.
            reset() { setState('idle'); },
            state() { return state; },
        };
        button._ppDownload = handle;
        return handle;
    },
};

/**
 * Takeover: put an element in front of everything and hand it the whole screen.
 *
 * The correctness-critical half of a full-screen surface -- scroll lock, the page-recede depth cue, focus
 * capture and restore, a Tab trap, and Escape -- factored out because three surfaces now do it (the claim
 * ceremony, the Lightbox, and the recap stage) and every one of them re-implemented the accessibility
 * parts slightly differently. Presentation stays entirely with the caller; this owns none of it.
 *
 * The Tab trap is not optional politeness: `aria-modal` alone does NOT stop the browser walking Tab onto
 * the page still sitting behind the scrim, so without it a keyboard user tabs straight out of the
 * takeover into content they cannot see.
 *
 * @param {HTMLElement} root  the takeover element; the caller has already built and inserted it
 * @param {Object} [opts]
 * @param {Function} [opts.onClose]  called once, after teardown
 * @param {boolean}  [opts.recede=true]  step the page back behind it (skipped under reduced motion)
 * @param {number}   [opts.exitMs=240]   how long the caller's exit transition needs before removal
 * @param {string}   [opts.focusSel]     what to focus on open; defaults to the first focusable
 * @param {Function} [opts.onKey]        extra key handling; return true to signal "handled"
 * @param {Function} [opts.onDismiss]    Escape calls THIS instead of closing. For surfaces where being
 *                                       dismissed does not mean being torn down -- the recap navigates
 *                                       away instead, and closing first would play a teardown the new
 *                                       page immediately replaces. The caller may still call close().
 * @returns {{close: Function}}
 */
function takeover(root, opts) {
    opts = opts || {};
    var reduce = window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches;
    var lastFocus = document.activeElement;
    var zoom = (opts.recede === false || reduce) ? null : document.getElementById('zoom-container');
    var prevOverflow = document.body.style.overflow;
    var closed = false;

    var FOCUSABLE = 'a[href], button:not([disabled]), input:not([disabled]), select:not([disabled]), ' +
        'textarea:not([disabled]), [tabindex]:not([tabindex="-1"])';

    function focusable() {
        return Array.prototype.filter.call(root.querySelectorAll(FOCUSABLE), function (el) {
            return el.offsetParent !== null;      // skip anything hidden
        });
    }

    function onKey(e) {
        if (opts.onKey && opts.onKey(e) === true) { return; }
        // A dialog opened INSIDE the takeover owns Escape and the Tab order while it is up. Without this
        // the capture-phase listener fires first and tears down the whole surface on the keypress that
        // was meant to dismiss the dialog -- the takeover would vanish out from under an open modal.
        if (root.querySelector('dialog[open]')) { return; }
        if (e.key === 'Escape') {
            e.preventDefault();
            // Deliberately AFTER the dialog check above: a modal open inside the takeover still owns
            // Escape, so dismissing it must not trigger the surface's own dismissal either.
            if (opts.onDismiss) { opts.onDismiss(); } else { close(); }
            return;
        }
        if (e.key !== 'Tab') { return; }
        var items = focusable();
        if (!items.length) { e.preventDefault(); return; }
        var first = items[0], last = items[items.length - 1];
        if (e.shiftKey && document.activeElement === first) { e.preventDefault(); last.focus(); }
        else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first.focus(); }
    }

    function close() {
        if (closed) { return; }                   // scrim, Escape and the caller's own control all land here
        closed = true;
        document.removeEventListener('keydown', onKey, true);
        document.body.style.overflow = prevOverflow;
        if (zoom) { zoom.classList.remove('pp-receded'); }
        var finish = function () {
            if (root.parentNode) { root.parentNode.removeChild(root); }
            // Return focus to whatever opened it, or the keyboard user is dumped at the top of the page.
            if (lastFocus && lastFocus.focus) { try { lastFocus.focus(); } catch (err) { /* gone */ } }
            if (opts.onClose) { opts.onClose(); }
        };
        if (reduce) { finish(); } else { setTimeout(finish, opts.exitMs != null ? opts.exitMs : 240); }
    }

    document.body.style.overflow = 'hidden';
    if (zoom) { zoom.classList.add('pp-receded'); }
    document.addEventListener('keydown', onKey, true);

    var target = opts.focusSel ? root.querySelector(opts.focusSel) : focusable()[0];
    if (target && target.focus) { target.focus(); }

    return { close: close };
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
window.PlatPursuit.takeover = takeover;
/**
 * Virtualized leaderboard: a full-height spacer with only the visible rows in the DOM.
 *
 * Extracted from game detail's Ranks panel, where the approach was worked out, so every board can use one
 * implementation rather than each growing its own. The surfaces this serves -- Global Boards, badge
 * detail, job detail, game detail -- all want the same three things: the PAGE scrollbar spans the whole
 * board, jumping to a rank is just a scroll position, and scrolling never inserts rows above the viewport
 * (which is what made the old marker/prepend approach lurch).
 *
 * The list is sized to `total * rowHeight` and rows are absolutely positioned by display position, so
 * layout never depends on what is currently mounted. Rows outside the window are evicted from the DOM but
 * their HTML stays cached, so scrolling back is instant and re-fetches nothing.
 *
 * SERVER CONTRACT: `fetchRows(start, from, count)` resolves to HTML containing `rowSelector` elements for
 * display positions [start, start+count). `start` is a display POSITION and `from` is its CANONICAL rank
 * -- they differ only when inverted, and a board that numbers its own rows needs both.
 *
 * @param {Object} o
 * @param {HTMLElement} o.list            the spacer; server-rendered first-window rows live inside it
 * @param {number} o.total                rows on the whole board (sizes the spacer)
 * @param {string} o.rowSelector          e.g. '.lb-row'
 * @param {function(): number} o.rowHeight   current row height in px; re-read on resize (breakpoints)
 * @param {function(number, number, number): Promise<string>} o.fetchRows
 * @param {number} [o.pageSize=50]        fetch granularity; must match what the server pages by
 * @param {boolean} [o.invert=false]      display order is the reverse of canonical rank
 * @param {string} [o.rankKey='lbRank']   dataset key on a row holding its CANONICAL rank
 * @param {number} [o.youRank]           the viewer's rank; that row gets `.is-you` + `aria-current`
 * @param {function(): number} [o.chromeInset]  sticky-header height, so a jump lands below it
 * @param {function(number, number, function)} [o.onRender]  (localTop, localBottom, posOf) per frame
 * @returns {{jump: function(number), refresh: function(), destroy: function()}}
 */
function virtualBoard(o) {
    var list = o.list;
    var total = o.total;
    var noop = function () {};
    if (!list || !total) { return { jump: noop, refresh: noop, destroy: noop }; }

    var PAGE = o.pageSize || 50;
    var invert = !!o.invert;
    var rankKey = o.rankKey || 'lbRank';
    var youRank = o.youRank || 0;    // 0 = anonymous, or a viewer who is not on this board
    var BUFFER = 8;      // rows rendered beyond the viewport each way
    var EVICT = 30;      // keep rows within this of the window mounted
    var H = o.rowHeight();

    var dataByPos = new Map();     // display-pos (1-indexed) -> row HTML, cached
    var rendered = new Map();      // display-pos -> element in the DOM
    var fetchedPages = new Set();  // page indices already fetched / in flight
    var highlightDp = 0;           // display-pos kept lit after a jump
    var highlightAnchor = 0;       // the destination scrollTop the jump scrolls TO
    var highlightArmed = false;    // true once the jump scroll has actually arrived

    function scroller() { return document.scrollingElement || document.documentElement; }
    function inset() { return o.chromeInset ? o.chromeInset() : 0; }

    // Canonical rank of a display position, and back. The LABEL is canonical; layout is by position.
    function rankOf(dp) { return invert ? total - dp + 1 : dp; }
    function posOf(rank) { return invert ? total - rank + 1 : rank; }

    function clearHighlight() {
        if (!highlightDp) { return; }
        var el = rendered.get(highlightDp);
        if (el) { el.classList.remove('is-found'); }
        highlightDp = 0;
        highlightArmed = false;
    }

    // Light `dp` and remember where the jump is scrolling to. `armed` stays false until that scroll
    // reaches the anchor, so the jump's own (smooth) travel is never mistaken for the user scrolling away.
    function setHighlight(dp, anchorY) {
        clearHighlight();
        highlightDp = dp;
        highlightAnchor = anchorY;
        highlightArmed = false;
        var el = rendered.get(dp);
        if (el) { el.classList.add('is-found'); }
    }

    // A reveal boot that ran before us leaves `.pp-reveal` on the wall, and `.pp-reveal .row` is
    // `opacity: 0` until a row earns `.is-revealed`. This engine mounts and unmounts rows continuously,
    // and they never reach that observer -- so every row past the first screenful would arrive INVISIBLE.
    // Stripped here rather than left to each page to remember, because the failure is silent: the board
    // looks frozen, nothing errors, and on a page whose wall gets replaced by a filter swap it only
    // reproduces on first load. (Beta caught exactly that on the Global Boards.)
    //
    // A virtualized row appears because you scrolled to it, which IS the reveal -- so there is nothing to
    // reinstate, only motion to stop fighting.
    list.classList.remove('pp-reveal');

    list.style.height = (total * H) + 'px';

    // Seed the cache + DOM from the server-rendered first window; convert those rows to absolute.
    Array.prototype.forEach.call(list.querySelectorAll(o.rowSelector), function (el) {
        var dp = posOf(parseInt(el.dataset[rankKey], 10));
        el.style.top = ((dp - 1) * H) + 'px';
        // Cached BEFORE marking, so the stored HTML stays the server's viewer-independent version -- a
        // remount re-applies the mark rather than baking it into the cache.
        dataByPos.set(dp, el.outerHTML);
        rendered.set(dp, el);
        describe(el, dp);
        markYou(el, dp);
    });
    fetchedPages.add(0);                       // the first window IS page 0

    // The viewer's own row, marked CLIENT-SIDE. The server's rows are byte-identical for every reader,
    // which is what keeps them cacheable -- so "this one is you" is applied here rather than rendered in.
    // Applied on every mount, not once: a row is evicted and remounted freely as you scroll past it.
    function markYou(el, dp) {
        if (!youRank || rankOf(dp) !== youRank) { return; }
        el.classList.add('is-you');
        el.setAttribute('aria-current', 'true');   // announced; the tint alone is not
    }

    // Mounted IN ORDER, not appended. Rows are evicted and remounted as you scroll, so appending meant
    // that scrolling UP put lower-numbered ranks at the end of the <ol> -- after which DOM order (which
    // is the order a screen reader reads, and the order Tab walks the hunter links) no longer matched
    // what was on screen. The mounted window is ~30 rows, so finding the insertion point is free.
    function mount(dp) {
        var tmp = document.createElement('template');
        tmp.innerHTML = dataByPos.get(dp).trim();
        var el = tmp.content.firstElementChild;
        el.style.top = ((dp - 1) * H) + 'px';

        var after = null, afterDp = Infinity;
        rendered.forEach(function (other, otherDp) {
            if (otherDp > dp && otherDp < afterDp) { afterDp = otherDp; after = other; }
        });
        list.insertBefore(el, after);

        rendered.set(dp, el);
        describe(el, dp);
        markYou(el, dp);
        if (dp === highlightDp) { el.classList.add('is-found'); }   // keep it lit across a remount
    }

    // The <ol> only ever holds the mounted window, so unaided it announces "list, 26 items" on a board of
    // 60,000 -- and the number changes as you scroll. `aria-posinset`/`aria-setsize` state the real
    // position and the real total, which is what the rank in the row means anyway.
    function describe(el, dp) {
        el.setAttribute('aria-posinset', dp);
        el.setAttribute('aria-setsize', total);
    }

    function fetchPage(p) {
        if (fetchedPages.has(p)) { return; }
        fetchedPages.add(p);
        var start = p * PAGE + 1;
        o.fetchRows(start, rankOf(start), PAGE)
            .then(function (html) {
                if (!list.isConnected) { return; }
                var tmp = document.createElement('template');
                tmp.innerHTML = String(html).trim();
                Array.prototype.forEach.call(tmp.content.querySelectorAll(o.rowSelector), function (el, i) {
                    dataByPos.set(start + i, el.outerHTML);
                });
                render();
            })
            .catch(function () { fetchedPages.delete(p); });   // allow a retry on the next scroll
    }

    function visible() {
        var rect = list.getBoundingClientRect();               // list top relative to the viewport
        var localTop = Math.max(0, -rect.top);
        var localBottom = Math.min(total * H, window.innerHeight - rect.top);
        return [
            Math.max(1, Math.floor(localTop / H) + 1 - BUFFER),
            Math.min(total, Math.ceil(localBottom / H) + BUFFER),
            localTop,
            localBottom
        ];
    }

    function render() {
        // A HIDDEN panel measures as zeros, so `visible()` would compute the first screenful as on-screen
        // and keep mounting rows on every scroll frame of whatever tab the reader is actually on. Badge
        // and job detail leave their board mounted when the tab closes (so re-opening is instant), which
        // is what makes this reachable.
        if (!list.offsetParent && getComputedStyle(list).position !== 'fixed') { return; }
        // Keep the jump highlight lit through the jump's own scroll, drop it once the USER scrolls away.
        // Movement alone cannot tell the two apart, so we ARM on arrival: while the (smooth) scroll is
        // still travelling toward the anchor it stays lit; once scrollTop lands within a row of the anchor
        // it has arrived, and after that a row-plus of movement is the user leaving.
        if (highlightDp) {
            var dist = Math.abs(scroller().scrollTop - highlightAnchor);
            if (!highlightArmed) { if (dist <= H) { highlightArmed = true; } }
            else if (dist > H) { clearHighlight(); }
        }
        var win = visible(), first = win[0], last = win[1];
        rendered.forEach(function (el, dp) {
            if (dp < first - EVICT || dp > last + EVICT) { el.remove(); rendered.delete(dp); }
        });
        for (var dp = first; dp <= last; dp++) {
            if (rendered.has(dp)) { continue; }
            if (dataByPos.has(dp)) { mount(dp); }
            else { fetchPage(Math.floor((dp - 1) / PAGE)); }
        }
        if (o.onRender) { o.onRender(win[2], win[3], posOf); }
    }

    // Smooth-scroll the PAGE so the target row lands ~a third down below the chrome, and keep it lit on
    // arrival. The highlight is anchored to the DESTINATION and armed by render() once the scroll gets
    // there, so the animation's own travel cannot read as "scrolled away" and clear it before it lands.
    // Reduced-motion users get an instant landing, armed at once.
    function jump(rank) {
        var dp = Math.max(1, Math.min(posOf(rank), total));
        var sc = scroller();
        var listTopDoc = window.scrollY + list.getBoundingClientRect().top;
        var chrome = inset();
        var maxTop = Math.max(0, sc.scrollHeight - sc.clientHeight);
        var y = Math.min(
            Math.max(0, listTopDoc + (dp - 1) * H - chrome - (window.innerHeight - chrome) * 0.34),
            maxTop
        );
        var reduce = window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches;
        setHighlight(dp, y);                                   // anchor to where the scroll will land
        sc.scrollTo({ top: y, behavior: reduce ? 'instant' : 'smooth' });
        render();                                              // the travel mounts the rest
    }

    // The row height changes across breakpoints, so re-read it, resize the spacer, and re-place the
    // mounted rows before rendering again.
    function relayout() {
        H = o.rowHeight();
        list.style.height = (total * H) + 'px';
        rendered.forEach(function (el, dp) { el.style.top = ((dp - 1) * H) + 'px'; });
        render();
    }

    // Both scroll and resize coalesce to one rAF (resize -> full relayout, scroll -> render).
    var ticking = false;
    function tick(fn) {
        if (ticking) { return; }
        ticking = true;
        requestAnimationFrame(function () { ticking = false; fn(); });
    }
    function onScroll() { tick(render); }
    function onResize() { tick(relayout); }
    window.addEventListener('scroll', onScroll, { passive: true });
    window.addEventListener('resize', onResize, { passive: true });

    render();

    return {
        jump: jump,
        refresh: render,
        destroy: function () {
            window.removeEventListener('scroll', onScroll);
            window.removeEventListener('resize', onResize);
        }
    };
}


/**
 * Mount a virtualized board from its `[data-lb-board]` root, and wire its jump affordances.
 *
 * `virtualBoard` is the ENGINE; this is the WIRING every board was otherwise copying -- read the data
 * attributes, build a rows URL, mount, then hook up the "you're #N" chip and the go-to-rank box. Three
 * surfaces run boards (Global Boards, badge detail's Ranks tab, job detail's Ranks tab) and a fourth is
 * adjacent (game detail, which has its own row component and its own toolbar). Copying ~50 lines of
 * wiring per surface is how a board ends up fetching in a granularity its server does not page by, which
 * fails as GAPS IN THE ROWS rather than as an error.
 *
 * The contract is the markup: `templates/trophies/partials/leaderboard_board.html` renders the root and
 * `leaderboard_jumpbar.html` the affordances. A caller supplies neither a page size nor a URL -- if it
 * did, that constant would be free to disagree with the server's. A root missing `data-lb-page-size`
 * does NOT fall back to a guess; it declines to mount, because guessing reproduces exactly the silent
 * row-gaps the attribute exists to prevent.
 *
 * Always returns a handle, never null. Even with no board to mount it wires the rank box's submit and
 * swallows it, because that <form> has no action and an unhandled Enter is a native GET that drops the
 * reader's filters.
 *
 * @param {HTMLElement} root      the `[data-lb-board]` element
 * @param {Object} [o]
 * @param {HTMLElement} [o.scope=root]  where the jump chip / rank box are looked for. Global Boards puts
 *                                      them on a card ABOVE the board, so its scope is the page wrapper.
 * @param {function(): number} [o.chromeInset]  sticky-header height, so a jump lands below it
 * @param {function(number, number, function)} [o.onRender]  forwarded to the engine
 * @returns {{jump: function(number), refresh: function(), destroy: function()}}  always a handle; an
 *          inert one when there is no board (an empty state came back)
 */
function wireBoard(root, o) {
    o = o || {};
    var scope = (o && o.scope) || root;

    // THE SUBMIT GUARD COMES FIRST, before any reason to bail out. The rank box is a real <form> with no
    // action and an unnamed input, so an unhandled Enter is a native GET to the bare path -- which drops
    // the reader's `?tab=`/`?country=`/`?edition=` and reloads the page they were reading. Wiring it only
    // on the success path meant an empty board (or a missing one) navigated away instead of doing
    // nothing, which is the worst of the three possible behaviours.
    var handle = null;
    function onSubmit(ev) {
        var form = ev.target.closest && ev.target.closest('[data-lb-gotoform]');
        if (!form || !scope.contains(form)) { return; }
        ev.preventDefault();                       // unconditional: never navigate
        if (!handle) { return; }
        var input = form.querySelector('input');
        var rank = parseInt((input && input.value) || '', 10);
        if (!(rank >= 1)) { return; }
        handle.jumpTo(Math.min(rank, handle.total));
        if (input) { input.blur(); }               // dismiss the touch keyboard before the scroll starts
    }
    scope.addEventListener('submit', onSubmit);

    function bail() {
        return { jump: function () {}, refresh: function () {}, destroy: function () {
            scope.removeEventListener('submit', onSubmit);
        } };
    }
    if (!root) { return bail(); }
    var wall = root.querySelector('[data-lb-wall]');
    var total = parseInt(root.dataset.lbTotal, 10) || 0;
    if (!wall || !total) { return bail(); }

    // NO FALLBACK on the page size, deliberately. `|| 50` was a JS copy of a server constant, and the
    // fallback IS the failure mode it was meant to cover: a client paging by 50 against a server paging
    // by something else does not error, it shows gaps in the rows. Missing means misconfigured, and a
    // board that refuses to mount is a bug somebody notices.
    var pageSize = parseInt(root.dataset.lbPageSize, 10);
    if (!(pageSize >= 1)) { return bail(); }
    var XHR = { headers: { 'X-Requested-With': 'XMLHttpRequest' } };
    var said = scope.querySelector('[data-lb-said]');
    var viewerRank = parseInt(root.dataset.lbViewerRank || '', 10);
    if (!(viewerRank >= 1)) { viewerRank = 0; }

    // The row height is a CSS custom property so the breakpoints own it, and it is read live rather than
    // captured: `--lb-row-h` changes at md:, and a captured value would place every row at the old pitch
    // after a rotate.
    function rowHeight() {
        return parseFloat(getComputedStyle(wall).getPropertyValue('--lb-row-h')) || 62;
    }

    var engine = PlatPursuit.virtualBoard({
        list: wall,
        total: total,
        rowSelector: '.lb-row',
        pageSize: pageSize,
        rowHeight: rowHeight,
        youRank: viewerRank,
        chromeInset: o.chromeInset,
        fetchRows: function (start, from, count) {
            var qp = new URLSearchParams(root.dataset.lbParams || '');
            qp.set('range', start);
            qp.set('from', from);
            qp.set('count', count);
            return fetch(root.dataset.lbRowsUrl + '?' + qp.toString(), XHR).then(function (r) {
                if (!r.ok) { throw new Error(r.status); }
                return r.text();
            });
        },
        onRender: function (localTop, localBottom, posOf) {
            // Which way the reader would travel to reach their own row, so the chip says where it GOES
            // rather than only that it exists.
            var chip = scope.querySelector('[data-lb-jump]');
            if (chip && viewerRank) {
                var H = rowHeight();
                var top = (posOf(viewerRank) - 1) * H;
                chip.dataset.lbDir = top + H < localTop ? 'up' : (top > localBottom ? 'down' : 'here');
            }
            if (o.onRender) { o.onRender(localTop, localBottom, posOf); }
        },
    });

    // A jump is a SCROLL, and a scroll is invisible to a screen reader -- there is no focus change and no
    // DOM event a reader is told about. The removed "show more" had a live region for exactly this
    // reason. `[data-lb-said]` is `sr-only`, so this costs the visual design nothing.
    function announce(rank) {
        if (said) { said.textContent = 'Jumped to rank ' + rank + ' of ' + total + '.'; }
    }
    function jumpTo(rank) {
        engine.jump(rank);
        announce(rank);
    }

    // DELEGATED from the scope, not bound to the chip and the form. On every surface here the board
    // arrives by fetch and can be replaced (a tab re-open, a filter swap), so a listener bound to the
    // elements themselves would die with the markup it was bound to -- silently, since the board still
    // scrolls and only the jumping stops working.
    function onClick(ev) {
        var chip = ev.target.closest && ev.target.closest('[data-lb-jump]');
        if (!chip || !scope.contains(chip) || !viewerRank) { return; }
        ev.preventDefault();
        jumpTo(viewerRank);
    }
    scope.addEventListener('click', onClick);

    handle = {
        total: total,
        jumpTo: jumpTo,
        jump: jumpTo,
        refresh: engine.refresh,
        destroy: function () {
            scope.removeEventListener('click', onClick);
            scope.removeEventListener('submit', onSubmit);
            engine.destroy();
        },
    };
    return handle;
}

/**
 * The board's entrance: the on-screen rows cascade in once, on first mount.
 *
 * NOT `staggerReveal`, and that is the whole point of it existing. That helper adds `.pp-reveal` to the
 * container permanently and `.pp-reveal .lb-row { opacity: 0 }` holds every row invisible until an
 * IntersectionObserver grants it `.is-revealed` -- which a virtualized wall never gets, because its rows
 * are mounted and evicted by scroll position rather than observed. The result is a board of blank space.
 * That exact bug shipped twice (badge detail's "show more", then the Global Boards wall), so the boards
 * animate with the Web Animations API instead: it leaves no class behind and cannot outlive its frames.
 *
 * The board card's Tally ticks here too, because this is the one place all three surfaces call on every
 * mount. It was wired on the landing alone, in a boot block that ran once -- so a tab swap replaced the
 * card and the new figure simply appeared, and the two fetched panels never ticked at all. Deliberately
 * NOT applied to the rows: fifty simultaneous counters is the "frantic counters" anti-pattern.
 *
 * @param {HTMLElement} root    the `[data-lb-board]` element
 * @param {HTMLElement} [scope] where the board card is looked for; it sits OUTSIDE the board root, on
 *                              the chrome card above it. Defaults to `root`.
 */
function boardEntrance(root, scope) {
    var tally = (scope || root || document).querySelector('.lb-boardcard__tally [data-countup]');
    // `countUp` jumps straight to the target under reduced motion, so it needs no guard of its own --
    // which is why it runs before the early return below rather than after it.
    if (tally && PlatPursuit.countUp) { PlatPursuit.countUp(tally, 850); }

    if (!root || (window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches)) { return; }
    var rows = root.querySelectorAll('.lb-row');
    for (var i = 0; i < rows.length && i < 14; i++) {      // the visible window only -- keep it quick
        if (!rows[i].animate) { return; }
        rows[i].animate(
            [{ opacity: 0, transform: 'translateY(10px)' }, { opacity: 1, transform: 'none' }],
            { duration: 340, delay: i * 26, easing: 'cubic-bezier(0.2, 0.8, 0.2, 1)', fill: 'backwards' }
        );
    }
}

window.PlatPursuit.animatePanel = animatePanel;
window.PlatPursuit.filterPanel = filterPanel;
window.PlatPursuit.InfiniteScroller = InfiniteScroller;
window.PlatPursuit.DragReorderManager = DragReorderManager;
window.PlatPursuit.ZoomAwareObserver = ZoomAwareObserver;
window.PlatPursuit.LeaderboardUtils = LeaderboardUtils;
window.PlatPursuit.ReviewProgressTiers = ReviewProgressTiers;
window.PlatPursuit.TrophyListRenderer = TrophyListRenderer;
window.PlatPursuit.SpoilerToggle = SpoilerToggle;
window.PlatPursuit.Lightbox = Lightbox;
window.PlatPursuit.StickyReveal = StickyReveal;
window.PlatPursuit.virtualBoard = virtualBoard;
window.PlatPursuit.wireBoard = wireBoard;
window.PlatPursuit.boardEntrance = boardEntrance;
window.PlatPursuit.slideViewIn = slideViewIn;
window.PlatPursuit.igniteTab = igniteTab;
window.PlatPursuit.wireTablist = wireTablist;
window.PlatPursuit.syncViewParam = syncViewParam;
window.PlatPursuit.staggerReveal = staggerReveal;
window.PlatPursuit.dismissableSheet = dismissableSheet;
window.PlatPursuit.CardDownload = CardDownload;
window.PlatPursuit.onPageReady = onPageReady;

/**
 * wireGuidelinesSheet -- the Community Guidelines sheet (`#gd-guidelines-modal`), opened OVER whatever
 * compose surface is showing from that surface's `[data-gd-guidelines-open]` link, so reading the rules
 * never loses an in-progress quick take. Read-only; agreement is recorded on submit, not here.
 *
 * Called by `RatingFields.attach` (quick-rate.js), because the link lives in the shared field partial and
 * so appears on every surface that composes those fields -- Game Detail's Ratings tab, the plat-card share
 * modal, and the Rate My Games wizard. The page controllers used to call this themselves, and the wizard
 * was the host that forgot. Stacking a second <dialog>.showModal() puts it on top; closing returns focus
 * to the modal underneath.
 *
 * Idempotent: safe to call from several page controllers, and a no-op when the sheet isn't on the page.
 */
var _guidelinesDelegateBound = false;
function wireGuidelinesSheet() {
    // The open delegate is on DOCUMENT, which survives an htmx body swap -- so it must be bound ONCE per
    // page load, not once per sheet element. Guarding it on the element (which is replaced by the swap)
    // added a listener per restore, each holding a detached dialog and throwing InvalidStateError on
    // every click. It resolves the sheet at call time for that reason.
    if (!_guidelinesDelegateBound) {
        _guidelinesDelegateBound = true;
        document.addEventListener('click', function (e) {
            if (!e.target.closest('[data-gd-guidelines-open]')) { return; }
            var live = document.getElementById('gd-guidelines-modal');
            if (live && live.showModal && !live.open) { live.showModal(); }
        });
    }
    // Element-level wiring dies with the node, so this half IS per element.
    var sheet = document.getElementById('gd-guidelines-modal');
    if (!sheet || sheet.dataset.wired === '1') { return; }
    sheet.dataset.wired = '1';
    var close = function () { if (sheet.close && sheet.open) { sheet.close(); } };
    sheet.querySelectorAll('[data-gd-modal-close]').forEach(function (b) { b.addEventListener('click', close); });
    sheet.addEventListener('click', function (e) { if (e.target === sheet) { close(); } });
    sheet.addEventListener('cancel', function (e) { e.preventDefault(); close(); });
    if (window.PlatPursuit.dismissableSheet) { window.PlatPursuit.dismissableSheet(sheet, { onClose: close }); }
}
window.PlatPursuit.wireGuidelinesSheet = wireGuidelinesSheet;

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
