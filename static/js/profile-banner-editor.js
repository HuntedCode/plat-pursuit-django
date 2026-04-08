/**
 * ProfileBannerEditor
 * Handles the banner image selection and vertical position adjustment
 * on the profile detail page. Premium users only.
 *
 * Requires: GameBackgroundPicker, PlatPursuit.API
 */
class ProfileBannerEditor {
    constructor(options = {}) {
        this.modal = document.getElementById('banner-editor-modal');
        this.editBtn = document.getElementById('banner-edit-btn');
        this.picker = null;
        this.selectedConcept = null;
        this.position = options.initialPosition || 50;
        this.initialData = options.initialData || null;

        if (!this.modal || !this.editBtn) return;
        this.init();
    }

    init() {
        this.editBtn.addEventListener('click', () => this.open());

        document.getElementById('banner-save-btn')?.addEventListener('click', () => this.save());
        document.getElementById('banner-remove-btn')?.addEventListener('click', () => this.remove());

        const slider = document.getElementById('banner-position-slider');
        if (slider) {
            slider.value = this.position;
            slider.addEventListener('input', (e) => this.updatePreview(e.target.value));
        }
    }

    open() {
        this.modal.showModal();

        if (!this.picker) {
            this.picker = new GameBackgroundPicker('banner-bg-picker-container', {
                initialValue: this.initialData,
                onSelect: (concept) => this.onGameSelected(concept),
                onClear: () => this.onGameCleared()
            });

            // If there's already a background, show the position section
            if (this.initialData) {
                this.selectedConcept = this.initialData;
                this.showPositionSection();
            }
        }
    }

    onGameSelected(concept) {
        this.selectedConcept = concept;
        this.showPositionSection();
    }

    showPositionSection() {
        const section = document.getElementById('banner-position-section');
        const previewImg = document.getElementById('banner-preview-image');

        if (section && previewImg && this.selectedConcept) {
            section.classList.remove('hidden');
            const bgUrl = this.selectedConcept.bg_url || this.selectedConcept.icon_url || '';
            previewImg.style.backgroundImage = `url('${bgUrl}')`;
            previewImg.style.backgroundSize = '100% auto';
            previewImg.style.backgroundRepeat = 'no-repeat';
            this.updatePreview(this.position);
        }
    }

    onGameCleared() {
        this.selectedConcept = null;
        document.getElementById('banner-position-section')?.classList.add('hidden');
    }

    updatePreview(value) {
        this.position = parseInt(value, 10);
        const previewImg = document.getElementById('banner-preview-image');
        if (previewImg) {
            previewImg.style.backgroundPosition = `center ${this.position}%`;
        }
    }

    async save() {
        const saveBtn = document.getElementById('banner-save-btn');
        if (saveBtn) {
            saveBtn.disabled = true;
            saveBtn.textContent = 'Saving...';
        }

        try {
            // Save background selection
            if (this.selectedConcept && this.selectedConcept.concept_id) {
                await PlatPursuit.API.request('/api/v1/user/quick-settings/', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        setting: 'selected_background',
                        value: this.selectedConcept.concept_id
                    })
                });
            }

            // Save position
            await PlatPursuit.API.request('/api/v1/user/quick-settings/', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    setting: 'banner_position',
                    value: this.position
                })
            });

            location.reload();
        } catch (err) {
            console.error('Failed to save banner:', err);
            PlatPursuit.ToastManager?.show('Failed to save banner. Please try again.', 'error');
            if (saveBtn) {
                saveBtn.disabled = false;
                saveBtn.textContent = 'Save Banner';
            }
        }
    }

    async remove() {
        const removeBtn = document.getElementById('banner-remove-btn');
        if (removeBtn) {
            removeBtn.disabled = true;
            removeBtn.textContent = 'Removing...';
        }

        try {
            await PlatPursuit.API.request('/api/v1/user/quick-settings/', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    setting: 'selected_background',
                    value: ''
                })
            });
            location.reload();
        } catch (err) {
            console.error('Failed to remove banner:', err);
            PlatPursuit.ToastManager?.show('Failed to remove banner. Please try again.', 'error');
            if (removeBtn) {
                removeBtn.disabled = false;
                removeBtn.textContent = 'Remove Banner';
            }
        }
    }
}
