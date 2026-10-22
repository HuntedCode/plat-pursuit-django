/**
 * The pre-synced home surface (2026-08 rebuild; the gates merge made it the template for the
 * anonymous, no-PSN, and syncing states -- the hero swaps per state, the sections are shared).
 *
 * Three jobs, all degradable -- the search is ANON-ONLY (the authed heroes drop the form, so
 * every search hook below must cope with an absent or reshaped hero):
 *  1. The hero search: POST the PSN name to search_sync_profile, then stage the wait honestly --
 *     queue line with a live dot, poll add_sync_status, and surface the profile link the moment
 *     basic ingestion lands (`account_id` truthy). "Error" from the status endpoint covers both
 *     not-found and failed-sync, so the copy speaks to both. The poll stops on error, on the
 *     link, or after a hard cap -- a landing page must never poll forever.
 *  2. Fit the showcase Profile Card (real 1200x630 markup) into its frame by transform, scale
 *     clamped at 1, no floor (the share modal's rule).
 *  3. Count up the heartbeat numbers when their band scrolls in, via the canonical
 *     PlatPursuit.countUp (data-countup) -- not a fourth hand-rolled ticker.
 *  6. The full-size viewer: the Profile Card and the share images at real size in a native <dialog>
 *     that pans, because at 375 a 1200x630 card is 0.28 scale and its labels are 3 to 4px (FV-06).
 *
 * Section arrival is PlatPursuit.arriveOnScroll (html.pp-arm is armed in extra_head).
 */
(function () {
    'use strict';

    var POLL_MS = 2500;
    var POLL_CAP = 120;   // 5 minutes of polling, then rest with an honest line

    // --- 1. The hero search ---------------------------------------------------------------

    function wireSearch() {
        var form = document.querySelector('[data-land-search]');
        if (!form) { return; }
        var statusBox = document.querySelector('[data-land-status]');
        var msg = document.querySelector('[data-land-msg]');
        var visit = document.querySelector('[data-land-visit]');
        var hint = document.querySelector('[data-land-hint]');
        var claim = document.querySelector('[data-land-claim]');
        var claimLink = claim && claim.querySelector('[data-land-claimlink]');
        var input = form.querySelector('input[name="psn_username"]');
        var button = form.querySelector('button[type="submit"]');
        // The form may exist while its satellites don't (a reshaped hero variant): every one of
        // these is dereferenced unguarded below, so bail as a set rather than throw on submit.
        if (!statusBox || !msg || !visit || !input || !button) { return; }
        var timer = null, polls = 0;

        function setState(state, text) {
            statusBox.hidden = false;
            statusBox.classList.toggle('is-waiting', state === 'waiting');
            statusBox.classList.toggle('is-error', state === 'error');
            statusBox.classList.toggle('is-ready', state === 'ready');
            msg.textContent = text;
            if (hint) { hint.hidden = true; }
        }

        function stopPolling() {
            if (timer) { clearInterval(timer); timer = null; }
        }

        // "That's you? Sign up free to claim it." (FV-05), only for a profile nobody has an account on yet
        // (`claimable`). Sign-up with `next` = Link PSN for this name: allauth resumes `next` when the email is
        // confirmed in this browser, so the hunter lands on Link PSN with the name filled in.
        function offerClaim(name, claimable) {
            if (!claim || !claimLink) { return; }
            claim.hidden = !claimable;
            if (!claimable) { return; }
            var next = form.dataset.urlLink + '?psn=' + encodeURIComponent(name);
            claimLink.href = form.dataset.urlSignup + '?next=' + encodeURIComponent(next);
        }

        function poll(name) {
            polls += 1;
            if (polls > POLL_CAP) {
                stopPolling();
                setState('rest', 'Still syncing. Long histories take real time. Search the name again in a few minutes and we will take you straight there.');
                return;
            }
            fetch(form.dataset.urlStatus + '?psn_username=' + encodeURIComponent(name), {
                headers: { 'Accept': 'application/json' },
            }).then(function (r) { return r.json(); }).then(function (data) {
                if (data.sync_status === 'error') {
                    stopPolling();
                    setState('error', "We couldn't find that name on PSN. Check the spelling, and note that fully private profiles can't be tracked.");
                    return;
                }
                if (data.account_id && data.slug) {
                    stopPolling();
                    setState('ready', 'Found. Trophies are still arriving, so the numbers will keep climbing for a while.');
                    visit.href = data.slug;
                    visit.hidden = false;
                    offerClaim(data.psn_username || name, data.claimable);
                }
            }).catch(function () { /* transient; the next tick retries */ });
        }

        var PSN_RE = /^[a-zA-Z0-9_-]{3,16}$/;   // the navbar search's own gate

        form.addEventListener('submit', function (e) {
            e.preventDefault();
            var name = (input.value || '').trim();
            if (!name) { return; }
            if (!PSN_RE.test(name)) {
                stopPolling();   // and the last name's result goes with it, or its link and claim sat under this error
                visit.hidden = true;
                offerClaim(name, false);
                setState('error', 'That does not look like a PSN name. Letters, numbers, dashes and underscores, 3 to 16 characters.');
                return;
            }
            stopPolling();
            polls = 0;
            visit.hidden = true;
            offerClaim(name, false);
            button.disabled = true;
            setState('waiting', 'Looking up ' + name + '...');

            fetch(form.action, {
                method: 'POST',
                body: new FormData(form),
                headers: { 'Accept': 'application/json' },
            }).then(function (r) {
                return r.json().then(function (data) { return { status: r.status, data: data }; });
            }).then(function (res) {
                button.disabled = false;
                // A COOLDOWN or a SIGN-IN refusal both mean: this hunter is already tracked. They exist
                // and are viewable RIGHT NOW, and only the refresh was declined. Hand over the link.
                //
                // This hero is ANONYMOUS, so `sign_in` is the case it meets most: any name a visitor
                // types that we already hold. Before the refusal was made honest a tracked name returned
                // 200 and the poll revealed this same link on its first tick, so painting either of
                // these red would be a regression dressed as a fix -- and this hero's entire job is
                // getting a stranger onto a profile page.
                if (res.data.slug && (res.data.reason === 'cooldown' || res.data.reason === 'sign_in')) {
                    stopPolling();
                    // TWO reasons, two sentences. `cooldown` means we synced them recently, so "already
                    // up to date" is true. `sign_in` means only that we track them and refused the
                    // refresh -- it says NOTHING about freshness, and using the cooldown's wording for
                    // it told a visitor a five-day-stale hunter was current.
                    // An unclaimed profile gets the claim line instead of "Sign in to refresh them": two account
                    // pitches in one breath, and the second is the one that is actually theirs to take.
                    setState('ready', res.data.reason === 'cooldown'
                        ? 'Found ' + name + '. Already up to date.'
                        : (res.data.claimable ? 'Found ' + name + '.' : 'Found ' + name + '. Sign in to refresh them.'));
                    visit.href = res.data.slug;
                    visit.hidden = false;
                    offerClaim(res.data.psn_username || name, res.data.claimable);
                    return;
                }
                if (res.status === 429) {
                    setState('error', res.data.error || 'Too many searches. Give it a minute and try again.');
                    return;
                }
                if (!res.data || !res.data.success) {
                    setState('error', (res.data && res.data.error) || 'Something went sideways. Try again in a moment.');
                    return;
                }
                setState('waiting', 'In the queue. First syncs pull an entire history, so give it a moment...');
                timer = setInterval(function () { poll(res.data.psn_username || name); }, POLL_MS);
            }).catch(function () {
                button.disabled = false;
                setState('error', 'Something went sideways. Try again in a moment.');
            });
        });
    }

    // --- 2. Fit the showcase card ---------------------------------------------------------

    function wireCardFit() {
        var frame = document.querySelector('[data-land-cardframe]');
        var scaler = document.querySelector('[data-land-cardscaler]');
        if (!frame || !scaler) { return; }
        var fit = function () {
            var scale = Math.min(1, frame.clientWidth / 1200);
            scaler.style.transform = 'scale(' + scale + ')';
        };
        fit();
        window.addEventListener('resize', fit);
    }

    // --- 3. Count-ups when the numbers scroll in -------------------------------------------

    function wireCountUps() {
        // :not([data-sync-tally]): the syncing hero's tally is OWNED by syncing.js, which
        // ticks it old-to-new off the poll payload. Sweeping it here would race that with a
        // second, uncancellable countUp loop (and defeat the server-rendered first paint).
        var els = Array.prototype.slice.call(document.querySelectorAll('[data-countup]:not([data-sync-tally])'));
        if (!els.length || !window.PlatPursuit || !PlatPursuit.countUp) { return; }
        if (!('IntersectionObserver' in window)) { return; }   // numbers are already rendered
        var io = new IntersectionObserver(function (entries) {
            entries.forEach(function (entry) {
                if (!entry.isIntersecting) { return; }
                io.unobserve(entry.target);
                PlatPursuit.countUp(entry.target, 900);
            });
        }, { rootMargin: '0px 0px -10% 0px' });
        els.forEach(function (el) { io.observe(el); });
    }

    // --- 4. The badge inspect (the How It Works precedent, verbatim shape) -----------------

    function wireBadgePeek() {
        var modal = document.getElementById('badge-peek');
        if (!modal || !window.PlatPursuit || !PlatPursuit.Medallion || !PlatPursuit.Medallion.detailModal) { return; }
        var peek = PlatPursuit.Medallion.detailModal({
            modal: modal,
            bodySel: '[data-peek-body]',
            closeSel: '[data-peek-close]',
        });
        var tmpl = modal.dataset.peekUrl;

        function openPeek(cell) {
            var id = cell.dataset.badgeId;
            if (!id) { return; }
            peek.open(tmpl.replace(/0\/$/, id + '/'), cell);
        }

        document.querySelectorAll('.land-medals__slot[data-badge-id]').forEach(function (cell) {
            cell.addEventListener('click', function () { openPeek(cell); });
            cell.addEventListener('keydown', function (e) {
                if (e.key === 'Enter' || e.key === ' ') {
                    e.preventDefault();
                    openPeek(cell);
                }
            });
        });
    }

    // --- 4b. The shelf's affordance nudge ---------------------------------------------------

    function wireShelfNudge() {
        var shelf = document.querySelector('.land-medals');
        if (!shelf || !('IntersectionObserver' in window)) { return; }
        if (window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches) { return; }
        var art = shelf.querySelector('.land-medals__slot .pp-med__art');
        if (!art || !art.animate) { return; }
        var io = new IntersectionObserver(function (entries) {
            entries.forEach(function (entry) {
                if (!entry.isIntersecting) { return; }
                io.disconnect();
                setTimeout(function () {
                    // A hovered medallion already shows the state the nudge previews -- and the
                    // animation ending under a held hover would snap between transform stacks.
                    if (art.matches && art.matches(':hover')) { return; }
                    // The hover state, previewed: lift, slight overshoot down, settle. Once.
                    art.animate([
                        { transform: 'none' },
                        { transform: 'translateY(-7%) scale(1.05)', offset: 0.4 },
                        { transform: 'translateY(1.5%) scale(0.995)', offset: 0.75 },
                        { transform: 'none' },
                    ], { duration: 950, easing: 'ease-in-out' });
                }, 650);
            });
        }, { threshold: 0.6 });
        io.observe(shelf);
    }

    // --- 5. The ratings carousel -----------------------------------------------------------

    function wireRatingsCarousel() {
        var host = document.querySelector('[data-land-carousel]');
        if (!host) { return; }
        var slides = Array.prototype.slice.call(host.querySelectorAll('.land-rdemo__slide'));
        var dots = Array.prototype.slice.call(host.querySelectorAll('[data-land-dotindex]'));
        if (slides.length < 2) { return; }
        var index = 0, timer = null;
        var reduce = window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches;

        function show(next) {
            index = (next + slides.length) % slides.length;
            slides.forEach(function (slide, i) {
                slide.classList.toggle('is-active', i === index);
                slide.setAttribute('aria-hidden', i === index ? 'false' : 'true');
            });
            dots.forEach(function (dot, i) {
                dot.classList.toggle('is-active', i === index);
                if (i === index) { dot.setAttribute('aria-current', 'true'); }
                else { dot.removeAttribute('aria-current'); }
            });
        }

        // The rotation only runs while the carousel is actually ON SCREEN: slides advancing
        // above the fold before anyone scrolls down would spend the motion where nobody sees
        // it, and the reader should always arrive on slide one.
        var inView = false;

        function start() {
            // No auto-advance under reduced motion: the dots remain the manual path.
            if (reduce || timer || !inView) { return; }
            timer = setInterval(function () { show(index + 1); }, 7000);
        }
        function stop() {
            if (timer) { clearInterval(timer); timer = null; }
        }

        dots.forEach(function (dot) {
            dot.addEventListener('click', function () {
                show(parseInt(dot.dataset.landDotindex, 10) || 0);
                stop();   // a chosen slide stays chosen; auto-advance resumes on mouse-out
            });
        });
        host.addEventListener('mouseenter', stop);
        host.addEventListener('mouseleave', start);
        host.addEventListener('focusin', stop);
        host.addEventListener('focusout', start);

        // Swipe: direct manipulation, the mobile-native way to page a carousel. TOUCH/PEN only
        // (a mouse drag is text selection, not a page gesture), horizontal only (touch-action:
        // pan-y on the host leaves vertical scrolling to the browser), and identified by
        // pointerId so a drag that wanders out and back can never page against a stale origin.
        // A swipe is a CHOICE, same as a dot press: the rotation stays stopped afterwards.
        var swipeX = null, swipeY = null, swipeId = null;
        host.addEventListener('pointerdown', function (e) {
            if (e.pointerType === 'mouse') { return; }
            swipeId = e.pointerId; swipeX = e.clientX; swipeY = e.clientY;
        });
        host.addEventListener('pointerup', function (e) {
            if (swipeId === null || e.pointerId !== swipeId) { return; }
            var dx = e.clientX - swipeX, dy = e.clientY - swipeY;
            swipeId = swipeX = swipeY = null;
            if (Math.abs(dx) < 44 || Math.abs(dx) < Math.abs(dy) * 1.5) { return; }
            stop();
            show(index + (dx < 0 ? 1 : -1));
        });
        host.addEventListener('pointercancel', function () { swipeId = swipeX = swipeY = null; });

        if ('IntersectionObserver' in window) {
            var io = new IntersectionObserver(function (entries) {
                entries.forEach(function (entry) {
                    inView = entry.isIntersecting;
                    if (inView) { start(); } else { stop(); }
                });
            }, { threshold: 0.35 });
            io.observe(host);
        } else {
            inView = true;
            start();
        }
    }

    // --- 6. The full-size viewer -----------------------------------------------------------

    function wireZoom() {
        var dialog = document.querySelector('[data-land-zoom-dialog]');
        // No <dialog> support (or no JS at all): the openers stay pictures, their captions still say what is on
        // them, and the "View full size" buttons stay `hidden` rather than offering a press that does nothing.
        if (!dialog || typeof dialog.showModal !== 'function') { return; }
        var stage = dialog.querySelector('[data-land-zoom-stage]');
        var heading = dialog.querySelector('[data-land-zoom-heading]');
        var closeBtn = dialog.querySelector('[data-land-zoom-close]');
        if (!stage || !heading || !closeBtn) { return; }
        var root = document.documentElement;
        var returnTo = null;

        // The stage holds the thing at REAL size (1200x630): the Profile Card, which is HTML already on this page
        // (the server-rendered card; no ids, no controls), copied whole; or a share image, by the source the page
        // already chose for it (`currentSrc`: the WebP, a tenth of the PNG's bytes on a phone's data).
        function fill(source) {
            stage.textContent = '';
            var scaler = source.querySelector('[data-land-cardscaler]');
            if (!scaler) {
                var pic = source.querySelector('img');
                if (!pic) { return; }
                var img = document.createElement('img');
                img.src = pic.currentSrc || pic.src;
                img.alt = pic.alt;
                img.width = 1200;
                img.height = 630;
                img.draggable = false;   // a mouse drag would lift the image instead of doing nothing
                img.className = 'land-zoom__img';
                stage.appendChild(img);
                return;
            }
            var card = document.createElement('div');
            card.className = 'land-zoom__card';
            card.innerHTML = scaler.innerHTML;
            stage.appendChild(card);
        }

        function open(source, opener) {
            fill(source);
            if (!stage.firstChild) { return; }
            heading.textContent = source.getAttribute('data-land-zoom-title') || 'Full size';
            returnTo = opener;
            root.classList.add('land-zoom-open');   // the page behind must not scroll under a drag
            dialog.showModal();
            stage.scrollLeft = 0;
            stage.scrollTop = 0;
            stage.focus({ preventScroll: true });   // arrow keys pan the stage straight away
        }

        dialog.addEventListener('close', function () {
            root.classList.remove('land-zoom-open');
            stage.textContent = '';
            if (returnTo && returnTo.isConnected) { returnTo.focus({ preventScroll: true }); }
            returnTo = null;
        });
        closeBtn.addEventListener('click', function () { dialog.close(); });
        // The dialog is full screen, so there is no backdrop to press; the stage's empty space around the item is
        // the lightbox's "outside", and a press there closes it. A press on the item itself (or a pan) does not.
        stage.addEventListener('click', function (e) { if (e.target === stage) { dialog.close(); } });

        // Each item (`data-land-zoom`) is the source. The picture (`data-land-zoom-target`) opens it on a tap, the
        // natural gesture; the "View full size" button under the caption is the named, focusable control. The
        // button sits OUTSIDE the picture: every corner of these cards holds content, and a button laid over one
        // covered it.
        document.querySelectorAll('[data-land-zoom]').forEach(function (item) {
            var button = item.querySelector('[data-land-zoom-open]');
            var target = item.querySelector('[data-land-zoom-target]');
            if (target) {
                target.classList.add('is-live');   // the zoom-in cursor, now that it is true
                target.addEventListener('click', function () { open(item, button || target); });
            }
            if (button) {
                button.hidden = false;   // shipped hidden: it only means something once this has run
                button.addEventListener('click', function () { open(item, button); });
            }
        });
    }

    function boot() {
        wireSearch();
        wireCardFit();
        wireCountUps();
        wireBadgePeek();
        wireShelfNudge();
        wireRatingsCarousel();
        wireZoom();
        if (window.PlatPursuit && PlatPursuit.arriveOnScroll) {
            // Deeper than the shared -8% default: on a long marketing scroll the entrance was
            // firing just below the fold and playing unseen (his note). -18% means a section is
            // properly INTO the viewport before its beat runs.
            PlatPursuit.arriveOnScroll({ rootMargin: '0px 0px -18% 0px' });
        } else {
            document.documentElement.classList.remove('pp-arm');
        }
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', boot);
    } else {
        boot();
    }
})();
