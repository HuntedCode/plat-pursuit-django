/**
 * Roadmap Detail Page
 *
 * Handles: reading progress bar, scrollspy TOC with click-lock and auto-center,
 * mobile TOC toggle, animated details open/close, auto-collapse earned guides,
 * trophy guide sort/filter, expand/collapse all, trophy link navigation,
 * image lightbox for guide screenshots, and deep link handling.
 */
(function () {
    'use strict';

    // ── Reading Progress Bar ──────────────────────────────────────────

    function initProgressBar() {
        const container = document.getElementById('roadmap-detail');
        if (!container) return;

        const bar = document.createElement('div');
        bar.id = 'reading-progress';
        Object.assign(bar.style, {
            position: 'fixed',
            top: '0',
            left: '0',
            height: '2px',
            zIndex: '9999',
            background: 'linear-gradient(90deg, var(--color-primary), var(--color-secondary))',
            width: '0',
            transition: 'width 50ms linear',
            pointerEvents: 'none',
        });
        document.body.appendChild(bar);

        function update() {
            const rect = container.getBoundingClientRect();
            const total = rect.height - window.innerHeight;
            if (total <= 0) {
                bar.style.width = '100%';
                return;
            }
            const pct = Math.min(100, Math.max(0, (-rect.top / total) * 100));
            bar.style.width = pct + '%';
        }

        window.addEventListener('scroll', update, { passive: true });
        update();
    }

    // ── Scrollspy ──────────────────────────────────────────────────────

    function initScrollspy() {
        const tocLinks = document.querySelectorAll('[data-toc-link]');
        const sections = document.querySelectorAll('[data-toc-section]');
        if (!tocLinks.length || !sections.length) return;

        let currentActive = null;
        let scrollLocked = false;
        let lockTimer = null;

        function setActive(id) {
            if (id === currentActive) return;
            currentActive = id;

            tocLinks.forEach((link) => {
                const isActive = link.getAttribute('href') === '#' + id;
                link.classList.toggle('text-primary', isActive);
                link.classList.toggle('font-semibold', isActive);
                link.classList.toggle('bg-primary/5', isActive);
                link.classList.toggle('text-base-content/50', !isActive);

                // Auto-center active item in sidebar
                if (isActive) {
                    const nav = link.closest('.roadmap-toc-nav');
                    if (nav) {
                        const linkRect = link.getBoundingClientRect();
                        const navRect = nav.getBoundingClientRect();
                        const relativeTop = linkRect.top - navRect.top + nav.scrollTop;
                        nav.scrollTo({
                            top: relativeTop - nav.offsetHeight / 2 + link.offsetHeight / 2,
                            behavior: 'smooth',
                        });
                    }
                }
            });
        }

        const observer = new IntersectionObserver(
            (entries) => {
                if (scrollLocked) return;
                entries.forEach((entry) => {
                    if (entry.isIntersecting) {
                        setActive(entry.target.id);
                    }
                });
            },
            {
                rootMargin: '-10% 0px -80% 0px',
                threshold: 0,
            }
        );

        sections.forEach((section) => observer.observe(section));

        // Smooth scroll on TOC link click with click-lock
        tocLinks.forEach((link) => {
            link.addEventListener('click', (e) => {
                e.preventDefault();
                const targetId = link.getAttribute('href').slice(1);
                const target = document.getElementById(targetId);
                if (!target) return;

                // Lock scrollspy to prevent flickering during smooth scroll
                scrollLocked = true;
                if (lockTimer) clearTimeout(lockTimer);
                lockTimer = setTimeout(() => {
                    scrollLocked = false;
                }, 1200);

                // Immediately set active state
                setActive(targetId);

                // If target is a trophy guide, ensure it's visible and open
                if (target.classList.contains('trophy-guide-item')) {
                    if (target.classList.contains('hidden')) target.classList.remove('hidden');
                    if (!target.open) target.open = true;
                }
                // Collectible area: TOC link to a collapsed (auto-completed)
                // chapter should pop it open before scrolling, otherwise the
                // user lands on a closed bar with no items visible.
                if (target.classList.contains('collectible-area-group') && !target.open) {
                    target.open = true;
                }

                target.scrollIntoView({ behavior: 'smooth', block: 'start' });
                history.replaceState(null, '', '#' + targetId);
            });
        });
    }

    // ── TOC Collapsible Sections ─────────────────────────────────────

    function initTocSections() {
        document.querySelectorAll('.toc-section-toggle').forEach((btn) => {
            const targetId = btn.dataset.tocSectionTarget;
            const list = document.getElementById(targetId);
            const chevron = btn.querySelector('.toc-section-chevron');
            if (!list) return;

            // Per-list animation lock. Prevents double-clicks during a
            // transition from queueing up overlapping animations (which
            // would leave the inline styles in an inconsistent state).
            let animating = false;

            btn.addEventListener('click', (e) => {
                // Don't collapse if clicking the anchor link inside the button
                if (e.target.closest('a')) return;
                if (animating) return;
                animating = true;

                const isHidden = list.classList.contains('hidden');

                // Same max-height + opacity pattern the chapter <details>
                // uses, scaled down (TOC lists are short, so the duration
                // is shorter than the full content collapse). `overflow:
                // hidden` is needed during the animation so children
                // don't poke past the shrinking max-height.
                if (isHidden) {
                    // Opening
                    list.classList.remove('hidden');
                    list.style.maxHeight = '0px';
                    list.style.opacity = '0';
                    list.style.overflow = 'hidden';
                    if (chevron) chevron.classList.toggle('rotate-180', false);
                    requestAnimationFrame(() => {
                        list.style.transition = 'max-height 200ms ease-out, opacity 160ms ease-out';
                        list.style.maxHeight = list.scrollHeight + 'px';
                        list.style.opacity = '1';
                    });
                    setTimeout(() => {
                        list.style.maxHeight = '';
                        list.style.opacity = '';
                        list.style.overflow = '';
                        list.style.transition = '';
                        animating = false;
                    }, 200);
                } else {
                    // Closing
                    list.style.maxHeight = list.scrollHeight + 'px';
                    list.style.overflow = 'hidden';
                    if (chevron) chevron.classList.toggle('rotate-180', true);
                    requestAnimationFrame(() => {
                        list.style.transition = 'max-height 180ms ease-out, opacity 140ms ease-out';
                        list.style.maxHeight = '0px';
                        list.style.opacity = '0';
                    });
                    setTimeout(() => {
                        list.classList.add('hidden');
                        list.style.maxHeight = '';
                        list.style.opacity = '';
                        list.style.overflow = '';
                        list.style.transition = '';
                        animating = false;
                    }, 180);
                }
            });
        });
    }

    // ── TOC Trophy Search Filter ──────────────────────────────────────

    function initTocTrophySearch() {
        document.querySelectorAll('.toc-trophy-search').forEach((input) => {
            input.addEventListener('input', () => {
                const query = input.value.toLowerCase().trim();
                const list = input.closest('ul');
                if (!list) return;

                list.querySelectorAll('.toc-trophy-entry').forEach((entry) => {
                    const name = entry.dataset.trophyName || '';
                    entry.style.display = !query || name.includes(query) ? '' : 'none';
                });

                // Hide phase headers whose entries are all filtered out by
                // the search; otherwise empty phase chips would float around.
                // Walk siblings between each header and the next, count matches.
                list.querySelectorAll('.toc-phase-header').forEach(header => {
                    let next = header.nextElementSibling;
                    let visibleCount = 0;
                    while (next && !next.classList.contains('toc-phase-header')) {
                        if (next.classList.contains('toc-trophy-entry') && next.style.display !== 'none') {
                            visibleCount += 1;
                        }
                        next = next.nextElementSibling;
                    }
                    header.style.display = (query && visibleCount === 0) ? 'none' : '';
                });
            });
        });
    }

    // ── TOC Scroll Fade Masks ─────────────────────────────────────────

    function initTocFadeMasks() {
        const nav = document.querySelector('.roadmap-toc-nav');
        if (!nav) return;

        function updateMask() {
            const { scrollTop, scrollHeight, clientHeight } = nav;
            const atTop = scrollTop < 8;
            const atBottom = scrollTop + clientHeight >= scrollHeight - 8;

            let mask = '';
            if (atTop && atBottom) {
                // Content fits, no mask needed
                mask = 'none';
            } else if (atTop) {
                mask = 'linear-gradient(to bottom, black, black calc(100% - 1.5rem), transparent)';
            } else if (atBottom) {
                mask = 'linear-gradient(to bottom, transparent, black 1.5rem, black)';
            } else {
                mask = 'linear-gradient(to bottom, transparent, black 1.5rem, black calc(100% - 1.5rem), transparent)';
            }

            nav.style.maskImage = mask;
            nav.style.webkitMaskImage = mask;
        }

        nav.addEventListener('scroll', updateMask, { passive: true });

        // Re-check when TOC sections expand/collapse (watch child list hidden toggling)
        document.querySelectorAll('.toc-section-toggle').forEach((btn) => {
            btn.addEventListener('click', () => requestAnimationFrame(updateMask));
        });

        updateMask();
    }

    // ── Mobile TOC Toggle ──────────────────────────────────────────────

    function initMobileToc() {
        const toggle = document.querySelector('.roadmap-toc-toggle');
        const dropdown = document.querySelector('.roadmap-toc-dropdown');
        const chevron = document.querySelector('.roadmap-toc-chevron');
        if (!toggle || !dropdown) return;

        toggle.addEventListener('click', () => {
            const isHidden = dropdown.classList.contains('hidden');
            dropdown.classList.toggle('hidden');
            if (chevron) chevron.classList.toggle('rotate-180', isHidden);
        });

        // Close dropdown when a link is clicked and scroll to target
        dropdown.querySelectorAll('.roadmap-toc-link').forEach((link) => {
            link.addEventListener('click', (e) => {
                e.preventDefault();
                dropdown.classList.add('hidden');
                if (chevron) chevron.classList.remove('rotate-180');

                const targetId = link.getAttribute('href').slice(1);
                const target = document.getElementById(targetId);
                if (target) {
                    target.scrollIntoView({ behavior: 'smooth', block: 'start' });
                    history.replaceState(null, '', '#' + targetId);
                }
            });
        });
    }

    // ── Animated Details Open/Close ────────────────────────────────────

    function initDetailsAnimation() {
        document.querySelectorAll('.roadmap-step-details').forEach((details) => {
            const summary = details.querySelector('summary');
            if (!summary) return;

            let animating = false;

            summary.addEventListener('click', (e) => {
                e.preventDefault();
                if (animating) return;
                animating = true;

                const content = details.querySelector('summary + div');
                if (!content) {
                    animating = false;
                    return;
                }

                if (details.open) {
                    // Closing
                    content.style.maxHeight = content.scrollHeight + 'px';
                    content.style.overflow = 'hidden';
                    requestAnimationFrame(() => {
                        content.style.transition = 'max-height 200ms ease-out, opacity 150ms ease-out';
                        content.style.maxHeight = '0px';
                        content.style.opacity = '0';
                    });
                    setTimeout(() => {
                        details.open = false;
                        content.style.maxHeight = '';
                        content.style.overflow = '';
                        content.style.opacity = '';
                        content.style.transition = '';
                        animating = false;
                    }, 200);
                } else {
                    // Opening
                    details.open = true;
                    content.style.overflow = 'hidden';
                    content.style.maxHeight = '0px';
                    content.style.opacity = '0';
                    requestAnimationFrame(() => {
                        content.style.transition = 'max-height 250ms ease-out, opacity 200ms ease-out';
                        content.style.maxHeight = content.scrollHeight + 'px';
                        content.style.opacity = '1';
                    });
                    setTimeout(() => {
                        content.style.maxHeight = '';
                        content.style.overflow = '';
                        content.style.opacity = '';
                        content.style.transition = '';
                        animating = false;
                    }, 250);
                }
            });
        });
    }

    // ── Auto-Collapse Earned Guides ───────────────────────────────────

    function initEarnedCollapse() {
        document.querySelectorAll('.trophy-guide-item[data-earned="1"]').forEach((details) => {
            if (details.open) {
                details.open = false;
            }
        });
    }

    // ── Trophy Guide Sort/Filter ───────────────────────────────────────

    function initTrophyGuideControls() {
        const sortSelect = document.querySelector('.trophy-guide-sort');
        const earnedFilter = document.querySelector('.trophy-guide-earned-filter input');
        const flagButtons = document.querySelectorAll('.trophy-flag-filter');
        const list = document.querySelector('.trophy-guides-list');
        if (!list) return;

        // Track active flag filter (only one at a time, toggle off to clear)
        let activeFlag = null;

        // Phase metadata for grouped sort (label, emoji, order). Optional —
        // grouped sort just falls back to default if not present.
        const phasesRaw = JSON.parse(document.getElementById('roadmap-trophy-phases')?.textContent || '[]');
        const phaseMeta = {};
        const phaseOrder = {};
        phasesRaw.forEach((p, idx) => {
            phaseMeta[p.key] = p;
            phaseOrder[p.key] = idx;
        });
        // Untagged trophies sort to the end under an "Other" header.
        phaseOrder[''] = phasesRaw.length;

        function clearPhaseHeaders() {
            list.querySelectorAll('.phase-section-header').forEach(h => h.remove());
        }

        // Persistent collapse state for TOC phase sections, keyed by phase key
        // (e.g. 'story', 'challenge', 'platinum'). Survives across re-renders
        // triggered by filter changes so toggling a flag doesn't expand
        // sections the user already collapsed.
        const collapsedPhases = new Set();

        function escapeHtml(s) {
            return String(s).replace(/[&<>"']/g, c => (
                {'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]
            ));
        }

        // Apply current collapse state to a TOC ul: rotate chevrons, hide
        // entries between a collapsed header and the next header.
        function applyTOCCollapseState(tocUl) {
            tocUl.querySelectorAll('.toc-phase-header').forEach(header => {
                const phaseKey = header.dataset.phaseKey;
                const collapsed = collapsedPhases.has(phaseKey);
                const chevron = header.querySelector('.toc-phase-chevron');
                if (chevron) chevron.classList.toggle('-rotate-90', collapsed);
                let next = header.nextElementSibling;
                while (next && !next.classList.contains('toc-phase-header')) {
                    next.classList.toggle('toc-phase-collapsed', collapsed);
                    next = next.nextElementSibling;
                }
            });
        }

        // Mirror the main list's order + visibility into both TOCs (sidebar +
        // mobile). Authors expect the table of contents to reflect what
        // they're actually seeing — when phase grouping reorders trophies or
        // a flag filter hides half of them, the TOC moves in lockstep.
        // The TOC search input uses inline style.display so its overrides
        // remain compatible with the .hidden class we toggle here.
        // For phase-grouped sort, headers render as small text rows that
        // collapse their group on click.
        function syncTOCFromList() {
            const sequence = [];
            Array.from(list.children).forEach(el => {
                if (el.classList.contains('phase-section-header')) {
                    sequence.push({
                        kind: 'header',
                        phaseKey: el.dataset.phaseKey || 'other',
                        emoji: el.dataset.phaseEmoji || '·',
                        label: el.dataset.phaseLabel || 'Other',
                        count: el.dataset.phaseCount || '0',
                    });
                } else if (el.classList.contains('trophy-guide-item')) {
                    sequence.push({
                        kind: 'item',
                        trophyId: el.dataset.order,
                        hidden: el.classList.contains('hidden'),
                    });
                }
            });

            document.querySelectorAll('#toc-trophy-guides, #toc-mobile-trophy-guides').forEach(tocUl => {
                tocUl.querySelectorAll('.toc-phase-header').forEach(h => h.remove());

                const entriesByTrophy = new Map();
                tocUl.querySelectorAll('.toc-trophy-entry').forEach(entry => {
                    entriesByTrophy.set(entry.dataset.trophyId, entry);
                });

                sequence.forEach(s => {
                    if (s.kind === 'header') {
                        const li = document.createElement('li');
                        li.className = 'toc-phase-header mt-2 first:mt-1 border-t border-base-content/5 pt-1.5';
                        li.dataset.phaseKey = s.phaseKey;
                        li.innerHTML = `
                            <button type="button" class="toc-phase-toggle w-full flex items-center gap-1.5 text-[10px] uppercase tracking-wider font-bold text-base-content/45 hover:text-base-content/80 px-1.5 py-0.5 rounded hover:bg-white/[0.04] transition-colors">
                                <span class="shrink-0">${s.emoji}</span>
                                <span class="truncate">${escapeHtml(s.label)}</span>
                                <span class="text-base-content/30 font-normal normal-case tracking-normal">(${s.count})</span>
                                <svg class="toc-phase-chevron w-3 h-3 ml-auto shrink-0 transition-transform duration-200" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2.5"><path stroke-linecap="round" stroke-linejoin="round" d="M19 9l-7 7-7-7"/></svg>
                            </button>
                        `;
                        tocUl.appendChild(li);
                    } else {
                        const tocEntry = entriesByTrophy.get(s.trophyId);
                        if (!tocEntry) return;
                        tocUl.appendChild(tocEntry);
                        tocEntry.classList.toggle('hidden', s.hidden);
                    }
                });

                applyTOCCollapseState(tocUl);
            });
        }

        // Event delegation for phase header collapse toggle. Attached once on
        // each TOC ul; survives the rebuild-on-sort cycle since headers are
        // children of the ul, not the ul itself.
        document.querySelectorAll('#toc-trophy-guides, #toc-mobile-trophy-guides').forEach(tocUl => {
            tocUl.addEventListener('click', (e) => {
                const header = e.target.closest('.toc-phase-header');
                if (!header || !tocUl.contains(header)) return;
                const phaseKey = header.dataset.phaseKey;
                if (!phaseKey) return;
                if (collapsedPhases.has(phaseKey)) {
                    collapsedPhases.delete(phaseKey);
                } else {
                    collapsedPhases.add(phaseKey);
                }
                // Apply to BOTH TOCs so sidebar + mobile stay in sync.
                document.querySelectorAll('#toc-trophy-guides, #toc-mobile-trophy-guides').forEach(applyTOCCollapseState);
            });
        });

        function renderGroupedByPhase(items, hideEarned) {
            clearPhaseHeaders();

            // Pull platinums out — they always pin to a "Platinum" section
            // at the top regardless of any phase tag they may have inherited.
            // Phase tagging is hidden for platinum trophies in the editor, but
            // we filter defensively here in case a stale value exists.
            const platinums = items.filter(i => i.dataset.type === 'platinum');
            const others = items.filter(i => i.dataset.type !== 'platinum');

            // Stable sort: by phase order, then preserve original data-order within phase.
            others.sort((a, b) => {
                const aPhase = a.dataset.phase || '';
                const bPhase = b.dataset.phase || '';
                const diff = (phaseOrder[aPhase] ?? 999) - (phaseOrder[bPhase] ?? 999);
                if (diff !== 0) return diff;
                return parseInt(a.dataset.order || 0) - parseInt(b.dataset.order || 0);
            });

            // Compute visibility per item (used by both platinum and others).
            function visibleFor(item) {
                if (hideEarned && item.dataset.earned === '1') return false;
                if (activeFlag && item.dataset[activeFlag] !== '1') return false;
                return true;
            }

            function makeHeader(phaseKey, emoji, label, count, badgeClass) {
                const header = document.createElement('div');
                header.className = 'phase-section-header flex items-center gap-2 mt-4 first:mt-0 mb-1 pl-1';
                // Data attributes let the TOC sync extract metadata without parsing
                // the visible text — and let collapse state key off a stable id.
                header.dataset.phaseKey = phaseKey;
                header.dataset.phaseEmoji = emoji;
                header.dataset.phaseLabel = label;
                header.dataset.phaseCount = count;
                header.dataset.phaseBadgeClass = badgeClass;
                header.innerHTML = `
                    <span class="badge badge-sm ${badgeClass} font-semibold gap-1">${emoji} ${label}</span>
                    <span class="text-xs text-base-content/40">${count}</span>
                    <div class="flex-1 h-px bg-base-content/10 ml-1"></div>
                `;
                return header;
            }

            // Platinum section first. Render the header only if at least one
            // platinum is visible after filtering, but ALWAYS re-append all
            // platinum items so DOM order is correct on the next render cycle
            // (filter toggles trigger a re-render and rely on stable order).
            const visiblePlatinums = platinums.filter(visibleFor);
            if (visiblePlatinums.length > 0) {
                list.appendChild(makeHeader('platinum', '🏆', 'Platinum', visiblePlatinums.length, 'badge-info'));
            }
            platinums.forEach(p => {
                list.appendChild(p);
                p.classList.toggle('hidden', !visibleFor(p));
            });

            // Then phase sections for the rest.
            const visibility = others.map(visibleFor);
            const sections = new Map();
            others.forEach((item, idx) => {
                const key = item.dataset.phase || '';
                if (!sections.has(key)) {
                    const meta = phaseMeta[key] || { label: 'Other', emoji: '·', badge_class: 'badge-ghost' };
                    sections.set(key, { ...meta, key, count: 0 });
                }
                if (visibility[idx]) sections.get(key).count += 1;
            });

            sections.forEach((section, key) => {
                // Render the header only if any item in this phase is visible
                // after filtering. But ALWAYS re-append items and apply the
                // .hidden class — otherwise items in a fully-filtered-out
                // phase keep their previous DOM position and stale visibility
                // class, which then breaks TOC mirroring on the next render.
                if (section.count > 0) {
                    list.appendChild(makeHeader(key || 'other', section.emoji, section.label, section.count, section.badge_class));
                }
                others.forEach((item, idx) => {
                    if ((item.dataset.phase || '') !== key) return;
                    list.appendChild(item);
                    item.classList.toggle('hidden', !visibility[idx]);
                });
            });

            syncTOCFromList();
        }

        function sortAndFilter() {
            const items = Array.from(list.querySelectorAll('.trophy-guide-item'));
            const sortBy = sortSelect ? sortSelect.value : 'default';
            const hideEarned = earnedFilter ? earnedFilter.checked : false;

            if (sortBy === 'phase') {
                renderGroupedByPhase(items, hideEarned);
                return;
            }

            // Non-grouped modes: ensure any leftover section headers are gone.
            clearPhaseHeaders();

            // Sort
            const typeOrder = { platinum: 0, gold: 1, silver: 2, bronze: 3 };
            items.sort((a, b) => {
                switch (sortBy) {
                    case 'type': {
                        const aType = typeOrder[a.dataset.type] ?? 4;
                        const bType = typeOrder[b.dataset.type] ?? 4;
                        return aType - bType;
                    }
                    case 'rarity':
                        return parseFloat(a.dataset.rarity || 100) - parseFloat(b.dataset.rarity || 100);
                    case 'earned':
                        return parseInt(b.dataset.earned || 0) - parseInt(a.dataset.earned || 0);
                    case 'unearned':
                        return parseInt(a.dataset.earned || 0) - parseInt(b.dataset.earned || 0);
                    default:
                        return parseInt(a.dataset.order || 0) - parseInt(b.dataset.order || 0);
                }
            });

            // Re-append in sorted order and apply filters
            items.forEach((item) => {
                list.appendChild(item);
                let hidden = false;
                if (hideEarned && item.dataset.earned === '1') hidden = true;
                if (activeFlag && item.dataset[activeFlag] !== '1') hidden = true;
                item.classList.toggle('hidden', hidden);
            });

            syncTOCFromList();
        }

        if (sortSelect) sortSelect.addEventListener('change', sortAndFilter);
        if (earnedFilter) earnedFilter.addEventListener('change', sortAndFilter);

        // Flag filter toggle buttons
        const flagColors = { missable: 'btn-warning', online: 'btn-info', unobtainable: 'btn-error' };
        flagButtons.forEach((btn) => {
            btn.addEventListener('click', () => {
                const flag = btn.dataset.flag;
                if (activeFlag === flag) {
                    // Deactivate
                    activeFlag = null;
                    btn.classList.remove(flagColors[flag], 'font-bold');
                    btn.classList.add('btn-ghost', 'border', 'border-base-300');
                } else {
                    // Deactivate previous
                    flagButtons.forEach((b) => {
                        b.classList.remove(flagColors[b.dataset.flag], 'font-bold');
                        b.classList.add('btn-ghost', 'border', 'border-base-300');
                    });
                    // Activate this one
                    activeFlag = flag;
                    btn.classList.remove('btn-ghost', 'border', 'border-base-300');
                    btn.classList.add(flagColors[flag], 'font-bold');
                }
                sortAndFilter();
            });
        });
    }

    // ── Expand/Collapse All ────────────────────────────────────────────

    function initExpandCollapse() {
        function getDetails(target) {
            if (target === 'roadmap-steps') return document.querySelectorAll('.roadmap-step-details');
            if (target === 'guides') return document.querySelectorAll('.trophy-guide-item');
            return null;
        }

        // Update only the inner .roadmap-expand-label so the icon SVGs
        // (visible on mobile) don't get clobbered by textContent reassignment.
        // Also flip data-state on the button so CSS swaps which icon shows
        // (chevron-double-down for "expand", chevron-double-up for "collapse").
        function setExpandLabel(btn, text) {
            const label = btn.querySelector('.roadmap-expand-label') || btn;
            label.textContent = text;
            btn.dataset.state = text === 'Collapse All' ? 'collapse' : 'expand';
        }

        function syncLabel(btn) {
            const details = getDetails(btn.dataset.target);
            if (!details || !details.length) return;
            const openCount = Array.from(details).filter((d) => d.open && !d.classList.contains('hidden')).length;
            const visibleCount = Array.from(details).filter((d) => !d.classList.contains('hidden')).length;
            setExpandLabel(btn, openCount > visibleCount / 2 ? 'Collapse All' : 'Expand All');
        }

        document.querySelectorAll('.roadmap-expand-toggle').forEach((btn) => {
            // Sync label to reflect actual state (earned collapse may have changed it)
            syncLabel(btn);

            btn.addEventListener('click', () => {
                const details = getDetails(btn.dataset.target);
                if (!details || !details.length) return;

                const openCount = Array.from(details).filter((d) => d.open && !d.classList.contains('hidden')).length;
                const visibleCount = Array.from(details).filter((d) => !d.classList.contains('hidden')).length;
                const shouldOpen = openCount <= visibleCount / 2;

                details.forEach((d) => {
                    if (!d.classList.contains('hidden')) {
                        d.open = shouldOpen;
                    }
                });

                setExpandLabel(btn, shouldOpen ? 'Collapse All' : 'Expand All');
            });
        });
    }

    // ── Trophy Link Navigation ─────────────────────────────────────────

    function navigateToGuide(href) {
        if (!href) return;
        const targetId = href.startsWith('#') ? href.slice(1) : href;
        const target = document.getElementById(targetId);
        if (!target) return;

        // Unhide if hidden by earned filter
        if (target.classList.contains('hidden')) {
            target.classList.remove('hidden');
        }

        // Open the details element if collapsed
        if (target.tagName === 'DETAILS' && !target.open) {
            target.open = true;
        }

        // Wait a frame for layout, then scroll and highlight
        requestAnimationFrame(() => {
            target.scrollIntoView({ behavior: 'smooth', block: 'center' });

            target.classList.add('ring-2', 'ring-primary/50');
            setTimeout(() => {
                target.classList.remove('ring-2', 'ring-primary/50');
            }, 1500);
        });
    }

    function initTrophyMentions() {
        document.querySelectorAll('.trophy-mention').forEach((mention) => {
            const href = mention.getAttribute('href');
            if (!href) return;

            // Extract trophy ID and find the corresponding guide card
            const targetId = href.startsWith('#') ? href.slice(1) : href;
            const guideCard = document.getElementById(targetId);
            if (!guideCard) return;

            const trophyType = guideCard.dataset.type;
            if (trophyType) {
                mention.dataset.trophyType = trophyType;

                // Append type label
                const label = document.createElement('span');
                label.className = 'trophy-mention-type';
                label.textContent = trophyType.charAt(0).toUpperCase() + trophyType.slice(1);
                mention.appendChild(label);
            }
        });
    }

    function initTrophyLinkNavigation() {
        // Step card trophy links + inline trophy mentions in markdown prose
        document.querySelectorAll('.roadmap-trophy-link, .trophy-mention').forEach((link) => {
            link.addEventListener('click', (e) => {
                e.preventDefault();
                navigateToGuide(link.getAttribute('href'));
            });
        });
    }

    // ── Deep Link Handling ──────────────────────────────────────────────

    function handleDeepLink() {
        const hash = window.location.hash;
        if (!hash) return;

        const targetId = hash.slice(1);
        const target = document.getElementById(targetId);
        if (!target) return;

        // Unhide if hidden by earned filter
        if (target.classList.contains('hidden')) {
            target.classList.remove('hidden');
        }

        // Open any parent details elements
        let el = target;
        while (el) {
            if (el.tagName === 'DETAILS' && !el.open) {
                el.open = true;
            }
            el = el.parentElement;
        }

        // If the target itself is a details, open it
        if (target.tagName === 'DETAILS' && !target.open) {
            target.open = true;
        }

        // Slight delay to let layout settle, then scroll
        requestAnimationFrame(() => {
            setTimeout(() => {
                target.scrollIntoView({ behavior: 'smooth', block: 'start' });
                // Highlight
                target.classList.add('ring-2', 'ring-primary/50');
                setTimeout(() => {
                    target.classList.remove('ring-2', 'ring-primary/50');
                }, 2000);
            }, 100);
        });
    }

    // ── Image Lightbox & Trophy Guide Galleries ─────────────────────

    function initImageLightbox() {
        const container = document.getElementById('roadmap-detail');
        if (!container) return;

        // ── Build shared lightbox overlay ──

        const overlay = document.createElement('div');
        overlay.id = 'roadmap-lightbox';
        Object.assign(overlay.style, {
            position: 'fixed',
            inset: '0',
            zIndex: '99999',
            background: 'rgba(0, 0, 0, 0.92)',
            backdropFilter: 'blur(4px)',
            display: 'none',
            alignItems: 'center',
            justifyContent: 'center',
        });

        overlay.innerHTML = `
            <button class="roadmap-lb-close" style="position:absolute;top:1rem;right:1rem;color:#fff;background:rgba(255,255,255,0.1);border:none;border-radius:9999px;width:2.5rem;height:2.5rem;display:flex;align-items:center;justify-content:center;cursor:pointer;font-size:1.25rem;transition:background 0.2s;" aria-label="Close">&times;</button>
            <button class="roadmap-lb-prev" style="position:absolute;left:1rem;top:50%;transform:translateY(-50%);color:#fff;background:rgba(255,255,255,0.1);border:none;border-radius:9999px;width:2.5rem;height:2.5rem;display:flex;align-items:center;justify-content:center;cursor:pointer;font-size:1.25rem;transition:background 0.2s;" aria-label="Previous image">&#8249;</button>
            <button class="roadmap-lb-next" style="position:absolute;right:1rem;top:50%;transform:translateY(-50%);color:#fff;background:rgba(255,255,255,0.1);border:none;border-radius:9999px;width:2.5rem;height:2.5rem;display:flex;align-items:center;justify-content:center;cursor:pointer;font-size:1.25rem;transition:background 0.2s;" aria-label="Next image">&#8250;</button>
            <div class="roadmap-lb-counter" style="position:absolute;top:1rem;left:1rem;color:rgba(255,255,255,0.6);font-size:0.75rem;font-weight:600;"></div>
            <img class="roadmap-lb-image" style="max-width:90vw;max-height:85vh;object-fit:contain;border-radius:0.75rem;" alt="" />
        `;
        document.body.appendChild(overlay);

        const lbImage = overlay.querySelector('.roadmap-lb-image');
        const lbCounter = overlay.querySelector('.roadmap-lb-counter');
        const lbClose = overlay.querySelector('.roadmap-lb-close');
        const lbPrev = overlay.querySelector('.roadmap-lb-prev');
        const lbNext = overlay.querySelector('.roadmap-lb-next');

        let activeGroup = [];
        let currentIndex = 0;

        function showImage(index) {
            if (!activeGroup.length) return;
            currentIndex = ((index % activeGroup.length) + activeGroup.length) % activeGroup.length;
            lbImage.src = activeGroup[currentIndex].src;
            lbImage.alt = activeGroup[currentIndex].alt || '';
            lbCounter.textContent = activeGroup.length > 1 ? (currentIndex + 1) + ' / ' + activeGroup.length : '';
            const multi = activeGroup.length > 1;
            lbPrev.style.display = multi ? 'flex' : 'none';
            lbNext.style.display = multi ? 'flex' : 'none';
        }

        function open(group, index) {
            activeGroup = group;
            showImage(index);
            overlay.style.display = 'flex';
            document.body.style.overflow = 'hidden';
        }

        function close() {
            overlay.style.display = 'none';
            document.body.style.overflow = '';
            activeGroup = [];
        }

        lbClose.addEventListener('click', close);
        lbPrev.addEventListener('click', () => showImage(currentIndex - 1));
        lbNext.addEventListener('click', () => showImage(currentIndex + 1));
        overlay.addEventListener('click', (e) => { if (e.target === overlay) close(); });
        document.addEventListener('keydown', (e) => {
            if (overlay.style.display !== 'flex') return;
            if (e.key === 'Escape') close();
            else if (e.key === 'ArrowLeft') showImage(currentIndex - 1);
            else if (e.key === 'ArrowRight') showImage(currentIndex + 1);
        });

        // ── Inline body images: click-to-fullscreen, no extraction ──
        // Body images live wherever the writer placed them in the markdown.
        // Galleries are rendered separately from `gallery_images` (see below).
        container.querySelectorAll('[class*="leading-relaxed"] img').forEach((img) => {
            // Browsers don't surface `alt` as a hover tooltip — only `title`.
            // Backfill `title` from `alt` so older inline images (uploaded
            // before the markdown generator started always emitting a title)
            // still show something on hover.
            if (!img.title && img.alt) img.title = img.alt;
            img.classList.add('cursor-pointer', 'hover:opacity-80', 'transition-opacity');
            img.addEventListener('click', () => open([img], 0));
        });

        // ── Structured galleries: per-trophy-guide / per-step thumbnail
        // grids rendered server-side from gallery_images. Each gallery is
        // its own slideshow group.
        container.querySelectorAll('.roadmap-gallery').forEach((gallery) => {
            const imgs = Array.from(gallery.querySelectorAll('img'));
            if (!imgs.length) return;
            imgs.forEach((img, i) => {
                img.addEventListener('click', () => open(imgs, i));
            });
        });
    }

    // ── Initialization ─────────────────────────────────────────────────

    // ---------------------------------------------------------------- //
    //  Collectibles Tracker
    // ---------------------------------------------------------------- //
    /**
     * Reader-side controller for the Collectibles Tracker section.
     *
     * Owns:
     *   - Per-item progress checkboxes (POSTs to API for logged-in users,
     *     localStorage for anonymous viewers).
     *   - Filter chips, search box, sort dropdown, hide-found toggle.
     *   - The rich-content chevron (toggling the details > 'open' attr).
     *   - Pill click handler: clicking a `[[slug]]` pill scrolls to the
     *     tracker and highlights items of that type.
     *
     * Only mounts when `#collectibles-tracker` is present on the page.
     */
    const CollectibleTracker = {
        rootEl: null,
        roadmapId: null,
        // Persistent anon-fallback storage key. Scoped per roadmap id so
        // someone playing through several games doesn't collide their found
        // sets across guides.
        _localKey() { return `pp_collectibles_found_r${this.roadmapId}`; },

        init() {
            const root = document.getElementById('collectibles-tracker');
            if (!root) return;
            this.rootEl = root;
            this.roadmapId = root.dataset.roadmapId;
            this._wireRowToggles();
            this._wireAreaAnimation();
            this._wireFilters();
            this._wireFlagFilters();
            this._wireSearch();
            this._wireHideFound();
            this._wirePillClicks();
            this._restoreAnonProgress();
            // Initial chip state: every chip has a `data-state="active"` if
            // its filter is the current selection. The "All" chip starts
            // active in the template; mirror that into the .is-active class
            // so the colored ring CSS picks it up.
            this._setActiveTypeFilter('all');
            this._recomputeTotals();
        },

        // ── Found-state row toggles ─────────────────────────────────
        // Whole row is the single click target. The rich-content panel
        // (when present) stays visible regardless of found state, so
        // re-visiting a found item to refresh memory just works. One
        // unambiguous click action per row.
        _wireRowToggles() {
            this.rootEl.querySelectorAll('.collectible-item-row-header').forEach(header => {
                header.addEventListener('click', () => {
                    const row = header.closest('.collectible-item-row');
                    if (!row) return;
                    const itemId = parseInt(row.dataset.itemId, 10);
                    const next = row.dataset.found !== '1';
                    this._setItemFound(itemId, next);
                });
                // Keyboard accessibility — Space / Enter toggle found.
                // Tabindex on the header + role=button gets us focus + AT
                // semantics; this handler completes the keyboard contract.
                header.addEventListener('keydown', (e) => {
                    if (e.key === ' ' || e.key === 'Enter') {
                        e.preventDefault();
                        header.click();
                    }
                });
            });
        },


        async _setItemFound(itemId, found, opts = {}) {
            const row = this.rootEl.querySelector(`.collectible-item-row[data-item-id="${itemId}"]`);
            if (!row) return;
            // Optimistic UI flip. Found-state visuals (border, opacity,
            // strikethrough) are CSS-driven off `[data-found]`, so we
            // only need to flip the attribute here. The decorative
            // checkbox stays synced via its `checked` property below.
            row.dataset.found = found ? '1' : '0';
            // Visual checkbox (decorative; pointer-events:none so it can't
            // be clicked directly — JS keeps its `checked` synced to the
            // row's data-found attribute).
            const cb = row.querySelector('.collectible-item-checkbox');
            if (cb) cb.checked = found;
            // aria-pressed on the row header reflects the toggled state.
            const header = row.querySelector('.collectible-item-row-header');
            if (header) {
                header.setAttribute('aria-pressed', String(found));
                const name = row.querySelector('.collectible-item-name')?.textContent?.trim() || 'item';
                header.setAttribute(
                    'aria-label',
                    found ? `Mark ${name} as not found` : `Mark ${name} as found`
                );
            }
            // Strikethrough + dim styling on `.collectible-item-name`
            // is CSS-driven off the row's `[data-found="1"]` selector —
            // no manual class toggling here.
            this._recomputeTotals();
            // When "Hide found" is active, marking an item found should
            // make it disappear immediately — re-run filters so the row
            // hides on the next paint instead of leaving a dangling
            // "found" row in a list that's supposed to hide them.
            this._applyFilters();
            // Auto-collapse the parent area if THIS toggle just made it
            // 100% complete. Only fires on the marking event (not on
            // every recompute) so a user who manually re-opens a fully
            // complete area doesn't get fought every keystroke. Caller
            // can pass `skipAutoCollapse` to suppress (used during anon
            // localStorage restore at init, where the synchronous final
            // pass handles collapse without an animation delay).
            if (found && !opts.skipAutoCollapse) this._maybeCollapseCompletedArea(row);

            // Persist. For logged-in users we POST/DELETE; for anonymous
            // viewers we stash the id in localStorage. The auth flag is
            // detected at request time — the API rejects unauth'd writes,
            // so we route around it.
            const isAnon = this.rootEl.dataset.viewerAuthenticated !== '1';
            if (isAnon) {
                this._writeAnonProgress(itemId, found);
                return;
            }
            try {
                const url = `/api/v1/collectibles/items/${itemId}/progress/`;
                if (found) {
                    await window.PlatPursuit.API.post(url, {});
                } else {
                    await window.PlatPursuit.API.delete(url);
                }
            } catch (err) {
                // Try anon fallback so the user's intent survives even if
                // the API call fails (e.g. they got logged out mid-session).
                // Rare path, but it beats silently dropping the toggle.
                this._writeAnonProgress(itemId, found);
            }
        },

        // Auto-collapse the row's owning area iff this toggle made it
        // hit 100% — a small "you finished this chapter" payoff without
        // a celebration modal. Slight delay so the user sees the
        // checkbox+strikethrough animate before the area closes.
        _maybeCollapseCompletedArea(row) {
            const area = row.closest('.collectible-area-group');
            if (!area || !area.open) return;
            const items = area.querySelectorAll('.collectible-item-row');
            const foundCount = area.querySelectorAll('.collectible-item-row[data-found="1"]').length;
            if (items.length === 0 || foundCount !== items.length) return;
            setTimeout(() => {
                if (area.open && area.querySelectorAll('.collectible-item-row').length ===
                    area.querySelectorAll('.collectible-item-row[data-found="1"]').length) {
                    this._animateAreaClose(area);
                }
            }, 450);
        },

        // ── Area details open/close animation ─────────────────────
        // Same pattern as the page-level `initDetailsAnimation`: hijack
        // summary clicks, animate the `summary + div` content's
        // max-height + opacity, then flip `details.open`. Auto-collapse
        // on chapter completion uses _animateAreaClose directly.
        _wireAreaAnimation() {
            this.rootEl.querySelectorAll('.collectible-area-group').forEach(area => {
                const summary = area.querySelector('summary');
                if (!summary || summary.dataset.areaAnimWired === '1') return;
                summary.dataset.areaAnimWired = '1';
                summary.addEventListener('click', (e) => {
                    if (area.dataset.animating === '1') {
                        e.preventDefault();
                        return;
                    }
                    e.preventDefault();
                    if (area.open) this._animateAreaClose(area);
                    else this._animateAreaOpen(area);
                });
            });
        },

        _animateAreaClose(area) {
            const content = area.querySelector('summary + div');
            if (!content) { area.open = false; return; }
            area.dataset.animating = '1';
            content.style.maxHeight = content.scrollHeight + 'px';
            content.style.overflow = 'hidden';
            requestAnimationFrame(() => {
                content.style.transition = 'max-height 220ms ease-out, opacity 180ms ease-out';
                content.style.maxHeight = '0px';
                content.style.opacity = '0';
            });
            setTimeout(() => {
                area.open = false;
                content.style.maxHeight = '';
                content.style.overflow = '';
                content.style.opacity = '';
                content.style.transition = '';
                delete area.dataset.animating;
            }, 220);
        },

        _animateAreaOpen(area) {
            const content = area.querySelector('summary + div');
            if (!content) { area.open = true; return; }
            area.dataset.animating = '1';
            area.open = true;
            content.style.overflow = 'hidden';
            content.style.maxHeight = '0px';
            content.style.opacity = '0';
            requestAnimationFrame(() => {
                content.style.transition = 'max-height 280ms ease-out, opacity 220ms ease-out';
                content.style.maxHeight = content.scrollHeight + 'px';
                content.style.opacity = '1';
            });
            setTimeout(() => {
                content.style.maxHeight = '';
                content.style.overflow = '';
                content.style.opacity = '';
                content.style.transition = '';
                delete area.dataset.animating;
            }, 280);
        },

        _readAnonProgress() {
            try {
                const raw = localStorage.getItem(this._localKey());
                return new Set(JSON.parse(raw || '[]'));
            } catch (_) {
                return new Set();
            }
        },

        _writeAnonProgress(itemId, found) {
            const set = this._readAnonProgress();
            if (found) set.add(itemId); else set.delete(itemId);
            try {
                localStorage.setItem(this._localKey(), JSON.stringify([...set]));
            } catch (_) { /* storage full / disabled — no-op */ }
        },

        _restoreAnonProgress() {
            // Server already rendered logged-in progress, so we only need
            // the localStorage path. Detect anon viewers; for them, hydrate
            // the checkboxes from storage and re-apply visuals.
            const isAnon = this.rootEl.dataset.viewerAuthenticated !== '1';
            if (!isAnon) return;
            const found = this._readAnonProgress();
            found.forEach(id => {
                const cb = this.rootEl.querySelector(
                    `.collectible-item-checkbox[data-item-id="${id}"]`
                );
                if (cb && !cb.checked) {
                    cb.checked = true;
                    // Trigger visual update via the same code path as user
                    // toggle, but skip the network call (anon path is local)
                    // AND skip the area auto-collapse animation — the
                    // initial-load snap-close pass below handles complete
                    // areas without the 450ms toggle delay.
                    this._setItemFound(id, true, { skipAutoCollapse: true });
                }
            });
            // Logged-in viewers get pre-collapsed complete areas server-
            // side; anon viewers' progress only just landed, so collapse
            // any newly-complete chapter here. Snap closed (no animation)
            // since this is initial load — no toggle event for the user
            // to react to.
            this.rootEl.querySelectorAll('.collectible-area-group[open]').forEach(area => {
                const items = area.querySelectorAll('.collectible-item-row');
                if (items.length === 0) return;
                const foundCount = area.querySelectorAll('.collectible-item-row[data-found="1"]').length;
                if (foundCount === items.length) area.open = false;
            });
        },

        // ── Total recompute (after a check/uncheck) ────────────────
        _recomputeTotals() {
            // Per-area totals + inline progress fill + TOC sync
            this.rootEl.querySelectorAll('.collectible-area-group').forEach(group => {
                const items = group.querySelectorAll('.collectible-item-row');
                const found = group.querySelectorAll('.collectible-item-row[data-found="1"]').length;
                const total = items.length;
                const foundEl = group.querySelector('.collectible-area-found-count');
                const totalEl = group.querySelector('.collectible-area-total-count');
                if (foundEl) foundEl.textContent = String(found);
                if (totalEl) totalEl.textContent = String(total);
                const fill = group.querySelector('.collectible-area-progress-fill');
                if (fill) {
                    fill.style.width = total ? `${(found / total) * 100}%` : '0%';
                }
                // Mirror the count + complete-checkmark into both TOCs
                // (sidebar + mobile). data-area-key matches the bucket
                // key used at server-render time.
                const key = group.dataset.collectibleAreaKey;
                if (!key) return;
                const isComplete = total > 0 && found === total;
                // Flip data-complete on the area itself — the success
                // tint, badge swap, and icon swap are all CSS-driven
                // off this attribute.
                group.dataset.complete = isComplete ? '1' : '0';
                document.querySelectorAll(
                    `.collectible-toc-area-count[data-area-key="${key}"]`
                ).forEach(el => {
                    el.querySelector('.collectible-toc-area-found').textContent = String(found);
                    el.querySelector('.collectible-toc-area-total').textContent = String(total);
                    el.classList.toggle('hidden', isComplete);
                });
                document.querySelectorAll(
                    `.collectible-toc-area-link[data-area-key="${key}"] .collectible-toc-area-check`
                ).forEach(el => el.classList.toggle('hidden', !isComplete));
            });
            // Per-type totals (chip + progress bar). Counts reflect the
            // underlying truth, not the current filter state.
            const typeStats = {};
            this.rootEl.querySelectorAll('.collectible-item-row').forEach(row => {
                const t = row.dataset.typeId;
                typeStats[t] = typeStats[t] || { total: 0, found: 0 };
                typeStats[t].total += 1;
                if (row.dataset.found === '1') typeStats[t].found += 1;
            });
            Object.keys(typeStats).forEach(t => {
                const { total, found } = typeStats[t];
                this.rootEl.querySelectorAll(
                    `.collectible-type-progress-label[data-type-id="${t}"], .collectible-type-progress-bar-label[data-type-id="${t}"]`
                ).forEach(el => { el.textContent = `${found}/${total}`; });
                const bar = this.rootEl.querySelector(`.collectible-type-progress-bar-fill[data-type-id="${t}"]`);
                if (bar) bar.style.width = total ? `${(found / total) * 100}%` : '0%';
                // Flip per-type complete state on every chip + progress
                // card sharing this type id (CSS in input.css paints the
                // success treatment off [data-complete="1"]).
                const typeDone = total > 0 && found === total;
                this.rootEl.querySelectorAll(
                    `.collectible-type-chip[data-type-filter="${t}"]`
                ).forEach(el => { el.dataset.complete = typeDone ? '1' : '0'; });
            });
            // Roadmap-wide total + hero banner + TOC label
            const allItems = this.rootEl.querySelectorAll('.collectible-item-row');
            const total = allItems.length;
            const allFound = this.rootEl.querySelectorAll('.collectible-item-row[data-found="1"]').length;
            const pct = total ? Math.round((allFound / total) * 100) : 0;
            // Hero banner numbers + bar
            const heroFound = this.rootEl.querySelector('.collectibles-hero-found');
            if (heroFound) heroFound.textContent = String(allFound);
            const heroPct = this.rootEl.querySelector('.collectibles-hero-percent');
            if (heroPct) heroPct.textContent = String(pct);
            const heroBar = this.rootEl.querySelector('.collectibles-hero-progress-bar');
            if (heroBar) heroBar.style.width = `${pct}%`;
            // 100% delight: the 🎉 reveals + bounces, the icon disc
            // pulses, and the "All collectibles found" tagline appears.
            // Heavy lifting is done by [data-complete="1"] CSS rules; we
            // just need to keep `hidden` off the celebration emoji and
            // flip the data attribute on the hero element below.
            const heroDone = total > 0 && allFound === total;
            const celebrate = this.rootEl.querySelector('.collectibles-hero-celebrate');
            if (celebrate) celebrate.classList.toggle('hidden', !heroDone);
            // Update the missable-remaining counter when the user toggles
            // a missable item. Hidden via CSS at 100%, so we don't need
            // a hide-toggle here — just keep the number current.
            const missableRemainEl = this.rootEl.querySelector('.collectibles-hero-missable-remaining');
            if (missableRemainEl) {
                let missableRemaining = 0;
                this.rootEl.querySelectorAll('.collectible-item-row[data-missable="1"]').forEach(r => {
                    if (r.dataset.found !== '1') missableRemaining += 1;
                });
                missableRemainEl.textContent = String(missableRemaining);
            }
            // Hero banner success treatment (gradient + icon + glow)
            // driven by [data-complete] in CSS.
            const hero = this.rootEl.querySelector('.collectibles-hero');
            if (hero) hero.dataset.complete = heroDone ? '1' : '0';
            // Legacy progress label (unused after the hero rewrite, but
            // preserved for any callers that grew dependencies on it).
            const totalLabel = this.rootEl.querySelector('.collectibles-progress-label');
            if (totalLabel) totalLabel.textContent = `${allFound}/${total}`;
            document.querySelectorAll('.toc-collectibles-found').forEach(el => {
                el.textContent = String(allFound);
            });
        },

        // ── Filter chips ───────────────────────────────────────────
        // Type-filter UI lives on the per-type progress cards (each has
        // `.collectible-type-chip`). Clicking a card scopes to that
        // type; clicking the same card again clears back to all. The
        // controls-bar "Showing: <type> · Clear" button is the second
        // escape route — surfaced only when a filter is active.
        _wireFilters() {
            this.rootEl.querySelectorAll('.collectible-type-chip').forEach(chip => {
                chip.addEventListener('click', () => {
                    const target = chip.dataset.typeFilter;
                    const alreadyActive = chip.dataset.state === 'active';
                    this._setActiveTypeFilter(alreadyActive ? 'all' : target);
                });
            });
            // Clear button in the controls bar — same effect as clicking
            // the active card a second time.
            const clearBtn = this.rootEl.querySelector('.collectibles-filter-status');
            if (clearBtn) {
                clearBtn.addEventListener('click', () => {
                    this._setActiveTypeFilter('all');
                });
            }
        },

        _setActiveTypeFilter(target) {
            this.rootEl.querySelectorAll('.collectible-type-chip').forEach(c => {
                const match = c.dataset.typeFilter === target;
                c.dataset.state = match ? 'active' : '';
                c.classList.toggle('is-active', match);
            });
            // Update the "Showing: <type> · Clear" status pill in the
            // controls bar. `inline-flex` is added/removed alongside
            // `hidden` so we don't have both display utilities applied
            // at once (Tailwind lint was complaining).
            const status = this.rootEl.querySelector('.collectibles-filter-status');
            if (status) {
                if (target === 'all') {
                    status.classList.add('hidden');
                    status.classList.remove('inline-flex');
                } else {
                    const activeChip = this.rootEl.querySelector(
                        `.collectible-type-chip[data-type-filter="${target}"]`
                    );
                    const typeName = activeChip?.dataset.typeName || 'Type';
                    const nameEl = status.querySelector('.collectibles-filter-status-name');
                    if (nameEl) nameEl.textContent = typeName;
                    status.classList.remove('hidden');
                    status.classList.add('inline-flex');
                }
            }
            this._applyFilters({ typeFilter: target });
        },

        _wireSearch() {
            const input = this.rootEl.querySelector('.collectibles-search');
            if (!input) return;
            let h = null;
            input.addEventListener('input', () => {
                clearTimeout(h);
                h = setTimeout(() => this._applyFilters(), 150);
            });
        },

        _wireHideFound() {
            const cb = this.rootEl.querySelector('.collectibles-hide-found input[type=checkbox]');
            if (!cb) return;
            cb.addEventListener('change', () => this._applyFilters());
        },

        _activeFilters() {
            const active = this.rootEl.querySelector('.collectible-type-chip[data-state="active"]');
            const typeFilter = active?.dataset.typeFilter || 'all';
            const search = (this.rootEl.querySelector('.collectibles-search')?.value || '').trim().toLowerCase();
            const hideFound = !!this.rootEl.querySelector('.collectibles-hide-found input[type=checkbox]')?.checked;
            // At most one flag-only filter active at a time (mutually
            // exclusive UI: clicking one disables the other in `_wireFlagFilters`).
            const flagOnlyBtn = this.rootEl.querySelector('.collectibles-flag-filter.is-active');
            const flagOnly = flagOnlyBtn?.dataset.flag || null;
            return { typeFilter, search, hideFound, flagOnly };
        },

        // Animate row out, then apply `hidden`. Showing is instant — a
        // fade-in costs perceived snappiness and the user just signaled
        // intent to see more. Re-entrancy: if a row is already mid-fade,
        // skip; the in-flight setTimeout will land it.
        _setRowVisible(row, visible) {
            if (visible) {
                if (row.classList.contains('hidden') || row.classList.contains('is-fading-out')) {
                    row.classList.remove('hidden');
                    row.classList.remove('is-fading-out');
                }
                return;
            }
            if (row.classList.contains('hidden') || row.classList.contains('is-fading-out')) return;
            row.classList.add('is-fading-out');
            setTimeout(() => {
                if (row.classList.contains('is-fading-out')) {
                    row.classList.add('hidden');
                    row.classList.remove('is-fading-out');
                }
            }, 220);
        },

        _applyFilters(override) {
            const f = Object.assign(this._activeFilters(), override || {});
            let anyVisible = false;
            this.rootEl.querySelectorAll('.collectible-item-row').forEach(row => {
                let visible = true;
                if (f.typeFilter !== 'all' && row.dataset.typeId !== f.typeFilter) visible = false;
                if (f.search && !(row.dataset.name || '').toLowerCase().includes(f.search)) visible = false;
                if (f.hideFound && row.dataset.found === '1') visible = false;
                if (f.flagOnly === 'missable' && row.dataset.missable !== '1') visible = false;
                if (f.flagOnly === 'dlc' && row.dataset.dlc !== '1') visible = false;
                this._setRowVisible(row, visible);
                if (visible) anyVisible = true;
            });
            // Hide whole area groups when none of their rows are visible.
            this.rootEl.querySelectorAll('.collectible-area-group').forEach(group => {
                const visibleRows = group.querySelectorAll('.collectible-item-row:not(.hidden)').length;
                group.classList.toggle('hidden', visibleRows === 0);
                // Also hide type sub-blocks within the group when their
                // rows are all filtered out.
                group.querySelectorAll('.collectible-area-type-block').forEach(block => {
                    const visibleHere = block.querySelectorAll('.collectible-item-row:not(.hidden)').length;
                    block.classList.toggle('hidden', visibleHere === 0);
                });
            });
            // Per-type progress cards are now buttons (also act as filter
            // chips). Keep them all visible — the colored ring on the
            // active one is the focus-of-attention cue, no need to hide
            // siblings. (This used to query `.collectibles-progress-strip > div`
            // when the cards were divs, which would now match nothing.)
            const empty = this.rootEl.querySelector('.collectibles-empty-state');
            if (empty) empty.classList.toggle('hidden', anyVisible);
        },

        // ── Flag filter buttons (Missable / DLC) ──────────────────
        // Replaces the old sort dropdown. Mutually exclusive: clicking
        // one toggles it on and turns the other off; clicking again
        // turns it off entirely (back to "all flags allowed"). State
        // is read from the `.is-active` class via `_activeFilters`.
        _wireFlagFilters() {
            this.rootEl.querySelectorAll('.collectibles-flag-filter').forEach(btn => {
                btn.addEventListener('click', () => {
                    const wasActive = btn.classList.contains('is-active');
                    this.rootEl.querySelectorAll('.collectibles-flag-filter').forEach(b => {
                        b.classList.remove('is-active');
                    });
                    if (!wasActive) btn.classList.add('is-active');
                    this._applyFilters();
                });
            });
        },


        // ── Pill click navigation ──────────────────────────────────
        _wirePillClicks() {
            // Clicking a `[[slug]]` pill anywhere on the page scrolls to
            // the tracker and applies that type as the active filter so
            // the viewer sees the pill's items in context.
            document.addEventListener('click', (e) => {
                const pill = e.target.closest('.collectible-pill[data-slug]');
                if (!pill) return;
                if (pill.classList.contains('is-broken')) return;
                e.preventDefault();
                const slug = pill.dataset.slug;
                const chip = this.rootEl?.querySelector(
                    `.collectible-type-chip[data-type-slug="${slug}"]`
                );
                if (chip) chip.click();
                this.rootEl?.scrollIntoView({ behavior: 'smooth', block: 'start' });
            });
        },
    };

    function init() {
        initProgressBar();
        initScrollspy();
        initTocSections();
        initTocTrophySearch();
        initTocFadeMasks();
        initMobileToc();
        initDetailsAnimation();
        initEarnedCollapse();
        initTrophyGuideControls();
        initExpandCollapse();
        initTrophyMentions();
        initTrophyLinkNavigation();
        initImageLightbox();
        CollectibleTracker.init();
        handleDeepLink();
    }

    window.PlatPursuit = window.PlatPursuit || {};
    window.PlatPursuit.RoadmapDetail = { init };
})();
