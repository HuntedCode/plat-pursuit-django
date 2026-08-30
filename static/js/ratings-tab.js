/**
 * The ACTIVE Ratings tab (concept Game page): per-group (DLC) selector, quick-rate modal glue,
 * the post-save live update, blurb add/replace/report, and the tab's one-shot entrance reveal.
 *
 * MIGRATED verbatim from game-detail.js's ratingsTab IIFE when the list-detail slim-down made
 * this page the ONE active ratings host (List detail's copy is deleted with its Ratings tab).
 * Adaptations, per the phase-2 plan: local pageRecede/reduce (plat-cards.js precedent), the
 * minibar Base/DLC label sync dropped (no [data-rate-mb-title] here), and the group swap
 * refreshes horizon bars in the shown panel. countReveal/countTo and the revealRatings entrance
 * came along -- reveal is exported as PlatPursuit.RatingsTab.reveal(panel) for game-page.js to
 * call one-shot when the Ratings view first shows. Load order: quick-rate.js (the shared form
 * controller) BEFORE this file, this file before game-page.js.
 */
document.addEventListener('DOMContentLoaded', () => {
    if (!document.getElementById('game-page-container')) return;

    const reduce = window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches;

    // Shared "page steps back" recede (used by the contract modal AND the hero's stats/badges modals).
    // Pivots the scale on the current viewport centre so it's a gentle scale-in-place at any scroll depth.
    const zoomContainer = document.getElementById('zoom-container');
    function pageRecede(on) {
        const pr = document.getElementById('page-recede');
        if (on && pr) { pr.style.transformOrigin = '50% ' + (window.innerHeight / 2 - pr.getBoundingClientRect().top) + 'px'; }
        if (zoomContainer) zoomContainer.classList.toggle('pp-receded', on);
    }

    // Same contract as game-page.js's fillBars, scoped: double-rAF so a freshly shown bar
    // transitions from 0 instead of snapping.
    function fillBars(root) {
        root.querySelectorAll('[data-gd-fill] .pp-horizon__fill').forEach((fill) => {
            const bar = fill.closest('[data-gd-fill]');
            const target = (bar.dataset.gdFill || '0') + '%';
            requestAnimationFrame(() => requestAnimationFrame(() => bar.style.setProperty('--horizon-progress', target)));
        });
    }

    // countReveal: zero a number now (it shows its SSR final value), then count 0->final after a stagger delay.
    function countReveal(el, delay, decimals) {
        if (!el || !PlatPursuit.countUp) return;
        const target = parseFloat((el.textContent || '').replace(/,/g, ''));
        if (isNaN(target)) return;   // empty state (— / blank): nothing to count
        el.dataset.countup = String(target);
        if (decimals) el.dataset.countupDecimals = String(decimals);
        el.textContent = decimals ? (0).toFixed(decimals) : '0';
        window.setTimeout(() => PlatPursuit.countUp(el, 650, { from: 0 }), delay);
    }
    // countTo: tick a number from its current value to a new one (the "your input mattered" reward on submit).
    function countTo(el, target, decimals) {
        if (!el) return;
        const t = parseFloat(target); if (isNaN(t)) return;
        const from = parseFloat((el.textContent || '').replace(/,/g, ''));
        el.dataset.countup = String(t);
        if (decimals) el.dataset.countupDecimals = String(decimals); else delete el.dataset.countupDecimals;
        if (PlatPursuit.countUp) PlatPursuit.countUp(el, 600, { from: isNaN(from) ? 0 : from });
        else el.textContent = decimals ? t.toFixed(decimals) : String(Math.round(t));
    }

    // Ratings panel entrance, ONE-SHOT on first arrival. Uses the SAME shared staggerReveal engine + motion the
    // About panel does (revealAbout), so the two sibling tabs open with one identical band cascade; then the
    // signature numbers (score / per-quality / hours) count up as their band lands -- parity with About's
    // growing bars and Career's hero count-ups. staggerReveal + countUp self-gate reduced motion; the extra
    // count-up + star-grow block is skipped there (the SSR values are already final).
    let ratingsRevealed = false;
    function revealRatings(panel) {
        if (ratingsRevealed) return;
        ratingsRevealed = true;
        const root = panel.querySelector('[data-gd-rate]');
        if (root && PlatPursuit.staggerReveal) {
            // ALL panels' cards (not just the active group's) so switching DLC never lands on an unrevealed band.
            PlatPursuit.staggerReveal({
                grid: root,
                cardSelector: '.gd-rate__snapshot, .gd-rate__sel, .gd-rate__panel > .gd-acard, .gd-rate__panel > .gd-blurbs',
                step: 55, batchCap: 300,
                reveal: (el, delay) => el.animate(
                    [{ opacity: 0, transform: 'translateY(10px)' }, { opacity: 1, transform: 'none' }],
                    { duration: 420, delay, easing: 'cubic-bezier(0.2, 0.8, 0.2, 1)', fill: 'backwards' }
                ),
            });
        }
        if (reduce) return;
        const active = panel.querySelector('.gd-rate__panel:not(.is-hidden)') || panel;
        // Count the signature numbers up as the verdict band lands (~110ms into the cascade).
        countReveal(active.querySelector('[data-cond-score]'), 120, 1);
        active.querySelectorAll('[data-cond-num]').forEach((el, i) => countReveal(el, 150 + i * 40, 1));
        countReveal(active.querySelector('[data-cond-hours]'), 135, 0);
        panel.querySelectorAll('.gd-rate__stats [data-gd-countup]').forEach((el, i) => {
            const n = parseInt(el.dataset.gdCountup, 10);
            if (isNaN(n)) return;
            el.dataset.countup = n;
            el.textContent = '0';
            window.setTimeout(() => PlatPursuit.countUp(el, 650, { from: 0 }), i * 55);
        });
        // Grow the star bar from empty, pairing with the score count-up (mirrors About resetting its bars first).
        const stars = active.querySelector('[data-cond-stars]');
        if (stars) {
            const fill = stars.style.getPropertyValue('--fill') || '0%';
            stars.style.setProperty('--fill', '0%');
            requestAnimationFrame(() => requestAnimationFrame(() => stars.style.setProperty('--fill', fill)));
        }
    }

    window.PlatPursuit = window.PlatPursuit || {};
    window.PlatPursuit.RatingsTab = { reveal: revealRatings };

    // ============================================================
    // Ratings tab: per-group (DLC) selector + quick-rate modal (rebuilt in Phase 4).
    // ============================================================
    (function ratingsTab() {
        const root = document.querySelector('[data-gd-rate]');
        if (!root) return;

        // ── Per-group selector: a pill row (few groups) OR a Base pill + DLC dropdown (many). Both drive the
        //    same panel toggle via [data-rate-ctg]; only the active group's [data-rate-panel] shows. ──
        const drop = root.querySelector('[data-rate-drop]');
        const dropBtn = root.querySelector('[data-rate-drop-toggle]');
        function closeDrop() {
            if (!dropBtn) return;
            dropBtn.setAttribute('aria-expanded', 'false');
            const m = drop && drop.querySelector('.gd-rate__dropmenu');
            if (m) m.hidden = true;
        }
        function selectGroup(ctgId, srcEl) {
            const target = root.querySelector('[data-rate-panel="' + ctgId + '"]');
            if (target && !target.classList.contains('is-hidden')) return;   // already showing this group -- no-op
            root.querySelectorAll('[data-rate-panel]').forEach((p) => p.classList.toggle('is-hidden', p.dataset.ratePanel !== ctgId));
            root.querySelectorAll('.gd-rate__segchip[data-rate-ctg]').forEach((c) => {
                const on = c.dataset.rateCtg === ctgId;
                c.classList.toggle('is-active', on);
                c.setAttribute('aria-pressed', on ? 'true' : 'false');
            });
            // The base pill and the dropdown button are the two mutually exclusive actives. Keep the dropdown
            // label a stable "DLC" (the verdict card's group title now carries the current name) so the button
            // doesn't jump width as you switch between DLCs.
            if (drop && dropBtn) {
                dropBtn.classList.toggle('is-active', !!(srcEl && srcEl.classList.contains('gd-rate__dropitem')));
            }
            // Settle the newly-shown group's bands in -- the same soft rise+fade the leaderboard uses on a
            // board switch (game-detail.js lbEntrance), for one consistent sub-switch motion across the page.
            if (target && !reduce) {
                target.querySelectorAll('.gd-acard, .gd-blurbs:not(.is-empty)').forEach((el) => {
                    el.animate([{ opacity: 0.5, transform: 'translateY(7px)' }, { opacity: 1, transform: 'none' }],
                               { duration: 240, easing: 'cubic-bezier(0.2, 0.8, 0.2, 1)', fill: 'backwards' });
                });
            }
            // (List detail's copy synced a minibar Base/DLC label here; this page's minibar
            // has no such slot -- instead refresh any bars the newly shown panel carries.)
            if (target) fillBars(target);
        }
        root.addEventListener('click', (e) => {
            const toggle = e.target.closest('[data-rate-drop-toggle]');
            if (toggle) {
                const open = toggle.getAttribute('aria-expanded') === 'true';
                toggle.setAttribute('aria-expanded', open ? 'false' : 'true');
                const menu = drop && drop.querySelector('.gd-rate__dropmenu');
                if (menu) {
                    menu.hidden = open;
                    // Move focus into the menu on open so the role=menu is keyboard-operable, not Tab-only.
                    if (!open) { const first = menu.querySelector('.gd-rate__dropitem'); if (first) first.focus(); }
                }
                return;
            }
            const chip = e.target.closest('[data-rate-ctg]');
            if (chip) { selectGroup(chip.dataset.rateCtg, chip); closeDrop(); }
        });
        if (drop) {
            document.addEventListener('click', (e) => { if (!drop.contains(e.target)) closeDrop(); });
            document.addEventListener('keydown', (e) => {
                if (e.key === 'Escape' && dropBtn && dropBtn.getAttribute('aria-expanded') === 'true') { closeDrop(); dropBtn.focus(); }
            });
            // Arrow / Home / End roving focus between DLC items while the menu is open (menu-pattern semantics).
            drop.addEventListener('keydown', (e) => {
                if (!dropBtn || dropBtn.getAttribute('aria-expanded') !== 'true') return;
                if (!['ArrowDown', 'ArrowUp', 'Home', 'End'].includes(e.key)) return;
                const items = Array.from(drop.querySelectorAll('.gd-rate__dropitem'));
                if (!items.length) return;
                e.preventDefault();
                const i = items.indexOf(document.activeElement);
                const next = e.key === 'ArrowDown' ? (items[i + 1] || items[0])
                    : e.key === 'ArrowUp' ? (items[i - 1] || items[items.length - 1])
                    : e.key === 'Home' ? items[0] : items[items.length - 1];
                next.focus();
            });
        }

        // ── Quick-rate modal. Opens from any .quick-rate-btn (SSR'd per group), prefills from data-existing,
        //    POSTs the rating, and live-updates the source group's bars/values without a reload. The endpoint
        //    and the form input names are the API contract (see quick_rate_modal.html). ──
        // The form mechanics -- prefill, slider readouts, the blurb counter, the hours gate, the
        // guidelines agree-on-submit, the POST and every close affordance -- live in the SHARED
        // PlatPursuit.QuickRate (quick-rate.js), because the plat-card share modal composes this same
        // dialog. This file keeps only what is specific to this page: which button was clicked, and the
        // live panel update afterwards.
        if (!PlatPursuit.QuickRate) return;

        let srcBtn = null;

        document.addEventListener('click', (e) => {
            const btn = e.target.closest('.quick-rate-btn');
            if (!btn) return;
            srcBtn = btn;
            const ex = btn.dataset.existing ? JSON.parse(btn.dataset.existing) : null;
            PlatPursuit.QuickRate.open({
                conceptId: btn.dataset.conceptId,
                groupId: btn.dataset.groupId,
                existing: ex,
                blurb: btn.dataset.existingBlurb || '',
                hoursLabel: btn.dataset.hoursLabel || 'Hours to Platinum',
                // Per-GROUP wording: a DLC pack has no platinum to be rough.
                recLabel: btn.dataset.recLabel,
                recLegend: btn.dataset.recLegend,
                // This host says it itself, at the end of applyRating -- after the whole card has been
                // live-updated, so the toast confirms a change you can already see rather than arriving
                // ahead of it. Without the flag RatingFields would add a second one.
                announcesSave: true,
                onOpen: () => pageRecede(true),
                onClose: () => pageRecede(false),
                onSaved: (data, payload) => applyRating(data, payload),
            });
        });

        // Tone + bar-fill thresholds MIRROR core/templatetags/custom_filters.py rating_tone + the SSR widthratio
        // percentages -- keep the two in sync so a live-updated bar matches a reloaded one.
        function toneOf(kind, v) {
            if (kind === 'difficulty' || kind === 'grindiness') return v < 4 ? 'good' : v < 8 ? 'warn' : 'bad';
            if (kind === 'hours') return v < 25 ? 'good' : v < 75 ? 'warn' : v < 100 ? 'high' : 'bad';
            if (kind === 'overall') return v < 2 ? 'bad' : v < 4 ? 'warn' : 'good';
            return v < 4 ? 'bad' : v < 8 ? 'warn' : 'good';   // fun
        }
        // Verdict words mirror core/templatetags/custom_filters.py rating_verdict (keep in sync).
        function verdictOf(kind, v) {
            if (kind === 'difficulty') return v < 2.5 ? 'A breeze' : v < 5 ? 'Fair' : v < 7.5 ? 'Tough' : 'Brutal';
            if (kind === 'grindiness') return v < 2.5 ? 'Breezy' : v < 5 ? 'Some grind' : v < 7.5 ? 'Grindy' : 'A slog';
            if (kind === 'fun') return v < 2.5 ? 'A chore' : v < 5 ? 'So-so' : v < 7.5 ? 'Fun' : 'A blast';
            if (kind === 'overall') return v < 2 ? 'Rough' : v < 3 ? 'Mixed' : v < 4 ? 'Solid' : v < 4.5 ? 'Great' : 'Beloved';
            return '';
        }
        // Synthesized summary sentence -- mirrors core/templatetags/custom_filters.py rating_summary.
        function summaryOf(a) {
            const d = a.avg_difficulty, g = a.avg_grindiness, f = a.avg_fun;
            const diff = d < 2.5 ? 'A breeze' : d < 5 ? 'Fairly easy' : d < 7.5 ? 'Tough' : 'Brutally hard';
            const grind = g < 2.5 ? 'not grindy' : g < 5 ? 'a little grindy' : g < 7.5 ? 'a real grind' : 'a serious slog';
            const fun = f < 2.5 ? 'a chore' : f < 5 ? 'just okay' : f < 7.5 ? 'good fun' : 'a blast to platinum';
            const conj = (f >= 5 && (d >= 5 || g >= 5)) ? 'but' : 'and';
            return diff + ', ' + grind + ', ' + conj + ' ' + fun + '.';
        }

        // Everything that happens AFTER a save lands. The controller has already closed the modal.
        function applyRating(data, payload) {
            const savedMsg = data.message || 'Rating saved!';
            const avg = data.community_averages;
            // Live-update the source group's panel in place (no reload): hero verdict/score/count, the
            // hours callout, and each quality's marker + verdict. Handles empty -> filled (drop --empty).
            const panel = srcBtn && srcBtn.closest('[data-rate-panel]');
            const card = panel && panel.querySelector('[data-rate-grid]');
            if (avg && card) {
                card.classList.remove('gd-cond--empty');
                const summary = card.querySelector('[data-cond-summary]'); if (summary) summary.textContent = summaryOf(avg);
                const sc = card.querySelector('[data-cond-score]'); if (sc) { countTo(sc, avg.avg_rating, 1); sc.classList.add('pp-tally--glow'); }
                const st = card.querySelector('[data-cond-stars]');
                if (st) { st.style.setProperty('--fill', (avg.avg_rating / 5 * 100) + '%'); st.setAttribute('aria-label', avg.avg_rating.toFixed(1) + ' out of 5'); }
                // From the PANEL, not the conditions grid: the total moved down to the action row so it no
                // longer sits beside the split's own "from N that answered" (two counts touching read as
                // one confused sentence). Everything else here is still queried from `card`.
                const ct = panel.querySelector('[data-rate-count]');
                if (ct && avg.count != null) ct.textContent = avg.count.toLocaleString() + ' rating' + (avg.count === 1 ? '' : 's');
                const hrs = card.querySelector('[data-cond-hours]'); if (hrs && avg.avg_hours != null) countTo(hrs, Math.round(avg.avg_hours), 0);
                const byStat = { difficulty: avg.avg_difficulty, grindiness: avg.avg_grindiness, fun: avg.avg_fun };
                Object.keys(byStat).forEach((kind) => {
                    const v = byStat[kind];
                    const tile = card.querySelector('.gd-cond__tile[data-stat="' + kind + '"]');
                    if (!tile || v == null) return;
                    tile.dataset.tone = toneOf(kind, v);
                    const vd = tile.querySelector('[data-cond-verdict]'); if (vd) vd.textContent = verdictOf(kind, v);
                    const nm = tile.querySelector('[data-cond-num]'); if (nm) countTo(nm, v, 1);
                });
                // The recommendation split. Guarded on the key existing rather than on its contents: the
                // averages dict is CACHED for an hour, so for a while after the field ships some panels
                // are drawing from a dict pickled before it existed. Missing key -> leave the row alone
                // rather than blanking a figure that is merely stale.
                const rec = avg.recommendation_split;
                const recEl = card.querySelector('[data-cond-rec]');
                if (recEl && rec && rec.answered) {
                    // Driven off the server's ORDERED options list rather than off known slugs, so the
                    // four display strings and the vocabulary itself stay server-side -- a reworded label
                    // or a new option lands here without a JS edit.
                    (rec.options || []).forEach((opt) => {
                        const cell = recEl.querySelector('[data-rec-cell="' + opt.value + '"]');
                        if (!cell) return;
                        const pct = cell.querySelector('[data-rec-pct]');
                        const n = cell.querySelector('[data-rec-count]');
                        if (pct) pct.textContent = opt.pct + '%';
                        if (n) n.textContent = opt.count.toLocaleString();
                        // The tooltip states the same fact in words, so it has to move with the figures --
                        // left alone it contradicted the percentage it sits on until the next reload.
                        const lbl = cell.getAttribute('data-rec-label');
                        if (lbl) {
                            cell.title = opt.count.toLocaleString() + ' of ' +
                                rec.answered.toLocaleString() + ' said “' + lbl + '”';
                        }
                        // A share that just went from 0 to 1 stops being held back, and one that is still
                        // 0 stays quiet -- both directions, or the dimming survives its own reason.
                        cell.classList.toggle('is-none', !opt.count);
                    });
                    const total = recEl.querySelector('[data-cond-rec-n]');
                    if (total) total.textContent = rec.answered.toLocaleString();
                    recEl.classList.remove('is-empty');
                }
                // Live-update the rating-spread chart bars + per-bar counts (10 columns keyed on the
                // integer half-step 1..10). Empty count -> clear to '' so the :empty label hides.
                if (avg.distribution) {
                    avg.distribution.forEach((row) => {
                        const el = card.querySelector('.gd-dist__col[data-dist-step="' + row.step + '"]');
                        if (!el) return;
                        const fill = el.querySelector('[data-dist-fill]'); if (fill) fill.style.height = row.bar + '%';
                        const dn = el.querySelector('[data-dist-n]'); if (dn) dn.textContent = row.count || '';
                    });
                }
            }
            // Live-sync the viewer's "Your take" comparison band (add / update / remove).
            if (card && avg) syncYouTake(card, payload, avg);
            // Live-sync the viewer's own quick take in this group's strip (add / replace / remove). Prefer
            // the server-echoed stored blurb (sanitized) over the raw typed text so the live card matches
            // what everyone else -- and the author on reload -- will see.
            syncOwnBlurb(panel, data.blurb ?? payload.blurb, payload.overall_rating);
            if (srcBtn) {
                // This attribute IS the prefill contract, and it is rebuilt from scratch here -- so
                // anything left out is silently absent the next time the modal opens. Built by the shared
                // `RatingFields.prefillFrom` rather than by hand, because the hand-built version was
                // missing `recommendation` when that field was added: the modal prefilled it fine on
                // first open (the SERVER renders this attribute) and then blank after any save, until a
                // reload put the server's version back.
                srcBtn.dataset.existing = JSON.stringify(
                    PlatPursuit.RatingFields.prefillFrom(data, payload)
                );
                srcBtn.dataset.existingBlurb = payload.blurb;
                const lbl = srcBtn.querySelector('span');
                if (lbl) lbl.textContent = 'Update rating';
            }
            // Toast AFTER close so it lands on the viewport #toast-container, not the modal's popover
            // (which the dialog takes down on close). The in-place live-update above is the primary
            // feedback; this is the persistent confirmation once the modal is gone.
            PlatPursuit.ToastManager.show(savedMsg, 'success');
        }

        // Build the viewer's own quick-take card (matches _blurb_card.html, You variant, no report). Name and
        // text go in via textContent -- never innerHTML -- so the viewer's own blurb can't inject markup.
        function buildBlurbCard(text, overall) {
            const li = document.createElement('li');
            li.className = 'gd-blurb gd-blurb--you';
            li.setAttribute('data-blurb-own', '');
            const url = root.dataset.viewerUrl || '#';
            const av = root.dataset.viewerAvatar || '';
            const fill = Math.max(0, Math.min(100, (overall / 5) * 100));
            li.innerHTML =
                '<a class="gd-blurb__av" href="' + url + '" tabindex="-1" aria-hidden="true">' +
                (av ? '<img alt="" loading="lazy" />' : '') + '</a>' +
                '<div class="gd-blurb__body"><div class="gd-blurb__head">' +
                '<a class="gd-blurb__name" href="' + url + '"></a>' +
                '<span class="gd-blurb__you">You</span>' +
                '<span class="gd-blurb__stars" style="--fill: ' + fill + '%;" role="img" aria-label="' + overall.toFixed(1) + ' out of 5"><span class="gd-blurb__stars-on"></span></span>' +
                '</div><p class="gd-blurb__text"></p>' +
                '<div class="gd-blurb__foot"><time class="gd-blurb__time">just now</time></div></div>';
            const img = li.querySelector('.gd-blurb__av img'); if (img) img.src = av;
            const nameEl = li.querySelector('.gd-blurb__name');
            const viewerName = root.dataset.viewerName || 'You';
            if (root.dataset.viewerMarkColour) {
                // Same structure the server renders (.pp-markname > .pp-supname) so the hover
                // state still applies; the glyph waits for a reload (this node is transient).
                const wrap = document.createElement('span');
                wrap.className = 'pp-markname';
                wrap.style.setProperty('--sup-t', root.dataset.viewerMarkColour);
                const nm = document.createElement('span');
                nm.className = 'pp-supname';
                nm.textContent = viewerName;
                wrap.appendChild(nm);
                nameEl.appendChild(wrap);
            } else {
                nameEl.textContent = viewerName;
            }
            li.querySelector('.gd-blurb__text').textContent = text;
            return li;
        }
        // Add / replace / remove the viewer's own card in a group's Quick takes strip after they post.
        function syncOwnBlurb(panel, text, overall) {
            const wrap = panel && panel.querySelector('[data-blurbs]');
            if (!wrap) return;
            const list = wrap.querySelector('[data-blurbs-list]');
            const existing = list && list.querySelector('[data-blurb-own]');
            const countEl = wrap.querySelector('[data-blurbs-count]');
            const bump = (d) => { if (countEl) countEl.textContent = String(Math.max(0, (parseInt(countEl.textContent, 10) || 0) + d)); };
            if (!text) {   // blurb cleared -> drop the card + decrement; re-empty the strip if it was the only one
                if (existing) { existing.remove(); bump(-1); }
                if (list && !list.children.length) wrap.classList.add('is-empty');
                return;
            }
            const fresh = buildBlurbCard(text, overall);
            if (existing) { existing.replaceWith(fresh); }                            // edit -> total unchanged
            else { if (list) list.insertBefore(fresh, list.firstChild); bump(1); }    // new take -> increment
            wrap.classList.remove('is-empty');
        }

        // Synthesized "you vs community" sentence -- mirrors core/templatetags/custom_filters.py
        // rating_comparison (keep the T threshold + wording in sync).
        function comparisonOf(u, a) {
            const d = u.difficulty - a.avg_difficulty, g = u.grindiness - a.avg_grindiness, f = u.fun_ranking - a.avg_fun;
            const T = 0.8;
            if (Math.abs(d) < T && Math.abs(g) < T && Math.abs(f) < T) return 'Right in line with the community.';
            const diff = d >= T ? 'tougher than most' : d <= -T ? 'easier than most' : 'about as tough as most';
            const grind = g >= T ? 'grindier' : g <= -T ? 'less grindy' : 'about as grindy';
            const fun = f >= T ? 'more fun' : f <= -T ? 'less fun' : 'just as fun';
            const conj = (f >= T && (d >= T || g >= T)) ? 'but' : 'and';
            return 'You found it ' + diff + ', ' + grind + ', ' + conj + ' ' + fun + '.';
        }
        // Add / update / remove the "Your take" band after a submit. Shows only with >1 rating to compare to.
        function syncYouTake(card, u, a) {
            let band = card.querySelector('[data-cond-you]');
            if (!a || a.count <= 1) { if (band) band.remove(); return; }
            if (!band) {
                band = document.createElement('div');
                band.className = 'gd-cond__you';
                band.setAttribute('data-cond-you', '');
                band.innerHTML = '<span class="gd-cond__you-lbl">Your take</span>' +
                    '<p class="gd-cond__you-txt" data-cond-you-txt></p>' +
                    '<span class="gd-cond__you-scores" data-cond-you-scores></span>';
                const tiles = card.querySelector('.gd-cond__metrics');
                if (tiles) tiles.insertAdjacentElement('afterend', band); else card.appendChild(band);
            }
            const txt = band.querySelector('[data-cond-you-txt]'); if (txt) txt.textContent = comparisonOf(u, a);
            const sc = band.querySelector('[data-cond-you-scores]');
            // Numbers only (our own computed floats) -> innerHTML is safe here.
            if (sc) sc.innerHTML = 'You <b>' + u.overall_rating.toFixed(1) + '</b>' +
                '<span class="gd-cond__you-vs">vs</span>community <b>' + a.avg_rating.toFixed(1) + '</b>';
        }

        // ── Report a quick take. One shared modal, opened from any card's [data-blurb-report]. ──
        const reportModal = document.getElementById('gd-blurb-report-modal');
        const reportForm = document.getElementById('gd-blurb-report-form');
        if (reportModal && reportForm) {
            let reportId = null, reportCard = null;
            function closeReport() { pageRecede(false); if (reportModal.close && reportModal.open) reportModal.close(); }
            reportModal.querySelectorAll('[data-gd-modal-close]').forEach((b) => b.addEventListener('click', closeReport));
            reportModal.addEventListener('click', (e) => { if (e.target === reportModal) closeReport(); });
            reportModal.addEventListener('cancel', (e) => { e.preventDefault(); closeReport(); });
            if (PlatPursuit.dismissableSheet) PlatPursuit.dismissableSheet(reportModal, { onClose: closeReport });

            document.addEventListener('click', (e) => {
                const btn = e.target.closest('[data-blurb-report]');
                if (!btn) return;
                reportId = btn.dataset.ratingId;
                reportCard = btn.closest('.gd-blurb');
                reportForm.reset();
                if (reportModal.showModal && !reportModal.open) {
                    const y = window.scrollY; reportModal.showModal();
                    if (window.scrollY !== y) window.scrollTo(0, y);
                    pageRecede(true);
                }
            });

            reportForm.addEventListener('submit', async (e) => {
                e.preventDefault();
                if (!reportId) return;
                const rSubmit = reportForm.querySelector('[data-gd-report-submit]');
                if (rSubmit) { rSubmit.disabled = true; rSubmit.textContent = 'Sending…'; }
                const payload = {
                    reason: reportForm.querySelector('[data-gd-report-reason]').value,
                    details: (reportForm.querySelector('[data-gd-report-details]').value || '').slice(0, 500),
                };
                try {
                    const data = await PlatPursuit.API.post('/api/v1/ratings/blurb/' + reportId + '/report/', payload);
                    if (reportCard) reportCard.classList.add('is-reported');
                    closeReport();
                    // Toast AFTER close: while the modal is open the toast host is its top-layer popover, which
                    // the dialog takes down with it on close -- so a success toast would flash and vanish. Once
                    // closed, ToastManager routes to the viewport #toast-container, where it persists.
                    PlatPursuit.ToastManager.show(data.message || 'Thanks, our team will take a look.', 'success');
                } catch (error) {
                    let msg = 'Could not submit report.';
                    try { const ed = await error.response?.json(); msg = ed?.error || msg; } catch (_) { /* ignore */ }
                    PlatPursuit.ToastManager.show(msg, 'error');
                } finally {
                    if (rSubmit) { rSubmit.disabled = false; rSubmit.textContent = 'Submit report'; }
                }
            });
        }

        // ── Community Guidelines sheet. Opens OVER the compose modal from its notice's [data-gd-guidelines-open]
        //    link, so reading the rules never loses the in-progress take. Read-only; agreement is recorded on
        //    submit. Stacking a second <dialog>.showModal() puts it on top; closing returns focus to the modal. ──
        //    Redundant now -- `RatingFields.attach` wires the sheet, and the only link to it lives inside
        //    the fields that attach composes. Kept as page-load belt-and-braces; both calls are idempotent.
        if (PlatPursuit.wireGuidelinesSheet) PlatPursuit.wireGuidelinesSheet();
    })();
});
