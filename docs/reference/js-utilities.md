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

### PlatPursuit.ZoomAwareObserver

Drop-in `IntersectionObserver` replacement. It was built to survive the legacy **ZoomScaler**
(a sub-768px `transform: scale()` system, now **removed**), whose `overflow: hidden` on
`#zoom-container` broke `IntersectionObserver` clipping. With ZoomScaler gone it detects no zoom and
delegates 100% to native `IntersectionObserver`; the scroll-event fallback is dead-but-inert. Kept as
a drop-in so its several callers don't need touching.

```js
const observer = new PlatPursuit.ZoomAwareObserver((entries) => {
    if (entries[0].isIntersecting) { loadMore(); }
}, { threshold: 0.1, scrollBuffer: 100 });

observer.observe(sentinel);
observer.disconnect();
```

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

### PlatPursuit.slideViewIn

| Method | Parameters | Purpose |
|--------|-----------|---------|
| `slideViewIn(panel, fromName, toName, order)` | HTMLElement, string, string, string[] | Directional "shared axis" view-switch slide |

Applies the shared `.pp-view-in-right` / `.pp-view-in-left` class (`components/motion.css`) to the incoming
`panel`, picking the direction from `order` (forward in the list slides in from the right, backward from
the left). No-ops when `fromName === toName` or under `prefers-reduced-motion`. Works for JS toggles (call
on the now-shown panel) and HTMX island swaps (call on the swapped-in root in `htmx:afterSwap`). Used by
Career tabs, Collection Case/Gallery/List, and the Badges Series/Gallery swap. → [motion-patterns.md](../reference/motion-patterns.md) (Directional view switch).

### PlatPursuit.wireTablist / igniteTab / syncViewParam

The shared behavior behind every rebuilt **segmented switcher** (view/tab toggle). Markup/class-agnostic —
each page keeps its own switch logic and just hands the tabs to these.

| Method | Parameters | Purpose |
|--------|-----------|---------|
| `wireTablist(tabs, opts)` | NodeList/Array, `{onSelect, isActive, manual, ignite}` | WAI-ARIA tablist: roving `tabindex` + Arrow/Home/End nav. Returns `{ syncTabindex }` |
| `igniteTab(tab)` | HTMLElement | One-shot `.pp-tab-ignite` glow bloom on the just-activated chip (restart-safe, reduced-motion gated) |
| `syncViewParam(view, opts)` | string, `{default, paramView, params}` | Reflect the active view in `?view=` (default view stays clean) + strip view-scoped params on leave |

`wireTablist` **automatic** activation (default) activates on click OR arrow — for cheap client-side
switches (Career tabs, Collection Case/Gallery/List + set shelves). **Manual** (`opts.manual`) moves focus
only, letting the tab's own click/Enter activate — for expensive swaps (the Badges Series/Gallery HTMX
`<a>` chips, where auto-activating per arrow would fire a request each keypress). Call the returned
`syncTabindex()` after the active tab changes elsewhere (e.g. an HTMX `afterSwap`). → [motion-patterns.md](../reference/motion-patterns.md) (tab ignite).

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

- **`ZoomAwareObserver` is now a thin wrapper over native `IntersectionObserver`** (its ZoomScaler reason was removed). New code can use `IntersectionObserver` directly; existing `ZoomAwareObserver` callers are fine as-is.
- **`PlatPursuit.API.request()` throws an `Error` with a `.response` property** (raw Response object) on non-ok status. Extract messages with `await error.response?.json().catch(() => null)`. Pass `{}` as body for no-body POSTs.
- **Don't migrate binary fetches** (blob/image downloads) to `PlatPursuit.API`. It's designed for JSON APIs.

## Related Docs

- [Template Architecture](template-architecture.md): Where utils.js is included and how the zoom wrapper works
- [Dashboard](../features/dashboard.md): Uses DragReorderManager for module reordering
- [Roadmap System](../features/roadmap-system.md): Uses API, UnsavedChangesManager, DragReorderManager in the staff editor
