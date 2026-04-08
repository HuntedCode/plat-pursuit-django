/**
 * browse-filters.js
 *
 * Shared HTMX filter controller for all browse pages (Games, Trophies,
 * Profiles, Companies, Genres, Flagged Games).
 *
 * Hybrid interaction model:
 *   - Checkboxes, chips, toggles, radio buttons, and selects auto-submit
 *     on change (instant feedback).
 *   - Text inputs (search) submit on Enter via the form's native submit.
 *   - An explicit submit button is kept for text-input confirmation but is
 *     not required for other controls.
 *
 * Usage:
 *   Add data-browse-form to the <form>. Inside it:
 *   - Auto-submit elements: checkboxes, radios, selects, and elements with
 *     [data-auto-submit] trigger an HTMX request on change.
 *   - The results container must have id="browse-results".
 *   - The form should have hx-get, hx-target="#browse-results",
 *     hx-push-url="true" set in the template.
 *
 * The module also handles:
 *   - Page-jump forms (pagination input).
 *   - Loading indicator on the results container.
 *   - Re-initializing tooltips or other post-swap JS after HTMX swap.
 */
(function () {
  'use strict';

  function init() {
    const form = document.querySelector('[data-browse-form]');
    if (!form) return;

    // ── Auto-submit for non-text controls ──────────────────────────────
    form.addEventListener('change', function (e) {
      const el = e.target;
      const isAutoSubmit =
        el.type === 'checkbox' ||
        el.type === 'radio' ||
        el.tagName === 'SELECT' ||
        el.closest('[data-auto-submit]');

      if (isAutoSubmit) {
        // Reset to page 1 on any filter change
        const pageInput = form.querySelector('input[name="page"]');
        if (pageInput) pageInput.value = '1';

        updateFilterBadge();
        htmx.trigger(form, 'submit');
      }
    });

    // ── Toggle buttons (platinum, shovelware, flags, view) ─────────────
    // Toggle buttons use hidden inputs inside the form. Clicking the button
    // toggles the hidden input value and triggers a submit.
    document.querySelectorAll('[data-filter-toggle]').forEach(function (btn) {
      btn.addEventListener('click', function (e) {
        e.preventDefault();
        const targetName = btn.dataset.filterToggle;
        const input = form.querySelector('input[name="' + targetName + '"]');
        if (!input) return;

        if (input.value === 'on') {
          input.value = '';
          btn.classList.remove('btn-active');
        } else {
          input.value = 'on';
          btn.classList.add('btn-active');
        }

        // Reset to page 1
        const pageInput = form.querySelector('input[name="page"]');
        if (pageInput) pageInput.value = '1';

        htmx.trigger(form, 'submit');
      });
    });

    // ── View toggle (grid/list) ────────────────────────────────────────
    document.querySelectorAll('[data-view-toggle]').forEach(function (btn) {
      btn.addEventListener('click', function (e) {
        e.preventDefault();
        var newView = btn.dataset.viewToggle;
        var input = form.querySelector('input[name="view"]');
        if (!input) return;
        input.value = newView;

        // Flip the button so the next click toggles back
        var opposite = newView === 'list' ? 'grid' : 'list';
        btn.dataset.viewToggle = opposite;
        btn.title = opposite === 'list' ? 'Switch To List View' : 'Switch To Grid View';
        btn.querySelector('span, svg:last-of-type');
        // Swap icon and label
        var isNowList = newView === 'list';
        btn.innerHTML = isNowList
          ? '<svg xmlns="http://www.w3.org/2000/svg" class="w-3.5 h-3.5" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="3" width="7" height="7"></rect><rect x="14" y="3" width="7" height="7"></rect><rect x="14" y="14" width="7" height="7"></rect><rect x="3" y="14" width="7" height="7"></rect></svg>Grid'
          : '<svg xmlns="http://www.w3.org/2000/svg" class="w-3.5 h-3.5" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><line x1="3" y1="12" x2="21" y2="12"></line><line x1="3" y1="6" x2="21" y2="6"></line><line x1="3" y1="18" x2="21" y2="18"></line></svg>List';

        htmx.trigger(form, 'submit');
      });
    });

    // ── Text search: submit on Enter ───────────────────────────────────
    const searchInputs = form.querySelectorAll('input[type="text"], input[type="search"]');
    searchInputs.forEach(function (input) {
      input.addEventListener('keydown', function (e) {
        if (e.key === 'Enter') {
          e.preventDefault();
          const pageInput = form.querySelector('input[name="page"]');
          if (pageInput) pageInput.value = '1';
          htmx.trigger(form, 'submit');
        }
      });
    });

    // ── Submit button (for users who click instead of pressing Enter) ──
    const submitBtn = form.querySelector('[data-submit-btn]');
    if (submitBtn) {
      submitBtn.addEventListener('click', function (e) {
        e.preventDefault();
        const pageInput = form.querySelector('input[name="page"]');
        if (pageInput) pageInput.value = '1';
        htmx.trigger(form, 'submit');
      });
    }
  }

  // ── Active filter badge on drawer summary ──────────────────────────────
  // Keys that don't count as "active filters" (display/pagination state only)
  var IGNORED_KEYS = {'page': 1, 'view': 1};

  function updateFilterBadge() {
    var form = document.querySelector('[data-browse-form]');
    if (!form) return;

    var summary = form.querySelector('.collapse-title');
    if (!summary) return;

    var badge = summary.querySelector('[data-filter-badge]');
    var hasActive = false;

    var formData = new FormData(form);
    formData.forEach(function (value, key) {
      if (!IGNORED_KEYS[key] && value) {
        hasActive = true;
      }
    });

    if (hasActive && !badge) {
      var span = document.createElement('span');
      span.className = 'badge badge-xs badge-primary animate-pulse';
      span.dataset.filterBadge = '';
      span.textContent = 'Active';
      summary.appendChild(span);
      summary.classList.add('text-primary');
    } else if (!hasActive && badge) {
      badge.remove();
      summary.classList.remove('text-primary');
    }
  }

  // ── Loading indicator ──────────────────────────────────────────────────
  document.addEventListener('htmx:beforeRequest', function (e) {
    const results = document.getElementById('browse-results');
    if (results && e.detail.target === results) {
      results.classList.add('opacity-50', 'pointer-events-none');
    }
  });

  document.addEventListener('htmx:afterSwap', function (e) {
    const results = document.getElementById('browse-results');
    if (results) {
      results.classList.remove('opacity-50', 'pointer-events-none');
    }

    // Re-bind page-jump forms inside the swapped content
    bindPageJumpForms();
    updateFilterBadge();
  });

  // ── Page-jump forms (pagination) ────────────────────────────────────
  function bindPageJumpForms() {
    document.querySelectorAll('.page-jump-form').forEach(function (pjForm) {
      if (pjForm.dataset.bound) return;
      pjForm.dataset.bound = 'true';

      pjForm.addEventListener('submit', function (e) {
        e.preventDefault();
        const browseForm = document.querySelector('[data-browse-form]');
        if (!browseForm) return;

        const pageVal = pjForm.querySelector('input[name="page"]')?.value;
        const pageInput = browseForm.querySelector('input[name="page"]');
        if (pageInput && pageVal) {
          pageInput.value = pageVal;
        }
        htmx.trigger(browseForm, 'submit');
      });
    });
  }

  // ── Pagination link interception ────────────────────────────────────
  // Pagination links (prev/next/first/last) should go through HTMX too.
  document.addEventListener('click', function (e) {
    const link = e.target.closest('[data-page-link]');
    if (!link) return;

    e.preventDefault();
    const browseForm = document.querySelector('[data-browse-form]');
    if (!browseForm) return;

    const page = link.dataset.pageLink;
    const pageInput = browseForm.querySelector('input[name="page"]');
    if (pageInput && page) {
      pageInput.value = page;
    }
    htmx.trigger(browseForm, 'submit');
  });

  // ── Save / Clear browse defaults ────────────────────────────────────
  function bindDefaultsButtons() {
    document.querySelectorAll('[data-save-defaults]').forEach(function (btn) {
      if (btn.dataset.boundDefaults) return;
      btn.dataset.boundDefaults = 'true';

      btn.addEventListener('click', function (e) {
        e.preventDefault();
        var page = btn.dataset.saveDefaults;
        var form = document.querySelector('[data-browse-form]');
        if (!form) return;

        // Collect current filter state from the form
        var formData = new FormData(form);
        var filters = {};
        formData.forEach(function (value, key) {
          if (key === 'page') return; // Skip pagination state
          if (!value) return;         // Skip empty values
          if (filters[key]) {
            if (!Array.isArray(filters[key])) filters[key] = [filters[key]];
            filters[key].push(value);
          } else {
            filters[key] = value;
          }
        });

        PlatPursuit.API.post('/api/v1/user/quick-settings/', {
          setting: 'browse_defaults',
          value: { page: page, filters: filters }
        }).then(function () {
          PlatPursuit.ToastManager.success('Default filters saved!');
        }).catch(function () {
          PlatPursuit.ToastManager.error('Failed to save defaults.');
        });
      });
    });

    document.querySelectorAll('[data-clear-defaults]').forEach(function (btn) {
      if (btn.dataset.boundDefaults) return;
      btn.dataset.boundDefaults = 'true';

      btn.addEventListener('click', function (e) {
        e.preventDefault();
        var page = btn.dataset.clearDefaults;

        PlatPursuit.API.post('/api/v1/user/quick-settings/', {
          setting: 'browse_defaults',
          value: { page: page, filters: {} }
        }).then(function () {
          PlatPursuit.ToastManager.success('Default filters cleared.');
        }).catch(function () {
          PlatPursuit.ToastManager.error('Failed to clear defaults.');
        });
      });
    });
  }

  // ── Initialize on DOMContentLoaded and after HTMX history restore ───
  document.addEventListener('DOMContentLoaded', function () {
    init();
    bindPageJumpForms();
    bindDefaultsButtons();
  });

  document.addEventListener('htmx:historyRestore', function () {
    init();
    bindPageJumpForms();
  });
})();
