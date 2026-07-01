/* Home premium motion -- count-up on the headline numbers as the page settles in.
 *
 * The entrance stagger itself is pure CSS (.home-reveal in home.css); this file handles only the
 * count-ups, which need JS. The reveal holds each section at opacity 0 while its numbers reset,
 * so there's no flicker of the final value. Reduced-motion: skipped entirely -- numbers show their
 * final (server-rendered) value. Loaded on the home only, via base.html's js_scripts block.
 */
(function () {
    'use strict';
    if (window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches) return;

    // Roll a number element from 0 up to its rendered integer value (cubic ease-out). Re-formats
    // with toLocaleString so grouped values (1,234) match the server's intcomma rendering.
    function tickUp(el, dur) {
        var target = parseInt((el.textContent || '').replace(/[^\d]/g, ''), 10);
        if (!(target > 1)) return;
        el.textContent = '0';
        var t0 = null;
        function step(ts) {
            if (t0 === null) t0 = ts;
            var p = Math.min(1, (ts - t0) / dur), e = 1 - Math.pow(1 - p, 3);
            el.textContent = Math.round(target * e).toLocaleString();
            if (p < 1) requestAnimationFrame(step); else el.textContent = target.toLocaleString();
        }
        requestAnimationFrame(step);
    }

    function run() {
        var nums = document.querySelectorAll('[data-countup]');
        for (var i = 0; i < nums.length; i++) tickUp(nums[i], 900);
    }

    if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', run);
    else run();
})();
