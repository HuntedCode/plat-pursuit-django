(function () {
    'use strict';

    const API = window.PlatPursuit?.API;
    const Toast = window.PlatPursuit?.ToastManager;
    const escapeHtml = window.PlatPursuit?.HTMLUtils?.escapeHtml || (s => s);

    const BASE_URL = '/api/v1/game-families';

    // Selected concepts for manual creation
    const selectedConcepts = new Map();

    // ── Tab switching ──
    function initTabs() {
        const tabs = document.querySelectorAll('#gf-tabs .tab');
        tabs.forEach(tab => {
            tab.addEventListener('click', () => {
                tabs.forEach(t => t.classList.remove('tab-active'));
                tab.classList.add('tab-active');

                document.querySelectorAll('.gf-tab-panel').forEach(c => c.classList.add('hidden'));
                const target = document.getElementById(`tab-${tab.dataset.tab}`);
                if (target) target.classList.remove('hidden');
            });
        });
    }

    function _switchToTab(tabName) {
        const tabs = document.querySelectorAll('#gf-tabs .tab');
        tabs.forEach(t => {
            t.classList.toggle('tab-active', t.dataset.tab === tabName);
        });
        document.querySelectorAll('.gf-tab-panel').forEach(c => c.classList.add('hidden'));
        const target = document.getElementById(`tab-${tabName}`);
        if (target) target.classList.remove('hidden');
    }

    // ── DOM helpers ──
    function _getFamilyCard(familyId) {
        return document.querySelector(`[data-family-id="${familyId}"]`);
    }

    function _renderConceptChip(familyId, concept) {
        const platforms = (concept.platforms || []).map(p =>
            `<span class="badge badge-xs badge-outline">${escapeHtml(p)}</span>`
        ).join('');
        const stub = concept.is_stub ? '<span class="badge badge-xs badge-error">Stub</span>' : '';
        const title = escapeHtml(concept.unified_title);

        const div = document.createElement('div');
        div.className = 'bg-base-200 rounded-lg px-3 py-2 flex items-center gap-2';
        div.setAttribute('data-concept-id', concept.id);
        div.innerHTML = `
            <span class="text-sm font-medium">${title}</span>
            ${platforms} ${stub}
            <button class="btn btn-ghost btn-xs text-error"
                    onclick="GameFamilyAdmin.removeConcept(${familyId}, ${concept.id}, '${concept.unified_title.replace(/'/g, "\\'")}')">
                &times;
            </button>
        `;
        return div;
    }

    function _updateFamilyConceptCount(card) {
        const conceptsContainer = card.querySelector('.flex.flex-wrap.gap-2.mt-2');
        const count = conceptsContainer ? conceptsContainer.querySelectorAll('[data-concept-id]').length : 0;
        const countBadge = card.querySelector('.badge-ghost.badge-sm');
        if (countBadge) countBadge.textContent = `${count} concepts`;
    }

    function _renderFamilyCard(family) {
        const card = document.createElement('div');
        card.className = 'card bg-base-100 border-2 border-base-300';
        card.setAttribute('data-family-id', family.id);

        const verifiedBadge = family.is_verified
            ? '<span class="badge badge-success badge-sm">Verified</span>'
            : '<span class="badge badge-warning badge-sm">Unverified</span>';
        const verifyBtn = family.is_verified
            ? `<button class="btn btn-sm btn-ghost text-warning" onclick="GameFamilyAdmin.toggleVerified(${family.id}, false)">Unverify</button>`
            : `<button class="btn btn-sm btn-ghost text-success" onclick="GameFamilyAdmin.toggleVerified(${family.id}, true)">Verify</button>`;
        const notesHtml = family.admin_notes
            ? `<p class="text-sm opacity-70 mb-2">${escapeHtml(family.admin_notes)}</p>`
            : '';
        const name = escapeHtml(family.canonical_name);
        const escapedName = family.canonical_name.replace(/'/g, "\\'").replace(/\\/g, '\\\\');
        const escapedNotes = (family.admin_notes || '').replace(/'/g, "\\'").replace(/\\/g, '\\\\');

        const conceptChips = (family.concepts || []).map(c => {
            const platforms = (c.platforms || []).map(p =>
                `<span class="badge badge-xs badge-outline">${escapeHtml(p)}</span>`
            ).join('');
            const stub = c.is_stub ? '<span class="badge badge-xs badge-error">Stub</span>' : '';
            const cName = escapeHtml(c.unified_title);
            const cEscaped = c.unified_title.replace(/'/g, "\\'");
            return `<div class="bg-base-200 rounded-lg px-3 py-2 flex items-center gap-2" data-concept-id="${c.id}">
                <span class="text-sm font-medium">${cName}</span>
                ${platforms} ${stub}
                <button class="btn btn-ghost btn-xs text-error"
                        onclick="GameFamilyAdmin.removeConcept(${family.id}, ${c.id}, '${cEscaped}')">&times;</button>
            </div>`;
        }).join('');

        card.innerHTML = `
            <div class="card-body">
                <div class="flex flex-col lg:flex-row justify-between items-start gap-4">
                    <div class="flex-1">
                        <div class="flex items-center gap-2 mb-2">
                            <h3 class="text-xl font-bold family-name">${name}</h3>
                            ${verifiedBadge}
                            <span class="badge badge-ghost badge-sm">${family.concept_count || family.concepts?.length || 0} concepts</span>
                        </div>
                        ${notesHtml}
                        <div class="flex flex-wrap gap-2 mt-2">${conceptChips}</div>
                    </div>
                    <div class="flex flex-col gap-2 flex-shrink-0">
                        <button class="btn btn-sm btn-outline" onclick="GameFamilyAdmin.editFamily(${family.id}, '${escapedName}', '${escapedNotes}')">Edit</button>
                        <button class="btn btn-sm btn-outline" onclick="GameFamilyAdmin.showAddConcept(${family.id})">Add Concept</button>
                        ${verifyBtn}
                        <button class="btn btn-sm btn-error btn-outline" onclick="GameFamilyAdmin.deleteFamily(${family.id}, '${escapedName}')">Delete</button>
                    </div>
                </div>
            </div>
        `;
        return card;
    }

    // ── Proposal Actions ──
    async function approveProposal(proposalId, proposedName) {
        const name = prompt('Canonical family name:', proposedName);
        if (name === null) return;

        try {
            const data = await API.post(`${BASE_URL}/proposals/${proposalId}/approve/`, {
                canonical_name: name.trim() || proposedName,
            });
            Toast.show('Proposal approved!', 'success');
            const card = document.querySelector(`[data-proposal-id="${proposalId}"]`);
            if (card) card.remove();

            // Add new family to Existing Families tab
            if (data.family) {
                const familiesList = document.getElementById('families-list');
                // Remove "No game families yet" placeholder if present
                const emptyMsg = familiesList.querySelector('.text-center.py-12');
                if (emptyMsg) emptyMsg.remove();
                familiesList.appendChild(_renderFamilyCard(data.family));
            }
        } catch (err) {
            const msg = await _extractError(err, 'Failed to approve proposal.');
            Toast.show(msg, 'error');
        }
    }

    async function rejectProposal(proposalId) {
        if (!confirm('Reject this proposal?')) return;

        try {
            await API.post(`${BASE_URL}/proposals/${proposalId}/reject/`, {});
            Toast.show('Proposal rejected.', 'info');
            const card = document.querySelector(`[data-proposal-id="${proposalId}"]`);
            if (card) card.remove();
        } catch (err) {
            const msg = await _extractError(err, 'Failed to reject proposal.');
            Toast.show(msg, 'error');
        }
    }

    // ── Family Management ──
    function editFamily(familyId, name, notes) {
        document.getElementById('edit-family-id').value = familyId;
        document.getElementById('edit-family-name').value = name;
        document.getElementById('edit-family-notes').value = notes;
        document.getElementById('edit-family-modal').showModal();
    }

    async function saveEdit() {
        const familyId = document.getElementById('edit-family-id').value;
        const name = document.getElementById('edit-family-name').value.trim();
        const notes = document.getElementById('edit-family-notes').value;

        if (!name) {
            Toast.show('Name is required.', 'error');
            return;
        }

        try {
            await API.patch(`${BASE_URL}/${familyId}/`, { canonical_name: name, admin_notes: notes });
            Toast.show('Family updated.', 'success');
            document.getElementById('edit-family-modal').close();

            // Update card DOM
            const card = _getFamilyCard(familyId);
            if (card) {
                const nameEl = card.querySelector('.family-name');
                if (nameEl) nameEl.textContent = name;

                // Update admin notes
                const notesEl = card.querySelector('.text-sm.opacity-70.mb-2');
                if (notes) {
                    if (notesEl) {
                        notesEl.textContent = notes;
                    } else {
                        const headerRow = card.querySelector('.flex.items-center.gap-2.mb-2');
                        if (headerRow) {
                            const p = document.createElement('p');
                            p.className = 'text-sm opacity-70 mb-2';
                            p.textContent = notes;
                            headerRow.after(p);
                        }
                    }
                } else if (notesEl) {
                    notesEl.remove();
                }

                // Update onclick handlers with new name/notes
                const escapedName = name.replace(/'/g, "\\'").replace(/\\/g, '\\\\');
                const escapedNotes = notes.replace(/'/g, "\\'").replace(/\\/g, '\\\\');
                const editBtn = card.querySelector('.btn-outline');
                if (editBtn) editBtn.setAttribute('onclick', `GameFamilyAdmin.editFamily(${familyId}, '${escapedName}', '${escapedNotes}')`);
                const deleteBtn = card.querySelector('.btn-error.btn-outline');
                if (deleteBtn) deleteBtn.setAttribute('onclick', `GameFamilyAdmin.deleteFamily(${familyId}, '${escapedName}')`);
            }
        } catch (err) {
            const msg = await _extractError(err, 'Failed to update family.');
            Toast.show(msg, 'error');
        }
    }

    async function toggleVerified(familyId, verified) {
        try {
            await API.patch(`${BASE_URL}/${familyId}/`, { is_verified: verified });
            Toast.show(verified ? 'Family verified.' : 'Family unverified.', 'success');

            const card = _getFamilyCard(familyId);
            if (card) {
                // Swap the verified badge
                const headerRow = card.querySelector('.flex.items-center.gap-2.mb-2');
                const oldBadge = headerRow?.querySelector('.badge-success, .badge-warning');
                if (oldBadge) {
                    oldBadge.className = verified
                        ? 'badge badge-success badge-sm'
                        : 'badge badge-warning badge-sm';
                    oldBadge.textContent = verified ? 'Verified' : 'Unverified';
                }

                // Swap the verify/unverify button
                const actionsCol = card.querySelector('.flex.flex-col.gap-2.flex-shrink-0');
                const oldVerifyBtn = actionsCol?.querySelector('.text-warning, .text-success');
                if (oldVerifyBtn) {
                    const newBtn = document.createElement('button');
                    newBtn.className = verified
                        ? 'btn btn-sm btn-ghost text-warning'
                        : 'btn btn-sm btn-ghost text-success';
                    newBtn.textContent = verified ? 'Unverify' : 'Verify';
                    newBtn.setAttribute('onclick', `GameFamilyAdmin.toggleVerified(${familyId}, ${!verified})`);
                    oldVerifyBtn.replaceWith(newBtn);
                }
            }
        } catch (err) {
            const msg = await _extractError(err, 'Failed to update verification.');
            Toast.show(msg, 'error');
        }
    }

    async function deleteFamily(familyId, name) {
        if (!confirm(`Delete family "${name}"? Concepts will be unlinked but not deleted.`)) return;

        try {
            await API.request(`${BASE_URL}/${familyId}/delete/`, { method: 'DELETE' });
            Toast.show('Family deleted.', 'info');
            const card = _getFamilyCard(familyId);
            if (card) card.remove();
        } catch (err) {
            const msg = await _extractError(err, 'Failed to delete family.');
            Toast.show(msg, 'error');
        }
    }

    async function removeConcept(familyId, conceptId, conceptTitle) {
        if (!confirm(`Remove "${conceptTitle}" from this family?`)) return;

        try {
            const data = await API.post(`${BASE_URL}/${familyId}/remove-concept/`, {
                concept_id: conceptId,
            });
            Toast.show('Concept removed.', 'info');

            const card = _getFamilyCard(familyId);
            if (!card) return;

            // If family was deleted (empty), remove the card
            if (data.message?.includes('deleted')) {
                card.remove();
                return;
            }

            // Remove the concept chip
            const chip = card.querySelector(`[data-concept-id="${conceptId}"]`);
            if (chip) chip.remove();
            _updateFamilyConceptCount(card);
        } catch (err) {
            const msg = await _extractError(err, 'Failed to remove concept.');
            Toast.show(msg, 'error');
        }
    }

    // ── Add Concept to Family ──
    function showAddConcept(familyId) {
        document.getElementById('add-concept-family-id').value = familyId;
        document.getElementById('add-concept-search').value = '';
        document.getElementById('add-concept-results').innerHTML = '';
        document.getElementById('add-concept-modal').showModal();
    }

    async function searchConceptsForAdd() {
        const query = document.getElementById('add-concept-search').value.trim();
        if (query.length < 2) return;
        const familyId = document.getElementById('add-concept-family-id').value;

        try {
            const data = await API.get(`${BASE_URL}/search-concepts/?q=${encodeURIComponent(query)}`);
            const container = document.getElementById('add-concept-results');
            container.innerHTML = '';

            data.results.forEach(concept => {
                const platforms = concept.platforms.map(p =>
                    `<span class="badge badge-xs badge-outline">${escapeHtml(p)}</span>`
                ).join(' ');
                const stub = concept.is_stub ? '<span class="badge badge-xs badge-error">Stub</span>' : '';
                const inFamily = concept.family_id
                    ? '<span class="badge badge-xs badge-warning">In Family</span>'
                    : '';

                const div = document.createElement('div');
                div.className = 'flex items-center justify-between bg-base-200 rounded-lg px-3 py-2';
                div.innerHTML = `
                    <div class="flex items-center gap-2 min-w-0">
                        ${concept.trophy_icon ? `<img src="${escapeHtml(concept.trophy_icon)}" class="w-8 h-8 rounded object-cover flex-shrink-0" alt="">` : ''}
                        <div class="min-w-0">
                            <p class="text-sm font-medium truncate">${escapeHtml(concept.unified_title)}</p>
                            <div class="flex gap-1">${platforms} ${stub} ${inFamily}</div>
                        </div>
                    </div>
                    ${!concept.family_id ? `<button class="btn btn-success btn-xs flex-shrink-0" onclick="GameFamilyAdmin.addConceptToFamily(${familyId}, ${concept.id})">Add</button>` : ''}
                `;
                container.appendChild(div);
            });

            if (data.results.length === 0) {
                container.innerHTML = '<p class="text-sm opacity-50 text-center py-4">No results found.</p>';
            }
        } catch (err) {
            Toast.show('Search failed.', 'error');
        }
    }

    async function addConceptToFamily(familyId, conceptId) {
        try {
            const data = await API.post(`${BASE_URL}/${familyId}/add-concept/`, {
                concept_id: conceptId,
            });
            Toast.show('Concept added!', 'success');
            document.getElementById('add-concept-modal').close();

            // Find the added concept from the response
            const card = _getFamilyCard(familyId);
            if (card && data.family) {
                const addedConcept = data.family.concepts.find(c => c.id === conceptId);
                if (addedConcept) {
                    const conceptsContainer = card.querySelector('.flex.flex-wrap.gap-2.mt-2');
                    if (conceptsContainer) {
                        conceptsContainer.appendChild(_renderConceptChip(familyId, addedConcept));
                    }
                    _updateFamilyConceptCount(card);
                }
            }
        } catch (err) {
            const msg = await _extractError(err, 'Failed to add concept.');
            Toast.show(msg, 'error');
        }
    }

    // ── Manual Create ──
    async function searchConcepts() {
        const query = document.getElementById('concept-search').value.trim();
        if (query.length < 2) return;

        try {
            const data = await API.get(`${BASE_URL}/search-concepts/?q=${encodeURIComponent(query)}`);
            const container = document.getElementById('search-results-list');
            const wrapper = document.getElementById('search-results');
            container.innerHTML = '';
            wrapper.classList.remove('hidden');

            data.results.forEach(concept => {
                const isSelected = selectedConcepts.has(concept.id);
                const platforms = concept.platforms.map(p =>
                    `<span class="badge badge-xs badge-outline">${escapeHtml(p)}</span>`
                ).join(' ');
                const stub = concept.is_stub ? '<span class="badge badge-xs badge-error">Stub</span>' : '';
                const inFamily = concept.family_id
                    ? '<span class="badge badge-xs badge-warning">In Family</span>'
                    : '';

                const div = document.createElement('div');
                div.className = `bg-base-200 rounded-lg p-3 cursor-pointer border-2 ${isSelected ? 'border-primary' : 'border-transparent'} ${concept.family_id ? 'opacity-50' : ''}`;
                div.innerHTML = `
                    <div class="flex items-center gap-2">
                        ${concept.trophy_icon ? `<img src="${escapeHtml(concept.trophy_icon)}" class="w-8 h-8 rounded object-cover flex-shrink-0" alt="">` : ''}
                        <div class="min-w-0">
                            <p class="text-sm font-medium truncate">${escapeHtml(concept.unified_title)}</p>
                            <p class="text-xs opacity-60">${escapeHtml(concept.concept_id)}</p>
                            <div class="flex gap-1 mt-1">${platforms} ${stub} ${inFamily}</div>
                        </div>
                    </div>
                `;
                if (!concept.family_id) {
                    div.addEventListener('click', () => toggleConceptSelection(concept, div));
                }
                container.appendChild(div);
            });

            if (data.results.length === 0) {
                container.innerHTML = '<p class="text-sm opacity-50 col-span-full text-center py-4">No results found.</p>';
            }
        } catch (err) {
            Toast.show('Search failed.', 'error');
        }
    }

    function toggleConceptSelection(concept, element) {
        if (selectedConcepts.has(concept.id)) {
            selectedConcepts.delete(concept.id);
            element.classList.remove('border-primary');
            element.classList.add('border-transparent');
        } else {
            selectedConcepts.set(concept.id, concept);
            element.classList.add('border-primary');
            element.classList.remove('border-transparent');
        }
        updateSelectedDisplay();
    }

    function updateSelectedDisplay() {
        const container = document.getElementById('selected-concepts');
        const countEl = document.getElementById('selected-count');
        const noMsg = document.getElementById('no-selection-msg');
        const createBtn = document.getElementById('create-family-btn');

        countEl.textContent = selectedConcepts.size;
        createBtn.disabled = selectedConcepts.size < 2;

        // Clear existing chips (keep the no-selection message)
        container.querySelectorAll('.concept-chip').forEach(c => c.remove());

        if (selectedConcepts.size === 0) {
            noMsg.classList.remove('hidden');
            return;
        }
        noMsg.classList.add('hidden');

        selectedConcepts.forEach((concept, id) => {
            const chip = document.createElement('div');
            chip.className = 'concept-chip badge badge-lg gap-1';
            chip.innerHTML = `
                ${escapeHtml(concept.unified_title)}
                <button class="btn btn-ghost btn-xs" onclick="GameFamilyAdmin.deselectConcept(${id})">&times;</button>
            `;
            container.appendChild(chip);
        });
    }

    function deselectConcept(conceptId) {
        selectedConcepts.delete(conceptId);
        // Update border in search results if visible
        document.querySelectorAll('#search-results-list > div').forEach(div => {
            // Re-render would be complex; just reload selection display
        });
        updateSelectedDisplay();
    }

    async function createFamily() {
        const name = document.getElementById('create-name').value.trim();
        const notes = document.getElementById('create-notes').value;

        if (!name) {
            Toast.show('Please enter a canonical name.', 'error');
            return;
        }
        if (selectedConcepts.size < 2) {
            Toast.show('Select at least 2 concepts.', 'error');
            return;
        }

        try {
            const data = await API.post(`${BASE_URL}/`, {
                canonical_name: name,
                concept_ids: Array.from(selectedConcepts.keys()),
                admin_notes: notes,
            });
            Toast.show('Game Family created!', 'success');

            // Reset form
            selectedConcepts.clear();
            updateSelectedDisplay();
            document.getElementById('create-name').value = '';
            document.getElementById('create-notes').value = '';
            document.getElementById('search-results').classList.add('hidden');

            // Add new family card to Existing Families tab
            if (data.family) {
                const familiesList = document.getElementById('families-list');
                const emptyMsg = familiesList.querySelector('.text-center.py-12');
                if (emptyMsg) emptyMsg.remove();
                familiesList.appendChild(_renderFamilyCard(data.family));
            }

            // Switch to families tab to show the result
            _switchToTab('families');
        } catch (err) {
            const msg = await _extractError(err, 'Failed to create family.');
            Toast.show(msg, 'error');
        }
    }

    // ── Helpers ──
    async function _extractError(error, fallback) {
        try {
            const errData = await error.response?.json();
            return errData?.error || fallback;
        } catch {
            return fallback;
        }
    }

    // ── Init ──
    document.addEventListener('DOMContentLoaded', () => {
        initTabs();

        // Enter key on search inputs
        document.getElementById('concept-search')?.addEventListener('keydown', e => {
            if (e.key === 'Enter') { e.preventDefault(); searchConcepts(); }
        });
        document.getElementById('add-concept-search')?.addEventListener('keydown', e => {
            if (e.key === 'Enter') { e.preventDefault(); searchConceptsForAdd(); }
        });
    });

    // Public API
    window.GameFamilyAdmin = {
        approveProposal,
        rejectProposal,
        editFamily,
        saveEdit,
        toggleVerified,
        deleteFamily,
        removeConcept,
        showAddConcept,
        searchConceptsForAdd,
        addConceptToFamily,
        searchConcepts,
        deselectConcept,
        createFamily,
    };
})();
