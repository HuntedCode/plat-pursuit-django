document.addEventListener('DOMContentLoaded', function() {
    // View toggle (grid/list)
    const viewToggle = document.getElementById('view-toggle');
    if (viewToggle) {
        viewToggle.addEventListener('click', function() {
            const currentView = viewToggle.dataset.viewType === 'list' ? 'grid' : 'list';
            const queryParams = new URLSearchParams(window.location.search);
            queryParams.set('view', currentView);
            window.location.search = queryParams.toString();
        });
    }

    // Platinum filter toggle
    const platinumButton = document.getElementById('platinum-toggle');
    if (platinumButton) {
        platinumButton.addEventListener('click', function() {
            const currentState = platinumButton.dataset.showOnlyPlatinum ? '' : 'on';
            const queryParams = new URLSearchParams(window.location.search);
            if (currentState) {
                queryParams.set('show_only_platinum', currentState);
            } else {
                queryParams.delete('show_only_platinum');
            }
            window.location.search = queryParams.toString();
        });
    }

    // Shovelware filter toggle
    const shovelwareButton = document.getElementById('shovelware-toggle');
    if (shovelwareButton) {
        shovelwareButton.addEventListener('click', function() {
            const currentState = shovelwareButton.dataset.filterShovelware ? '' : 'on';
            const queryParams = new URLSearchParams(window.location.search);
            if (currentState) {
                queryParams.set('filter_shovelware', currentState);
            } else {
                queryParams.delete('filter_shovelware');
            }
            window.location.search = queryParams.toString();
        });
    }

    // Page-jump forms (handles multiple forms on page via class selector)
    document.querySelectorAll('.page-jump-form').forEach(function(form) {
        form.addEventListener('submit', function(e) {
            e.preventDefault();
            const newPage = form.querySelector('input[name="page"]').value;
            const anchor = form.dataset.anchor;
            const currentParams = new URLSearchParams(window.location.search);
            currentParams.set('page', newPage);
            const newUrl = window.location.pathname + '?' + currentParams.toString() + (anchor ? '#' + anchor : '');
            window.location.href = newUrl;
        });
    });
});
