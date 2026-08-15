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
Career tabs and the Badges Series/Gallery swap. → [motion-patterns.md](../reference/motion-patterns.md) (Directional view switch).

### PlatPursuit.wireTablist / igniteTab / syncViewParam

The shared behavior behind every rebuilt **segmented switcher** (view/tab toggle). Markup/class-agnostic —
each page keeps its own switch logic and just hands the tabs to these.

| Method | Parameters | Purpose |
|--------|-----------|---------|
| `wireTablist(tabs, opts)` | NodeList/Array, `{onSelect, isActive, manual, ignite}` | WAI-ARIA tablist: roving `tabindex` + Arrow/Home/End nav. Returns `{ syncTabindex }` |
| `igniteTab(tab)` | HTMLElement | One-shot `.pp-tab-ignite` glow bloom on the just-activated chip (restart-safe, reduced-motion gated) |
| `syncViewParam(view, opts)` | string, `{default, paramView, params}` | Reflect the active view in `?view=` (default view stays clean) + strip view-scoped params on leave |

`wireTablist` **automatic** activation (default) activates on click OR arrow — for cheap client-side
switches (Career tabs, Badges Series/Gallery). **Manual** (`opts.manual`) moves focus
only, letting the tab's own click/Enter activate — for expensive swaps (the Badges Series/Gallery HTMX
`<a>` chips, where auto-activating per arrow would fire a request each keypress). Call the returned
`syncTabindex()` after the active tab changes elsewhere (e.g. an HTMX `afterSwap`). → [motion-patterns.md](../reference/motion-patterns.md) (tab ignite).

### PlatPursuit.staggerReveal

| Method | Parameters | Purpose |
|--------|-----------|---------|
| `staggerReveal(opts)` | `{grid, cardSelector, reveal, step?, batchCap?, appendCap?, hideClass?}` | Staggered WAAPI grid reveal for HTMX-swapped / infinite-scroll grids |

Hides the grid's cards (`hideClass`, default `.pp-reveal`), reveals those already present in ONE DOM-order
batch, and returns `{ observe(nodes), disconnect() }` — call `observe()` on infinite-scroll-appended cards
(from `InfiniteScroller`'s `onAppend`) so they reveal as they scroll in. The page supplies the per-card
animation via `reveal(el, delayMs)` (use WAAPI `el.animate` so arrivals restart on freshly HTMX-swapped
nodes); the engine owns the reduced-motion gate + batch stagger + observer, and marks each card
`.is-revealed` once. Returns `null` when motion is off / no cards / no IntersectionObserver. **The standard
for rebuilt browse grids** (Badges; the pending Challenges/Franchise/Company/Game-Lists/Browse rebuilds).
**Not** for every reveal — see the note in [motion-patterns.md](../reference/motion-patterns.md) (Staggered grid reveal).

### PlatPursuit.dismissableSheet

| Method | Parameters | Purpose |
|--------|-----------|---------|
| `dismissableSheet(dialog, opts)` | HTMLElement, `{onClose, scrim?, threshold?, handle?}` | iOS-style "swipe down to close" for a modal on touch |

**The common practice for every mobile modal.** Wire it on the modal's dialog: on a downward flick past
`threshold` (default 90px) it slides the sheet off (fading `scrim`) and calls `onClose` — which should
**hide instantly** (the helper already did the exit) and run the same cleanup the close button does. Drag
only starts from the top of the dialog's scroll (mid-content scroll isn't hijacked), and **never from a
draggable control** (`input`, `textarea`, `select`, `[role="slider"]`) -- a finger sliding a range input
never travels perfectly horizontally, and the helper preventDefault()s any downward movement, so it was
dragging the sheet instead of the thumb. Links and buttons are deliberately NOT excluded: they have no
drag gesture to protect, and excluding them would leave a sheet whose body is a wall of cards with only
its gutters draggable. Pass **`handle`** (a selector) to restrict the drag to one region -- the quick-rate
modal uses `'.gd-modal__head'`, because it is the one sheet that is a FORM rather than something you read
and its grabber pill sits in that header. The helper adds
`.pp-dismissable` to the dialog, surfacing the shared touch-only grabber handle (`.pp-dismissable::before`):
it fades in a beat after the sheet opens (`ppGrabIn`), rides the sheet off on a swipe, and fades out on a
non-drag close (`ppGrabOut`, keyed on the modal's `.is-closing`) — all in `badge-inspect.css`, reduced-motion gated.
Live on the badge-detail stats + contract modals, the Career job/contract modal, and (via
`Medallion.detailModal`) every medallion **peek** across collection / badge list / badge detail. The peek
can't FLIP its disc back from a dragged-off position, so on swipe it instead **returns the object home** --
the source medallion reappears in the grid with a subtle materialize settle -- while the tap/close button
keeps the grow/shrink "put-down". Both routes send the object back to its slot.

### PlatPursuit.CardDownload

| Method | Parameters | Purpose |
|--------|-----------|---------|
| `attach(button, opts)` | HTMLElement, `{url, filename, toast?, onStart?, onError?, labels?, autoBind?}` | Wire a three-state download button. Returns `{run, setBlocked, reset, state}` |

**The common practice for every server-rendered share card.** Pairs with
`components/download-button.css` (the `.pp-dl` state classes) and
`partials/download_button_icons.html` (the three glyphs, all shipped, CSS picks one). Live on the plat
card modal, the recap ceremony, and the recap's below-fold panel — which between them had three copies of
fetch-blob-anchor before this.

The PNG is composed by headless Chromium on the server, so the press is **not** instant: `busy` is the
load-bearing state, not `done`. And the file lands somewhere the page cannot see, so `done` is the only
confirmation there is. Both are why the button is the progress indicator and announces like one
(`aria-busy`, and the label swaps in place rather than the button being rebuilt).

Fetch-then-save rather than navigating, which is the whole reason it exists: `location.href` is fine
while the endpoint returns an attachment but its failure paths are not — a render error returns JSON and
the per-user rate limit returns an HTML 403, either of which replaces the page with a bare error
document. On the recap that took the open ceremony with it.

Points worth knowing before you touch it:

| Thing | Why |
|---|---|
| `url` / `filename` are **functions** | Resolved at press time. The ground and the art index both change while a modal is open, and a URL captured at bind time saves the card the hunter was looking at a minute ago |
| `disabled` is **derived** | From the caller's reason (`setBlocked`, e.g. preview still loading) and the in-flight one, never written by either. They used to race: a theme swap re-disabled the button while the "Saved" revert timer was queued to re-enable it, and whichever fired last won |
| idle label belongs to the **caller** | Unless `labels.idle` is passed, idle means "whatever it said before" — the plat card names its variant ("Download 100% card") and a fixed string demoted it to a generic "Download" the first time it was used |
| the width is **pinned** at press | The stylesheet's `min-width` only knows OUR three labels, so a longer caller label shrank the button 50px mid-press and shuffled the row it sits in. Measured, not guessed — it depends on the font that loaded |
| `toast: false` inside a takeover | The page toast host is `z-50`. A `<dialog>` gets a top-layer host for free; a takeover div (the recap stage, `z-90`) does not, so a toast fired from inside renders *behind* it. Those surfaces carry their own error line instead |
| a **download** failure must not block | "Give it a minute" was being shown by the same call that disabled the only button that could take the advice. A *preview* failure blocks (no card exists); a download failure does not |

### PlatPursuit.wireGuidelinesSheet

| Method | Parameters | Purpose |
|--------|-----------|---------|
| `wireGuidelinesSheet()` | none | Wires the Community Guidelines sheet (`#gd-guidelines-modal`) -- close affordances + the `[data-gd-guidelines-open]` open delegate |

Opens the sheet OVER whatever compose surface is showing, so reading the rules never loses an
in-progress quick take. Idempotent and a no-op when the sheet isn't on the page, so every page that
composes a surface linking to it can just call it. Read-only: agreement is recorded on submit, not here.

### PlatPursuit.RatingFields / PlatPursuit.QuickRate

Both live in `static/js/quick-rate.js`, not `utils.js` -- one feature's controller rather than a general
utility, loaded only by the pages that compose it. The file is two layers, because the rating form has
three hosts and only two of them are modals:

- **`RatingFields`** owns the FORM and knows nothing about presentation.
- **`QuickRate`** is the modal (`#gd-qr-modal`) wrapped around it.

The fields themselves are one template, `partials/_rating_fields.html`, included by both hosts. The input
NAMES are the API contract -- do not rename them.

| Method | Parameters | Purpose |
|--------|-----------|---------|
| `RatingFields.attach(form, opts)` | see below | Drives any `<form>` carrying the shared fields; returns a handle |
| `QuickRate.open(opts)` | the same, plus modal options | Opens + drives the quick-rate modal (`quick_rate_modal.html`) |

```js
var fields = PlatPursuit.RatingFields.attach(formEl, {
    conceptId, groupId,          // required -- the POST target
    existing, blurb,             // prefill; null/'' for a fresh rating
    submitEl,                    // defaults to [data-gd-qr-submit] INSIDE the form
    submitLabel, hoursLabel, playtimeHint,
    onSaved(data, payload), onError(message),
    onChange({ready, hours}),    // readiness changed -- gate your own button on it
});
// -> { setTarget(conceptId, groupId), prefill(existing, blurb), label(opts), submit(), state(), detach() }
```

`setTarget()` exists for the Rate My Games wizard, which advances through a queue against ONE form:
detaching and re-attaching per game stacks a fresh set of listeners each time, and one submit then posts
as many times as you have rated. `onChange` exists for the same host -- its submit button lives outside
the form, next to Skip, so it gates itself on the hours field rather than letting the controller own it.

```js
PlatPursuit.QuickRate.open({
    conceptId, groupId,          // required -- the POST target
    existing,                    // {difficulty, grindiness, fun_ranking, overall_rating,
                                 //  hours_to_platinum} or null
    blurb,                       // existing quick take
    title, submitLabel, cancelLabel, hoursLabel, playtimeHint,
    onSaved(data, payload),      // the save landed (the modal has already closed)
    onCancel(),                  // the explicit secondary button -- NOT a dismiss
    onDismiss(),                 // X / backdrop / Esc / swipe
    onOpen(), onClose(),         // lifecycle, for page chrome (e.g. the recede)
});
```

The controller owns **everything except what happens after a save**: prefill (including the blurb),
slider readouts, the character counter, the hours gate, agree-to-guidelines-on-submit, the POST, error
surfacing, and (in `QuickRate`) every close affordance. Hosts: the Game Detail Ratings tab (its `onSaved`
live-updates the community panel), the plat-card share modal (its `onSaved` invalidates the preview
cache), and the Rate My Games wizard, which composes `RatingFields` directly because it renders the form
inline beside a trophy list.

`onCancel` and `onDismiss` are **deliberately separate**. The share flow opens this as a pre-download
prompt where the secondary button means "skip, just download" -- so a dismiss must never fire it, or the
universal get-me-out-of-here affordance hands you a file.

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
- **A modal that rebinds listeners per open must tear down on the dialog's `close` event**, not only
  on its explicit paths. `dismissableSheet`'s swipe bypasses them, so the next open double-binds and a
  single submit fires twice. `QuickRate` binds an idempotent teardown to `close` for this reason.
- **A host that gates its own submit button must be told LAST.** `RatingFields.attach()` resets the
  button before prefilling, because prefilling announces readiness through `onChange` -- reversing that
  order re-enabled a button the host had just correctly disabled.
- **`PlatPursuit.API.request()` throws an `Error` with a `.response` property** (raw Response object) on non-ok status. Extract messages with `await error.response?.json().catch(() => null)`. Pass `{}` as body for no-body POSTs.
- **Don't migrate binary fetches** (blob/image downloads) to `PlatPursuit.API`. It's designed for JSON APIs.

## Related Docs

- [Template Architecture](template-architecture.md): Where utils.js is included and how the zoom wrapper works
- [Dashboard](../features/dashboard.md): Uses DragReorderManager for module reordering
- [Roadmap System](../features/roadmap-system.md): Uses API, UnsavedChangesManager, DragReorderManager in the staff editor
