/*
 * Milestone celebration overlay -- the "you hit this in your trophy-hunting career, congrats" moment.
 *
 * A self-contained, dependency-light overlay. Call:
 *   window.MilestoneCelebration.play({ name, tierLabel, rarity, accent, iconSvg })
 * It builds a single reusable DOM node, tints it with the milestone accent, springs the card in with a glow,
 * and dismisses on button / scrim / Escape. Reduced-motion falls back to a plain fade. If several are queued
 * (e.g. a sync crossed multiple rungs), they play one after another.
 */
(function () {
    var overlay = null, card = null, iconEl = null, nameEl = null, tierEl = null, rarityEl = null;
    var queue = [], playing = false, lastFocus = null;
    var reduce = window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches;

    function build() {
        if (overlay) { return; }
        overlay = document.createElement('div');
        overlay.className = 'msx';
        overlay.setAttribute('role', 'dialog');
        overlay.setAttribute('aria-modal', 'true');
        overlay.setAttribute('aria-label', 'Milestone reached');
        overlay.hidden = true;
        overlay.innerHTML =
            '<div class="msx__scrim" data-msx-scrim></div>' +
            '<div class="msx__card" role="document">' +
                '<span class="msx__glow" aria-hidden="true"></span>' +
                '<span class="msx__icon" data-msx-icon aria-hidden="true"></span>' +
                '<span class="msx__eyebrow">Milestone reached</span>' +
                '<h2 class="msx__name" data-msx-name></h2>' +
                '<div class="msx__tier" data-msx-tier></div>' +
                '<div class="msx__rarity" data-msx-rarity></div>' +
                '<button type="button" class="msx__dismiss" data-msx-dismiss>Nice!</button>' +
            '</div>';
        document.body.appendChild(overlay);
        card = overlay.querySelector('.msx__card');
        iconEl = overlay.querySelector('[data-msx-icon]');
        nameEl = overlay.querySelector('[data-msx-name]');
        tierEl = overlay.querySelector('[data-msx-tier]');
        rarityEl = overlay.querySelector('[data-msx-rarity]');
        overlay.querySelector('[data-msx-dismiss]').addEventListener('click', close);
        overlay.querySelector('[data-msx-scrim]').addEventListener('click', close);
    }

    function onKeydown(e) { if (e.key === 'Escape') { close(); } }

    function render(data) {
        card.style.setProperty('--msx-accent', data.accent || 'var(--pp-primary)');
        iconEl.innerHTML = data.iconSvg || '';
        nameEl.textContent = data.name || 'Milestone';
        tierEl.textContent = data.tierLabel || '';
        if (data.rarity) { rarityEl.textContent = data.rarity; rarityEl.hidden = false; }
        else { rarityEl.hidden = true; }
    }

    function show() {
        overlay.hidden = false;
        lastFocus = document.activeElement;
        document.addEventListener('keydown', onKeydown);
        // Focus the dismiss button so Enter/Escape work + focus is trapped-ish.
        var btn = overlay.querySelector('[data-msx-dismiss]');
        if (reduce) {
            overlay.classList.add('is-in');
            if (btn) { btn.focus(); }
            return;
        }
        overlay.classList.remove('is-in', 'is-out');
        // rAF so the initial (hidden) state is committed before the entrance transition.
        requestAnimationFrame(function () {
            requestAnimationFrame(function () {
                overlay.classList.add('is-in');
                if (btn) { btn.focus(); }
            });
        });
    }

    function playNext() {
        if (playing || !queue.length) { return; }
        playing = true;
        build();
        render(queue.shift());
        show();
    }

    function play(data) {
        if (!data) { return; }
        queue.push(data);
        playNext();
    }

    function close() {
        if (!overlay || overlay.hidden) { return; }
        document.removeEventListener('keydown', onKeydown);
        var done = function () {
            overlay.hidden = true;
            overlay.classList.remove('is-in', 'is-out');
            playing = false;
            if (lastFocus && lastFocus.focus) { try { lastFocus.focus(); } catch (e) {} }
            if (queue.length) { playNext(); }        // chain any queued celebrations
        };
        if (reduce) { done(); return; }
        overlay.classList.remove('is-in');
        overlay.classList.add('is-out');
        var onEnd = function (ev) {
            if (ev.target !== card) { return; }
            card.removeEventListener('transitionend', onEnd);
            done();
        };
        card.addEventListener('transitionend', onEnd);
        // Safety net if transitionend doesn't fire.
        setTimeout(function () { if (!overlay.hidden && overlay.classList.contains('is-out')) { done(); } }, 500);
    }

    window.MilestoneCelebration = { play: play, close: close };
})();
