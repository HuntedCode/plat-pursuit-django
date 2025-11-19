document.addEventListener('DOMContentLoaded', function() {
    const viewToggle = document.getElementById('view-toggle');
    if (!viewToggle) return;
    viewToggle.addEventListener('click', function() {
        const currentView = viewToggle.dataset.viewType == 'list' ? 'grid' : 'list';
        const queryParams = new URLSearchParams(window.location.search);
        queryParams.set('view', currentView);
        window.location.search = queryParams.toString();
    });
});

document.addEventListener('DOMContentLoaded', function() {
    const platinumButton = document.getElementById('platinum-toggle');
    if (!platinumButton) return;
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
});

document.addEventListener('DOMContentLoaded', function() {
    const shovelwareButton = document.getElementById('shovelware-toggle');
    if (!shovelwareButton) return;
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
});