/* Horizon primitive (progress treatment) -- client API.
 * The bars are server-rendered + CSS-driven; this is only for client-side updates (set a
 * bar's progress and, in `band` tone, recompute its cool->warm band). bandFor() MUST stay in
 * sync with the horizon_band template filter (core/templatetags/custom_filters.py).
 * Namespaced under window.PlatPursuit.Horizon. */
(function () {
    'use strict';

    function bandFor(pct) {
        pct = Number(pct) || 0;
        if (pct < 30) return 'cool';
        if (pct < 65) return 'warming';
        if (pct < 90) return 'warm';
        return 'hot';
    }

    // Set a .pp-horizon root's progress; in band tone, refresh data-horizon-band too.
    function update(root, pct) {
        if (!root) return;
        pct = Math.max(0, Math.min(100, Number(pct) || 0));
        root.style.setProperty('--horizon-progress', pct + '%');
        if (root.hasAttribute('data-horizon-band')) {
            root.setAttribute('data-horizon-band', bandFor(pct));
        }
    }

    window.PlatPursuit = window.PlatPursuit || {};
    window.PlatPursuit.Horizon = { bandFor: bandFor, update: update };
})();
