# Frame Component

The Frame is the chrome that wraps every badge wherever it renders: tier-tinted backdrop, art layers, plinth with engraving, optional pin chip. It's one of the four signature visual primitives in the PlatPursuit design system (see [visual-identity.md §3](../design/visual-identity.md)). This doc is the implementation reference: data contract, sizes, states, public JS API, theming, the Earn Moment, and gotchas.

## Quick Start

```django
{% include "components/frame.html" with frame=frame_ctx %}
```

The partial reads from a single context object `frame`. Compose it in the calling view or template — the partial knows nothing about the `Badge` model. A future planner will add a thin `Badge.to_frame_context(user)` adapter that maps Badge / UserBadge fields into this dict.

Minimal earned example:

```python
frame_ctx = {
    "tier": "gold",
    "state": "earned",
    "series_name": "Marvel Universe",
    "badge_name": "Default Badge",
    "art_layers": [
        "/static/images/badges/backdrops/3_backdrop.png",
        "/static/images/badges/default.png",
        "/static/images/badges/foregrounds/3_foreground.png",
    ],
    "engraving_rank": 247,
    "earned_date": "Jan 21, 2025",
    "stages_done": 10,
    "stages_total": 10,
    "rarity_pct": 3,
    "rarity_rank": 84,
    "rarity_class": "rare",
    "next_tier_label": "Platinum",
}
```

The JS controller auto-initializes on `DOMContentLoaded`, so Frames rendered by the server are wired up automatically. For dynamically inserted Frames (HTMX swaps, modal mounts, etc.), call `PlatPursuit.Frame.init(swappedRoot)` explicitly.

## File Map

| File | Purpose |
|------|---------|
| `templates/components/frame.html` | Main partial (outer card + flippable wrapping) |
| `templates/components/_frame_face_front.html` | Front-face content (shared between flippable + non-flippable) |
| `templates/components/_frame_rarity_sprite.html` | SVG `<symbol>` defs for the rarity icons (auto-mounted in `base.html`) |
| `static/css/components/frame.css` | All Frame CSS, organized in 14 numbered sections |
| `static/js/frame.js` | Public `PlatPursuit.Frame` controller |
| `static/css/input.css` | Tailwind entry — `@import "./components/frame.css"` bundles the component into `output.css` |
| `templates/base.html` | Mounts the rarity sprite once per page |
| `templates/design/frame_component_test.html` | State × size × tier test harness at `/design/frame-component/` |

## Data Contract

`frame_ctx` keys consumed by the partial. Everything is optional unless marked **required**.

| Key | Type | Required | Notes |
|-----|------|----------|-------|
| `tier` | `"bronze" \| "silver" \| "gold" \| "platinum"` | **yes** | Drives every tier-tinted token. |
| `state` | `"earned" \| "in_progress" \| "unearned"` | **yes** | Visual state. Pinning is a separate flag (see `is_pinned`). |
| `size` | `"large" \| "default" \| "compact" \| "mini"` | no | Default `"default"`. |
| `series_name` | str | **yes** | Title bar left side. |
| `badge_name` | str | **yes** | Plinth front + back header. |
| `description` | str | no | Back face only; suppressed if missing. |
| `art_layers` | list[str] (URLs) | **yes** | Rendered as `<img class="pp-frame__layer">` into `.pp-frame__art`. Three layers is canonical (backdrop, default, foreground) but any number works. |
| `engraving_rank` | int | no | Earned cards only. `None` suppresses the engraving entirely. Rank `1` triggers the first-earn pulsing animation. |
| `engraving_total_label` | str | no | Default `"of all time"` — the **decorated** engraving format. |
| `earned_date` | str | no | Pre-formatted display string (e.g. `"Aug 15, 2024"`). |
| `stages_done` / `stages_total` | int | no | Used for the back-face stats card and for the in-progress plinth meta line. |
| `rarity_pct` | float | no | Front + back face. |
| `rarity_rank` | int | no | Back face only (e.g. `"3% · #2,341"`). |
| `rarity_class` | `"common" \| "uncommon" \| "rare" \| "mythic"` | yes for earned | Drives the rarity icon (none / dot / diamond / sparkle). |
| `next_tier_label` | str | no | Back face. Use `"Maxed"` for top-tier holders — the partial automatically relabels the field "Status" instead of "Next tier". |
| `progress_pct` | int 0-100 | no | In-progress state only. Drives the `--pp-build` inline style (the masked badge fill height). |
| `is_pinned` | bool | no | Adds Pin Variant D: cyan accent border + map-pin chip at top-left. Combines with any state. |
| `is_earn_staged` | bool | no | Stages an in-progress card for an upcoming Earn Moment animation: adds `pp-frame--flippable` + `pp-earn-back-staged` and renders the back face (hidden) so the earn-moment back-scan has content to reveal. Pair with `engraving_rank` (the rank the user will receive when earned) so the etch phase reveals "#X of all time" text. |
| `dom_id` | str | no | Sets `id` on the outer `.pp-frame` — needed if you call `PlatPursuit.Frame.triggerEarnMoment` against this card. |
| `flair_slug` | str | no | Future Flair package extension (v1 has no flair variants, but the slot + class pass-through are wired). |
| `allow_flip` | bool | no | Default `True` for `state=earned`. Pass `False` if the calling template wraps the Frame in an `<a>` so click-to-flip doesn't fight with navigation. |

## Sizes

| Size | Use case | Behavior |
|------|----------|----------|
| `large` | Badge / game detail hero | 320px wide, larger type, extra art padding. |
| `default` | Gallery / grid | Fluid width. Tier label in the title bar (right-aligned). |
| `compact` | In-progress lists, home screen tiles | Title bar + plinth hidden. Tier identity carried entirely by the chrome (border + tier-tinted notches + backdrop). No on-art banner. |
| `mini` | Inline / leaderboards / chips | 110px max width. Same chrome-only tier identity as compact. |

## States

| State | Visual | Plinth meta | Engraving |
|-------|--------|-------------|-----------|
| `earned` | Full reveal. Title bar shows tier. Hover lift + tier-tinted gleam sweep. | "Earned [date]" + rarity | "#[rank] of all time" (or first-earn pulse for `#1`) |
| `in_progress` | Blueprint mode: cyan grid, lock icon, Fabricating banner, construction line at the `--pp-build` height. | "[done] of [total] stages" + rarity | Placeholder (reserves height) |
| `unearned` | Blueprint at 0% with "To Earn" stamp. Lock icon centered. | (empty) | (omitted) |

`is_pinned` is independent of state — any state can be pinned. Pinned cards get a cyan accent border + the map-pin chip at top-left; on blueprint cards the accent pulse takes priority over the schematic-glow hover.

## Tiers

| Tier | Notch size | Identity |
|------|------------|----------|
| Bronze | 7px | Warm orange chrome + backdrop, no shimmer. |
| Silver | 8px | Cool gray chrome + faint inset glow on notches. |
| Gold | 9px | Golden chrome + soft inset border glow + warm shadows on hover. |
| Platinum | 10px | Cyan-white chrome + animated notch shimmer (`pp-notch-shimmer`). |

Tier maps to the existing badge-system tiers. The data contract uses string values; mapping from the Badge model field happens in the adapter (deferred work).

## Public API (JS)

```js
window.PlatPursuit.Frame = {
    init(root = document),                 // wire every .pp-frame inside root
    flip(target),                          // toggle .is-flipped on a frame element
    triggerEarnMoment(target, opts),       // returns Promise<{cancelled, reduced}>
    cancelEarnMoment(target),              // stop + reset to pre-play state
    refreshTitleScroll(target),            // re-measure title overflow after dynamic text change
    destroy(target)                        // tear down observers + timers
}
```

`triggerEarnMoment(target, opts)` options:

| Key | Type | Notes |
|-----|------|-------|
| `onPhase` | `(phase) => void` | Called at each phase: `"build"`, `"cooling"`, `"border"`, `"sealed"`, `"uncloak"`, `"searing"`, `"gleaming"`, `"engraving"`, `"flip-back"`, `"back-scan"`, `"flip-front"`, `"settling"`. |
| `onComplete` | `() => void` | Called when the settle finishes. |
| `scale` | number | Override the tier-derived duration multiplier (default comes from `TIER_EARN_SCALE[tier].duration`). |

The Promise resolves to `{ cancelled: true }` if `cancelEarnMoment` is called mid-play, or `{ reduced: true }` if `prefers-reduced-motion` short-circuited the choreography.

## CSS Theming

Public CSS variables (safe to override at the component, page, or container level):

| Variable | Default | Purpose |
|----------|---------|---------|
| `--tier-bronze` / `-silver` / `-gold` / `-platinum` | warm-cool tier hex | Base tier colors. |
| `--tier-bronze-dim` / `-silver-dim` / `-gold-dim` / `-platinum-dim` | darker tone | Gradient stops for tier notches + chrome. |
| `--frame-aspect` | `1 / 1` | Aspect ratio of the art container. |
| `--frame-art-pad` | `10px` (default), `14px` (large), `5px` (mini) | Inset padding around the art layers. |
| `--pp-build` | `0%` | Blueprint build height. Pass on the element style for in-progress cards. |
| `--earn-scale` | `1` | Set by `triggerEarnMoment` per tier (0.75 / 0.9 / 1 / 1.25). |

Internal-only variables (do not override): `--frame-bg`, `--frame-border`, `--frame-chrome-bg`, `--frame-art-bg`, `--spark-*`, `--scan-fwd`, `--scan-back`, `--art-bottom`, `--art-range`, `--notch-size`, `--gleam-color`.

## The Earn Moment

The Earn Moment is the choreographed completion sequence that plays when a Pursuer earns a badge. Twelve phases, ~21 seconds at Gold scale, with intensity scaled per tier via `--earn-scale`.

| Phase | Time (Gold) | Description |
|-------|-------------|-------------|
| 1 | 0–1800ms | Build pulse 90% → 100% with weld sparks. |
| 2 | 1800ms | `.pp-earn-cooling` — card lifts. |
| 3 | 2900–4300ms | Twin-welder border seal. |
| 4 | 4300–5700ms | `.pp-earn-sealed` glow pulse. |
| 5 | 5800–8000ms | `.pp-earn-uncloak` — bottom-to-top scan reveals the earned background. |
| 6 | 8200–10000ms | `.pp-earn-searing` — badge materializes red-white hot, shakes, settles. |
| 7 | 10200–10900ms | `.pp-earn-gleaming` — sheen sweep. |
| 8 | 11100–14100ms | Engraving etches in with traveling welding head + sparks. |
| 9 | 14300ms | Card flips to back face. |
| 10 | 15150–17150ms | `.pp-earn-back-scan` — right-to-left reveal of back-face content. |
| 11 | 18000ms | Card flips back to front. |
| 12 | 18900–20900ms | `.pp-earn-settling` — descend with tier-tinted flourish. |

Per-tier scaling:

| Tier | Duration | Spark count | Notes |
|------|----------|-------------|-------|
| Bronze | 0.75× | 1 | Reduced halo stack, max ~0.45px shake. |
| Silver | 0.9× | 1 | Intermediate intensity, silver-tinted end-state. |
| Gold | 1.0× | 2 | Baseline (all keyframes tuned for this tier). |
| Platinum | 1.25× | 3 | Max halos + post-settle chromatic shimmer (subtle `filter: hue-rotate(0 → 8° → 0)`). |

To trigger:

```js
const card = document.getElementById('my-frame');
PlatPursuit.Frame.triggerEarnMoment(card, {
    onComplete: () => console.log('earned!'),
});
```

## Reduced Motion

With `prefers-reduced-motion: reduce` active:

- **Earn Moment**: skips choreography. JS applies the end-state (`pp-frame--blueprint` and `pp-frame--unearned` removed, `--pp-build` set to `100%`, engraving placeholder cleared) in a single tick. `onComplete` still fires; the Promise resolves with `{ reduced: true }`.
- **Hover gleam**: CSS suppresses the sweep transform + opacity.
- **Title marquee**: CSS suppresses the `pp-frame-title-marquee` animation and falls back to ellipsis truncation.
- **Decorative loops** (mythic rarity pulse, fab-banner pulse, platinum notch shimmer, first-earn pulse, pinned-accent pulse, flip transform): suppressed via the `@media` block at the end of `frame.css`.

Defense in depth: the JS gate and the CSS gate are independent. Either can fail open without breaking the other.

To test: in Chromium DevTools → Rendering → Emulate CSS media feature `prefers-reduced-motion: reduce`. Trigger an earn moment; it should snap to the end-state with no sparks or scan.

## Accessibility

- Flippable cards are `cursor: pointer` and click-to-flip toggles `.is-flipped`. There is **no keyboard support** for flipping in v1; the back face content (description, stats) is duplicated information that's also surfaced elsewhere in the product. If the Frame becomes a primary navigation surface, add `role="button"` + `tabindex="0"` + Enter/Space handling.
- Decorative SVGs use `aria-hidden="true"` (lock icon, pin chip).
- The earn-moment scan beams and weld heads are presentational — they're dynamically appended and removed; no announcement.
- Decide your alt text discipline at the call site. Art layers use `alt=""` by default since the badge name + plinth label communicate the same identity.

## Gotchas and Pitfalls

- **SVG sprite must render once per page**. The component partial assumes `<symbol id="rarity-dot|diamond|sparkle">` is available in the DOM. `base.html` includes `components/_frame_rarity_sprite.html` right after `<body>`. Pages that bypass `base.html` (full-page error templates, custom share-image templates, etc.) must include the sprite themselves.
- **Don't use `ZoomScaler`**. The Frame is built for the modern responsive layout system, not the legacy 768px-scaled chrome. It assumes its own `transform` real estate (hover lift, flip, earn lift). Wrapping it in a `transform: scale(...)` container can break the earn moment's `getBoundingClientRect` measurements (the uncloak phase remaps face coords through `--art-bottom` / `--art-range`).
- **`ResizeObserver` cleanup**. The controller registers a `ResizeObserver` per Frame for title overflow detection. If you remove Frames from the DOM dynamically (HTMX swaps, modal close), call `PlatPursuit.Frame.destroy(target)` first or the observers leak.
- **Click-to-flip vs anchor navigation**. If the calling template wraps the Frame in an `<a>`, set `allow_flip=False` on the context — otherwise the click handler races the link. The partial itself never emits an anchor.
- **`@property --pp-build` browser support**. Requires Chrome 85+, Firefox 128+, Safari 16.4+. Below those versions the build mask transition snaps instead of animating. Functional but less polished. The pre-existing daisyUI `--radialprogress` build warnings about `@property` are unrelated and harmless.
- **Inline `style="--pp-build: ..."` and CSP**. The partial emits an inline style attribute for in-progress cards. Verify the site CSP allows `style-src 'unsafe-inline'` (or the equivalent nonce / hash policy) before mounting on a page with a stricter CSP override.
- **JS file must be loaded per-page, not in base.html**. `static/js/frame.js` is loaded only on pages that mount Frames, via `{% block js_scripts %}`. It's not in `base.html` because most pages don't need it. Auto-init handles all server-rendered Frames; HTMX-swapped Frames need an explicit `PlatPursuit.Frame.init(swappedRoot)` call.
- **The prototype at `/design/frame/` is the design history, not a stale copy**. It stays referenced in `visual-identity.md` as the source of truth for design rationale. The test harness at `/design/frame-component/` is the production-component verification surface. Don't retire the prototype.
- **Reduced motion is JS-gated for the earn moment, CSS-gated for everything else**. If you add a new decorative animation to `frame.css`, also add it to the `@media (prefers-reduced-motion: reduce)` block at the bottom of the stylesheet.

## Reference Implementation

- **Production test harness**: [`/design/frame-component/`](../../templates/design/frame_component_test.html) — every state × size × tier combination rendered through the partial. Use this to verify visual parity after CSS / JS changes.
- **Design prototype**: [`/design/frame/`](../../templates/design/frame_preview.html) — the original 5000-line workshop. Preserved for design rationale.

## Related Docs

- [Visual Identity](../design/visual-identity.md) — Frame is one of four signature primitives; §3 covers the design rationale.
- [Product Identity](../design/product-identity.md) — strategic frame for the badge / Pursuer system the Frame serves.
- [Design System](./design-system.md) — site-wide styling tokens and patterns the Frame coexists with.
