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
- GPU-only motion; `prefers-reduced-motion` honored.

### States (all pure CSS on the same art)
| State | Treatment |
|-------|-----------|
| `earned` | Full colour + tier aura; hover light-catch (glint) + lift |
| `in_progress` | Faint materializing subject + **cool** tier-coloured segmented ring |
| `unearned` | Dim grayscale mount + ghosted subject silhouette (a named, waiting slot) |
| `maintenance` | Tarnished full art + **warm** segmented ring (red = broken, amber = repaired) + "Lapsed" chip |

### The segmented ring
In-progress + maintenance show a **segmented** progress ring: **one segment per platinum/100%** toward
the badge (`--done`/`--total` = `stages_done`/`stages_total`). **Capped at 12** — above that,
`pp-med--ring-smooth` renders a smooth arc instead (segments stop being countable), and the "X / Y"
number carries the detail. A per-badge requirement is **Platinum** for bronze/gold tiers, **100%** for
silver/platinum (migration `0046` tier choices) — the detail modal labels this correctly.

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
trap + Escape; the slot keeps its badge-page `href` as a **no-JS fallback**. The premium
"turn it in your hand" interaction (cursor tilt + flip) is a planned follow-up.

## Gotchas and Pitfalls

- **`art_layers` are full URLs and a *filtered* list.** Render raw (`{{ layer }}`, never `{% static %}`
  — double-prefix) and **loop** rather than hardcode 3 `<img>`s (no-custom-art badges have 2 layers).
  State filters target `nth-child(1)` = backdrop, `nth-child(2)` = subject.
- **The ring sits BEHIND the art** (`z-index: 0`) so the badge overlaps its inner edge — intentional
  "ring around the object" depth. Don't shrink the ring to hug the art or it hides behind it.
- **`radial-gradient(circle, ...)` masks default to `farthest-corner`**, so mask percentages map to the
  element *corner*, not its radius — a too-tight band lands outside the circle and vanishes. Use
  `closest-side` if you need radius-mapped percentages.
- **Multi-line component comments must be `{% comment %}`**, not `{# #}` (which is single-line only and
  leaks to the page).
- **The count clears the ring's overhang** via a `--sz`-proportional top margin; the group title clears
  it via its bottom margin. If you resize the ring, re-check both.
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
