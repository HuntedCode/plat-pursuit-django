/**
 * Game Players Modal
 * Lazily fetches and displays players for a game with filtering, sorting, and pagination.
 * Each stat card on the game detail page opens this modal with a relevant preset.
 */
(function() {
    'use strict';

    const PRESETS = {
        total:     { title: 'All Players',          sort: 'progress', filters: {} },
        monthly:   { title: 'Monthly Players',      sort: 'recent',   filters: { monthly: true } },
        trophies:  { title: 'All Players',           sort: 'progress', filters: {} },
        plats:     { title: 'Platinum Earners',      sort: 'recent',   filters: { has_plat: true } },
        completes: { title: '100% Completionists',   sort: 'recent',   filters: { min_progress: 100 } },
        avg:       { title: 'All Players',           sort: 'progress', filters: {} },
    };

    const PAGE_SIZE = 20;
    let _npId = null;
    let _abort = null;
    let _currentPreset = null;
    let _offset = 0;
    let _totalCount = 0;
    let _bound = false;

    // DOM refs (cached on first open)
    let _modal, _title, _list, _loading, _empty, _loadMoreWrap, _loadMoreBtn;
    let _searchInput, _sortSelect, _platToggle, _minProgress, _maxProgress, _countEl;

    function _cacheDOM() {
        _modal = document.getElementById('game-players-modal');
        _title = document.getElementById('gp-title');
        _list = document.getElementById('gp-list');
        _loading = document.getElementById('gp-loading');
        _empty = document.getElementById('gp-empty');
        _loadMoreWrap = document.getElementById('gp-load-more-wrap');
        _loadMoreBtn = document.getElementById('gp-load-more');
        _searchInput = document.getElementById('gp-search');
        _sortSelect = document.getElementById('gp-sort');
        _platToggle = document.getElementById('gp-plat-toggle');
        _minProgress = document.getElementById('gp-min-progress');
        _maxProgress = document.getElementById('gp-max-progress');
        _countEl = document.getElementById('gp-count');
        _npId = _modal ? _modal.dataset.npId : null;
    }

    function _bindEvents() {
        if (_bound) return;
        _bound = true;

        // Debounced search
        _searchInput.addEventListener('input', PlatPursuit.debounce(function() { _resetAndFetch(); }, 300));

        // Sort change
        _sortSelect.addEventListener('change', function() { _resetAndFetch(); });

        // Platinum toggle
        _platToggle.addEventListener('change', function() { _resetAndFetch(); });

        // Progress range inputs (debounced)
        var debouncedProgress = PlatPursuit.debounce(function() { _resetAndFetch(); }, 500);
        _minProgress.addEventListener('input', debouncedProgress);
        _maxProgress.addEventListener('input', debouncedProgress);

        // Load More button
        _loadMoreBtn.addEventListener('click', function() { _fetchPlayers(false); });
    }

    function _buildURL() {
        var params = new URLSearchParams();
        params.set('sort', _sortSelect.value);
        params.set('limit', PAGE_SIZE);
        params.set('offset', _offset);

        var search = _searchInput.value.trim();
        if (search) params.set('search', search);

        if (_platToggle.checked) params.set('has_plat', 'true');

        var minP = parseInt(_minProgress.value, 10);
        var maxP = parseInt(_maxProgress.value, 10);
        if (!isNaN(minP) && minP > 0) params.set('min_progress', minP);
        if (!isNaN(maxP) && maxP < 100) params.set('max_progress', maxP);

        // Preset-driven monthly filter
        if (_currentPreset && PRESETS[_currentPreset] && PRESETS[_currentPreset].filters.monthly) {
            params.set('monthly', 'true');
        }

        return '/api/v1/games/' + encodeURIComponent(_npId) + '/players/?' + params.toString();
    }

    async function _fetchPlayers(isReset) {
        if (!_npId) return;

        // Abort previous in-flight request
        if (_abort) _abort.abort();
        _abort = new AbortController();

        if (isReset) {
            _offset = 0;
            _list.innerHTML = '';
        }

        _loading.classList.remove('hidden');
        _empty.classList.add('hidden');
        _loadMoreWrap.classList.add('hidden');

        try {
            var data = await PlatPursuit.API.get(_buildURL(), { signal: _abort.signal });

            _totalCount = data.count;
            _countEl.textContent = _totalCount.toLocaleString();

            if (data.players.length === 0 && isReset) {
                _empty.classList.remove('hidden');
                return;
            }

            _offset += data.players.length;

            // Build rows
            var fragment = document.createDocumentFragment();
            for (var i = 0; i < data.players.length; i++) {
                fragment.appendChild(_renderRow(data.players[i]));
            }
            _list.appendChild(fragment);

            // Show "Load More" if more results exist
            if (_offset < _totalCount) {
                _loadMoreWrap.classList.remove('hidden');
            }

        } catch (error) {
            if (error.name === 'AbortError') return;
            var msg = 'Failed to load players.';
            try {
                var errData = await error.response?.json();
                msg = errData?.error || msg;
            } catch (_e) { /* ignore parse error */ }
            PlatPursuit.ToastManager.error(msg);
        } finally {
            _loading.classList.add('hidden');
        }
    }

    function _renderRow(player) {
        var e = PlatPursuit.HTMLUtils.escape;
        var row = document.createElement('a');
        row.href = player.profile_url;
        row.className = 'flex items-center gap-3 p-2 rounded-lg hover:bg-base-100 transition-colors';

        var ringClass = player.is_premium ? 'ring-warning' : 'ring-primary';
        var nameClass = player.is_premium ? 'legendary-title' : '';
        var platBadge = player.has_plat
            ? '<span class="badge badge-xs badge-primary ml-1" title="Platinum earned">P</span>'
            : '';

        // Format date
        var dateStr = '';
        if (player.most_recent_trophy_date) {
            var d = new Date(player.most_recent_trophy_date);
            dateStr = d.toLocaleDateString(undefined, { year: 'numeric', month: 'short', day: 'numeric' });
        }

        var progressColor = player.progress === 100 ? 'text-success' : 'text-base-content';

        row.innerHTML =
            '<div class="avatar flex-shrink-0">' +
                '<div class="w-8 h-8 rounded-full ring-2 ' + ringClass + '">' +
                    '<img src="' + e(player.avatar_url) + '" alt="" loading="lazy" decoding="async" class="object-cover bg-secondary/50" />' +
                '</div>' +
            '</div>' +
            '<div class="flex-1 min-w-0">' +
                '<div class="flex items-center gap-1">' +
                    '<span class="' + nameClass + ' text-sm font-medium line-clamp-1 pr-1 italic">' + e(player.display_psn_username) + '</span>' +
                    (player.flag ? '<span class="text-sm shrink-0">' + e(player.flag) + '</span>' : '') +
                    platBadge +
                '</div>' +
                (dateStr ? '<span class="text-xs text-base-content/50">' + e(dateStr) + '</span>' : '') +
            '</div>' +
            '<div class="text-right flex-shrink-0">' +
                '<div class="text-sm font-bold ' + progressColor + '">' + player.progress + '%</div>' +
                '<div class="text-xs text-base-content/50">' + player.earned_trophies_count + ' trophies</div>' +
            '</div>';

        return row;
    }

    function _resetAndFetch() {
        _fetchPlayers(true);
    }

    function _applyPreset(preset) {
        _currentPreset = preset;
        var config = PRESETS[preset] || PRESETS.total;

        _title.textContent = config.title;
        _sortSelect.value = config.sort;
        _searchInput.value = '';
        _platToggle.checked = !!config.filters.has_plat;
        _minProgress.value = config.filters.min_progress != null ? config.filters.min_progress : 0;
        _maxProgress.value = config.filters.max_progress != null ? config.filters.max_progress : 100;
    }

    /**
     * Open the modal with a preset configuration.
     * @param {string} preset - One of: total, monthly, trophies, plats, completes, avg
     */
    function open(preset) {
        if (!_modal) {
            _cacheDOM();
            if (!_modal) return;
            _bindEvents();
        }

        _applyPreset(preset);
        _modal.showModal();
        _fetchPlayers(true);
    }

    window.GamePlayersModal = { open: open };
})();
