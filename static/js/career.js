/**
 * Career page (/career/): the hero, the Jobs dossier/sheet, the radar, and the Contracts board
 * controller (filters, facets, infinite scroll, claims).
 *
 * Moved out of templates/trophies/career.html (2026-10), where it ran inline: it is now cached by the
 * browser, hashed by collectstatic, and covered by ESLint (static/js only), which the inline copy
 * never was. Server data reaches it through the page, never through template tags in here:
 *   #career-radar   json_script island (job_render.build_profile_jobs()['radar'])
 *   #rp-facets      json_script island (board facet counts)
 *   #rp-list        data-results-url, data-accept-url, data-has-next
 * Loaded with a plain <script src> after chart.umd and claim-ceremony.js: no defer/async, so it runs
 * in the same order the inline script did.
 */
// Shared: count a number up from 0 to its target (data-countup attr, or the element's text),
// easeOutCubic. Used by the hero/stats, the dossier tiles, the sheet rows, and the modal.
function ppCountUp(el, dur) {
    var dec = parseInt(el.dataset.countupDecimals || '0', 10);
    var raw = (el.dataset.countup != null ? el.dataset.countup : (el.textContent || '').replace(/,/g, ''));
    var target = parseFloat(raw);
    if (isNaN(target)) return;
    function fmt(v) { return dec ? v.toFixed(dec) : Math.round(v).toLocaleString(); }
    el.textContent = fmt(0);
    if (target === 0) return;
    var d = dur || 750, start = null;
    function step(ts) {
        if (start === null) start = ts;
        var p = Math.min(1, (ts - start) / d);
        el.textContent = fmt(target * (1 - Math.pow(1 - p, 3)));
        if (p < 1) requestAnimationFrame(step); else el.textContent = fmt(target);
    }
    requestAnimationFrame(step);
}

// Career: discipline radar (overview + per-discipline drill-down) and tile -> detail modal.
(function () {
    var css = getComputedStyle(document.documentElement);
    var chart = null;
    var canvas = document.getElementById('disc-radar');
    var revealRadar = function () {};   // reassigned once the chart is built (bridges the if-scope)
    var reduce = window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches;

    if (typeof Chart !== 'undefined' && canvas) {
        var slugs = ['combat', 'exploration', 'mind', 'heart', 'finesse'];
        var discColors = slugs.map(function (s) { return css.getPropertyValue('--disc-' + s).trim(); });
        var cyan = css.getPropertyValue('--pp-primary').trim() || '#67d1f8';
        // The radar's dataset (built by job_render.build_profile_jobs) rides a json_script island.
        var RADAR = (function () {
            var el = document.getElementById('career-radar');
            try { return JSON.parse(el.textContent); } catch (e) { return { max: 10, modes: {}, job_slugs: {} }; }
        })();
        var RMAX = RADAR.max;
        var detail = document.getElementById('radar-detail');
        var basePR = 5, bigPR = 9;

        var DATA = RADAR.modes;   // { overview: {labels, values}, <discipline>: {labels, values} }
        // Slug per radar index, for the click-to-filter jump: overview mode -> discipline slugs (== `slugs`);
        // a per-discipline mode -> that discipline's job slugs in radar order (matches d.jobs / radar labels).
        var JOB_SLUGS = RADAR.job_slugs;
        // Map a clicked radar index -> a board filter ({discipline} in overview, {job} in a discipline mode).
        function filterForIndex(i) {
            if (i < 0) { return null; }
            if (currentMode === 'overview') { return slugs[i] ? { discipline: slugs[i] } : null; }
            var js = JOB_SLUGS[currentMode];
            return (js && js[i]) ? { job: js[i] } : null;
        }
        // Open the Contracts -> Board scoped to that filter: hand the filter to the contracts IIFE, switch
        // to the Contracts tab, and bring the board into view (mirrors the claim-banner jump).
        function jumpToBoard(filter) {
            if (!filter) { return; }
            document.dispatchEvent(new CustomEvent('pp:board-filter', { detail: filter }));
            var tab = document.getElementById('career-tab-contracts');
            if (tab) { tab.click(); }   // -> showView('contracts') via the view-toggle tablist
            var strip = document.querySelector('.pp-switch');
            if (strip) { strip.scrollIntoView({ behavior: reduce ? 'auto' : 'smooth', block: 'start' }); }
        }

        // #3 Soft glow around the radar line/fill (Chart.js has no native glow). In Overview each
        // point then re-glows in its own discipline color (the line stays the aggregate cyan).
        var glowPlugin = { id: 'radarGlow',
            beforeDatasetDraw: function (c, a) { c.ctx.save(); c.ctx.shadowColor = c.data.datasets[a.index].borderColor; c.ctx.shadowBlur = 8; },
            afterDatasetDraw: function (c) { c.ctx.restore(); },
            afterDatasetsDraw: function (c) {
                var ds = c.data.datasets[0];
                if (!Array.isArray(ds.pointBackgroundColor)) return;   // per-discipline mode is already one color
                var ctx = c.ctx;
                c.getDatasetMeta(0).data.forEach(function (pt, i) {
                    var r = (pt.options && pt.options.radius) || basePR;
                    ctx.save();
                    ctx.shadowColor = ds.pointBackgroundColor[i]; ctx.shadowBlur = 12;
                    ctx.fillStyle = ds.pointBackgroundColor[i];
                    ctx.beginPath(); ctx.arc(pt.x, pt.y, r, 0, 2 * Math.PI); ctx.fill();
                    ctx.restore();
                });
            } };
        chart = new Chart(canvas, {
            type: 'radar',
            data: { labels: [], datasets: [{ data: [], fill: true, borderWidth: 2, pointRadius: basePR, pointHoverRadius: bigPR }] },
            options: {
                responsive: true, maintainAspectRatio: true,
                animation: reduce ? false : { duration: 550, easing: 'easeOutQuart' },   // #3 smoother morph
                plugins: { legend: { display: false } },
                onHover: function (e, els) {   // #2 hovering a radar point highlights its detail row (+ clickable cursor)
                    if (e && e.native && e.native.target) { e.native.target.style.cursor = els.length ? 'pointer' : ''; }
                    if (!detail) return;
                    var idx = els.length ? els[0].index : -1;
                    detail.querySelectorAll('.rd-row').forEach(function (r) { r.classList.toggle('is-active', (+r.dataset.i) === idx); });
                },
                onClick: function (e, els) {   // clicking a radar point jumps to the Board filtered to it
                    if (els && els.length) { jumpToBoard(filterForIndex(els[0].index)); }
                },
                scales: { r: { beginAtZero: true, suggestedMax: RMAX, grid: { color: 'rgba(255,255,255,0.08)' }, angleLines: { color: 'rgba(255,255,255,0.08)' }, pointLabels: { color: '#cdd2da', font: { size: 13, weight: '600' } }, ticks: { display: false } } },
            },
            plugins: [glowPlugin],
        });

        var LABELS = {};
        document.querySelectorAll('.radar-tab').forEach(function (t) { LABELS[t.dataset.mode] = t.textContent.trim(); });
        var radarPainted = false;
        var currentMode = 'overview';

        function highlightPoint(i, on) {   // #2 enlarge one point without re-animating the whole chart
            var ds = chart.data.datasets[0];
            ds.pointRadius = ds.data.map(function (_, idx) { return (on && idx === i) ? bigPR : basePR; });
            chart.update('none');
        }

        // #1 detail rows render empty (bar 0, Lv 0), then each fills + counts up as it staggers in.
        var renderPanel = function (labels, values, colorOf, title) {
            if (!detail) return;
            var rows = labels.map(function (label, i) {
                var pct = Math.max(0, Math.min(100, (values[i] / RMAX) * 100));
                var c = colorOf(i);
                return '<li class="rd-row" data-i="' + i + '" role="button" tabindex="0" title="Filter the Board by ' + label + '" style="--rd: ' + c + ';">'
                    + '<span class="rd-row__label">' + label + '</span>'
                    + '<span class="pp-horizon pp-horizon__track rd-row__bar" data-fill="' + pct + '%" style="--horizon-accent: ' + c + '; --horizon-h: 8px; --horizon-progress: ' + (reduce ? pct : 0) + '%;"><span class="pp-horizon__fill"></span></span>'
                    + '<span class="rd-row__val">Lv <b data-countup="' + values[i] + '">' + (reduce ? values[i] : 0) + '</b></span></li>';
            }).join('');
            detail.innerHTML = '<div class="radar-detail__title">' + title + '</div><ul class="radar-detail__list">' + rows + '</ul>';
            if (reduce) return;
            detail.querySelectorAll('.rd-row').forEach(function (row, i) {   // each row's content follows its stagger
                setTimeout(function () {
                    var bar = row.querySelector('.rd-row__bar');
                    if (bar) bar.style.setProperty('--horizon-progress', bar.dataset.fill);
                    var v = row.querySelector('b[data-countup]');
                    if (v) ppCountUp(v, 550);
                }, i * 60 + 40);
            });
        };

        // #2 hovering a detail row highlights its radar point (delegated; rows re-render each paint).
        if (detail) {
            var hoveredI = -1;
            detail.addEventListener('mouseover', function (e) {
                var r = e.target.closest('.rd-row'); if (!r) return;
                var i = +r.dataset.i; if (i === hoveredI) return;
                hoveredI = i; highlightPoint(i, true);
                detail.querySelectorAll('.rd-row').forEach(function (x) { x.classList.toggle('is-active', x === r); });
            });
            detail.addEventListener('mouseleave', function () {
                hoveredI = -1; highlightPoint(-1, false);
                detail.querySelectorAll('.rd-row').forEach(function (x) { x.classList.remove('is-active'); });
            });
            // Click / Enter / Space a row -> jump to the Board filtered to that discipline (overview) or job.
            function activateRow(r) { if (r) { jumpToBoard(filterForIndex(+r.dataset.i)); } }
            detail.addEventListener('click', function (e) { activateRow(e.target.closest('.rd-row')); });
            detail.addEventListener('keydown', function (e) {
                if (e.key === 'Enter' || e.key === ' ') { var r = e.target.closest('.rd-row'); if (r) { e.preventDefault(); activateRow(r); } }
            });
        }

        var paint = function (mode) {
            currentMode = mode;
            var ds = chart.data.datasets[0];
            if (mode === 'overview') {
                chart.data.labels = DATA.overview.labels;
                ds.data = DATA.overview.values;
                ds.borderColor = cyan;
                ds.backgroundColor = 'rgba(103, 209, 248, 0.12)';
                ds.pointBackgroundColor = discColors;
                ds.pointBorderColor = discColors;
                renderPanel(DATA.overview.labels, DATA.overview.values, function (i) { return discColors[i]; }, 'Disciplines');
            } else {
                var color = css.getPropertyValue('--disc-' + mode).trim();
                chart.data.labels = DATA[mode].labels;
                ds.data = DATA[mode].values;
                ds.borderColor = color;
                ds.backgroundColor = color.replace(')', ' / 0.16)');
                ds.pointBackgroundColor = color;
                ds.pointBorderColor = color;
                renderPanel(DATA[mode].labels, DATA[mode].values, function () { return color; }, LABELS[mode] + ' jobs');
            }
            ds.pointRadius = basePR;   // clear any hover-enlarged point
            chart.update();
        };

        // #4 Sliding tab indicator: a pill that glides to the active tab, carrying its disc color.
        var tabsWrap = document.querySelector('.radar-tabs');
        var slider = tabsWrap && tabsWrap.querySelector('.radar-tabs__slider');
        function activeTab() { return tabsWrap && tabsWrap.querySelector('.radar-tab.is-active'); }
        function moveSlider(tab) {
            if (!slider || !tab || !tab.offsetWidth) return;
            slider.style.setProperty('--disc', getComputedStyle(tab).getPropertyValue('--disc'));
            slider.style.width = tab.offsetWidth + 'px';
            slider.style.height = tab.offsetHeight + 'px';
            slider.style.transform = 'translate(' + tab.offsetLeft + 'px,' + tab.offsetTop + 'px)';
            slider.style.opacity = '1';
        }

        // Paint + position the slider the first time the radar tab is actually on screen (a Chart drawn
        // while its tab is display:none animates on a 0-size canvas -> finishes invisibly -> "pops").
        revealRadar = function () {
            chart.resize();                                                  // canvas was 0-size while hidden
            if (!radarPainted) { radarPainted = true; paint(currentMode); }  // first reveal: animate in
            requestAnimationFrame(function () { moveSlider(activeTab()); });
        };
        var radarView = document.querySelector('.lab-view[data-view="radar"]');
        if (radarView && !radarView.hidden) revealRadar();                   // deep-linked ?view=radar

        document.querySelectorAll('.radar-tab').forEach(function (tab) {
            tab.addEventListener('click', function () {
                document.querySelectorAll('.radar-tab').forEach(function (t) { t.classList.remove('is-active'); });
                tab.classList.add('is-active');
                moveSlider(tab);
                paint(tab.dataset.mode);
            });
        });
        window.addEventListener('resize', function () { moveSlider(activeTab()); });
    }

    // Career view toggle: Jobs (default) / Radar / Contracts. Scope the query to [data-view] so the
    // nested Board|History sub-toggle (.pp-switch__chip[data-contract-view], inside the Contracts panel)
    // isn't swept into this top-level tablist.
    var viewTabs = document.querySelectorAll('.pp-switch__chip[data-view]');
    var views = document.querySelectorAll('.lab-view');
    var VIEW_ORDER = ['jobs', 'radar', 'contracts'];
    function currentView() {
        var cur = null;
        views.forEach(function (v) { if (!v.hidden) cur = v.dataset.view; });
        return cur;
    }
    var tablist;
    function showView(name) {
        var from = currentView();
        var changed = from !== name;
        var shown = null;
        views.forEach(function (v) {
            var on = v.dataset.view === name;
            v.hidden = !on;
            if (on) { shown = v; }
        });
        // (A) Directional cross-fade: the incoming panel slides + fades in from the side it lives on.
        if (changed) { PlatPursuit.slideViewIn(shown, from, name, VIEW_ORDER); }
        var activeTab = null;
        viewTabs.forEach(function (t) {
            var on = t.dataset.view === name;
            t.classList.toggle('is-active', on);
            t.setAttribute('aria-selected', on ? 'true' : 'false');
            if (on) { activeTab = t; }
        });
        if (tablist) { tablist.syncTabindex(); }            // (D) roving tabindex -- only the active tab is Tab-reachable
        if (changed) { PlatPursuit.igniteTab(activeTab); }  // (B) the newly-active pill blooms its glow once
        // Reflect the active tab in the URL (shareable + reload-safe) and SCOPE the contract filters to the
        // Contracts tab -- strip them when leaving (a shared Jobs/Radar link stays clean); returning re-adds
        // them from the still-set filter UI via pp:contracts-shown -> syncURL().
        PlatPursuit.syncViewParam(name, { default: 'jobs', paramView: 'contracts', params: ['status', 'new', 'q', 'sort', 'discipline', 'job', 'platform', 'page', 'scope'] });
        // Animate the radar in the first time it's shown (and keep it correctly sized after).
        if (name === 'radar') revealRadar();
        // Stagger the contract cards in on reveal (mirrors the Jobs tab's content animation).
        if (name === 'contracts' && changed) document.dispatchEvent(new CustomEvent('pp:contracts-shown'));
    }
    // Click + Arrow/Home/End keyboard nav + roving tabindex, all via the shared tablist helper.
    // Guarded like every other shared-helper call: an unguarded throw here would take the rest of this
    // IIFE with it. Pre-existing; caught by the same audit that found four of them in the board IIFE.
    tablist = PlatPursuit.wireTablist
        ? PlatPursuit.wireTablist(viewTabs, { onSelect: function (t) { showView(t.dataset.view); } })
        : null;
    // A one-shot ignite on load so the active pill "comes alive".
    var initTab = document.querySelector('.pp-switch__chip[data-view].is-active');
    if (initTab) { setTimeout(function () { if (initTab.classList.contains('is-active')) { PlatPursuit.igniteTab(initTab); } }, 260); }

    // Rail / deep-link jumps to a specific Career view without a reload.
    document.querySelectorAll('[data-lab-goto]').forEach(function (el) {
        el.addEventListener('click', function (e) {
            e.preventDefault();
            showView(el.dataset.labGoto);
            // A status jump (the claim banner) opens the Board on that status across every platform. It used to
            // click the status chip, which kept whatever scope was showing: from History it lit a hidden chip,
            // the server dropped the status there, and the hunter stayed on History.
            var f = el.dataset.gotoFilter;
            if (f) document.dispatchEvent(new CustomEvent('pp:board-filter', { detail: { status: f, allPlatforms: true } }));
            // Scroll the Career view up under the sticky chrome (scroll-padding-top handles the offset)
            // so the toolbar + the now-filtered board come into view -- 'nearest' barely moved since the
            // banner sits right above the tabs.
            var tabs = document.querySelector('.pp-switch');
            if (tabs) tabs.scrollIntoView({ behavior: 'smooth', block: 'start' });
        });
    });
})();

// Jobs layout toggle: Dossier <-> Character sheet, remembered per browser.
(function () {
    var btns = document.querySelectorAll('.jlayout__btn');
    var layouts = document.querySelectorAll('.jobs-layout');
    if (!btns.length) return;
    var KEY = 'pp-jobs-layout';
    var reduce = window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches;
    var sheetRevealed = false;
    // The character sheet assembles on its first reveal: sections cascade in, and each section's bars
    // fill + levels tick up AS it reveals (delay matched to the CSS section stagger, not all at once).
    function revealSheet() {
        var sheet = document.querySelector('.jsheet');
        if (sheetRevealed || reduce || !sheet) return;
        sheetRevealed = true;
        sheet.classList.add('jsheet--revealing');
        sheet.querySelectorAll('.jsheet__bar[data-fill]').forEach(function (b) { b.style.setProperty('--horizon-progress', '0%'); });
        sheet.querySelectorAll('.jsheet__disc').forEach(function (sec, i) {
            setTimeout(function () {   // matches the 0.07s-per-section CSS cascade
                sec.querySelectorAll('.jsheet__bar[data-fill]').forEach(function (b) { b.style.setProperty('--horizon-progress', b.dataset.fill); });
                sec.querySelectorAll('.jsheet__lv').forEach(function (l) { ppCountUp(l, 650); });
            }, i * 70 + 40);
        });
    }
    function show(name) {
        layouts.forEach(function (l) { l.hidden = (l.dataset.layout !== name); });
        btns.forEach(function (b) {
            var on = b.dataset.layout === name;
            b.classList.toggle('is-active', on);
            b.setAttribute('aria-selected', on ? 'true' : 'false');
        });
        if (name === 'sheet') revealSheet();
        try { localStorage.setItem(KEY, name); } catch (e) {}
    }
    var saved = null;
    try { saved = localStorage.getItem(KEY); } catch (e) {}
    if (saved === 'sheet' || saved === 'dossier') show(saved);
    btns.forEach(function (b) { b.addEventListener('click', function () { show(b.dataset.layout); }); });
})();

(function () {
    var details = document.getElementById('element-details');
    var modal = document.getElementById('emodal');
    var panel = document.getElementById('emodal-panel');
    var dialog = modal ? modal.querySelector('.emodal__dialog') : null;
    var backdrop = modal ? modal.querySelector('.emodal__backdrop') : null;
    var zoom = document.getElementById('zoom-container');
    if (!details || !modal || !panel || !dialog) return;
    var flipEl = null, focusEl = null, afterFn = null, portaled = false, scrollFn = null;
    var reduce = window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches;
    var FOCUSABLE = 'a[href], button:not([disabled]), [tabindex]:not([tabindex="-1"])';

    // Container-transform: the dialog grows from (and shrinks back to) the tapped tile.
    function flip(fromRect, opening) {
        dialog.style.animation = 'none';   // suppress the default pop first, so we measure the true final rect
        dialog.style.transformOrigin = 'center center';
        var dr = dialog.getBoundingClientRect();
        var sx = Math.max(0.06, fromRect.width / dr.width);
        var sy = Math.max(0.06, fromRect.height / dr.height);
        var dx = (fromRect.left + fromRect.width / 2) - (dr.left + dr.width / 2);
        var dy = (fromRect.top + fromRect.height / 2) - (dr.top + dr.height / 2);
        var t = 'translate(' + dx + 'px,' + dy + 'px) scale(' + sx + ',' + sy + ')';
        if (opening) {
            dialog.style.transition = 'none';
            dialog.style.transform = t;
            dialog.style.opacity = '0.4';
            dialog.getBoundingClientRect();   // reflow so the start state sticks
            dialog.style.transition = 'transform 0.42s cubic-bezier(0.34, 1.2, 0.64, 1), opacity 0.24s ease';   // spring overshoot
            dialog.style.transform = 'none';
            dialog.style.opacity = '1';
        } else {
            dialog.style.transition = 'transform 0.26s cubic-bezier(0.4,0,1,1), opacity 0.2s ease';
            dialog.style.transform = t;
            dialog.style.opacity = '0';
        }
    }
    // Content choreography: count the level up + fill the XP bar once the panel is injected (the
    // glyph draw + section stagger are CSS animations that play on injection).
    // Post-inject content choreography, per source. Job detail: count the level + fill the XP bar.
    function animateJobContent() {
        if (reduce) return;
        var lvl = panel.querySelector('.edetail__level-num');
        if (lvl) ppCountUp(lvl, 700);
        var bar = panel.querySelector('.edetail__bar[data-fill]');
        if (bar) {
            bar.style.setProperty('--horizon-progress', '0%');
            requestAnimationFrame(function () { requestAnimationFrame(function () { bar.style.setProperty('--horizon-progress', bar.dataset.fill); }); });
        }
    }
    // Contract detail: once the dialog has grown, cascade the job pills in, then the game cards.
    function animateContractContent() {
        if (reduce) return;   // content is visible via the reduced-motion CSS override
        var i = 0;
        panel.querySelectorAll('.rpm-job').forEach(function (el) { el.style.animationDelay = (i++ * 55) + 'ms'; el.classList.add('rpm-in'); });
        panel.querySelectorAll('.rpg').forEach(function (el) {
            var delay = i++ * 45;
            el.style.animationDelay = delay + 'ms';
            el.classList.add('rpm-in');
            var fill = el.querySelector('.rpg__prog-fill');   // (D) fill the bar just after the card rises in
            if (fill && fill.dataset.fill) { fill.style.width = '0%'; setTimeout(function () { fill.style.width = fill.dataset.fill; }, delay + 260); }
        });
    }
    function resetDialog() {
        ['animation', 'transition', 'transform', 'transformOrigin', 'opacity'].forEach(function (p) { dialog.style[p] = ''; });
        if (backdrop) { backdrop.style.transition = ''; backdrop.style.opacity = ''; }
    }
    function hideNow() {
        modal.hidden = true;
        if (scrollFn) { dialog.removeEventListener('scroll', scrollFn); scrollFn = null; }
        panel.innerHTML = '';
        document.body.style.overflow = '';
        if (zoom) zoom.classList.remove('pp-receded');
        dialog.classList.remove('emodal__dialog--wide');
        resetDialog();
        if (focusEl && document.contains(focusEl)) focusEl.focus();   // restore focus to the trigger
        flipEl = focusEl = afterFn = null;
    }

    // Generic open: inject html, grow the dialog from flipTarget's rect, restore focus to focusTarget.
    function open(html, flipTarget, focusTarget, after) {
        if (!portaled && zoom) { document.body.appendChild(modal); portaled = true; }   // out of the scaled wrapper
        panel.innerHTML = html;
        modal.hidden = false;
        document.body.style.overflow = 'hidden';
        if (zoom && !reduce) zoom.classList.add('pp-receded');   // the page pushes back
        flipEl = flipTarget || null; focusEl = focusTarget || flipTarget || null; afterFn = after || null;
        if (!reduce && flipEl) flip(flipEl.getBoundingClientRect(), true);   // grow from the source
        setTimeout(function () { if (afterFn) afterFn(); }, 340);   // content waits for the dialog to grow
        (dialog.querySelector('.emodal__close') || dialog).focus();   // move focus into the dialog
    }
    function openEl(slug, trigger) {
        var src = details.querySelector('[data-slug="' + slug + '"]');
        if (!src) return;
        open(src.innerHTML, trigger, trigger, animateJobContent);
    }
    function openContract(card, btn) {
        var tpl = card.querySelector('.rp-modal-src');
        if (tpl) { openContractHtml(tpl.innerHTML, card, btn); return; }   // legacy in-page modal
        var url = btn && btn.getAttribute('data-modal-url');               // server path: fetch on open
        if (!url) return;
        fetch(url, { headers: { 'X-Requested-With': 'XMLHttpRequest' }, credentials: 'same-origin' })
            .then(function (r) { return r.ok ? r.text() : null; })
            .then(function (html) { if (html != null) openContractHtml(html, card, btn); })
            .catch(function () {});
    }
    function openContractHtml(html, card, btn) {
        dialog.classList.add('emodal__dialog--wide');   // roomier dialog for the games grid
        open(html, card, btn, animateContractContent);   // grow from the card, focus back to the button
        var head = panel.querySelector('.rpm__head'), bar = panel.querySelector('.rpm__bar'), headImg = panel.querySelector('.rpm__head-art img');
        if (!head || !bar) return;
        if (reduce) {
            // Reduced motion: discrete swap, no scroll-linked transforms.
            scrollFn = function () { bar.classList.toggle('is-stuck', dialog.scrollTop > head.offsetHeight - 46); };
        } else {
            bar.style.transition = 'none';   // (B) opacity is scroll-driven, so no lag  [REVERT B: remove this line]
            var ticking = false;
            scrollFn = function () {
                if (ticking) return; ticking = true;
                requestAnimationFrame(function () {
                    ticking = false;
                    var st = dialog.scrollTop, p = Math.min(1, st / Math.max(1, head.offsetHeight - 46));
                    if (headImg) headImg.style.transform = 'translate3d(0,' + (p * 20).toFixed(1) + 'px,0)';   // (A) header parallax
                    // (B) Continuous collapse: dissolve the full header into the compact bar as you scroll.
                    //     TO REVERT B: delete the next three lines and the bar.style.transition line above,
                    //     then use: bar.classList.toggle('is-stuck', st > head.offsetHeight - 46);
                    head.style.opacity = (1 - Math.min(1, p * 1.3)).toFixed(3);
                    bar.style.opacity = p.toFixed(3);
                    bar.classList.toggle('is-stuck', p > 0.02);   // pointer-events once visible
                });
            };
        }
        dialog.addEventListener('scroll', scrollFn, { passive: true });
        scrollFn();   // set the initial state
    }
    function closeEl() {
        if (zoom) zoom.classList.remove('pp-receded');
        if (!reduce && flipEl && document.contains(flipEl)) {
            var done = false;
            var finish = function () { if (done) return; done = true; dialog.removeEventListener('transitionend', onEnd); hideNow(); };
            var onEnd = function (e) { if (e.target === dialog && e.propertyName === 'transform') finish(); };
            dialog.addEventListener('transitionend', onEnd);
            flip(flipEl.getBoundingClientRect(), false);   // shrink back to the source
            setTimeout(finish, 400);   // safety net if transitionend doesn't fire
        } else {
            hideNow();
        }
    }
    document.querySelectorAll('.ptile').forEach(function (t) {
        t.addEventListener('click', function () { openEl(t.dataset.slug, t); });
        t.addEventListener('keydown', function (e) { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); openEl(t.dataset.slug, t); } });
    });
    var rpListEl = document.getElementById('rp-list');   // delegated: streamed-in cards work too
    if (rpListEl) rpListEl.addEventListener('click', function (e) {
        var b = e.target.closest('.rp-tier2-btn');
        if (b) { var card = b.closest('.rp-row'); if (card) openContract(card, b); }
    });
    modal.querySelectorAll('[data-close]').forEach(function (b) { b.addEventListener('click', closeEl); });
    document.addEventListener('keydown', function (e) {
        if (modal.hidden) return;
        if (e.key === 'Escape') { closeEl(); return; }
        if (e.key === 'Tab') {   // trap Tab within the dialog
            var f = Array.prototype.slice.call(dialog.querySelectorAll(FOCUSABLE))
                .filter(function (el) { return el.offsetParent !== null; });
            if (!f.length) { e.preventDefault(); dialog.focus(); return; }
            var first = f[0], last = f[f.length - 1];
            if (e.shiftKey && document.activeElement === first) { e.preventDefault(); last.focus(); }
            else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first.focus(); }
        }
    });

    // Drag-to-dismiss (touch): flick the dialog down to close -- the shared PlatPursuit.dismissableSheet
    // (adds the grabber handle + owns the slide-off); onClose steps the page back forward + hides.
    if (window.PlatPursuit && PlatPursuit.dismissableSheet) {
        PlatPursuit.dismissableSheet(dialog, {
            scrim: backdrop,
            onClose: function () { if (zoom) { zoom.classList.remove('pp-receded'); } hideNow(); }
        });
    }
})();

// Premium: count numbers up (Career Level, discipline totals, stat cards) as they scroll into view.
// The dossier tile levels are excluded here -- the band reveal drives those in sync with each band.
(function () {
    var els = Array.prototype.slice.call(document.querySelectorAll('[data-countup]'))
        .filter(function (el) { return !el.closest('.jdoss'); });
    if (!els.length) return;
    var reduce = window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches;
    function fmt(v, dec) { return dec ? v.toFixed(dec) : Math.round(v).toLocaleString(); }
    function run(el) {
        var target = parseFloat(el.dataset.countup);
        if (isNaN(target)) return;
        var dec = parseInt(el.dataset.countupDecimals || '0', 10);
        if (reduce || target === 0) { el.textContent = fmt(target, dec); return; }
        var dur = 900, start = null;
        function step(ts) {
            if (start === null) start = ts;
            var p = Math.min(1, (ts - start) / dur);
            var eased = 1 - Math.pow(1 - p, 3);   // easeOutCubic
            el.textContent = fmt(target * eased, dec);
            if (p < 1) requestAnimationFrame(step); else el.textContent = fmt(target, dec);
        }
        requestAnimationFrame(step);
    }
    if (reduce || !('IntersectionObserver' in window)) { els.forEach(run); return; }
    els.forEach(function (el) { el.textContent = fmt(0, parseInt(el.dataset.countupDecimals || '0', 10)); });  // reset to 0 (no flash of final)
    var io = new IntersectionObserver(function (entries) {
        entries.forEach(function (e) { if (e.isIntersecting) { run(e.target); io.unobserve(e.target); } });
    }, { threshold: 0.4 });
    // Gated on the first-visit modal: numbers hold at 0 behind its scrim and tick on close.
    (window.ppAfterCareerModal || function (f) { f(); })(function () {
        els.forEach(function (el) { io.observe(el); });
    });
})();

// Premium: the hero rank ladder's "X to next" counts DOWN from the from-zero distance (the next
// boundary level -- thousands, high up) to the real remaining, in sync with the bars filling on load.
(function () {
    var el = document.querySelector('.pgl--rank .pgl__next-n');
    if (!el || el.dataset.cdFrom == null) return;
    var from = parseFloat(el.dataset.cdFrom), to = parseFloat(el.dataset.cdTo);
    if (isNaN(from) || isNaN(to)) return;
    var reduce = window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches;
    function run() {
        if (reduce) { el.textContent = to.toLocaleString(); return; }
        var dur = 1800, start = null, span = to - from;
        function step(ts) {
            if (start === null) start = ts;
            var p = Math.min(1, (ts - start) / dur);
            var eased = 1 - Math.pow(1 - p, 3);   // easeOutCubic (matches the ladder fill)
            el.textContent = Math.round(from + span * eased).toLocaleString();
            if (p < 1) requestAnimationFrame(step); else el.textContent = to.toLocaleString();
        }
        requestAnimationFrame(step);
    }
    if (reduce || !('IntersectionObserver' in window)) { run(); return; }
    el.textContent = from.toLocaleString();   // start big (no flash of the final number)
    var io = new IntersectionObserver(function (entries) {
        entries.forEach(function (e) { if (e.isIntersecting) { run(); io.unobserve(e.target); } });
    }, { threshold: 0.4 });
    (window.ppAfterCareerModal || function (f) { f(); })(function () { io.observe(el); });
})();

// The discipline cross-highlight (hover an arc or a legend row -> spotlight that discipline) used to live
// here as ~27 lines of JS. It is now declarative in `elements.css`, scoped to `.lab-dna-pair` (career.html's
// hero wrapper), and
// shared with the home lobby and the Hall of Fame plaque -- which the JS never reached, so those two had no
// highlight until the CSS was written and then briefly had a SECOND implementation alongside this one.
//
// What the JS did and the CSS now does: dim the other arcs, dim the other legend rows, pause the spin, and
// (new) swap the ring's centre for the hovered discipline's level. The JS dimmed arcs to 0.16 inline, which
// beat the stylesheet's 0.2 and made Career the one host that looked different.

// Premium: each dossier band reveals on scroll-in (slides up); its content THEN follows -- the icons
// draw, the bars fill, the levels count up -- so nothing animates while the band is still sliding in.
(function () {
    var jdoss = document.querySelector('.jdoss');
    if (!jdoss) return;
    var bands = Array.prototype.slice.call(jdoss.querySelectorAll('.jdoss__band'));
    var reduce = window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches;
    if (reduce || !('IntersectionObserver' in window) || !bands.length) return;   // static: renders final
    jdoss.classList.add('jdoss--js');
    bands.forEach(function (band) {   // pre-hide the content until the band reveals
        band.querySelectorAll('.job__bar[data-fill]').forEach(function (b) { b.style.setProperty('--horizon-progress', '0%'); });
        band.querySelectorAll('.job').forEach(function (t) { t.classList.add('job--predraw'); });
        band.querySelectorAll('.job__lv-num[data-countup]').forEach(function (l) { l.textContent = '0'; });
    });
    function revealContent(band) {
        band.querySelectorAll('.job__bar[data-fill]').forEach(function (b) { b.style.setProperty('--horizon-progress', b.dataset.fill); });
        band.querySelectorAll('.job').forEach(function (t) { t.classList.remove('job--predraw'); t.classList.add('job--drawing'); });
        band.querySelectorAll('.job__lv-num[data-countup]').forEach(function (l) { ppCountUp(l, 650); });
    }
    var io = new IntersectionObserver(function (entries) {
        entries.filter(function (e) { return e.isIntersecting; }).forEach(function (e, i) {
            io.unobserve(e.target);
            var band = e.target;
            setTimeout(function () {   // stagger bands that appear together (on load)
                band.classList.add('is-revealed');
                setTimeout(function () { revealContent(band); }, 110);   // content follows closely -- no dead beat
            }, i * 90);
        });
    }, { threshold: 0.2 });
    (window.ppAfterCareerModal || function (f) { f(); })(function () {
        bands.forEach(function (b) { io.observe(b); });
    });
})();

// Premium: cursor-tracking 3D tilt on the dossier job tiles (hover-capable, motion-OK devices only).
(function () {
    var canHover = window.matchMedia && window.matchMedia('(hover: hover)').matches;
    var reduce = window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches;
    if (!canHover || reduce) return;
    var MAX = 6;   // max tilt degrees
    document.querySelectorAll('.jdoss .job').forEach(function (tile) {
        var r = null;   // base (untilted) rect, cached on enter so the tilt can't feed back on itself
        tile.addEventListener('mouseenter', function () { r = tile.getBoundingClientRect(); });
        tile.addEventListener('mousemove', function (e) {
            if (!r) r = tile.getBoundingClientRect();
            var px = (e.clientX - r.left) / r.width - 0.5;
            var py = (e.clientY - r.top) / r.height - 0.5;
            tile.style.transition = 'transform 0.08s ease';
            tile.style.transform = 'perspective(600px) rotateX(' + (-py * MAX).toFixed(2) + 'deg) rotateY(' + (px * MAX).toFixed(2) + 'deg) translateY(-3px) scale(1.02)';
        });
        tile.addEventListener('mouseleave', function () { r = null; tile.style.transition = 'transform 0.3s ease'; tile.style.transform = ''; });
    });
})();

// Contracts board: server-driven filter/sort + infinite scroll. Per-card behaviors (progressive
// image load, hover carousel, reveal choreography) are re-run for every streamed-in batch via
// initCards(); the icon<->ring link and the claim flow are delegated off #rp-list. (The tier-2 trigger
// is NOT -- it is bound in a different IIFE above, near the modal it opens. This line said otherwise.)
(function () {
    var list = document.getElementById('rp-list');
    if (!list) return;
    var PP = window.PlatPursuit || {};
    var API = PP.API, Toast = PP.ToastManager, debounce = PP.debounce;
    // NO `reduce` here any more: its three readers (the carousel, the reveal, the stagger) all moved
    // into shared helpers that gate themselves. Kept out deliberately rather than left declared-and-unread,
    // where it would read as a guard this block still applies.
    var hasIO = 'IntersectionObserver' in window;

    // ---- Per-card behaviors (idempotent; data-*Init guards stop a re-init double-binding) ----
    // The shared `progressiveArt` (utils.js) since job detail became the second page rendering this
    // card. It shipped there without this call and every cover shimmered forever -- the skeleton's
    // infinite animation only stops on `.is-loaded`, which is JS's job, so the behaviour had to travel
    // with the markup rather than stay here.
    function initProgressiveLoad(scope) { if (PP.progressiveArt) { PP.progressiveArt(scope); } }

    // The shared `coverCarousel` (utils.js) since job detail became the second page rendering this card.
    // Its beat and fade were local constants here; they travel with the behaviour now, and the fade still
    // has to match the CSS transition on `.rp-tile__img--fx`.
    function initCarousel(scope) { if (PP.coverCarousel) { PP.coverCarousel(scope, { beat: 1500, fade: 560 }); } }

    // The scroll-in reveal (XP count-up, tier bars filling, ring arcs drawing, job cells igniting), on
    // the shared `cardReveal` since job detail became the second page that wants it.
    //
    // RESTORED here in the same change: an earlier extraction spliced this file between two anchors and
    // took the observer and its `revealCard` body with it, leaving `initReveal` referencing an undeclared
    // `revealIO`. That threw on every `initCards()` -- so infinite scroll, the entrance stagger and the
    // `pp:contracts-shown` listener, all of which run AFTER it, silently never started.
    var cardRevealer = PP.cardReveal ? PP.cardReveal() : null;
    function initReveal(scope) {
        if (!cardRevealer) { return; }
        cardRevealer.observe(scope.querySelectorAll('.rp-row'));
    }

    function initCards(scope) {
        scope = scope || list;
        initProgressiveLoad(scope);
        initCarousel(scope);
        initReveal(scope);
    }

    // The batch entrance rise (chip change / appended page / tab open), on the shared `staggerCards`.
    function staggerCards(cards) { if (PP.staggerCards) { PP.staggerCards(cards); } }

    // ---- icon <-> ring link: the shared `ringHoverLink` (utils.js), delegated so it survives
    // streamed-in cards. Extracted here when job detail became the second caller -- same behaviour, and
    // that page passes its own item selector because it shows the jobs as named pills, not as the map.
    // GUARDED, like every other `PlatPursuit.*` call in this file. This one sits at IIFE top level, so
    // an unguarded throw here takes the entire board controller below it with it -- the claim flow, every
    // filter and chip, `seedFromURL`, `initCards`, the scroller and the landing stagger. The page would
    // still render its server-side cards and look completely correct. `PP` is snapshotted as
    // `window.PlatPursuit || {}`, and a stale cached `utils.js` is a documented failure mode here.
    if (PP.ringHoverLink) { PP.ringHoverLink(list); }

    // ---- Delegated: claim flow ----
    function markAccepted(card) {
        card.dataset.status = 'accepted';
        card.classList.remove('rp-row--claimable'); card.classList.add('rp-row--accepted');
        var badge = card.querySelector('.rp-badge'); if (badge) badge.textContent = 'Claimed';
        card.classList.remove('rp-row--just-accepted'); void card.offsetWidth; card.classList.add('rp-row--just-accepted');
        card.addEventListener('animationend', function h(e) { if (e.target !== card) return; card.classList.remove('rp-row--just-accepted'); card.removeEventListener('animationend', h); });
    }
    function refreshAcceptAll(count) {
        var btn = document.getElementById('rp-accept-all');
        if (!btn) return;
        if (count > 0) { btn.textContent = 'Claim all (' + count + ')'; btn.disabled = false; } else { btn.remove(); }
    }
    // Reload after a claim but KEEP the tab the user is on. The Career tabs are client-side only, so a
    // plain reload would land them back on the default Jobs tab. Reflect the visible panel into ?view=
    // (the server reads it to set the active tab) and preserve the rest of the querystring (the contracts
    // board's filters/sort). If nothing changed, force a plain reload so it still refreshes.
    function reloadKeepingView() {
        var vis = document.querySelector('.lab-view:not([hidden])');
        var v = vis && vis.dataset.view;
        var url = new URL(window.location.href);
        if (v === 'jobs') url.searchParams.delete('view');   // jobs is the default -> clean URL (matches showView)
        else if (v) url.searchParams.set('view', v);
        if (url.href === window.location.href) window.location.reload();
        else window.location.replace(url.href);   // replace (not assign): a refresh shouldn't add a history entry
    }
    function applyResult(res) {
        (res.accepted || []).forEach(function (slug) {
            var sel = (window.CSS && CSS.escape) ? CSS.escape(slug) : slug;
            var card = list.querySelector('.rp-row[data-slug="' + sel + '"]');
            if (!card) return;
            markAccepted(card);
            // Drop the card if it no longer belongs in the current view: the claimable filter (it's now
            // accepted), or the Board scope once it's fully banked (it graduates to History). We can't know
            // "fully banked" client-side, so on the Board we remove it and let the post-ceremony reload
            // reconcile -- a fully-banked one won't come back; a partially-banked one (100% still to earn)
            // reappears from the server. That reload happens right after, so the flicker is imperceptible.
            if (state.status === 'claimable' || state.scope === 'board') card.remove();
        });
        refreshAcceptAll(res.claimable_count || 0);
        // The claim ceremony IS the feedback (the award flows into the jobs it leveled). Toast only
        // as a fallback when the ceremony module or its payload isn't available.
        // A claim levels real jobs, so the Jobs (dossier + sheet), Radar, and hero panels -- ALL built
        // from the server's career context -- are now stale in the DOM (the tabs render once on load and
        // just toggle). Refresh once the moment is over so every panel reflects the new levels. We reload
        // rather than patch client-side because the radar canvas + per-discipline geometry can't be
        // re-derived without duplicating career_service; a reload is correct across every surface at once.
        var claim = res.claim, Ceremony = PP.ClaimCeremony;
        if (Ceremony && claim && claim.jobs && claim.jobs.length) {
            Ceremony.play(claim).then(reloadKeepingView);   // refresh (on the same tab) after the ceremony closes
        } else {
            if (Toast && res.granted) Toast.show('Banked ' + Number(res.granted).toLocaleString() + ' XP', 'success');
            if (res.granted) setTimeout(reloadKeepingView, 1100);   // no ceremony -> still refresh, same tab
        }
    }
    async function accept(payload, btn) {
        if (!API) return;
        if (btn) { btn.disabled = true; btn.classList.add('btn-disabled'); }
        try { applyResult(await API.post(ACCEPT_URL, payload)); }
        catch (err) {
            var data = err.response ? await err.response.json().catch(function () { return null; }) : null;
            if (Toast) Toast.show((data && data.error) || 'Could not claim the Contract.', 'error');
            if (btn) { btn.disabled = false; btn.classList.remove('btn-disabled'); }
        }
    }
    list.addEventListener('click', function (e) { var b = e.target.closest('.rp-accept'); if (b) { e.preventDefault(); accept({ slug: b.dataset.slug }, b); } });
    var allBtn = document.getElementById('rp-accept-all');
    if (allBtn) allBtn.addEventListener('click', function () { accept({ all: true }, allBtn); });

    // ---- Server-driven filter/sort + infinite scroll ----
    var RESULTS_URL = list.dataset.resultsUrl;
    // Every URL param the board owns: what syncURL clears before re-writing, and what marks a URL as
    // carrying a board filter. (showView's strip list, in the view IIFE, is a separate copy; a test holds
    // it to this one.)
    var BOARD_KEYS = ['status', 'new', 'q', 'sort', 'discipline', 'job', 'platform', 'scope'];
    var ACCEPT_URL = list.dataset.acceptUrl;
    var sentinel = document.getElementById('rp-sentinel');
    var loading = document.getElementById('rp-loading');
    var noMatch = document.getElementById('rp-no-match');
    var empty = document.getElementById('rp-empty');
    var searchInput = document.getElementById('rp-search');
    var searchField = null;   // PlatPursuit.wireSearchField handle (clear/Escape/has-value + spinner setBusy)
    var statusBar = document.querySelector('.rp-filters');
    // Selected by its DATA hook, not its class: the status chips next to it are found by
    // `data-filter`, so behaviour reads from data attributes here and the class stays styling.
    // It was a class lookup with an unread `data-new` sitting beside it -- markup that looks
    // like a hook and is not, which this codebase has been caught by before.
    var newChip = document.querySelector('[data-new]');
    function syncNewChip() {
        if (!newChip) return;
        newChip.classList.toggle('is-active', !!state.newOnly);
        newChip.setAttribute('aria-pressed', state.newOnly ? 'true' : 'false');
    }
    if (newChip) newChip.addEventListener('click', function () {
        // A toggle, not a status: it narrows whatever the status chips already selected.
        state.newOnly = !state.newOnly;
        syncNewChip();
        fetchPage(1, false);
    });
    var sortSel = document.getElementById('rp-sort-select');
    var countEl = document.getElementById('rp-count');
    var advToggle = document.getElementById('rp-adv-toggle');
    var advPanel = document.getElementById('rp-advanced');
    var advBadge = document.getElementById('rp-adv-count');
    // SCOPED TO THE ADVANCED PANEL, and it has to be. A bare `.rp-discs` is whichever one comes
    // FIRST in the document, and the new-contracts modal (included at the top of this page, long
    // before the board) now renders one too. That made `discs` point at the MODAL: the board's
    // selection handler bound to the wrong element, the board's own popovers got no controller at
    // all, and the modal's popovers got two -- so a click opened one and the second controller,
    // seeing it already open, closed it again inside the same click. They looked dead.
    var discs = advPanel ? advPanel.querySelector('.rp-discs') : null;
    var plats = advPanel ? advPanel.querySelector('.rp-plats') : null;
    var tokensRow = document.getElementById('rp-tokens-row');
    var tokensBox = document.getElementById('rp-tokens');
    var relaxBtn = document.getElementById('rp-relax');
    var DEFAULT_PLATS = ['PS5', 'PS4'];
    var VALID_PLATS = plats ? Array.prototype.slice.call(plats.querySelectorAll('.rp-plat')).map(function (c) { return c.dataset.plat; }) : [];
    var prefersReduced = window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches;
    // jobs + disciplines are multi-select, ANDed server-side (e.g. "driver + slayer game"). `scope` is the
    // Board|History sub-view (Board = actionable, History = fully-banked).
    var state = { status: '', newOnly: false, jobs: [], disciplines: [], q: '', sort: 'relevance', platforms: null, scope: 'board' };
    var pageNo = 1, hasNext = list.dataset.hasNext === '1', busy = false;

    function buildParams(n) {
        var p = new URLSearchParams();
        if (state.status && state.status !== 'all') p.set('status', state.status);
        state.jobs.forEach(function (j) { p.append('job', j); });               // ANDed server-side
        state.disciplines.forEach(function (d) { p.append('discipline', d); });
        if (state.q) p.set('q', state.q);
        if (state.sort && state.sort !== scopeDefaultSort(state.scope)) p.set('sort', state.sort);   // omit the scope's default (clean URL)
        if (state.platforms) state.platforms.forEach(function (pl) { p.append('platform', pl); });  // null -> server default (current-gen)
        if (state.newOnly) p.set('new', '1');
        if (state.scope === 'history') p.set('scope', 'history');   // board is the default -> omitted (clean URL)
        p.set('page', String(n));
        return p.toString();
    }
    function noFilters() { return !state.status && !state.newOnly && !state.jobs.length && !state.disciplines.length && !state.q && !state.platforms; }

    // Animated tally: roll the result count to its new value (Tally primitive). Skips on reduced-motion.
    function countText(v) { return v + ' contract' + (v === 1 ? '' : 's'); }
    var shownCount = parseInt((countEl && countEl.textContent) || '0', 10) || 0, countRAF = null;
    function setCount(total) {
        if (!countEl) return;
        if (countRAF) { cancelAnimationFrame(countRAF); countRAF = null; }   // supersede any in-flight roll
        if (prefersReduced || total === shownCount || !window.requestAnimationFrame) {
            countEl.textContent = countText(total); shownCount = total; return;
        }
        var from = shownCount, start = null, dur = 420;   // `from` = last displayed value (updated each frame below)
        function step(ts) {
            if (start === null) start = ts;
            var t = Math.min(1, (ts - start) / dur), eased = 1 - Math.pow(1 - t, 3);   // easeOutCubic
            shownCount = Math.round(from + (total - from) * eased);
            countEl.textContent = countText(shownCount);
            if (t < 1) { countRAF = requestAnimationFrame(step); } else { shownCount = total; countRAF = null; }
        }
        countRAF = requestAnimationFrame(step);
    }

    // ---- Faceted chip counts + smart-empty suggestion (read from the #rp-facets JSON island) ----
    function readFacets(scope) {
        var el = (scope || document).querySelector('#rp-facets');
        if (!el) return null;
        try { return JSON.parse(el.textContent); } catch (e) { return null; }
    }
    function setChipN(chip, n) {
        var span = chip.querySelector('.rp-chip__n');
        if (!span) return;
        span.textContent = n; span.hidden = false;
        chip.classList.toggle('rp-chip--empty', n === 0);   // dim a facet that would return nothing
    }
    function setPopN(item, n) {
        var span = item.querySelector('.rp-pop__n');
        if (!span) return;
        span.textContent = n;   // stays in flow (empty until loaded) so its margin-left:auto keeps the check flush-right
        item.classList.toggle('rp-pop__item--empty', n === 0);
    }
    function applyFacets(f) {
        if (!f) return;
        if (f.status && statusBar) statusBar.querySelectorAll('.rp-chip').forEach(function (c) {
            var n = f.status[c.dataset.filter]; setChipN(c, n == null ? 0 : n);
        });
        if (newChip) setChipN(newChip, f.new == null ? 0 : f.new);
        if (f.platform && plats) plats.querySelectorAll('.rp-plat').forEach(function (c) {
            var n = f.platform[c.dataset.plat]; setChipN(c, n == null ? 0 : n);
        });
        if (discs) {                                          // popover job + discipline (head) counts
            if (f.job) discs.querySelectorAll('.rp-pop__item[data-job]').forEach(function (it) {
                var n = f.job[it.dataset.job]; setPopN(it, n == null ? 0 : n);
            });
            if (f.discipline) discs.querySelectorAll('.rp-pop__item--head').forEach(function (it) {
                var n = f.discipline[it.dataset.disc]; setPopN(it, n == null ? 0 : n);
            });
        }
        renderSuggest(f.suggest);
    }
    function renderSuggest(s) {
        if (!relaxBtn) return;
        if (s && s.count > 0) {
            relaxBtn.textContent = 'Drop “' + s.label + '” to see ' + s.count + ' contract' + (s.count === 1 ? '' : 's');
            relaxBtn.dataset.kind = s.kind; relaxBtn.dataset.value = s.value || '';
            relaxBtn.hidden = false;
        } else {
            relaxBtn.hidden = true; relaxBtn.removeAttribute('data-kind'); relaxBtn.removeAttribute('data-value');
        }
    }

    // ---- Shareable filter state in the URL (History API -- no reload, hot-swap untouched) ----
    function syncURL() {
        if (!window.history || !history.replaceState) return;
        var params = new URLSearchParams(location.search);
        // EVERY key buildParams can emit has to be listed here. A key that is written but never
        // cleared survives being switched off (the URL keeps claiming a filter the board has
        // dropped, and seedFromURL turns it back on next reload) and doubles up when switched on
        // again. That is the exact "a filter the controller does not fully know about" failure
        // _filter_contracts' own comment records.
        BOARD_KEYS.forEach(function (k) { params.delete(k); });
        var bp = new URLSearchParams(buildParams(1)); bp.delete('page');
        bp.forEach(function (v, k) { params.append(k, v); });          // keep any non-board params (e.g. view=contracts)
        var qs = params.toString();
        history.replaceState(null, '', location.pathname + (qs ? '?' + qs : '') + location.hash);
    }

    function updateEmptyStates() {
        var any = !!list.querySelector('.rp-row');
        if (noMatch) noMatch.hidden = any || noFilters();
        if (empty) empty.hidden = any ? true : !noFilters();
    }
    // Mirror each toggle chip's is-active state to aria-pressed (disc triggers use aria-expanded instead).
    function syncAria() {
        document.querySelectorAll('.rp-toolbar .rp-chip:not(.rp-disc__trigger)').forEach(function (c) {
            c.setAttribute('aria-pressed', c.classList.contains('is-active') ? 'true' : 'false');
        });
    }
    // boardQuery: the page-1 query of the board on screen, or of the one on its way (null after a failed
    // load, when the grid is still the board from before it). A page-1 fetch for the SAME query is dropped:
    // a re-tapped active chip, a repeated claim-banner jump, Clear filters with nothing set, a repeated
    // radar jump. Each refetched an identical board, aborting any request already in flight.
    // Safe because buildParams encodes every field of `state`, so an equal query asks for the same filters.
    var reqSeq = 0, controller = null, boardQuery = null;
    function fetchPage(n, append) {
        if (!RESULTS_URL) return;
        syncAria();
        if (append && busy) return;                          // don't stack scroll-appends onto an in-flight load
        if (!append) {
            var query = buildParams(1);
            if (query === boardQuery) return;                // already on screen, or on its way
            boardQuery = query;
            syncURL();                                       // reflect the (just-committed) filters in the URL
        }
        if (!append && controller) controller.abort();      // a filter change supersedes any in-flight request
        var myReq = ++reqSeq;                                // any later fetchPage supersedes this response
        controller = ('AbortController' in window) ? new AbortController() : null;
        busy = true; if (loading) loading.hidden = false;
        if (!append && searchField) searchField.setBusy(true);   // search-field spinner on filter/search fetches
        fetch(RESULTS_URL + '?' + buildParams(n), {
            headers: { 'X-Requested-With': 'XMLHttpRequest' }, credentials: 'same-origin',
            signal: controller ? controller.signal : undefined,
        })
            .then(function (r) {
                if (!r.ok) throw 0;
                return r.text().then(function (html) {
                    return { html: html, hasNext: r.headers.get('X-Has-Next') === '1', total: parseInt(r.headers.get('X-Total') || '0', 10) };
                });
            })
            .then(function (d) {
                if (myReq !== reqSeq) return;                // superseded: a newer request now owns busy + the grid
                busy = false; if (loading) loading.hidden = true;
                if (searchField) searchField.setBusy(false);
                hasNext = d.hasNext; setCount(d.total);
                var tmp = document.createElement('div'); tmp.innerHTML = d.html;
                var cards = Array.prototype.slice.call(tmp.querySelectorAll('.rp-row'));
                // DISCONNECT before dropping the cards: the reveal observer holds a strong reference to
                // every card that has not yet scrolled into view, so replacing the list without releasing
                // them retains a detached node per card, per filter change, for the life of the page.
                // `initCards` below re-observes whatever replaces them.
                if (!append) {
                    if (cardRevealer) { cardRevealer.disconnect(); }
                    list.innerHTML = '';
                    applyFacets(readFacets(tmp));   // facets ride the page-1 swap
                }
                cards.forEach(function (c) { list.appendChild(c); });
                pageNo = n;
                initCards(list);
                staggerCards(cards);
                updateEmptyStates();
                observeSentinel();
            })
            .catch(function (e) {
                if ((e && e.name === 'AbortError') || myReq !== reqSeq) return;   // superseded -> the newer request owns state
                busy = false; if (loading) loading.hidden = true;
                if (searchField) searchField.setBusy(false);
                if (!append) {
                    boardQuery = null;                       // the grid is not the board asked for, so the same query may retry
                    // Usually it is still the OLD board (the request failed), and hasNext/pageNo still describe it
                    // while `state` holds the new filters: left observed, the sentinel would append the NEW filters'
                    // next page onto the OLD grid. (Rarely the throw came from rendering a page that did arrive;
                    // stopping is still the safe answer.) Stop scrolling until a page-1 load succeeds and re-arms it.
                    hasNext = false; observeSentinel();
                }
                if (Toast) Toast.error('Could not load Contracts. Please try again.');
            });
    }
    var sentIO = hasIO ? new IntersectionObserver(function (entries) {
        if (entries[0].isIntersecting && hasNext && !busy) fetchPage(pageNo + 1, true);
    }, { rootMargin: '400px' }) : null;
    function observeSentinel() {
        if (!sentIO || !sentinel) return;
        sentIO.unobserve(sentinel);
        if (hasNext) sentIO.observe(sentinel);
    }

    // Filters-toggle badge: count of active advanced filters (each selected job/discipline + non-default platforms).
    function updateAdvCount() {
        var n = state.jobs.length + state.disciplines.length;
        if (state.platforms) n++;
        if (advBadge) { advBadge.textContent = String(n); advBadge.hidden = n === 0; }
    }

    // Status quick-filter (single-select).
    if (statusBar) statusBar.addEventListener('click', function (e) {
        var chip = e.target.closest('.rp-chip'); if (!chip) return;
        statusBar.querySelectorAll('.rp-chip').forEach(function (c) { c.classList.remove('is-active'); });
        chip.classList.add('is-active');
        state.status = chip.dataset.filter || '';
        fetchPage(1, false);
    });

    // Sort control.
    if (sortSel) sortSel.addEventListener('change', function () { state.sort = sortSel.value; fetchPage(1, false); });

    // ---- Board | History scope sub-toggle (segmented switcher; re-fetches the grid like a filter) ----
    var toolbar = document.querySelector('.rp-toolbar');
    var scopeSwitch = document.getElementById('rp-scope-switch');
    var scopeChips = scopeSwitch ? Array.prototype.slice.call(scopeSwitch.querySelectorAll('[data-contract-view]')) : [];
    var SCOPE_DEFAULT_SORT = { board: 'relevance', history: 'banked' };
    function scopeDefaultSort(sc) { return SCOPE_DEFAULT_SORT[sc] || 'relevance'; }
    // Show only the sort options valid for the active scope; if the current pick isn't one of them, fall
    // back to the scope default. `option[hidden]` is honored by modern browsers; `disabled` is belt-and-braces.
    function syncSortOptions(sc) {
        if (!sortSel) return;
        Array.prototype.forEach.call(sortSel.options, function (o) {
            var show = (o.dataset.scope || 'board').split(' ').indexOf(sc) !== -1;
            o.hidden = !show; o.disabled = !show;
        });
        var cur = sortSel.querySelector('option[value="' + state.sort + '"]');
        if (!cur || cur.hidden) { state.sort = scopeDefaultSort(sc); }
        sortSel.value = state.sort;
    }
    function applyScopeUI(sc) {
        if (toolbar) toolbar.dataset.scope = sc;   // CSS hides the status chip row in History
        scopeChips.forEach(function (c) {
            var on = c.dataset.contractView === sc;
            c.classList.toggle('is-active', on);
            c.setAttribute('aria-selected', on ? 'true' : 'false');
        });
        syncSortOptions(sc);
        if (empty) {
            empty.textContent = (sc === 'history')
                ? 'No completed Contracts yet. Bank a Contract on the Board and it lands here.'
                : 'No Contracts on the board yet. Contracts are curated games that level your jobs, check back soon.';
        }
        var allB = document.getElementById('rp-accept-all');   // nothing to claim in History
        if (allB) allB.style.display = (sc === 'history') ? 'none' : '';
    }
    function setScope(sc, userAction) {
        sc = (sc === 'history') ? 'history' : 'board';
        if (userAction && state.scope === sc) return;
        state.scope = sc;
        state.sort = scopeDefaultSort(sc);   // each scope lands on its own default sort
        // status (available/pursuing/claimable) is a Board-only filter and its row is hidden in History,
        // so clear it on a scope switch -- otherwise a stale status could zero out History with no visible
        // chip to undo it. jobs/disciplines/search/platforms are scope-agnostic and carry over.
        state.status = '';
        state.newOnly = false;   // Latest rides the Board row, which History hides -- same reason
        syncNewChip();
        if (statusBar) statusBar.querySelectorAll('.rp-chip').forEach(function (c, i) { c.classList.toggle('is-active', i === 0); });
        applyScopeUI(sc);
        if (userAction) {
            var active = scopeChips.filter(function (c) { return c.dataset.contractView === sc; })[0];
            if (active && PlatPursuit.igniteTab) PlatPursuit.igniteTab(active);
            fetchPage(1, false);
        }
    }
    if (scopeChips.length && PlatPursuit.wireTablist) {
        PlatPursuit.wireTablist(scopeChips, { onSelect: function (c) { setScope(c.dataset.contractView, true); } });
    }

    // Advanced panel toggle (animated height+opacity collapse via the shared helper).
    if (advToggle && advPanel) advToggle.addEventListener('click', function () {
        var open = advPanel.hidden;   // currently hidden -> this click opens it
        advToggle.setAttribute('aria-expanded', open ? 'true' : 'false');
        advToggle.classList.toggle('is-open', open);
        if (window.PlatPursuit && PlatPursuit.animatePanel) { PlatPursuit.animatePanel(advPanel, open, true); }
        else { advPanel.hidden = !open; }
    });

    // Discipline -> job drill-down: multi-select toggles across popovers, ANDed server-side. Selected
    // items become removable tokens; a popover stays open so several can be picked from one list.
    // Popover open/close mechanics are the shared PlatPursuit.discPopovers helper (shared with Browse Games);
    // this page owns only the SELECTION (jobs/disciplines -> tokens -> fetchPage).
    var discPop = (window.PlatPursuit && PlatPursuit.discPopovers) ? PlatPursuit.discPopovers(discs) : { closeAll: function () {} };
    // Reflect the current selection into popover items, trigger chips (active + count), the "All"
    // chip, and the token row.
    function refreshDiscUI() {
        if (!discs) return;
        discs.querySelectorAll('.rp-pop__item[data-job]').forEach(function (it) {
            var on = state.jobs.indexOf(it.dataset.job) !== -1;
            it.classList.toggle('is-selected', on); it.setAttribute('aria-pressed', on ? 'true' : 'false');
        });
        discs.querySelectorAll('.rp-pop__item--head').forEach(function (it) {
            var on = state.disciplines.indexOf(it.dataset.disc) !== -1;
            it.classList.toggle('is-selected', on); it.setAttribute('aria-pressed', on ? 'true' : 'false');
        });
        discs.querySelectorAll('.rp-disc__trigger').forEach(function (t) {
            var pop = t.parentElement.querySelector('.rp-pop');
            var jobsHere = pop ? pop.querySelectorAll('.rp-pop__item[data-job].is-selected').length : 0;
            var total = jobsHere + (state.disciplines.indexOf(t.dataset.disc) !== -1 ? 1 : 0);
            t.classList.toggle('is-active', total > 0);
            var badge = t.querySelector('.rp-disc__count');
            if (badge) { badge.textContent = String(total); badge.hidden = total === 0; }
        });
        var allChip = discs.querySelector('[data-disc="all"]');
        if (allChip) allChip.classList.toggle('is-active', !state.jobs.length && !state.disciplines.length);
        renderTokens();
    }
    function renderTokens() {
        if (!tokensBox) return;
        tokensBox.textContent = '';
        var items = state.disciplines.map(function (d) {
            var head = discs.querySelector('.rp-pop__item--head[data-disc="' + d + '"]');
            return { attr: 'disc', slug: d, name: head ? head.dataset.name : d, disc: d };
        }).concat(state.jobs.map(function (j) {
            var it = discs.querySelector('.rp-pop__item[data-job="' + j + '"]');
            return { attr: 'job', slug: j, name: it ? it.dataset.name : j, disc: it ? it.dataset.disc : '' };
        }));
        items.forEach(function (f) {
            var b = document.createElement('button');
            b.type = 'button'; b.className = 'rp-token';
            b.setAttribute('data-' + f.attr, f.slug);
            if (f.disc) b.style.setProperty('--disc', 'var(--disc-' + f.disc + ')');   // f.disc is a controlled slug
            b.setAttribute('aria-label', 'Remove ' + f.name);
            var label = document.createElement('span'); label.textContent = f.name;
            var x = document.createElement('span'); x.className = 'rp-token__x'; x.setAttribute('aria-hidden', 'true'); x.textContent = '×';
            b.appendChild(label); b.appendChild(x); tokensBox.appendChild(b);
        });
        if (tokensRow) tokensRow.hidden = items.length === 0;
    }
    function toggleFrom(list, slug) {
        var i = list.indexOf(slug);
        if (i === -1) list.push(slug); else list.splice(i, 1);
        refreshDiscUI(); updateAdvCount(); fetchPage(1, false);
    }
    function clearDiscJobs() {
        state.jobs = []; state.disciplines = [];
        refreshDiscUI(); updateAdvCount(); fetchPage(1, false);
    }
    // Selection only -- discPopovers owns the trigger open/close, click-outside, and Escape. The popover
    // stays open on a job/head click (multi-select).
    if (discs) discs.addEventListener('click', function (e) {
        var head = e.target.closest('.rp-pop__item--head');
        if (head) { toggleFrom(state.disciplines, head.dataset.disc); return; }
        var jobItem = e.target.closest('.rp-pop__item[data-job]');
        if (jobItem) { toggleFrom(state.jobs, jobItem.dataset.job); return; }
        if (e.target.closest('[data-disc="all"]')) { clearDiscJobs(); discPop.closeAll(); }
    });

    // Removing a token drops that job/discipline from the AND selection.
    if (tokensBox) tokensBox.addEventListener('click', function (e) {
        var tok = e.target.closest('.rp-token'); if (!tok) return;
        if (tok.dataset.job) toggleFrom(state.jobs, tok.dataset.job);
        else if (tok.dataset.disc) toggleFrom(state.disciplines, tok.dataset.disc);
    });
    var tokensClear = document.getElementById('rp-tokens-clear');
    if (tokensClear) tokensClear.addEventListener('click', clearDiscJobs);

    // Platform selector (multi-select; the default current-gen set sends no param -> server default).
    function platsEqualDefault(arr) {
        return arr.length === DEFAULT_PLATS.length && DEFAULT_PLATS.every(function (p) { return arr.indexOf(p) !== -1; });
    }
    if (plats) plats.addEventListener('click', function (e) {
        var chip = e.target.closest('.rp-plat'); if (!chip) return;
        chip.classList.toggle('is-active');
        var active = Array.prototype.slice.call(plats.querySelectorAll('.rp-plat.is-active')).map(function (c) { return c.dataset.plat; });
        if (!active.length) { chip.classList.add('is-active'); return; }   // never allow an empty selection
        state.platforms = platsEqualDefault(active) ? null : active;
        updateAdvCount(); fetchPage(1, false);
    });

    if (searchInput) {
        var onSearch = function () { state.q = searchInput.value.trim(); fetchPage(1, false); };
        searchInput.addEventListener('input', debounce ? debounce(onSearch, 250) : onSearch);
        // Shared search-field affordances (clear x / Escape-to-clear / has-value + the `/` hint). Clearing
        // re-runs the search; the in-flight spinner is driven by setBusy() in fetchPage. (`/`+Cmd/K is global.)
        if (window.PlatPursuit && PlatPursuit.wireSearchField) {
            searchField = PlatPursuit.wireSearchField(searchInput, { onClear: onSearch });
        }
    }

    // Reset the filters, staying in the current sub-view (Board/History) with its default sort.
    function resetAll() {
        var sc = state.scope;
        state = { status: '', newOnly: false, jobs: [], disciplines: [], q: '', sort: scopeDefaultSort(sc), platforms: null, scope: sc };
        syncNewChip();
        if (searchInput) searchInput.value = '';
        if (searchField) searchField.sync();   // clearing the field -> drop .has-value so the clear-x hides
        if (statusBar) statusBar.querySelectorAll('.rp-chip').forEach(function (c, i) { c.classList.toggle('is-active', i === 0); });
        if (plats) plats.querySelectorAll('.rp-plat').forEach(function (c) { c.classList.toggle('is-active', DEFAULT_PLATS.indexOf(c.dataset.plat) !== -1); });
        applyScopeUI(sc);   // keeps the sort options + sortSel.value in sync with the scope
        refreshDiscUI(); updateAdvCount(); fetchPage(1, false);
    }
    var clearBtn = document.getElementById('rp-clear-filters');
    if (clearBtn) clearBtn.addEventListener('click', resetAll);

    // Smart empty state: drop the single filter the server says frees up the most results.
    if (relaxBtn) relaxBtn.addEventListener('click', function () {
        var kind = relaxBtn.dataset.kind, value = relaxBtn.dataset.value || '';
        if (kind === 'job') { var i = state.jobs.indexOf(value); if (i !== -1) state.jobs.splice(i, 1); refreshDiscUI(); }
        else if (kind === 'discipline') { var j = state.disciplines.indexOf(value); if (j !== -1) state.disciplines.splice(j, 1); refreshDiscUI(); }
        else if (kind === 'status') { state.status = ''; if (statusBar) statusBar.querySelectorAll('.rp-chip').forEach(function (c) { c.classList.toggle('is-active', c.dataset.filter === 'all'); }); }
        else if (kind === 'platform') { state.platforms = VALID_PLATS.slice(); if (plats) plats.querySelectorAll('.rp-plat').forEach(function (c) { c.classList.add('is-active'); }); }   // widen to ALL platforms (matches the server count)
        else if (kind === 'q') { state.q = ''; if (searchInput) searchInput.value = ''; }
        else if (kind === 'new') { state.newOnly = false; syncNewChip(); }
        updateAdvCount(); fetchPage(1, false);
    });

    // Seed the controls from the URL so a shared/reloaded link rebuilds the exact filter state. The
    // cards are already server-rendered for these params, so this only syncs the UI -- no fetch.
    function seedFromURL() {
        var p = new URLSearchParams(location.search);
        state.scope = (p.get('scope') === 'history') ? 'history' : 'board';
        state.status = (state.scope === 'history') ? '' : (p.get('status') || '');   // status is Board-only
        state.q = p.get('q') || '';
        state.sort = p.get('sort') || scopeDefaultSort(state.scope);   // scope-validated by applyScopeUI below
        state.jobs = p.getAll('job');
        state.disciplines = p.getAll('discipline');
        var pl = p.getAll('platform').filter(function (x) { return VALID_PLATS.indexOf(x) !== -1; });
        state.platforms = pl.length ? pl : null;
        if (searchInput) searchInput.value = state.q;
        if (searchField) searchField.sync();   // programmatic value set fires no 'input' event -> sync .has-value (clear-x)
        // Board-only, for the same reason status is (two lines up): CSS hides the Latest chip in
        // History, so `?scope=history&new=1` -- a hand-edited URL, a stale bookmark -- would narrow
        // History to the last 14 days with NO visible control to undo it. setScope already clears
        // it on a user scope switch; this is the load path, which is the one that can arrive there.
        state.newOnly = (state.scope !== 'history') && p.get('new') === '1';
        if (statusBar) statusBar.querySelectorAll('.rp-chip').forEach(function (c) {
            c.classList.toggle('is-active', (state.status || 'all') === c.dataset.filter);
        });
        syncNewChip();
        if (plats) { var act = state.platforms || DEFAULT_PLATS; plats.querySelectorAll('.rp-plat').forEach(function (c) { c.classList.toggle('is-active', act.indexOf(c.dataset.plat) !== -1); }); }
        refreshDiscUI();
        applyScopeUI(state.scope);   // sub-chip active + toolbar data-scope + scope-valid sort options (sets sortSel.value)
    }

    // Initial: seed from the URL + apply the SSR facet counts, then start infinite scroll. No fetch --
    // the server already rendered page 1 for these exact params (shareable/reload-safe, no flash).
    seedFromURL();
    boardQuery = buildParams(1);   // the SSR board, so re-asking for it on load is a no-op too
    // The Contracts tab's badge counts claimables on EVERY platform, but the default board shows
    // current-gen with no status filter, so a tap could land on fewer than the badge promised. The FIRST
    // time the tab is opened in a visit, onto that untouched default board, it lands on Ready to Claim
    // (every platform) instead, the banner's jump. Armed only when the URL carried no board filter (a
    // deep link or shared filter is never overridden), and spent on the first entry to Contracts however
    // it happens, landing or not, so a later return never swaps out a board the hunter has been using.
    var landingQuery = BOARD_KEYS.some(function (k) { return new URLSearchParams(location.search).has(k); })
        ? null : boardQuery;
    applyFacets(readFacets(document));
    updateAdvCount();
    syncAria();
    updateEmptyStates();
    initCards(list);
    observeSentinel();
    var panel = list.closest('.lab-view');
    if (panel && !panel.hidden) {
        staggerCards(Array.prototype.slice.call(list.querySelectorAll('.rp-row')));  // Contracts is the landing view
        landingQuery = null;   // the hunter has already seen this board, so the claim landing is spent
    }
    document.addEventListener('pp:contracts-shown', function () {
        syncURL();   // returning to Contracts: re-add its (still-set) filters to the URL that showView scoped away
        staggerCards(Array.prototype.slice.call(list.querySelectorAll('.rp-row')));
        // The one-time claim landing (see `landingQuery`). `boardQuery === landingQuery` means the board
        // is still the untouched default: the radar jump changes it BEFORE clicking the tab, so it is
        // never hijacked, and the banner's own jump asks for the same board, so the dedupe drops it.
        var tab = document.getElementById('career-tab-contracts');
        var landing = landingQuery;
        landingQuery = null;   // spent on this first entry, landing or not
        if (landing && boardQuery === landing && tab && parseInt(tab.dataset.claimable || '0', 10) > 0) {
            document.dispatchEvent(new CustomEvent('pp:board-filter', { detail: { status: 'claimable', allPlatforms: true } }));
        }
    });

    // Jumps to the Board: a radar point / detail bar (radar IIFE) or the claim banner dispatches
    // pp:board-filter to open the BOARD scoped to a single discipline, job or status, REPLACING any current
    // filters and any History scope. `allPlatforms` lights every platform rather than the current-gen
    // default: the claim banner counts claimables regardless of platform, so the default hid the legacy and
    // VR ones it promised. (Only a contract whose member games ALL have no platform tags still misses every
    // platform filter; see the claim-banner gotcha in job-board-contracts.md.) The view switch itself is the
    // dispatcher's job; this just sets the board state + refetches.
    document.addEventListener('pp:board-filter', function (e) {
        var f = (e && e.detail) || {};
        state.scope = 'board';
        state.status = f.status || ''; state.q = ''; state.newOnly = false; syncNewChip();
        state.jobs = f.job ? [f.job] : [];
        state.disciplines = f.discipline ? [f.discipline] : [];
        state.platforms = f.allPlatforms ? VALID_PLATS.slice() : null;
        state.sort = scopeDefaultSort('board');
        if (searchInput) searchInput.value = '';
        if (searchField) searchField.sync();
        if (statusBar) statusBar.querySelectorAll('.rp-chip').forEach(function (c) { c.classList.toggle('is-active', (state.status || 'all') === c.dataset.filter); });
        var lit = state.platforms || DEFAULT_PLATS;
        if (plats) plats.querySelectorAll('.rp-plat').forEach(function (c) { c.classList.toggle('is-active', lit.indexOf(c.dataset.plat) !== -1); });
        applyScopeUI('board');   // sub-toggle + sort options + status row
        refreshDiscUI();         // render the disc/job token so the active filter is visible when Filters opens
        updateAdvCount();
        fetchPage(1, false);
    });

    // Deep-linked to a FILTERED contracts board (e.g. the job modal's "View in History" jump): the page
    // loads at the top (hero), so the board sits below the fold and you land stranded above it. When the
    // URL carries a board filter, scroll the tab strip into view so the board is on screen. A plain
    // ?view=contracts (no filter) still opens at the top. Runs after DOMContentLoaded so main.js has
    // published --sticky-top (the scroll-padding-top offset), landing the strip below the sticky chrome.
    function scrollToFilteredBoard() {
        var qp = new URLSearchParams(location.search);
        if (qp.get('view') !== 'contracts') return;
        // `new` belongs in this list more than any other key: it is the one filter that arrives
        // from an EXTERNAL link (the Discord announcement), where the reader has no idea the board
        // is already filtered unless we take them to it.
        if (!(qp.get('scope') === 'history' || qp.has('job') || qp.has('discipline') || qp.has('status') || qp.has('q') || qp.has('platform') || qp.has('new'))) return;
        var strip = document.querySelector('.pp-switch');
        if (strip) { strip.scrollIntoView({ behavior: prefersReduced ? 'auto' : 'smooth', block: 'start' }); }
    }
    if (document.readyState === 'loading') {
        window.addEventListener('DOMContentLoaded', function () { requestAnimationFrame(scrollToFilteredBoard); });
    } else {
        requestAnimationFrame(scrollToFilteredBoard);
    }
})();

// Sticky mini-bar: init on DOMContentLoaded so main.js has already published --sticky-top (chrome height).
// The bar's tab duplicates are wired by the view-toggle above (it captured every .pp-switch__chip at init).
document.addEventListener('DOMContentLoaded', function () {
    if (window.PlatPursuit && PlatPursuit.StickyReveal) { PlatPursuit.StickyReveal.init(); }
});
