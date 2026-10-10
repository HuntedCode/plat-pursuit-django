/**
 * The challenge share dialog (`#cc-share`): fetch a run's card, fit it, paint the chosen ground, download it.
 *
 * THE PLAT MODAL'S LIFECYCLE, PORTED WHOLE rather than in the parts that looked necessary, because every
 * piece of it is a bug that shipped once (see plat-cards.js for each one's story):
 *   - fit() budgets from the VIEWPORT and the chrome, never the box's own height, and clamps at 0 rather than
 *     at a minimum preview size -- a floor paints the card over the swatch row on landscape phones
 *   - a request token, so a slow preview for one run cannot land in a dialog since reopened for another
 *   - `disabled` on the download is derived (CardDownload owns the in-flight half, this the preview half)
 *   - the exit is played before the native close, and the page un-recedes on the same beat
 *   - listeners on the dialog are bound EVERY boot, because a history restore replaces <body> wholesale
 * What it leaves out is what only a game completion has: the rating prompt and the per-game art grounds.
 *
 * THE GROUND IS READ OFF THE SWATCH (`--pc-theme-bg`), the Profile Card tab's way, not from a JSON theme
 * registry: the thing you clicked and the thing that paints are one value. The card's scrims are inner
 * layers, so painting its root background is exactly what the renderer does to the PNG.
 *
 * ONE DIALOG, MANY TRIGGERS. Any `[data-challenge-share]` button opens it with its own `data-html-url`,
 * `data-png-url` and `data-name`, so My Challenges can offer a card per run. The download's filename comes
 * back with the preview, so the button does not need to know it.
 */
(function () {
    'use strict';

    var PP = window.PlatPursuit || {};
    var REDUCE = window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches;
    // Must match .pc-modal__box's max-height in plat-cards.css; see the same constant in plat-cards.js.
    var BOX_VH = 0.92;

    // NO PREVIEW CACHE, unlike the plat modal. That one caches because it prefetches on hover; this has no
    // prefetch, and a run's card is NOT stable for the life of the page: the picker rewrites squares in place
    // without a reload, so a cached preview would disagree with the freshly rendered download.
    var dlg = null, current = null, reqToken = 0, downloader = null, previewBlocked = false;

    function pageRecede(on) {
        if (REDUCE) { return; }
        var zoom = document.getElementById('zoom-container');
        var pr = document.getElementById('page-recede');
        if (on && pr) {
            pr.style.transformOrigin = '50% ' + (window.innerHeight / 2 - pr.getBoundingClientRect().top) + 'px';
        }
        if (zoom) { zoom.classList.toggle('pp-receded', on); }
    }

    function fit() {
        if (!dlg || !dlg.open) { return; }
        var frame = dlg.querySelector('[data-share-frame]');
        var scaler = dlg.querySelector('[data-share-preview]');
        if (!frame || !scaler) { return; }

        frame.style.width = '';
        var scale = Math.min(1, frame.clientWidth / 1200);
        var stage = frame.parentElement;
        var head = dlg.querySelector('.pc-modal__head');
        var controls = dlg.querySelector('.pc-modal__controls');
        var err = dlg.querySelector('[data-share-error]');
        if (stage && head && controls) {
            var pad = window.getComputedStyle(stage);
            var chrome = head.offsetHeight + controls.offsetHeight
                + parseFloat(pad.paddingTop) + parseFloat(pad.paddingBottom) + 2;
            if (err && !err.hidden) { chrome += err.offsetHeight; }
            scale = Math.max(0, Math.min(scale, (window.innerHeight * BOX_VH - chrome) / 630));
        }
        scaler.style.transform = 'scale(' + scale + ')';
        frame.style.height = Math.round(630 * scale) + 'px';
        frame.style.width = Math.round(1200 * scale) + 'px';
    }

    function syncDownloadEnabled() {
        if (downloader) { downloader.setBlocked(previewBlocked); }
    }

    function setBusy(on) {
        var loading = dlg.querySelector('[data-share-loading]');
        var frame = dlg.querySelector('[data-share-frame]');
        if (loading) { loading.hidden = !on; }
        if (frame) { frame.setAttribute('aria-busy', on ? 'true' : 'false'); }
        previewBlocked = !!on;
        syncDownloadEnabled();
    }

    // A PREVIEW failure blocks the download (there is no card); a DOWNLOAD failure does not, or the advice to
    // try again would arrive with the only button that could take it disabled.
    function showError(msg, blocks) {
        var e = dlg && dlg.querySelector('[data-share-error]');
        if (!e) { return; }
        e.hidden = !msg;
        e.textContent = msg || '';
        if (msg && blocks !== false) { previewBlocked = true; syncDownloadEnabled(); }
        fit();          // the line is in flow, so it takes room fit() had already given the preview
    }

    function picked() { return dlg && dlg.querySelector('[data-share-theme]:checked'); }

    function applyTheme() {
        var card = dlg && dlg.querySelector('[data-share-preview] .share-image-content');
        var choice = picked();
        var label = choice && choice.closest('.pc-theme');
        var ground = label && getComputedStyle(label).getPropertyValue('--pc-theme-bg').trim();
        if (card && ground) { card.style.background = ground; }
    }

    function loadPreview() {
        var token = ++reqToken;
        setBusy(true); showError('');
        PP.API.request(current.htmlUrl)
            .then(function (data) {
                if (token !== reqToken) { return; }
                if (data.filename) { current.filename = data.filename; }
                var scaler = dlg.querySelector('[data-share-preview]');
                scaler.innerHTML = data.html;
                if (PP.runScripts) { PP.runScripts(scaler); }
                scaler.classList.add('is-in');
                applyTheme();
                fit();
            })
            .catch(function () {
                if (token !== reqToken) { return; }
                showError("Couldn't build your card. Try again in a moment.");
            })
            .finally(function () { if (token === reqToken) { setBusy(false); } });
    }

    function open(trigger) {
        current = {
            htmlUrl: trigger.dataset.htmlUrl,
            pngUrl: trigger.dataset.pngUrl,
            filename: 'challenge-card.png',     // replaced by the server's name once the preview lands
        };
        var name = dlg.querySelector('[data-share-name]');
        if (name) { name.textContent = trigger.dataset.name || ''; }
        if (downloader) { downloader.reset(); }
        var scaler = dlg.querySelector('[data-share-preview]');
        scaler.innerHTML = '';
        scaler.classList.remove('is-in');
        if (!dlg.open) { dlg.showModal(); pageRecede(true); }
        fit();          // size the stage before the fetch, so it does not pop
        loadPreview();
    }

    function close() {
        if (!dlg || !dlg.open || dlg.classList.contains('is-closing')) { return; }
        pageRecede(false);
        if (REDUCE) { dlg.close(); return; }
        dlg.classList.add('is-closing');
        var done = false;
        function finish() {
            if (done) { return; }
            done = true;
            dlg.classList.remove('is-closing');
            dlg.close();
        }
        dlg.addEventListener('animationend', finish, { once: true });
        setTimeout(finish, 260);        // the animation may never fire (hidden tab, etc.)
    }

    function pngUrl() {
        var choice = picked();
        return current.pngUrl + (choice ? '?theme=' + encodeURIComponent(choice.value) : '');
    }

    function boot(first) {
        dlg = document.getElementById('cc-share');
        if (!dlg) { return; }

        var go = dlg.querySelector('[data-share-download]');
        if (go && PP.CardDownload) {
            downloader = PP.CardDownload.attach(go, {
                url: pngUrl,
                filename: function () { return current ? current.filename : 'challenge-card.png'; },
                toast: 'Card saved to your downloads.',
                onStart: function () { showError(''); },
                onError: function (msg) { showError(msg, false); },
            });
            syncDownloadEnabled();
        }

        dlg.addEventListener('click', function (e) {
            if (e.target === dlg || e.target.closest('[data-share-close]')) { close(); }
        });
        dlg.addEventListener('change', function (e) {
            if (e.target.matches('[data-share-theme]')) { applyTheme(); }
        });
        // Esc closes natively without passing through close(), so the recede is undone here too.
        dlg.addEventListener('close', function () { current = null; reqToken++; pageRecede(false); });
        dlg.addEventListener('cancel', function () { dlg.classList.remove('is-closing'); });
        // A DIRECT close, not the choreographed one: the swipe helper has already slid the sheet away and
        // cleared its transform, so handing it `close` popped the sheet back and played a second exit (the
        // picker fixed the same bug the same way). The `close` listener above undoes the recede.
        if (PP.dismissableSheet) {
            PP.dismissableSheet(dlg, { onClose: function () { if (dlg.open) { dlg.close(); } } });
        }

        if (first) {
            window.addEventListener('resize', fit);
            document.body.addEventListener('click', function (e) {
                var trigger = e.target.closest('[data-challenge-share]');
                if (trigger && dlg) { open(trigger); }
            });
        }
    }

    if (PP.onPageReady) { PP.onPageReady(boot); }
})();
