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

    createProgressDots() {
        this.progressDots.innerHTML = '';
        this.slides.forEach((_, index) => {
            const dot = document.createElement('span');
            dot.className = 'progress-dot';
            dot.dataset.slide = index;
            dot.addEventListener('click', () => {
                // Block navigation if on unanswered quiz
                const currentSlideType = this.slides[this.currentSlide]?.type;
                if (this.quizManager.isQuizSlide(currentSlideType) && !this.quizManager.canNavigate()) {
                    // Shake the slide to indicate they need to answer
                    const slideEl = this.slidesContainer.querySelectorAll('.recap-slide')[this.currentSlide];
                    if (slideEl) {
                        slideEl.classList.add('animate-shake');
                        setTimeout(() => slideEl.classList.remove('animate-shake'), 500);
                    }
                    return;
                }
                this.goToSlide(index);
            });
            this.progressDots.appendChild(dot);
        });
    }

    setupEventListeners() {
        this.prevBtn.addEventListener('click', () => {
            this.prevSlide();
        });

        this.nextBtn.addEventListener('click', () => {
            this.nextSlide();
        });

        // Keyboard navigation
        document.addEventListener('keydown', (e) => {
            if (e.key === 'ArrowLeft') {
                this.prevSlide();
            } else if (e.key === 'ArrowRight') {
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

    goToSlide(index) {
        if (index < 0 || index >= this.slides.length) return;

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

        // Update progress dots
        const dots = this.progressDots.querySelectorAll('.progress-dot');
        dots.forEach((dot, i) => {
            dot.classList.toggle('active', i === index);
        });

        // Trigger slide-specific animations (only on first visit)
        const slideType = this.slides[index].type;
        const slideEl = slideEls[index];
        if (!this.animatedSlides.has(index)) {
            this.animatedSlides.add(index);
            // Small delay to let the slide transition start
            setTimeout(() => {
                this.triggerSlideAnimations(slideEl, slideType);

                // Initialize quiz if this is a quiz slide
                if (this.quizManager.isQuizSlide(slideType)) {
                    this.quizManager.initQuizSlide(slideEl, slideType);
                    // Update buttons AFTER quiz is initialized (hasAnswered is now false)
                    this.updateNavigationButtons();
                }
            }, 100);
        } else if (this.quizManager.isQuizSlide(slideType)) {
            // For revisited quiz slides, still need to update buttons based on answered state
            this.updateNavigationButtons();
        }

        // Trigger celebration effects on specific slides
        this.triggerSlideEffects(this.slides[index]);

        // Show share section on summary slide
        if (slideType === 'summary') {
            this.shareSection.classList.add('visible');
            // Load share preview and setup buttons
            this.loadSharePreview();
        } else {
            this.shareSection.classList.remove('visible');
        }

        // Update navigation button states for non-quiz slides
        // Quiz slides handle their own button updates after initialization
        if (!this.quizManager.isQuizSlide(slideType)) {
            this.updateNavigationButtons();
        }
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
            case 'intro':
                this.animateIntroSlide(slideEl);
                break;
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
     * Animate all elements with count-up class (actual number counting 0 to N)
     */
    animateCountUpElements(slideEl) {
        const countElements = slideEl.querySelectorAll('.count-up');
        countElements.forEach(el => {
            // Get the target value from text content
            const text = el.textContent.trim();
            const targetValue = parseInt(text.replace(/[^0-9]/g, ''), 10);

            if (isNaN(targetValue) || targetValue === 0) return;

            // Preserve any prefix/suffix (like % or +)
            const prefix = text.match(/^[^0-9]*/)?.[0] || '';
            const suffix = text.match(/[^0-9]*$/)?.[0] || '';

            // Animate from 0 to target
            this.animateCountUp(el, targetValue, 1500, prefix, suffix);
        });
    }

    /**
     * Animate a single number from 0 to endValue
     */
    animateCountUp(element, endValue, duration = 1500, prefix = '', suffix = '') {
        const startValue = 0;
        const startTime = performance.now();

        const animate = (currentTime) => {
            const elapsed = currentTime - startTime;
            const progress = Math.min(elapsed / duration, 1);

            // Ease-out-expo for satisfying deceleration
            const easeOutExpo = 1 - Math.pow(2, -10 * progress);
            const currentValue = Math.floor(startValue + (endValue - startValue) * easeOutExpo);

            element.textContent = `${prefix}${currentValue.toLocaleString()}${suffix}`;

            if (progress < 1) {
                requestAnimationFrame(animate);
            } else {
                // Ensure final value is exact
                element.textContent = `${prefix}${endValue.toLocaleString()}${suffix}`;
            }
        };

        // Start from 0
        element.textContent = `${prefix}0${suffix}`;
        requestAnimationFrame(animate);
    }

    /**
     * Intro slide animation - trophy bounce with glow
     */
    animateIntroSlide(slideEl) {
        const trophyIcon = slideEl.querySelector('.trophy-icon, svg, .text-6xl');
        if (trophyIcon) {
            trophyIcon.classList.add('animate-trophy-bounce');
        }
    }

    /**
     * Total trophies slide - number scales in, then stats fan in
     */
    animateTotalTrophiesSlide(slideEl) {
        const statsSection = slideEl.querySelector('.stats');
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
    animateRarestTrophySlide(slideEl) {
        const card = slideEl.querySelector('.card');
        if (card) {
            card.classList.add('animate-spotlight-reveal');
        }

        // Reveal earn rate badge with delay
        const badge = slideEl.querySelector('.badge');
        if (badge) {
            badge.style.opacity = '0';
            badge.style.transform = 'scale(0.8)';

            setTimeout(() => {
                badge.style.transition = 'all 0.4s cubic-bezier(0.34, 1.56, 0.64, 1)';
                badge.style.opacity = '1';
                badge.style.transform = 'scale(1)';
                badge.classList.add('animate-pulse-once');
            }, 1200);
        }
    }

    /**
     * Activity calendar slide - dots fill in sequentially
     */
    animateCalendarSlide(slideEl) {
        const days = slideEl.querySelectorAll('.calendar-day');
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

            // Add click handler for days with platinums
            if (day.classList.contains('platinum-day')) {
                day.addEventListener('click', () => {
                    this.showPlatinumDetails(day);
                });
            }
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

        try {
            const platinums = JSON.parse(platinumsData);
            const dayNumber = dayElement.dataset.day;

            // Create modal HTML
            const modalHTML = `
                <div class="fixed inset-0 bg-black/60 backdrop-blur-sm flex items-center justify-center z-50 p-4" id="platinum-modal">
                    <div class="card bg-base-300 shadow-2xl max-w-md w-full border border-primary/30">
                        <div class="card-body">
                            <div class="flex justify-between items-start mb-4">
                                <div>
                                    <h3 class="text-2xl font-bold text-primary">Day ${dayNumber}</h3>
                                    <p class="text-sm text-base-content/60">${platinums.length} Platinum${platinums.length > 1 ? 's' : ''} Earned</p>
                                </div>
                                <button class="btn btn-sm btn-circle btn-ghost" onclick="document.getElementById('platinum-modal').remove()">
                                    <svg class="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                                        <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M6 18L18 6M6 6l12 12"></path>
                                    </svg>
                                </button>
                            </div>
                            <div class="space-y-3 max-h-96 overflow-y-auto">
                                ${platinums.map(plat => `
                                    <div class="flex items-center gap-3 p-3 bg-base-200 rounded-lg border border-primary/10 hover:border-primary/30 transition-all">
                                        ${plat.icon_url ? `
                                            <img src="${plat.icon_url}" alt="${plat.trophy_name}" class="w-12 h-12 rounded-lg flex-shrink-0" />
                                        ` : ''}
                                        <div class="flex-1 min-w-0">
                                            <p class="font-semibold text-base-content line-clamp-2">${plat.game_name}</p>
                                            <p class="text-sm text-base-content/70 line-clamp-1">${plat.trophy_name}</p>
                                        </div>
                                        <svg class="w-6 h-6 text-primary flex-shrink-0" viewBox="0 0 24 24" fill="currentColor">
                                            <path d="M12 2L15.09 8.26L22 9.27L17 14.14L18.18 21.02L12 17.77L5.82 21.02L7 14.14L2 9.27L8.91 8.26L12 2Z"/>
                                        </svg>
                                    </div>
                                `).join('')}
                            </div>
                        </div>
                    </div>
                </div>
            `;

            // Insert modal
            document.body.insertAdjacentHTML('beforeend', modalHTML);

            // Close on backdrop click
            const modal = document.getElementById('platinum-modal');
            modal.addEventListener('click', (e) => {
                if (e.target === modal) {
                    modal.remove();
                }
            });

            // Close on escape key
            const escapeHandler = (e) => {
                if (e.key === 'Escape') {
                    modal.remove();
                    document.removeEventListener('keydown', escapeHandler);
                }
            };
            document.addEventListener('keydown', escapeHandler);

        } catch (error) {
            console.error('Error parsing platinum data:', error);
        }
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

    triggerSlideEffects(slide) {
        if (slide.type === 'platinums' || slide.type === 'summary') {
            // Fire confetti
            if (window.PlatPursuit && window.PlatPursuit.CelebrationManager) {
                window.PlatPursuit.CelebrationManager.loadConfetti().then(() => {
                    window.PlatPursuit.CelebrationManager.fireSideConfetti(2000);
                }).catch(() => {});
            }
        }
    }

    setupShareButtons() {
        // Download button
        const downloadBtn = document.getElementById('download-recap-image');
        if (downloadBtn && !downloadBtn._hasListener) {
            downloadBtn.addEventListener('click', () => {
                // Track download intent immediately when button is clicked
                PlatPursuit.API.post('/api/v1/tracking/site-event/', {
                    event_type: 'recap_image_download',
                    object_id: `${this.year}-${String(this.month).padStart(2, '0')}`
                }).catch(err => {
                    console.error('[RECAP] Failed to track download:', err);
                });

                // Proceed with download
                this.downloadShareImage();
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
        if (this.quizManager.isQuizSlide(currentSlideType) && !this.quizManager.canNavigate()) {
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
        if (this.quizManager.isQuizSlide(currentSlideType) && !this.quizManager.canNavigate()) {
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
        const canNavigate = this.quizManager.canNavigate();

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
        this.quizResults = {};
        this.currentQuizSlide = null;
        this.hasAnswered = false;
    }

    /**
     * Check if a slide type is a quiz slide
     */
    isQuizSlide(slideType) {
        return slideType && slideType.startsWith('quiz_');
    }

    /**
     * Initialize a quiz slide when it becomes active
     */
    initQuizSlide(slideEl, slideType) {
        this.currentQuizSlide = slideType;
        this.hasAnswered = false;

        // Set up click handlers for quiz options
        const options = slideEl.querySelectorAll('[data-quiz-option]');
        options.forEach(option => {
            option.addEventListener('click', (e) => this.handleOptionClick(e, slideEl, slideType));
        });

        // Set up submit button for multi-select quizzes
        const submitBtn = slideEl.querySelector('[data-quiz-submit]');
        if (submitBtn) {
            submitBtn.addEventListener('click', () => this.handleMultiSelectSubmit(slideEl, slideType));
        }
    }

    /**
     * Handle click on a single-select quiz option
     */
    handleOptionClick(e, slideEl, slideType) {
        if (this.hasAnswered) return;

        const option = e.currentTarget;
        const isMultiSelect = slideEl.querySelector('[data-quiz-submit]') !== null;

        if (isMultiSelect) {
            // Toggle selection for multi-select
            option.classList.toggle('selected');
            option.classList.toggle('ring-2');
            option.classList.toggle('ring-primary');
        } else {
            // Single select - immediate answer
            this.hasAnswered = true;
            const selectedValue = option.dataset.quizOption;

            // Find the element with data-quiz-correct (it's on the inner card, not slideEl)
            const quizContainer = slideEl.querySelector('[data-quiz-correct]');
            const correctValue = quizContainer ? quizContainer.dataset.quizCorrect : null;

            // Compare as strings to handle both numeric and string values consistently
            const isCorrect = String(selectedValue) === String(correctValue);

            this.showSingleSelectFeedback(slideEl, option, isCorrect, correctValue);
            this.recordResult(slideType, isCorrect);

            // Show answer detail for closest badge quiz
            if (slideType === 'quiz_closest_badge') {
                const answerDetail = slideEl.querySelector('[data-quiz-answer-detail]');
                if (answerDetail) {
                    answerDetail.classList.remove('hidden');
                    answerDetail.classList.add('animate-bounce-in');
                }
            }

            // Keep buttons disabled - they'll be re-enabled when the next slide loads
            // Auto-advance after feedback
            setTimeout(() => {
                this.recapManager.nextSlide();
            }, 2000);
        }
    }

    /**
     * Handle submit for multi-select quizzes (like spot the platinums)
     */
    handleMultiSelectSubmit(slideEl, slideType) {
        if (this.hasAnswered) return;
        this.hasAnswered = true;

        const selectedOptions = slideEl.querySelectorAll('[data-quiz-option].selected');
        // Find the element with data-quiz-correct (it's on the inner card, not slideEl)
        const quizContainer = slideEl.querySelector('[data-quiz-correct]');
        const correctValues = JSON.parse(quizContainer ? quizContainer.dataset.quizCorrect : '[]');

        const selectedValues = Array.from(selectedOptions).map(el => el.dataset.quizOption);

        // Calculate score
        const correctSelections = selectedValues.filter(v => correctValues.includes(v)).length;
        const incorrectSelections = selectedValues.filter(v => !correctValues.includes(v)).length;
        const missedSelections = correctValues.filter(v => !selectedValues.includes(v)).length;

        const isFullyCorrect = incorrectSelections === 0 && missedSelections === 0;

        this.showMultiSelectFeedback(slideEl, selectedValues, correctValues);
        this.recordResult(slideType, isFullyCorrect, {
            correct: correctSelections,
            incorrect: incorrectSelections,
            missed: missedSelections
        });

        // Auto-advance after feedback
        setTimeout(() => {
            this.recapManager.nextSlide();
        }, 2500);
    }

    /**
     * Show feedback for single-select quiz
     */
    showSingleSelectFeedback(slideEl, selectedOption, isCorrect, correctValue) {
        const allOptions = slideEl.querySelectorAll('[data-quiz-option]');

        allOptions.forEach(option => {
            option.classList.add('pointer-events-none');

            // Compare as strings for consistency
            if (String(option.dataset.quizOption) === String(correctValue)) {
                option.classList.add('ring-2', 'ring-success', 'bg-success/20');
            } else if (option === selectedOption && !isCorrect) {
                option.classList.add('ring-2', 'ring-error', 'bg-error/20');
            } else {
                option.classList.add('opacity-50');
            }
        });

        // Show feedback message
        this.showFeedbackMessage(slideEl, isCorrect);
    }

    /**
     * Show feedback for multi-select quiz
     */
    showMultiSelectFeedback(slideEl, selectedValues, correctValues) {
        const allOptions = slideEl.querySelectorAll('[data-quiz-option]');

        allOptions.forEach(option => {
            const value = option.dataset.quizOption;
            const wasSelected = selectedValues.includes(value);
            const isCorrect = correctValues.includes(value);

            option.classList.add('pointer-events-none');

            if (isCorrect && wasSelected) {
                // Correct selection
                option.classList.remove('ring-primary');
                option.classList.add('ring-2', 'ring-success', 'bg-success/20');
            } else if (isCorrect && !wasSelected) {
                // Missed correct answer
                option.classList.add('ring-2', 'ring-warning', 'bg-warning/20', 'opacity-75');
            } else if (!isCorrect && wasSelected) {
                // Incorrect selection
                option.classList.remove('ring-primary');
                option.classList.add('ring-2', 'ring-error', 'bg-error/20');
            } else {
                // Correctly not selected
                option.classList.add('opacity-50');
            }
        });

        // Hide submit button
        const submitBtn = slideEl.querySelector('[data-quiz-submit]');
        if (submitBtn) {
            submitBtn.classList.add('hidden');
        }

        // Show feedback
        const correct = selectedValues.filter(v => correctValues.includes(v)).length;
        const total = correctValues.length;
        const isFullyCorrect = correct === total && selectedValues.length === total;

        this.showFeedbackMessage(slideEl, isFullyCorrect, `${correct}/${total} correct`);
    }

    /**
     * Show feedback message on the slide
     */
    showFeedbackMessage(slideEl, isCorrect, extraText = '') {
        const feedbackContainer = slideEl.querySelector('[data-quiz-feedback]') ||
            this.createFeedbackContainer(slideEl);

        let message, className;
        if (isCorrect) {
            message = this.getCorrectMessage();
            className = 'text-success';
        } else {
            message = this.getIncorrectMessage();
            className = 'text-warning';
        }

        if (extraText) {
            message += ` (${extraText})`;
        }

        feedbackContainer.innerHTML = `
            <div class="animate-bounce-in ${className} text-xl font-bold mt-4">
                ${message}
            </div>
        `;
        feedbackContainer.classList.remove('hidden');
    }

    /**
     * Create feedback container if it doesn't exist
     */
    createFeedbackContainer(slideEl) {
        const container = document.createElement('div');
        container.dataset.quizFeedback = '';
        container.className = 'text-center';
        const card = slideEl.querySelector('.card');
        if (card) {
            card.appendChild(container);
        }
        return container;
    }

    /**
     * Get a random correct answer message
     */
    getCorrectMessage() {
        const messages = [
            'Nailed it! 🎯',
            'You got it! ✨',
            'Perfect! 🌟',
            'Exactly right! 💫',
            'Nice one! 🏆'
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
    canNavigate() {
        if (!this.currentQuizSlide) return true;
        return this.hasAnswered;
    }
}

// Export to global
window.MonthlyRecapManager = MonthlyRecapManager;
window.RecapQuizManager = RecapQuizManager;
