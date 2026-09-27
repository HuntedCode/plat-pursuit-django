/**
 * My Challenges -- one behaviour: hiding a run, with a confirmation that explains itself.
 *
 * Everything else on this page is server-rendered and posts a plain form. Starting or resuming a run is
 * a navigation, so it needs no JavaScript at all and deliberately has none: the page works with this
 * file blocked.
 *
 * A NATIVE `confirm()`, not the site's dialog primitive, following the reasoning `list-detail.js` writes
 * down for its own delete: the dialog primitive is for things you are COMPOSING, and this is a yes/no
 * about a thing that already exists. Naming the run in the prompt matters more than the chrome does.
 *
 * WHAT THE COPY HAS TO DO, and it is the reason this confirms at all. Hiding is reversible and loses
 * nothing -- the run keeps every square and Start brings it back. But somebody who came looking for
 * Delete needs telling that BEFORE they act, not after, and a bare "Hide this?" does not tell them.
 */
(function () {
    'use strict';

    var PP = window.PlatPursuit || {};
    var API = PP.API;
    var Toast = PP.ToastManager;

    /**
     * Hide one run.
     *
     * The service is idempotent, so a double press is not an error. The page RELOADS on success rather
     * than mutating the card in place: hiding changes the card's verb (Hide disappears, Continue becomes
     * Resume), its status badge and the header's tally, and rebuilding three coupled states by hand is
     * how they come to disagree with the server. A reload is honest and this is a once-in-a-while action.
     */
    function onHide(btn) {
        var name = btn.dataset.name || 'this challenge';
        var ok = window.confirm(
            'Hide ' + name + '?\n\n'
            + 'It comes off your profile and out of the hub, but nothing is lost -- every square you have '
            + 'finished stays finished, and pressing Start brings this same run back.'
        );
        if (!ok) { return; }

        btn.setAttribute('aria-disabled', 'true');
        API.post(btn.dataset.url)
            .then(function () { window.location.reload(); })
            .catch(function (err) {
                btn.removeAttribute('aria-disabled');
                // The server's own message where there is one -- it is written for a hunter -- and a
                // generic line only when there is not. Never a bare `catch()`: a failure the page
                // swallows is a button that looks like it worked.
                var fallback = 'Could not hide that challenge.';
                if (err && err.response && typeof err.response.json === 'function') {
                    err.response.json()
                        .then(function (data) { Toast.show((data && data.error) || fallback, 'error'); })
                        .catch(function () { Toast.show(fallback, 'error'); });
                    return;
                }
                Toast.show(fallback, 'error');
            });
    }

    /**
     * `first` guards the body-level listener so a Back/Forward cache restore does not bind a second one.
     * The convention `lists-browse.js` and `list-detail.js` both document.
     */
    function boot(first) {
        if (!first) { return; }
        document.body.addEventListener('click', function (event) {
            var btn = event.target.closest('[data-chal-hide]');
            if (!btn) { return; }
            if (btn.getAttribute('aria-disabled') === 'true') { return; }
            onHide(btn);
        });
    }

    if (PP.onPageReady) { PP.onPageReady(boot); } else { boot(true); }
}());
