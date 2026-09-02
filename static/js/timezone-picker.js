/**
 * Timezone picker -- shared between the recap archive's utility row and its first-run modal.
 *
 * Extracted from a 120-line <script> block inside `recap/_timezone_section_js.html`. The modal needs the
 * same curated zone list, the same browser detection and the same save path, and a second copy of any of
 * those would be a second thing to keep correct -- the zone list in particular is data, and data
 * duplicated across two files diverges quietly.
 *
 * The save endpoint (`POST /api/v1/user/timezone/`) un-finalizes the hunter's recaps when the zone
 * actually changes, so they regenerate against the new month boundaries. It also stamps
 * `timezone_confirmed_at`, which is what tells the modal never to open by itself again.
 */
(function () {
    'use strict';

const GROUPS = {
    'Americas': [
        'America/New_York', 'America/Chicago', 'America/Denver',
        'America/Los_Angeles', 'America/Anchorage', 'Pacific/Honolulu',
        'America/Phoenix', 'America/Toronto', 'America/Vancouver',
        'America/Halifax', 'America/St_Johns', 'America/Mexico_City',
        'America/Bogota', 'America/Lima', 'America/Santiago',
        'America/Sao_Paulo', 'America/Argentina/Buenos_Aires', 'America/Caracas',
    ],
    'Europe': [
        'Europe/London', 'Europe/Dublin', 'Europe/Lisbon',
        'Europe/Paris', 'Europe/Berlin', 'Europe/Madrid',
        'Europe/Rome', 'Europe/Amsterdam', 'Europe/Brussels',
        'Europe/Vienna', 'Europe/Warsaw', 'Europe/Prague',
        'Europe/Budapest', 'Europe/Bucharest', 'Europe/Athens',
        'Europe/Helsinki', 'Europe/Stockholm', 'Europe/Oslo',
        'Europe/Copenhagen', 'Europe/Moscow', 'Europe/Istanbul',
    ],
    'Asia & Middle East': [
        'Asia/Dubai', 'Asia/Riyadh', 'Asia/Tehran',
        'Asia/Karachi', 'Asia/Kolkata', 'Asia/Dhaka',
        'Asia/Bangkok', 'Asia/Singapore', 'Asia/Hong_Kong',
        'Asia/Shanghai', 'Asia/Taipei', 'Asia/Seoul',
        'Asia/Tokyo', 'Asia/Manila', 'Asia/Jakarta',
    ],
    'Oceania': [
        'Pacific/Auckland', 'Australia/Sydney', 'Australia/Melbourne',
        'Australia/Brisbane', 'Australia/Perth', 'Australia/Adelaide',
        'Pacific/Fiji',
    ],
    'Africa': [
        'Africa/Cairo', 'Africa/Johannesburg', 'Africa/Lagos',
        'Africa/Nairobi', 'Africa/Casablanca',
    ],
    'Other': ['UTC'],
};

    /** The browser's own zone, or null where the API is unavailable or blocked. */
    function detect() {
        try {
            return Intl.DateTimeFormat().resolvedOptions().timeZone || null;
        } catch (e) {
            return null;
        }
    }

    /** Human form: "America/New_York" -> "New York". */
    function label(tz) {
        return String(tz || '').replace(/^[^/]+\//, '').replace(/_/g, ' ');
    }

    /**
     * Fill a <select> with the curated groups.
     *
     * The hunter's current zone and the browser's detected zone are added to a "Detected / Current" group
     * when the curated list does not already carry them -- without it, someone in an uncommon zone would
     * open the picker and not find the zone they are already in.
     */
    function populate(select, currentTz) {
        if (!select) { return; }
        select.innerHTML = '';

        const listed = new Set();
        Object.values(GROUPS).forEach(function (tzs) { tzs.forEach(function (tz) { listed.add(tz); }); });

        const browserTz = detect();
        const extras = [];
        if (currentTz && !listed.has(currentTz)) { extras.push(currentTz); }
        if (browserTz && !listed.has(browserTz) && browserTz !== currentTz) { extras.push(browserTz); }

        if (extras.length) {
            const g = document.createElement('optgroup');
            g.label = 'Detected / Current';
            extras.forEach(function (tz) {
                const o = document.createElement('option');
                o.value = tz;
                o.textContent = tz.replace(/_/g, ' ');
                if (tz === currentTz) { o.selected = true; }
                g.appendChild(o);
            });
            select.appendChild(g);
        }

        Object.keys(GROUPS).forEach(function (name) {
            const g = document.createElement('optgroup');
            g.label = name;
            GROUPS[name].forEach(function (tz) {
                const o = document.createElement('option');
                o.value = tz;
                o.textContent = label(tz);
                if (tz === currentTz) { o.selected = true; }
                g.appendChild(o);
            });
            select.appendChild(g);
        });
    }

    /**
     * Save a zone. Resolves to the API payload, rejects with a human message.
     *
     * Callers decide what to do afterwards: the utility row reloads (the page it is on is built from the
     * old boundaries), the modal closes first so the reload is not a surprise mid-dialog.
     */
    async function save(tz) {
        try {
            return await PlatPursuit.API.post('/api/v1/user/timezone/', { timezone: tz });
        } catch (err) {
            let msg = 'Failed to update timezone.';
            try {
                const data = await err.response?.json();
                msg = (data && data.error) || msg;
            } catch (e) { /* no body, keep the default */ }
            throw new Error(msg);
        }
    }

    window.PlatPursuit = window.PlatPursuit || {};
    window.PlatPursuit.TimezonePicker = { GROUPS: GROUPS, detect: detect, label: label,
                                          populate: populate, save: save };
})();
