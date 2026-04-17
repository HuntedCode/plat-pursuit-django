# JavaScript Utilities

All shared JavaScript utilities live in `static/js/utils.js` (~1100 lines) and are exported on the `window.PlatPursuit` namespace. Every page includes this file, making these utilities available globally. Individual page scripts access them as `PlatPursuit.API`, `PlatPursuit.ToastManager`, etc.

The browse pages (Games, Profiles, Trophies, Companies, Genres, Themes, Flagged Games) share an HTMX-driven filter controller in `static/js/browse-filters.js` (~290 lines). It is a separate file rather than a `PlatPursuit.*` utility because it self-initializes against `[data-browse-filters]` containers and is only loaded on browse templates.

## Utilities

### PlatPursuit.ToastManager

Shows temporary alert messages in the `#toast-container` element (positioned outside the zoom wrapper in `base.html`).

| Method | Parameters | Purpose |
|--------|-----------|---------|
| `show(message, type, duration)` | type: 'info'\|'success'\|'error'\|'warning', duration: ms (default 5000) | Show toast |
| `success(message, duration)` | duration default 5000 | Success toast |
| `error(message, duration)` | duration default 7000 | Error toast (longer default) |
| `warning(message, duration)` | duration default 5000 | Warning toast |
| `info(message, duration)` | duration default 5000 | Info toast |

Uses DaisyUI alert classes. Auto-removes after duration with slide-out animation. Error toasts get minimum 7000ms duration.

### PlatPursuit.CSRFToken

Retrieves CSRF token for Django requests. Checks hidden input field first, falls back to cookies.

| Method | Returns | Purpose |
|--------|---------|---------|
| `get()` | string | Get CSRF token value |
| `getHeaders(additionalHeaders)` | object | Headers object with `X-CSRFToken` merged |

### PlatPursuit.TimeFormatter

Format timestamps for display.

| Method | Parameters | Returns | Purpose |
|--------|-----------|---------|---------|
| `relative(timestamp)` | string\|Date | string | "5m ago", "2h ago", "3d ago", etc. |
| `absolute(timestamp, options)` | string\|Date, Intl options | string | Locale-formatted date/time |
| `countdown(totalSeconds)` | number | string | "HH:MM:SS" format |

### PlatPursuit.API

HTTP client with CSRF token injection and automatic response parsing.

| Method | Parameters | Returns | Purpose |
|--------|-----------|---------|---------|
| `request(url, options)` | fetch options | Promise | Base request with CSRF + error handling |
| `get(url, options)` | | Promise | GET request |
| `post(url, data, options)` | data: object (JSON.stringified) | Promise | POST with JSON body |
| `put(url, data, options)` | data: object | Promise | PUT with JSON body |
| `patch(url, data, options)` | data: object | Promise | PATCH with JSON body |
| `delete(url, options)` | | Promise | DELETE request |
| `postFormData(url, formData, options)` | FormData object | Promise | POST without Content-Type (browser sets boundary) |
| `fetchHTML(url, options)` | | Promise\<string\> | GET with `X-Requested-With: XMLHttpRequest` |

**Error handling**: On non-ok responses, throws `Error` with `.response` property containing the raw Response object. Callers extract server messages via:
```js
try {
    const data = await PlatPursuit.API.post(url, body);
} catch (error) {
    const errData = await error.response?.json().catch(() => null);
    const msg = errData?.error || 'Something went wrong';
}
```

**Auto-parsing**: 204 returns null, JSON content-type returns parsed object, everything else returns text.

**For no-body POSTs**: Pass empty object `{}` (it gets JSON.stringified). Don't use `post(url)` without a body.

**Do not migrate binary fetches**: API auto-parses as JSON/text. Use raw `fetch()` for blob/image downloads.

### PlatPursuit.HTMLUtils

| Method | Parameters | Returns | Purpose |
|--------|-----------|---------|---------|
| `escape(text)` | string | string | XSS-safe HTML escaping via `textContent`/`innerHTML` |

### PlatPursuit.debounce

```js
const debouncedFn = PlatPursuit.debounce(fn, delay = 300);
```

Creates a trailing-edge debounced function. Returns a new function that delays `fn` execution until `delay` ms after the last call.

### PlatPursuit.InfiniteScroller

Factory for infinite scroll with IntersectionObserver.

```js
const scroller = PlatPursuit.InfiniteScroller.create({
    gridId: 'game-grid',           // Container element ID
    sentinelId: 'scroll-sentinel', // Trigger element ID
    loadingId: 'loading-spinner',  // Loading indicator ID
    paginateBy: 24,                // Items per page (determines if more exist)
    formSelector: '#filter-form',  // Optional: resets page on submit
    scrollKey: 'games_scroll',     // Optional: localStorage key for scroll restore
    cardSelector: '.card',         // Optional: selector for items in fetched HTML
});

// Cleanup
scroller.destroy();
```

Fetches next page via AJAX with `X-Requested-With: XMLHttpRequest`, parses HTML, appends matching elements to the grid. Automatically stops when a page returns no matching elements or 404.

### PlatPursuit.UnsavedChangesManager

Warns users before navigating away with unsaved changes. Intercepts link clicks, browser back button, and tab close.

```js
PlatPursuit.UnsavedChangesManager.init({
    hasUnsavedChanges: () => formIsDirty,     // Required: returns boolean
    onSaveAndLeave: async (url) => { ... },   // Optional: save before leaving
    showSaveButton: true,                      // Optional: show save option in modal
    modalId: 'unsaved-changes-modal',          // Optional: custom modal ID
});
```

| Method | Purpose |
|--------|---------|
| `init(config)` | Initialize with config (destroys previous if any) |
| `forceNavigate(url)` | Navigate without checking for changes |
| `isActive()` | Check if manager is initialized |
| `destroy()` | Remove all event listeners and reset |

Requires a `<dialog>` element with buttons: `#unsaved-stay-btn`, `#unsaved-discard-btn`, `#unsaved-save-btn`.

### PlatPursuit.ZoomScaler

Enables uniform sub-768px page scaling via `transform: scale()`.

```js
PlatPursuit.ZoomScaler.init();  // Call once per page in {% block js_scripts %}
```

Adds `.zoom-active` to `#zoom-container`, which activates CSS rules in `input.css`. Handles height correction via MutationObserver since `transform: scale()` doesn't change the layout box. See CLAUDE.md for the full zoom wrapper architecture.

### PlatPursuit.ZoomAwareObserver

Drop-in `IntersectionObserver` replacement that works correctly when ZoomScaler is active.

```js
const observer = new PlatPursuit.ZoomAwareObserver((entries) => {
    if (entries[0].isIntersecting) { loadMore(); }
}, { threshold: 0.1, scrollBuffer: 100 });

observer.observe(sentinel);
observer.disconnect();
```

When `ZoomScaler` is active (sub-768px), `overflow: hidden` on `#zoom-container` breaks `IntersectionObserver`'s clipping calculations. `ZoomAwareObserver` detects this and switches to a scroll-event fallback using `getBoundingClientRect()` (which correctly accounts for CSS transforms). On desktop, it delegates to native `IntersectionObserver` with zero overhead.

**Options:** All standard `IntersectionObserver` options, plus `scrollBuffer` (default 100): pixels beyond viewport to trigger detection in scroll fallback mode.

**API:** `observe(target)`, `unobserve(target)`, `disconnect()` (same as `IntersectionObserver`).

**Important:** Use `ZoomAwareObserver` instead of `IntersectionObserver` for any viewport-relative infinite scroll sentinel. Do NOT use it for observers with a custom `root` element (e.g., modal scroll containers), as those are unaffected by the zoom transform.

### PlatPursuit.DragReorderManager

Smooth, touch-friendly drag-and-drop reordering powered by SortableJS.

```js
const dragger = new PlatPursuit.DragReorderManager({
    container: document.getElementById('my-list'),
    itemSelector: '.sortable-item',
    onReorder: (itemId, newPosition, allItemIds) => { ... },
    handleSelector: '.drag-handle',     // Optional: restrict drag to handle
    onStart: (evt) => { ... },          // Optional: callback on drag start
    onEnd: (evt) => { ... },            // Optional: callback on drag end
});
dragger.destroy();                      // Cleanup when done
```

Wraps SortableJS with `forceFallback: true` for consistent cross-browser behavior (including touch devices). Provides 200ms ease animations, swap threshold to prevent flickering in grid layouts, and auto-scroll near container edges. The `onReorder` callback signature matches the legacy API for backward compatibility.

**CSS classes** (defined in `input.css`): `.sortable-ghost` (dashed placeholder), `.sortable-chosen` (shadow + scale lift), `.sortable-drag`, `.sortable-fallback`.

**Requires**: `static/js/vendor/Sortable.min.js` loaded before `utils.js` (added in `base.html`). Degrades gracefully if SortableJS is not available.

### PlatPursuit.LeaderboardUtils

| Method | Parameters | Purpose |
|--------|-----------|---------|
| `updatePage(form, paramName)` | HTMLFormElement, string | Navigate to page number from form input |

Validates page number against min/max before navigation.

### PlatPursuit.CoachMarks

Factory for spotlight-style page tours. Dims the page with a dark overlay, cuts a transparent window around the current target, and positions a tooltip beside it with step controls (Prev/Next/Skip + counter).

| Method | Parameters | Returns | Purpose |
|--------|-----------|---------|---------|
| `createTour(config)` | `{ steps, elementIds, dismissUrl }` | tour instance | Build a tour wired to the given overlay/tooltip DOM |

The returned tour exposes `init(autoShow)`, `open()`, `close()`, `next()`, `prev()`, `dismiss(action)`. Step shape: `{ target (CSS selector), title, description, icon (inner SVG markup), position: 'top'|'bottom' }`. The dismiss endpoint receives `{ action, last_step }` as JSON.

Tooltip positioning uses `window.visualViewport` when available (so mobile browser chrome and the virtual keyboard are respected) and always clamps the tooltip inside the visible viewport so its controls stay reachable, even when the target is taller than the screen. In that tall-target case the tooltip may overlap the highlighted section, which is preferable to the old behavior where Next/Skip could be pushed off-screen on mobile.

Currently used by `badge-detail-tour.js` and `game-detail-tour.js`. Shared CSS: `.coach-overlay`, `.coach-tooltip`, `.coach-target-highlight` in `static/css/input.css`.

## Namespace Pattern

All utilities are declared as `const` or `class` at module scope, then exported at the bottom:

```js
window.PlatPursuit = window.PlatPursuit || {};
window.PlatPursuit.ToastManager = ToastManager;
window.PlatPursuit.API = API;
// ... etc
```

To add a new utility: define it above the export block, then add a `window.PlatPursuit.YourUtility = YourUtility;` line.

## Gotchas and Pitfalls

- **Never use raw `IntersectionObserver` for viewport-relative infinite scroll sentinels.** CSS `transform: scale()` combined with `overflow: hidden` (from ZoomScaler) breaks `IntersectionObserver` clipping on sub-768px screens. Always use `ZoomAwareObserver` instead. Observers with a custom `root` element (e.g., inside a modal scroll container) are unaffected and can use `IntersectionObserver` directly.
- **`PlatPursuit.API.request()` throws an `Error` with a `.response` property** (raw Response object) on non-ok status. Extract messages with `await error.response?.json().catch(() => null)`. Pass `{}` as body for no-body POSTs.
- **Don't migrate binary fetches** (blob/image downloads) to `PlatPursuit.API`. It's designed for JSON APIs.

## Page-Specific Modules

These are standalone JS files loaded only on their respective pages. They follow the same `window.PlatPursuit.*` namespace pattern but are not part of `utils.js`.

| File | Class / Instance | Page | Purpose |
|------|------------------|------|---------|
| `static/js/welcome-tour.js` | `WelcomeTourManager` | All (via `base.html`) | 7-step modal carousel teaching hub navigation |
| `static/js/game-detail-tour.js` | `GameDetailTourManager` (CoachMarks) | Game detail | 4-step coach marks (stats, flags, reviews, lists) |
| `static/js/badge-detail-tour.js` | `BadgeDetailTourManager` (CoachMarks) | Badge detail | 4-step coach marks (overview, tiers, stages, leaderboards) |

The Welcome Tour is a daisyUI `<dialog>` carousel. The game and badge detail tours share a single implementation through `PlatPursuit.CoachMarks.createTour(config)` (above); each of those files is a thin wrapper supplying step copy, DOM ids, and the dismiss endpoint. All three tour managers expose the same public surface: `init(autoShow)` entry point, `open()`/`dismiss(action)` lifecycle, keyboard nav (arrow keys + Escape), and a dismiss guard to prevent double API calls. See [Tutorial System](../design/tutorial-system.md) for design details.

## Related Docs

- [Template Architecture](template-architecture.md): Where utils.js is included and how the zoom wrapper works
- [Dashboard](../features/dashboard.md): Uses DragReorderManager for module reordering
- [Roadmap System](../features/roadmap-system.md): Uses API, UnsavedChangesManager, DragReorderManager in the staff editor
- [Tutorial System](../design/tutorial-system.md): Welcome Tour, Game Detail Tour, Badge Detail Tour
