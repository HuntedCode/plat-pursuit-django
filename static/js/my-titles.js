/*
 * My Titles page. Each source section (Badge / Milestone / Special) has its own
 * Have/Need filter + sort; equipping is global. Deliberately styling-free: the JS only
 * toggles semantic state classes (.is-equipped, .is-active, .is-hidden, .tt-flash, the
 * .tt-none note's hidden attr) and sets text content. All visuals live in titles.css.
 */
(function () {
    'use strict';

    var API = window.PlatPursuit.API;
    var Toast = window.PlatPursuit.ToastManager;

    var SORTERS = {
        name: function (a, b) { return (a.dataset.titleName || '').localeCompare(b.dataset.titleName || ''); },
        recent: function (a, b) { return (b.dataset.earnedAt || '').localeCompare(a.dataset.earnedAt || ''); },
        // "Closest to earning": earned ones are done, so they sink to the bottom;
        // among the unearned, the nearest (highest progress) rises to the top.
        closest: function (a, b) {
            var ah = a.dataset.state === 'have', bh = b.dataset.state === 'have';
            if (ah !== bh) return ah ? 1 : -1;
            return (parseFloat(b.dataset.progress) || 0) - (parseFloat(a.dataset.progress) || 0);
        }
    };

    var MyTitlesPage = {
        _sections: [],
        _hero: null,
        _heroName: null,
        _heroFlavor: null,
        _sparkleCount: 0,
        _sparkleTimer: null,

        init: function () {
            var self = this;
            this._hero = document.getElementById('tt-hero');
            this._heroName = document.getElementById('tt-hero-title');
            this._heroFlavor = this._hero ? this._hero.querySelector('[data-flavor]') : null;

            document.querySelectorAll('[data-tt-section]').forEach(function (el) {
                var sort = el.querySelector('.tt-sort');
                var s = {
                    name: el.dataset.ttSection,
                    el: el,
                    container: el.querySelector('[data-tt-items]'),
                    seg: el.querySelector('.tt-seg'),
                    sort: sort,
                    none: el.querySelector('.tt-none'),
                    filter: 'all',
                    sortBy: sort ? sort.value : 'name'
                };
                self._sections.push(s);
                self._restore(s);
                self._wireSection(s);
                self._apply(s);
            });

            this._wireGlobal();
        },

        // ── Section filter / sort ───────────────────────────────────────────
        _wireSection: function (s) {
            var self = this;
            if (s.seg) {
                s.seg.addEventListener('click', function (e) {
                    var btn = e.target.closest('[data-filter]');
                    if (!btn) return;
                    s.filter = btn.dataset.filter;
                    s.seg.querySelectorAll('[data-filter]').forEach(function (b) { b.classList.toggle('is-active', b === btn); });
                    self._apply(s); self._save(s);
                });
            }
            if (s.sort) {
                s.sort.addEventListener('change', function () { s.sortBy = s.sort.value; self._apply(s); self._save(s); });
            }
        },

        _items: function (s) {
            return s.container ? Array.prototype.slice.call(s.container.querySelectorAll(':scope > [data-title-id]')) : [];
        },

        _apply: function (s) {
            if (!s.container) return;
            var items = this._items(s), visible = 0;
            items.forEach(function (it) {
                var show = s.filter === 'all' || it.dataset.state === s.filter;
                it.classList.toggle('is-hidden', !show);
                if (show) visible++;
            });
            var sorter = SORTERS[s.sortBy] || SORTERS.name;
            items.sort(sorter).forEach(function (it) { s.container.appendChild(it); });
            if (s.none) s.none.hidden = visible !== 0;
        },

        // ── Equip / unequip (global) ────────────────────────────────────────
        _wireGlobal: function () {
            var self = this;
            this._sections.forEach(function (s) {
                if (!s.container) return;
                s.container.addEventListener('click', function (e) {
                    if (e.target.closest('a')) return;             // "Earn <badge>" link navigates
                    var el = e.target.closest('[data-title-id]');
                    if (!el || el.dataset.state !== 'have' || el.classList.contains('is-equipped')) return;
                    self._equip(parseInt(el.dataset.titleId, 10));
                });
            });
            if (this._hero) {
                this._hero.addEventListener('click', function (e) {
                    if (e.target.closest('[data-action="unequip"]')) self._unequip();
                    else if (e.target.closest('[data-sparkle]')) self._sparkle();
                });
            }
        },

        _equip: function (titleId) {
            if (!titleId) return;
            var self = this;
            API.post('/api/v1/equip-title/', { title_id: titleId }).then(function (data) {
                if (!data || !data.success) return;
                self._markEquipped(titleId);
                self._setHero(titleId, data.title_name);
                self._flash(titleId);
                if (Toast) Toast.show('Title equipped: ' + data.title_name, 'success');
            }).catch(function (err) { self._error(err, 'Failed to equip title.'); });
        },

        _unequip: function () {
            var self = this;
            API.post('/api/v1/equip-title/', { title_id: null }).then(function (data) {
                if (!data || !data.success) return;
                self._markEquipped(null);
                self._setHero(null, null);
                if (Toast) Toast.show('Title removed.', 'success');
            }).catch(function (err) { self._error(err, 'Failed to remove title.'); });
        },

        _markEquipped: function (titleId) {
            document.querySelectorAll('[data-title-id]').forEach(function (el) {
                el.classList.toggle('is-equipped', titleId !== null && parseInt(el.dataset.titleId, 10) === titleId);
            });
        },

        _setHero: function (titleId, name) {
            if (!this._hero) return;
            var on = titleId !== null && name;
            this._hero.classList.toggle('is-equipped', !!on);
            if (this._heroName) {
                this._heroName.textContent = on ? name : 'No title equipped';
                this._heroName.classList.toggle('is-empty', !on);
            }
            if (this._heroFlavor) {
                this._heroFlavor.textContent = on ? 'Your legend, your identity.' : 'Every legend needs a name. Equip one below.';
            }
        },

        _flash: function (titleId) {
            var el = document.querySelector('[data-title-id="' + titleId + '"]');
            if (!el) return;
            el.classList.add('tt-flash');
            el.addEventListener('animationend', function h() { el.classList.remove('tt-flash'); el.removeEventListener('animationend', h); });
        },

        _error: function (err, fallback) {
            if (!Toast) return;
            if (err && err.response) {
                err.response.json().then(function (d) { Toast.show((d && d.error) || fallback, 'error'); }).catch(function () { Toast.show(fallback, 'error'); });
            } else { Toast.show(fallback, 'error'); }
        },

        // ── State persistence ───────────────────────────────────────────────
        _save: function (s) {
            try {
                sessionStorage.setItem('titles_' + s.name + '_filter', s.filter);
                sessionStorage.setItem('titles_' + s.name + '_sort', s.sortBy);
            } catch (e) { /* ignore */ }
        },

        _restore: function (s) {
            try {
                var f = sessionStorage.getItem('titles_' + s.name + '_filter');
                var so = sessionStorage.getItem('titles_' + s.name + '_sort');
                if (s.seg && ['all', 'have', 'need'].indexOf(f) !== -1) {
                    s.filter = f;
                    s.seg.querySelectorAll('[data-filter]').forEach(function (b) { b.classList.toggle('is-active', b.dataset.filter === f); });
                }
                if (s.sort && so) {
                    var ok = Array.prototype.some.call(s.sort.options, function (o) { return o.value === so; });
                    if (ok) { s.sortBy = so; s.sort.value = so; }
                }
            } catch (e) { /* ignore */ }
        },

        // ── Easter egg: rapid sparkle clicks ────────────────────────────────
        _sparkle: function () {
            var self = this;
            this._sparkleCount++;
            clearTimeout(this._sparkleTimer);
            if (this._sparkleCount >= 5) {
                this._sparkleCount = 0;
                if (Toast) Toast.show('You found a secret! ...but titles are earned, not clicked into existence.', 'info');
                return;
            }
            this._sparkleTimer = setTimeout(function () { self._sparkleCount = 0; }, 2000);
        }
    };

    window.PlatPursuit.MyTitlesPage = MyTitlesPage;
})();
