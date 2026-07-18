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

    // ── Text search: submit on Enter. Forms that opt in with `data-live-search` ALSO live-filter as you
    // type (debounced). Opt-in only: most browse forms carry hx-push-url, so auto-submitting per keystroke
    // burst would spam history + multiply queries on every list endpoint. Enter stays the universal path.
    const searchInputs = form.querySelectorAll('input[type="text"], input[type="search"]');
    const liveSearch = form.hasAttribute('data-live-search');
    let searchTimer = null;
    function submitSearch() {
      const pageInput = form.querySelector('input[name="page"]');
      if (pageInput) pageInput.value = '1';
      htmx.trigger(form, 'submit');
    }
    searchInputs.forEach(function (input) {
      input.addEventListener('keydown', function (e) {
        if (e.key === 'Enter') {
          e.preventDefault();
          clearTimeout(searchTimer);
          submitSearch();
        }
      });
      if (liveSearch) {
        input.addEventListener('input', function () {
          clearTimeout(searchTimer);
          searchTimer = setTimeout(submitSearch, 400);
        });
      }
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

    // ── Range sliders (ratings + time-to-beat) ────────────────────────────
    initBrowseSliders(form);

    // ── Community flag split-controls ([+ Label −]) ───────────────────
    initFlagSplitControls(form);
  }

  // ── Community-flag split control ──────────────────────────────────────
  // Each flag is a join group with three segments: [+ Label −]. The +
  // and − buttons are single-click toggles for show-only / hide intent.
  // Two hidden checkboxes per flag (show_X, hide_X) carry the form
  // state. Show and Hide are mutually exclusive; activating one clears
  // the other. Click an active button to clear back to "Any".
  function initFlagSplitControls(form) {
    form.querySelectorAll('[data-flag-group]').forEach(function (group) {
      if (group.dataset.flagSplitBound) return;
      group.dataset.flagSplitBound = 'true';

      var flag = group.dataset.flagGroup;
      var showInput = group.querySelector('input[name="show_' + flag + '"]');
      var hideInput = group.querySelector('input[name="hide_' + flag + '"]');
      var showBtn = group.querySelector('[data-flag-action="show"]');
      var hideBtn = group.querySelector('[data-flag-action="hide"]');
      var label = group.querySelector('[data-flag-label]');
      if (!showInput || !hideInput || !showBtn || !hideBtn || !label) return;

      function submit() {
        var pageInput = form.querySelector('input[name="page"]');
        if (pageInput) pageInput.value = '1';
        updateFilterBadge();
        htmx.trigger(form, 'submit');
      }

      // The active visual is pure CSS (.pp-flag :has(input:checked)); the JS only flips the checkboxes.
      showBtn.addEventListener('click', function (e) {
        e.preventDefault();
        if (showInput.checked) {
          showInput.checked = false;
        } else {
          showInput.checked = true;
          hideInput.checked = false;
        }
        submit();
      });

      hideBtn.addEventListener('click', function (e) {
        e.preventDefault();
        if (hideInput.checked) {
          hideInput.checked = false;
        } else {
          hideInput.checked = true;
          showInput.checked = false;
        }
        submit();
      });
    });
  }

  // ── Dual-range slider logic ──────────────────────────────────────────────
  function initBrowseSliders(form) {
    var ranges = form.querySelectorAll('[data-dual-range]');
    if (!ranges.length) return;

    var submitTimer = null;

    ranges.forEach(function (container) {
      var lo = container.querySelector('[data-dual-range-lo]');
      var hi = container.querySelector('[data-dual-range-hi]');
      var fill = container.querySelector('[data-dual-range-fill]');
      var name = container.dataset.dualRangeName;
      var unit = container.dataset.rangeUnit || '';
      var label = form.querySelector('[data-dual-range-label="' + name + '"]');

      if (!lo || !hi) return;

      function updateFill() {
        var min = parseFloat(lo.min);
        var max = parseFloat(lo.max);
        var loVal = parseFloat(lo.value);
        var hiVal = parseFloat(hi.value);
        var range = max - min;
        if (fill && range > 0) {
          fill.style.left = ((loVal - min) / range * 100) + '%';
          fill.style.right = ((max - hiVal) / range * 100) + '%';
        }
      }

      function updateLabel() {
        if (!label) return;
        var loVal = parseFloat(lo.value);
        var hiVal = parseFloat(hi.value);
        var min = parseFloat(lo.min);
        var max = parseFloat(lo.max);
        if (loVal <= min && hiVal >= max) {
          label.textContent = 'Any';
        } else {
          label.textContent = lo.value + unit + ' \u2013 ' + hi.value + unit;
        }
      }

      function clamp() {
        if (parseFloat(lo.value) > parseFloat(hi.value)) {
          lo.value = hi.value;
        }
      }

      function onInput() {
        clamp();
        updateFill();
        updateLabel();
      }

      function onCommit() {
        clamp();
        updateFill();
        updateLabel();

        var pageInput = form.querySelector('input[name="page"]');
        if (pageInput) pageInput.value = '1';
        clearTimeout(submitTimer);
        submitTimer = setTimeout(function () {
          htmx.trigger(form, 'submit');
        }, 250);
      }

      lo.addEventListener('input', onInput);
      hi.addEventListener('input', onInput);
      lo.addEventListener('change', onCommit);
      hi.addEventListener('change', onCommit);

      // Set initial fill position
      updateFill();
    });
  }

  // ── Active filter badge on drawer summary ──────────────────────────────
  // Keys that don't count as "active filters" (display/pagination state only)
  var IGNORED_KEYS = {'page': 1, 'view': 1, 'category': 1};

  function updateFilterBadge() {
    var form = document.querySelector('[data-browse-form]');
    if (!form) return;

    var summary = form.querySelector('.collapse-title');
    if (!summary) return;

    var badge = summary.querySelector('[data-filter-badge]');
    var hasActive = false;

    var formData = new FormData(form);
    formData.forEach(function (value, key) {
      if (IGNORED_KEYS[key] || !value) return;

      // Dual-range sliders at default (min or max) don't count as active
      var el = form.querySelector('[name="' + key + '"]');
      if (el && el.type === 'range') {
        if (el.dataset.dualRangeLo !== undefined && el.value === el.min) return;
        if (el.dataset.dualRangeHi !== undefined && el.value === el.max) return;
      }

      hasActive = true;
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
  // Dim via the shared `.is-swapping` settle class (CSS: #browse-results.is-swapping { opacity }) rather
  // than a second opacity utility, so the request-time dim matches the change-time settle exactly (one value,
  // one system). pointer-events-none blocks clicks on the stale grid mid-swap.
  document.addEventListener('htmx:beforeRequest', function (e) {
    const results = document.getElementById('browse-results');
    if (results && e.detail.target === results) {
      results.classList.add('is-swapping', 'pointer-events-none');
    }
  });

  document.addEventListener('htmx:afterSwap', function (e) {
    const results = document.getElementById('browse-results');
    if (results) {
      results.classList.remove('is-swapping', 'pointer-events-none');
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

  // ── "I'm Feeling Lucky" button (random game redirect) ──────────────
  function bindLuckyButton() {
    document.querySelectorAll('[data-lucky-btn]').forEach(function (btn) {
      if (btn._luckyBound) return;
      btn._luckyBound = true;
      btn.addEventListener('click', function (e) {
        e.preventDefault();
        var form = document.querySelector('[data-browse-form]');
        if (!form) return;
        var formData = new FormData(form);
        // Strip fields irrelevant to filtering
        formData.delete('page');
        formData.delete('sort');
        formData.delete('view');
        var params = new URLSearchParams(formData);
        // Page-level scope carried by the button (e.g. genre/theme on tag-detail pages).
        // Stored as a data attribute rather than a hidden form input so HTMX filter
        // submits aren't polluted with it.
        var extra = btn.getAttribute('data-lucky-extra');
        if (extra) {
          new URLSearchParams(extra).forEach(function (value, key) {
            params.append(key, value);
          });
        }
        var qs = params.toString();
        window.location.href = '/games/lucky/' + (qs ? '?' + qs : '');
      });
    });
  }

  // ── Visual-only dual-range slider init (for non-HTMX pages) ────────
  // Exposes slider visual behavior (fill, label, clamping) without
  // HTMX auto-submit. Profile detail and trophy case pages call this
  // directly on their forms.
  window.PlatPursuit = window.PlatPursuit || {};
  PlatPursuit.initDualRangeSliders = function (container) {
    var ranges = container.querySelectorAll('[data-dual-range]');
    if (!ranges.length) return;

    ranges.forEach(function (el) {
      var lo = el.querySelector('[data-dual-range-lo]');
      var hi = el.querySelector('[data-dual-range-hi]');
      var fill = el.querySelector('[data-dual-range-fill]');
      var name = el.dataset.dualRangeName;
      var unit = el.dataset.rangeUnit || '';
      var label = container.querySelector('[data-dual-range-label="' + name + '"]');

      if (!lo || !hi) return;

      function updateFill() {
        var min = parseFloat(lo.min);
        var max = parseFloat(lo.max);
        var loVal = parseFloat(lo.value);
        var hiVal = parseFloat(hi.value);
        var range = max - min;
        if (fill && range > 0) {
          fill.style.left = ((loVal - min) / range * 100) + '%';
          fill.style.right = ((max - hiVal) / range * 100) + '%';
        }
      }

      function updateLabel() {
        if (!label) return;
        var loVal = parseFloat(lo.value);
        var hiVal = parseFloat(hi.value);
        var min = parseFloat(lo.min);
        var max = parseFloat(lo.max);
        if (loVal <= min && hiVal >= max) {
          label.textContent = 'Any';
        } else {
          label.textContent = lo.value + unit + ' \u2013 ' + hi.value + unit;
        }
      }

      function clamp() {
        if (parseFloat(lo.value) > parseFloat(hi.value)) {
          lo.value = hi.value;
        }
      }

      function onInput() {
        clamp();
        updateFill();
        updateLabel();
      }

      lo.addEventListener('input', onInput);
      hi.addEventListener('input', onInput);
      lo.addEventListener('change', onInput);
      hi.addEventListener('change', onInput);

      updateFill();
    });
  };

  // ── Initialize on DOMContentLoaded and after HTMX history restore ───
  document.addEventListener('DOMContentLoaded', function () {
    init();
    bindPageJumpForms();
    bindDefaultsButtons();
    bindLuckyButton();
  });

  document.addEventListener('htmx:historyRestore', function () {
    init();
    bindPageJumpForms();
    bindLuckyButton();
  });

  // Re-init when HTMX swaps in new content containing a browse form
  document.addEventListener('htmx:afterSwap', function (e) {
    var target = e.detail.target;
    if (target && target.querySelector && target.querySelector('[data-browse-form]')) {
      init();
      bindPageJumpForms();
      bindLuckyButton();
    }
  });
})();
