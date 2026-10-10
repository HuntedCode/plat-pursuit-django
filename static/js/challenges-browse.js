/**
 * challenges-browse.js -- the staggered reveal and infinite scroll for the two public challenge pages.
 *
 * ONE FILE FOR BOTH PAGES. `/community/challenges/` and its Hall of Fame emit the same ids
 * (`#items-grid`, `#ch-sentinel`, `#ch-loading`), so binding by id serves both -- two files for two pages
 * that behave identically is two things to keep in step.
 *
 * THEY NO LONGER EMIT THE SAME ENTRY CLASS, and this file said they did. Challenges draws `.pp-crun` cards
 * and the Hall of Fame draws `.pp-chero` heroes, because an in-flight run and a finished one are not the
 * same object. A selector naming only the card would leave every Hall of Fame row unrevealed AND uncounted
 * by the scroller, which reads as the page being broken rather than as a missing animation.
 *
 * SO BOTH CARRY `.pp-centry`, A SHARED HOOK, rather than this file listing the two classes. That is not
 * tidiness: `staggerReveal` builds its query by CONCATENATION --
 * `querySelectorAll(sel + ':not(.pp-revealing):not(.is-revealed)')`, the unconditional initial-batch query
 * inside `staggerReveal` (named rather than cited by line, which rots on the next edit to `utils.js`) -- and a comma-separated
 * `sel` binds those guards to the LAST term only. `'.pp-crun, .pp-chero'` would expand to
 * `.pp-crun, .pp-chero:not(.pp-revealing):not(.is-revealed)`, so every already-revealed `.pp-crun` would be
 * re-animated on each observer pass. One class, one term, no concatenation hazard -- and the CSS reveal pair
 * in `challenges.css` keys on the same hook for the same reason.
 *
 * Everything filter-shaped is `browse-filters.js` already: live search, auto-submit on the type radios and
 * the sort select, the URL push. This file owns only what that controller does not -- the reveal and the
 * scroll -- on the shared engines every other browse grid uses.
 *
 * Wired through `PlatPursuit.onPageReady(boot)`, which runs on first load AND on an htmx history restore, so
 * both handles are torn down and rebuilt rather than doubling up.
 */
(function () {
    'use strict';
    var PP = window.PlatPursuit || {};

    var scroller = null, revealHandle = null;

    // ONE DEFINITION, two readers, and a SINGLE-TERM selector by requirement rather than by preference --
    // see the concatenation note in the file header. Both entry partials carry this class beside their own.
    var ENTRY = '.pp-centry';

    function initReveal() {
        if (revealHandle) { revealHandle.disconnect(); revealHandle = null; }
        var grid = document.getElementById('items-grid');
        if (!grid) { return; }
        // THE BLANK-GRID ESCAPE HATCH, and it is the only place this safety can live. `pp-reveal` is baked
        // into the partial by the SERVER (htmx's settle step would strip a class added here after a swap),
        // and `.pp-reveal .pp-centry { opacity: 0 }` holds every row hidden until something reveals it. So if
        // `utils.js` is missing, stale-cached or threw before `staggerReveal` was assigned, bailing quietly
        // would leave a page of invisible rows above a populated `data-result-count` -- a blank grid that
        // reads as a server bug. `gamelists.js` names this hazard; nothing was acting on it here.
        //
        // IT HAS TO COVER THE NULL RETURN TOO, which the first version of this missed while claiming to be
        // "the only place this safety can live". `staggerReveal` bails to `null` with `PP.staggerReveal`
        // perfectly present -- no `IntersectionObserver`, or no card matching the selector -- and it returns
        // BEFORE it adds `pp-reveal` itself, so it never cleans up the copy the SERVER baked in. A browser
        // without IO would have held a populated grid invisible. Reduced motion also returns `null`, but
        // `.pp-reveal .pp-centry { opacity: 0 }` is gated on `no-preference`, so clearing it there is a
        // no-op rather than a second behaviour.
        if (!PP.staggerReveal) { grid.classList.remove('pp-reveal'); return; }
        // THE SAME GRAMMAR AS THE OTHER BROWSE GRIDS, on the same shared engine: a fade on an ease-out and a
        // rise on a slight overshoot, so the cards settle rather than snapping.
        var fadeEase = 'cubic-bezier(0.2, 0.8, 0.2, 1)';
        var springEase = 'cubic-bezier(0.34, 1.4, 0.64, 1)';
        revealHandle = PP.staggerReveal({
            grid: grid, cardSelector: ENTRY, step: 22,
            reveal: function (el, delayMs) {
                if (!el.animate) { return; }
                el.animate([{ opacity: 0 }, { opacity: 1 }],
                           { duration: 420, delay: delayMs, easing: fadeEase, fill: 'backwards' });
                el.animate([{ transform: 'translateY(14px) scale(0.965)' }, { transform: 'none' }],
                           { duration: 500, delay: delayMs, easing: springEase, fill: 'backwards' });
            },
        });
        if (!revealHandle) { grid.classList.remove('pp-reveal'); }
    }

    function initScroller() {
        if (scroller && scroller.destroy) { scroller.destroy(); scroller = null; }
        if (!PP.InfiniteScroller) { return; }
        // READ FROM THE GRID, not hardcoded. The scroller uses `paginateBy` to work out which page to
        // resume from after an htmx history restore, so a value that disagrees with the server's
        // `paginate_by` re-fetches a page already in the DOM and appends duplicates. This was the literal
        // `24` with a comment insisting it match `_ChallengeBrowseView.paginate_by` -- which stopped being
        // possible the moment the Hall of Fame took its own page size (8, to keep its cover fan-out inside
        // `cover_games_for`'s documented budget). One literal cannot match two views.
        var grid = document.getElementById('items-grid');
        var pageSize = parseInt((grid && grid.dataset.pageSize) || '', 10);
        scroller = PP.InfiniteScroller.create({
            gridId: 'items-grid', sentinelId: 'ch-sentinel', loadingId: 'ch-loading',
            // A missing or junk attribute falls back to 24 rather than to NaN, which
            // `Math.ceil(loaded / NaN)` would turn into a resume page of NaN and a dead scroller.
            paginateBy: pageSize > 0 ? pageSize : 24, cardSelector: ENTRY,
            // NEWLY APPENDED CARDS JOIN THE REVEAL. Without this a scrolled page's cards are already opaque
            // when they arrive, so the grid animates for the first 24 rows and then stops -- which reads as
            // the animation breaking rather than as a deliberate end.
            onAppend: function (nodes) { if (revealHandle) { revealHandle.observe(nodes); } },
        });
    }

    function boot() {
        initReveal();
        initScroller();
    }

    // REBOUND AFTER EVERY FILTER SWAP. htmx replaces `#browse-results`' contents, so the `#items-grid` both
    // handles were watching is a detached node afterwards -- the scroller would page forever against
    // something nobody can see, and the reveal would never fire for the new cards.
    document.body.addEventListener('htmx:afterSwap', function (event) {
        if (event.target && event.target.id === 'browse-results') { boot(); }
    });

    if (PP.onPageReady) { PP.onPageReady(boot); } else { document.addEventListener('DOMContentLoaded', boot); }
}());
