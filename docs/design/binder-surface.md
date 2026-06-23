# The Binder (Surface)

The Binder is a branded **Surface** in the PlatPursuit design system — a recognizable layout container that arranges Frames into a trading-card binder metaphor. It sits alongside the four signature primitives (Frame, Pursuer Card, Horizon, Tally) defined in [visual-identity.md §3](visual-identity.md#3-signatures), but is categorically different from them: primitives are *atomic* (small repeated units of identity), Surfaces are *composite* (branded containers that arrange primitives).

This doc captures the design decisions and technical learnings from the Binder prototype at [`/design/binder/`](../../templates/design/binder_preview.html). The workshop below describes the **full** six-view design; the production extraction (see status note) is a deliberate subset.

> **Production status (shipped).** The Binder is mounted at **`/my-pursuit/collection/`** (`templates/components/binder.html` + `binder.css` + `binder.js`, fed by `collection_service.build_collection_context`). The baseline extraction is **Single + Binder presentation only**, with two deliberate divergences from the workshop:
> - **Sets, not one continuous run.** Each badge type (Series, Developers, …) is its own binder view, picked from a set-tab strip; pages paginate within a set. Cross-linked with the sibling **List** view (`#card-NNNN` ↔ `#row-NNNN`).
> - **Gallery / Compact / Spread were NOT extracted.** The responsive grid (4 → 2 → 1 columns, chrome slimmed on phones) solved the mobile-fit problem that **Gallery** existed for, and the **List** view covers dense scanning — so Gallery was dropped as a redundant middle ground (Binder = the charm, List = the hunting tool). Compact + Spread remain workshop-only.

---

## Concept

If the Frame turns a badge into a trading card, the Binder turns a collection of trading cards into a physical artifact: a three-ring sleeve binder. Pages with pocket sleeves. A spine running down the middle. Page tabs marking series. A cover. Page numbers. The visual metaphor a long-time card collector reaches for.

The Binder is the implementation of the "Album" concept named in [visual-identity.md §3 → The Album](visual-identity.md#the-album-badge-gallery-as-trading-card-binder). That section names the *what*; this doc names the *how*.

**Why a Surface, not a primitive.** The four primitives (Frame, Pursuer Card, Horizon, Tally) are atomic — small repeated units of brand identity used across the product. Binder is the opposite: a large, one-per-screen container that *arranges* primitives (specifically: Frames). Mixing the two flattens the conceptual hierarchy. Categorizing the Binder as a Surface keeps the four-primitive framing clean and creates a sibling slot for future containers (a Trophy Case for completed platinums, a Showcase for the Pursuer Card hero, a Wall for milestone displays).

---

## The Six Views

The user has full agency to choose how they view their collection. The Binder offers five view combinations, plus a sibling list view for power users.

### Binder views (5)

A 3 × 2 matrix of **view mode** (Single / Compact / Spread) × **presentation** (Binder / Gallery), minus one impossible combination.

| # | Mode | Presentation | When the user picks this |
|---|------|-------------|--------------------------|
| 1 | Single | Binder | Default. One full-bleed page at a time, complete binder chrome (spine, rings, cover, tabs). The "I just opened the binder" feel. |
| 2 | Compact | Binder | One page at a time, slim-chrome Frames so more cards fit per page. Dense scanning while keeping the binder vessel. |
| 3 | Spread | Binder | Two facing pages with the spine between, 3D page-flip animation between spreads. The "I'm flipping through the binder" feel. **Disabled on Gallery** (no binder spine to flip around). |
| 4 | Single | Gallery | One page at a time, no binder chrome — just a clean grid of sleeves. For users who like the album structure but find the binder dressing too literal. |
| 5 | Compact | Gallery | Same as Compact Binder but no chrome. Dense scanning, no decoration. |

**Mobile constraint.** Below the 1024px breakpoint, the binder dressing fights the narrow viewport (the spine eats horizontal space, the cover banner eats vertical space, drag-to-flip needs precise pointer control). Mobile force-snaps to **Single + Gallery** on entry and on resize-down from desktop. The other four combinations are desktop-only.

### Collection list view (sixth view)

At [`/design/badge-collection/`](../../templates/design/badge_collection_list.html), a sortable / filterable / searchable **table** of every card. Same data set as the Binder, different presentation — the trade is browseability for inspectability. The two views are bidirectionally deep-linked: every row links to `#card-NNNN` in the Binder, every binder sleeve links to `#row-NNNN` in the list. Users who want to find a specific badge fast jump to the list; users who want to *experience* their collection stay in the Binder.

The list view is conceptually part of the same Surface — same data, same identity, same chrome family — but lives in a sibling template because the table layout has no overlap with the binder dressing.

---

## Locked Design Decisions

The prototype iterated on every one of these; what's written here is what the team settled on after the workshop A/B/C/D testing.

### Spread mode: 3D page-flip choreography

The Spread view is the headline interaction. Two pages of the binder are visible at once; navigating to the next/previous spread plays a horizontal page-flip animation as if the user is turning a physical page.

- **Drag-to-flip.** The user grabs the outer edge of either page (an 80px-wide slim drag zone) and pulls toward the spine. The page rotates in real time tracking the drag. Releasing past the 50% threshold commits the flip; releasing before springs the page back to rest.
- **Arrow-button-to-flip.** A bottom pill-shaped overlay holds Prev / Next buttons + a "Spread N / M" counter. Clicking advances the full 0 → 180° rotation in one shot.
- **Easing.** `cubic-bezier(0.32, 0, 0.18, 1.0)` over 880ms. Soft start, accelerates through 90°, lands clean at 180°. No overshoot — earlier drafts used 1.04 (small overshoot for "plastic settle" feel) but the overshoot scaled with rotation magnitude; full button rotations wobbled ~7° past 180° and read as a flash of OLD content. Drag commits (which only cover the remaining angle) didn't show it. Locking y2 at exactly 1.0 lands every flip flat.
- **Proportional duration on drag commits.** A drag committed at 67% of the way needs the remaining 33% of the curve, not the full 880ms. The commit duration scales with remaining angle, floored at 200ms so micro-commits still feel smooth.
- **Back-face content.** The rotating page's back side shows a clone of the destination page's left side, so the rotation visually carries the user *into* the next spread rather than vanishing into a blank back. The back face uses `backface-visibility: hidden` + an internal `rotateY(180deg)` on the cloned content so the destination only becomes visible past the 90° threshold.

### Page chrome (the binder dressing)

- **Spine.** 56px column down the center of the spread. In Single + Compact modes the spine still renders as a vertical stripe — but as a single continuous binding mechanism running through the stacked pages, not a divider between left and right.
- **Rings.** Three holes per page, aligned along the spine. Threaded visually through the spine column.
- **Page tab.** A small protruding flag on the outer edge of each page labeling the series. Color-keyed per series palette. Position-sticky as the user scrolls so the tab stays visible.
- **Cover.** Front and back covers render in Single + Compact modes; suppressed in Spread (the cover lives as the binder's "edge state" — what you see when the binder is closed; opening it puts you on a page, not the cover).
- **Sleeves.** Each badge sits in a pocket sleeve — a card-shaped container with subtle border, inset shadow, and per-row spacing.
- **Page numbers.** "Page X / Y" stamped in the bottom-center of each page.
- **Bookmark.** A ribbon hanging off the front cover, decorative.

### Presentation toggle (Binder vs Gallery)

The Gallery preset systematically strips every piece of binder dressing — covers, spine, rings, page tabs, page numbers, bookmark — leaving just the sleeves in a clean grid. CSS scoped under `[data-binder-presentation="gallery"]` does the work; no separate templates. Switching presentation is a single attribute flip on the binder root, no DOM rebuild.

### Cross-view navigation

- **List → Binder.** Each list row's "View ->" deep-links to its binder card (`#card-NNNN`); the controller shows the binder and the card scrolls/flips into view. (The prototype's reverse `#row-NNNN` jump was dropped in production — the binder links out to badge detail pages instead, via each series header.)
- **Detail links.** Both views link out to badge detail pages: the List via each row's series name, the Binder via each series header.
- **Persistent state.** Mode + presentation are not yet persisted across sessions in the prototype; production extraction should consider localStorage or user-preference persistence.

---

## Technical Learnings

The Binder workshop surfaced a half-dozen technical pitfalls that future Surfaces (and the eventual Binder code extraction) should plan for. The most important live below; the comments in [`templates/design/binder_preview.html`](../../templates/design/binder_preview.html) hold the full case-by-case rationale.

### preserve-3d makes z-index unreliable

`pages-stage` uses `transform-style: preserve-3d` so the 3D page-flip rotation works. Inside a preserve-3d context, browsers prefer 3D Z-position over CSS z-index for paint ordering. Two elements at the same Z (e.g., both spreads at `translateZ(0)`) can sort by either z-index or DOM order depending on the browser. **The fix: give the active spread a sub-pixel `translateZ` (we use `0.5px`).** It's invisible (0.02% perspective scaling, well under sub-pixel) but is a real Z difference that every browser respects.

DOM order is kept as a belt-and-suspenders fallback: the active spread is appended to the end of `pages-stage` on every state change. When the user exits Spread mode, the original DOM order is restored so Single/Compact/Gallery views show spreads in their logical sequence again.

### Spread-mode entry needs an explicit visibility sync

The page's entry animation (`opacity 0 → 1` over 650ms via the `is-visible` class added by an `IntersectionObserver`) fights the spread-mode toggle. When the user switches from another mode into Spread, the outer `setMode` flow runs `syncPageState` on every page *before* `enterFlipMode` sets `is-current` on `spread[0]`. At that moment every spread is still `display:none`, so all pages get `is-visible` stripped — and `spread[0]`'s pages are then invisible until the IntersectionObserver fires async.

**The fix: `enterFlipMode` re-runs `syncPageState` on the now-current spread's pages immediately after applying `is-current`.** The `is-mode-snap` class is still active from `setMode`, so the visibility flip snaps without the 650ms fade.

### Spread-mode exit needs the same sync

Symmetric problem on the way out. `setMode('compact')` runs `syncPageState` over every page *before* `exitFlipMode` restores the DOM order. The sync sees the spread-mode reordered DOM (where `spread[0]` was moved to the end of `pages-stage`), strips `is-visible` off pages that look off-screen, and then the DOM gets restored — leaving page 1 invisible until a scroll event.

**The fix: `exitFlipMode` re-runs `syncPageState` on every page after the DOM restore.** Same `is-mode-snap` trick.

### Flip rotations need their destination pre-snapped to visible

Same family of bug. When a flip starts (drag pointerdown OR arrow button click), the destination spread becomes `is-next` (display:flex), but its pages are still at `opacity:0` until the IntersectionObserver fires. Drag flips hide it because the user almost always takes >650ms to commit, giving the fade time to finish naturally. Button flips (an 880ms rotation) can land while the destination's pages are still mid-fade — the swap reveals partially-faded content, which reads as a flash of "wrong" content.

**The fix: a `snapDestinationVisible` helper called from both the drag pointerdown and the flip-button handlers, applying `is-mode-snap` + `syncPageState` to the destination's pages so they snap to fully opaque before the rotation starts.**

### Cubic-bezier overshoot scales with rotation magnitude

Already covered above. The lesson generalizes: overshoot curves are great for short snappy motion but dangerous on long rotations. If you're using `cubic-bezier(_, _, _, 1.04)` for a flip-style animation, calculate the visible overshoot at maximum amplitude (`magnitude × 0.04`) and verify it's within the surface's tolerance. For full-180° page flips the answer was "no" — 7.2° past horizontal is a visible wobble.

### Corner notches must layer above blueprint overlays

The Frame's tier-tinted corner-notch diamonds sit at the four corners of every card. The Binder's blueprint / unearned overlays (fabricating banner, construction line, blueprint mark, hatched veil) historically sat at z-indices 4–6 — same range as the corner notches at z-index 5. **The fix on the Frame side: bump corner-notch z-index to 10**, well above any blueprint element. The Binder consumes the Frame as-is, so this fix lives in `frame.css`, not in the binder workshop.

---

## Reference Implementations

- **The workshop** ([`/design/binder/`](../../templates/design/binder_preview.html)): the canonical interactive prototype with all six views, the page-flip choreography, both presentation modes, drag + arrow navigation, the cross-link to the list view, every chrome variant. Until the full extraction happens this is the source of truth.
- **The list view** ([`/design/badge-collection/`](../../templates/design/badge_collection_list.html)): the sortable / filterable list pendant. Bidirectionally deep-linked to the workshop.

---

## Anti-Patterns

Things the workshop intentionally avoided, that future extractions and production mounts should also avoid:

- **Photoreal leather / parchment / wood-grain.** Skeumorphism breaks the four-primitive identity test (see [visual-identity.md anti-ref #2](visual-identity.md#5-what-we-are-not) and #7). The Binder is a *conceptual* binder, not a literal one. Chrome reads as "binder" through silhouette, layout, and material *suggestion* — never texture replication.
- **Accordion / tabbed-pane UIs labeled as a "binder".** A binder isn't an accordion. The flip animation matters; the sleeve grid matters; the chrome matters. An accordion of badge series with a binder skin is not the Binder.
- **A wall of identical empty-placeholder cards.** The "I haven't earned that one yet" gap is part of the collector's pull, but the gap needs to be a *named slot*, not a `[ ? ]` repeated 50 times.
- **Tier-themed page colors.** Pages stay neutral (paper-toned). Tier identity lives on the Frames inside the sleeves, not on the pages around them. Coloring pages by tier loses the album-ness — pages are *substrate*, Frames are *content*.

---

## Open Threads

- **Full code extraction.** The prototype currently lives as a single ~3000-line workshop template. When the Binder gets a real product mounting point, follow the Frame extraction model: split into `templates/components/binder.html` + sub-partials, dedicated `static/css/components/binder.css`, JS controller as `window.PlatPursuit.Binder`, test harness at `/design/binder-component/`, prototype stays alive at `/design/binder/`. The technical learnings above are the contract; the workshop is the visual reference.
- **Production mounting point.** Where the Binder ships first is the next decision. Candidates: the Logbook hero, a dedicated `/my-pursuit/collection/` route, the Badge Gallery rebuild named in [visual-identity.md Open Threads](visual-identity.md#open-threads). All three are plausible; pick when the gamification Phase 1 work tells us what surface the user lands on first.
- **Data contract.** The workshop hand-builds spread data in [`core/views.py:BinderPreviewView`](../../core/views.py); a production extraction needs a real `Badge.to_binder_context(user)` adapter (sibling to the deferred `Badge.to_frame_context(user)` adapter in the Frame doc). Pagination, lazy-load, and series-grouping rules all live in that adapter.
- **State persistence.** Mode + presentation aren't persisted in the prototype. Production extraction should decide: localStorage (simple, no backend cost), user-preference (consistent across devices), or per-session (no persistence at all). The Frame's flip state has the same open question.
- **Page-tab interaction.** The page tabs are currently labels only. A natural extension is making them jump-to-series anchors so a long binder is scannable. Defer until the production mount tells us how many series the user typically has.

---

## Related Docs

- [Visual Identity](visual-identity.md) — Binder appears in §3 as the "Album" concept; this doc is the implementation reference.
- [Product Identity](product-identity.md) — strategic frame for the badge collection system the Binder displays.
- [Frame Component](../reference/frame-component.md) — the primitive the Binder arranges. Every sleeve is a Frame.
- [Design System](../reference/design-system.md) — site-wide tokens the Binder coexists with.
