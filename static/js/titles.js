/*
 * Titles page: the Yours / Within reach / All switcher + equip-in-place.
 *
 * All three panels are server-rendered; this only toggles which is visible (with the shared directional
 * slide + ignite) and keeps ?view= in sync. Equipping POSTs the existing /api/v1/equip-title/ contract and
 * patches the DOM in place -- the nameplate is the payoff, so it updates first and flashes.
 *
 * Wired via PlatPursuit.onPageReady so it survives an HTMX Back/Forward restore.
 */
(function () {
    var PP = window.PlatPursuit || {};
    var ORDER = ['yours', 'reach', 'all'];
    var current = 'yours';
    var revealHandle = null;

    function panels() { return document.querySelectorAll('[data-ttl-panel]'); }
    function panelFor(view) { return document.querySelector('[data-ttl-panel="' + view + '"]'); }

    function reveal(view) {
        var panel = panelFor(view);
        if (!panel || !PP.staggerReveal) { return; }
        if (revealHandle) { revealHandle.disconnect(); revealHandle = null; }
        var grid = panel.querySelector('[data-ttl-grid]');
        if (!grid) { return; }
        revealHandle = PP.staggerReveal({
            grid: grid, cardSelector: '.ttl-row', step: 22,
            reveal: function (el, delay) {
                if (!el.animate) { return; }
                el.animate([{ opacity: 0 }, { opacity: 1 }],
                           { duration: 380, delay: delay, easing: 'cubic-bezier(0.2, 0.8, 0.2, 1)', fill: 'backwards' });
                el.animate([{ transform: 'translateY(9px)' }, { transform: 'none' }],
                           { duration: 440, delay: delay, easing: 'cubic-bezier(0.34, 1.4, 0.64, 1)', fill: 'backwards' });
            },
        });
    }

    function show(view) {
        if (view === current) { return; }
        panels().forEach(function (p) { p.hidden = p.dataset.ttlPanel !== view; });
        document.querySelectorAll('[data-ttl-switch] .pp-switch__chip').forEach(function (chip) {
            var on = chip.dataset.view === view;
            chip.classList.toggle('is-active', on);
            chip.setAttribute('aria-selected', on ? 'true' : 'false');
        });
        if (PP.slideViewIn) { PP.slideViewIn(panelFor(view), current, view, ORDER); }
        if (PP.syncViewParam) { PP.syncViewParam(view, { default: 'yours' }); }
        current = view;
        reveal(view);
    }

    // ---- Equip / unequip -------------------------------------------------
    function setPlate(name) {
        var plate = document.querySelector('[data-ttl-plate]');
        var nameEl = document.querySelector('[data-ttl-plate-name]');
        if (!plate || !nameEl) { return; }
        var has = !!name;
        nameEl.textContent = has ? name : 'No title equipped';
        nameEl.classList.toggle('ttl-plate__name--none', !has);
        plate.classList.toggle('ttl-plate--empty', !has);
        if (has) {
            plate.classList.remove('is-flash');
            void plate.offsetWidth;          // restart the beat on a re-equip
            plate.classList.add('is-flash');
        }
    }

    function markWorn(titleId) {
        document.querySelectorAll('[data-ttl-row]').forEach(function (row) {
            var isIt = titleId !== null && String(row.dataset.titleId) === String(titleId);
            row.classList.toggle('is-worn', isIt);
        });
    }

    function equip(titleId, name, btn) {
        if (!PP.API) { return; }
        // Pending state: block the double-tap and say something on a slow network.
        var buttons = document.querySelectorAll('[data-ttl-equip]');
        buttons.forEach(function (b) { b.disabled = true; });
        if (btn) { btn.textContent = 'Wearing…'; }

        PP.API.post('/api/v1/equip-title/', { title_id: titleId }).then(function () {
            setPlate(titleId === null ? null : name);
            markWorn(titleId);            // one class toggle -- the marker/button swap follows in CSS
            if (PP.ToastManager) {
                PP.ToastManager.success(titleId === null ? 'Title removed.' : 'Now wearing "' + name + '".');
            }
        }).catch(function (err) {
            var say = function (m) { if (PP.ToastManager) { PP.ToastManager.error(m); } };
            var msg = 'Could not update your title.';
            if (err && err.response) {
                err.response.json().then(function (d) { say((d && d.error) || msg); }).catch(function () { say(msg); });
            } else { say(msg); }
        }).finally(function () {
            buttons.forEach(function (b) { b.disabled = false; });
            if (btn) { btn.textContent = 'Wear this'; }
        });
    }

    function boot(first) {
        if (first && PP.countUp) {
            document.querySelectorAll('[data-scard-count]').forEach(function (el) { PP.countUp(el, 900); });
        }

        var chips = document.querySelectorAll('[data-ttl-switch] .pp-switch__chip');
        if (chips.length && PP.wireTablist) {
            PP.wireTablist(chips, { ignite: true, onSelect: function (chip) { show(chip.dataset.view); } });
        }

        // Land on ?view= when present (a shared/bookmarked link), else Yours.
        var wanted = new URLSearchParams(location.search).get('view');
        if (wanted && ORDER.indexOf(wanted) !== -1 && wanted !== current) {
            var chip = document.querySelector('[data-ttl-switch] .pp-switch__chip[data-view="' + wanted + '"]');
            if (chip) { show(wanted); }
        } else {
            reveal(current);
        }

        // Delegated so rows re-rendered by a future partial swap keep working.
        document.addEventListener('click', function (e) {
            var eq = e.target.closest('[data-ttl-equip]');
            if (eq) { equip(parseInt(eq.dataset.titleId, 10), eq.dataset.titleName, eq); return; }
            if (e.target.closest('[data-ttl-unequip]')) { equip(null, null, null); }
        });
    }

    if (PP.onPageReady) { PP.onPageReady(boot); }
    else { document.addEventListener('DOMContentLoaded', function () { boot(true); }); }
})();
