document.addEventListener('DOMContentLoaded', function() {
    const radioButtons = document.querySelectorAll('input[name="profile-tabs"]')
    const currentTab = new URLSearchParams(window.location.search).get('tab') || 'games';
    const sentinel = document.getElementById(`${currentTab}-sentinel`);
    const loading = document.getElementById(`${currentTab}-loading`);
    const grid = document.getElementById(`${currentTab}-grid`);
    if (!radioButtons || !loading || !sentinel || !grid) return;

    let page = 2;
    const baseUrl = window.location.pathname;
    const queryParams = new URLSearchParams(window.location.search);
    queryParams.delete('page');
    let nextPageUrl  = `${baseUrl}?page=${page}&${queryParams.toString()}`;
    let isLoading = false;

    radioButtons.forEach(radio => {
        radio.addEventListener('change', () => {
            const selectedTab = radio.value;
            const newUrl = `${baseUrl}?tab=${selectedTab}`;
            window.location.href = newUrl;
        });
    });

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
    form = document.getElementById(`${currentTab}-form`);
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

    const observer = new IntersectionObserver(entries => {
        if (entries[0].isIntersecting) {
            loadMore();
        }
    }, { threshold: 1.0 });

    if (grid.children.length >= 50) {
        observer.observe(sentinel);
    }
});