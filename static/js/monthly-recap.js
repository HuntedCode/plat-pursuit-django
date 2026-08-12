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
/** The share card's own pixel width. It renders at its natural size and is SCALED to fit, because the
 *  thing on screen and the thing downloaded have to be the same object. */
const SHARE_CARD_WIDTH = 1200;

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
        // Width of each NAVIGATION edge, as a fraction of the stage. What is left between them is the
        // pause zone -- so this single number is the whole 30/40/30 split, and the JS and the stylesheet
        // are not free to drift apart about where the boundaries are.
        this.ZONE_EDGE = 0.30;
        this.pinned = false;          // a latched pause, as opposed to the transient hold under a finger
        this.stageOpen = false;       // the deck does not run until the hunter enters
        this.prevBtn = document.getElementById('prev-slide');
        this.nextBtn = document.getElementById('next-slide');
        this.holdBtn = document.getElementById('hold-slide');
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
    openStage(opts = {}) {
        if (this.stageOpen) return;
        this.stageOpen = true;
        // Card-only: the same stage, opened straight at its ending. Someone who came back for the card
        // gets the card -- with the look picker and a preview -- rather than a blind download, and
        // without a deck they did not ask for. `is-card-only` drops the deck's chrome.
        const cardOnly = Boolean(opts.cardOnly);
        this.container.classList.toggle('is-card-only', cardOnly);

        document.body.appendChild(this.container);
        this.container.hidden = false;
        void this.container.offsetWidth;              // let the un-hide land before the fade starts
        this.container.classList.add('is-in');

        this.container.classList.add('is-aim-fwd');   // discoverable before the pointer moves at all
        this.handle = PlatPursuit.takeover(this.container, {
            focusSel: '#recap-exit',
            exitMs: 280,
            onClose: () => this.onStageClosed(),
        });

        // Warm the card while the deck plays. It is a template render (/html/), not Playwright, so this
        // is cheap -- and it means the ending transitions instantly instead of showing a spinner at the
        // exact moment the ceremony is meant to pay off.
        this.warmCard();

        if (cardOnly) {
            // The card may still be in flight; warmCard resolves before this runs on a warm cache, and
            // showCardScene is a no-op until the HTML exists, so ask again once it lands.
            Promise.resolve(this.warmCard()).then(() => this.showCardScene());
            return;
        }

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

        // Height available to the card, derived from the COLUMN rather than the frame's own box. Reading
        // the frame's height would be circular once the frame hugs its scaled content, and clearing the
        // height to re-measure would force a synchronous layout every time.
        const availableHeight = () => {
            const wrap = frame.parentElement;
            const cs = getComputedStyle(wrap);
            const gap = parseFloat(cs.rowGap) || 0;
            let used = parseFloat(cs.paddingTop) + parseFloat(cs.paddingBottom)
                     + gap * Math.max(0, wrap.children.length - 1);
            for (const el of wrap.children) {
                if (el !== frame) used += el.getBoundingClientRect().height;
            }
            return Math.max(0, wrap.clientHeight - used);
        };

        const fit = () => {
            const bw = frame.clientWidth, bh = availableHeight();
            const cw = card.offsetWidth, ch = card.offsetHeight;
            if (!cw || !ch) return;
            // Never scale UP: past 1:1 it is a blurry enlargement of a fixed-size render.
            const scale = Math.min(1, bw / cw, bh / ch);
            frame.style.setProperty('--rcx-card-scale', String(scale));
            // Hug the SCALED card. `transform` does not affect layout, so a frame left to size itself
            // stays full card-height however far down the card was scaled -- on a phone that is a 630px
            // box around a 180px card, which pushed the label to the top of the stage and left the
            // buttons stranded at the bottom. With the frame hugging, the column centres as one stack.
            frame.style.height = `${Math.round(ch * scale)}px`;
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
        this.container.classList.remove('is-card-only');
        this.cancelResume();
        this.stopBeatTimer();
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

    /**
     * Paint every bar for the active beat.
     *
     * Each fill's width is COMMITTED INLINE rather than left to the class rules. Relying on classes meant
     * relying on how a browser cancels an in-flight transition when the rule carrying it stops matching --
     * behaviour that varies, and that I could not pin down from a harness while a skipped bar was being
     * reported as still animating. An inline value outranks every rule here and cannot be interpolated
     * toward, so a bar behind the playhead is full the instant it is painted. No semantics to trust.
     */
    paintBars(index) {
        if (!this.bars) return;
        const paused = this.container.classList.contains('is-waiting')
            || this.container.classList.contains('is-manual');

        this.bars.forEach((bar, n) => {
            const fill = bar.querySelector('i');
            bar.classList.remove('is-live', 'is-done');
            fill.style.transition = 'none';
            if (n < index) {
                bar.classList.add('is-done');
                fill.style.width = '100%';        // behind the playhead: done, immediately
            } else if (n > index) {
                fill.style.width = '0';           // ahead of it: untouched
            }
        });

        const live = this.bars[index];
        if (!live) return;
        const fill = live.querySelector('i');

        if (this.prefersReducedMotion) {
            live.classList.add('is-done');
            fill.style.width = '100%';
            return;
        }

        // Rewind to zero and COMMIT it, then hand the width back to the stylesheet so `.is-live` can run
        // it to 100% over the beat. Without the commit the fill eases BACKWARDS from wherever the last
        // beat left it; without handing it back, the inline zero pins the bar and it never moves at all.
        fill.style.width = '0';
        void fill.offsetWidth;
        live.classList.add('is-live');
        if (!paused) {
            fill.style.transition = '';
            fill.style.width = '';
        }
    }

    setupEventListeners() {
        // The zones stay as focusable controls for keyboard and assistive tech; pointer advancement is
        // handled below, where it can see what was actually clicked.
        this.prevBtn.addEventListener('click', () => this.prevSlide());
        this.nextBtn.addEventListener('click', () => this.nextSlide());
        // The pause zone is a real control, not just a region: without one, the only way to stop the deck
        // would be a pointer gesture, which is no way at all for a keyboard or a screen reader.
        if (this.holdBtn) this.holdBtn.addEventListener('click', () => this.togglePause());
        if (this.prefersReducedMotion) {
            // ...and a control that does nothing does not belong in the tab order, nor does the hint get
            // to advertise it. The middle simply becomes inert; the edges still navigate.
            this.container.classList.add('is-static');
            if (this.holdBtn) this.holdBtn.hidden = true;
        }

        this.container.addEventListener('click', (e) => {
            if (!this.stageOpen || this.container.classList.contains('is-ending')) return;
            // Anything interactive owns its own click: quiz options, calendar days, the exit, the
            // Continue control, links inside a beat.
            if (e.target.closest('button, a, [data-quiz-option], input, select, dialog')) return;

            const zone = this.zoneAt(e.clientX);
            if (zone === 'back') this.prevSlide();
            else if (zone === 'fwd') this.nextSlide();
            else this.togglePause();
        });

        const begin = document.getElementById('recap-begin');
        if (begin) begin.addEventListener('click', () => this.openStage());

        const exit = document.getElementById('recap-exit');
        if (exit) exit.addEventListener('click', () => this.handle && this.handle.close());

        // Coming back for the card should not mean sitting through the deck again.
        const quick = document.getElementById('recap-quick-download');
        if (quick) quick.addEventListener('click', () => this.openStage({ cardOnly: true }));

        const advance = document.getElementById('recap-advance');
        if (advance) advance.addEventListener('click', (e) => {
            if (e.target.closest('[data-advance]')) this.nextSlide();
        });

        // The error state's retry used to be an inline `onclick` in the template -- the one thing
        // `test_no_inline_event_handlers` exists to keep off this page.
        const retry = document.getElementById('error-retry');
        if (retry) retry.addEventListener('click', () => window.location.reload());

        const done = document.getElementById('recap-done');
        if (done) done.addEventListener('click', () => this.handle && this.handle.close());

        const share = document.getElementById('recap-share');
        if (share) share.addEventListener('click', () => {
            this.seenSummary = true;
            if (this.handle) this.handle.close();     // onStageClosed opens and scrolls to the panel
        });

        window.addEventListener('pagehide', () => this.abortLoad());

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
                this.holdBeat();
            } else {
                if (this.pinned) return;    // paused on purpose before leaving; it stays paused
                // Restart the beat outright rather than resuming: the two clocks drifted apart while the
                // tab was backgrounded, and a clean restart is the only state they can agree on.
                this.container.classList.remove('is-held');
                const el = this.slidesContainer.querySelectorAll('.recap-slide')[this.currentSlide];
                this.armBeat(this.currentSlide, el);
            }
        });

        // Hold to pause -- the affordance every story UI has. Pointer events rather than touch, so a
        // mouse press pauses too.
        // Show WHICH way a click will go, as the pointer moves. Going back was an invisible third of the
        // screen: aiming to skip forward and landing on it instead is the most jarring thing the deck can
        // do, because nothing on screen suggested backward was even possible.
        this.container.addEventListener('pointermove', (e) => {
            if (!this.stageOpen || this.container.classList.contains('is-ending')) return;
            const zone = this.zoneAt(e.clientX);
            this.container.classList.toggle('is-aim-back', zone === 'back');
            this.container.classList.toggle('is-aim-fwd', zone === 'fwd');
            this.container.classList.toggle('is-aim-hold', zone === 'hold');
        });

        this.container.addEventListener('pointerdown', () => this.holdBeat());
        ['pointerup', 'pointercancel', 'pointerleave'].forEach((evt) => {
            this.container.addEventListener(evt, () => this.releaseBeat());
        });
        this.container.addEventListener('pointerleave', () => {
            this.container.classList.remove('is-aim-back', 'is-aim-fwd', 'is-aim-hold');
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

    /**
     * One request for the whole deck.
     *
     * This used to fan out one request PER BEAT in parallel. At 20 beats that is 20 requests for a single
     * month, and DRF throttles at 60/min per user across the entire API -- so flicking between a few
     * months exhausted the bucket and started 429ing everything the page did, including the notification
     * poll. The deck was starving the rest of the site of its own request budget.
     *
     * Aborting on navigation (below) is necessary but was never sufficient: a month view that COMPLETED
     * still cost 20. The volume itself was the bug.
     */
    async prefetchAllSlides() {
        const loadingSlide = document.getElementById('loading-slide');
        this._abort = new AbortController();

        try {
            const data = await PlatPursuit.API.get(
                `/api/v1/recap/${this.year}/${this.month}/deck/`,
                { signal: this._abort.signal },
            );
            (data.slides || []).forEach((slide, index) => {
                this.slideCache[`${slide.type}_${index}`] = slide.html;
            });
        } catch (error) {
            if (error.name === 'AbortError') return;      // month switched; the new load owns the page
            console.error('Error loading recap deck:', error);
        }

        if (loadingSlide) loadingSlide.remove();
        this.renderAllSlides();
    }

    /** Drop an in-flight deck load when the page goes away. Secondary to the batching above -- a browser
     *  cancels pending requests on navigation anyway, and anything the server already received has
     *  already cost its throttle slot -- but it stops a half-applied response from racing teardown. */
    abortLoad() {
        if (this._abort) { this._abort.abort(); this._abort = null; }
    }

    getFallbackSlideHTML(slideType) {
        return `
            <div class="text-center py-8">
                <p class="text-base-content/70">Error loading slide</p>
            </div>
        `;
    }

    renderAllSlides() {
        // Remove only the SLIDES. `innerHTML = ''` was wiping the whole stage, and the stage is not just
        // slides -- the tap zones, the direction arrows and the hint line are markup that lives here too.
        // They were destroyed on first render and never came back, so the arrows were never in the DOM to
        // be seen and the zone buttons the controller had already captured were detached nodes.
        this.slidesContainer.querySelectorAll('.recap-slide').forEach((el) => el.remove());

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
    /**
     * Set this beat's length, paint the bar with it, then arm the timeout -- one number, one place.
     *
     * NB the ordering is tidiness, not a fix: the bar's transition does not begin until the current task
     * ends, so it picks up `--rcx-beat` whichever order these run in. A negative control confirmed that.
     * The bug that actually made the bars wrong was in the CSS -- see `is-waiting` in recap-stage.css.
     */
    armBeat(index, slideEl, ms) {
        this.cancelResume();          // a queued resume belongs to the beat we are leaving
        this.pinned = false;          // ...and so does a pause: moving on is what un-pauses the deck
        this.container.classList.remove('is-pinned', 'is-held');
        this._beatLeft = null;
        this._beatMs = ms != null ? ms : this.beatDuration(slideEl);
        this.container.style.setProperty('--rcx-beat', this._beatMs + 'ms');
        this.syncBeatState(this.slides[index]?.type);   // BEFORE painting -- paintBars reads it
        this.paintBars(index);
        this.startBeatTimer();
    }

    /**
     * Whether THIS beat is waiting on the hunter, expressed as classes on the stage.
     *
     * Split out of startBeatTimer because paintBars needs the answer for the beat it is about to paint,
     * and startBeatTimer runs after it. Left there, the slide following a quiz was painted while the
     * stage still said "waiting", so its bar kept an inline `width: 0` and never ran.
     */
    syncBeatState(type) {
        const gated = this.quizManager.isQuizSlide(type) && !this.quizManager.canNavigate(type);
        const manual = this.MANUAL_BEATS.has(type);
        const blocked = gated || manual || Boolean(document.querySelector('dialog[open]'));
        this.container.classList.toggle('is-waiting', blocked);
        this.container.classList.toggle('is-manual', manual);
        const advance = document.getElementById('recap-advance');
        if (advance) advance.hidden = !manual || this.currentSlide >= this.slides.length - 1;
        return blocked;
    }

    startBeatTimer() {
        this.stopBeatTimer();
        if (this.prefersReducedMotion || !this.stageOpen) return;
        // A pinned beat stays pinned. Guarding here rather than at each call site means a dialog closing,
        // a quiz being answered or a deferred release cannot quietly restart a deck you paused. The
        // remainder in `_beatLeft` survives untouched, because only a real start consumes it.
        if (this.pinned) return;
        // Re-sync in case something changed since the beat was armed -- a quiz being answered, a dialog
        // opening or closing. armBeat has already done it for a fresh beat.
        const blocked = this.syncBeatState(this.slides[this.currentSlide]?.type);
        if (blocked) {
            this._beatEndsAt = null;      // no clock running: there is no remainder to carry
            return;
        }

        // A resume runs the REMAINDER; a fresh beat uses the length armBeat already wrote.
        // A remainder is only honoured for the slide it was taken from. Anything else is stale by
        // definition, and using it would run this beat on the previous beat's leftovers.
        const carry = (this._beatLeft && this._beatLeft.index === this.currentSlide)
            ? this._floor(this._beatLeft.ms) : null;
        this._beatLeft = null;

        const ms = carry != null ? carry : (this._beatMs || this.BEAT_MIN_MS);
        if (carry != null) this.container.style.setProperty('--rcx-beat', ms + 'ms');
        this._beatEndsAt = performance.now() + ms;
        this.beatTimer = setTimeout(() => this.endOfBeat(), ms);
    }

    /**
     * What this beat does when its time runs out: advance, or -- on the last one -- hand over to the card.
     *
     * The summary used to bail out of `startBeatTimer` and schedule its own `setTimeout` instead, which
     * made it the ONE beat paced by a second clock. Nothing that governs the first clock reached it: a
     * held finger did not stop it, the tab-visibility restart did not re-align it, and a latched pause
     * left the deck reporting "paused" while the card arrived on schedule underneath. Same shape as the
     * quiz dwell that raced the beat timer -- see the file header. One clock; the ending is a branch.
     */
    endOfBeat() {
        if (this.currentSlide >= this.slides.length - 1) this.showCardScene();
        else this.nextSlide();
    }

    /**
     * Which of the three regions is this x-coordinate in?
     *
     * Thirty / forty / thirty. The deck used to be a two-way split where everything that was not the
     * backward edge advanced, which made the middle -- the part you are actually reading -- a forward
     * button. Pausing meant holding a finger down for as long as you wanted to look, which is the wrong
     * gesture for the slides that need it most: the ones with the most to read.
     *
     * So the middle is now its own control and it LATCHES. The edges keep navigation and are wide enough
     * to hit without aiming, and a tap that lands in the middle because you misjudged the edge pauses
     * rather than skipping something -- a recoverable, visible outcome instead of a lost beat.
     *
     * One definition, shared by the click handler and the hover affordance, so what you are shown and
     * what you get cannot disagree.
     */
    zoneAt(clientX) {
        const box = this.container.getBoundingClientRect();
        const at = (clientX - box.left) / (box.width || 1);
        if (at < this.ZONE_EDGE) return 'back';
        if (at > 1 - this.ZONE_EDGE) return 'fwd';
        return 'hold';
    }

    /**
     * Latch or unlatch the pause.
     *
     * A no-op on a beat that has no clock: quizzes and the calendar already wait on you, so "paused"
     * there would be a state the deck was in without anything being suspended.
     */
    togglePause() {
        // Nothing to pause under reduced motion: no beat is ever armed there, so the deck only moves when
        // the hunter moves it. Latching anyway would put a "Resume" control on screen offering to restart
        // something that was never running -- the same lie the `is-waiting` guard below exists to prevent.
        if (this.prefersReducedMotion) return;
        if (!this.pinned && this.container.classList.contains('is-waiting')) return;
        this.pinned = !this.pinned;
        this.container.classList.toggle('is-pinned', this.pinned);
        if (this.holdBtn) this.holdBtn.setAttribute('aria-label', this.pinned ? 'Resume' : 'Pause');
        // `click` lands AFTER `pointerup`, so the hold this tap began has already been released and the
        // bar handed back to the stylesheet. Re-take it rather than assuming it is still held.
        this.cancelResume();
        if (this.pinned) this.holdBeat(); else this.releaseBeat();
    }

    /** A quiz has been answered: hold on the verdict for a moment, then move on -- re-armed through the
     *  normal beat path so the deck never has two clocks running at it. */
    dwellOnAnswer(ms) {
        const el = this.slidesContainer.querySelectorAll('.recap-slide')[this.currentSlide];
        this.armBeat(this.currentSlide, el, ms);
    }

    /**
     * Freeze the beat under a finger.
     *
     * The bar has to be PINNED at the width it is showing. `transition: none` does not freeze an
     * in-flight transition -- it drops the animation and applies the target immediately, so the bar
     * snapped to full on every pointerdown, which is every click.
     */
    holdBeat() {
        if (!this.stageOpen || this.container.classList.contains('is-held')) return;
        // Freeze the bar FIRST and unconditionally. A latched pause arrives on `click`, by which point
        // `pointerup` has already released the hold and handed the bar back to the stylesheet -- so there
        // may be no timer left to stop, but there is always a bar mid-flight to catch.
        const fill = this.container.querySelector('.rcx__bar.is-live i');
        if (fill) fill.style.width = getComputedStyle(fill).width;
        // Only recompute the remainder when a clock is actually running. Overwriting it from a stale
        // `_beatEndsAt` would throw away the remainder the pointerdown half of this same tap captured.
        if (this.beatTimer) {
            this._beatLeft = this._beatEndsAt
                ? { index: this.currentSlide, ms: Math.max(0, this._beatEndsAt - performance.now()) }
                : null;
            this.stopBeatTimer();
        }
        this.container.classList.add('is-held');
    }

    /**
     * Resume with what is LEFT of the beat, not a fresh one -- holding to finish reading a slide should
     * not hand you the whole slide again.
     *
     * Resuming on the NEXT FRAME, not immediately, and that is the important part. Every click is also a
     * pointerdown/pointerup pair, so a click late in a beat leaves a near-zero remainder here; scheduling
     * that straight away can fire it in the gap between `pointerup` and `click` and advance the deck
     * BEFORE the click does -- two slides for one click. Deferring a frame means a pending click always
     * navigates first, and `armBeat` cancels this resume when it does. The race is gone by construction
     * rather than by the timings happening to work out.
     */
    releaseBeat() {
        if (this.pinned) return;        // latched: lifting the finger is not what ends this pause
        if (!this.container.classList.contains('is-held')) return;
        this.container.classList.remove('is-held');
        const fill = this.container.querySelector('.rcx__bar.is-live i');
        if (fill) fill.style.width = '';        // back to the stylesheet, which runs it to 100%
        this.cancelResume();
        this._resumeFrame = requestAnimationFrame(() => {
            this._resumeFrame = null;
            this.startBeatTimer();
        });
    }

    cancelResume() {
        if (this._resumeFrame) { cancelAnimationFrame(this._resumeFrame); this._resumeFrame = null; }
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

    /** Floor on any scheduled advance. A remainder of a millisecond is not a beat -- it is a slide
     *  flashing past -- and it is exactly the value a click late in a beat leaves behind. */
    _floor(ms) {
        return Math.max(250, ms);
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

        this.armBeat(index, slideEls[index]);
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
            // The hold before the hand-over is this beat's OWN beat, armed by the normal path like every
            // other -- so pausing it actually pauses it. `endOfBeat` decides what happens when it expires.
        } else {
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
            // The card leads; the controls sit UNDER it. They used to sit above, which made the panel
            // open on a settings row rather than on the thing being made.
            shareContent.innerHTML = `
                <div class="rcs__stack">
                    <div class="rcs__frame">
                        <div class="rcs__scaler">
                            <div id="share-preview-inner" class="rcs__card">${data.html}</div>
                        </div>
                    </div>

                    <div class="rcs__controls">
                        <label class="rcs__ctl-label" for="recap-background-select">Background</label>
                        <select id="recap-background-select" class="select select-bordered select-sm rcs__select">
                            ${this.renderBackgroundOptions()}
                        </select>
                        <button type="button" id="open-recap-color-grid" class="rcs__grid-btn"
                                aria-label="Choose a background from the grid">
                            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"
                                 stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
                                <rect width="7" height="7" x="3" y="3" rx="1" /><rect width="7" height="7" x="14" y="3" rx="1" />
                                <rect width="7" height="7" x="3" y="14" rx="1" /><rect width="7" height="7" x="14" y="14" rx="1" />
                            </svg>
                        </button>
                        <button id="download-recap-image" class="btn btn-sm md:btn-md btn-primary rcs__dl"
                                data-year="${this.year}" data-month="${this.month}">
                            Download
                            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"
                                 stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
                                <path d="M12 4v12M6 12l6 6 6-6M4 21h16" />
                            </svg>
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

        // Anchored on a NAMED class, not on `.relative`. A utility class is not a contract: the panel
        // around this was rebuilt and the utility went with it, which would have left the 1200px card
        // sitting unscaled inside a 600px frame with no error anywhere -- `closest` simply returns null
        // and this bails.
        const container = previewInner.closest('.rcs__frame');
        if (!container) return;

        const scale = container.offsetWidth / SHARE_CARD_WIDTH;
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
        } else {
            // On the last beat "move on" means the card, not nothing. This used to be a dead tap: the
            // summary's hand-over was on its own timer, so the forward zone had nothing to hand to and
            // the only way past the summary was to wait it out.
            this.showCardScene();
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
        this.QUIZ_FEEDBACK_MS = 2200;      // long enough to read the verdict before the deck moves on
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

        // The feedback dwell IS this beat's remaining time, not a second timer racing the first. The bar
        // restarts and runs for it, so what you watch counts down to the advance you actually get.
        this.recapManager.dwellOnAnswer(this.QUIZ_FEEDBACK_MS);
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
