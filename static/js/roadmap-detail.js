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

            btn.addEventListener('click', (e) => {
                // Don't collapse if clicking the anchor link inside the button
                if (e.target.closest('a')) return;

                const isHidden = list.classList.contains('hidden');
                list.classList.toggle('hidden');
                if (chevron) chevron.classList.toggle('rotate-180', !isHidden);
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

        function sortAndFilter() {
            const items = Array.from(list.querySelectorAll('.trophy-guide-item'));
            const sortBy = sortSelect ? sortSelect.value : 'default';
            const hideEarned = earnedFilter ? earnedFilter.checked : false;

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

        function syncLabel(btn) {
            const details = getDetails(btn.dataset.target);
            if (!details || !details.length) return;
            const openCount = Array.from(details).filter((d) => d.open && !d.classList.contains('hidden')).length;
            const visibleCount = Array.from(details).filter((d) => !d.classList.contains('hidden')).length;
            btn.textContent = openCount > visibleCount / 2 ? 'Collapse All' : 'Expand All';
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

                btn.textContent = shouldOpen ? 'Collapse All' : 'Expand All';
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
        handleDeepLink();
    }

    window.PlatPursuit = window.PlatPursuit || {};
    window.PlatPursuit.RoadmapDetail = { init };
})();
