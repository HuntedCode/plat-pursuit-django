/**
 * ReelSpinner - Shared "Pick My Next Game" reel spinner.
 *
 * A configurable slot-machine-style picker that randomly selects a game
 * from a pool of assigned, incomplete challenge slots. Used by both
 * A-Z Challenge and Genre Challenge detail pages.
 *
 * Config:
 *   slots          - Array of slot objects (from window._SPINNER_SLOTS)
 *   challengeId    - Number, the challenge ID
 *   tileLabel      - Function(slot) => String, text shown above tile icon ("A" or "Action")
 *   tileLabelClass - String|null, optional CSS classes for the label element
 *   tileIcon       - Function(slot) => String, icon URL
 *   tileName       - Function(slot) => String, game name
 *   resultBadge    - Function(slot) => String, badge text in result card
 *   coverApiUrl    - Function(challengeId) => String, PATCH endpoint
 *   coverPayload   - Function(slot) => Object, request body for cover PATCH
 *   gridSelector   - Function(slot) => String, CSS selector for grid card highlight
 *   winnerKey      - Function(slot) => String, unique key per slot for matching
 *
 * Uses: PlatPursuit.API, PlatPursuit.ToastManager, PlatPursuit.HTMLUtils,
 *       PlatPursuit.CelebrationManager
 */

function ReelSpinner(config) {
    this.config = config;
    this._spinnerSlots = config.slots || [];
    this._challengeId = config.challengeId;

    // State
    this._reelItems = [];
    this._winner = null;
    this._winnerTileIndex = -1;
    this._finalTranslateX = 0;
    this._isSpinning = false;
    this._animFrameId = null;
    this._lastWinnerKey = null;
    this._resultTimeoutId = null;
    this._resultInnerTimeoutId = null;

    // Knife easter egg
    this._knifeTileIndex = -1;
    this._knifeLanded = false;
    this._nearKnifeHit = false;

    // Audio
    this._audioCtx = null;
    this._audioGain = null;
    this._audioMuted = false;
    this._noiseBuffer = null;
    this._lastTickTime = 0;
    this._lastTickTileIdx = -1;
}

// ─── Constants ───────────────────────────────────────────────────────────────

ReelSpinner.TILE_WIDTH = 104; // 96px tile (w-24) + 8px gap (gap-2)

ReelSpinner.DICE_SVG = '<svg xmlns="http://www.w3.org/2000/svg" class="w-full h-full" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="2" y="2" width="20" height="20" rx="2"/><circle cx="8" cy="8" r="1.5" fill="currentColor"/><circle cx="16" cy="8" r="1.5" fill="currentColor"/><circle cx="8" cy="16" r="1.5" fill="currentColor"/><circle cx="16" cy="16" r="1.5" fill="currentColor"/><circle cx="12" cy="12" r="1.5" fill="currentColor"/></svg>';

ReelSpinner.CAMERA_SVG = '<svg xmlns="http://www.w3.org/2000/svg" class="w-full h-full" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M23 19a2 2 0 0 1-2 2H3a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h4l2-3h6l2 3h4a2 2 0 0 1 2 2z"/><circle cx="12" cy="13" r="4"/></svg>';

ReelSpinner.FLAVOR_TEXTS = [
    'Your next platinum awaits!',
    'The hunt begins here.',
    'Destiny has spoken. Time to earn that plat.',
    'The trophies are calling. Will you answer?',
    'This one has your name on it.',
    'No trophy can hide from you.',
    'Lock in. This plat is yours.',
    'The pursuit continues.',
];

ReelSpinner.SPEAKER_ON_SVG = `
    <svg xmlns="http://www.w3.org/2000/svg" class="w-4 h-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
        <polygon points="11 5 6 9 2 9 2 15 6 15 11 19 11 5"/>
        <path d="M19.07 4.93a10 10 0 0 1 0 14.14"/>
        <path d="M15.54 8.46a5 5 0 0 1 0 7.07"/>
    </svg>
`;

ReelSpinner.SPEAKER_OFF_SVG = `
    <svg xmlns="http://www.w3.org/2000/svg" class="w-4 h-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
        <polygon points="11 5 6 9 2 9 2 15 6 15 11 19 11 5"/>
        <line x1="23" y1="9" x2="17" y2="15"/>
        <line x1="17" y1="9" x2="23" y2="15"/>
    </svg>
`;

// ─── Knife Easter Egg ────────────────────────────────────────────────────────

ReelSpinner.KNIFE_IMAGE = '/static/images/easter-eggs/knife.png';
ReelSpinner.KNIFE_APPEAR_CHANCE = 0.01;  // 1-in-100: knife shows up in reel
ReelSpinner.KNIFE_LAND_CHANCE = 0.001;    // 1-in-1000: knife is the winner

ReelSpinner.KNIFE_NEAR_MISS_TEXTS = [
    'So close to the knife...',
    'The knife giveth, the knife taketh away.',
    'A knife was spotted in the reel!',
    'You almost unboxed a rare one!',
];

ReelSpinner.KNIFE_WIN_TEXTS = [
    'You unboxed a knife!',
    'Against all odds... the knife is yours.',
    'One in a thousand. Legendary.',
    'The rarest of pulls. Platinum-tier luck.',
];

// ─── Public API ──────────────────────────────────────────────────────────────

/**
 * Bind the spinner to a button element by ID.
 * Validates minimum slot count and attaches click handler.
 */
ReelSpinner.prototype.bind = function(buttonId) {
    const btn = document.getElementById(buttonId);
    if (!btn) return;

    if (this._spinnerSlots.length < 2) {
        btn.disabled = true;
        btn.classList.add('btn-disabled');
        btn.title = this._spinnerSlots.length === 0
            ? 'No assigned games to pick from'
            : 'Need at least 2 assigned games to spin';
        return;
    }

    btn.addEventListener('click', () => this._openModal());
};

// ─── Modal Lifecycle ─────────────────────────────────────────────────────────

ReelSpinner.prototype._openModal = function() {
    if (!document.getElementById('pick-next-game-modal')) {
        document.body.insertAdjacentHTML('beforeend', this._buildModalHTML());
        this._bindEvents();

        // Set initial mute icon from localStorage
        this._audioMuted = localStorage.getItem('pp_spinner_muted') === '1';
        this._updateMuteIcon();
    }

    this._resetSpinner();
    document.getElementById('pick-next-game-modal').showModal();
};

ReelSpinner.prototype._buildModalHTML = function() {
    return `
        <dialog id="pick-next-game-modal" class="modal">
            <div class="modal-box max-w-2xl bg-base-200 p-0 overflow-hidden relative">
                <canvas id="spinner-confetti-canvas" class="absolute inset-0 w-full h-full pointer-events-none z-50"></canvas>
                <div class="bg-base-300/50 px-6 py-4 flex items-center justify-between">
                    <h3 class="font-bold text-lg flex items-center gap-2">
                        <span class="inline-flex w-5 h-5 text-secondary">${ReelSpinner.DICE_SVG}</span>
                        Pick My Next Game!
                    </h3>
                    <div class="flex items-center gap-1">
                        <button class="btn btn-sm btn-circle btn-ghost" id="spinner-mute-btn" title="Toggle sound">
                            <svg xmlns="http://www.w3.org/2000/svg" class="w-4 h-4" id="spinner-mute-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                                <polygon points="11 5 6 9 2 9 2 15 6 15 11 19 11 5"/>
                                <path d="M19.07 4.93a10 10 0 0 1 0 14.14"/>
                                <path d="M15.54 8.46a5 5 0 0 1 0 7.07"/>
                            </svg>
                        </button>
                        <button class="btn btn-sm btn-circle btn-ghost" id="spinner-close-btn">&#x2715;</button>
                    </div>
                </div>

                <div class="relative px-6 py-8" id="spinner-area">
                    <div class="absolute top-0 bottom-0 left-1/2 -translate-x-1/2 z-20 flex flex-col items-center pointer-events-none">
                        <div class="w-0 h-0 border-l-[10px] border-r-[10px] border-t-[14px]
                                    border-l-transparent border-r-transparent border-t-secondary
                                    drop-shadow-lg"></div>
                        <div class="w-0.5 flex-1 bg-secondary/30"></div>
                        <div class="w-0 h-0 border-l-[10px] border-r-[10px] border-b-[14px]
                                    border-l-transparent border-r-transparent border-b-secondary
                                    drop-shadow-lg"></div>
                    </div>

                    <div class="overflow-x-hidden overflow-y-visible rounded-lg py-2" id="spinner-viewport">
                        <div class="flex gap-2" id="spinner-strip" style="will-change: transform;"></div>
                    </div>

                    <div class="absolute left-6 top-8 bottom-8 w-16 bg-gradient-to-r from-base-200 to-transparent z-10 pointer-events-none rounded-l-lg"></div>
                    <div class="absolute right-6 top-8 bottom-8 w-16 bg-gradient-to-l from-base-200 to-transparent z-10 pointer-events-none rounded-r-lg"></div>
                </div>

                <div id="spinner-result" class="hidden px-6 py-6">
                    <div class="flex flex-col items-center gap-3">
                        <div class="w-24 h-24 rounded-xl overflow-hidden border-2 border-secondary shadow-lg shadow-secondary/30"
                             id="spinner-result-icon"></div>
                        <div class="text-center">
                            <span class="badge badge-secondary badge-lg font-bold mb-1"
                                  id="spinner-result-badge"></span>
                            <h4 class="text-xl font-bold" id="spinner-result-name"></h4>
                            <p class="text-sm text-base-content/60 mt-1"
                               id="spinner-result-progress"></p>
                            <p class="text-secondary font-medium mt-2 italic pr-1"
                               id="spinner-result-flavor"></p>
                        </div>
                    </div>
                </div>

                <div class="px-6 pb-6 flex flex-col items-center gap-3">
                    <div class="flex gap-3">
                        <button class="btn btn-secondary btn-sm" id="spinner-spin-btn">
                            Spin!
                        </button>
                        <button class="btn btn-ghost btn-sm hidden" id="spinner-again-btn">
                            Spin Again
                        </button>
                    </div>
                    <button class="btn btn-ghost btn-xs hidden gap-1 text-base-content/50" id="spinner-set-cover-btn">
                        <span class="inline-flex w-3.5 h-3.5">${ReelSpinner.CAMERA_SVG}</span>
                        Set as Display Cover
                    </button>
                </div>
            </div>
            <form method="dialog" class="modal-backdrop"><button>close</button></form>
        </dialog>
    `;
};

ReelSpinner.prototype._bindEvents = function() {
    const self = this;

    document.getElementById('spinner-spin-btn').addEventListener('click', () => {
        self._startSpin();
    });

    document.getElementById('spinner-again-btn').addEventListener('click', () => {
        self._resetSpinner();
        self._startSpin();
    });

    document.getElementById('spinner-close-btn').addEventListener('click', () => {
        document.getElementById('pick-next-game-modal').close();
    });

    document.getElementById('spinner-set-cover-btn').addEventListener('click', () => {
        self._setWinnerAsCover();
    });

    document.getElementById('spinner-mute-btn').addEventListener('click', () => {
        self._toggleMute();
    });

    document.getElementById('pick-next-game-modal').addEventListener('close', () => {
        if (self._isSpinning) {
            self._isSpinning = false;
            if (self._animFrameId) {
                cancelAnimationFrame(self._animFrameId);
                self._animFrameId = null;
            }
        }

        // Cancel any pending result timeouts to prevent stale DOM manipulation
        if (self._resultTimeoutId) {
            clearTimeout(self._resultTimeoutId);
            self._resultTimeoutId = null;
        }
        if (self._resultInnerTimeoutId) {
            clearTimeout(self._resultInnerTimeoutId);
            self._resultInnerTimeoutId = null;
        }

        // Highlight winning slot on the grid
        if (self._lastWinnerKey && self._winner) {
            const selector = self.config.gridSelector(self._winner);
            const gridSlot = document.querySelector(selector);
            if (gridSlot) {
                gridSlot.classList.add('spinner-grid-highlight');
                setTimeout(() => gridSlot.classList.remove('spinner-grid-highlight'), 2500);
            }
        }
    });
};

// ─── Reel Strip ──────────────────────────────────────────────────────────────

ReelSpinner.prototype._buildReelStrip = function() {
    const strip = document.getElementById('spinner-strip');
    strip.innerHTML = '';
    strip.style.transform = 'translateX(0)';

    // Reset knife state
    this._knifeTileIndex = -1;
    this._knifeLanded = false;
    this._nearKnifeHit = false;

    // Roll knife chances once per spin
    const knifeLandRoll = Math.random() < ReelSpinner.KNIFE_LAND_CHANCE;
    const knifeAppears = knifeLandRoll || Math.random() < ReelSpinner.KNIFE_APPEAR_CHANCE;
    if (knifeLandRoll) this._knifeLanded = true;

    const e = PlatPursuit.HTMLUtils.escape;
    const slots = this._spinnerSlots;
    const labelClass = this.config.tileLabelClass || 'text-xs font-black';

    const repeatCount = Math.max(Math.ceil(70 / slots.length), 8);
    this._reelItems = [];

    for (let i = 0; i < repeatCount; i++) {
        const shuffled = [...slots].sort(() => Math.random() - 0.5);
        for (const slot of shuffled) {
            this._reelItems.push(slot);
            strip.insertAdjacentHTML('beforeend', `
                <div class="spinner-tile flex-shrink-0 w-24 h-32 rounded-lg border-2
                            border-base-300 bg-base-100 flex flex-col items-center
                            justify-center gap-1 p-1.5 transition-all duration-300">
                    <span class="${labelClass} text-secondary text-center">${e(this.config.tileLabel(slot))}</span>
                    <img src="${e(this.config.tileIcon(slot))}" alt=""
                         class="w-16 h-16 rounded object-cover" />
                    <span class="text-[0.6rem] text-base-content/50 leading-tight
                                 line-clamp-2 text-center w-full">${e(this.config.tileName(slot))}</span>
                </div>
            `);
        }
    }

    // Knife insertion is deferred to _insertKnifeTile() so it can be placed
    // relative to the winner tile (called after _pickWinner in _startSpin)
    this._knifeAppears = knifeAppears;
};

/**
 * Insert the knife tile near the winner. Called after _pickWinner() so we
 * know this._winnerTileIndex. The knife spawns within a 10-tile window
 * around the winner: 8 tiles ahead (before) or 2 tiles behind (after).
 */
ReelSpinner.prototype._insertKnifeTile = function() {
    if (!this._knifeAppears || this._reelItems.length === 0) return;

    const e = PlatPursuit.HTMLUtils.escape;
    const strip = document.getElementById('spinner-strip');
    const totalTiles = this._reelItems.length;
    const winner = this._winnerTileIndex;

    // Knife-land: insert at the winner position so the reel lands on it
    // Knife-appear: insert within a 10-tile window around the winner
    //   -8 (ahead in scroll, passes center before winner) to +2 (behind)
    let knifeIdx;
    if (this._knifeLanded) {
        knifeIdx = winner;
    } else {
        const offsets = [];
        for (let o = -8; o <= 2; o++) {
            if (o === 0) continue; // Never on top of the winner itself
            const idx = winner + o;
            if (idx >= 0 && idx < totalTiles) offsets.push(idx);
        }
        if (offsets.length === 0) return;
        knifeIdx = offsets[Math.floor(Math.random() * offsets.length)];
    }

    // Build the knife's own tile HTML
    const knifeTileHTML = `
        <div class="spinner-tile spinner-tile-knife flex-shrink-0 w-24 h-32 rounded-lg border-2
                    border-base-300 bg-base-100 flex flex-col items-center
                    justify-center gap-1 p-1.5 transition-all duration-300">
            <span class="text-xs font-black text-warning text-center">???</span>
            <img src="${e(ReelSpinner.KNIFE_IMAGE)}" alt=""
                 class="w-16 h-16 rounded object-contain" />
            <span class="text-[0.6rem] text-warning/70 leading-tight
                         text-center w-full font-bold">Rare Drop</span>
        </div>
    `;

    // Insert into the DOM before the tile at knifeIdx
    const existingTiles = strip.querySelectorAll('.spinner-tile');
    const refTile = existingTiles[knifeIdx];
    if (refTile) {
        refTile.insertAdjacentHTML('beforebegin', knifeTileHTML);
    } else {
        strip.insertAdjacentHTML('beforeend', knifeTileHTML);
    }

    // Splice a null sentinel into _reelItems so indices stay aligned with DOM
    this._reelItems.splice(knifeIdx, 0, null);
    this._knifeTileIndex = knifeIdx;

    if (this._knifeLanded) {
        // Knife-land: the knife IS the winner, reel should land on it
        this._winner = null;
        this._winnerTileIndex = knifeIdx;
    } else if (knifeIdx <= this._winnerTileIndex) {
        // Knife was inserted before the winner, shift winner index by 1
        this._winnerTileIndex += 1;
    }
};

// ─── Winner Selection ────────────────────────────────────────────────────────

ReelSpinner.prototype._pickWinner = function() {
    // Always pick a normal game winner first (used for positioning even on knife-land)
    const idx = Math.floor(Math.random() * this._spinnerSlots.length);
    this._winner = this._spinnerSlots[idx];

    const winnerKey = this.config.winnerKey(this._winner);

    // Collect ALL tile indices matching the winner, excluding the knife tile
    const candidates = [];
    for (let i = 0; i < this._reelItems.length; i++) {
        if (this._reelItems[i] && this.config.winnerKey(this._reelItems[i]) === winnerKey) {
            candidates.push(i);
        }
    }

    // Filter to tiles past 30% of the reel (ensures decent scroll distance)
    const minIndex = Math.floor(this._reelItems.length * 0.3);
    const viable = candidates.filter(i => i >= minIndex);

    // Pick randomly from viable candidates (fallback to all candidates)
    const pool = viable.length > 0 ? viable : candidates;
    this._winnerTileIndex = pool[Math.floor(Math.random() * pool.length)];
};

ReelSpinner.prototype._calculateFinalPosition = function() {
    const viewport = document.getElementById('spinner-viewport');
    const viewportWidth = viewport.offsetWidth;
    const tw = ReelSpinner.TILE_WIDTH;

    const tileCenter = (this._winnerTileIndex * tw) + (tw / 2);
    const viewportCenter = viewportWidth / 2;

    // Small random jitter so it doesn't always land dead-center
    const jitter = (Math.random() - 0.5) * (tw * 0.3);

    this._finalTranslateX = -(tileCenter - viewportCenter + jitter);
};

// ─── Animation ───────────────────────────────────────────────────────────────

ReelSpinner.prototype._startSpin = function() {
    const spinBtn = document.getElementById('spinner-spin-btn');
    const againBtn = document.getElementById('spinner-again-btn');
    spinBtn.disabled = true;
    spinBtn.innerHTML = '<span class="loading loading-spinner loading-xs"></span>';
    againBtn.classList.add('hidden');

    this._isSpinning = true;
    this._lastWinnerKey = null;

    // Show spinner area, hide result
    document.getElementById('spinner-area').classList.remove('hidden');
    document.getElementById('spinner-result').classList.add('hidden');

    this._buildReelStrip();
    this._pickWinner();
    this._insertKnifeTile();
    this._calculateFinalPosition();

    const strip = document.getElementById('spinner-strip');
    const endX = this._finalTranslateX;
    const totalDistance = Math.abs(endX);

    // Duration scales with distance, clamped between 3-6 seconds
    const duration = Math.min(Math.max(totalDistance * 1.2, 3000), 6000);
    const startTime = performance.now();

    this._onSpinStart();

    const self = this;
    const animate = (now) => {
        if (!self._isSpinning) return;

        const elapsed = now - startTime;
        const progress = Math.min(elapsed / duration, 1);

        // Cubic ease-out: fast start, smooth deceleration
        const eased = 1 - Math.pow(1 - progress, 3);
        const currentX = endX * eased;
        strip.style.transform = `translateX(${currentX}px)`;

        self._onTick(progress);

        if (progress < 1) {
            self._animFrameId = requestAnimationFrame(animate);
        } else {
            self._onLandResult();
        }
    };

    this._animFrameId = requestAnimationFrame(animate);
};

ReelSpinner.prototype._onLandResult = function() {
    this._isSpinning = false;
    this._lastWinnerKey = this._winner ? this.config.winnerKey(this._winner) : null;

    // Detect near-miss: winner adjacent to knife (but not a knife landing itself)
    this._nearKnifeHit = !this._knifeLanded && this._knifeTileIndex >= 0 &&
        Math.abs(this._winnerTileIndex - this._knifeTileIndex) <= 1;

    // Play reveal fanfare (knife landing gets the dramatic version)
    if (this._knifeLanded) {
        this._playKnifeFanfare();
    } else {
        this._playRevealFanfare();
    }

    // Highlight the winning tile (gold for knife, secondary for normal)
    const tiles = document.querySelectorAll('#spinner-strip .spinner-tile');
    const winningTile = tiles[this._winnerTileIndex];
    if (winningTile) {
        if (this._knifeLanded) {
            winningTile.classList.add('border-warning', 'scale-110', 'spinner-winner-glow');
        } else {
            winningTile.classList.add('border-secondary', 'scale-110', 'spinner-winner-glow');
        }
    }

    // Brief pause, then show result
    const self = this;
    this._resultTimeoutId = setTimeout(() => {
        self._resultTimeoutId = null;
        self._showResult();
    }, 600);
};

ReelSpinner.prototype._showResult = function() {
    // Guard: skip if modal was closed before this fired
    const modal = document.getElementById('pick-next-game-modal');
    if (!modal || !modal.open) return;

    const e = PlatPursuit.HTMLUtils.escape;
    const w = this._winner;
    const spinnerArea = document.getElementById('spinner-area');
    const isKnifeWin = this._knifeLanded;

    // Populate result card while still hidden
    const resultIcon = document.getElementById('spinner-result-icon');
    const resultBadge = document.getElementById('spinner-result-badge');
    const resultName = document.getElementById('spinner-result-name');
    const resultProgress = document.getElementById('spinner-result-progress');
    const resultFlavor = document.getElementById('spinner-result-flavor');

    if (isKnifeWin) {
        // Knife celebration: no game, pure glory
        resultIcon.innerHTML =
            `<img src="${e(ReelSpinner.KNIFE_IMAGE)}" alt="Knife" class="w-full h-full object-contain p-1" />`;
        resultIcon.classList.remove('border-secondary', 'shadow-secondary/30');
        resultIcon.classList.add('border-warning', 'shadow-warning/30');
        resultBadge.textContent = 'KNIFE!';
        resultBadge.classList.remove('badge-secondary');
        resultBadge.classList.add('badge-warning');
        const winText = ReelSpinner.KNIFE_WIN_TEXTS[Math.floor(Math.random() * ReelSpinner.KNIFE_WIN_TEXTS.length)];
        resultName.textContent = winText;
        resultProgress.textContent = '';
        resultFlavor.textContent = '1 in 1,000';

        // Track the rare knife landing
        PlatPursuit.API.post('/api/v1/tracking/site-event/', {
            event_type: 'easter_egg',
            object_id: 'knife_landed'
        }).catch(() => {});
    } else {
        // Normal game result
        resultIcon.innerHTML =
            `<img src="${e(this.config.tileIcon(w))}" alt="" class="w-full h-full object-cover" />`;
        resultIcon.classList.remove('border-warning', 'shadow-warning/30');
        resultIcon.classList.add('border-secondary', 'shadow-secondary/30');
        resultBadge.textContent = this.config.resultBadge(w);
        resultBadge.classList.remove('badge-warning');
        resultBadge.classList.add('badge-secondary');
        resultName.textContent = this.config.tileName(w);

        const progressText = w.progress > 0
            ? `${w.progress}% complete`
            : 'Not started yet';
        resultProgress.textContent = progressText;

        // Near-miss: override flavor text if winner landed adjacent to a knife
        if (this._nearKnifeHit) {
            const knifeFlavor = ReelSpinner.KNIFE_NEAR_MISS_TEXTS[
                Math.floor(Math.random() * ReelSpinner.KNIFE_NEAR_MISS_TEXTS.length)
            ];
            resultFlavor.textContent = knifeFlavor;
        } else {
            const flavor = ReelSpinner.FLAVOR_TEXTS[Math.floor(Math.random() * ReelSpinner.FLAVOR_TEXTS.length)];
            resultFlavor.textContent = flavor;
        }
    }

    // Phase 1: Fade out the reel (opacity only, no layout shift)
    spinnerArea.classList.add('animate-spinner-fade-out');

    // Phase 2: Once fade completes, swap to result card
    const self = this;
    this._resultInnerTimeoutId = setTimeout(() => {
        self._resultInnerTimeoutId = null;

        // Guard: skip if modal was closed during fade
        const innerModal = document.getElementById('pick-next-game-modal');
        if (!innerModal || !innerModal.open) return;

        spinnerArea.classList.add('hidden');
        spinnerArea.classList.remove('animate-spinner-fade-out');

        // Reveal result card
        const resultEl = document.getElementById('spinner-result');
        resultEl.classList.remove('hidden');
        resultEl.classList.add('animate-spinner-fade-in');

        // Update buttons
        document.getElementById('spinner-spin-btn').classList.add('hidden');
        document.getElementById('spinner-again-btn').classList.remove('hidden');

        // Show "Set as Display Cover" button (hidden for knife wins)
        const coverBtn = document.getElementById('spinner-set-cover-btn');
        if (coverBtn) {
            if (isKnifeWin) {
                coverBtn.classList.add('hidden');
            } else {
                coverBtn.classList.remove('hidden', 'text-success');
                coverBtn.classList.add('text-base-content/50');
                coverBtn.disabled = false;
                coverBtn.innerHTML = `
                    <span class="inline-flex w-3.5 h-3.5">${ReelSpinner.CAMERA_SVG}</span>
                    Set as Display Cover
                `;
            }
        }

        // Fire confetti + pop sound (gold confetti for knife, rainbow for normal)
        self._playConfettiPop();
        if (PlatPursuit.CelebrationManager) {
            PlatPursuit.CelebrationManager.loadConfetti().then(() => {
                if (typeof confetti !== 'function') return;
                const canvas = document.getElementById('spinner-confetti-canvas');
                if (!canvas) return;
                const myConfetti = confetti.create(canvas, { resize: true });
                if (isKnifeWin) {
                    // Gold confetti burst for knife win
                    myConfetti({
                        particleCount: 150,
                        spread: 100,
                        origin: { x: 0.5, y: 0.5 },
                        colors: ['#fbbf24', '#f59e0b', '#d97706', '#fcd34d', '#fffbeb'],
                        disableForReducedMotion: true,
                    });
                } else {
                    myConfetti({
                        particleCount: 80,
                        spread: 70,
                        origin: { x: 0.5, y: 0.5 },
                        colors: ['#67d1f8', '#f472b6', '#a855f7', '#3b82f6', '#22c55e', '#eab308'],
                        disableForReducedMotion: true,
                    });
                }
            }).catch(() => {});
        }
    }, 400);
};

// ─── Cover API ───────────────────────────────────────────────────────────────

ReelSpinner.prototype._setWinnerAsCover = async function() {
    const btn = document.getElementById('spinner-set-cover-btn');
    if (!btn || !this._winner) return;

    btn.disabled = true;
    btn.innerHTML = '<span class="loading loading-spinner loading-xs"></span>';

    try {
        await PlatPursuit.API.patch(
            this.config.coverApiUrl(this._challengeId),
            this.config.coverPayload(this._winner)
        );
        btn.innerHTML = `
            <svg xmlns="http://www.w3.org/2000/svg" class="w-3.5 h-3.5" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M5 13l4 4L19 7"/></svg>
            Display Cover Set!
        `;
        btn.classList.remove('text-base-content/50');
        btn.classList.add('text-success');
        PlatPursuit.ToastManager.success(`Display cover set to ${this.config.tileName(this._winner)}`);
    } catch (error) {
        btn.disabled = false;
        btn.innerHTML = `
            <span class="inline-flex w-3.5 h-3.5">${ReelSpinner.CAMERA_SVG}</span>
            Set as Display Cover
        `;
        let msg = 'Failed to set display cover.';
        try { const errData = await error.response?.json(); msg = errData?.error || msg; } catch {}
        PlatPursuit.ToastManager.error(msg);
    }
};

// ─── Reset ───────────────────────────────────────────────────────────────────

ReelSpinner.prototype._resetSpinner = function() {
    this._isSpinning = false;
    if (this._animFrameId) {
        cancelAnimationFrame(this._animFrameId);
        this._animFrameId = null;
    }
    if (this._resultTimeoutId) {
        clearTimeout(this._resultTimeoutId);
        this._resultTimeoutId = null;
    }
    if (this._resultInnerTimeoutId) {
        clearTimeout(this._resultInnerTimeoutId);
        this._resultInnerTimeoutId = null;
    }

    const result = document.getElementById('spinner-result');
    if (result) {
        result.classList.add('hidden');
        result.classList.remove('animate-spinner-fade-in');
    }

    // Show spinner area (clear both hidden and any mid-fade state)
    const spinnerArea = document.getElementById('spinner-area');
    if (spinnerArea) {
        spinnerArea.classList.remove('hidden', 'animate-spinner-fade-out');
    }

    const spinBtn = document.getElementById('spinner-spin-btn');
    if (spinBtn) {
        spinBtn.classList.remove('hidden');
        spinBtn.disabled = false;
        spinBtn.textContent = 'Spin!';
    }

    const againBtn = document.getElementById('spinner-again-btn');
    if (againBtn) againBtn.classList.add('hidden');

    const coverBtn = document.getElementById('spinner-set-cover-btn');
    if (coverBtn) {
        coverBtn.classList.add('hidden');
        coverBtn.classList.remove('text-success');
        coverBtn.classList.add('text-base-content/50');
        coverBtn.disabled = false;
        coverBtn.innerHTML = `
            <span class="inline-flex w-3.5 h-3.5">${ReelSpinner.CAMERA_SVG}</span>
            Set as Display Cover
        `;
    }

    this._buildReelStrip();
};

// ─── Audio System ────────────────────────────────────────────────────────────

ReelSpinner.prototype._initAudio = function() {
    if (this._audioCtx) return;

    const AudioCtx = window.AudioContext || window.webkitAudioContext;
    if (!AudioCtx) return;

    this._audioCtx = new AudioCtx();
    if (this._audioCtx.state === 'suspended') this._audioCtx.resume();

    this._audioGain = this._audioCtx.createGain();
    this._audioGain.gain.value = 0.5;
    this._audioGain.connect(this._audioCtx.destination);

    // Read mute preference
    this._audioMuted = localStorage.getItem('pp_spinner_muted') === '1';
    this._updateMuteIcon();
};

ReelSpinner.prototype._toggleMute = function() {
    this._audioMuted = !this._audioMuted;
    localStorage.setItem('pp_spinner_muted', this._audioMuted ? '1' : '0');
    this._updateMuteIcon();
};

ReelSpinner.prototype._updateMuteIcon = function() {
    const btn = document.getElementById('spinner-mute-btn');
    if (!btn) return;
    btn.innerHTML = this._audioMuted ? ReelSpinner.SPEAKER_OFF_SVG : ReelSpinner.SPEAKER_ON_SVG;
    btn.title = this._audioMuted ? 'Unmute sound' : 'Mute sound';
};

ReelSpinner.prototype._getNoiseBuffer = function() {
    if (this._noiseBuffer) return this._noiseBuffer;
    const size = Math.floor(this._audioCtx.sampleRate * 0.5);
    const buffer = this._audioCtx.createBuffer(1, size, this._audioCtx.sampleRate);
    const data = buffer.getChannelData(0);
    for (let i = 0; i < size; i++) {
        data[i] = Math.random() * 2 - 1;
    }
    this._noiseBuffer = buffer;
    return buffer;
};

ReelSpinner.prototype._playWhoosh = function() {
    if (!this._audioCtx || this._audioMuted) return;

    const ctx = this._audioCtx;
    const now = ctx.currentTime;

    // Filtered white noise with rising cutoff
    const noise = ctx.createBufferSource();
    noise.buffer = this._getNoiseBuffer();

    const filter = ctx.createBiquadFilter();
    filter.type = 'lowpass';
    filter.frequency.setValueAtTime(200, now);
    filter.frequency.exponentialRampToValueAtTime(1200, now + 0.3);

    const gain = ctx.createGain();
    gain.gain.setValueAtTime(0, now);
    gain.gain.linearRampToValueAtTime(0.15, now + 0.05);
    gain.gain.exponentialRampToValueAtTime(0.001, now + 0.4);

    noise.connect(filter);
    filter.connect(gain);
    gain.connect(this._audioGain);
    noise.start(now);
    noise.stop(now + 0.45);
};

ReelSpinner.prototype._playTick = function() {
    if (!this._audioCtx || this._audioMuted) return;

    const ctx = this._audioCtx;
    const now = ctx.currentTime;

    const osc = ctx.createOscillator();
    osc.type = 'sine';
    osc.frequency.value = 800;

    const gain = ctx.createGain();
    gain.gain.setValueAtTime(0.08, now);
    gain.gain.exponentialRampToValueAtTime(0.001, now + 0.02);

    osc.connect(gain);
    gain.connect(this._audioGain);
    osc.start(now);
    osc.stop(now + 0.03);
};

ReelSpinner.prototype._playRevealFanfare = function() {
    if (!this._audioCtx || this._audioMuted) return;

    const ctx = this._audioCtx;
    const now = ctx.currentTime;
    const notes = [523.25, 659.25, 783.99, 1046.50]; // C5, E5, G5, C6

    for (let i = 0; i < notes.length; i++) {
        const osc = ctx.createOscillator();
        const gain = ctx.createGain();

        osc.type = 'sine';
        osc.frequency.value = notes[i];

        const start = now + i * 0.1;
        gain.gain.setValueAtTime(0, start);
        gain.gain.linearRampToValueAtTime(0.25, start + 0.02);
        gain.gain.exponentialRampToValueAtTime(0.001, now + 0.6);

        osc.connect(gain);
        gain.connect(this._audioGain);
        osc.start(start);
        osc.stop(now + 0.65);
    }
};

ReelSpinner.prototype._playConfettiPop = function() {
    if (!this._audioCtx || this._audioMuted) return;

    const ctx = this._audioCtx;
    const now = ctx.currentTime;

    // Confetti cannon: bandpass-filtered noise burst ("pfft!")
    const noise = ctx.createBufferSource();
    noise.buffer = this._getNoiseBuffer();

    const bandpass = ctx.createBiquadFilter();
    bandpass.type = 'bandpass';
    bandpass.frequency.setValueAtTime(1200, now);
    bandpass.frequency.exponentialRampToValueAtTime(600, now + 0.1);
    bandpass.Q.value = 0.8;

    const gain = ctx.createGain();
    gain.gain.setValueAtTime(0.3, now);
    gain.gain.linearRampToValueAtTime(0.25, now + 0.02);
    gain.gain.exponentialRampToValueAtTime(0.001, now + 0.12);

    noise.connect(bandpass);
    bandpass.connect(gain);
    gain.connect(this._audioGain);
    noise.start(now);
    noise.stop(now + 0.15);
};

ReelSpinner.prototype._playKnifeFanfare = function() {
    if (!this._audioCtx || this._audioMuted) return;

    const ctx = this._audioCtx;
    const now = ctx.currentTime;

    // Dramatic ascending fanfare: E5, G#5, B5, E6 (E major, brighter than normal C major)
    const notes = [659.25, 830.61, 987.77, 1318.51];

    for (let i = 0; i < notes.length; i++) {
        const osc = ctx.createOscillator();
        const gain = ctx.createGain();

        osc.type = 'sine';
        osc.frequency.value = notes[i];

        const start = now + i * 0.12;
        gain.gain.setValueAtTime(0, start);
        gain.gain.linearRampToValueAtTime(0.3, start + 0.02);
        gain.gain.exponentialRampToValueAtTime(0.001, now + 0.8);

        osc.connect(gain);
        gain.connect(this._audioGain);
        osc.start(start);
        osc.stop(now + 0.85);
    }

    // Metallic shimmer on top
    const noise = ctx.createBufferSource();
    noise.buffer = this._getNoiseBuffer();

    const hp = ctx.createBiquadFilter();
    hp.type = 'highpass';
    hp.frequency.value = 4000;

    const noiseGain = ctx.createGain();
    noiseGain.gain.setValueAtTime(0, now);
    noiseGain.gain.linearRampToValueAtTime(0.06, now + 0.3);
    noiseGain.gain.exponentialRampToValueAtTime(0.001, now + 0.7);

    noise.connect(hp);
    hp.connect(noiseGain);
    noiseGain.connect(this._audioGain);
    noise.start(now);
    noise.stop(now + 0.75);
};

ReelSpinner.prototype._onSpinStart = function() {
    this._initAudio();
    this._lastTickTime = 0;
    this._lastTickTileIdx = -1;
    this._playWhoosh();
};

ReelSpinner.prototype._onTick = function(progress) {
    if (!this._audioCtx || this._audioMuted) return;

    // Calculate which tile the center indicator is currently over
    const viewport = document.getElementById('spinner-viewport');
    if (!viewport) return;
    const viewportCenter = viewport.offsetWidth / 2;
    const currentX = Math.abs(this._finalTranslateX * (1 - Math.pow(1 - progress, 3)));
    const currentTileIdx = Math.floor((currentX + viewportCenter) / ReelSpinner.TILE_WIDTH);

    // Play tick when we cross a new tile boundary
    if (currentTileIdx !== this._lastTickTileIdx) {
        const now = performance.now();
        // Rate-limit: skip if last tick was <40ms ago (avoid buzzing at high speed)
        if (now - this._lastTickTime > 40) {
            this._playTick();
            this._lastTickTime = now;
        }
        this._lastTickTileIdx = currentTileIdx;
    }
};

// ─── Register ────────────────────────────────────────────────────────────────

window.PlatPursuit = window.PlatPursuit || {};
window.PlatPursuit.ReelSpinner = ReelSpinner;
