/**
 * Hub sub-nav interactions.
 *
 * Desktop: a grouped pill rail that folds its overflowing tail into a "More" dropdown so it never
 * shows a horizontal scrollbar. The rail clips visually (overflow:hidden); this measures which pills
 * pass the edge, hides them, and clones them (grouped) into the More menu so they stay reachable.
 *
 * Mobile: a current-page trigger that opens a grouped sheet. The sheet is visibility:hidden +
 * aria-hidden when closed so its links leave the tab order / AT tree; Escape closes and returns
 * focus to the trigger (the a11y model the chrome audit called exemplary).
 */
document.addEventListener('DOMContentLoaded', function () {
    var root = document.querySelector('[data-subnav]');
    if (!root) return;

    // ── Desktop overflow "More" ───────────────────────────────────────────
    var rail = root.querySelector('[data-subnav-rail]');
    var moreWrap = root.querySelector('[data-subnav-more]');
    var moreBtn = root.querySelector('[data-subnav-more-btn]');
    var moreMenu = root.querySelector('[data-subnav-more-menu]');

    function closeMore() {
        if (!moreWrap) return;
        moreWrap.classList.remove('is-open');
        if (moreMenu) moreMenu.hidden = true;
        if (moreBtn) moreBtn.setAttribute('aria-expanded', 'false');
    }
    function openMore() {
        if (!moreWrap) return;
        moreWrap.classList.add('is-open');
        if (moreMenu) moreMenu.hidden = false;
        if (moreBtn) moreBtn.setAttribute('aria-expanded', 'true');
    }

    function buildMenu(overflowed) {
        moreMenu.textContent = '';
        var order = [];
        var byGroup = {};
        overflowed.forEach(function (p) {
            var label = p.getAttribute('data-group') || '';
            if (!byGroup[label]) { byGroup[label] = []; order.push(label); }
            byGroup[label].push(p);
        });
        order.forEach(function (label) {
            var grp = document.createElement('div');
            grp.className = 'pp-sub__group';
            if (label) {
                var gl = document.createElement('span');
                gl.className = 'pp-sub__gl';
                gl.textContent = label;
                grp.appendChild(gl);
            }
            byGroup[label].forEach(function (p) {
                var a = document.createElement('a');
                a.className = 'pp-subpill' + (p.classList.contains('is-active') ? ' is-active' : '');
                a.href = p.href;
                a.textContent = p.textContent;
                a.setAttribute('role', 'menuitem');
                if (p.hasAttribute('aria-current')) a.setAttribute('aria-current', 'page');
                grp.appendChild(a);
            });
            moreMenu.appendChild(grp);
        });
    }

    function measure() {
        if (!rail || !moreWrap) return;
        // Reset to the full rail, then re-fold. Bail while the desktop row is hidden (<lg): a
        // display:none ancestor makes every rect 0 and would fold everything spuriously.
        closeMore();
        moreWrap.hidden = true;
        var pills = Array.prototype.slice.call(rail.querySelectorAll('[data-subnav-pill]'));
        var groups = Array.prototype.slice.call(rail.querySelectorAll('[data-subnav-group]'));
        pills.forEach(function (p) { p.classList.remove('is-overflow'); });
        groups.forEach(function (g) { g.classList.remove('is-overflow'); });
        if (!rail.offsetParent || rail.clientWidth === 0) return;

        // No overflow at full width -> nothing to fold.
        if (rail.scrollWidth <= rail.clientWidth + 1) return;

        // Reveal More (it reserves width as a rail sibling), then fold every pill past the edge.
        moreWrap.hidden = false;
        var limit = rail.getBoundingClientRect().right - 4;   // small buffer
        var overflowing = false;
        var overflowed = [];
        pills.forEach(function (p) {
            if (!overflowing && p.getBoundingClientRect().right > limit) overflowing = true;
            if (overflowing) { p.classList.add('is-overflow'); overflowed.push(p); }
        });
        if (!overflowed.length) { moreWrap.hidden = true; return; }

        // Hide a group whose pills all folded, so its label doesn't dangle alone.
        groups.forEach(function (g) {
            var gp = g.querySelectorAll('[data-subnav-pill]');
            var all = gp.length > 0;
            for (var i = 0; i < gp.length; i++) { if (!gp[i].classList.contains('is-overflow')) { all = false; break; } }
            if (all) g.classList.add('is-overflow');
        });
        buildMenu(overflowed);
    }

    if (moreBtn) {
        moreBtn.addEventListener('click', function (e) {
            e.stopPropagation();
            if (moreWrap.classList.contains('is-open')) closeMore(); else openMore();
        });
    }
    document.addEventListener('click', function (e) {
        if (moreWrap && !moreWrap.contains(e.target)) closeMore();
    });

    // Re-measure on load, font swap, and viewport resize. Observe the nav (viewport-sized, stable)
    // rather than the rail, whose width this function mutates -- observing the rail would loop.
    var rafId = null;
    function scheduleMeasure() {
        if (rafId) return;
        rafId = requestAnimationFrame(function () { rafId = null; measure(); });
    }
    measure();
    if (window.ResizeObserver) {
        new ResizeObserver(scheduleMeasure).observe(root);
    } else {
        window.addEventListener('resize', scheduleMeasure);
    }
    if (document.fonts && document.fonts.ready) { document.fonts.ready.then(measure); }

    // ── Mobile sheet ──────────────────────────────────────────────────────
    var toggle = root.querySelector('[data-subnav-toggle]');
    var sheet = root.querySelector('[data-subnav-sheet]');

    function setSheet(open) {
        if (!sheet) return;
        sheet.classList.toggle('is-open', open);
        sheet.setAttribute('aria-hidden', open ? 'false' : 'true');
        if (toggle) toggle.setAttribute('aria-expanded', open ? 'true' : 'false');
    }
    if (toggle && sheet) {
        toggle.addEventListener('click', function (e) {
            e.stopPropagation();
            setSheet(!sheet.classList.contains('is-open'));
        });
        document.addEventListener('click', function (e) {
            if (sheet.classList.contains('is-open') && !sheet.contains(e.target) && e.target !== toggle) {
                setSheet(false);
            }
        });
    }

    // Escape closes whichever surface is open (sheet returns focus to its trigger).
    document.addEventListener('keydown', function (e) {
        if (e.key !== 'Escape') return;
        if (sheet && sheet.classList.contains('is-open')) { setSheet(false); if (toggle) toggle.focus(); }
        if (moreWrap && moreWrap.classList.contains('is-open')) { closeMore(); if (moreBtn) moreBtn.focus(); }
    });
});
