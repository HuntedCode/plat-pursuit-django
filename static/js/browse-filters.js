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

    // ── Range sliders (ratings + time-to-beat) ────────────────────────────
    initBrowseSliders(form);

    // ── Badge picker modal ──────────────────────────────────────────────
    initBadgePicker(form);
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

  // ── Badge picker modal logic ────────────────────────────────────────────
  function initBadgePicker(form) {
    var modal = document.getElementById('browse-badge-picker');
    if (!modal) return;

    var searchInput = modal.querySelector('#badge-picker-search');
    var grid = modal.querySelector('#badge-picker-grid');
    var emptyState = modal.querySelector('#badge-picker-empty');
    var countLabel = modal.querySelector('#badge-picker-count');
    var sortSelect = modal.querySelector('#badge-picker-sort');
    var typeChips = modal.querySelectorAll('.badge-type-chip');
    var items = grid.querySelectorAll('.badge-pick-item');

    var activeType = '';
    var debounceTimer = null;

    // Trigger button opens modal
    var triggerBtn = document.getElementById('badge-picker-trigger');
    if (triggerBtn) {
      triggerBtn.addEventListener('click', function (e) {
        e.preventDefault();
        modal.showModal();
      });
    }

    // Search: debounced filtering
    if (searchInput) {
      searchInput.addEventListener('input', function () {
        clearTimeout(debounceTimer);
        debounceTimer = setTimeout(filterBadgeGrid, 150);
      });
    }

    // Type chip selection
    typeChips.forEach(function (chip) {
      chip.addEventListener('click', function () {
        activeType = chip.dataset.badgeType;
        typeChips.forEach(function (c) {
          if (c.dataset.badgeType === activeType) {
            c.className = 'badge-type-chip btn btn-xs btn-secondary rounded-full';
          } else {
            c.className = 'badge-type-chip btn btn-xs btn-ghost rounded-full border border-base-300';
          }
        });
        filterBadgeGrid();
      });
    });

    // Sort
    if (sortSelect) {
      sortSelect.addEventListener('change', function () {
        sortBadgeGrid();
      });
    }

    // Badge card selection (event delegation on grid)
    grid.addEventListener('click', function (e) {
      var card = e.target.closest('.badge-pick-item');
      if (!card) return;

      var slug = card.dataset.seriesSlug;
      var name = card.dataset.badgeName;

      // Update hidden input
      var hiddenInput = form.querySelector('#badge-series-input');
      if (hiddenInput) hiddenInput.value = slug;

      // Update trigger button
      var trigger = document.getElementById('badge-picker-trigger');
      var label = document.getElementById('badge-picker-label');
      if (trigger) {
        trigger.classList.remove('btn-ghost', 'border', 'border-dashed', 'border-base-300');
        trigger.classList.add('btn-secondary', 'font-bold');
      }
      if (label) label.textContent = name;

      // Ensure clear button exists (create if needed after HTMX restore)
      ensureClearButton();

      // Close modal
      modal.close();

      // Submit form with page reset
      var pageInput = form.querySelector('input[name="page"]');
      if (pageInput) pageInput.value = '1';
      htmx.trigger(form, 'submit');
    });

    // Clear button
    bindClearButton(form);

    // Reset modal state when closed (so it's fresh on next open)
    modal.addEventListener('close', function () {
      if (searchInput) searchInput.value = '';
      activeType = '';
      typeChips.forEach(function (c) {
        if (c.dataset.badgeType === '') {
          c.className = 'badge-type-chip btn btn-xs btn-secondary rounded-full';
        } else {
          c.className = 'badge-type-chip btn btn-xs btn-ghost rounded-full border border-base-300';
        }
      });
      filterBadgeGrid();
      if (sortSelect) sortSelect.value = 'alpha';
      sortBadgeGrid();
    });

    function filterBadgeGrid() {
      var query = searchInput ? searchInput.value.toLowerCase().trim() : '';
      var visibleCount = 0;

      items.forEach(function (item) {
        var nameMatch = !query || (item.dataset.badgeName || '').toLowerCase().indexOf(query) !== -1;
        var typeMatch = !activeType || item.dataset.badgeType === activeType;
        var visible = nameMatch && typeMatch;

        item.style.display = visible ? '' : 'none';
        if (visible) visibleCount++;
      });

      if (emptyState) {
        emptyState.classList.toggle('hidden', visibleCount > 0);
      }
      if (grid) {
        grid.classList.toggle('hidden', visibleCount === 0);
      }
      if (countLabel) {
        countLabel.textContent = visibleCount + ' badge' + (visibleCount !== 1 ? 's' : '');
      }
    }

    function sortBadgeGrid() {
      var sortVal = sortSelect ? sortSelect.value : 'alpha';
      var arr = Array.from(items);

      arr.sort(function (a, b) {
        if (sortVal === 'earned') {
          return (parseInt(b.dataset.badgeEarned, 10) || 0) - (parseInt(a.dataset.badgeEarned, 10) || 0);
        }
        if (sortVal === 'stages') {
          return (parseInt(b.dataset.badgeStages, 10) || 0) - (parseInt(a.dataset.badgeStages, 10) || 0);
        }
        // alpha (default)
        return a.dataset.badgeName.localeCompare(b.dataset.badgeName);
      });

      var fragment = document.createDocumentFragment();
      arr.forEach(function (item) {
        fragment.appendChild(item);
      });
      grid.appendChild(fragment);
    }

    function ensureClearButton() {
      if (document.getElementById('badge-picker-clear')) return;
      var trigger = document.getElementById('badge-picker-trigger');
      if (!trigger) return;

      var clearBtn = document.createElement('button');
      clearBtn.type = 'button';
      clearBtn.id = 'badge-picker-clear';
      clearBtn.className = 'btn btn-xs btn-ghost border border-base-300 text-error/70 hover:text-error hover:border-error/30 transition-all';
      clearBtn.title = 'Clear badge filter';
      clearBtn.innerHTML = '<svg xmlns="http://www.w3.org/2000/svg" class="w-3 h-3" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><line x1="18" y1="6" x2="6" y2="18"></line><line x1="6" y1="6" x2="18" y2="18"></line></svg>';
      trigger.parentNode.insertBefore(clearBtn, trigger.nextSibling);
      bindClearButton(form);
    }

    function bindClearButton(browseForm) {
      var clearBtn = document.getElementById('badge-picker-clear');
      if (!clearBtn || clearBtn.dataset.bound) return;
      clearBtn.dataset.bound = 'true';

      clearBtn.addEventListener('click', function (e) {
        e.preventDefault();

        // Clear hidden input
        var hiddenInput = browseForm.querySelector('#badge-series-input');
        if (hiddenInput) hiddenInput.value = '';

        // Reset trigger button
        var trigger = document.getElementById('badge-picker-trigger');
        var label = document.getElementById('badge-picker-label');
        if (trigger) {
          trigger.classList.remove('btn-secondary', 'font-bold');
          trigger.classList.add('btn-ghost', 'border', 'border-base-300', 'border-dashed');
        }
        if (label) label.textContent = 'Pick a Badge';

        // Remove clear button
        clearBtn.remove();

        // Submit form
        var pageInput = browseForm.querySelector('input[name="page"]');
        if (pageInput) pageInput.value = '1';
        htmx.trigger(browseForm, 'submit');
      });
    }
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
