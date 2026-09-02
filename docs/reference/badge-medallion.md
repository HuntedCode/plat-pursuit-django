# Badge Medallion + the Collection Gallery

The shipped presentation of a badge on `/collection/`: the badge as a **precious OBJECT**, not the
trading-card [Frame](frame-component.md). The badge art is already a self-contained laurel-framed
medallion (backdrop + main + foreground layers), so the Frame's rectangular card chrome was
double-framing a round object. The decision was validated at `/design/badge-presentation/`.

> **Scope.** This is currently collection-only. Game detail, badge detail, and share cards still use
> the Frame; a site-wide Frame -> Medallion migration (+ a visual-identity constitution update) is a
> planned follow-up, not done.

## The component

`templates/components/badge_medallion.html` + `static/css/components/badge-medallion.css`.

- **Reads the SAME frame dict as `components/frame.html`** (from `build_badge_frame` (DELETED 2026-08)), so it's a
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
| `unearned` | Dark grayscale mount + ghosted subject silhouette (a named, waiting slot), **no fill** (and no meter unless the surface passes `always_meter`) |
| `maintenance` | Tarnished base + **rising-colour (restored) subject** + **warm** multi-bar meter + "Lapsed" chip |

**Earned is the only fully-bright state.** In-progress deliberately wears the *same* dark mount as
unearned so "done vs not done" reads instantly across a shelf; the two are told apart by the **meter and
the rising-colour fill**, not by brighter base art. On a surface passing **`always_meter`** both draw a
meter, so there the **fill** alone separates them.

### Earn mark (`show_ids`)
Pass **`show_ids=True`** and, for earned badges, the medallion prints the permanent **earn rank**
(`7th`, the Nth profile to earn this tier — glows in the tier accent) under the count, from the frame
dict's `engraving_rank`. Passed by the Case shelf, Gallery cells, and Showcase (there the redundant
`N/N` count is hidden); **not** the tiny Chase strip or the detail modal (which lists it in its full
stats grid). (The set number that used to share this line was removed 2026-08-23 with the
`set_number` field — the new system never assigned the numbers.)

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
The cells come from `frame['segments']` (a bool list built in `build_badge_frame` (DELETED 2026-08), only when countable:
`0 < stages_total <= SEGMENT_CAP`, cap = **12**). Above the cap `segments` is omitted and the template
renders **one smooth bar** off `progress_pct` (`pp-med__meter--smooth`); the "X / Y" count carries the
detail. Cool tier colour for in-progress, warm amber for maintenance.

**`always_meter`** opts a surface into a PERMANENT bar, drawn in every state including `earned` — used by
the two collection walls, where a wall of nothing but badges makes the bar a column you read down. A full
one takes `.is-full` (a brighter fill + a wider glow) so a finished bar is distinguishable from a gauge
pinned at max. Two traps, both of which have bitten: the glow must sit on the **track**, since `--smooth`
sets `overflow: hidden` and clips a shadow declared on the fill; and `--meter-c` is defaulted on the
**base** `.pp-med` with a literal fallback, because an undefined custom property invalidates every
`color-mix()` referencing it and renders the bar EMPTY rather than uncoloured. Collection frames never set
`segments`, so those walls always take the smooth branch. A
per-badge requirement is **Platinum** for bronze/gold tiers, **100%** for silver/platinum (migration
`0046` tier choices) — the detail modal labels this correctly. (This replaced an earlier segmented
*ring* that wrapped the badge; the ring detracted from the object, so it was moved to a bar below.)

## The Gallery (the single view)

`templates/components/collection_gallery.html` + `static/css/components/collection-gallery.css`, wired by
`static/js/collection.js` (`initGallery` + `initDetail`). `/collection/` is ONE view now: a flat,
filterable / sortable / searchable **wall of medallions**. The earlier Case + List views were retired —
the Gallery's sort/filter absorbed both "browse what you have" and the dense data scan.

- **The badge set is the viewer's ENGAGED editions** — the live per-platform-group badges (Legacy HD /
  Ultra HD) of every series they hold or have started. `collection_service.build_collection_context` emits
  a flat `list_badges` of Frame dicts; there is **no per-tier grouping** (the grouping-badge system has no
  tiers — each edition is its own badge). Whale-safe: a fixed handful of bulk reads, **no live eval**.
- **Per-edition state is READ from the materialized read-model** — `SeriesBadgeStanding.group_progress`
  (`{edition_key: [cleared, gating]}`, written by the sync's `recompute_standing`), run through the shared
  `badge_xp.edition_display_state`. The **badge-detail modal** derives state through the *same* helper but
  from a *live* eval (it needs the full stage journey), and both reflect the last sync — so a card and its
  modal can't disagree. State: held -> earned (holo when mastered); this edition has partial progress ->
  in_progress + its own progress; else unearned (an edition the viewer has 0% on — the series
  furthest-along would wrongly paint it). Unearned editions are then DROPPED from the wall, unless the
  whole series is untouched (so a series can never vanish entirely). See [badge-backend-rebuild.md](../design/rebuild/badge-backend-rebuild.md)
  for why per-edition progress is a materialized read-model (factual, recompute-from-scratch), not a live eval
  on the wall (it'd be O(engaged series) per load — a whale-scale timeout) nor a request cache.
- **Filter/sort** (`collection.js`, module scope: `stateMatches` / `elMatches` / `sortValue` / `compareBy` /
  `wireFilterChips`): **edition** (Legacy HD / Ultra HD) + **state** (earned / in-progress / not earned)
  chips + a Set `<select>`, plus a `key:dir` sort `<select>` (default `progress:desc`). All operate on the
  cells' `data-*` attributes.
- **The caption is three lines**: series name, then the **edition** in its own tier colour
  (`.pp-gallery__edition`, reading the card's `--tier-c`), then one stat. In-progress cells show their
  "X / Y stages" there (via `data-stages` + `statText`); earned cells show **"N stages · date"** under the
  *Recently earned* sort (composed in `statText` from `data-stage-total` + `data-earned-label`) and the
  rarity grade under the others. The count is NOT repeated under the medallion — both walls pass
  `no_count=True`, since the caption is already the stage figure's home.

## The detail ("pick it up")

Tap a medallion -> `CollectionBadgeModalView` (`/collection/badge/<id>/`) fetches **one** GROUP badge's
detail (`get_badge_detail`, live per-group) and renders the shared `components/group_badge_modal.html` —
the SAME modal the badge-detail page's peek uses — a modal with the medallion big + full stats. Focus
trap + Escape; the cell keeps its badge-page `href` as a **no-JS fallback**.

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
- **The meter cells come from `frame['segments']`, computed server-side** (`build_badge_frame` (DELETED 2026-08)), NOT in
  the template — Django templates can't loop N times without a filter, so the bool list is prebuilt.
  The Frame was replaced by the Badge Medallion; `frame_service` and its `SEGMENT_CAP` were deleted in 2026-08. Medallion composition now lives in `GroupBadge.art_layers()`.
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
- The binder is fully gone as of the 2026-08 staff/design strip-down: the `/design/binder/` lab and its
  files were deleted (nothing in `collection_service` consumed them; the lab built its own spreads).
- **Two server-rendered views = the badge set in the DOM ~2x** (Case shelves + Gallery wall). As of the
  July 2026 discovery restructure the Collection is scoped to the viewer's **ENGAGED series** (series they
  hold or are in-progress on -- see `collection_service._engaged_scope`), so the count is **bounded by the
  viewer's engaged set, not the whole catalog**. `loading="lazy"` + `hidden` keeps inactive/off-screen
  images from fetching. Full-catalog discovery lives on the Browse badge Gallery (server-paginated), not
  here, so this DOM stays bounded even for whales.

## Related Docs

- [Frame Component](frame-component.md) — the trading-card housing the Medallion supersedes on the collection.
- [Binder Surface](../design/binder-surface.md) — the retired binder (design record only; the lab was deleted 2026-08).
- [Visual Identity](../design/visual-identity.md) — the primitive constitution.
- [Premium Motion Patterns](motion-patterns.md) — the motion recipes + GPU gotchas.
