const tableBody = document.getElementById('stats-table');
const chartCanvas = document.getElementById('calls-chart');
let chart;

function updateStats(statsData) {
    tableBody.innerHTML = '';
    let callsData = [];
    let labels = [];
    if (!statsData.machines) return;
    Object.entries(statsData.machines).forEach(([machineId, instances]) => {
        Object.entries(instances).forEach(([id, stat]) => {
            const row = tableBody.insertRow();
            row.insertCell(0).textContent = machineId;
            row.insertCell(1).textContent = id;
            row.insertCell(2).textContent = stat.busy ? 'Yes' : 'No';
            row.insertCell(3).textContent = stat.healthy ? 'Yes' : 'No';
            row.insertCell(4).textContent = stat.calls_in_window;
            row.insertCell(5).textContent = stat.access_token_expiry_in.toFixed(2);
            row.insertCell(6).textContent = stat.refresh_token_expiry_in.toFixed(2);
            row.insertCell(7).textContent = stat.token_scopes;
            row.insertCell(8).textContent = stat.npsso_cookie;

            callsData.push(stat.calls_in_window);
            labels.push(`${machineId}-${id}`);
        });
    });

    if (chart) chart.destroy();
    chart = new Chart(chartCanvas, {
        type: 'bar',
        data: {
            labels: labels,
            datasets: [{
                label: 'Calls in Window',
                data: callsData,
                backgroundColor: 'rgba(59, 130, 246, 0.5)',
                borderColor: 'rgba(59, 130, 246, 1)',
                borderWidth: 1
            }]
        },
        options: {
            responsive: true,
            scales: {
                y: { beginAtZero: true }
            }
        }
    });
}

let eventSource;
function connectSSE() {
    eventSource = new EventSource('/api/token-stats/sse/');
    eventSource.onmessage = function(event) {
        try {
            const stats = JSON.parse(event.data)
            if (stats.error) {
                console.error('SSE error from server: ', stats.error);
                return;
            }
            updateStats(stats);
        } catch (e) {
            console.error('Error parsing SSE data:', e);
        }
    };
    eventSource.onerror = function() {
        console.error('SSE connection error. Retrying in 5s...');
        eventSource.close();
        setTimeout(connectSSE, 5000);
    };
}
connectSSE();

// Fallback: Initial fetch to populate table
fetch('/api/token-stats/')
    .then(response => response.json())
    .then(stats => updateStats(stats))
    .catch(error => console.error('Error fetching initial stats:', error));
