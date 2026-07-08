# Badge Medallion + the Collection Case

The shipped presentation of a badge on `/collection/`: the badge as a **precious OBJECT**, not the
trading-card [Frame](frame-component.md). The badge art is already a self-contained laurel-framed
medallion (backdrop + main + foreground layers), so the Frame's rectangular card chrome was
double-framing a round object. The decision was validated at `/design/badge-presentation/`.

> **Scope.** This is currently collection-only. Game detail, badge detail, and share cards still use
> the Frame; a site-wide Frame -> Medallion migration (+ a visual-identity constitution update) is a
> planned follow-up, not done.

## The component

`templates/components/badge_medallion.html` + `static/css/components/badge-medallion.css`.

- **Reads the SAME frame dict as `components/frame.html`** (from `build_badge_frame`), so it's a
  drop-in. Keys used: `tier`, `state`, `series_name`, `art_layers` (a full-URL, filtered list:
  `[backdrop, main[, foreground]]` — a badge with no custom art has NO foreground), `stages_done`,
  `stages_total`, `dom_id`. Optional include params: `extra_class`, and **`no_id`** (pass `no_id=True`
  to suppress the `dom_id` anchor when the same frame renders in more than one place — see the
  duplicate-ID gotcha).
- **Sizing** via `--sz` on `.pp-med` (the container sets it per breakpoint). The art lives in a square
  `.pp-med__stage`; the "X / Y" count sits in normal flow *below* it (never over the art).
- **Material weight** — `.pp-med__art` carries a stacked `drop-shadow` filter (top rim-light + crisp
  bottom edge + soft cast) that reads the badge as a *raised metal object*, not a flat sticker.
  `drop-shadow` (not `box-shadow`) traces the PNG silhouette, so it works on any badge shape.
- GPU-only motion; `prefers-reduced-motion` honored.

### States (all pure CSS on the same art)
| State | Treatment |
|-------|-----------|
| `earned` | Full colour + tier aura; hover light-catch (glint sweeps the whole medallion) + lift |
| `in_progress` | **Dark waiting mount — identical to `unearned`** + **rising-colour subject** + a **cool** multi-bar meter below |
| `unearned` | Dark grayscale mount + ghosted subject silhouette (a named, waiting slot), **no meter, no fill** |
| `maintenance` | Tarnished base + **rising-colour (restored) subject** + **warm** multi-bar meter + "Lapsed" chip |

**Earned is the only fully-bright state.** In-progress deliberately wears the *same* dark mount as
unearned so "done vs not done" reads instantly across a shelf; the two are told apart by the **meter and
the rising-colour fill** (in-progress has both, unearned has neither), not by brighter base art.

### Edition + earn marks (`show_ids`)
Pass **`show_ids=True`** and the medallion prints a small line under the count: the badge's **set number**
(`#0042`, every badge has one — muted) and, for earned badges, the permanent **earn rank** (`7th`, the
Nth profile to earn this tier — glows in the tier accent). Both come from the frame dict (`set_number`,
`engraving_rank`), so it's read-at-a-glance without opening the badge. Passed by the Case shelf, Gallery
cells, and Showcase (there the redundant `N/N` count is hidden); **not** the tiny Chase strip or the
detail modal (which lists both in its full stats grid). The Case is sorted by `set_number` by default
(series stay together as consecutive groups of 4), so the printed numbers read in order down a shelf.

### Rising-colour fill
In-progress + maintenance overlay a **full-colour copy of ONLY the subject** (`art_layers.1`, the main
layer) that reveals **from the bottom up to `progress_pct`** — the badge visibly "colours in" as you
complete it. It's `.pp-med__fill` (a mask-clipped div) at `z-index: 2` — above the darkened base subject,
below any foreground (`.pp-med__l:nth-child(3)` is bumped to `z-index: 3`). For maintenance the base
tarnish lives on the individual `.pp-med__l` layers (not the whole `.pp-med__art`), so the fill escapes
it and reads as **restored** colour rising over a tarnished base.

### The multi-bar meter
In-progress + maintenance render a **segmented multi-bar** below the art (echoing the job page's tier
ladder, `.pgl`): **one rounded cell per platinum/100%** toward the badge, filled up to `stages_done`.
The cells come from `frame['segments']` (a bool list built in `build_badge_frame`, only when countable:
`0 < stages_total <= SEGMENT_CAP`, cap = **12**). Above the cap `segments` is omitted and the template
renders **one smooth bar** off `progress_pct` (`pp-med__meter--smooth`); the "X / Y" count carries the
detail. Cool tier colour for in-progress (`--meter-c: var(--med-c)`), warm amber for maintenance. A
per-badge requirement is **Platinum** for bronze/gold tiers, **100%** for silver/platinum (migration
`0046` tier choices) — the detail modal labels this correctly. (This replaced an earlier segmented
*ring* that wrapped the badge; the ring detracted from the object, so it was moved to a bar below.)

## The Case

`templates/components/collection_case.html` + `static/css/components/collection-case.css`, wired by
`static/js/collection.js` (`initCase` + `initDetail`). Replaced the binder on `/collection/`.

- **Set tabs -> a shelf per set** (badge type). Within a shelf, badges **group by series**: the 4 tiers
  (bronze -> platinum) stay bound in one `.pp-case__group` panel and never split across a row.
  `collection_service` emits `groups` per set (grouped by `series_slug`, which the model confirms
  "groups tiers of the same series").
- Responsive: 2x2 within a group on mobile -> 4-in-a-row with width; one -> two groups per row.

## The three views (Case / Gallery / List)

`/collection/` exposes the same flat badge set three ways, switched by the view toggle (a generic
`data-collection-view` tablist wired by `initViewToggle`). All three read one context build; the Gallery
and List reuse `list_badges` (already flattened by `_flatten_for_list`), so switching views is free.

| View | What it's for | Presentation |
|------|---------------|--------------|
| **Case** (`collection_case.html`) | The display piece — browse what you have + what's missing | Set tabs -> series-grouped shelves of medallions, plus Showcase + Chase |
| **Gallery** (`collection_gallery.html`) | The **visual** hunting tool — "show me all bronze", "only the ones I own" | A flat, filterable/sortable **wall of medallions**; tap -> detail modal |
| **List** (`collection_list.html`) | The **data** hunting tool — dense scan/sort by rarity, rank, set # | A sortable/filterable table (column-header sort) |

- **Gallery + List share one filter/sort engine** in `collection.js` (`stateMatches`/`elMatches`/
  `sortValue`/`compareBy`/`wireFilterChips` at module scope). They filter identical `data-*` attributes;
  only the sort UI differs (Gallery = a `key:dir` `<select>`; List = clickable column headers with
  `aria-sort`). `initGallery` and `initList` are thin wrappers over the shared primitives.
- The tier/state/set **filter chips + search + empty-state + count** markup is shared — the Gallery
  reuses the List's `.pp-list__toolbar` / `__chip` / `__search` / `__stats` / `__empty` classes; its own
  CSS file only adds the sort control + the medallion grid + captions.

## The detail ("pick it up")

Tap a medallion -> `CollectionBadgeModalView` (`/collection/badge/<id>/`) fetches **one** badge's detail
(single-hero `build_badge_frame`, whale-safe) -> a modal with the medallion big + full stats. Focus
trap + Escape; the slot keeps its badge-page `href` as a **no-JS fallback**.

**"Turn it in your hand"** (`initTilt`): the big medallion tilts in 3D toward the cursor with a
light-tracking glare (a JS-injected `.pp-med__glare`) and springs back on leave. It's a hover affordance,
gated on `(any-hover: hover) and (any-pointer: fine)` (NOT the plain `hover`/`pointer` — those check the
*primary* device, which is `coarse` on touchscreen laptops even with a mouse) and disabled under
`prefers-reduced-motion`. The cursor→rotation rect is read off the untransformed scene so the tilt doesn't
feed back into its own bbox, and the rotation is applied as an **inline** transform so it beats the base
`:hover` lift's specificity.

**Layered depth (parallax) — the load-bearing architecture.** The 3D lives in
`@media (any-hover) and (any-pointer) and (no-preference)`:
- **Perspective on the SCENE (`.pp-med__stage`), rotation on the CARD (`.pp-med__art`).** The card is the
  layers' *direct* parent — that's mandatory. Rotating the stage instead leaves the layers a level too
  deep and `preserve-3d` flattens them.
- **Two-plane look:** the backdrop (laurel) stays at Z0; the subject, its rising fill, and the glare lift
  together to `translateZ(40px)`. The **foreground layer is hidden** on the hero so the subject owns
  center stage. The raised subject carries a soft `drop-shadow` (a filter on the *leaf* is safe) to cast
  onto the backdrop so it reads as *mounted*, not floating.
- The flat material `drop-shadow` thickness is dropped here (`filter: none` on the card) — a filter
  flattens 3D, and the parallax supplies the depth instead. Reduced-motion / touch keeps the flat, thick,
  static badge (and the dialog stays a scroll container there).

The companion **flip** (to a back face) is still a planned follow-up.

## Gotchas and Pitfalls

- **`art_layers` are full URLs and a *filtered* list.** Render raw (`{{ layer }}`, never `{% static %}`
  — double-prefix) and **loop** rather than hardcode 3 `<img>`s (no-custom-art badges have 2 layers).
  State filters target `nth-child(1)` = backdrop, `nth-child(2)` = subject.
- **The meter cells come from `frame['segments']`, computed server-side** (`build_badge_frame`), NOT in
  the template — Django templates can't loop N times without a filter, so the bool list is prebuilt.
  Change the cap in one place: `frame_service.SEGMENT_CAP`.
- **Only `earned` is bright.** If you touch the state art filters, keep `in_progress` matched to
  `unearned` (they share the dark-mount selectors) — brightening in-progress art breaks the at-a-glance
  "done vs not done" read the meter exists to preserve.
- **Parallax flatteners — the whole reason this was a saga.** `transform-style: preserve-3d` silently
  computes to `flat` (layers "all move together") from THREE distinct places. All three bit us:
  1. **On the element itself:** `isolation: isolate`, non-`none` `filter`, `overflow` ≠ visible,
     `opacity < 1`, `mask`, `clip-path`, or **`will-change: transform`** (it promotes a compositing layer
     that flattens). `.pp-med__stage` has `isolation: isolate` (glint blend) and `.pp-med__art` has the
     material `filter` — both overridden (`isolation: auto`, `filter: none`) in the 3D block. Do NOT add
     `will-change` to the card.
  2. **On ANY ancestor of the 3D scene:** a `filter` / `overflow` ≠ visible / `will-change: transform`
     re-flattens the whole subtree. The dialog's own `overflow: auto` (scroll) + base `will-change:
     transform` did this — both are overridden to `visible` / `auto` on the dialog in the 3D block.
  3. **On a CHILD:** `mix-blend-mode` on a descendant isolates a compositing group that flattens the
     card. The JS-injected `.pp-med__glare` originally had `mix-blend-mode: screen`; injecting it *into
     the card* flattened the very depth it sat in. The glare now uses a plain (no-blend) highlight.
  If depth ever disappears again, walk the scene → card → layers chain AND every ancestor for one of these.
- **The rising-colour fill img is `.pp-med__fill-l`, NOT `.pp-med__l`.** It's nested in `.pp-med__fill`
  and deliberately outside the `.pp-med__l` class so the state darkening filters (`… .pp-med__l:nth-child(2)`)
  don't grey it out — it must stay full-colour. Same reason maintenance tarnish is on `.pp-med__l`, not
  `.pp-med__art`.
- **Multi-line component comments must be `{% comment %}`**, not `{# #}` (which is single-line only and
  leaks to the page).
- **Counts align across states via the meter's reserved space.** Earned/unearned give the count a full
  top margin; when a meter precedes it (`.pp-med__meter + .pp-med__count`) the meter has already
  supplied that space, so the margin shrinks. If you change the meter's height/margin, re-check both so
  the "X / Y" labels stay on one line across a mixed-state row.
- **`dom_id` must be emitted exactly once per badge.** The same earned/in-progress frame renders in its
  shelf AND in the Showcase/Chase/Gallery — but the `#card-<id>` deep-link anchor lives on the **shelf**
  medallion only. Showcase, Chase, and every Gallery cell pass **`no_id=True`**; forgetting it produces
  duplicate IDs and sends the deep-link jump to the wrong node.
- Retiring the binder from the collection did **not** delete `binder.html`/`binder.css`/`binder.js` —
  they're still used by `/design/binder/`, which is also why `collection_service.spreads`/`pages` are
  **still built** (the binder lab consumes them). They are not dead despite the Case not using `spreads`.
- **Three server-rendered views = every badge in the DOM ~2x** (Case shelves + Gallery wall, plus the
  List rows). The count is **catalog-bounded, not per-user** (all live badges show for everyone), so it's
  not a whale-safety issue, and `loading="lazy"` + `hidden` keeps inactive/off-screen images from
  fetching. But at a large badge catalog the raw node count grows; if that ever bites, render the Gallery
  lazily (build its DOM only on first switch) rather than up front.

## Related Docs

- [Frame Component](frame-component.md) — the trading-card housing the Medallion supersedes on the collection.
- [Binder Surface](../design/binder-surface.md) — the retired binder (now a design lab only).
- [Visual Identity](../design/visual-identity.md) — the primitive constitution.
- [Premium Motion Patterns](motion-patterns.md) — the motion recipes + GPU gotchas.
