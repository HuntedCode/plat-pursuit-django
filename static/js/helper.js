document.addEventListener('DOMContentLoaded', function() {
    const trophiesGrid = document.getElementById('trophies-grid');
    const loading = document.getElementById('loading');
    const trophiesSentinel = document.getElementById('trophies-sentinel');
    if (!trophiesGrid || !loading || !trophiesSentinel) return;

    let trophyPage = 2;
    const baseUrl = window.location.pathname;
    const queryParams = new URLSearchParams(window.location.search);
    queryParams.delete('page');
    queryParams.set('content_type', 'trophies')
    let nextTrophyPageUrl  = `${baseUrl}?page=${trophyPage}&${queryParams.toString()}`;
    let isTrophyLoading = false;

    const loadMoreTrophies = async () => {
        if (!nextTrophyPageUrl || isTrophyLoading) return;
        isTrophyLoading = true;
        loading.classList.remove('hidden')

        try {
            const response = await fetch(nextTrophyPageUrl, {
                headers: {'X-Requested-With': 'XMLHttpRequest' }
            });
            const html = await response.text();
            const parser = new DOMParser();
            const doc = parser.parseFromString(html, 'text/html');
            const newCards = doc.querySelectorAll('.card');
            if (newCards.length === 0) {
                nextTrophyPageUrl = null;
            } else {
                newCards.forEach(card => trophiesGrid.appendChild(card.cloneNode(true)));
                trophyPage++;
                nextTrophyPageUrl = `${baseUrl}?page=${trophyPage}&${queryParams.toString()}`;
            }
        } catch (error) {
            console.error('Error loading more items:', error);
        } finally {
            isTrophyLoading = false;
            loading.classList.add('hidden');
        }
    };

    trophyForm = document.getElementById('trophy-form');
    if (trophyForm) {
        trophyForm.addEventListener('submit', () => {
            trophyPage = 2;
            nextTrophyPageUrl = `${baseUrl}?page=${trophyPage}&${queryParams.toString()}`;
            trophiesGrid.innerHTML = '';
        });
    }

    const trophyObserver = new IntersectionObserver(entries => {
        if (entries[0].isIntersecting) {
            loadMoreTrophies();
        }
    }, { threshold: 1.0 });

    if (trophiesGrid.children.length >= 50) {
        trophyObserver.observe(trophiesSentinel);
    }
});