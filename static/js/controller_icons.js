/**
 * controller_icons.js — Editor support for PlayStation controller shortcodes.
 *
 * Two pieces:
 *   1. Picker: toolbar button (data-fmt="controller") opens a floating popover
 *      with a grouped grid of glyphs. Click an icon to insert :shortcode: at
 *      the cursor of the last-focused textarea.
 *   2. Autocomplete: typing ":" in a roadmap-body textarea opens a dropdown
 *      filtered by shortcode prefix, identical UX to the @mention popover.
 *
 * Both lazy-load /static/images/controller/manifest.json on first use. The
 * icon set (ps4 vs ps5) is read from #roadmap-editor[data-controller-icon-set]
 * so platform-variant glyphs (Share/Create, Options, Touchpad) preview with
 * the right hardware in the picker.
 *
 * Surfaces with autocomplete: general tips, step descriptions, trophy guide
 * bodies. Notes are excluded (they render client-side plaintext, no markdown).
 */
(function () {
    'use strict';

    const MANIFEST_URL = '/static/images/controller/manifest.json';
    const ICON_BASE = '/static/images/controller';
    const TARGET_SELECTOR = '.general-tips-input, .step-desc-input, .trophy-guide-body';
    const MAX_AUTOCOMPLETE_RESULTS = 6;

    let manifestPromise = null;
    let iconSet = 'ps4';

    function loadManifest() {
        if (!manifestPromise) {
            manifestPromise = fetch(MANIFEST_URL, { credentials: 'same-origin' })
                .then(r => {
                    if (!r.ok) throw new Error(`manifest fetch failed: ${r.status}`);
                    return r.json();
                })
                .catch(err => {
                    console.error('[controller_icons] manifest load failed', err);
                    manifestPromise = null;
                    return null;
                });
        }
        return manifestPromise;
    }

    function detectIconSet() {
        const root = document.getElementById('roadmap-editor');
        const set = root?.dataset.controllerIconSet;
        iconSet = (set === 'ps5') ? 'ps5' : 'ps4';
    }

    function iconUrl(canonical, spec) {
        const folder = spec.set === 'shared' ? 'shared' : iconSet;
        return `${ICON_BASE}/${folder}/${canonical}.svg`;
    }

    function escapeHtml(s) {
        return String(s).replace(/[&<>"']/g, c => (
            {'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]
        ));
    }

    function insertAtCursor(textarea, text) {
        const start = textarea.selectionStart;
        const end = textarea.selectionEnd;
        const value = textarea.value;
        textarea.value = value.substring(0, start) + text + value.substring(end);
        const caret = start + text.length;
        textarea.focus();
        textarea.setSelectionRange(caret, caret);
        textarea.dispatchEvent(new Event('input', { bubbles: true }));
    }

    function replaceRange(textarea, from, to, text) {
        const value = textarea.value;
        textarea.value = value.substring(0, from) + text + value.substring(to);
        const caret = from + text.length;
        textarea.focus();
        textarea.setSelectionRange(caret, caret);
        textarea.dispatchEvent(new Event('input', { bubbles: true }));
    }

    // ------------------------------------------------------------------ //
    //  Picker
    // ------------------------------------------------------------------ //
    const Picker = {
        panelEl: null,
        targetTextarea: null,

        async open(textarea, anchorEl) {
            const manifest = await loadManifest();
            if (!manifest) return;
            this.targetTextarea = textarea;
            if (!this.panelEl) {
                this.panelEl = this._build(manifest);
                document.body.appendChild(this.panelEl);
                document.addEventListener('click', (e) => this._onDocClick(e), true);
                document.addEventListener('keydown', (e) => {
                    if (e.key === 'Escape' && this._isVisible()) {
                        e.preventDefault();
                        this.close();
                    }
                });
            }
            this._reposition(anchorEl);
            this.panelEl.classList.remove('hidden');
        },

        close() {
            if (this.panelEl) this.panelEl.classList.add('hidden');
            this.targetTextarea = null;
        },

        _isVisible() {
            return this.panelEl && !this.panelEl.classList.contains('hidden');
        },

        _onDocClick(e) {
            if (!this._isVisible()) return;
            if (this.panelEl.contains(e.target)) return;
            // Don't close if the click was on the toolbar button that opens us
            if (e.target.closest('.fmt-controller-btn')) return;
            this.close();
        },

        _build(manifest) {
            const el = document.createElement('div');
            el.id = 'controller-icon-picker';
            el.className = 'hidden fixed z-50 rounded-lg border-2 border-base-300 bg-base-200 shadow-xl p-3';
            el.style.minWidth = '280px';
            el.style.maxWidth = '360px';
            el.setAttribute('data-readonly-exempt', '');
            el.addEventListener('mousedown', (e) => e.preventDefault());

            const header = document.createElement('div');
            header.className = 'flex items-center justify-between mb-2 pb-2 border-b border-base-content/10';
            header.innerHTML = `
                <span class="text-xs font-semibold text-base-content/70 uppercase tracking-wider">Controller Buttons</span>
                <span class="text-[10px] text-base-content/40 uppercase">${escapeHtml(iconSet)}</span>
            `;
            el.appendChild(header);

            manifest.groups.forEach(group => {
                const groupEl = document.createElement('div');
                groupEl.className = 'mb-2 last:mb-0';

                const label = document.createElement('div');
                label.className = 'text-[10px] text-base-content/40 uppercase tracking-wider mb-1';
                label.textContent = group.label;
                groupEl.appendChild(label);

                const grid = document.createElement('div');
                grid.className = 'grid grid-cols-5 gap-1';
                group.shortcodes.forEach(code => {
                    const spec = manifest.shortcodes[code];
                    if (!spec) return;
                    const btn = document.createElement('button');
                    btn.type = 'button';
                    btn.className = 'controller-icon-btn p-1.5 rounded hover:bg-base-300/60 transition-colors flex items-center justify-center';
                    btn.title = `${spec.label} — :${code}:`;
                    btn.innerHTML = `<img src="${iconUrl(code, spec)}" alt="${escapeHtml(spec.label)}" class="w-5 h-5 object-contain" />`;
                    btn.addEventListener('click', () => {
                        if (this.targetTextarea) {
                            insertAtCursor(this.targetTextarea, `:${code}:`);
                        }
                        this.close();
                    });
                    grid.appendChild(btn);
                });
                groupEl.appendChild(grid);
                el.appendChild(groupEl);
            });

            const hint = document.createElement('div');
            hint.className = 'mt-2 pt-2 border-t border-base-content/10 text-[10px] text-base-content/40';
            hint.textContent = 'Tip: type ":" in any guide field for autocomplete.';
            el.appendChild(hint);

            return el;
        },

        _reposition(anchorEl) {
            const rect = anchorEl.getBoundingClientRect();
            const panelW = this.panelEl.offsetWidth || 320;
            const panelH = this.panelEl.scrollHeight || 320;
            const viewportW = window.innerWidth;
            const viewportH = window.innerHeight;
            let left = Math.round(rect.left);
            if (left + panelW > viewportW - 8) left = viewportW - panelW - 8;
            if (left < 8) left = 8;
            let top = Math.round(rect.bottom + 4);
            if (top + panelH > viewportH - 8) {
                top = Math.round(rect.top - panelH - 4);
            }
            if (top < 8) top = 8;
            this.panelEl.style.left = `${left}px`;
            this.panelEl.style.top = `${top}px`;
        },
    };

    // ------------------------------------------------------------------ //
    //  Autocomplete
    // ------------------------------------------------------------------ //
    const Autocomplete = {
        dropdownEl: null,
        activeTextarea: null,
        tokenStart: -1,
        candidates: [],
        selectedIndex: 0,
        manifest: null,

        async init() {
            this.manifest = await loadManifest();
            if (!this.manifest) return;
            this.dropdownEl = this._buildDropdown();
            document.body.appendChild(this.dropdownEl);
            document.addEventListener('input', (e) => this._onInput(e));
            document.addEventListener('keydown', (e) => this._onKeyDown(e), true);
            document.addEventListener('focusout', (e) => this._onFocusOut(e));
            window.addEventListener('resize', () => {
                if (this.activeTextarea) this._reposition();
            });
            window.addEventListener('scroll', () => {
                if (this.activeTextarea) this._reposition();
            }, true);
        },

        _buildDropdown() {
            const el = document.createElement('div');
            el.id = 'controller-shortcode-autocomplete';
            el.className = 'hidden fixed z-50 max-h-64 overflow-y-auto rounded-lg border-2 border-base-300 bg-base-200 shadow-xl flex-col';
            el.style.minWidth = '220px';
            el.setAttribute('data-readonly-exempt', '');
            el.addEventListener('mousedown', (e) => e.preventDefault());
            return el;
        },

        _onInput(e) {
            const ta = e.target.closest(TARGET_SELECTOR);
            if (!ta) {
                this._hide();
                return;
            }
            const detected = this._detectToken(ta);
            if (!detected) {
                this._hide();
                return;
            }
            this.activeTextarea = ta;
            this.tokenStart = detected.tokenStart;
            this.candidates = this._filter(detected.prefix);
            this.selectedIndex = 0;
            if (this.candidates.length === 0) {
                this._hide();
                return;
            }
            this._render();
            this._show();
            this._reposition();
        },

        _onKeyDown(e) {
            if (!this._isVisible()) return;
            if (!e.target.matches(TARGET_SELECTOR)) return;
            if (e.key === 'ArrowDown') {
                e.preventDefault();
                this.selectedIndex = (this.selectedIndex + 1) % this.candidates.length;
                this._render();
            } else if (e.key === 'ArrowUp') {
                e.preventDefault();
                this.selectedIndex = (this.selectedIndex - 1 + this.candidates.length) % this.candidates.length;
                this._render();
            } else if (e.key === 'Enter' || e.key === 'Tab') {
                e.preventDefault();
                e.stopPropagation();
                this._commit();
            } else if (e.key === 'Escape') {
                e.preventDefault();
                this._hide();
            }
        },

        _onFocusOut(e) {
            const next = e.relatedTarget;
            if (next && this.dropdownEl?.contains(next)) return;
            setTimeout(() => {
                if (document.activeElement?.matches(TARGET_SELECTOR)) return;
                this._hide();
            }, 100);
        },

        _detectToken(textarea) {
            const val = textarea.value;
            const caret = textarea.selectionStart;
            const before = val.substring(0, caret);
            // Match a `:` at start-of-input or preceded by whitespace/newline,
            // followed by 0+ shortcode chars (a-z0-9-) up to caret. The prefix
            // can be empty so we open the dropdown the moment ":" is typed.
            const m = before.match(/(^|[\s\n])(:)([a-z0-9-]*)$/);
            if (!m) return null;
            return {
                prefix: m[3],
                tokenStart: caret - m[3].length - 1,  // position of ':'
            };
        },

        _filter(prefix) {
            const lower = prefix.toLowerCase();
            const all = Object.keys(this.manifest.shortcodes);
            const aliases = Object.keys(this.manifest.aliases);
            const universe = all.concat(aliases);
            if (!lower) {
                // Empty prefix: show face buttons + common shoulders first
                return ['square', 'triangle', 'circle', 'cross', 'l1', 'r1']
                    .filter(c => this.manifest.shortcodes[c]);
            }
            const prefixHits = universe.filter(c => c.startsWith(lower));
            const seen = new Set();
            const ranked = [];
            prefixHits.forEach(c => {
                const canonical = this.manifest.aliases[c] || c;
                if (seen.has(canonical)) return;
                if (!this.manifest.shortcodes[canonical]) return;
                seen.add(canonical);
                ranked.push(canonical);
            });
            return ranked.slice(0, MAX_AUTOCOMPLETE_RESULTS);
        },

        _render() {
            this.dropdownEl.innerHTML = '';
            this.candidates.forEach((code, idx) => {
                const spec = this.manifest.shortcodes[code];
                const item = document.createElement('button');
                item.type = 'button';
                item.dataset.code = code;
                const isSelected = idx === this.selectedIndex;
                item.className = (
                    'shortcode-item w-full text-left px-3 py-1.5 text-xs flex items-center gap-2 '
                    + (isSelected ? 'bg-info/15' : 'hover:bg-base-300/50')
                );
                item.innerHTML = `
                    <img src="${iconUrl(code, spec)}" alt="" class="w-4 h-4 object-contain shrink-0" />
                    <span class="flex-1 min-w-0">
                        <span class="font-medium">${escapeHtml(spec.label)}</span>
                        <span class="text-base-content/40 ml-1 font-mono text-[10px]">:${escapeHtml(code)}:</span>
                    </span>
                `;
                item.addEventListener('click', () => {
                    this.selectedIndex = idx;
                    this._commit();
                });
                this.dropdownEl.appendChild(item);
            });
        },

        _reposition() {
            if (!this.activeTextarea || !this.dropdownEl) return;
            const rect = this.activeTextarea.getBoundingClientRect();
            this.dropdownEl.style.left = `${Math.round(rect.left)}px`;
            this.dropdownEl.style.width = `${Math.round(Math.min(rect.width, 320))}px`;
            const viewportH = window.innerHeight;
            const dropH = Math.min(this.dropdownEl.scrollHeight || 256, 256);
            const wouldClip = rect.bottom + 4 + dropH > viewportH;
            if (wouldClip && rect.top - 4 - dropH > 0) {
                this.dropdownEl.style.top = `${Math.round(rect.top - 4 - dropH)}px`;
            } else {
                this.dropdownEl.style.top = `${Math.round(rect.bottom + 4)}px`;
            }
        },

        _commit() {
            if (!this.activeTextarea || this.candidates.length === 0) return;
            const code = this.candidates[this.selectedIndex];
            const ta = this.activeTextarea;
            const tokenEnd = ta.selectionStart;
            replaceRange(ta, this.tokenStart, tokenEnd, `:${code}: `);
            this._hide();
        },

        _show() {
            this.dropdownEl.classList.remove('hidden');
            this.dropdownEl.classList.add('flex');
        },
        _hide() {
            if (this.dropdownEl) {
                this.dropdownEl.classList.add('hidden');
                this.dropdownEl.classList.remove('flex');
            }
            this.activeTextarea = null;
            this.tokenStart = -1;
        },
        _isVisible() {
            return this.dropdownEl && !this.dropdownEl.classList.contains('hidden');
        },
    };

    // ------------------------------------------------------------------ //
    //  Toolbar wiring
    // ------------------------------------------------------------------ //
    function init() {
        detectIconSet();
        Autocomplete.init();

        // Toolbar button. Using a capture-phase listener so the picker opens
        // before the existing FORMAT_MAP fallback in roadmap_editor.js gets a
        // chance to swallow the click for an unknown data-fmt key.
        document.addEventListener('click', (e) => {
            const btn = e.target.closest('.fmt-controller-btn');
            if (!btn) return;
            e.preventDefault();
            e.stopImmediatePropagation();
            // Find the textarea the toolbar belongs to (same logic as
            // roadmap_editor.js getTargetTextarea).
            const toolbar = btn.closest('.formatting-toolbar');
            let ta = null;
            if (toolbar) {
                let el = toolbar.nextElementSibling;
                while (el && !ta) {
                    if (el.tagName === 'TEXTAREA') ta = el;
                    else if (el.querySelector) ta = el.querySelector('textarea');
                    el = el.nextElementSibling;
                }
                if (!ta) ta = toolbar.parentElement?.querySelector('textarea');
            }
            if (!ta) return;
            Picker.open(ta, btn);
        }, true);
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', init);
    } else {
        init();
    }

    // Expose for debugging / future external callers
    window.PlatPursuit = window.PlatPursuit || {};
    window.PlatPursuit.ControllerIcons = { Picker, Autocomplete, loadManifest };
})();
