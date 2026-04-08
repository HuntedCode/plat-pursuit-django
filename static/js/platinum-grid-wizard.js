/**
 * Platinum Grid Wizard
 *
 * Manages the 3-step wizard for building a shareable platinum grid image:
 * 1. Configure (icon type, sort, filters)
 * 2. Select (checklist of platinums)
 * 3. Preview & Download (layout, theme, generate)
 *
 * Reads initial data from window.PLAT_GRID_CONFIG.
 */
(function () {
    'use strict';

    const CONFIG = window.PLAT_GRID_CONFIG;
    if (!CONFIG) return;

    // ── State ──────────────────────────────────────────
    const state = {
        iconType: 'game',
        sort: 'recent',
        filterShovelware: false,
        filterHidden: false,
        selected: new Set(),
        theme: 'default',
        themeName: 'Default',
        themeBg: '',
        cols: 'auto',
        previewGenerated: false,
    };

    // ── DOM refs ───────────────────────────────────────
    const $ = (sel) => document.querySelector(sel);
    const $$ = (sel) => document.querySelectorAll(sel);

    const steps = [null, $('#step-1'), $('#step-2'), $('#step-3')];
    const indicators = [null, $('#step-indicator-1'), $('#step-indicator-2'), $('#step-indicator-3')];
    const checklistContainer = $('#checklist-container');
    const selectionCounter = $('#selection-counter');
    const searchInput = $('#checklist-search');

    // ── Step navigation ────────────────────────────────
    let currentStep = 1;

    function goToStep(n) {
        steps[currentStep].classList.add('hidden');
        steps[n].classList.remove('hidden');

        for (let i = 1; i <= 3; i++) {
            indicators[i].classList.toggle('step-primary', i <= n);
        }

        currentStep = n;

        if (n === 2) {
            buildChecklist();
        }
        if (n === 3) {
            state.previewGenerated = false;
            $('#btn-download-png').disabled = true;
            $('#preview-container').innerHTML = '<p class="text-base-content/40 text-sm italic text-center pt-20">Click "Generate Preview" to see your grid.</p>';
            $('#preview-container').style.height = '';
        }

        window.scrollTo({ top: 0, behavior: 'smooth' });
    }

    // ── Config buttons (Step 1) ────────────────────────
    $$('.config-btn').forEach(btn => {
        btn.addEventListener('click', () => {
            const key = btn.dataset.config;
            const val = btn.dataset.value;

            // Update state
            if (key === 'icon_type') state.iconType = val;
            if (key === 'sort') state.sort = val;

            // Update button visuals
            $$(`[data-config="${key}"]`).forEach(b => {
                const isActive = b.dataset.value === val;
                b.classList.toggle('btn-primary', isActive);
                b.classList.toggle('btn-ghost', !isActive);
                b.classList.toggle('border', !isActive);
                b.classList.toggle('border-base-300', !isActive);
            });
        });
    });

    // Filter checkboxes
    $('#filter-shovelware')?.addEventListener('change', (e) => {
        state.filterShovelware = e.target.checked;
    });
    $('#filter-hidden')?.addEventListener('change', (e) => {
        state.filterHidden = e.target.checked;
    });

    // ── Checklist (Step 2) ─────────────────────────────
    function getFilteredPlatinums() {
        let items = [...CONFIG.platinums];

        if (state.filterShovelware) {
            items = items.filter(p => !p.is_shovelware);
        }
        if (state.filterHidden) {
            items = items.filter(p => !p.is_hidden);
        }

        // Sort
        if (state.sort === 'rarest') {
            items.sort((a, b) => a.psn_earn_rate - b.psn_earn_rate);
        }
        // 'recent' is the default order from the backend

        return items;
    }

    function buildChecklist() {
        const items = getFilteredPlatinums();
        const html = [];
        const visibleIds = new Set(items.map(p => p.id));

        // Prune selections for items no longer visible (due to filter changes)
        for (const id of state.selected) {
            if (!visibleIds.has(id)) state.selected.delete(id);
        }

        // Pre-select up to maxIcons if selection is empty
        if (state.selected.size === 0) {
            items.slice(0, CONFIG.maxIcons).forEach(p => state.selected.add(p.id));
        }

        for (const p of items) {
            const checked = state.selected.has(p.id) ? 'checked' : '';
            const earnDate = p.earned_date ? new Date(p.earned_date).toLocaleDateString() : '';
            const earnRate = p.psn_earn_rate ? p.psn_earn_rate.toFixed(1) + '%' : '';
            const iconUrl = state.iconType === 'trophy' ? p.trophy_icon : p.game_image;
            const escapedName = PlatPursuit.HTMLUtils.escape(p.game_name);
            const tags = [];
            if (p.is_shovelware) tags.push('<span class="badge badge-xs badge-warning">Shovelware</span>');
            if (p.is_hidden) tags.push('<span class="badge badge-xs badge-ghost">Hidden</span>');

            html.push(`
                <label class="checklist-row flex items-center gap-2 md:gap-3 p-2 rounded-lg cursor-pointer
                              hover:bg-base-300/50 transition-colors border border-transparent
                              ${checked ? 'bg-base-300/30 border-primary/20' : ''}"
                       data-id="${p.id}" data-name="${escapedName.toLowerCase()}">
                    <input type="checkbox" class="checkbox checkbox-sm checkbox-primary shrink-0" ${checked}
                           data-plat-id="${p.id}" />
                    <div class="w-10 h-10 md:w-12 md:h-12 rounded overflow-hidden shrink-0 border border-base-content/10">
                        ${iconUrl
                            ? `<img src="${PlatPursuit.HTMLUtils.escape(iconUrl)}" class="w-full h-full object-cover" loading="lazy" />`
                            : '<div class="w-full h-full bg-base-300 flex items-center justify-center text-base-content/30 text-xs">?</div>'
                        }
                    </div>
                    <div class="min-w-0 flex-1">
                        <p class="text-sm font-semibold line-clamp-1 pr-1">${escapedName}</p>
                        <div class="flex items-center gap-2 text-xs text-base-content/50">
                            ${earnDate ? `<span>${earnDate}</span>` : ''}
                            ${earnRate ? `<span class="text-base-content/30">&middot;</span><span>PSN: ${earnRate}</span>` : ''}
                            ${tags.length ? `<span class="text-base-content/30">&middot;</span>${tags.join(' ')}` : ''}
                        </div>
                    </div>
                </label>
            `);
        }

        checklistContainer.innerHTML = html.join('');
        updateCounter();

        // Checkbox change handlers
        checklistContainer.querySelectorAll('input[type="checkbox"]').forEach(cb => {
            cb.addEventListener('change', (e) => {
                const id = parseInt(e.target.dataset.platId);
                const row = e.target.closest('.checklist-row');

                if (e.target.checked) {
                    if (state.selected.size >= CONFIG.maxIcons) {
                        e.target.checked = false;
                        PlatPursuit.ToastManager?.show(`Maximum of ${CONFIG.maxIcons} selections.`, 'warning');
                        return;
                    }
                    state.selected.add(id);
                    row.classList.add('bg-base-300/30', 'border-primary/20');
                } else {
                    state.selected.delete(id);
                    row.classList.remove('bg-base-300/30', 'border-primary/20');
                }
                updateCounter();
            });
        });
    }

    function updateCounter() {
        if (selectionCounter) {
            selectionCounter.textContent = `${state.selected.size}/${CONFIG.maxIcons}`;
        }
        // Disable next button if nothing selected
        const nextBtn = $('#btn-to-step-3');
        if (nextBtn) {
            nextBtn.disabled = state.selected.size === 0;
        }
    }

    // Search within checklist
    searchInput?.addEventListener('input', PlatPursuit.debounce(() => {
        const query = searchInput.value.toLowerCase().trim();
        checklistContainer.querySelectorAll('.checklist-row').forEach(row => {
            const name = row.dataset.name || '';
            row.classList.toggle('hidden', query && !name.includes(query));
        });
    }, 200));

    // Select All / Deselect All
    $('#btn-select-all')?.addEventListener('click', () => {
        const visible = checklistContainer.querySelectorAll('.checklist-row:not(.hidden) input[type="checkbox"]');
        visible.forEach(cb => {
            const id = parseInt(cb.dataset.platId);
            if (!cb.checked && state.selected.size < CONFIG.maxIcons) {
                cb.checked = true;
                state.selected.add(id);
                cb.closest('.checklist-row').classList.add('bg-base-300/30', 'border-primary/20');
            }
        });
        updateCounter();
    });

    $('#btn-deselect-all')?.addEventListener('click', () => {
        checklistContainer.querySelectorAll('input[type="checkbox"]').forEach(cb => {
            cb.checked = false;
            const id = parseInt(cb.dataset.platId);
            state.selected.delete(id);
            cb.closest('.checklist-row').classList.remove('bg-base-300/30', 'border-primary/20');
        });
        updateCounter();
    });

    // Remove cross-gen duplicates: for each GameFamily, keep the newest platform version
    const PLATFORM_PRIORITY = { 'PS5': 5, 'PSVR2': 4.5, 'PS4': 4, 'PSVR': 3.5, 'PS3': 3, 'PS Vita': 2, 'PSP': 1 };

    function bestPlatformScore(platforms) {
        if (!platforms || !platforms.length) return 0;
        return Math.max(...platforms.map(p => PLATFORM_PRIORITY[p] || 0));
    }

    $('#btn-remove-dupes')?.addEventListener('click', () => {
        const items = getFilteredPlatinums();
        // Group selected items by family_id
        const families = {};
        for (const p of items) {
            if (!state.selected.has(p.id) || !p.family_id) continue;
            if (!families[p.family_id]) families[p.family_id] = [];
            families[p.family_id].push(p);
        }

        let removed = 0;
        for (const [familyId, members] of Object.entries(families)) {
            if (members.length <= 1) continue;
            // Sort by platform priority descending, then by earned date descending (newest first)
            members.sort((a, b) => {
                const platDiff = bestPlatformScore(b.platforms) - bestPlatformScore(a.platforms);
                if (platDiff !== 0) return platDiff;
                return (b.earned_date || '').localeCompare(a.earned_date || '');
            });
            // Keep the first (best), uncheck the rest
            for (let i = 1; i < members.length; i++) {
                const dupeId = members[i].id;
                if (state.selected.has(dupeId)) {
                    state.selected.delete(dupeId);
                    const cb = checklistContainer.querySelector(`input[data-plat-id="${dupeId}"]`);
                    if (cb) {
                        cb.checked = false;
                        cb.closest('.checklist-row')?.classList.remove('bg-base-300/30', 'border-primary/20');
                    }
                    removed++;
                }
            }
        }

        updateCounter();
        if (removed > 0) {
            PlatPursuit.ToastManager?.show(`Removed ${removed} cross-gen duplicate${removed !== 1 ? 's' : ''}.`, 'success');
        } else {
            PlatPursuit.ToastManager?.show('No duplicates found in selection.', 'info');
        }
    });

    // ── Preview & Download (Step 3) ────────────────────
    function getSelectedIdsString() {
        // Preserve the filtered/sorted order
        const filtered = getFilteredPlatinums();
        const ordered = filtered.filter(p => state.selected.has(p.id));
        return ordered.map(p => p.id).join(',');
    }

    function buildQueryParams() {
        const params = new URLSearchParams();
        params.set('icon_ids', getSelectedIdsString());
        params.set('icon_type', state.iconType);
        params.set('theme', state.theme);
        const cols = state.cols;
        if (cols !== 'auto') params.set('cols', cols);
        return params;
    }

    // Scale preview to fit container width (called on generate + resize)
    function scalePreview() {
        const wrapper = $('#preview-wrapper');
        const container = $('#preview-container');
        if (!wrapper || !container || !state.previewWidth) return;

        const availableWidth = container.clientWidth - 16;
        const scale = Math.min(1, availableWidth / state.previewWidth);
        wrapper.style.transform = `scale(${scale})`;
        // Center horizontally via margin (flex centering breaks with transform-origin)
        const scaledWidth = state.previewWidth * scale;
        const leftMargin = Math.max(0, (availableWidth - scaledWidth) / 2);
        wrapper.style.marginLeft = `${leftMargin}px`;
        container.style.height = `${Math.ceil(state.previewHeight * scale) + 16}px`;
    }

    window.addEventListener('resize', PlatPursuit.debounce(scalePreview, 150));

    // Generate Preview
    async function generatePreview() {
        if (state.selected.size === 0) {
            PlatPursuit.ToastManager?.show('No platinums selected.', 'warning');
            return;
        }

        const btn = $('#btn-refresh-preview');
        const container = $('#preview-container');
        btn.disabled = true;
        btn.innerHTML = '<span class="loading loading-spinner loading-sm"></span> Generating...';
        container.innerHTML = '<div class="flex flex-col items-center gap-2 py-8"><span class="loading loading-spinner loading-lg text-primary"></span><span class="text-sm text-base-content/50">Building preview...</span></div>';

        try {
            const params = buildQueryParams();
            const data = await PlatPursuit.API.get(`${CONFIG.htmlUrl}?${params.toString()}`);

            // Apply theme background to the preview wrapper so it matches the final PNG
            const themeBg = state.themeBg || 'linear-gradient(to bottom right, #2a2e34, #32363d, #2a2e34)';

            container.innerHTML = `
                <div id="preview-wrapper" style="
                    width: ${data.width}px;
                    height: ${data.height}px;
                    background: ${themeBg};
                    border-radius: 8px;
                    box-shadow: 0 4px 24px rgba(0,0,0,0.4);
                    overflow: hidden;
                    transform-origin: top left;
                ">
                    ${data.html}
                </div>
            `;

            // Store dimensions for resize handler
            state.previewWidth = data.width;
            state.previewHeight = data.height;
            scalePreview();

            state.previewGenerated = true;
            $('#btn-download-png').disabled = false;
        } catch (err) {
            const errData = await err.response?.json().catch(() => null);
            const msg = errData?.error || 'Failed to generate preview.';
            container.innerHTML = `<p class="text-error text-sm">${PlatPursuit.HTMLUtils.escape(msg)}</p>`;
            PlatPursuit.ToastManager?.show(msg, 'error');
        } finally {
            btn.disabled = false;
            btn.innerHTML = '<svg xmlns="http://www.w3.org/2000/svg" class="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2"><polyline points="23 4 23 10 17 10"/><path d="M20.49 15a9 9 0 1 1-2.12-9.36L23 10"/></svg> Generate Preview';
        }
    }

    // Theme nudge: prompt user if they haven't picked a theme yet
    const themeNudgeModal = $('#theme-nudge-modal');

    $('#btn-refresh-preview')?.addEventListener('click', () => generatePreview());

    // Download PNG
    async function downloadPng() {
        if (state.selected.size === 0) return;

        const btn = $('#btn-download-png');
        btn.disabled = true;
        btn.innerHTML = '<span class="loading loading-spinner loading-sm"></span> Rendering...';

        try {
            const params = buildQueryParams();
            const response = await fetch(`${CONFIG.pngUrl}?${params.toString()}`, {
                credentials: 'same-origin',
                headers: { 'X-CSRFToken': PlatPursuit.CSRFToken.get() },
            });

            if (!response.ok) {
                const errData = await response.json().catch(() => null);
                throw new Error(errData?.error || 'Download failed.');
            }

            const blob = await response.blob();
            const url = URL.createObjectURL(blob);
            const a = document.createElement('a');
            a.href = url;
            a.download = response.headers.get('Content-Disposition')?.match(/filename="(.+)"/)?.[1] || 'platinum_grid.png';
            document.body.appendChild(a);
            a.click();
            a.remove();
            URL.revokeObjectURL(url);

            PlatPursuit.ToastManager?.show('Image downloaded!', 'success');
        } catch (err) {
            PlatPursuit.ToastManager?.show(err.message || 'Failed to download.', 'error');
        } finally {
            btn.disabled = false;
            btn.innerHTML = '<svg xmlns="http://www.w3.org/2000/svg" class="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="7 10 12 15 17 10"/><line x1="12" y1="15" x2="12" y2="3"/></svg> Download PNG';
        }
    }

    $('#btn-download-png')?.addEventListener('click', () => {
        if (state.theme === 'default') {
            themeNudgeModal?.showModal();
            return;
        }
        downloadPng();
    });

    // Nudge modal: "Browse Themes" opens the theme picker
    $('#nudge-browse-themes')?.addEventListener('click', () => {
        themeNudgeModal?.close();
        themeModal?.showModal();
    });

    // Nudge modal: "Continue Anyway" proceeds with default download
    $('#nudge-continue')?.addEventListener('click', () => {
        themeNudgeModal?.close();
        downloadPng();
    });

    // Column selector
    $('#grid-cols')?.addEventListener('change', (e) => {
        state.cols = e.target.value;
    });

    // ── Theme Picker ───────────────────────────────────
    const themeModal = $('#theme-picker-modal');

    // Initialize default theme background from the pre-selected button
    const defaultThemeBtn = themeModal?.querySelector('.theme-preview-btn.border-primary') ||
                            themeModal?.querySelector('.theme-preview-btn[data-theme-key="default"]');
    if (defaultThemeBtn) {
        state.themeBg = defaultThemeBtn.style.background || '';
    }

    $('#btn-open-theme-picker')?.addEventListener('click', () => {
        themeModal?.showModal();
    });

    // Listen for theme button clicks inside the modal
    themeModal?.addEventListener('click', (e) => {
        const btn = e.target.closest('.theme-preview-btn');
        if (!btn) return;

        const key = btn.dataset.themeKey;
        const name = btn.dataset.themeName;
        if (!key) return;

        state.theme = key;
        state.themeName = name || key;
        state.themeBg = btn.style.background || '';
        $('#selected-theme-label').textContent = state.themeName;

        // Update selected visual
        themeModal.querySelectorAll('.theme-preview-btn').forEach(b => {
            b.classList.remove('border-primary', 'ring-2', 'ring-primary');
        });
        btn.classList.add('border-primary', 'ring-2', 'ring-primary');

        // Live-update preview background if a preview exists
        const wrapper = $('#preview-wrapper');
        if (wrapper) {
            wrapper.style.background = state.themeBg || 'linear-gradient(to bottom right, #2a2e34, #32363d, #2a2e34)';
        }
    });

    // ── Step Navigation Wiring ─────────────────────────
    // Use class selectors to support both top and bottom nav buttons
    $('#btn-to-step-2')?.addEventListener('click', () => goToStep(2));

    function goToStep3() {
        if (state.selected.size === 0) {
            PlatPursuit.ToastManager?.show('Select at least one platinum.', 'warning');
            return;
        }
        goToStep(3);
    }
    $('#btn-to-step-3')?.addEventListener('click', goToStep3);
    $$('.btn-to-step-3').forEach(btn => btn.addEventListener('click', goToStep3));
    $$('.btn-back-to-step-1').forEach(btn => btn.addEventListener('click', () => goToStep(1)));
    $$('.btn-back-to-step-2').forEach(btn => btn.addEventListener('click', () => goToStep(2)));
    $('#btn-back-to-step-1')?.addEventListener('click', () => goToStep(1));
    $('#btn-back-to-step-2')?.addEventListener('click', () => goToStep(2));

})();
