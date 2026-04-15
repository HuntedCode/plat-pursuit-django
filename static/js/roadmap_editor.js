/**
 * Roadmap Editor
 *
 * Staff-only editor for creating/editing game roadmaps.
 * Reuses: PlatPursuit.API, DragReorderManager, ToastManager, debounce, HTMLUtils
 */
(function() {
    'use strict';

    const API = window.PlatPursuit.API;
    const Toast = window.PlatPursuit.ToastManager;
    const debounce = window.PlatPursuit.debounce;

    const TROPHY_TYPE_COLORS = {
        platinum: 'badge-info',
        gold: 'badge-warning',
        silver: 'badge-ghost',
        bronze: 'badge-accent',
    };

    // ------------------------------------------------------------------ //
    //  State
    // ------------------------------------------------------------------ //

    const editorEl = document.getElementById('roadmap-editor');
    if (!editorEl) return;

    const roadmapId = parseInt(editorEl.dataset.roadmapId, 10);
    const tabsData = JSON.parse(document.getElementById('roadmap-tabs-data')?.textContent || '[]');
    const trophiesByGroup = JSON.parse(document.getElementById('roadmap-trophies-data')?.textContent || '{}');

    let activeTabId = tabsData.length ? tabsData[0].id : null;
    let hasUnsaved = false;
    let dragManagers = {};

    // ------------------------------------------------------------------ //
    //  Save Status
    // ------------------------------------------------------------------ //

    const saveIcon = document.getElementById('save-icon');
    const saveText = document.getElementById('save-text');
    const saveStatus = document.getElementById('save-status');

    function setSaveStatus(state) {
        if (!saveStatus) return;
        switch (state) {
            case 'saving':
                saveStatus.className = 'flex items-center gap-1 text-xs text-warning/70 ml-auto shrink-0';
                saveIcon.innerHTML = '<circle class="animate-spin origin-center" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="2" fill="none" stroke-dasharray="40 60"/>';
                saveText.textContent = 'Saving...';
                break;
            case 'saved':
                saveStatus.className = 'flex items-center gap-1 text-xs text-success/70 ml-auto shrink-0';
                saveIcon.innerHTML = '<path stroke-linecap="round" stroke-linejoin="round" d="M5 13l4 4L19 7"/>';
                saveText.textContent = 'Saved';
                hasUnsaved = false;
                break;
            case 'unsaved':
                saveStatus.className = 'flex items-center gap-1 text-xs text-warning/70 ml-auto shrink-0';
                saveIcon.innerHTML = '<path stroke-linecap="round" stroke-linejoin="round" d="M12 8v4m0 4h.01"/>';
                saveText.textContent = 'Unsaved';
                hasUnsaved = true;
                break;
            case 'error':
                saveStatus.className = 'flex items-center gap-1 text-xs text-error/70 ml-auto shrink-0';
                saveIcon.innerHTML = '<path stroke-linecap="round" stroke-linejoin="round" d="M6 18L18 6M6 6l12 12"/>';
                saveText.textContent = 'Error';
                break;
        }
    }

    async function apiCall(method, url, body) {
        setSaveStatus('saving');
        try {
            const result = await API[method](url, body);
            setSaveStatus('saved');
            return result;
        } catch (err) {
            setSaveStatus('error');
            const errData = await err.response?.json().catch(() => null);
            const msg = errData?.error || 'An error occurred.';
            Toast.show(msg, 'error');
            throw err;
        }
    }

    // ------------------------------------------------------------------ //
    //  Tab Switching
    // ------------------------------------------------------------------ //

    function initTabs() {
        document.querySelectorAll('.roadmap-tab-btn').forEach(btn => {
            btn.addEventListener('click', () => {
                const tabId = parseInt(btn.dataset.tabId, 10);
                switchTab(tabId);
            });
        });
    }

    function switchTab(tabId) {
        activeTabId = tabId;

        // Update button states
        document.querySelectorAll('.roadmap-tab-btn').forEach(btn => {
            const isActive = parseInt(btn.dataset.tabId, 10) === tabId;
            btn.classList.toggle('bg-primary', isActive);
            btn.classList.toggle('text-primary-content', isActive);
            btn.classList.toggle('shadow-sm', isActive);
            btn.classList.toggle('text-base-content/60', !isActive);
            btn.classList.toggle('hover:text-base-content', !isActive);
            btn.classList.toggle('hover:bg-base-300/50', !isActive);
            btn.setAttribute('aria-selected', isActive ? 'true' : 'false');
        });

        // Show/hide panels
        document.querySelectorAll('.roadmap-tab-panel').forEach(panel => {
            const isActive = parseInt(panel.dataset.tabId, 10) === tabId;
            panel.classList.toggle('hidden', !isActive);
        });
    }

    // ------------------------------------------------------------------ //
    //  Step Rendering
    // ------------------------------------------------------------------ //

    function renderSteps(tabId) {
        const tabData = tabsData.find(t => t.id === tabId);
        if (!tabData) return;

        const container = document.querySelector(`.steps-container[data-tab-id="${tabId}"]`);
        if (!container) return;

        container.innerHTML = '';
        const emptyState = container.parentElement.querySelector('.steps-empty-state');

        if (tabData.steps.length === 0) {
            emptyState?.classList.remove('hidden');
            return;
        }

        emptyState?.classList.add('hidden');

        tabData.steps.forEach((step, index) => {
            const el = createStepElement(step, index + 1, tabData.trophy_group_id);
            container.appendChild(el);
        });

        initDragReorder(tabId);
    }

    function createStepElement(step, number, groupId) {
        const template = document.getElementById('step-template');
        const el = template.content.firstElementChild.cloneNode(true);

        el.dataset.stepId = step.id;
        el.dataset.itemId = step.id;  // DragReorderManager reads data-item-id
        el.querySelector('.step-number').textContent = number;
        el.querySelector('.step-title-input').value = step.title;
        el.querySelector('.step-desc-input').value = step.description;
        el.querySelector('.step-youtube-input').value = step.youtube_url || '';

        // Trophy picker
        const picker = el.querySelector('.trophy-picker');
        const trophies = trophiesByGroup[groupId] || [];
        const associatedIds = new Set(step.trophy_ids || []);
        const countBadge = el.querySelector('.trophy-count-badge');
        countBadge.textContent = associatedIds.size;

        trophies.forEach(t => {
            const itemTemplate = document.getElementById('trophy-picker-item-template');
            const item = itemTemplate.content.firstElementChild.cloneNode(true);

            const checkbox = item.querySelector('.trophy-checkbox');
            checkbox.dataset.trophyId = t.trophy_id;
            checkbox.checked = associatedIds.has(t.trophy_id);

            const icon = item.querySelector('.trophy-picker-icon');
            icon.src = t.icon_url || '';
            icon.alt = t.name;

            item.querySelector('.trophy-picker-name').textContent = t.name;

            const typeBadge = item.querySelector('.trophy-picker-type');
            typeBadge.textContent = t.type;
            typeBadge.classList.add(TROPHY_TYPE_COLORS[t.type] || 'badge-ghost');

            checkbox.addEventListener('change', () => {
                saveTrophyAssociations(step.id, el);
            });

            picker.appendChild(item);
        });

        // Event listeners
        const debouncedStepSave = debounce(() => saveStep(step.id, el), 800);
        el.querySelector('.step-title-input').addEventListener('input', () => {
            setSaveStatus('unsaved');
            debouncedStepSave();
        });
        el.querySelector('.step-desc-input').addEventListener('input', () => {
            setSaveStatus('unsaved');
            debouncedStepSave();
        });
        el.querySelector('.step-youtube-input').addEventListener('input', () => {
            setSaveStatus('unsaved');
            debouncedStepSave();
        });

        el.querySelector('.delete-step-btn').addEventListener('click', () => {
            deleteStep(step.id);
        });

        return el;
    }

    async function saveStep(stepId, el) {
        const title = el.querySelector('.step-title-input').value.trim();
        const description = el.querySelector('.step-desc-input').value;
        const youtube_url = el.querySelector('.step-youtube-input').value.trim();

        if (!title) return;

        await apiCall('patch', `/api/v1/roadmap/${roadmapId}/tab/${activeTabId}/steps/${stepId}/`, {
            title, description, youtube_url
        });
    }

    async function saveTrophyAssociations(stepId, stepEl) {
        const checkboxes = stepEl.querySelectorAll('.trophy-checkbox:checked');
        const trophyIds = Array.from(checkboxes).map(cb => parseInt(cb.dataset.trophyId, 10));

        const countBadge = stepEl.querySelector('.trophy-count-badge');
        countBadge.textContent = trophyIds.length;

        await apiCall('put', `/api/v1/roadmap/${roadmapId}/tab/${activeTabId}/steps/${stepId}/trophies/`, {
            trophy_ids: trophyIds
        });
    }

    async function deleteStep(stepId) {
        if (!confirm('Delete this step? This cannot be undone.')) return;

        await apiCall('delete', `/api/v1/roadmap/${roadmapId}/tab/${activeTabId}/steps/${stepId}/`);

        // Remove from local state and re-render
        const tabData = tabsData.find(t => t.id === activeTabId);
        if (tabData) {
            tabData.steps = tabData.steps.filter(s => s.id !== stepId);
            renderSteps(activeTabId);
        }

        Toast.show('Step deleted.', 'success');
    }

    // ------------------------------------------------------------------ //
    //  Add Step
    // ------------------------------------------------------------------ //

    function initAddStepButtons() {
        document.querySelectorAll('.add-step-btn').forEach(btn => {
            btn.addEventListener('click', async () => {
                const tabId = parseInt(btn.dataset.tabId, 10);
                await addStep(tabId);
            });
        });
    }

    async function addStep(tabId) {
        const result = await apiCall('post', `/api/v1/roadmap/${roadmapId}/tab/${tabId}/steps/`, {
            title: 'New Step',
            description: '',
        });

        // Add to local state and re-render
        const tabData = tabsData.find(t => t.id === tabId);
        if (tabData && result) {
            tabData.steps.push({
                id: result.id,
                title: result.title,
                description: result.description,
                youtube_url: result.youtube_url || '',
                order: result.order,
                trophy_ids: [],
            });
            renderSteps(tabId);

            // Focus the new step's title input
            const container = document.querySelector(`.steps-container[data-tab-id="${tabId}"]`);
            const lastStep = container?.lastElementChild;
            if (lastStep) {
                lastStep.querySelector('.step-title-input')?.focus();
                lastStep.querySelector('.step-title-input')?.select();
            }
        }

        Toast.show('Step added.', 'success');
    }

    // ------------------------------------------------------------------ //
    //  Drag Reorder
    // ------------------------------------------------------------------ //

    function initDragReorder(tabId) {
        const container = document.querySelector(`.steps-container[data-tab-id="${tabId}"]`);
        if (!container || container.children.length < 2) return;

        // Destroy existing manager for this tab
        if (dragManagers[tabId]) {
            dragManagers[tabId].destroy?.();
        }

        dragManagers[tabId] = new window.PlatPursuit.DragReorderManager({
            container: container,
            itemSelector: '.step-card',
            handleSelector: '.step-handle',
            onReorder: async (itemId, newPosition, allItemIds) => {
                const stepIds = allItemIds.map(id => parseInt(id, 10));
                await apiCall('post', `/api/v1/roadmap/${roadmapId}/tab/${tabId}/steps/reorder/`, {
                    step_ids: stepIds
                });

                // Update local state
                const tabData = tabsData.find(t => t.id === tabId);
                if (tabData) {
                    const stepMap = {};
                    tabData.steps.forEach(s => { stepMap[s.id] = s; });
                    tabData.steps = stepIds.map(id => stepMap[id]).filter(Boolean);
                }

                // Re-number the step badges
                container.querySelectorAll('.step-card').forEach((el, idx) => {
                    el.querySelector('.step-number').textContent = idx + 1;
                });
            },
        });
    }

    // ------------------------------------------------------------------ //
    //  General Tips & YouTube (debounced autosave)
    // ------------------------------------------------------------------ //

    function initTabFields() {
        document.querySelectorAll('.general-tips-input').forEach(textarea => {
            const tabId = parseInt(textarea.dataset.tabId, 10);
            const debouncedSave = debounce(async () => {
                await apiCall('patch', `/api/v1/roadmap/${roadmapId}/tab/${tabId}/`, {
                    general_tips: textarea.value
                });
            }, 1000);

            textarea.addEventListener('input', () => {
                setSaveStatus('unsaved');
                debouncedSave();
            });
        });

        document.querySelectorAll('.youtube-url-input').forEach(input => {
            const tabId = parseInt(input.dataset.tabId, 10);
            const debouncedSave = debounce(async () => {
                await apiCall('patch', `/api/v1/roadmap/${roadmapId}/tab/${tabId}/`, {
                    youtube_url: input.value
                });
            }, 1000);

            input.addEventListener('input', () => {
                setSaveStatus('unsaved');
                debouncedSave();
            });
        });
    }

    // ------------------------------------------------------------------ //
    //  Metadata Fields
    // ------------------------------------------------------------------ //

    function initMetadataFields() {
        // Populate initial values from tabsData
        tabsData.forEach(tab => {
            document.querySelectorAll(`.metadata-input[data-tab-id="${tab.id}"]`).forEach(input => {
                const field = input.dataset.field;
                const val = tab[field];
                if (val !== null && val !== undefined) input.value = val;
            });
            document.querySelectorAll(`.metadata-toggle[data-tab-id="${tab.id}"]`).forEach(toggle => {
                const field = toggle.dataset.field;
                toggle.checked = !!tab[field];
                const label = toggle.closest('label')?.querySelector('.online-required-label');
                if (label) label.textContent = toggle.checked ? 'Yes' : 'No';
            });
        });

        // Debounced save for number inputs
        document.querySelectorAll('.metadata-input').forEach(input => {
            const tabId = parseInt(input.dataset.tabId, 10);
            const field = input.dataset.field;
            const debouncedSave = debounce(async () => {
                const body = {};
                body[field] = input.value;
                await apiCall('patch', `/api/v1/roadmap/${roadmapId}/tab/${tabId}/`, body);
            }, 1000);

            input.addEventListener('input', () => {
                setSaveStatus('unsaved');
                debouncedSave();
            });
        });

        // Immediate save for toggle
        document.querySelectorAll('.metadata-toggle').forEach(toggle => {
            const tabId = parseInt(toggle.dataset.tabId, 10);
            const field = toggle.dataset.field;

            toggle.addEventListener('change', async () => {
                const label = toggle.closest('label')?.querySelector('.online-required-label');
                if (label) label.textContent = toggle.checked ? 'Yes' : 'No';
                const body = {};
                body[field] = toggle.checked;
                await apiCall('patch', `/api/v1/roadmap/${roadmapId}/tab/${tabId}/`, body);
            });
        });
    }

    // ------------------------------------------------------------------ //
    //  Trophy Guides
    // ------------------------------------------------------------------ //

    function renderTrophyGuides(tabId) {
        const tabData = tabsData.find(t => t.id === tabId);
        if (!tabData) return;

        const container = document.querySelector(`.trophy-guides-container[data-tab-id="${tabId}"]`);
        if (!container) return;

        const groupId = container.dataset.groupId;
        const trophies = trophiesByGroup[groupId] || [];
        const guides = tabData.trophy_guides || {};

        container.innerHTML = '';

        trophies.forEach(t => {
            const template = document.getElementById('trophy-guide-template');
            const el = template.content.firstElementChild.cloneNode(true);

            el.dataset.trophyId = t.trophy_id;

            const icon = el.querySelector('.trophy-guide-icon');
            icon.src = t.icon_url || '';
            icon.alt = t.name;

            el.querySelector('.trophy-guide-name').textContent = t.name;

            const typeBadge = el.querySelector('.trophy-guide-type');
            typeBadge.textContent = t.type;
            typeBadge.classList.add(TROPHY_TYPE_COLORS[t.type] || 'badge-ghost');

            const statusBadge = el.querySelector('.trophy-guide-status');
            const guideData = guides[t.trophy_id] || {};
            const body = typeof guideData === 'string' ? guideData : (guideData.body || '');
            const textarea = el.querySelector('.trophy-guide-body');
            textarea.value = body;

            // Load flag checkboxes
            const flagCheckboxes = el.querySelectorAll('.trophy-guide-flag');
            flagCheckboxes.forEach(cb => {
                const flag = cb.dataset.flag;
                if (typeof guideData === 'object' && guideData[flag]) {
                    cb.checked = true;
                }
            });

            if (body) {
                statusBadge.textContent = 'Written';
                statusBadge.classList.add('badge-success');
            } else {
                statusBadge.textContent = 'Empty';
            }

            // Open if has content
            if (body) {
                el.setAttribute('open', '');
            }

            // Collect current flags from checkboxes
            function getFlags() {
                const flags = {};
                flagCheckboxes.forEach(cb => { flags[cb.dataset.flag] = cb.checked; });
                return flags;
            }

            // Debounced autosave
            const debouncedSave = debounce(async () => {
                const currentBody = textarea.value;
                const flags = getFlags();
                await apiCall('put', `/api/v1/roadmap/${roadmapId}/tab/${tabId}/trophy-guides/${t.trophy_id}/`, {
                    body: currentBody,
                    ...flags,
                });

                // Update status badge and local state
                const tabData = tabsData.find(td => td.id === tabId);
                if (tabData) {
                    if (currentBody.trim()) {
                        tabData.trophy_guides[t.trophy_id] = { body: currentBody, ...flags };
                    } else {
                        delete tabData.trophy_guides[t.trophy_id];
                    }
                    updateTrophyGuideCounter(tabId);
                }
                if (currentBody.trim()) {
                    statusBadge.textContent = 'Written';
                    statusBadge.className = 'trophy-guide-status badge badge-xs badge-success ml-auto shrink-0';
                } else {
                    statusBadge.textContent = 'Empty';
                    statusBadge.className = 'trophy-guide-status badge badge-xs badge-ghost ml-auto shrink-0';
                }
            }, 1000);

            textarea.addEventListener('input', () => {
                setSaveStatus('unsaved');
                debouncedSave();
            });

            flagCheckboxes.forEach(cb => {
                cb.addEventListener('change', () => {
                    setSaveStatus('unsaved');
                    debouncedSave();
                });
            });

            container.appendChild(el);
        });
    }

    // ------------------------------------------------------------------ //
    //  Publish / Unpublish
    // ------------------------------------------------------------------ //

    function initPublishButtons() {
        const publishBtn = document.getElementById('publish-btn');
        const unpublishBtn = document.getElementById('unpublish-btn');

        if (publishBtn) {
            publishBtn.addEventListener('click', async () => {
                await apiCall('post', `/api/v1/roadmap/${roadmapId}/publish/`, { action: 'publish' });
                Toast.show('Roadmap published!', 'success');
                updatePublishUI('published');
            });
        }

        if (unpublishBtn) {
            unpublishBtn.addEventListener('click', async () => {
                await apiCall('post', `/api/v1/roadmap/${roadmapId}/publish/`, { action: 'unpublish' });
                Toast.show('Roadmap unpublished.', 'info');
                updatePublishUI('draft');
            });
        }
    }

    function updatePublishUI(newStatus) {
        const badge = document.getElementById('status-badge');
        if (badge) {
            badge.textContent = newStatus.charAt(0).toUpperCase() + newStatus.slice(1);
            badge.classList.toggle('badge-warning', newStatus === 'draft');
            badge.classList.toggle('badge-success', newStatus === 'published');
        }

        // Swap the publish/unpublish button
        const publishBtn = document.getElementById('publish-btn');
        const unpublishBtn = document.getElementById('unpublish-btn');

        if (newStatus === 'published') {
            if (publishBtn) publishBtn.classList.add('hidden');
            if (unpublishBtn) {
                unpublishBtn.classList.remove('hidden');
            } else {
                // Create unpublish button
                const btnContainer = publishBtn?.parentElement;
                if (btnContainer && publishBtn) {
                    const btn = document.createElement('button');
                    btn.id = 'unpublish-btn';
                    btn.className = 'btn btn-sm btn-warning gap-1';
                    btn.innerHTML = `
                        <svg xmlns="http://www.w3.org/2000/svg" class="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2">
                            <path stroke-linecap="round" stroke-linejoin="round" d="M18.364 18.364A9 9 0 005.636 5.636m12.728 12.728A9 9 0 015.636 5.636m12.728 12.728L5.636 5.636"/>
                        </svg>
                        <span class="hidden md:inline">Unpublish</span>`;
                    btn.addEventListener('click', async () => {
                        await apiCall('post', `/api/v1/roadmap/${roadmapId}/publish/`, { action: 'unpublish' });
                        Toast.show('Roadmap unpublished.', 'info');
                        updatePublishUI('draft');
                    });
                    publishBtn.replaceWith(btn);
                }
            }
        } else {
            if (unpublishBtn) unpublishBtn.classList.add('hidden');
            if (publishBtn) {
                publishBtn.classList.remove('hidden');
            } else {
                const btnContainer = unpublishBtn?.parentElement;
                if (btnContainer && unpublishBtn) {
                    const btn = document.createElement('button');
                    btn.id = 'publish-btn';
                    btn.className = 'btn btn-sm btn-success gap-1';
                    btn.innerHTML = `
                        <svg xmlns="http://www.w3.org/2000/svg" class="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2">
                            <path stroke-linecap="round" stroke-linejoin="round" d="M9 12l2 2 4-4m6 2a9 9 0 11-18 0 9 9 0 0118 0z"/>
                        </svg>
                        <span class="hidden md:inline">Publish</span>`;
                    btn.addEventListener('click', async () => {
                        await apiCall('post', `/api/v1/roadmap/${roadmapId}/publish/`, { action: 'publish' });
                        Toast.show('Roadmap published!', 'success');
                        updatePublishUI('published');
                    });
                    unpublishBtn.replaceWith(btn);
                }
            }
        }
    }

    // ------------------------------------------------------------------ //
    //  Formatting Toolbar
    // ------------------------------------------------------------------ //

    const FORMAT_MAP = {
        bold:      { before: '**', after: '**', placeholder: 'bold text' },
        italic:    { before: '*',  after: '*',  placeholder: 'italic text' },
        underline: { before: '__', after: '__', placeholder: 'underlined text' },
        strike:    { before: '~~', after: '~~', placeholder: 'strikethrough' },
        link:      { before: '[',  after: '](url)', placeholder: 'link text' },
        list:      { linePrefix: '- ' },
        quote:     { linePrefix: '> ' },
    };

    // Track last-focused textarea so toolbar clicks can target it
    let lastFocusedTextarea = null;

    document.addEventListener('focusin', (e) => {
        if (e.target.tagName === 'TEXTAREA') {
            lastFocusedTextarea = e.target;
        }
    });

    function getTargetTextarea(toolbarEl) {
        // Walk siblings to find the nearest textarea
        let el = toolbarEl.nextElementSibling;
        while (el) {
            if (el.tagName === 'TEXTAREA') return el;
            const inner = el.querySelector?.('textarea');
            if (inner) return inner;
            el = el.nextElementSibling;
        }
        // Fallback: search within the same parent container
        return toolbarEl.parentElement?.querySelector('textarea') || null;
    }

    function applyFormat(textarea, before, after, placeholder) {
        const start = textarea.selectionStart;
        const end = textarea.selectionEnd;
        const text = textarea.value;
        const selected = text.substring(start, end) || placeholder;
        const replacement = before + selected + after;

        // Direct value manipulation (reliable across all browsers)
        textarea.value = text.substring(0, start) + replacement + text.substring(end);

        // Position cursor to select the inserted text
        const cursorStart = start + before.length;
        const cursorEnd = cursorStart + selected.length;
        textarea.focus();
        textarea.setSelectionRange(cursorStart, cursorEnd);

        textarea.dispatchEvent(new Event('input', { bubbles: true }));
    }

    function applyLinePrefix(textarea, prefix) {
        const start = textarea.selectionStart;
        const text = textarea.value;
        const lineStart = text.lastIndexOf('\n', start - 1) + 1;

        textarea.value = text.substring(0, lineStart) + prefix + text.substring(lineStart);
        const newPos = start + prefix.length;
        textarea.focus();
        textarea.setSelectionRange(newPos, newPos);

        textarea.dispatchEvent(new Event('input', { bubbles: true }));
    }

    function initFormattingToolbars() {
        // Formatting buttons
        document.addEventListener('click', (e) => {
            const btn = e.target.closest('.fmt-btn');
            if (!btn) return;
            e.preventDefault();

            const toolbar = btn.closest('.formatting-toolbar');
            const textarea = getTargetTextarea(toolbar) || lastFocusedTextarea;
            if (!textarea) return;

            const fmtKey = btn.dataset.fmt;

            // Trophy link button
            if (fmtKey === 'trophy-link') {
                showTrophyLinkPicker(textarea);
                return;
            }

            // Image upload button
            if (fmtKey === 'image') {
                uploadImage(textarea);
                return;
            }

            const fmt = FORMAT_MAP[fmtKey];
            if (!fmt) return;

            if (fmt.linePrefix) {
                applyLinePrefix(textarea, fmt.linePrefix);
            } else {
                applyFormat(textarea, fmt.before, fmt.after, fmt.placeholder);
            }
        });

        // Help toggle - show/hide global help panel
        document.addEventListener('click', (e) => {
            const btn = e.target.closest('.fmt-help-toggle');
            if (!btn) return;

            const helpPanel = document.getElementById('global-formatting-help');
            if (helpPanel) {
                helpPanel.classList.toggle('hidden');
                helpPanel.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
            }
        });
    }

    // ------------------------------------------------------------------ //
    //  Trophy Link Picker
    // ------------------------------------------------------------------ //

    function showTrophyLinkPicker(textarea) {
        // Find the active tab's trophy group
        const activePanel = document.querySelector(`.roadmap-tab-panel:not(.hidden)`);
        const groupId = activePanel?.dataset.groupId;
        const trophies = trophiesByGroup[groupId] || [];

        if (!trophies.length) {
            Toast.show('No trophies available for this tab.', 'warning');
            return;
        }

        // Remove any existing picker
        document.getElementById('trophy-link-picker')?.remove();

        const picker = document.createElement('div');
        picker.id = 'trophy-link-picker';
        picker.className = 'fixed inset-0 z-50 flex items-center justify-center bg-black/50';
        picker.innerHTML = `
            <div class="bg-base-200 border-2 border-base-300 rounded-xl shadow-2xl w-[90vw] max-w-md max-h-[70vh] flex flex-col">
                <div class="flex items-center justify-between p-3 border-b border-base-300">
                    <h3 class="text-sm font-bold">Link to Trophy Guide</h3>
                    <button class="btn btn-ghost btn-xs btn-circle" id="trophy-link-close">&times;</button>
                </div>
                <div class="p-2 border-b border-base-300">
                    <input type="text" class="input input-bordered input-sm w-full" id="trophy-link-search" placeholder="Search trophies...">
                </div>
                <div class="overflow-y-auto flex-1 p-2 space-y-1" id="trophy-link-list"></div>
            </div>
        `;

        document.body.appendChild(picker);

        const list = picker.querySelector('#trophy-link-list');
        const search = picker.querySelector('#trophy-link-search');

        function renderList(filter) {
            const filtered = filter
                ? trophies.filter(t => t.name.toLowerCase().includes(filter.toLowerCase()))
                : trophies;

            list.innerHTML = filtered.map(t => `
                <button class="trophy-link-option flex items-center gap-2 w-full p-2 rounded-lg hover:bg-white/[0.05] transition-colors text-left" data-trophy-id="${t.trophy_id}" data-trophy-name="${t.name.replace(/"/g, '&quot;')}">
                    <img src="${t.icon_url}" alt="" class="w-7 h-7 rounded object-cover shrink-0">
                    <span class="text-sm truncate flex-1">${t.name}</span>
                    <span class="badge badge-xs ${TROPHY_TYPE_COLORS[t.type] || 'badge-ghost'} shrink-0">${t.type}</span>
                </button>
            `).join('');
        }

        renderList();
        search.focus();
        search.addEventListener('input', () => renderList(search.value));

        // Select trophy
        list.addEventListener('click', (e) => {
            const opt = e.target.closest('.trophy-link-option');
            if (!opt) return;

            const trophyId = opt.dataset.trophyId;
            const trophyName = opt.dataset.trophyName;

            applyFormat(textarea, '[', `](#trophy-guide-${trophyId})`, trophyName);
            picker.remove();
        });

        // Close
        picker.querySelector('#trophy-link-close').addEventListener('click', () => picker.remove());
        picker.addEventListener('click', (e) => {
            if (e.target === picker) picker.remove();
        });
        document.addEventListener('keydown', function escHandler(e) {
            if (e.key === 'Escape') {
                picker.remove();
                document.removeEventListener('keydown', escHandler);
            }
        });
    }

    // ------------------------------------------------------------------ //
    //  Image Upload
    // ------------------------------------------------------------------ //

    function uploadImage(textarea) {
        const input = document.createElement('input');
        input.type = 'file';
        input.accept = 'image/jpeg,image/png,image/webp,image/gif';

        input.addEventListener('change', async () => {
            const file = input.files[0];
            if (!file) return;

            if (file.size > 5 * 1024 * 1024) {
                Toast.show('Image must be under 5MB.', 'error');
                return;
            }

            setSaveStatus('saving');
            try {
                const formData = new FormData();
                formData.append('image', file);

                const result = await API.postFormData('/api/v1/roadmap/upload-image/', formData);
                if (result?.url) {
                    applyFormat(textarea, '![', `](${result.url})`, file.name.replace(/\.[^.]+$/, ''));
                    setSaveStatus('saved');
                    Toast.show('Image uploaded.', 'success');
                }
            } catch (err) {
                setSaveStatus('error');
                const errData = await err.response?.json().catch(() => null);
                Toast.show(errData?.error || 'Image upload failed.', 'error');
            }
        });

        input.click();
    }

    // ------------------------------------------------------------------ //
    //  Keyboard Shortcuts
    // ------------------------------------------------------------------ //

    function initKeyboardShortcuts() {
        document.addEventListener('keydown', (e) => {
            if (!e.ctrlKey && !e.metaKey) return;

            const textarea = document.activeElement;
            if (!textarea || textarea.tagName !== 'TEXTAREA') return;

            let fmt;
            switch (e.key.toLowerCase()) {
                case 'b': fmt = FORMAT_MAP.bold; break;
                case 'i': fmt = FORMAT_MAP.italic; break;
                case 'u': fmt = FORMAT_MAP.underline; break;
                default: return;
            }

            e.preventDefault();
            wrapSelection(textarea, fmt.before, fmt.after, fmt.placeholder);
        });
    }

    // ------------------------------------------------------------------ //
    //  Textarea Auto-Resize
    // ------------------------------------------------------------------ //

    function autoResize(textarea) {
        textarea.style.height = 'auto';
        textarea.style.height = textarea.scrollHeight + 'px';
    }

    function initAutoResize() {
        document.addEventListener('input', (e) => {
            if (e.target.classList.contains('auto-resize')) {
                autoResize(e.target);
            }
        });

        // Initial resize for textareas with content
        requestAnimationFrame(() => {
            document.querySelectorAll('textarea.auto-resize').forEach(autoResize);
        });
    }

    // ------------------------------------------------------------------ //
    //  Trophy Guide Counter
    // ------------------------------------------------------------------ //

    function updateTrophyGuideCounter(tabId) {
        const tabData = tabsData.find(t => t.id === tabId);
        if (!tabData) return;

        const groupId = document.querySelector(`.trophy-guides-container[data-tab-id="${tabId}"]`)?.dataset.groupId;
        const totalTrophies = (trophiesByGroup[groupId] || []).length;
        const writtenGuides = Object.keys(tabData.trophy_guides || {}).filter(
            k => {
                const v = tabData.trophy_guides[k];
                const body = typeof v === 'string' ? v : (v?.body || '');
                return body.trim();
            }
        ).length;

        const counter = document.querySelector(`.trophy-guide-counter[data-tab-id="${tabId}"]`);
        if (counter) {
            counter.textContent = `Individual tips for each trophy. ${writtenGuides} of ${totalTrophies} written. Supports markdown.`;
        }
    }

    // ------------------------------------------------------------------ //
    //  Initialization
    // ------------------------------------------------------------------ //

    function init() {
        initTabs();
        initAddStepButtons();
        initTabFields();
        initMetadataFields();
        initPublishButtons();
        initFormattingToolbars();
        initKeyboardShortcuts();
        initAutoResize();

        // Render all tabs
        tabsData.forEach(tab => {
            renderSteps(tab.id);
            renderTrophyGuides(tab.id);
            updateTrophyGuideCounter(tab.id);
        });

        // Warn on navigation with unsaved changes
        window.addEventListener('beforeunload', (e) => {
            if (hasUnsaved) {
                e.preventDefault();
                e.returnValue = '';
            }
        });
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', init);
    } else {
        init();
    }
})();
