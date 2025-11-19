document.addEventListener('DOMContentLoaded', function() {
    const form = document.getElementById('filter-form');
    const submitBtn = document.getElementById('submit-btn');
    let hasChanged = false;

    form.addEventListener('input', function(e) {
        if (!hasChanged) {
            hasChanged = true;
            submitBtn.disabled = false;
            submitBtn.classList.add('shimmer');
            submitBtn.classList.add('italic');
        }
    });

    form.addEventListener('change', function(e) {
        if (!hasChanged) {
            hasChanged = true;
            submitBtn.disabled = false;
            submitBtn.classList.add('shimmer');
            submitBtn.classList.add('italic');
        }
    });
});