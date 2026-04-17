/**
 * Profile Editor: Steam-style showcase customization.
 *
 * Responsibilities:
 * - Add / remove showcases from the profile
 * - Drag-reorder active showcases
 * - Favorite Games picker modal with checkbox selection
 * - Full page reload after structural changes (add/remove) to keep available/active in sync
 */
(function () {
    'use strict';

    const FAVORITE_GAMES_MAX = 6;

    document.addEventListener('DOMContentLoaded', function () {
        initAddButtons();
        initRemoveButtons();
        initDragReorder();
        initFavoriteGamesPicker();
        initBadgePicker();
        initRarestTrophiesOptions();
    });

    // ──────────────────────────────────────────────────────────────
    // Add showcase
    // ──────────────────────────────────────────────────────────────
    function initAddButtons() {
        document.querySelectorAll('.add-showcase-btn').forEach(function (btn) {
            btn.addEventListener('click', async function () {
                const slug = btn.dataset.showcaseSlug;
                if (!slug || btn.disabled) return;
                btn.disabled = true;
                try {
                    await PlatPursuit.API.post('/api/v1/profile/showcases/', {
                        showcase_type: slug,
                    });
                    PlatPursuit.ToastManager.show('Showcase added!', 'success');
                    window.location.reload();
                } catch (error) {
                    btn.disabled = false;
                    let msg = 'Failed to add showcase.';
                    try {
                        const errData = await error.response?.json();
                        if (errData?.error) msg = errData.error;
                    } catch (_) {}
                    PlatPursuit.ToastManager.show(msg, 'error');
                }
            });
        });
    }

    // ──────────────────────────────────────────────────────────────
    // Remove showcase
    // ──────────────────────────────────────────────────────────────
    function initRemoveButtons() {
        document.querySelectorAll('.remove-showcase-btn').forEach(function (btn) {
            btn.addEventListener('click', async function () {
                const slug = btn.dataset.showcaseSlug;
                if (!slug) return;
                if (!confirm('Remove this showcase from your profile?')) return;

                btn.disabled = true;
                try {
                    await PlatPursuit.API.request('/api/v1/profile/showcases/' + slug + '/', {
                        method: 'DELETE',
                    });
                    PlatPursuit.ToastManager.show('Showcase removed.', 'success');
                    window.location.reload();
                } catch (error) {
                    btn.disabled = false;
                    let msg = 'Failed to remove showcase.';
                    try {
                        const errData = await error.response?.json();
                        if (errData?.error) msg = errData.error;
                    } catch (_) {}
                    PlatPursuit.ToastManager.show(msg, 'error');
                }
            });
        });
    }

    // ──────────────────────────────────────────────────────────────
    // Drag reorder
    // ──────────────────────────────────────────────────────────────
    function initDragReorder() {
        const container = document.getElementById('active-showcases-list');
        if (!container) return;
        if (container.querySelectorAll('.active-showcase-row').length < 2) return;

        new PlatPursuit.DragReorderManager({
            container: container,
            itemSelector: '.active-showcase-row',
            handleSelector: '.drag-handle',
            onReorder: async function (_itemId, _newPosition, allItemIds) {
                // allItemIds are the showcase_type slugs in new order
                try {
                    await PlatPursuit.API.post('/api/v1/profile/showcases/reorder/', {
                        showcase_types: allItemIds,
                    });
                    PlatPursuit.ToastManager.show('Order saved.', 'success');
                } catch (error) {
                    let msg = 'Failed to save order.';
                    try {
                        const errData = await error.response?.json();
                        if (errData?.error) msg = errData.error;
                    } catch (_) {}
                    PlatPursuit.ToastManager.show(msg, 'error');
                }
            },
        });
    }

    // ──────────────────────────────────────────────────────────────
    // Favorite Games picker
    // ──────────────────────────────────────────────────────────────
    function initFavoriteGamesPicker() {
        const dataEl = document.getElementById('favorite-games-data');
        const modal = document.getElementById('favorite-games-modal');
        const resultsEl = document.getElementById('favorite-games-results');
        const counterEl = document.getElementById('favorite-games-counter');
        const searchEl = document.getElementById('favorite-games-search');
        const saveBtn = document.getElementById('favorite-games-save');
        const filterPlatEl = document.getElementById('fg-filter-plat');
        const filterCompletedEl = document.getElementById('fg-filter-completed');
        const filterNoShovelwareEl = document.getElementById('fg-filter-no-shovelware');
        const configureBtns = document.querySelectorAll('.configure-btn[data-showcase-slug="favorite_games"]');

        if (!dataEl || !modal || configureBtns.length === 0) return;

        const data = JSON.parse(dataEl.textContent);
        const allGames = data.games || [];
        let selectedIds = new Set(data.selected_ids || []);

        function updateCounter() {
            counterEl.textContent = selectedIds.size + ' / ' + FAVORITE_GAMES_MAX;
            counterEl.classList.toggle('text-primary', selectedIds.size > 0);
        }

        function currentFilter() {
            const q = (searchEl.value || '').trim().toLowerCase();
            const wantPlat = filterPlatEl && filterPlatEl.checked;
            const wantCompleted = filterCompletedEl && filterCompletedEl.checked;
            const hideShovelware = filterNoShovelwareEl && filterNoShovelwareEl.checked;
            return { q, wantPlat, wantCompleted, hideShovelware };
        }

        function renderList() {
            const esc = PlatPursuit.HTMLUtils.escape;
            const { q, wantPlat, wantCompleted, hideShovelware } = currentFilter();

            const filtered = allGames.filter(g => {
                if (q && !g.title_name.toLowerCase().includes(q)) return false;
                if (hideShovelware && g.is_shovelware && !selectedIds.has(g.game_id)) return false;
                if (wantPlat && !g.has_plat) return false;
                if (wantCompleted && g.progress !== 100) return false;
                return true;
            });

            if (filtered.length === 0) {
                resultsEl.innerHTML = '<p class="text-xs text-base-content/40 italic text-center py-8">No games match your filters.</p>';
                return;
            }

            let html = '<div class="grid grid-cols-1 md:grid-cols-2 gap-1.5">';
            for (const g of filtered) {
                const checked = selectedIds.has(g.game_id);
                const disabled = !checked && selectedIds.size >= FAVORITE_GAMES_MAX;
                html += `
                    <label class="flex items-center gap-2 p-2 rounded-lg bg-white/[0.03] border border-base-content/5 cursor-pointer hover:bg-white/[0.06] transition-colors ${disabled ? 'opacity-50 cursor-not-allowed' : ''}">
                        <input type="checkbox" class="checkbox checkbox-sm checkbox-primary shrink-0"
                               data-game-id="${g.game_id}"
                               ${checked ? 'checked' : ''}
                               ${disabled ? 'disabled' : ''} />
                        <div class="w-8 h-8 rounded overflow-hidden ring-1 ring-base-300 shrink-0">
                            ${g.icon_url
                                ? `<img src="${esc(g.icon_url)}" alt="" class="w-full h-full object-cover object-top" loading="lazy" />`
                                : '<div class="w-full h-full bg-base-300"></div>'
                            }
                        </div>
                        <span class="text-xs font-medium line-clamp-1 pr-1 flex-1 min-w-0">${esc(g.title_name)}</span>
                        ${g.is_shovelware
                            ? '<span class="badge badge-xs badge-warning shrink-0" title="Shovelware">SW</span>'
                            : ''
                        }
                        ${g.has_plat
                            ? '<span class="badge badge-xs badge-ghost shrink-0">Plat</span>'
                            : (g.progress === 100
                                ? '<span class="badge badge-xs badge-ghost shrink-0">100%</span>'
                                : '')
                        }
                    </label>
                `;
            }
            html += '</div>';
            resultsEl.innerHTML = html;

            resultsEl.querySelectorAll('input[type="checkbox"][data-game-id]').forEach(function (cb) {
                cb.addEventListener('change', function () {
                    const gid = parseInt(cb.dataset.gameId, 10);
                    if (cb.checked) selectedIds.add(gid);
                    else selectedIds.delete(gid);
                    updateCounter();
                    // Re-render to update disabled state when at max
                    renderList();
                });
            });
        }

        configureBtns.forEach(function (btn) {
            btn.addEventListener('click', function () {
                renderList();
                updateCounter();
                modal.showModal();
            });
        });

        searchEl.addEventListener('input', PlatPursuit.debounce(renderList, 150));
        [filterPlatEl, filterCompletedEl, filterNoShovelwareEl].forEach(function (el) {
            if (el) el.addEventListener('change', renderList);
        });

        saveBtn.addEventListener('click', async function () {
            saveBtn.disabled = true;
            const originalText = saveBtn.textContent;
            saveBtn.textContent = 'Saving...';
            try {
                await PlatPursuit.API.post(
                    '/api/v1/profile/showcases/favorite_games/config/',
                    { config: { game_ids: Array.from(selectedIds) } }
                );
                PlatPursuit.ToastManager.show('Favorite games saved!', 'success');
                modal.close();
                // Update stored data so reopening shows current selection
                data.selected_ids = Array.from(selectedIds);
            } catch (error) {
                let msg = 'Failed to save.';
                try {
                    const errData = await error.response?.json();
                    if (errData?.error) msg = errData.error;
                } catch (_) {}
                PlatPursuit.ToastManager.show(msg, 'error');
            } finally {
                saveBtn.disabled = false;
                saveBtn.textContent = originalText;
            }
        });
    }

    // ──────────────────────────────────────────────────────────────
    // Badge picker
    // Uses the existing ToggleShowcaseBadgeView API (per-click toggle).
    // ──────────────────────────────────────────────────────────────
    function initBadgePicker() {
        const BADGES_MAX = 5;
        const dataEl = document.getElementById('badge-showcase-data');
        const modal = document.getElementById('badge-picker-modal');
        const resultsEl = document.getElementById('badge-picker-results');
        const counterEl = document.getElementById('badge-picker-counter');
        const searchEl = document.getElementById('badge-picker-search');
        const configureBtns = document.querySelectorAll('.configure-btn[data-showcase-slug="badge_showcase"]');

        if (!dataEl || !modal || configureBtns.length === 0) return;

        const data = JSON.parse(dataEl.textContent);
        const allBadges = data.badges || [];
        let selectedIds = new Set(data.selected_ids || []);

        function updateCounter() {
            counterEl.textContent = selectedIds.size + ' / ' + BADGES_MAX;
            counterEl.classList.toggle('text-primary', selectedIds.size > 0);
        }

        function tierColor(tier) {
            if (tier === 4) return 'badge-primary';
            if (tier === 3) return 'badge-warning';
            if (tier === 2) return 'badge-ghost';
            return 'text-amber-700 bg-amber-700/10 border-amber-700/30';
        }

        function renderList(filter) {
            const esc = PlatPursuit.HTMLUtils.escape;
            const q = (filter || '').trim().toLowerCase();
            const filtered = q
                ? allBadges.filter(b => b.name.toLowerCase().includes(q))
                : allBadges;

            if (filtered.length === 0) {
                resultsEl.innerHTML = '<p class="text-xs text-base-content/40 italic text-center py-8">No badges match your search.</p>';
                return;
            }

            let html = '<div class="grid grid-cols-2 md:grid-cols-3 gap-1.5">';
            for (const b of filtered) {
                const checked = selectedIds.has(b.badge_id);
                const disabled = !checked && selectedIds.size >= BADGES_MAX;
                html += `
                    <label class="flex items-center gap-2 p-2 rounded-lg bg-white/[0.03] border border-base-content/5 cursor-pointer hover:bg-white/[0.06] transition-colors ${disabled ? 'opacity-50 cursor-not-allowed' : ''}">
                        <input type="checkbox" class="checkbox checkbox-sm checkbox-primary shrink-0"
                               data-badge-id="${b.badge_id}"
                               ${checked ? 'checked' : ''}
                               ${disabled ? 'disabled' : ''} />
                        <div class="w-10 h-10 rounded overflow-hidden shrink-0 bg-base-300/40 flex items-center justify-center">
                            ${b.icon_url
                                ? `<img src="${esc(b.icon_url)}" alt="" class="w-full h-full object-contain" loading="lazy" />`
                                : '<div class="w-full h-full bg-base-300"></div>'
                            }
                        </div>
                        <div class="flex-1 min-w-0">
                            <p class="text-xs font-medium line-clamp-1 pr-1">${esc(b.name)}</p>
                            <span class="badge badge-xs ${tierColor(b.tier)}">${esc(b.tier_name)}</span>
                        </div>
                    </label>
                `;
            }
            html += '</div>';
            resultsEl.innerHTML = html;

            resultsEl.querySelectorAll('input[type="checkbox"][data-badge-id]').forEach(function (cb) {
                cb.addEventListener('change', async function () {
                    const bid = parseInt(cb.dataset.badgeId, 10);
                    cb.disabled = true;
                    try {
                        const resp = await PlatPursuit.API.post('/api/v1/badges/showcase/', {
                            badge_id: bid,
                        });
                        if (resp.action === 'added') selectedIds.add(bid);
                        else selectedIds.delete(bid);
                        updateCounter();
                        renderList(searchEl.value);
                    } catch (error) {
                        cb.checked = !cb.checked; // revert
                        let msg = 'Failed to toggle badge.';
                        try {
                            const errData = await error.response?.json();
                            if (errData?.error) msg = errData.error;
                        } catch (_) {}
                        PlatPursuit.ToastManager.show(msg, 'error');
                    } finally {
                        cb.disabled = false;
                    }
                });
            });
        }

        configureBtns.forEach(function (btn) {
            btn.addEventListener('click', function () {
                renderList('');
                updateCounter();
                modal.showModal();
            });
        });

        searchEl.addEventListener('input', PlatPursuit.debounce(function () {
            renderList(searchEl.value);
        }, 150));
    }

    // ──────────────────────────────────────────────────────────────
    // Rarest Trophies options (simple boolean toggle)
    // ──────────────────────────────────────────────────────────────
    function initRarestTrophiesOptions() {
        const modal = document.getElementById('rarest-trophies-modal');
        const toggle = document.getElementById('rarest-one-per-game');
        const saveBtn = document.getElementById('rarest-trophies-save');
        const configureBtns = document.querySelectorAll('.configure-btn[data-showcase-slug="rarest_trophies"]');

        if (!modal || !toggle || !saveBtn || configureBtns.length === 0) return;

        configureBtns.forEach(function (btn) {
            btn.addEventListener('click', function () {
                // Read current value from button's data attr
                toggle.checked = btn.dataset.onePerGame !== 'false';
                modal.showModal();
            });
        });

        saveBtn.addEventListener('click', async function () {
            saveBtn.disabled = true;
            const originalText = saveBtn.textContent;
            saveBtn.textContent = 'Saving...';
            try {
                await PlatPursuit.API.post(
                    '/api/v1/profile/showcases/rarest_trophies/config/',
                    { config: { one_per_game: toggle.checked } }
                );
                PlatPursuit.ToastManager.show('Settings saved!', 'success');
                // Update all matching buttons' data attr so re-opening reflects the new value
                configureBtns.forEach(function (btn) {
                    btn.dataset.onePerGame = toggle.checked ? 'true' : 'false';
                });
                modal.close();
            } catch (error) {
                let msg = 'Failed to save.';
                try {
                    const errData = await error.response?.json();
                    if (errData?.error) msg = errData.error;
                } catch (_) {}
                PlatPursuit.ToastManager.show(msg, 'error');
            } finally {
                saveBtn.disabled = false;
                saveBtn.textContent = originalText;
            }
        });
    }
})();
