/**
 * MonthlyRecapManager - Handles the animated slide presentation for monthly recaps
 *
 * Features:
 * - Animated slide transitions (Spotify Wrapped style)
 * - Animated number counting (0 to N)
 * - Per-slide unique entrance animations
 * - Manual navigation (prev/next, progress dots)
 * - Confetti celebrations on key slides
 * - Share image generation on final slide
 * - Background theme selection (uses GRADIENT_THEMES from server)
 * - Slides rendered from Django templates via API
 */
class MonthlyRecapManager {
    constructor(containerId, recapData, year, month) {
        this.container = document.getElementById(containerId);
        this.data = recapData;
        this.year = year;
        this.month = month;
        this.slides = recapData.slides || [];
        this.currentSlide = 0;

        // Background selection
        this.currentBackground = 'default';
        this.backgroundStyles = this._buildBackgroundStyles();

        // Cache for fetched slide HTML
        this.slideCache = {};

        // Track which slides have been animated (prevent re-animation on revisit)
        this.animatedSlides = new Set();

        // Check for reduced motion preference
        this.prefersReducedMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches;

        // Quiz manager for interactive slides
        this.quizManager = new RecapQuizManager(this);

        // DOM elements
        this.slidesContainer = document.getElementById('recap-slides');
        this.progressDots = document.getElementById('progress-dots');
        // Pacing is DERIVED, not fixed. One beat is a single number and the next is a month of calendar
        // days; a single duration makes the short ones drag and cuts the dense ones off mid-read.
        // Beats you are meant to POKE AT are not on a clock. The calendar's platinum days open a detail
        // dialog, and a timer running behind an open modal either yanks the slide out from under you or
        // has to be paused-and-resumed in a way nobody can predict. They advance on an explicit control.
        this.MANUAL_BEATS = new Set(['activity_calendar']);
        this.BEAT_MIN_MS = 4000;
        this.BEAT_MAX_MS = 9500;
        this.READ_MS_PER_WORD = 260;      // ~230 wpm, plus room to look at the art rather than read it
        this.beatTimer = null;
        this.stageOpen = false;       // the deck does not run until the hunter enters
        this.prevBtn = document.getElementById('prev-slide');
        this.nextBtn = document.getElementById('next-slide');
        this.shareSection = document.getElementById('share-section');

        this.init();
    }

    /**
     * Build background styles from window.GRADIENT_THEMES
     */
    _buildBackgroundStyles() {
        // Check if themes are loaded from server
        if (window.GRADIENT_THEMES && Object.keys(window.GRADIENT_THEMES).length > 0) {
            return this._buildFromExternalThemes(window.GRADIENT_THEMES);
        }

        // Error: themes should always be provided by server
        console.error('GRADIENT_THEMES not loaded. Ensure gradient_themes_json template tag is included.');

        // Minimal fallback with just default theme
        return {
            'default': {
                name: 'Default',
                description: 'Default gradient',
                accentColor: '#67d1f8',
                getStyle: () => ({
                    background: 'linear-gradient(to bottom right, #2a2e34, #32363d, #2a2e34)'
                }),
                getHeaderStyle: () => ({
                    background: 'linear-gradient(135deg, rgba(103, 209, 248, 0.15) 0%, rgba(103, 209, 248, 0.05) 100%)',
                    borderColor: '#67d1f8'
                })
            }
        };
    }

    /**
     * Convert server-provided themes to the format expected by this class
     * Filters out game art themes since recaps don't have game context
     */
    _buildFromExternalThemes(themes) {
        const styles = {};

        for (const [key, theme] of Object.entries(themes)) {
            // Skip game art themes - recaps don't have game images to use
            if (theme.requiresGameImage) {
                continue;
            }

            styles[key] = {
                name: theme.name,
                description: theme.description,
                accentColor: theme.accentColor,
                getStyle: function() {
                    const result = {
                        background: theme.background,
                        backgroundSize: theme.backgroundSize || undefined,
                        backgroundPosition: theme.backgroundPosition || undefined,
                        backgroundRepeat: theme.backgroundRepeat || undefined
                    };
                    // Remove undefined properties
                    Object.keys(result).forEach(k => result[k] === undefined && delete result[k]);
                    return result;
                },
                getHeaderStyle: function() {
                    return {
                        background: theme.bannerBackground,
                        borderColor: theme.bannerBorderColor
                    };
                }
            };
        }

        return styles;
    }

    /**
     * Render background dropdown options
     */
    renderBackgroundOptions() {
        const entries = Object.entries(this.backgroundStyles);

        // Separate default from others
        const defaultEntry = entries.find(([key]) => key === 'default');
        const otherEntries = entries.filter(([key]) => key !== 'default');

        // Sort others alphabetically by name
        otherEntries.sort((a, b) => a[1].name.localeCompare(b[1].name));

        // Combine with default first
        const sortedEntries = defaultEntry ? [defaultEntry, ...otherEntries] : otherEntries;

        return sortedEntries
            .map(([key, style]) => `<option value="${key}">${style.name}</option>`)
            .join('');
    }

    /**
     * Apply selected background style to an element and its header
     */
    async applyBackground(element) {
        if (!element) return;

        const styleKey = this.currentBackground;
        const styleDef = this.backgroundStyles[styleKey];

        if (!styleDef) return;

        const styles = styleDef.getStyle();

        // Apply background styles to main element
        Object.entries(styles).forEach(([prop, value]) => {
            element.style[prop] = value;
        });

        // Apply header styles if getHeaderStyle exists
        if (styleDef.getHeaderStyle) {
            const header = element.querySelector('[data-element="recap-header"]');
            if (header) {
                const headerStyles = styleDef.getHeaderStyle();

                // Apply background
                if (headerStyles.background) {
                    header.style.background = headerStyles.background;
                }

                // Apply border color
                if (headerStyles.borderColor) {
                    header.style.borderLeftColor = headerStyles.borderColor;
                }
            }
        }
    }

    async init() {
        // Create progress dots
        this.createProgressDots();

        // Set up event listeners
        this.setupEventListeners();

        // Prefetch all slides
        await this.prefetchAllSlides();

        // Set up swipe support for mobile
        this.setupSwipeSupport();

        // The deck height tracks the viewport, so which slides overflow changes with it
        this.watchSlideOverflow();

        // Track page view
        PlatPursuit.API.post('/api/v1/tracking/site-event/', {
            event_type: 'recap_page_view',
            object_id: `${this.year}-${String(this.month).padStart(2, '0')}`
        }).catch(err => {
            console.warn('Failed to track page view:', err);
        });

        // Show first slide
        this.goToSlide(0);
    }

    /**
     * Enter the ceremony. The stage is already built and its slides already prefetched -- it renders
     * hidden on the page, so the wait happens while the hunter is reading the cover and entering is
     * instant. It moves to <body> because a `position: fixed` element inside a transformed ancestor
     * (the page-recede wrapper) is positioned against THAT ancestor, not the viewport, and would sit
     * scaled down in the corner.
     */
    openStage() {
        if (this.stageOpen) return;
        this.stageOpen = true;

        document.body.appendChild(this.container);
        this.container.hidden = false;
        void this.container.offsetWidth;              // let the un-hide land before the fade starts
        this.container.classList.add('is-in');

        this.handle = PlatPursuit.takeover(this.container, {
            focusSel: '#recap-exit',
            exitMs: 280,
            onClose: () => this.onStageClosed(),
        });

        // Warm the card while the deck plays. It is a template render (/html/), not Playwright, so this
        // is cheap -- and it means the ending transitions instantly instead of showing a spinner at the
        // exact moment the ceremony is meant to pay off.
        this.warmCard();

        // Start the deck at the top every time it is entered, so "watch it again" actually replays.
        this.goToSlide(0);
    }

    /** Fetch the share card's HTML once per stage session. Failure is silent: the deck still ends fine
     *  on the summary, and the page below still offers the card. */
    async warmCard() {
        if (this.cardHtml) return;
        try {
            const data = await PlatPursuit.API.get(`/api/v1/recap/${this.year}/${this.month}/html/`);
            this.cardHtml = (data && data.html) || '';
            // Mount immediately: the layout cost is paid here, mid-deck, where nothing is animating.
            this.mountCard();
        } catch (err) {
            this.cardHtml = '';
        }
    }

    /**
     * The ending: the summary has had its beat, and now it becomes the thing you keep.
     *
     * The card renders at its natural share dimensions, because those are what the PNG is built from --
     * re-laying it out for the screen would mean the thing you looked at and the thing you downloaded
     * were different objects. So it is SCALED to fit instead.
     */
    /**
     * Put the card in the DOM and measure it NOW, long before the ending needs it. It is laid out but
     * invisible (visibility, not display -- `display: none` skips layout entirely, which would just
     * defer the same cost to the worst possible moment).
     */
    mountCard() {
        const frame = document.getElementById('recap-card-frame');
        if (!frame || !this.cardHtml || frame.firstElementChild) return;

        frame.innerHTML = this.cardHtml;
        const card = frame.firstElementChild;
        if (!card) return;

        const fit = () => {
            const bw = frame.clientWidth, bh = frame.clientHeight;
            const cw = card.offsetWidth, ch = card.offsetHeight;
            if (!cw || !ch) return;
            // Never scale UP: past 1:1 it is a blurry enlargement of a fixed-size render.
            frame.style.setProperty('--rcx-card-scale', String(Math.min(1, bw / cw, bh / ch)));
        };
        fit();
        this._fitCard = PlatPursuit.debounce(fit, 120);
        window.addEventListener('resize', this._fitCard);
    }

    showCardScene() {
        const scene = document.getElementById('recap-card');
        if (!scene || !this.cardHtml) return;

        this.stopBeatTimer();
        this.mountCard();                       // no-op if it is already mounted, which it should be
        scene.setAttribute('aria-hidden', 'false');
        // The only work in this frame: one class. `is-ending` lifts the summary out of the way and the
        // card rises into the space it leaves -- one composition recomposing itself.
        this.container.classList.add('is-ending');

        const dl = document.getElementById('recap-download');
        if (dl && !dl._wired) {
            dl._wired = true;
            dl.addEventListener('click', () => this.downloadCard());
        }
        const custom = document.getElementById('recap-customise');
        if (custom && !custom._wired) {
            custom._wired = true;
            // Customising is a page job, not a ceremony job: leave, and land on the picker below.
            custom.addEventListener('click', () => {
                this.seenSummary = true;
                if (this.handle) this.handle.close();
            });
        }
    }

    /** The one expensive call in the whole flow -- everything else is a template render. */
    downloadCard() {
        const theme = this.currentBackground && this.currentBackground !== 'default'
            ? `&theme=${encodeURIComponent(this.currentBackground)}` : '';
        window.location.href =
            `/api/v1/recap/${this.year}/${this.month}/png/?image_format=landscape${theme}`;
    }

    /** takeover() has already removed the stage and restored focus; put the page back in order. */
    onStageClosed() {
        this.stageOpen = false;
        this.stopBeatTimer();
        clearTimeout(this._cardTimer);
        this.container.classList.remove('is-ending');
        if (this._fitCard) { window.removeEventListener('resize', this._fitCard); this._fitCard = null; }
        this.handle = null;
        const shareSection = document.getElementById('share-section');
        if (shareSection && this.seenSummary) {
            shareSection.classList.add('visible');
            shareSection.scrollIntoView({ behavior: 'smooth', block: 'start' });
        }
    }

    /**
     * The segmented timer, one bar per beat. A row of dots can say "you are on 3 of 16"; only a filling
     * bar can say "this is paced, and it is moving" -- which is the difference between a carousel widget
     * and a story. Bars are not clickable: on a paced deck, jumping to an arbitrary beat is meaningless,
     * and the tap zones already own moving.
     */
    createProgressDots() {
        this.progressDots.innerHTML = '';
        this.bars = this.slides.map(() => {
            const bar = document.createElement('span');
            bar.className = 'rcx__bar';
            bar.appendChild(document.createElement('i'));
            this.progressDots.appendChild(bar);
            return bar;
        });
    }

    /**
     * The colour world. Read off the slide's OWN `.rcp--accent-*` modifier rather than a type->colour
     * map in here, so the template stays the single source: a slide that changes its accent changes the
     * whole stage with it, and the two can never disagree.
     */
    applyBeatAccent(slideEl) {
        const shell = slideEl && slideEl.querySelector('.rcp');
        const map = { 'rcp--accent-secondary': 'secondary', 'rcp--accent-warm': 'accent',
                      'rcp--accent-success': 'success' };
        let accent = 'primary';
        if (shell) {
            Object.keys(map).some((cls) => shell.classList.contains(cls) && (accent = map[cls]));
        }
        this.container.dataset.accent = accent;
    }

    /** Paint the bars for the active beat and restart its fill from zero. */
    paintBars(index) {
        if (!this.bars) return;
        this.bars.forEach((bar, n) => {
            bar.classList.remove('is-live', 'is-done');
            if (n < index) bar.classList.add('is-done');
        });
        const live = this.bars[index];
        if (!live) return;
        if (this.prefersReducedMotion) { live.classList.add('is-done'); return; }

        // Rewind, then run. The inline width MUST be cleared before `is-live` lands: an inline style
        // beats the `.is-live i { width: 100% }` class rule, so leaving `width: 0` on the element pinned
        // the bar at zero for the entire beat and the timer never appeared to move.
        const fill = live.querySelector('i');
        fill.style.transition = 'none';
        fill.style.width = '0';
        void fill.offsetWidth;            // commit the rewind before re-enabling the transition
        fill.style.transition = '';
        fill.style.width = '';            // hand the width back to the stylesheet
        live.classList.add('is-live');
    }

    setupEventListeners() {
        this.prevBtn.addEventListener('click', () => {
            this.prevSlide();
        });

        this.nextBtn.addEventListener('click', () => {
            this.nextSlide();
        });

        const begin = document.getElementById('recap-begin');
        if (begin) begin.addEventListener('click', () => this.openStage());

        const exit = document.getElementById('recap-exit');
        if (exit) exit.addEventListener('click', () => this.handle && this.handle.close());

        // Coming back for the card should not mean sitting through the deck again.
        const quick = document.getElementById('recap-quick-download');
        if (quick) quick.addEventListener('click', () => this.downloadCard());

        const advance = document.getElementById('recap-advance');
        if (advance) advance.addEventListener('click', (e) => {
            if (e.target.closest('[data-advance]')) this.nextSlide();
        });

        const done = document.getElementById('recap-done');
        if (done) done.addEventListener('click', () => this.handle && this.handle.close());

        const share = document.getElementById('recap-share');
        if (share) share.addEventListener('click', () => {
            this.seenSummary = true;
            if (this.handle) this.handle.close();     // onStageClosed opens and scrolls to the panel
        });

        // Leaving the tab desynced the deck: background tabs throttle setTimeout (clamped to a second or
        // more) while the CSS transition driving the timer bar is throttled on a different schedule, so
        // the two came back disagreeing -- a bar sitting full against a beat that had not advanced, or a
        // beat advancing under a bar that had barely moved.
        //
        // Rather than trying to reconcile two clocks that drifted apart, the current beat simply RESTARTS
        // on return: bar and timer begin from the same instant, so they cannot disagree. Restarting is
        // also the honest behaviour -- you were not looking, so you get the beat back.
        document.addEventListener('visibilitychange', () => {
            if (!this.stageOpen) return;
            if (document.hidden) {
                this.stopBeatTimer();
                this.container.classList.add('is-held');    // freezes the bar where it stands
            } else {
                this.container.classList.remove('is-held');
                this.paintBars(this.currentSlide);
                this.startBeatTimer();
            }
        });

        // Hold to pause -- the affordance every story UI has. Pointer events rather than touch, so a
        // mouse press pauses too.
        ['pointerdown', 'pointerup', 'pointercancel', 'pointerleave'].forEach((evt) => {
            this.container.addEventListener(evt, () => {
                const held = evt === 'pointerdown';
                this.container.classList.toggle('is-held', held);
                if (held) this.stopBeatTimer(); else this.startBeatTimer();
            });
        });

        // Keyboard navigation. Bound at the DOCUMENT, so it has to yield: to anyone typing (the share
        // section below the deck has a background <select>, and caret movement inside a field must not
        // also flip the slide), to an open dialog (the platinum detail owns Escape and arrow keys while
        // it is up), and to modified presses, which belong to the browser.
        document.addEventListener('keydown', (e) => {
            if (e.key !== 'ArrowLeft' && e.key !== 'ArrowRight') return;
            if (e.altKey || e.ctrlKey || e.metaKey) return;
            if (document.querySelector('dialog[open]')) return;

            const el = e.target;
            if (el && (el.isContentEditable ||
                       ['INPUT', 'TEXTAREA', 'SELECT'].includes(el.tagName))) return;

            if (e.key === 'ArrowLeft') {
                this.prevSlide();
            } else {
                this.nextSlide();
            }
        });
    }

    /**
     * Set up swipe support for mobile navigation
     */
    setupSwipeSupport() {
        let touchStartX = 0;
        let touchStartY = 0;
        let touchEndX = 0;
        let touchEndY = 0;

        const minSwipeDistance = 50;
        const maxVerticalDistance = 100;

        this.slidesContainer.addEventListener('touchstart', (e) => {
            touchStartX = e.changedTouches[0].screenX;
            touchStartY = e.changedTouches[0].screenY;
        }, { passive: true });

        this.slidesContainer.addEventListener('touchend', (e) => {
            touchEndX = e.changedTouches[0].screenX;
            touchEndY = e.changedTouches[0].screenY;

            const deltaX = touchEndX - touchStartX;
            const deltaY = Math.abs(touchEndY - touchStartY);

            // Only register as swipe if horizontal movement is significant
            // and vertical movement is minimal (not scrolling)
            if (Math.abs(deltaX) > minSwipeDistance && deltaY < maxVerticalDistance) {
                if (deltaX < 0) {
                    // Swipe left - go to next slide
                    this.nextSlide();
                } else {
                    // Swipe right - go to previous slide
                    this.prevSlide();
                }
            }
        }, { passive: true });
    }

    async prefetchAllSlides() {
        // Remove loading slide
        const loadingSlide = document.getElementById('loading-slide');

        // Fetch all slides in parallel
        const fetchPromises = this.slides.map((slide, index) =>
            this.fetchSlideHTML(slide.type, index)
        );

        try {
            await Promise.all(fetchPromises);

            // Remove loading slide after all fetches complete
            if (loadingSlide) loadingSlide.remove();

            // Render slides from cache
            this.renderAllSlides();
        } catch (error) {
            console.error('Error prefetching slides:', error);
            // Still remove loading and show what we have
            if (loadingSlide) loadingSlide.remove();
            this.renderAllSlides();
        }
    }

    async fetchSlideHTML(slideType, index) {
        const cacheKey = `${slideType}_${index}`;

        // Check cache
        if (this.slideCache[cacheKey]) {
            return this.slideCache[cacheKey];
        }

        try {
            const data = await PlatPursuit.API.get(`/api/v1/recap/${this.year}/${this.month}/slide/${slideType}/`);
            this.slideCache[cacheKey] = data.html;
            return data.html;
        } catch (error) {
            console.error(`Error fetching slide ${slideType}:`, error);
            // Return fallback HTML
            return this.getFallbackSlideHTML(slideType);
        }
    }

    getFallbackSlideHTML(slideType) {
        return `
            <div class="text-center py-8">
                <p class="text-base-content/70">Error loading slide</p>
            </div>
        `;
    }

    renderAllSlides() {
        // Clear container
        this.slidesContainer.innerHTML = '';

        // Render each slide
        this.slides.forEach((slide, index) => {
            const slideEl = document.createElement('div');
            slideEl.className = 'recap-slide';
            slideEl.dataset.index = index;

            const cacheKey = `${slide.type}_${index}`;
            slideEl.innerHTML = this.slideCache[cacheKey] || this.getFallbackSlideHTML(slide.type);

            this.slidesContainer.appendChild(slideEl);
        });

        // Set up share button listeners after slides are rendered
        this.setupShareButtons();

        // Check for overflow on each slide after a brief delay for rendering
        setTimeout(() => this.checkSlideOverflow(), 100);
    }

    /**
     * `.has-overflow` top-aligns a slide that is taller than the deck so it can be scrolled to. The deck's
     * height is `clamp(520px, 100vh - 360px, 720px)`, so it MOVES with the viewport -- rotating a phone or
     * dragging a desktop window changes which slides overflow. Checking only once after render left slides
     * centred and clipped at the new size, with the overflowing top unreachable.
     */
    watchSlideOverflow() {
        const recheck = PlatPursuit.debounce(() => this.checkSlideOverflow(), 150);
        window.addEventListener('resize', recheck);
        if (window.visualViewport) {
            // Mobile browser chrome collapsing on scroll changes 100vh without firing `resize`.
            window.visualViewport.addEventListener('resize', recheck);
        }
    }

    checkSlideOverflow() {
        const slideEls = this.slidesContainer.querySelectorAll('.recap-slide');
        slideEls.forEach(el => {
            // Check if content overflows the slide height
            if (el.scrollHeight > el.clientHeight) {
                el.classList.add('has-overflow');
            } else {
                el.classList.remove('has-overflow');
            }
        });
    }

    /** Pacing. A quiz holds the deck until it is answered -- the gate already exists, this just stops
     *  the clock so the timer bar does not keep running against a beat that cannot advance. */
    startBeatTimer() {
        this.stopBeatTimer();
        if (this.prefersReducedMotion || !this.stageOpen) return;
        const type = this.slides[this.currentSlide]?.type;
        const gated = this.quizManager.isQuizSlide(type) && !this.quizManager.canNavigate(type);
        const manual = this.MANUAL_BEATS.has(type);
        // A dialog open over the stage stops the clock too -- whatever opened it owns the moment.
        const blocked = gated || manual || Boolean(document.querySelector('dialog[open]'));

        this.container.classList.toggle('is-waiting', blocked);
        this.container.classList.toggle('is-manual', manual);
        const advance = document.getElementById('recap-advance');
        if (advance) advance.hidden = !manual || this.currentSlide >= this.slides.length - 1;

        if (blocked || this.currentSlide >= this.slides.length - 1) return;

        const slideEl = this.slidesContainer.querySelectorAll('.recap-slide')[this.currentSlide];
        const ms = this.beatDuration(slideEl);
        this.container.style.setProperty('--rcx-beat', ms + 'ms');   // the timer bar runs for exactly this long
        this.beatTimer = setTimeout(() => this.nextSlide(), ms);
    }

    /**
     * How long this beat gets: reading time for its words, plus a beat per thing to look at (a cover, a
     * medallion, a calendar cell), clamped so nothing races or stalls. Measured off the rendered slide,
     * so a beat that grows new content is paced correctly without anyone updating a table.
     */
    beatDuration(slideEl) {
        if (!slideEl) return this.BEAT_MIN_MS;
        const words = (slideEl.textContent || '').trim().split(/\s+/).filter(Boolean).length;
        const things = slideEl.querySelectorAll('img, .scard, .pp-med, .activity-dot, .rcp__chip').length;
        const ms = 1400 + words * this.READ_MS_PER_WORD + Math.min(things, 24) * 90;
        return Math.round(Math.min(this.BEAT_MAX_MS, Math.max(this.BEAT_MIN_MS, ms)));
    }

    stopBeatTimer() {
        if (this.beatTimer) { clearTimeout(this.beatTimer); this.beatTimer = null; }
    }

    goToSlide(index) {
        if (index < 0 || index >= this.slides.length) return;
        // Direction drives the transition: the outgoing beat leaves the way you sent it.
        this.container.classList.toggle('is-back', index < this.currentSlide);
        this.container.classList.add('is-underway');

        // Update current slide
        const prevIndex = this.currentSlide;
        this.currentSlide = index;

        // Update slide visibility
        const slideEls = this.slidesContainer.querySelectorAll('.recap-slide');
        slideEls.forEach((el, i) => {
            if (i === index) {
                el.classList.add('active');
                el.classList.remove('exiting');
            } else if (i === prevIndex) {
                el.classList.remove('active');
                el.classList.add('exiting');
                // Remove exiting class after animation
                setTimeout(() => el.classList.remove('exiting'), 600);
            } else {
                el.classList.remove('active', 'exiting');
            }
        });

        this.paintBars(index);
        this.startBeatTimer();
        this.applyBeatAccent(slideEls[index]);


        // Trigger slide-specific animations (only on first visit)
        const slideType = this.slides[index].type;
        const slideEl = slideEls[index];
        if (!this.animatedSlides.has(index)) {
            this.animatedSlides.add(index);
            // Small delay to let the slide transition start
            setTimeout(() => {
                // Interactivity FIRST and unconditionally: triggerSlideAnimations early-returns under
                // prefers-reduced-motion, and the calendar's platinum-day click handlers used to be
                // registered inside its animation loop -- so asking for less motion silently removed a
                // feature rather than just its movement.
                this.wireSlideInteractions(slideEl, slideType);
                this.triggerSlideAnimations(slideEl, slideType);

                // Initialize quiz if this is a quiz slide
                if (this.quizManager.isQuizSlide(slideType)) {
                    this.quizManager.initQuizSlide(slideEl, slideType);
                    // Update buttons AFTER the quiz is wired up
                    this.updateNavigationButtons();
                }
            }, 100);
        } else if (this.quizManager.isQuizSlide(slideType)) {
            // For revisited quiz slides, still need to update buttons based on answered state
            this.updateNavigationButtons();
        }

        // Trigger celebration effects on specific slides
        this.triggerSlideEffects(this.slides[index]);

        // The closing beat hands over: the stage offers Share / Done, and the panel below opens once
        // they leave. Revealing it mid-ceremony was pointless -- it sits behind a full-screen takeover.
        const card = document.getElementById('recap-card');
        if (slideType === 'summary') {
            this.seenSummary = true;
            this.loadSharePreview();
            // Hold on the summary for its own beat, then hand over. Cleared whenever the beat changes, so
            // stepping back off the summary cannot strand a transition that then fires over another beat.
            clearTimeout(this._cardTimer);
            this._cardTimer = setTimeout(() => {
                if (this.slides[this.currentSlide]?.type === 'summary') this.showCardScene();
            }, this.beatDuration(slideEls[index]));
        } else {
            clearTimeout(this._cardTimer);
            this.container.classList.remove('is-ending');
            if (card) card.setAttribute('aria-hidden', 'true');
            if (this._fitCard) { window.removeEventListener('resize', this._fitCard); this._fitCard = null; }
        }

        // Update navigation button states for non-quiz slides
        // Quiz slides handle their own button updates after initialization
        if (!this.quizManager.isQuizSlide(slideType)) {
            this.updateNavigationButtons();
        }
    }

    /**
     * Per-slide interactivity. Runs for EVERY viewer, motion preference included -- anything registered
     * here must be behaviour the slide needs to work, not decoration.
     */
    wireSlideInteractions(slideEl, slideType) {
        if (!slideEl) return;
        if (slideType === 'quiz_score') {
            this.fillQuizScore(slideEl);
        }
        if (slideType === 'activity_calendar') {
            slideEl.querySelectorAll('.rcp-cal__cell--plat').forEach((day) => {
                day.addEventListener('click', () => this.showPlatinumDetails(day));
            });
        }
    }

    /**
     * The payoff slide. The deck has always computed this score and never shown it. It has no server
     * payload -- it is whatever the hunter answered THIS sitting -- so it is filled in here, and it is
     * filled in `wireSlideInteractions` rather than an animator so that a reduced-motion viewer still
     * gets the numbers.
     */
    fillQuizScore(slideEl) {
        const score = this.quizManager.getScore();
        // Quizzes gate navigation until answered, so reaching this slide means every quiz before it has
        // a result. Guard anyway: a deck with no quizzes drops this slide server-side, and a future
        // change to the gate should degrade to a blank slide, not a broken one.
        if (!score) return;

        const set = (sel, text) => {
            const el = slideEl.querySelector(sel);
            if (el) el.textContent = text;
        };
        set('[data-score-correct]', String(score.correct));
        set('[data-score-total]', `/ ${score.total}`);

        const pips = slideEl.querySelector('[data-score-pips]');
        if (pips) {
            pips.replaceChildren(...this.quizManager.orderedResults().map((hit, i) => {
                const pip = document.createElement('span');
                pip.className = `rcp-score__pip ${hit ? 'is-hit' : 'is-miss'} stagger-item`;
                pip.style.animationDelay = `${300 + i * 120}ms`;
                return pip;
            }));
        }

        set('[data-score-verdict]', this.quizScoreVerdict(score));
    }

    /** Graded, because "you knew yourself" and "your own month surprised you" are different sentences. */
    quizScoreVerdict(score) {
        if (score.total === 0) return '';
        if (score.correct === score.total) return 'You know exactly what you did. Every single one.';
        if (score.percentage >= 50) return 'You had a decent sense of your own month.';
        if (score.correct > 0) return 'Your own month managed to surprise you.';
        return 'Not one. Your month was stranger than you thought.';
    }

    /**
     * Trigger slide-specific animations when a slide becomes active
     */
    triggerSlideAnimations(slideEl, slideType) {
        if (!slideEl || this.prefersReducedMotion) return;

        // Animate count-up numbers
        this.animateCountUpElements(slideEl);

        // Per-slide specific animations
        switch (slideType) {
            case 'total_trophies':
                this.animateTotalTrophiesSlide(slideEl);
                break;
            case 'platinums':
                this.animatePlatinumsSlide(slideEl);
                break;
            case 'rarest_trophy':
                this.animateRarestTrophySlide(slideEl);
                break;
            case 'activity_calendar':
                this.animateCalendarSlide(slideEl);
                break;
            case 'badges':
                this.animateBadgesSlide(slideEl);
                break;
        }
    }

    /**
     * Count-ups. The deck marks its animated figures with `data-countup` and delegates to the shared
     * PlatPursuit.countUp, which already owns the easing, locale formatting, decimal support and the
     * reduced-motion short-circuit. The private copy this replaced re-derived the target by stripping
     * non-digits out of textContent, so it could not represent a decimal earn rate or a negative change.
     *
     * Sign and unit live in SIBLING elements in the templates, because countUp overwrites textContent.
     */
    animateCountUpElements(slideEl) {
        slideEl.querySelectorAll('[data-countup]').forEach((el) => PlatPursuit.countUp(el, 1200));
    }

    /**
     * Total trophies slide - number scales in, then stats fan in
     */
    animateTotalTrophiesSlide(slideEl) {
        const statsSection = slideEl.querySelector('.rcp-tiers');
        if (statsSection) {
            // Initially hide stats
            statsSection.style.opacity = '0';
            statsSection.style.transform = 'translateY(20px)';

            // Reveal stats after count-up completes
            setTimeout(() => {
                statsSection.style.transition = 'all 0.6s cubic-bezier(0.34, 1.56, 0.64, 1)';
                statsSection.style.opacity = '1';
                statsSection.style.transform = 'translateY(0)';
            }, 1600);
        }
    }

    /**
     * Platinums slide - cards cascade in diagonally
     */
    animatePlatinumsSlide(slideEl) {
        const cards = slideEl.querySelectorAll('.stagger-item');
        cards.forEach((card, i) => {
            card.style.opacity = '0';
            card.style.transform = 'translate(30px, 30px)';

            setTimeout(() => {
                card.style.transition = 'all 0.5s cubic-bezier(0.34, 1.56, 0.64, 1)';
                card.style.opacity = '1';
                card.style.transform = 'translate(0, 0)';
            }, 200 + (i * 150));
        });
    }

    /**
     * Rarest trophy slide - spotlight reveal effect
     */
    animateRarestTrophySlide() {
        // Nothing. The trophy icon carries `animate-spotlight-reveal` from the template and the rarity
        // stamp arrives on the shared `.rcp > *` cascade as the last child.
        //
        // This used to hold the stamp back with inline styles, which ran a frame AFTER the browser had
        // already painted it -- so it appeared, vanished, and then animated in. A JS entrance can never
        // beat the paint of the markup it is trying to hide; that has to come from CSS, and the cascade
        // already does it.
    }

    /**
     * Activity calendar slide - dots fill in sequentially
     */
    animateCalendarSlide(slideEl) {
        const days = slideEl.querySelectorAll('.rcp-cal__cell');
        const delay = 30; // ms between each day

        days.forEach((day, index) => {
            const dot = day.querySelector('.activity-dot');
            if (!dot) return;

            // Start invisible and small
            dot.style.transform = 'scale(0)';
            dot.style.opacity = '0';

            setTimeout(() => {
                dot.style.transition = 'transform 0.3s cubic-bezier(0.34, 1.56, 0.64, 1), opacity 0.3s ease';
                dot.style.transform = 'scale(1)';
                dot.style.opacity = '1';

                // Add pop effect for high-activity days
                const levelClass = Array.from(dot.classList).find(c => c.startsWith('activity-level-'));
                const level = levelClass ? parseInt(levelClass.replace('activity-level-', '')) : 0;
                if (level >= 3) {
                    setTimeout(() => {
                        dot.style.transform = 'scale(1.15)';
                        setTimeout(() => {
                            dot.style.transform = 'scale(1)';
                        }, 150);
                    }, 100);
                }
            }, index * delay);

            // NB: the platinum-day click handler is NOT registered here. It lives in
            // wireSlideInteractions, which runs regardless of motion preference.
        });
    }

    /**
     * Show platinum trophy details when calendar day is clicked
     */
    showPlatinumDetails(dayElement) {
        const platinumsData = dayElement.dataset.platinums;
        if (!platinumsData) {
            return;
        }

        let platinums;
        try {
            platinums = JSON.parse(platinumsData);
        } catch (error) {
            console.error('Could not parse platinum data for the calendar day:', error);
            return;
        }
        if (!Array.isArray(platinums) || !platinums.length) return;

        const esc = PlatPursuit.HTMLUtils.escape;
        const day = dayElement.dataset.day;

        // A native <dialog> rather than a hand-rolled overlay. showModal() brings the top layer, the
        // backdrop, focus trapping and Escape-to-close with it -- the version this replaced registered its
        // own document-level Escape handler and only removed it when Escape fired, so closing by backdrop
        // or by the X button leaked a listener that kept firing on every later keypress.
        //
        // Game and trophy names are PSN-sourced text and are ESCAPED. They used to be interpolated raw
        // into innerHTML.
        const dialog = document.createElement('dialog');
        dialog.className = 'rcp-plats';
        dialog.innerHTML = `
            <div class="rcp-plats__head">
                <div>
                    <h3 class="rcp-plats__day">Day ${esc(day)}</h3>
                    <p class="rcp-plats__count">${platinums.length} platinum${platinums.length > 1 ? 's' : ''} earned</p>
                </div>
                <button type="button" class="rcp-plats__close" aria-label="Close">
                    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"
                         stroke-linecap="round" aria-hidden="true"><path d="M6 18L18 6M6 6l12 12" /></svg>
                </button>
            </div>
            <ul class="rcp-plats__list">
                ${platinums.map((plat) => `
                    <li class="rcp-plats__item">
                        ${plat.icon_url ? `<img class="rcp-plats__icon" src="${esc(plat.icon_url)}" alt="" loading="lazy" draggable="false" />` : ''}
                        <div class="rcp-plats__text">
                            <p class="rcp-plats__game">${esc(plat.game_name || '')}</p>
                            <p class="rcp-plats__trophy">${esc(plat.trophy_name || '')}</p>
                        </div>
                        <span class="rcp-plats__seal" aria-hidden="true">
                            <svg viewBox="0 0 24 24" fill="currentColor"><path d="M12 2L15.09 8.26L22 9.27L17 14.14L18.18 21.02L12 17.77L5.82 21.02L7 14.14L2 9.27L8.91 8.26L12 2Z"/></svg>
                        </span>
                    </li>
                `).join('')}
            </ul>
        `;

        dialog.addEventListener('close', () => {
            dialog.remove();
            this.startBeatTimer();      // the stage stopped its clock while this was up
        });
        dialog.querySelector('.rcp-plats__close').addEventListener('click', () => dialog.close());
        // Backdrop click: the ::backdrop pseudo-element reports the dialog itself as the target.
        dialog.addEventListener('click', (e) => { if (e.target === dialog) dialog.close(); });

        this.stopBeatTimer();
        // Into the STAGE, not the body: it belongs to the ceremony, so the takeover can tell it is open
        // (and hand it Escape) and it is torn down with the stage if the hunter leaves while it is up.
        // showModal() promotes it to the top layer regardless of where it sits in the DOM, so being
        // inside a position:fixed ancestor does not affect how it is centred.
        (this.container || document.body).appendChild(dialog);
        dialog.showModal();
    }

    /**
     * Badges slide - stamp effect for each badge
     */
    animateBadgesSlide(slideEl) {
        const badges = slideEl.querySelectorAll('.stagger-item');
        badges.forEach((badge, i) => {
            badge.style.opacity = '0';
            badge.style.transform = 'scale(2) rotate(10deg)';

            setTimeout(() => {
                badge.style.transition = 'all 0.4s cubic-bezier(0.34, 1.56, 0.64, 1)';
                badge.style.opacity = '1';
                badge.style.transform = 'scale(1) rotate(0deg)';
            }, 300 + (i * 200));
        });
    }

    async loadSharePreview() {
        const shareContent = document.getElementById('share-content');
        if (!shareContent || shareContent._loaded) return;

        shareContent.innerHTML = '<div class="flex justify-center py-8"><span class="loading loading-spinner loading-lg text-primary"></span></div>';

        try {
            // Fetch landscape preview
            const data = await PlatPursuit.API.get(`/api/v1/recap/${this.year}/${this.month}/html/`);

            // Create preview with background selector and scaled-down share card
            shareContent.innerHTML = `
                <div class="flex flex-col items-center gap-6">
                    <!-- Background Selector -->
                    <div class="flex flex-col sm:flex-row items-center gap-3 w-full max-w-xl">
                        <label for="recap-background-select" class="text-sm text-base-content/70 whitespace-nowrap">Background:</label>
                        <select id="recap-background-select" class="select select-sm select-bordered bg-base-200 flex-1">
                            ${this.renderBackgroundOptions()}
                        </select>
                        <button type="button" id="open-recap-color-grid" class="btn btn-sm btn-primary btn-square" title="Choose from grid">
                            <svg xmlns="http://www.w3.org/2000/svg" class="h-5 w-5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                                <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M4 6a2 2 0 012-2h2a2 2 0 012 2v2a2 2 0 01-2 2H6a2 2 0 01-2-2V6zM14 6a2 2 0 012-2h2a2 2 0 012 2v2a2 2 0 01-2 2h-2a2 2 0 01-2-2V6zM4 16a2 2 0 012-2h2a2 2 0 012 2v2a2 2 0 01-2 2H6a2 2 0 01-2-2v-2zM14 16a2 2 0 012-2h2a2 2 0 012 2v2a2 2 0 01-2 2h-2a2 2 0 01-2-2v-2z" />
                            </svg>
                        </button>
                    </div>

                    <!-- Scaled Preview Container -->
                    <div class="w-full" style="max-width: 600px;">
                        <div class="relative rounded-lg border-2 border-primary/30 shadow-lg overflow-hidden" style="aspect-ratio: 1200 / 630;">
                            <div class="absolute inset-0" style="transform-origin: top left;">
                                <div id="share-preview-inner" style="width: 1200px; height: 630px; transform-origin: top left;">
                                    ${data.html}
                                </div>
                            </div>
                        </div>
                    </div>

                    <!-- Download Button -->
                    <div class="flex justify-center">
                        <button id="download-recap-image" class="btn btn-primary gap-2" data-year="${this.year}" data-month="${this.month}">
                            <svg xmlns="http://www.w3.org/2000/svg" class="h-5 w-5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                                <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M4 16v1a3 3 0 003 3h10a3 3 0 003-3v-1m-4-4l-4 4m0 0l-4-4m4 4V4" />
                            </svg>
                            Download Image
                        </button>
                    </div>
                </div>
            `;

            // Apply dynamic scaling based on container width
            this.scaleSharePreview();

            shareContent._loaded = true;
            this.setupShareButtons();

            // Handle window resize
            window.addEventListener('resize', () => this.scaleSharePreview());

        } catch (error) {
            console.error('Error loading share preview:', error);
            shareContent.innerHTML = `
                <div class="text-center py-4">
                    <p class="text-base-content/70 mb-4">Preview unavailable</p>
                    <div class="flex justify-center">
                        <button id="download-recap-image" class="btn btn-primary gap-2" data-year="${this.year}" data-month="${this.month}">
                            <svg xmlns="http://www.w3.org/2000/svg" class="h-5 w-5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                                <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M4 16v1a3 3 0 003 3h10a3 3 0 003-3v-1m-4-4l-4 4m0 0l-4-4m4 4V4" />
                            </svg>
                            Download Image
                        </button>
                    </div>
                </div>
            `;
            this.setupShareButtons();
        }
    }

    scaleSharePreview() {
        const previewInner = document.getElementById('share-preview-inner');
        if (!previewInner) return;

        const container = previewInner.closest('.relative');
        if (!container) return;

        // Calculate scale based on container width vs original card width (1200px)
        const containerWidth = container.offsetWidth;
        const scale = containerWidth / 1200;

        previewInner.style.transform = `scale(${scale})`;
    }

    /**
     * Deliberately empty, and kept as a seam rather than deleted so the call site stays honest about
     * where a per-beat effect WOULD go.
     *
     * This used to fire side-confetti on both the platinums and the summary beats. Anti-reference #4:
     * casual mobile games over-celebrate everything, and we celebrate the meaningful moments and let the
     * rest breathe. Two confetti bursts in one deck is not a celebration, it is a texture -- and on a
     * paced ceremony the beats now carry their own weight through choreography instead.
     */
    triggerSlideEffects() {}

    setupShareButtons() {
        // Download button
        const downloadBtn = document.getElementById('download-recap-image');
        if (downloadBtn && !downloadBtn._hasListener) {
            downloadBtn.addEventListener('click', () => {
                // Theme nudge: prompt if still on default
                if (this.currentBackground === 'default') {
                    this._showThemeNudge();
                    return;
                }
                this._trackAndDownload();
            });
            downloadBtn._hasListener = true;
        }

        // Background selector
        const backgroundSelect = document.getElementById('recap-background-select');
        if (backgroundSelect && !backgroundSelect._hasListener) {
            backgroundSelect.addEventListener('change', (e) => {
                this.currentBackground = e.target.value;
                this.updatePreviewBackground();
            });
            backgroundSelect._hasListener = true;
        }

        // Color grid modal button
        const colorGridBtn = document.getElementById('open-recap-color-grid');
        if (colorGridBtn && !colorGridBtn._hasListener) {
            colorGridBtn.addEventListener('click', () => this.openColorModal());
            colorGridBtn._hasListener = true;
        }
    }

    _trackAndDownload() {
        PlatPursuit.API.post('/api/v1/tracking/site-event/', {
            event_type: 'recap_image_download',
            object_id: `${this.year}-${String(this.month).padStart(2, '0')}`
        }).catch(err => {
            console.error('[RECAP] Failed to track download:', err);
        });
        this.downloadShareImage();
    }

    _showThemeNudge() {
        const modal = document.getElementById('theme-nudge-modal');
        if (!modal) {
            this._trackAndDownload();
            return;
        }

        const browseBtn = document.getElementById('nudge-browse-themes');
        const continueBtn = document.getElementById('nudge-continue');

        const newBrowse = browseBtn?.cloneNode(true);
        const newContinue = continueBtn?.cloneNode(true);
        browseBtn?.replaceWith(newBrowse);
        continueBtn?.replaceWith(newContinue);

        newBrowse?.addEventListener('click', () => {
            modal.close();
            this.openColorModal();
        });

        newContinue?.addEventListener('click', () => {
            modal.close();
            this._trackAndDownload();
        });

        modal.showModal();
    }

    /**
     * Update preview background when theme changes
     */
    updatePreviewBackground() {
        const previewInner = document.getElementById('share-preview-inner');
        if (!previewInner) return;

        const shareContent = previewInner.querySelector('.share-image-content');
        if (shareContent) {
            this.applyBackground(shareContent);
        }
    }

    /**
     * Open color grid modal for visual theme selection
     */
    openColorModal() {
        if (!window.PlatPursuit?.getColorGridModal) {
            console.warn('ColorGridModal not initialized');
            return;
        }

        const colorModal = window.PlatPursuit.getColorGridModal();

        // Open modal with current background and callback
        colorModal.open(this.currentBackground, (selectedTheme) => {
            // Update internal state
            this.currentBackground = selectedTheme;

            // Sync dropdown to match selection
            const selectElement = document.getElementById('recap-background-select');
            if (selectElement) {
                const optionExists = Array.from(selectElement.options).some(opt => opt.value === selectedTheme);
                if (optionExists) {
                    selectElement.value = selectedTheme;
                } else {
                    selectElement.value = 'default';
                }
            }

            // Update preview
            this.updatePreviewBackground();
        }, 'landscape', {});
    }

    async downloadShareImage() {
        const btn = document.getElementById('download-recap-image');

        const originalText = btn.innerHTML;
        btn.disabled = true;
        btn.innerHTML = '<span class="loading loading-spinner loading-sm"></span> Generating...';

        try {
            // Fetch PNG from server-side Playwright renderer
            const url = `/api/v1/recap/${this.year}/${this.month}/png/?image_format=landscape&theme=${this.currentBackground}`;

            const response = await fetch(url, {
                credentials: 'same-origin',
                headers: {
                    'X-CSRFToken': PlatPursuit.CSRFToken.get(),
                },
            });

            if (!response.ok) {
                throw new Error(`Server rendering failed: ${response.status}`);
            }

            const blob = await response.blob();

            const downloadUrl = URL.createObjectURL(blob);
            const a = document.createElement('a');
            a.href = downloadUrl;
            a.download = `platpursuit_recap_${this.year}_${this.month}.png`;
            document.body.appendChild(a);
            a.click();
            document.body.removeChild(a);
            URL.revokeObjectURL(downloadUrl);

        } catch (error) {
            console.error('Error generating share image:', error);
            if (window.PlatPursuit && window.PlatPursuit.ToastManager) {
                window.PlatPursuit.ToastManager.show('Failed to generate share image', 'error');
            }
        } finally {
            btn.disabled = false;
            btn.innerHTML = originalText;
        }
    }

    nextSlide() {
        // Check if we're on a quiz slide that hasn't been answered
        const currentSlideType = this.slides[this.currentSlide]?.type;
        if (this.quizManager.isQuizSlide(currentSlideType) && !this.quizManager.canNavigate(currentSlideType)) {
            // Shake the slide to indicate they need to answer
            const slideEl = this.slidesContainer.querySelectorAll('.recap-slide')[this.currentSlide];
            if (slideEl) {
                slideEl.classList.add('animate-shake');
                setTimeout(() => slideEl.classList.remove('animate-shake'), 500);
            }
            return;
        }

        if (this.currentSlide < this.slides.length - 1) {
            this.goToSlide(this.currentSlide + 1);
        }
    }

    prevSlide() {
        // Check if we're on a quiz slide that hasn't been answered
        const currentSlideType = this.slides[this.currentSlide]?.type;
        if (this.quizManager.isQuizSlide(currentSlideType) && !this.quizManager.canNavigate(currentSlideType)) {
            // Shake the slide to indicate they need to answer
            const slideEl = this.slidesContainer.querySelectorAll('.recap-slide')[this.currentSlide];
            if (slideEl) {
                slideEl.classList.add('animate-shake');
                setTimeout(() => slideEl.classList.remove('animate-shake'), 500);
            }
            return;
        }

        if (this.currentSlide > 0) {
            this.goToSlide(this.currentSlide - 1);
        }
    }

    /**
     * Update navigation button states based on current slide
     * Disables buttons on unanswered quiz slides
     */
    updateNavigationButtons() {
        const currentSlideType = this.slides[this.currentSlide]?.type;
        const isQuizSlide = this.quizManager.isQuizSlide(currentSlideType);
        const canNavigate = this.quizManager.canNavigate(currentSlideType);

        // Disable/enable buttons based on quiz state
        if (isQuizSlide && !canNavigate) {
            this.prevBtn.disabled = true;
            this.nextBtn.disabled = true;
            this.prevBtn.classList.add('btn-disabled', 'opacity-30');
            this.nextBtn.classList.add('btn-disabled', 'opacity-30');
        } else {
            this.prevBtn.disabled = false;
            this.nextBtn.disabled = false;
            this.prevBtn.classList.remove('btn-disabled', 'opacity-30');
            this.nextBtn.classList.remove('btn-disabled', 'opacity-30');
        }
    }
}

/**
 * RecapQuizManager - Handles interactive quiz slides within the recap
 *
 * Features:
 * - Manages quiz state and answers
 * - Provides feedback on correct/incorrect answers
 * - Tracks quiz score for summary
 * - Handles navigation blocking until answer selected
 */
class RecapQuizManager {
    constructor(recapManager) {
        this.recapManager = recapManager;
        // Answered state is PER SLIDE, derived from quizResults -- there is no separate flag. A single
        // `hasAnswered` boolean was shared by every quiz in the deck, and initQuizSlide (which reset it)
        // only runs on a slide's FIRST visit. So: answer quiz A, advance to quiz B, then go back to A --
        // A's own answer no longer counted, canNavigate() returned false, and the hunter was stuck on a
        // slide they had already answered, with the shake firing on every attempt to leave.
        this.quizResults = {};
        this.currentQuizSlide = null;
    }

    /** Has this specific quiz been answered? */
    isAnswered(slideType) {
        return Boolean(slideType) && Object.prototype.hasOwnProperty.call(this.quizResults, slideType);
    }

    /**
     * Check if a slide type is a quiz slide
     */
    isQuizSlide(slideType) {
        // `quiz_score` REPORTS on the quizzes; it does not ask anything, so it must not be gated like one.
        return Boolean(slideType) && slideType.startsWith('quiz_') && slideType !== 'quiz_score';
    }

    /**
     * Initialize a quiz slide when it becomes active
     */
    initQuizSlide(slideEl, slideType) {
        this.currentQuizSlide = slideType;

        // Set up click handlers for quiz options
        const options = slideEl.querySelectorAll('[data-quiz-option]');
        options.forEach(option => {
            option.addEventListener('click', (e) => this.handleOptionClick(e, slideEl, slideType));
        });
    }

    /**
     * Handle a quiz answer. Every quiz in the deck is single-select: the multi-select branch that used to
     * live here served only `get_quiz_platinum_options`, which had no caller and no template emitting
     * `[data-quiz-submit]`, so nothing could ever reach it.
     */
    handleOptionClick(e, slideEl, slideType) {
        if (this.isAnswered(slideType)) return;

        const option = e.currentTarget;
        const selectedValue = option.dataset.quizOption;

        // `data-quiz-correct` sits on the .rcp shell, not on slideEl.
        const quizContainer = slideEl.querySelector('[data-quiz-correct]');
        const correctValue = quizContainer ? quizContainer.dataset.quizCorrect : null;

        // Compare as strings: option values are numbers on some quizzes and ids on others.
        const isCorrect = String(selectedValue) === String(correctValue);

        // Record BEFORE rendering feedback: the recorded result IS the answered flag, so it has to exist
        // before anything can re-enter and re-answer.
        this.recordResult(slideType, isCorrect);
        this.showSingleSelectFeedback(slideEl, option, isCorrect, correctValue);

        // The closest-badge quiz reveals how far along the correct answer actually is.
        if (slideType === 'quiz_closest_badge') {
            const answerDetail = slideEl.querySelector('[data-quiz-answer-detail]');
            if (answerDetail) {
                answerDetail.hidden = false;
                answerDetail.classList.add('animate-bounce-in');
            }
        }

        // Navigation buttons stay disabled; they re-enable when the next slide loads.
        setTimeout(() => this.recapManager.nextSlide(), 2000);
    }

    /**
     * Show feedback for single-select quiz
     */
    showSingleSelectFeedback(slideEl, selectedOption, isCorrect, correctValue) {
        const allOptions = slideEl.querySelectorAll('[data-quiz-option]');

        allOptions.forEach(option => {
            option.classList.add('is-locked');

            // Compare as strings for consistency
            if (String(option.dataset.quizOption) === String(correctValue)) {
                option.classList.add('is-correct');
            } else if (option === selectedOption && !isCorrect) {
                option.classList.add('is-wrong');
            } else {
                option.classList.add('is-dimmed');
            }
        });

        // Show feedback message
        this.showFeedbackMessage(slideEl, isCorrect);
    }

    /**
     * Show feedback message on the slide
     */
    showFeedbackMessage(slideEl, isCorrect, extraText = '') {
        const feedbackContainer = slideEl.querySelector('[data-quiz-feedback]') ||
            this.createFeedbackContainer(slideEl);

        const message = isCorrect ? this.getCorrectMessage() : this.getIncorrectMessage();
        feedbackContainer.classList.remove('is-correct', 'is-wrong');
        feedbackContainer.classList.add(isCorrect ? 'is-correct' : 'is-wrong', 'animate-bounce-in');
        feedbackContainer.textContent = extraText ? `${message} (${extraText})` : message;
        feedbackContainer.hidden = false;
    }

    /**
     * Create feedback container if it doesn't exist
     */
    createFeedbackContainer(slideEl) {
        const container = document.createElement('div');
        container.dataset.quizFeedback = '';
        container.className = 'rcp-quiz__verdict';
        const shell = slideEl.querySelector('.rcp');
        if (shell) {
            shell.appendChild(container);
        }
        return container;
    }

    /**
     * Get a random correct answer message
     */
    getCorrectMessage() {
        const messages = [
            'Nailed it.',
            'You got it.',
            'Exactly right.',
            'Nice one.',
            'You know yourself well.'
        ];
        return messages[Math.floor(Math.random() * messages.length)];
    }

    /**
     * Get a random incorrect answer message
     */
    getIncorrectMessage() {
        const messages = [
            'Not quite!',
            'Close one!',
            'Almost!',
            'Good guess!'
        ];
        return messages[Math.floor(Math.random() * messages.length)];
    }

    /**
     * Record quiz result
     */
    recordResult(slideType, isCorrect, details = null) {
        this.quizResults[slideType] = {
            correct: isCorrect,
            details: details,
            timestamp: Date.now()
        };
    }

    /** Correct/incorrect in the order the quizzes were answered -- one pip each on the score slide. */
    orderedResults() {
        return Object.values(this.quizResults)
            .sort((a, b) => a.timestamp - b.timestamp)
            .map((r) => r.correct);
    }

    /**
     * Get overall quiz score
     */
    getScore() {
        const results = Object.values(this.quizResults);
        if (results.length === 0) return null;

        const correct = results.filter(r => r.correct).length;
        return {
            correct,
            total: results.length,
            percentage: Math.round((correct / results.length) * 100)
        };
    }

    /**
     * Check if user can navigate away from current quiz slide
     */
    canNavigate(slideType) {
        if (!this.isQuizSlide(slideType)) return true;
        return this.isAnswered(slideType);
    }
}

// Export to global
window.MonthlyRecapManager = MonthlyRecapManager;
window.RecapQuizManager = RecapQuizManager;
