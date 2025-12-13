document.addEventListener('DOMContentLoaded', function() {
    const grid = document.getElementById('items-grid');
    const loading = document.getElementById('loading');
    const sentinel = document.getElementById('sentinel');
    if (!grid || !loading || !sentinel) return;

    let page = 2;
    const baseUrl = window.location.pathname;
    const queryParams = new URLSearchParams(window.location.search);
    queryParams.delete('page');
    let nextPageUrl  = `${baseUrl}?page=${page}&${queryParams.toString()}`;
    let isLoading = false;

    const loadMore = async () => {
        if (!nextPageUrl || isLoading) return;
        isLoading = true;
        loading.classList.remove('hidden')

        try {
            const response = await fetch(nextPageUrl, {
                headers: {'X-Requested-With': 'XMLHttpRequest' }
            });
            const html = await response.text();
            const parser = new DOMParser();
            const doc = parser.parseFromString(html, 'text/html');
            const newCards = doc.querySelectorAll('.card');
            if (newCards.length === 0) {
                nextPageUrl = null;
            } else {
                newCards.forEach(card => grid.appendChild(card.cloneNode(true)));
                page++;
                nextPageUrl = `${baseUrl}?page=${page}&${queryParams.toString()}`;
            }
        } catch (error) {
            console.error('Error loading more items:', error);
        } finally {
            isLoading = false;
            loading.classList.add('hidden');
        }
    };

    const scrollKey = 'profileScrollPos';
    form = document.getElementById('filter-form');
    if (form) {
        form.addEventListener('submit', () => {
            localStorage.setItem(scrollKey, window.scrollY);
            page = 2;
            nextPageUrl = `${baseUrl}?page=${page}&${queryParams.toString()}`;
        });
    }

    const savedScroll = localStorage.getItem(scrollKey);
    if (savedScroll) {
        window.scrollTo({
            top: parseInt(savedScroll),
            behavior: 'smooth'
        });
        localStorage.removeItem(scrollKey);
    }

    document.querySelector('form').addEventListener('submit', () => {
        page = 2;
        nextPageUrl = `${baseUrl}?page=${page}&${queryParams.toString()}`;
        grid.innerHTML = '';
    });

    const observer = new IntersectionObserver(entries => {
        if (entries[0].isIntersecting) {
            loadMore();
        }
    }, { threshold: 1.0 });

    if (grid.children.length >= 6) {
        observer.observe(sentinel);
    }
});