/*
 * Tiers, Grids & Polls browse -- page motion only. Filtering (search + sort) is HTMX-native via
 * browse-filters.js; this owns the header count-up, the staggered tile reveal, infinite scroll and
 * the settle.
 *
 * Deliberately thinner than lists-browse.js: no sticky mini-bar, and no shape toggle to keep in step
 * -- the tabs are LINKS to three separate URLs, so switching shape is a navigation the browser owns
 * rather than a control this file has to synchronise.
 *
 * Wired via PlatPursuit.onPageReady(boot): element wiring re-runs on first load AND on an HTMX
 * Back/Forward history restore; body-level listeners bind once, guarded by `first`.
 */
(function () {
    var PP = window.PlatPursuit || {};

    var scroller = null, revealHandle = null, countLast = null;

    // NO `cellSelector` HERE, and that is not an omission. The game card's scroller passes one
    // because its cards are wrapped so a quick-add button can sit beside the anchor; a prompt tile is
    // a single `<a>` with nothing beside it, so the card IS the cell.
    var CARD = '.pp-gtile';

    function initReveal() {
        if (revealHandle) { revealHandle.disconnect(); revealHandle = null; }
        var grid = document.getElementById('items-grid');
        if (!grid || !PP.staggerReveal) { return; }
        var fadeEase = 'cubic-bezier(0.2, 0.8, 0.2, 1)';
        var springEase = 'cubic-bezier(0.34, 1.4, 0.64, 1)';
        revealHandle = PP.staggerReveal({
            grid: grid, cardSelector: CARD, step: 22,
            reveal: function (el, delayMs) {
                if (!el.animate) { return; }
                el.animate([{ opacity: 0 }, { opacity: 1 }],
                           { duration: 420, delay: delayMs, easing: fadeEase, fill: 'backwards' });
                el.animate([{ transform: 'translateY(14px) scale(0.965)' }, { transform: 'none' }],
                           { duration: 500, delay: delayMs, easing: springEase, fill: 'backwards' });
            },
        });
    }

    function initScroller() {
        if (scroller && scroller.destroy) { scroller.destroy(); scroller = null; }
        if (!PP.InfiniteScroller) { return; }
        scroller = PP.InfiniteScroller.create({
            gridId: 'items-grid', sentinelId: 'pr-sentinel', loadingId: 'pr-loading',
            paginateBy: 24, cardSelector: CARD,       // matches BrowsePromptsView.paginate_by
            onAppend: function (nodes) { if (revealHandle) { revealHandle.observe(nodes); } },
        });
    }

    // The header total ticks to the new value, read off the freshly-swapped grid rather than
    // re-fetched -- the count the page has already paid for.
    function tickCount() {
        var grid = document.getElementById('items-grid');
        var headEl = document.getElementById('pr-count');
        if (!grid || !headEl) { return; }
        var newVal = parseFloat(grid.getAttribute('data-result-count'));
        if (isNaN(newVal)) { return; }
        if (countLast === null) { countLast = parseFloat(headEl.dataset.countup); }
        headEl.dataset.countup = newVal;
        if (PP.countUp && !isNaN(countLast) && countLast !== newVal) {
            PP.countUp(headEl, 700, { from: countLast });
        } else {
            headEl.textContent = newVal.toLocaleString();
        }
        countLast = newVal;
    }

    /**
     * The create dialog.
     *
     * A native `<dialog>` on the `.gd-modal` recipe, so the focus trap, Escape and focus restoration
     * come from the element rather than from this file. `dismissableSheet` adds the touch grab pill
     * and the swipe-down dismiss the site uses for sheets.
     *
     * THIS POSTS JSON RATHER THAN SUBMITTING, unlike the lists create dialog, and the difference is
     * the refusal. `create_prompt` can decline for reasons the hunter can act on -- the prompt cap,
     * a banned word, a title that sanitizes to nothing -- and the endpoint hands those back in its
     * own words. A plain form submit would replace the page with an error and lose what was typed;
     * here the refusal lands in the dialog beside the field it is about.
     *
     * The destination is `detail_url` FROM THE SERVER, never a path assembled here from the id.
     */
    function wireCreate(first) {
        if (!first) { return; }                 // bound once: the dialog is outside the swap target
        var dialog = document.getElementById('pr-create');
        var open = document.querySelector('[data-pr-create-open]');
        if (!dialog || !open || !dialog.showModal) { return; }

        var form = dialog.querySelector('[data-pr-create-form]');
        var error = dialog.querySelector('[data-pr-create-error]');
        var save = dialog.querySelector('[data-pr-create-save]');

        function close() {
            if (dialog.open) { dialog.close(); }
        }

        function fail(message) {
            if (!error) { return; }
            error.textContent = message;
            error.hidden = false;
        }

        // ── the grid-only fields ──
        //
        // REVEALED, not injected: the markup is server-rendered with its options and this only
        // toggles `hidden`, so there is no second copy of the size list in the client.
        var gridFields = dialog.querySelector('[data-pr-create-grid]');

        function syncShape() {
            if (!gridFields) { return; }
            var picked = dialog.querySelector('input[name="shape"]:checked');
            gridFields.hidden = !picked || picked.value !== 'grid';
        }

        Array.prototype.forEach.call(
            dialog.querySelectorAll('input[name="shape"]'),
            function (radio) { radio.addEventListener('change', syncShape); });

        open.addEventListener('click', function () {
            if (error) { error.hidden = true; error.textContent = ''; }
            syncShape();          // the dialog reopens on whatever shape was left selected
            dialog.showModal();
            var title = dialog.querySelector('[data-pr-create-title]');
            if (title) { title.focus(); }
        });

        Array.prototype.forEach.call(
            dialog.querySelectorAll('[data-pr-create-close]'),
            function (button) { button.addEventListener('click', close); });

        if (PP.dismissableSheet) { PP.dismissableSheet(dialog, { onClose: close }); }

        if (form) {
            form.addEventListener('submit', function (e) {
                e.preventDefault();
                if (error) { error.hidden = true; }
                if (save) { save.disabled = true; }

                // THE RECTANGLE, AS TWO NUMBERS. The select carries "4x3" because one control is
                // one decision for the author; the service takes columns and rows and validates
                // them as a PAIR against its own list, so it never has to parse this string.
                var body = new FormData(form);
                body.delete('grid_size');
                var size = dialog.querySelector('[data-pr-create-size]');
                if (size && size.value) {
                    var parts = size.value.split('x');
                    body.append('grid_columns', parts[0]);
                    body.append('grid_rows', parts[1]);
                }
                var dupes = dialog.querySelector('[data-pr-create-duplicates]');
                body.append('allow_duplicates', dupes && dupes.checked ? 'true' : 'false');

                PP.postJson(form.dataset.url, body)
                    .then(function (data) {
                        // Straight to the new prompt, which is empty and is where the next thing
                        // happens. A toast on the page they are leaving would not be read.
                        window.location.href = data.detail_url;
                    })
                    .catch(function (err) {
                        if (save) { save.disabled = false; }
                        if (window.console && window.console.error) {
                            window.console.error('[prompts-browse] create failed', {
                                status: (err && err.response && err.response.status) || 'no response',
                                error: err,
                            });
                        }
                        // THE SERVICE'S OWN WORDS when it refused -- they name the actual problem
                        // ("you have 25 prompts already"), which a generic apology would discard.
                        fail(err && err.signedOut
                             ? 'You may have been signed out. Reload the page and try again.'
                             : ((err && err.response && err.message) || 'That could not be made.'));
                    });
            });
        }
    }

    function boot(first) {
        initReveal();
        initScroller();
        wireCreate(first);

        if (first) {
            // ON `afterSettle`, NOT `afterSwap`: htmx restores server-rendered attributes during
            // settle, so anything read or written before it can be overwritten a frame later.
            document.body.addEventListener('htmx:afterSettle', function (evt) {
                if (!evt.target || evt.target.id !== 'browse-results') { return; }
                tickCount();
                initReveal();
                initScroller();
            });
        }
    }

    if (PP.onPageReady) { PP.onPageReady(boot); }
    else { document.addEventListener('DOMContentLoaded', function () { boot(true); }); }
})();
