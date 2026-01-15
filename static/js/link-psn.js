/**
 * Link PSN Account - JavaScript functionality
 * Handles verification code copying and PSN profile verification polling
 */

document.addEventListener('DOMContentLoaded', () => {
    // Handle verification code copying (Step 2)
    const copyDiv = document.getElementById('copy-div');
    if (copyDiv) {
        const codeElement = document.getElementById('verification-code');
        const copyTooltip = document.getElementById('copy-tooltip');

        copyDiv.addEventListener('click', () => {
            const code = codeElement.innerText;
            navigator.clipboard.writeText(code).then(() => {
                copyTooltip.dataset.tip = "Copied!";
            }).catch(err => {
                console.error('Failed to copy:', err);
            });
        });
    }

    // Handle PSN verification polling (Step 3)
    const progressContainer = document.getElementById('progress-container');
    if (progressContainer) {
        let pollInterval;
        const maxTime = 30;  // seconds
        let elapsed = 0;

        // Read data from container attributes
        const container = progressContainer.parentElement;
        const profileId = container.dataset.profileId;
        const startTime = container.dataset.startTime;
        const statusUrl = container.dataset.statusUrl;
        const linkPsnUrl = container.dataset.linkPsnUrl;
        const profileDetailUrl = container.dataset.profileDetailUrl;

        function pollStatus() {
            console.log("Polling..");
            const url = `${statusUrl}?profile_id=${profileId}&start_time=${encodeURIComponent(startTime)}`;

            fetch(url)
                .then(response => {
                    if (!response.ok) {
                        throw new Error('HTTP error ' + response.status);
                    }
                    return response.json();
                })
                .then(data => {
                    if (data.error) {
                        clearInterval(pollInterval);
                        alert(data.error);  // e.g., Sync error message
                        window.location.href = linkPsnUrl;
                    } else if (data.synced) {
                        clearInterval(pollInterval);
                        if (data.verified) {
                            alert("Verification successful! Redirecting...");
                            window.location.href = profileDetailUrl;
                        } else {
                            alert("Verification failed. The code was not found in your 'About Me' section or has expired. Please try again.");
                            window.location.href = linkPsnUrl;
                        }
                    }
                    // If not synced, continue polling
                })
                .catch(error => {
                    clearInterval(pollInterval);
                    alert("Error checking status: " + error.message);
                    window.location.href = linkPsnUrl;
                });

            elapsed += 1;
            document.getElementById('poll-progress').value = elapsed;

            if (elapsed >= maxTime) {
                clearInterval(pollInterval);
                alert("Verification timed out. Please try again.");
                window.location.href = linkPsnUrl;
            }
        }

        pollInterval = setInterval(pollStatus, 1000);  // Poll every 1s
        pollStatus();  // Initial immediate check
    }
});
