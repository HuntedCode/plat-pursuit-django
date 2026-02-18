/**
 * Platinum Calendar Challenge JavaScript
 *
 * Handles:
 * - CalendarChallengeDetail: Detail page with interactive calendar, day modals,
 *   inline rename, delete, and share link.
 *
 * Uses: PlatPursuit.API, PlatPursuit.ToastManager, PlatPursuit.HTMLUtils
 */

const CalendarChallengeDetail = (() => {
    let challengeId = null;

    // ─── Holiday Data ────────────────────────────────────────────────────
    // Served from Python (holiday_service.py) via template <script> block.
    // Dynamically computed for the current year (floating holidays, Ramadan, etc.).
    const INTL_HOLIDAYS = window.__CALENDAR_HOLIDAYS?.intl || {};
    const US_HOLIDAYS = window.__CALENDAR_HOLIDAYS?.us || {};

    function init(id) {
        challengeId = id;
        _bindDayCells();
        _bindDeleteButton();
        _bindInlineRename();
        _bindShareUrl();
        _bindShareImageButton();
        _bindHolidayToggle();
        _applyHolidayHighlights();
    }

    // ─── Day Cell Click → Modal ────────────────────────────────────────────

    let _dayModalAbort = null;

    function _bindDayCells() {
        document.querySelectorAll('.cal-day-filled').forEach(cell => {
            cell.addEventListener('click', () => {
                const month = parseInt(cell.dataset.month, 10);
                const day = parseInt(cell.dataset.day, 10);
                if (month && day) _openDayModal(month, day);
            });
        });
    }

    async function _openDayModal(month, day) {
        const modal = document.getElementById('day-detail-modal');
        const titleEl = document.getElementById('day-modal-title');
        const contentEl = document.getElementById('day-modal-content');

        if (!modal || !titleEl || !contentEl) return;

        // Abort any in-flight day detail request
        if (_dayModalAbort) _dayModalAbort.abort();
        _dayModalAbort = new AbortController();

        // Show loading state
        const monthNames = [
            '', 'January', 'February', 'March', 'April', 'May', 'June',
            'July', 'August', 'September', 'October', 'November', 'December'
        ];
        titleEl.textContent = `${monthNames[month]} ${day}`;
        contentEl.innerHTML = '<div class="flex justify-center py-4"><span class="loading loading-spinner loading-md"></span></div>';
        modal.showModal();

        try {
            const data = await PlatPursuit.API.get(
                `/api/v1/challenges/calendar/${challengeId}/day/${month}/${day}/`,
                { signal: _dayModalAbort.signal }
            );

            if (!data.platinums || data.platinums.length === 0) {
                contentEl.innerHTML = '<p class="text-base-content/50 text-center py-4">No platinums found for this day.</p>';
                return;
            }

            const e = PlatPursuit.HTMLUtils.escape;
            let html = '<div class="space-y-3">';
            for (const plat of data.platinums) {
                const icon = e(plat.title_icon_url || '');
                const name = e(plat.title_name || 'Unknown Game');
                const url = e(plat.game_url || '#');
                const platforms = (plat.title_platform || []).map(p =>
                    `<span class="badge badge-xs badge-ghost">${e(p)}</span>`
                ).join(' ');

                // Format the earned date
                const earnedDate = plat.earned_date_time
                    ? new Date(plat.earned_date_time).toLocaleDateString(undefined, {
                        year: 'numeric', month: 'short', day: 'numeric'
                    })
                    : '';

                html += `
                    <a href="${url}" class="flex items-center gap-3 p-2 rounded-lg hover:bg-base-200 transition-colors group">
                        <div class="w-12 h-12 rounded-lg overflow-hidden flex-shrink-0 border border-base-300">
                            ${icon
                                ? `<img src="${icon}" alt="" class="w-full h-full object-cover" loading="lazy" />`
                                : '<div class="w-full h-full bg-base-300 flex items-center justify-center text-base-content/20 text-xs">?</div>'
                            }
                        </div>
                        <div class="flex-1 min-w-0">
                            <div class="font-medium text-sm group-hover:text-primary transition-colors truncate">${name}</div>
                            <div class="flex items-center gap-2 mt-0.5">
                                ${platforms}
                                ${earnedDate ? `<span class="text-xs text-base-content/50">${e(earnedDate)}</span>` : ''}
                            </div>
                        </div>
                        <svg xmlns="http://www.w3.org/2000/svg" class="w-4 h-4 text-base-content/30 group-hover:text-primary transition-colors flex-shrink-0" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M9 18l6-6-6-6"/></svg>
                    </a>
                `;
            }
            html += '</div>';
            contentEl.innerHTML = html;

        } catch (error) {
            if (error.name === 'AbortError') return;
            let msg = 'Failed to load day details.';
            try { const errData = await error.response?.json(); msg = errData?.error || msg; } catch {}
            contentEl.innerHTML = `<p class="text-error text-center py-4">${PlatPursuit.HTMLUtils.escape(msg)}</p>`;
        }
    }

    // ─── Delete Button ─────────────────────────────────────────────────────

    function _bindDeleteButton() {
        const btn = document.getElementById('delete-challenge-btn');
        if (!btn) return;

        const modal = document.getElementById('delete-confirm-modal');
        const confirmBtn = document.getElementById('delete-confirm-btn');
        if (!modal || !confirmBtn) return;

        btn.addEventListener('click', () => modal.showModal());

        confirmBtn.addEventListener('click', async () => {
            try {
                await PlatPursuit.API.delete(`/api/v1/challenges/calendar/${challengeId}/delete/`);
                modal.close();
                PlatPursuit.ToastManager.success('Challenge deleted.');
                window.location.href = '/my-challenges/';
            } catch (error) {
                modal.close();
                let msg = 'Failed to delete challenge.';
                try { const errData = await error.response?.json(); msg = errData?.error || msg; } catch {}
                PlatPursuit.ToastManager.error(msg);
            }
        });
    }

    // ─── Inline Rename ─────────────────────────────────────────────────────

    function _bindInlineRename() {
        const display = document.getElementById('challenge-name-display');
        const nameText = document.getElementById('challenge-name-text');
        const editBtn = document.getElementById('challenge-name-edit-btn');
        const editor = document.getElementById('challenge-name-editor');
        const input = document.getElementById('challenge-name-input');
        const saveBtn = document.getElementById('challenge-name-save-btn');
        const cancelBtn = document.getElementById('challenge-name-cancel-btn');
        const renameCounter = document.getElementById('rename-counter');

        if (!editBtn || !editor || !input || !saveBtn || !cancelBtn) return;

        function updateCounter() {
            if (renameCounter) renameCounter.textContent = `${input.value.length}/75`;
        }

        let originalName = nameText ? nameText.textContent.trim() : input.value.trim();
        let isSaving = false;

        function openEditor() {
            input.value = originalName;
            updateCounter();
            display.classList.add('hidden');
            editBtn.classList.add('hidden');
            editor.classList.remove('hidden');
            editor.classList.add('flex');
            input.focus();
            input.select();
        }

        function closeEditor() {
            editor.classList.add('hidden');
            editor.classList.remove('flex');
            display.classList.remove('hidden');
            editBtn.classList.remove('hidden');
        }

        async function saveName() {
            if (isSaving) return;

            const newName = input.value.trim();
            if (!newName) {
                PlatPursuit.ToastManager.error('Calendar name cannot be empty.');
                input.focus();
                return;
            }
            if (newName === originalName) {
                closeEditor();
                return;
            }

            isSaving = true;
            input.disabled = true;
            saveBtn.disabled = true;
            const savedHTML = saveBtn.innerHTML;
            saveBtn.innerHTML = '<span class="loading loading-spinner loading-xs"></span>';

            try {
                await PlatPursuit.API.patch(
                    `/api/v1/challenges/calendar/${challengeId}/update/`,
                    { name: newName }
                );

                originalName = newName;
                if (nameText) nameText.textContent = newName;
                input.value = newName;

                document.title = document.title.replace(
                    /^.*? - /,
                    `${newName} - `
                );

                // Sync share button data attributes and modal title
                const shareBtn = document.getElementById('share-image-btn');
                const bannerBtn = document.getElementById('banner-share-image-btn');
                const modalTitle = document.getElementById('calendar-share-modal-title');
                if (shareBtn) shareBtn.dataset.challengeName = newName;
                if (bannerBtn) bannerBtn.dataset.challengeName = newName;
                if (modalTitle) modalTitle.textContent = `Share: ${newName}`;

                closeEditor();
                PlatPursuit.ToastManager.success('Calendar renamed!');

            } catch (err) {
                let msg = 'Failed to rename calendar.';
                try { const errData = await err.response?.json(); msg = errData?.error || msg; } catch {}
                PlatPursuit.ToastManager.error(msg);
            } finally {
                isSaving = false;
                input.disabled = false;
                saveBtn.disabled = false;
                saveBtn.innerHTML = savedHTML;
            }
        }

        editBtn.addEventListener('click', openEditor);
        if (nameText) {
            nameText.style.cursor = 'pointer';
            nameText.addEventListener('click', openEditor);
        }
        cancelBtn.addEventListener('click', closeEditor);
        saveBtn.addEventListener('click', saveName);
        input.addEventListener('keydown', (e) => {
            if (e.key === 'Enter') { e.preventDefault(); saveName(); }
            else if (e.key === 'Escape') { e.preventDefault(); closeEditor(); }
        });
        input.addEventListener('input', updateCounter);
    }

    // ─── Share URL ─────────────────────────────────────────────────────────

    function _bindShareUrl() {
        const btn = document.getElementById('share-url-btn');
        if (!btn) return;

        btn.addEventListener('click', async () => {
            const url = window.location.href;
            if (navigator.share) {
                try {
                    await navigator.share({ title: document.title, url });
                } catch {} // User cancelled
            } else {
                try {
                    await navigator.clipboard.writeText(url);
                    PlatPursuit.ToastManager.success('Link copied to clipboard!');
                } catch {
                    PlatPursuit.ToastManager.error('Failed to copy link.');
                }
            }
        });
    }

    // ─── Share Image Button ──────────────────────────────────────────────

    function _bindShareImageButton() {
        if (typeof CalendarChallengeShareManager === 'undefined') return;

        let activeManager = null;

        const openShareModal = (challengeName) => {
            const modal = document.getElementById('calendar-share-modal');
            const content = document.getElementById('calendar-share-modal-content');
            if (!modal || !content) return;

            // Clean up previous manager's picker to prevent listener leaks
            if (activeManager && activeManager.gameBgPicker) {
                activeManager.gameBgPicker.destroy();
            }

            const manager = new CalendarChallengeShareManager(challengeId, challengeName);
            content.innerHTML = manager.renderShareSection();
            manager.init();
            activeManager = manager;

            modal.showModal();
        };

        // Header "Share Image" button
        const btn = document.getElementById('share-image-btn');
        if (btn) {
            btn.addEventListener('click', () => openShareModal(btn.dataset.challengeName || ''));
        }

        // CTA banner "Create Share Card" button
        const bannerBtn = document.getElementById('banner-share-image-btn');
        if (bannerBtn) {
            bannerBtn.addEventListener('click', () => openShareModal(bannerBtn.dataset.challengeName || ''));
        }
    }

    // ─── Holiday Highlights ─────────────────────────────────────────────

    function _bindHolidayToggle() {
        const toggle = document.getElementById('us-holiday-toggle');
        if (!toggle) return;

        // Restore saved preference
        toggle.checked = localStorage.getItem('calendarShowUSHolidays') === 'true';

        toggle.addEventListener('change', () => {
            localStorage.setItem('calendarShowUSHolidays', toggle.checked);
            _applyHolidayHighlights();
        });
    }

    function _applyHolidayHighlights() {
        const showUS = document.getElementById('us-holiday-toggle')?.checked || false;

        document.querySelectorAll('.cal-day[data-month][data-day]').forEach(cell => {
            const key = `${cell.dataset.month}-${cell.dataset.day}`;
            const isFilled = cell.dataset.filled === 'true';
            const gameName = cell.dataset.gameName || '';

            // International takes priority over US
            const holiday = INTL_HOLIDAYS[key] || (showUS ? US_HOLIDAYS[key] : null);

            if (holiday) {
                cell.style.border = `2px solid ${holiday.color}`;
                cell.style.boxShadow = `0 0 4px ${holiday.color}40`;

                // DaisyUI tooltip
                cell.classList.add('tooltip', 'tooltip-top');
                const tip = isFilled ? `${holiday.name} | ${gameName}` : holiday.name;
                cell.setAttribute('data-tip', tip);

                // Remove native title to prevent double-tooltip
                cell.removeAttribute('title');
            } else {
                // Reset: clear holiday styles
                cell.style.border = '';
                cell.style.boxShadow = '';
                cell.classList.remove('tooltip', 'tooltip-top');
                cell.removeAttribute('data-tip');

                // Restore native title for filled cells
                if (isFilled && gameName) {
                    cell.setAttribute('title', gameName);
                }
            }
        });
    }

    return { init };
})();
