/**
 * Rate My Games -- the rating wizard at /community/rate-my-games/.
 *
 * Owns the QUEUE: fetch a page of unrated games, show one, skip or submit, advance, prefetch before
 * running out. It does NOT own the rating form -- that is `PlatPursuit.RatingFields` (quick-rate.js),
 * the same controller behind the quick-rate modal on Game Detail and the plat-card share flow. This file
 * used to carry a second copy, which had drifted into having no quick take, no live readouts and no
 * field-level errors.
 *
 * The form is attached ONCE and re-pointed per game via setTarget(). Re-attaching per game would stack a
 * fresh set of listeners on the same <form> every time the queue advanced, and one submit would post as
 * many times as you had rated.
 */
window.PlatPursuit = window.PlatPursuit || {};

(function () {
    'use strict';

    var PP = window.PlatPursuit;
    var PAGE = 20;              // queue page size
    var PREFETCH_AT = 5;        // fetch the next page once this many games are left

    function el(id) { return document.getElementById(id); }

    PP.RateMyGames = {
        queue: [],
        index: 0,
        done: 0,                // how many the hunter has got through this session
        total: 0,
        queueType: 'base',
        includeShovelware: false,
        offset: 0,
        hasMore: false,
        loading: false,
        fields: null,           // the RatingFields handle

        init() {
            // Deep-link straight to the DLC queue (?queue_type=dlc), which is what the DLC count on the
            // Community hub links to.
            if (new URLSearchParams(window.location.search).get('queue_type') === 'dlc') {
                this.queueType = 'dlc';
            }

            this.wireTabs();
            this.wireShovelware();
            this.wireActions();
            this.wireTrophyPanel();
            this.wireForm();

            document.querySelectorAll('.scard__value[data-countup]').forEach(function (n) { PP.countUp(n, 850); });

            this.syncTabs();
            // reset() rather than load(): onPageReady runs this again on an HTMX history restore, where the
            // DOM is fresh but this singleton still holds the last visit's queue, index and offset.
            this.reset();
        },

        // ---------------------------------------------------------------- //
        //  Queue selection
        // ---------------------------------------------------------------- //

        tabs() { return Array.prototype.slice.call(document.querySelectorAll('.rmg-qtab')); },

        wireTabs() {
            var self = this;
            // Shared tablist behaviour: click + arrow keys + roving tabindex, and the one-shot ignite bloom
            // on the chip that just became active. It binds the click itself -- adding our own would call
            // setQueue twice per press.
            this.tablist = PP.wireTablist(this.tabs(), {
                ignite: true,
                onSelect: function (tab) { self.setQueue(tab.dataset.queue); },
            });
        },

        setQueue(type) {
            if (!type || type === this.queueType) { return; }
            this.queueType = type;
            this.syncTabs();
            this.reset();
        },

        syncTabs() {
            var self = this;
            this.tabs().forEach(function (tab) {
                var on = tab.dataset.queue === self.queueType;
                tab.classList.toggle('is-active', on);
                tab.setAttribute('aria-selected', on ? 'true' : 'false');
                if (on) {
                    var stage = el('rmg-stage');
                    if (stage) { stage.setAttribute('aria-labelledby', tab.id); }
                }
            });
            if (this.tablist) { this.tablist.syncTabindex(); }
        },

        wireShovelware() {
            var self = this;
            var box = el('rmg-shovelware');
            if (!box) { return; }
            this.includeShovelware = box.checked;
            box.addEventListener('change', function () {
                self.includeShovelware = box.checked;
                self.reset();
            });
        },

        reset() {
            this.queue = [];
            this.index = 0;
            this.done = 0;
            this.total = 0;
            this.offset = 0;
            this.hasMore = false;
            this.load();
        },

        // ---------------------------------------------------------------- //
        //  Fetching
        // ---------------------------------------------------------------- //

        url() {
            var u = '/api/v1/ratings/wizard/queue/?queue_type=' + this.queueType
                + '&limit=' + PAGE + '&offset=' + this.offset;
            if (this.includeShovelware) { u += '&include_shovelware=1'; }
            return u;
        },

        /** DLC comes back grouped by parent concept; flatten it, carrying the parent's identity down. */
        flatten(groups) {
            var flat = [];
            (groups || []).forEach(function (g) {
                (g.items || []).forEach(function (item) {
                    flat.push({
                        concept_id: g.concept_id,
                        unified_title: g.unified_title,
                        concept_icon_url: g.concept_icon_url,
                        slug: g.slug,
                        is_shovelware: !!g.is_shovelware,
                        trophy_group_id: item.trophy_group_id,
                        trophy_group_name: item.trophy_group_name,
                        hours_label: item.hours_label,
                        is_dlc: true,
                    });
                });
            });
            return flat;
        },

        take(data) {
            if (this.queueType === 'dlc') {
                this.hasMore = !!data.has_more;
                var flat = this.flatten(data.groups);
                this.queue = this.offset === 0 ? flat : this.queue.concat(flat);
                if (this.offset === 0) { this.total = data.total_items || 0; }
            } else {
                var page = data.queue || [];
                this.queue = this.offset === 0 ? page : this.queue.concat(page);
                if (this.offset === 0) { this.total = data.count || 0; }
                this.hasMore = !!data.has_more;
            }
        },

        async load() {
            if (this.loading) { return; }
            this.loading = true;
            this.show('loading');

            try {
                this.take(await PP.API.get(this.url()));
                if (this.queue.length === 0) { this.showDone(); } else { this.render(); }
            } catch (error) {
                var data = await error.response?.json().catch(function () { return null; });
                var msg = data?.error || 'Could not load your games. Try again in a moment.';
                PP.ToastManager.error(msg);
                // A toast alone would leave the stage blank, with nothing to press. Say it where they are
                // looking and give them the retry.
                var line = el('rmg-fail-text');
                if (line) { line.textContent = msg; }
                this.show('fail');
            } finally {
                this.loading = false;
            }
        },

        /** Top up the queue in the background, before the hunter reaches the end of it. */
        async prefetch() {
            if (this.loading) { return; }
            this.loading = true;
            try {
                this.take(await PP.API.get(this.url()));
            } catch {
                // Silent: the queue still has games in it, and load() surfaces a failure that matters.
            } finally {
                this.loading = false;
            }
        },

        // ---------------------------------------------------------------- //
        //  Stage
        // ---------------------------------------------------------------- //

        /** Exactly one of loading / card / done / fail shows at a time. */
        show(which) {
            var map = { loading: 'rmg-loading', card: 'rmg-card', done: 'rmg-done', fail: 'rmg-fail' };
            Object.keys(map).forEach(function (key) {
                var node = el(map[key]);
                if (node) { node.classList.toggle('hidden', key !== which); }
            });
            var prog = el('rmg-progress');
            if (prog) { prog.classList.toggle('hidden', which !== 'card'); }
        },

        current() { return this.queue[this.index] || null; },

        render() {
            var game = this.current();
            if (!game) { this.showDone(); return; }

            this.show('card');

            var cover = el('rmg-cover');
            var img = el('rmg-cover-img');
            var art = game.concept_icon_url || '';
            if (cover && img) {
                cover.classList.toggle('rmg__cover--none', !art);
                img.src = art;
                img.alt = art ? game.unified_title || '' : '';
            }

            var title = el('rmg-title');
            if (title) { title.textContent = game.unified_title || ''; }

            // The trophy group only earns a line when it isn't just "the base game".
            var isDlc = !!game.is_dlc;
            var group = el('rmg-group');
            var groupName = el('rmg-group-name');
            var named = isDlc || game.trophy_group_id !== 'default';
            if (group) { group.classList.toggle('hidden', !named); }
            if (groupName && named) { groupName.textContent = game.trophy_group_name || ''; }

            var dlcFlag = el('rmg-flag-dlc');
            if (dlcFlag) { dlcFlag.classList.toggle('hidden', !isDlc); }
            var shovelFlag = el('rmg-flag-shovel');
            if (shovelFlag) { shovelFlag.classList.toggle('hidden', !game.is_shovelware); }

            var formTitle = el('rmg-form-title');
            if (formTitle) { formTitle.textContent = isDlc ? 'Rate this DLC' : 'Rate this game'; }

            this.renderStats(game);
            this.pointFormAt(game);
            this.loadTrophies();
            this.renderProgress();
        },

        renderStats(game) {
            var bar = el('rmg-stats');
            if (!bar) { return; }
            var stats = game.stats;
            var any = Boolean(stats || game.platinum_date);
            bar.classList.toggle('hidden', !any);
            if (!any) { return; }

            var set = function (id, on, text) {
                var node = el(id);
                var slot = el(id + '-text');
                if (!node || !slot) { return; }
                node.classList.toggle('hidden', !on);
                if (on) { slot.innerHTML = text; }
            };

            var platted = '';
            if (game.platinum_date) {
                var d = new Date(game.platinum_date);
                platted = 'Platted ' + d.toLocaleDateString(undefined, { month: 'short', day: 'numeric', year: 'numeric' });
            }
            // Values are numbers we computed or a formatted date -- no user content reaches innerHTML here.
            set('rmg-stat-plat', Boolean(platted), platted);
            set('rmg-stat-trophies', Boolean(stats), stats ? '<b>' + stats.earned_trophies + '</b> / ' + stats.total_trophies + ' trophies' : '');
            set('rmg-stat-progress', Boolean(stats), stats ? '<b>' + stats.progress + '%</b> complete' : '');
            set('rmg-stat-playtime', Boolean(stats && stats.play_hours), stats ? '<b>' + stats.play_hours + 'h</b> played' : '');
        },

        renderProgress() {
            var current = Math.min(this.done + 1, this.total || this.done + 1);
            var noun = this.queueType === 'dlc' ? 'DLC' : 'Game';
            var text = el('rmg-progress-text');
            if (text) { text.textContent = noun + ' ' + current + ' of ' + this.total; }

            var pct = this.total > 0 ? Math.round((this.done / this.total) * 100) : 0;
            var pctEl = el('rmg-progress-pct');
            if (pctEl) { pctEl.textContent = pct + '%'; }
            var fill = el('rmg-progress-fill');
            if (fill) { fill.style.setProperty('--horizon-progress', pct + '%'); }
        },

        showDone() {
            this.show('done');
            var dlcBtn = el('rmg-done-dlc');
            // Only offer the other queue when you're not already in it.
            if (dlcBtn) { dlcBtn.classList.toggle('hidden', this.queueType === 'dlc'); }

            var title = el('rmg-done-title');
            var copy = el('rmg-done-copy');
            var nothing = this.done === 0;
            var noun = this.queueType === 'dlc' ? 'DLC' : 'games';
            if (title) { title.textContent = nothing ? 'Nothing waiting' : 'All caught up'; }
            if (copy) {
                copy.textContent = nothing
                    ? 'There are no unrated ' + noun + ' in your library right now. Finish something and come back.'
                    : "That's every " + noun + ' you had left to rate. Nice work, hunter.';
            }
        },

        // ---------------------------------------------------------------- //
        //  The form (behaviour lives in RatingFields)
        // ---------------------------------------------------------------- //

        wireForm() {
            var self = this;
            var form = el('rmg-form');
            var submit = el('rmg-submit');
            if (!form || !PP.RatingFields) { return; }

            this.fields = PP.RatingFields.attach(form, {
                submitEl: submit,
                submitLabel: 'Submit rating',
                // The one gate: hours. Said inline, next to the button, rather than only as a toast on refusal.
                onChange: function (state) {
                    if (submit) { submit.disabled = !state.ready; }
                    var req = el('rmg-req');
                    if (req) { req.classList.toggle('is-met', state.ready); }
                },
                onSaved: function () {
                    var game = self.current();
                    PP.ToastManager.success((game && game.unified_title ? game.unified_title : 'Rating') + ' rated!');
                    self.advance();
                },
                onError: function (msg) { PP.ToastManager.error(msg); },
            });
        },

        /** Re-point the one attached form at the game now on screen, and clear the last one's answers. */
        pointFormAt(game) {
            if (!this.fields) { return; }
            this.fields.setTarget(game.concept_id, game.trophy_group_id);
            this.fields.label({
                hoursLabel: game.hours_label || 'Hours to Platinum',
                // Their tracked playtime for THIS game, where we have it -- the number they're being asked
                // to estimate is one we can often help with.
                playtimeHint: game.stats && game.stats.play_hours
                    ? 'Your tracked playtime: about ' + game.stats.play_hours + ' hour' + (game.stats.play_hours === 1 ? '' : 's') + '.'
                    : '',
            });
            // No argument = back to the defaults. Both queues serve only UNRATED items, so there is never an
            // existing rating to prefill here.
            this.fields.prefill();
        },

        // ---------------------------------------------------------------- //
        //  Trophy reference panel
        // ---------------------------------------------------------------- //

        isDesktop() { return window.innerWidth >= 1024; },

        wireTrophyPanel() {
            var self = this;
            var toggle = el('rmg-trophy-toggle');
            var body = el('rmg-trophy-body');
            if (!toggle || !body) { return; }

            toggle.addEventListener('click', function () {
                // Desktop keeps the panel open (CSS pins it visible); the header is inert there.
                if (self.isDesktop()) { return; }
                var opening = body.classList.contains('hidden');
                body.classList.toggle('hidden', !opening);
                toggle.setAttribute('aria-expanded', opening ? 'true' : 'false');
                if (opening && !body.dataset.loaded) { self.loadTrophies(true); }
            });
        },

        /**
         * Fetch the current game's trophies. On desktop the panel is open, so this runs on every advance;
         * on smaller screens it waits for the hunter to open it (`force`), because the list is a reference
         * they may never look at and it is a request per game.
         */
        async loadTrophies(force) {
            var game = this.current();
            var body = el('rmg-trophy-body');
            var content = el('rmg-trophy-content');
            var count = el('rmg-trophy-count');
            if (!game || !content || !body) { return; }

            if (!force && !this.isDesktop()) {
                // Collapsed: clear the last game's list so opening it can't show the wrong trophies.
                content.innerHTML = '';
                delete body.dataset.loaded;
                body.classList.add('hidden');
                var toggle = el('rmg-trophy-toggle');
                if (toggle) { toggle.setAttribute('aria-expanded', 'false'); }
                if (count) { count.textContent = ''; }
                return;
            }

            var token = (this._troToken = (this._troToken || 0) + 1);
            content.innerHTML = '<div class="pp-gbrowse__loading"><span class="pp-gbrowse__spinner" aria-hidden="true"></span></div>';
            if (count) { count.textContent = ''; }

            try {
                var data = await PP.API.get('/api/v1/ratings/' + game.concept_id + '/group/' + game.trophy_group_id + '/trophies/');
                // A fast skip can land a stale response after the next game's request went out. Drop it --
                // otherwise the panel shows the previous game's trophy list under the current game's name.
                if (token !== this._troToken) { return; }
                content.innerHTML = PP.TrophyListRenderer.buildList(data.trophies);
                body.dataset.loaded = 'true';
                if (count) { count.textContent = data.count; }
            } catch (error) {
                if (token !== this._troToken) { return; }
                var err = await error.response?.json().catch(function () { return null; });
                content.innerHTML = '<p class="pp-trolist__err">' + PP.HTMLUtils.escape(err?.error || 'Could not load the trophy list.') + '</p>';
            }
        },

        // ---------------------------------------------------------------- //
        //  Advancing
        // ---------------------------------------------------------------- //

        wireActions() {
            var self = this;
            var skip = el('rmg-skip');
            var submit = el('rmg-submit');
            var dlc = el('rmg-done-dlc');
            var retry = el('rmg-retry');

            // Skipping is not progress: it does not move the meter, and the game is still waiting next time.
            if (skip) { skip.addEventListener('click', function () { self.advance(false); }); }
            if (submit) { submit.addEventListener('click', function () { if (self.fields) { self.fields.submit(); } }); }
            if (dlc) { dlc.addEventListener('click', function () { self.setQueue('dlc'); }); }
            // Retries at the SAME offset -- a mid-queue page that failed is the page we still want.
            if (retry) { retry.addEventListener('click', function () { self.load(); }); }
        },

        advance(counts) {
            if (counts !== false) { this.done++; }
            this.index++;

            if (this.hasMore && this.queue.length - this.index <= PREFETCH_AT) {
                this.offset += PAGE;
                this.prefetch();
            }

            if (this.index >= this.queue.length) {
                // Ran out mid-page: if more is coming, wait for it rather than declaring the queue finished.
                if (this.hasMore || this.loading) { this.show('loading'); this.waitForMore(); return; }
                this.showDone();
                return;
            }
            this.deal();
        },

        /** Wait out the in-flight prefetch, then continue. */
        waitForMore() {
            var self = this;
            if (this.loading) { setTimeout(function () { self.waitForMore(); }, 120); return; }
            if (this.index < this.queue.length) { this.deal(); return; }
            // Still short with more to come: the prefetch failed (it fails silently by design). Ask again at
            // the same offset, this time through load(), which reports it. Otherwise a dropped request would
            // read as "all caught up" and hide games the hunter still has waiting.
            if (this.hasMore) { this.load(); return; }
            this.showDone();
        },

        /**
         * The one signature moment on this page: the game just dealt with slides out to the left, the next
         * one arrives from the right. The stage holds the OUTGOING height across the swap so the page
         * doesn't lurch when a 12-trophy game follows a 60-trophy one, and the whole thing collapses to a
         * plain swap under reduced motion.
         */
        deal() {
            var self = this;
            var stage = el('rmg-stage');
            var card = el('rmg-card');
            var still = window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches;
            if (!stage || !card || still) { this.render(); return; }

            stage.style.minHeight = stage.offsetHeight + 'px';
            stage.classList.add('is-dealing');
            setTimeout(function () {
                stage.classList.remove('is-dealing');
                self.render();
                card.classList.remove('pp-view-in-right');
                void card.offsetWidth;              // restart the entrance on every deal
                card.classList.add('pp-view-in-right');
                // Released on the next frame, once the incoming card has laid out at its own height.
                requestAnimationFrame(function () { stage.style.minHeight = ''; });
            }, 180);                                 // matches rmgDealOut
        },
    };
})();
