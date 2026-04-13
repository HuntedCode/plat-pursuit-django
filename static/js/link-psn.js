/**
 * Link PSN Account - JavaScript functionality
 * Handles verification code copying and PSN profile verification polling.
 */

document.addEventListener('DOMContentLoaded', () => {
    const { ToastManager } = PlatPursuit;

    // ── Copy to Clipboard (Step 2) ──────────────────────────────────

    const copyBtn = document.getElementById('copy-btn');
    if (copyBtn) {
        const targetId = copyBtn.getAttribute('data-copy-target');
        const codeEl = document.getElementById(targetId);
        const copyIcon = document.getElementById('copy-icon');
        const checkIcon = document.getElementById('check-icon');

        function showCopySuccess() {
            copyIcon.classList.add('hidden');
            checkIcon.classList.remove('hidden');
            ToastManager.success('Code copied to clipboard!');

            setTimeout(() => {
                checkIcon.classList.add('hidden');
                copyIcon.classList.remove('hidden');
            }, 2000);
        }

        function fallbackCopy(text) {
            const textarea = document.createElement('textarea');
            textarea.value = text;
            textarea.style.position = 'fixed';
            textarea.style.opacity = '0';
            document.body.appendChild(textarea);
            textarea.select();
            try {
                document.execCommand('copy');
                showCopySuccess();
            } catch {
                ToastManager.error('Could not copy. Please select and copy the code manually.');
            }
            document.body.removeChild(textarea);
        }

        copyBtn.addEventListener('click', () => {
            if (!codeEl) return;
            const text = codeEl.textContent.trim();

            if (navigator.clipboard?.writeText) {
                navigator.clipboard.writeText(text)
                    .then(showCopySuccess)
                    .catch(() => fallbackCopy(text));
            } else {
                fallbackCopy(text);
            }
        });
    }

    // ── Verification Polling (Step 3) ───────────────────────────────

    const pollContainer = document.getElementById('poll-container');
    if (pollContainer) {
        const profileId = pollContainer.dataset.profileId;
        const startTime = pollContainer.dataset.startTime;
        const statusUrl = pollContainer.dataset.statusUrl;
        const linkPsnUrl = pollContainer.dataset.linkPsnUrl;
        const profileDetailUrl = pollContainer.dataset.profileDetailUrl;

        const progressBar = document.getElementById('poll-progress');
        const elapsedLabel = document.getElementById('poll-elapsed');
        const statusText = document.getElementById('poll-status');
        const dotsSpan = document.getElementById('poll-dots');
        const timeoutBlock = document.getElementById('poll-timeout');

        const maxTime = 30;
        let elapsed = 0;
        let dotCount = 0;
        let pollInterval;
        let active = true;

        function animateDots() {
            dotCount = (dotCount + 1) % 4;
            dotsSpan.textContent = '.'.repeat(dotCount);
        }

        function redirectAfterDelay(url, delayMs) {
            setTimeout(() => { window.location.href = url; }, delayMs);
        }

        function stopPolling() {
            active = false;
            clearInterval(pollInterval);
        }

        function onTimeout() {
            stopPolling();
            statusText.textContent = 'Timed out';
            dotsSpan.textContent = '';
            timeoutBlock.classList.remove('hidden');
        }

        function pollStatus() {
            if (!active) return;

            animateDots();
            elapsed += 1;

            progressBar.value = elapsed;
            elapsedLabel.textContent = `${elapsed} / ${maxTime}s`;

            if (elapsed >= maxTime) {
                onTimeout();
                return;
            }

            const url = `${statusUrl}?profile_id=${profileId}&start_time=${encodeURIComponent(startTime)}`;

            PlatPursuit.API.get(url)
                .then(data => {
                    if (!active) return;

                    if (data.error) {
                        stopPolling();
                        ToastManager.error(data.error);
                        redirectAfterDelay(linkPsnUrl, 1500);
                    } else if (data.synced) {
                        stopPolling();
                        if (data.verified) {
                            ToastManager.success('Verification successful! Redirecting to your profile...');
                            redirectAfterDelay(profileDetailUrl, 1500);
                        } else {
                            ToastManager.error('Verification failed. The code was not found in your "About Me" section. Please try again.');
                            redirectAfterDelay(linkPsnUrl, 2500);
                        }
                    }
                })
                .catch(error => {
                    if (!active) return;

                    stopPolling();
                    const msg = error.message || 'An unexpected error occurred.';
                    ToastManager.error(`Error checking status: ${msg}`);
                    redirectAfterDelay(linkPsnUrl, 2000);
                });
        }

        // Start polling: immediate first check, then every 1s
        pollStatus();
        pollInterval = setInterval(pollStatus, 1000);
    }
});
