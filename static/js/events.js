document.addEventListener('DOMContentLoaded', () => {
    const container = document.getElementById('events-container');
    const pagination = document.getElementById('pagination');

    // Get data from data attributes
    const events = JSON.parse(container.dataset.events || '[]');
    const pageSize = parseInt(container.dataset.pageSize || '5', 10);
    const totalPages = Math.ceil(events.length / pageSize);
    let currentPage = 1;

    const renderPage = (page) => {
        container.innerHTML = '';
        const start = (page - 1) * pageSize;
        const end = start + pageSize;
        events.slice(start, end).forEach(event => {
            const card = document.createElement('div');
            card.className = `card bg-base-100 shadow-md shadow-neutral border-2 border-base-300 transition duration-300 hover:shadow-lg hover:shadow-${event.color || 'primary'} group w-full cursor-pointer`;
            card.setAttribute('onclick', `document.location="${event.slug}"`);
            card.innerHTML = `
                <div class="card-body flex flex-row items-center gap-4 p-3 overflow-hidden w-full">
                    <div class="flex-shrink-0 mr-3">
                        <div class="badge badge-${event.color || 'neutral'} badge-lg border-2 border-base-300 flex flex-col items-center p-3 min-h-16 justify-center">
                            <span class="text-xs font-bold">${new Date(event.date).toLocaleString('default', { month: 'short' })}</span>
                            <span class="text-lg font-bold">${new Date(event.date).getDate() + 1}</span>
                        </div>
                    </div>

                    <div class="space-y-1 overflow-hidden text-start">
                        <h4 class="card-title text-sm sm:text-base w-full line-clamp-2">${event.title}</h4>
                        <p class="text-xs text-base-content/70 line-clamp-1">${event.time || 'All Day'}</p>
                        <div class="overflow-hidden max-h-0 group-hover:max-h-32 transition-max-height duration-300 ease-in-out">
                            <p class="text-sm mt-2 line-clamp-2">${event.description || 'No details available.'}</p>
                        </div>
                    </div>
                </div>
            `;
            container.appendChild(card);
        });
    };

    const renderPagination = () => {
        pagination.innerHTML = '';
        const prev = document.createElement('button');
        prev.className = 'join-item btn btn-sm' + (currentPage === 1 ? ' btn-disabled' : '');
        prev.textContent = '«';
        prev.setAttribute('aria-label', 'Previous page');
        prev.onclick = () => { if (currentPage > 1) { currentPage--; renderPage(currentPage); renderPagination(); } };
        pagination.appendChild(prev);

        for (let i = 1; i <= totalPages; i++) {
            const btn = document.createElement('button');
            btn.className = 'join-item btn btn-sm' + (i === currentPage ? ' btn-active' : '');
            btn.textContent = i;
            btn.setAttribute('aria-label', `Page ${i}`);
            btn.setAttribute('aria-current', i === currentPage ? 'page' : 'false');
            btn.onclick = () => { currentPage = i; renderPage(i); renderPagination(); };
            pagination.appendChild(btn);
        }

        const next = document.createElement('button');
        next.className = 'join-item btn btn-sm' + (currentPage === totalPages ? ' btn-disabled' : '');
        next.textContent = '»';
        next.setAttribute('aria-label', 'Next page');
        next.onclick = () => { if (currentPage < totalPages) { currentPage++; renderPage(currentPage); renderPagination(); } };
        pagination.appendChild(next);
    };

    if (events.length > 0) {
        renderPage(1);
        renderPagination();
    } else {
        container.innerHTML = '<p class="text-center text-base-content/70">No upcoming events.</p>';
    }
});
