/**
 * Game Lists page behaviour.
 *
 * Deliberately thin. The character counters come from the shared `PlatPursuit.wireCharCounters`, and
 * swipe-to-dismiss from `PlatPursuit.dismissableSheet` -- the same helper the guidelines sheet uses,
 * which is what gives the modal an iOS-style drag handle on touch instead of a phone-sized dialog
 * with no way to flick it away.
 *
 * The dialog is a NATIVE <dialog>, matching the house pattern for FORM sheets. That is not laziness:
 * it brings a real focus trap, Escape, and an inert background for free, all of which
 * `.pp-detail-modal` (the content-VIEW primitive) hand-rolls. What the first cut got wrong was the
 * skin, not the element -- it wore DaisyUI's `modal-box` and read as a different product.
 */
(function () {
    'use strict';

    function wireCreateDialog() {
        var dialog = document.getElementById('gl-create');
        if (!dialog || dialog.dataset.wired === '1') { return; }
        dialog.dataset.wired = '1';

        // Choreographed exit, the way `.gd-modal` does it: add `.is-closing`, wait for the
        // animation, then close. The standard asks for exits as carefully handled as entrances, and
        // the previous version's comment claimed a single close path existed "so a future exit
        // animation only has to be added in one place" -- this is that place, now used.
        var close = function () {
            if (!dialog.close || !dialog.open) { return; }
            var reduced = window.matchMedia
                && window.matchMedia('(prefers-reduced-motion: reduce)').matches;
            if (reduced) { dialog.close(); return; }
            if (dialog.classList.contains('is-closing')) { return; }
            dialog.classList.add('is-closing');
            var done = function () {
                dialog.classList.remove('is-closing');
                dialog.close();
            };
            dialog.addEventListener('animationend', done, { once: true });
            // A dropped `animationend` would strand the dialog open and un-closable.
            setTimeout(function () {
                if (dialog.classList.contains('is-closing')) { done(); }
            }, 400);
        };

        // DELEGATED, not bound per element. An opener can now appear inside `#my-lists-panel`, which
        // is replaced on every scope switch -- and `wireCreateDialog` returns early on re-entry
        // because the dialog is already wired, so a per-element binding would never reach it. The
        // button would render, look right, and do nothing: the same half-a-pattern trap as baking
        // `pp-reveal` with no observer.
        document.body.addEventListener('click', function (e) {
            if (!e.target.closest || !e.target.closest('[data-gl-open]')) { return; }
            if (dialog.showModal && !dialog.open) { dialog.showModal(); }
        });

        dialog.querySelectorAll('[data-gl-close]').forEach(function (button) {
            button.addEventListener('click', close);
        });

        // Backdrop click. The <dialog> itself is the full-viewport element and the box is inside it,
        // so a click landing on the dialog and not the box is a click on the backdrop.
        dialog.addEventListener('click', function (e) { if (e.target === dialog) { close(); } });

        // Escape goes through the same path as every other close, so a future exit animation only
        // has to be added in one place.
        dialog.addEventListener('cancel', function (e) { e.preventDefault(); close(); });

        // Suggested names fill the field rather than submitting: they are a shortcut past a blank
        // input, not eight ways to create a list. Focus returns to the input afterwards so the
        // hunter can keep typing, and `input` is dispatched so the character counter follows.
        var nameField = dialog.querySelector('#gl-name');
        dialog.querySelectorAll('[data-gl-suggest]').forEach(function (chip) {
            chip.addEventListener('click', function () {
                if (!nameField) { return; }
                nameField.value = chip.textContent.trim();
                nameField.dispatchEvent(new Event('input', { bubbles: true }));
                nameField.focus();
                nameField.setSelectionRange(nameField.value.length, nameField.value.length);
            });
        });

        if (window.PlatPursuit && window.PlatPursuit.dismissableSheet) {
            // `handle` is REQUIRED here, and the helper's own docs say why: omit it on a sheet you
            // READ, pass one on a sheet you OPERATE, "where an accidental dismiss costs unsaved
            // work". This is a form holding a typed name and up to 300 characters of description.
            // Without a handle, a downward swipe starting on the header, a label, the chip row or
            // the footer armed the drag and destroyed the lot past 90px with no confirmation.
            window.PlatPursuit.dismissableSheet(dialog, {
                onClose: close,
                handle: '.gl-dialog__head',
            });
        }
    }


    /**
     * The Mine|Following scope switcher.
     *
     * HTMX does the fetch and the swap; this supplies everything HTMX does not: the roving tabindex
     * and Arrow/Home/End model (`wireTablist`), the one-shot activation bloom (`igniteTab`), and the
     * directional panel slide (`slideViewIn`) -- the three beats the design system and motion-patterns
     * ask a tab group for. `{manual: true}` because the chips are `<a hx-get>`: wireTablist must not
     * also bind click, or the panel switches twice.
     */
    var revealHandle = null;
    var scroller = null;

    /**
     * The staggered tile reveal, on the shared engine -- the same grammar every other tile grid on
     * the site uses.
     *
     * NOT optional here, and that is the whole point: the results partial bakes `pp-reveal` into its
     * markup on htmx requests (it has to -- htmx's settle step restores server attributes on id'd
     * swapped elements, so a class added afterwards is wiped). `.pp-reveal .pp-gcard { opacity: 0 }`
     * then holds every tile hidden until something reveals it. With no observer wired, swapped-in
     * tiles stayed invisible forever and the panel read as blank. The Following tab hid the bug by
     * being empty -- no tiles, nothing to hide -- so it only showed on the way back to Mine.
     *
     * `staggerReveal` bails (returning null, adding no class) under reduced motion or with no cards,
     * and the CSS above is gated on `prefers-reduced-motion: no-preference`, so neither path can
     * leave a tile stuck hidden.
     */
    function initReveal() {
        if (revealHandle) { revealHandle.disconnect(); revealHandle = null; }
        var grid = document.getElementById('my-lists-grid');
        if (!grid || !window.PlatPursuit || !window.PlatPursuit.staggerReveal) { return; }
        var fadeEase = 'cubic-bezier(0.2, 0.8, 0.2, 1)';
        var springEase = 'cubic-bezier(0.34, 1.4, 0.64, 1)';
        revealHandle = window.PlatPursuit.staggerReveal({
            grid: grid, cardSelector: '.pp-gcard', step: 22,
            reveal: function (el, delayMs) {
                if (!el.animate) { return; }
                el.animate([{ opacity: 0 }, { opacity: 1 }],
                           { duration: 420, delay: delayMs, easing: fadeEase, fill: 'backwards' });
                el.animate([{ transform: 'translateY(14px) scale(0.965)' }, { transform: 'none' }],
                           { duration: 500, delay: delayMs, easing: springEase, fill: 'backwards' });
            },
        });
    }

    /**
     * Infinite scroll for the panel.
     *
     * `MyListsView` sets `paginate_by`, and Following is uncapped -- without this the 25th followed
     * list is unreachable by every route. The sentinel and spinner live OUTSIDE the swapped panel so
     * they survive a scope switch; the scroller is re-created against the fresh grid after each one.
     */
    function initScroller() {
        if (scroller && scroller.destroy) { scroller.destroy(); scroller = null; }
        if (!window.PlatPursuit || !window.PlatPursuit.InfiniteScroller) { return; }
        scroller = window.PlatPursuit.InfiniteScroller.create({
            gridId: 'my-lists-grid',
            sentinelId: 'gl-my-sentinel',
            loadingId: 'gl-my-loading',
            paginateBy: 24,               // matches MyListsView.paginate_by
            cardSelector: '.pp-gcard',
            // Newly appended tiles carry the same server-baked `pp-reveal`, so they need the
            // observer too or they append invisible -- the identical trap the swap path hit.
            onAppend: function (nodes) { if (revealHandle) { revealHandle.observe(nodes); } },
        });
    }

    function wireScopeSwitcher(first) {
        var strip = document.querySelector('[data-gl-scopes]');
        if (!strip || strip.dataset.wired === '1') { return; }
        strip.dataset.wired = '1';

        var chips = strip.querySelectorAll('.pp-switch__chip');
        var order = Array.prototype.map.call(chips, function (c) { return c.dataset.scope; });

        var tablist = null;
        if (window.PlatPursuit && window.PlatPursuit.wireTablist) {
            // Keep the handle: it returns `syncTabindex`, which is the documented way to re-sync the
            // roving tabindex after the active chip moves. Discarding it and re-implementing the
            // same two lines inline is a second copy to keep in step.
            tablist = window.PlatPursuit.wireTablist(chips, { manual: true });
        }

        function markActive(chip) {
            Array.prototype.forEach.call(chips, function (c) {
                var on = c === chip;
                c.classList.toggle('is-active', on);
                c.setAttribute('aria-selected', on ? 'true' : 'false');
            });
            if (tablist && tablist.syncTabindex) { tablist.syncTabindex(); }
        }

        function activeChip() {
            for (var i = 0; i < chips.length; i++) {
                if (chips[i].classList.contains('is-active')) { return chips[i]; }
            }
            return null;
        }

        // Everything below binds on DOCUMENT.BODY, which SURVIVES an htmx history restore -- so it
        // binds exactly once. Element wiring above re-runs every time because those nodes are new;
        // these would double-bind. That split is the whole point of `onPageReady(fn(first))`.
        if (!first) { return; }

        // Active state moves on the REQUEST, not on the response: the chip should light the instant
        // it is pressed rather than after a round trip, which is what makes the switch feel local.
        document.body.addEventListener('htmx:beforeRequest', function (e) {
            var el = (e.detail && e.detail.elt) || e.target;
            var chip = el && el.closest
                ? el.closest('[data-gl-scopes] .pp-switch__chip') : null;
            if (!chip) { return; }
            // Re-clicking the tab you are already on: cancel it. Otherwise every press refetches,
            // re-swaps, replays the reveal on tiles that never moved, and -- because the chips carry
            // `hx-push-url` -- pushes a duplicate history entry, so Back needs six presses to
            // escape. Lifted from the badge list, which already learned this.
            if (chip.classList.contains('is-active')) { e.preventDefault(); return; }
            markActive(chip);
            if (window.PlatPursuit && window.PlatPursuit.igniteTab) {
                window.PlatPursuit.igniteTab(chip);
            }
        });

        // The other half of the optimistic update. If the request 500s, is aborted, or is refused by
        // the gate, no swap happens and the chip is left lit on a scope the panel is not showing.
        // Reconcile against what is actually RENDERED rather than against what was clicked, so a
        // superseded request (two fast clicks, no `hx-sync` on the chips) settles correctly too.
        document.body.addEventListener('htmx:afterRequest', function (e) {
            var el = (e.detail && e.detail.elt) || e.target;
            if (!el || !el.closest || !el.closest('[data-gl-scopes] .pp-switch__chip')) { return; }
            var grid = document.getElementById('my-lists-grid');
            if (!grid) { return; }
            var rendered = null;
            Array.prototype.forEach.call(chips, function (c) {
                if (c.dataset.scope === grid.dataset.scope) { rendered = c; }
            });
            if (rendered && rendered !== activeChip()) { markActive(rendered); }
        });

        document.body.addEventListener('htmx:afterSwap', function (e) {
            // Guard on htmx's OWN swap target, not on `e.target` (which can be a swapped-IN child)
            // and not merely on the grid existing. Without it this runs for every swap that bubbles
            // to body -- a toast, an out-of-band update, a future follow button on this page.
            var target = (e.detail && e.detail.target) || e.target;
            if (!target || target.id !== 'my-lists-panel') { return; }

            var grid = document.getElementById('my-lists-grid');
            if (!grid) { return; }
            // Direction comes from the panel's own `data-scope`, which the server rendered -- so the
            // slide follows what actually arrived rather than what was clicked.
            var from = strip.dataset.lastScope || null;
            var to = grid.dataset.scope;
            if (window.PlatPursuit && window.PlatPursuit.slideViewIn) {
                window.PlatPursuit.slideViewIn(grid, from, to, order);
            }
            strip.dataset.lastScope = to;

            // The panel's label is rendered once, outside the swap, so it keeps whichever chip was
            // active on page load unless something moves it -- announcing the Following panel as
            // labelled by the Mine chip.
            var panel = document.querySelector('[data-gl-panel]');
            if (panel) { panel.setAttribute('aria-labelledby', 'gl-scope-' + to); }

            // The swapped grid is a fresh node carrying a server-baked `pp-reveal`, so it needs its
            // own observer or its tiles never become visible -- and the scroller is watching a grid
            // that no longer exists.
            initReveal();
            initScroller();
        });

        var current = document.getElementById('my-lists-grid');
        if (current) { strip.dataset.lastScope = current.dataset.scope; }
    }

    /**
     * `onPageReady(fn(first))` is the shared contract for every HTMX view-swap page, and it exists
     * because the two halves of a restore behave differently: htmx replaces the history element's
     * INNER HTML, so element nodes are all fresh (re-wire them every time -- the old bindings died
     * with the old nodes), while `document.body` itself PERSISTS, so body-level listeners survive
     * and re-binding them would double-fire.
     *
     * The hand-rolled `DOMContentLoaded` + `htmx:historyRestore` pair this replaces got that split
     * wrong -- it re-ran everything, body listeners included -- and its comment asserted htmx
     * replaces the body wholesale, which the shared helper's own docstring contradicts.
     */
    function boot(first) {
        wireCreateDialog();
        wireScopeSwitcher(first);
        initReveal();
        initScroller();
    }

    if (window.PlatPursuit && window.PlatPursuit.onPageReady) {
        window.PlatPursuit.onPageReady(boot);
    } else {
        document.addEventListener('DOMContentLoaded', function () { boot(true); });
    }
})();
