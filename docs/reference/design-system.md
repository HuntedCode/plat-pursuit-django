# Design System Reference

The canonical styling and design reference for PlatPursuit. The dashboard is the reference implementation and the design baseline for the entire site. Every page is being rebuilt from the ground up to match its design language.

This doc covers **site-wide building blocks** (cards, grids, spacing, colors, component patterns). Page-level layout decisions (content width, sidebars, tab systems) are page-specific and not covered here.

## Site-Wide Redesign: Process

Every page goes through a three-part rebuild process:

### 1. Backend Audit

Read the view, queryset, and any services. Identify:

- **Performance issues**: N+1 queries, expensive subqueries, missing annotations, unnecessary prefetches
- **Missing data opportunities**: User-specific context (played status, completion %), personalized data that the new design could surface
- **Cleanup candidates**: Duplicate logic, organic growth that needs refactoring, context data the new design no longer needs

Only rebuild the backend where there's a clear win. Don't touch views that are already clean and performant.

### 2. Frontend Rebuild

Ground-up template rebuild using the dashboard as the literal design target. This is NOT a "re-style" or "add breakpoints" pass. The question to ask is: **"Would this component look at home inside a dashboard module?"** If the answer is no, rebuild it until it does.

Key principles:
- Use the tokens and patterns defined in this doc
- Every piece of data should have a clear visual hierarchy (labels, grouping, contrast)
- Flavor text, personality, and contextual messaging are part of the Platinum Pursuit Standard
- Mobile-first: design for 375px, then expand at `md:` and `lg:`

### 3. Polish

Final audit reviewing every new/modified file against the Platinum Pursuit Standard, responsive compliance, visual cohesion, and interactive polish (hover states, transitions, focus indicators). See CLAUDE.md for the full audit checklist.

---

## Responsive Philosophy

We build **three layouts** per page: mobile, tablet, and desktop. Tailwind classes use mobile-first breakpoints.

| Breakpoint | Width | Prefix | Role |
|------------|-------|--------|------|
| Base (no prefix) | 0-767px | none | Mobile: the smallest designed layout |
| `md:` | 768px+ | `md:` | Tablet: more breathing room, multi-column where appropriate |
| `lg:` | 1024px+ | `lg:` | Desktop: primary design target, full experience |
| `xl:`, `2xl:` | 1280px+, 1536px+ | `xl:`, `2xl:` | Large desktop refinements (optional) |

**Key rules:**
- Base styles must look correct at **375px** (iPhone SE), the narrowest target
- `md:` values should match what previously was the "tablet baseline"
- `lg:` values are the desktop experience
- `sm:` (640px) is available but rarely needed; most layouts jump from mobile to `md:`

**Phone-only refinements (`@media (max-width: 640px)`):** Component CSS (`static/css/components/*.css`)
occasionally needs a tweak that should apply on phones **but not** tablets/desktop, where the mobile-first
`min-width` ladder can't express it (a `min-width` rule adds complexity going up; this removes it going
down). For those, a hand-written `@media (max-width: 640px)` block is the sanctioned escape hatch. It is
desktop-first by nature (overrides the base rule below the phone cutoff), so keep it to genuinely
phone-scoped polish: swipe-to-close modal chrome, the mobile peek scroll cap, collapsing toolbars, and the
badge-detail stage ladder (slimmer gutter/node + stacked meta). Prefer the mobile-first `md:`/`lg:` ladder
for layout; reach for `max-width: 640px` only for these phone-only overrides.

### Legacy Migration Pattern

When converting a page from ZoomScaler to proper responsive:

```
Before: p-5 lg:p-7          (base = tablet, lg = desktop)
After:  p-3 md:p-5 lg:p-7   (base = mobile, md = tablet, lg = desktop)
```

The current base value moves to `md:`, a tighter mobile value becomes the new base, and `lg:` stays unchanged.

---

## Card Anatomy

Every content module uses this card structure:

```html
<div class="card bg-base-200/90 border-2 border-base-300 shadow-lg shadow-neutral">
    <div class="card-body p-3 md:p-5 lg:p-7">
        <!-- Header -->
        <div class="flex items-center justify-between mb-3">
            <h2 class="card-title text-base lg:text-lg font-bold flex items-center gap-2">
                <!-- Icon + Title -->
            </h2>
            <!-- Optional: action link, badge, or count -->
        </div>

        <!-- Content -->
        ...
    </div>
</div>
```

### Card Tokens

| Element | Classes | Notes |
|---------|---------|-------|
| Card container | `card bg-base-200/90 border-2 border-base-300 shadow-lg shadow-neutral` | 90% opacity for definition against page background |
| Card body padding | `p-3 md:p-5 lg:p-7` | Tighter on mobile, generous on desktop |
| Card title | `card-title text-base lg:text-lg font-bold` | No size change at `md:` |
| Title icon | `w-5 h-5` with theme color (e.g., `text-primary`) | Consistent 20px across all sizes |

### Card Variants

**Page structure = STACKED chrome + FREE content (site-wide rule, 2026-07).** The chrome is carded -- an accented page-header card, then optional stat/education/toolbar cards, each `mb-3`/`mb-4` apart. The main content (grids, lists, tab panels) is NOT wrapped in an outer card; it flows free below the chrome, like the Collection (header card -> free toggle -> free Case/Gallery). Never one bordered box swallowing the header + sections, and never an outer card around the content grid -- both trap long / paginated (infinite-scroll) pages in an ever-growing border. Item-level cards *inside* the content (game/badge cards, tiles, the empty state) are fine; it's the outer content wrapper that must not be a card.

**Content module cards** (dashboard modules, detail page sections): Full card tokens with `p-3 md:p-5 lg:p-7` padding.

**Compact utility cards** (toolbars, filter bars, page headers): Tighter padding `p-3 md:p-4`. These are control surfaces, not content display, so they should not feel bloated.

**Page header cards**: Add a left accent border for visual identity: `border-l-4 border-l-primary`. Include an icon, title, contextual subtitle, and any page-level controls.

**Browse/item cards** (game cards, badge cards in grids): Minimal padding `p-1 md:p-1.5`. Shadow starts at `shadow-md`, with colored glow on hover: `hover:shadow-lg hover:shadow-{color}/30 hover:border-{color}`.

**Cards over game artwork backgrounds** (game detail, badge detail, any page where game art shows behind cards): Use `bg-base-200/80` for outer cards and `bg-base-300/35` for inner stat cells (`hover:bg-base-300/50`). The artwork varies dramatically between games (bright vs dark, busy vs simple), so these opacities balance readability against letting the art show through. Do NOT use the standard `/90` or `/70` tokens on these pages as they are either too opaque (hiding the art) or too transparent (poor contrast on bright artwork).

---

## Inner Elements

### Section Headers

Dashboard-style labels for groups within a card:

```html
<h4 class="text-xs font-semibold text-base-content/60 uppercase tracking-wider mb-2 flex items-center gap-1.5">
    <svg class="w-3.5 h-3.5">...</svg>
    Section Label
</h4>
```

### Stat Cells (number + label)

Used for trophy counts, stats grids, tier breakdowns. Uses the **theme-safe contrast** pattern so cells remain visible on both light and dark themes.

```html
<div class="bg-white/[0.03] border border-base-content/5 rounded-lg p-1.5 md:p-3 text-center">
    <div class="text-lg md:text-xl lg:text-2xl font-black text-primary">42</div>
    <div class="text-xs text-base-content/50 font-medium">Label</div>
</div>
```

For smaller stat cells (summary rows):

```html
<div class="flex flex-col items-center gap-0.5 p-1 md:p-2 rounded-lg bg-white/[0.03] border border-base-content/5">
    <span class="font-bold text-sm lg:text-base">99</span>
    <span class="text-xs text-base-content/50">Label</span>
</div>
```

### Inner Panels (grouped stats, analytics sections)

Used in premium analytics modules where multiple stat groups sit side by side.

```html
<div class="bg-white/[0.03] border border-base-content/5 rounded-lg p-2 md:p-3 lg:p-4">
    <!-- Panel content -->
</div>
```

### List Item Rows (icon + text + metadata)

Used for game lists, badge lists, activity feeds, leaderboard entries.

```html
<div class="flex items-center gap-2 md:gap-3 p-2 rounded-lg bg-white/[0.03] border border-base-content/5 hover:bg-white/[0.06] transition-colors">
    <!-- Icon (shrink-0) -->
    <div class="flex-1 min-w-0">
        <!-- Text content with line-clamp-1 pr-1 -->
    </div>
    <!-- Trailing metadata (shrink-0) -->
</div>
```

---

## Component Patterns

### Page Header Card

Every rebuilt page should have a header card that establishes context and houses page-level controls.

```html
<div class="card bg-base-200/90 border-2 border-base-300 border-l-4 border-l-primary shadow-lg shadow-neutral mb-3">
    <div class="card-body p-3 md:p-4 lg:p-5">
        <div class="flex items-center justify-between gap-3">
            <div class="min-w-0">
                <div class="flex items-center gap-2 flex-wrap">
                    <svg class="w-5 h-5 md:w-6 md:h-6 text-primary shrink-0">...</svg>
                    <h1 class="text-lg md:text-xl lg:text-2xl font-bold">Page Title</h1>
                    <span class="badge badge-sm badge-ghost">Context Badge</span>
                </div>
                <p class="text-xs md:text-sm text-base-content/50 mt-0.5 italic pr-1">
                    Dynamic, contextual subtitle with personality.
                </p>
            </div>
            <div class="flex items-center gap-1.5 shrink-0">
                <!-- Page-level toggle buttons -->
            </div>
        </div>
    </div>
</div>
```

**Subtitle guidelines**: The subtitle should be dynamic and contextual, not generic. Change based on active filters, current sort, result count, or user state. Add personality (flavor text, playful phrasing) per the Platinum Pursuit Standard.

### Tab Group / View Switcher (site-wide standard, 2026-07)

**The one sanctioned tab treatment.** Any in-page tab group or view switcher -- 2-way (Case/Gallery, Series/Gallery) or multi-tab (Jobs/Radar/Contracts) -- uses the shared **`.pp-switch`** component (`static/css/components/switcher.css`): one bordered container holding transparent chips, active chip tinted, an icon per chip. Right-aligned in its own `flex items-center justify-end mt-5` row -- `mt-5` is the standard top gap above the switcher (it sits below the page-header card / whatever chrome precedes it). Used site-wide: Career, Badges (Series/Gallery), Collection. Unified 2026-07 from three near-identical class systems (`.lab-view-tab` / `.pp-collection__view-chip` / `.pp-vtoggle`, all retired).

```html
<div class="flex items-center justify-end mt-5">
  <div class="pp-switch" role="tablist" aria-label="...">    <!-- segmented container -->
    <button class="pp-switch__chip is-active" role="tab" aria-selected="true">
      <svg>...</svg>Tab A                                    <!-- ~15px Lucide icon + label -->
    </button>
    <button class="pp-switch__chip" role="tab" aria-selected="false"><svg>...</svg>Tab B</button>
  </div>
</div>
```

- **Container `.pp-switch`**: `inline-flex; flex-wrap:wrap; gap:6px; padding:4px; border-radius:8px; border:1px solid var(--pp-border); background:color-mix(in oklab, var(--pp-bg-1) 60%, transparent)`.
- **Chip `.pp-switch__chip`**: transparent at rest (`color:var(--pp-text-mute); border:1px solid transparent; border-radius:5px; padding:6px 14px`, `7px 18px` at `md:`); `text-decoration:none` (works for `<a>` chips); hover -> `color:var(--pp-text)`; `:active` presses `translateY(1px)`.
- **Active chip `.is-active`**: `color:var(--pp-text); font-weight:700; background:color-mix(in oklab, var(--pp-primary) 16%, transparent); border-color:color-mix(in oklab, var(--pp-primary) 50%, var(--pp-border))`. Settles FLAT -- no resting glow. The one-time activation bloom (`.pp-tab-ignite` via `PlatPursuit.igniteTab`) fades to flat.
- **Icon**: a ~15px Lucide stroke glyph before the label. A trailing count badge (e.g. Career's claimable count) sits after the label.
- **Mini-bar copies**: wrap each chip's label in `<span class="pp-switch__lbl">` so it collapses to icon-only on mobile (`.pp-minibar__controls .pp-switch__lbl { display:none }`).
- **Behavior**: wire with `PlatPursuit.wireTablist(chips, {onSelect})` (roving tabindex + Arrow/Home/End; `{manual:true}` for HTMX `<a>` chips). Directional panel slide via `PlatPursuit.slideViewIn`. → [js-utilities](js-utilities.md), [motion-patterns](motion-patterns.md).
- **A11y**: `role="tablist"` on the container, `role="tab"` + `aria-selected` on chips, `aria-controls` -> the panel; `:focus-visible` ring in `--pp-primary`.
- **Distinct siblings (NOT `.pp-switch`)**: Career's `.jlayout__btn` (rounded-pill Dossier/Sheet toggle) and the Case's `.pp-case__set-tab` (completion-ring set filters) are their own components.

### Progression ladder (.pgl)

Shared primitive for ANY tier/rank progression (`static/css/components/elements.css`). A row of rungs: reached rungs fill the accent, the current rung fills partway + widens + glows, upcoming rungs stay dim. Accent-parameterized — set `--pgl-accent` on the ladder (or per-rung `--rung-c`) so it takes on the feature's colour. Rung fill is driven by `--f` (0–100%), state by `.is-on` (reached) / `.is-current`.

```html
<div class="pgl pgl--static">           <!-- --static = resting fill only, no mount choreography -->
  <div class="pgl__track">
    <span class="pgl__rung is-on" style="--rung-c: #cf9160; --f: 100%;"></span>
    <span class="pgl__rung" style="--rung-c: #b9c6d6; --f: 0%;"></span>
  </div>
</div>
```

- **Consumers**: Pursuer rank ladder (Career hero, `.pgl--rank`), job prestige ladder (job detail), claim ceremony rank-up (`.is-charging`), badge tier ascent rail (`.pgl--static`). Reuse it — don't re-roll a ladder.
- **`.pgl--static`**: disables the entrance choreography (rung draw cascade, current-rung bloom, division ticks) for a resting filled rail — use inside HTMX swap islands or wherever you just want the meter.
- **Reduced motion**: the mount animations are gated; the resting filled state is the reduced-motion fallback.

### Filter/Search Toolbar Card

Compact card for search, sort, and filter controls. Collapsible drawer for secondary filters.

```html
<div class="card bg-base-200/90 border-2 border-base-300 shadow-lg shadow-neutral mb-3">
    <div class="card-body p-3 md:p-4">
        <!-- Row: search input (with icon) + sort dropdown + submit button -->
        <div class="flex flex-col md:flex-row gap-2 md:gap-3">
            <div class="relative flex-1">
                <svg class="w-4 h-4 absolute left-3 top-1/2 -translate-y-1/2 text-base-content/40 pointer-events-none">...</svg>
                <input class="input input-bordered input-sm md:input-md w-full pl-9 md:pl-10" />
            </div>
            <select class="select select-bordered select-sm md:select-md w-full md:w-48 lg:w-56">...</select>
            <button class="btn btn-sm md:btn-md btn-primary w-full md:w-auto">...</button>
        </div>

        <!-- Collapsible filters -->
        <details class="collapse collapse-arrow bg-white/[0.03] rounded-lg mt-2 border border-base-content/10">
            <summary class="collapse-title min-h-0 py-2 px-3 text-sm font-semibold text-base-content/60">
                Filters
            </summary>
            <div class="collapse-content px-3 pb-3 pt-0">
                <!-- Filter sections separated by border-t border-base-content/10 -->
            </div>
        </details>
    </div>
</div>
```

> The snippet above is the **legacy DaisyUI** shape. The rebuilt house toolbar uses `.pp-bgal__*` / `.pp-gbrowse__*` (see `game_list.html`, `components/{browse-gallery,game-browse}.css`). Follow that on rebuilt pages, plus the shared premium features below.

### Search toolbar — shared premium features (2026-07)

Two shared helpers (in `static/js/utils.js`) give **any** search toolbar — browse or bespoke — the same premium behavior. The `.pp-search-*` visuals live in shared CSS (`components/browse-gallery.css`) keyed on `[data-search-wrap]` + `.has-value`/`.is-searching`, so they work on any positioned wrapper. **Live on: Browse Games, Badge list (Series + Gallery), Collection Gallery, Career Contracts.** (`browse-filters.js` calls the same helper internally; bespoke controllers call it directly.)

**Automatic — `/` + ⌘K/Ctrl+K focus:**
- A single global handler (utils.js) focuses the first *visible* `[data-page-search]` input (hidden inactive-tab inputs are skipped), falling back to a `[data-browse-form]` text/search input — so all ~18 `browse-filters.js` pages get it for free. On bespoke/tabbed pages, add `data-page-search` to the primary search input. `/` is skipped while typing in an input/textarea/select/contenteditable; ⌘K always fires.

**Opt-in affordances — `PlatPursuit.wireSearchField(input, {onClear})`:**
- Call it once per search input. It wires the `.has-value` toggle, the `[data-search-clear]` clear button, and Escape-to-clear (both call `onClear`), and returns `{ setBusy(bool) }` for the in-flight spinner. `onClear` should re-run/re-submit your filter.
- Markup (inside a `[data-search-wrap]` wrapper): `<span class="pp-search-spin">`, `<button class="pp-search-clear" data-search-clear tabindex="-1">`, `<kbd class="pp-search-kbd">/`. Visibility is state-driven — only one of {hint, clear, spinner} shows at a time.
- Call `setBusy(true/false)` around your request for the spinner (skip it for client-side/instant filters — Collection Gallery does). Browse forms keep `data-live-search` for live filtering.

**Active-filter chips** (per-page, not auto — each page's filters + labels differ). Dismissable pills of the applied *panel* filters (search + platform/regions/sort are excluded), each with a remove-URL, plus a **Clear all**. Reference impl on Browse Games:
- View builds them with `browse_helpers.get_active_filter_chips(request, form)` → `{filter_chips: [{label, remove_url}], filter_clear_url}` (urlencoded querystrings, page reset).
- Rendered by `partials/browse/active_filters.html` (a `#gbrowse-active-filters` container) included in the toolbar **and** OOB-swapped (`with oob=True`) from the results partial so it stays in sync when filters change via the panel.
- **Chip removal is a full nav** (plain `href`), not HTMX — so the collapsed panel's form controls reset in lockstep with the chips. The container is kept truly `:empty` when no filters are active so its margin collapses.

### Toggle-Button Checkboxes

For filter options (platforms, regions, categories). Replaces traditional checkboxes.

```html
<label class="cursor-pointer">
    <input type="checkbox" name="field" value="val" class="sr-only peer" />
    <span class="btn btn-xs peer-checked:btn-primary peer-checked:font-bold btn-ghost border border-base-300 transition-all">
        Label
    </span>
</label>
```

**Critical**: Use `sr-only` (not `hidden`) for the input. `hidden` applies `display: none` which prevents form submission.

### Toggle Buttons (page-level controls)

For binary toggles (show/hide, grid/list, filter on/off).

```html
<!-- Active state -->
<button class="btn btn-xs md:btn-sm gap-1 btn-primary">
    <svg class="w-3.5 h-3.5">...</svg>
    <span class="hidden md:inline">Label</span>
</button>

<!-- Inactive state -->
<button class="btn btn-xs md:btn-sm gap-1 btn-ghost border border-base-300">
    <svg class="w-3.5 h-3.5">...</svg>
    <span class="hidden md:inline">Label</span>
</button>
```

Active states should use semantic colors: `btn-primary` for selection filters, `btn-warning` for exclusion filters.

### Pagination

Wrapped in a subtle panel with dashboard-style buttons.

```html
<div class="bg-base-300/30 rounded-lg px-3 py-2 md:px-4 md:py-2.5">
    <div class="flex flex-col md:flex-row justify-center items-center gap-2">
        <!-- Nav buttons: btn-ghost border border-base-content/10 -->
        <!-- Current page: btn-primary no-animation cursor-default font-bold -->
        <!-- Page jump form (optional) -->
    </div>
</div>
```

### Active Filter Summary

Shows current filter state below the toolbar.

```html
<div class="flex flex-wrap items-center gap-1.5 mb-3 text-xs text-base-content/50">
    <span class="font-semibold">Showing:</span>
    <span class="badge badge-xs badge-ghost">Filter Value</span>
    <span class="text-base-content/30">·</span>
    <span class="badge badge-xs badge-primary">Special Filter</span>
    <a href="{% url 'reset_url' %}" class="link link-primary text-xs ml-1">Reset</a>
</div>
```

### Browse Cards (grid items)

For game cards, badge cards, and similar grid items. Miniature versions of dashboard modules.

```html
<div class="card bg-base-200/90 border-2 border-base-300 shadow-md shadow-neutral
            transition-all duration-300 hover:shadow-lg hover:shadow-{color}/30 hover:border-{color}
            group p-1 md:p-1.5">
    <!-- Image (game covers use 3:4 portrait; non-game content can use aspect-square) -->
    <figure class="relative aspect-[3/4] bg-white/[0.03] border border-base-content/5 rounded-lg overflow-hidden">
        <img class="w-full h-full object-cover object-top" />
    </figure>

    <!-- Content -->
    <div class="card-body m-0 px-1.5 md:px-2 py-1.5 md:py-2 gap-1">
        <h3 class="text-xs font-bold line-clamp-2">Title</h3>
        <!-- Badges, tags -->

        <!-- Stats panel (pushed to bottom) -->
        <div class="bg-white/[0.03] border border-base-content/5 rounded-lg p-1 md:p-1.5 mt-auto">
            <!-- Stat rows separated by border-t border-base-content/10 -->
        </div>
    </div>
</div>
```

**Hover**: Use colored shadow glow (`hover:shadow-lg hover:shadow-{color}/30`) instead of scale transforms. Scale can cause layout shifts and feels heavy.

### Browse Rows (list items)

For list view variants of browse pages.

```html
<div class="card bg-base-200/90 border-2 border-base-300 shadow-md shadow-neutral
            transition-all duration-300 hover:shadow-lg hover:shadow-{color}/30 hover:border-{color} group">
    <div class="card-body m-0 px-2 py-2 md:px-3 md:py-3">
        <div class="flex flex-col md:flex-row md:items-center gap-2 md:gap-4">
            <!-- Image + Title (always horizontal, flex-1 min-w-0) -->
            <!-- Stats section (stacks below on mobile, inline on tablet+) -->
            <!--   Mobile: bg-white/[0.03] border border-base-content/5 rounded-lg p-1.5 -->
            <!--   Tablet+: md:bg-transparent md:border-0 md:rounded-none md:p-0 -->
        </div>
    </div>
</div>
```

### Premium CTA Cards

Used when a feature is limited for free users (game lists, badge showcase, custom themes). Responsive column-to-row layout with star icon, feature checklist, and upgrade button.

```html
<div class="card bg-base-200/90 border-2 border-primary/20 shadow-lg shadow-neutral">
    <div class="card-body p-3 md:p-5">
        <div class="flex flex-col md:flex-row items-center gap-3 md:gap-4">
            <div class="text-primary shrink-0">
                <svg class="w-8 h-8 md:w-10 md:h-10"><!-- star icon --></svg>
            </div>
            <div class="flex-1 text-center md:text-left">
                <h3 class="font-bold text-base md:text-lg">Headline</h3>
                <p class="text-xs text-base-content/50 mb-2">Supporting text.</p>
                <div class="flex flex-wrap justify-center md:justify-start gap-x-4 gap-y-1 text-xs md:text-sm text-base-content/60">
                    <span class="flex items-center gap-1">
                        <svg class="w-3.5 h-3.5 text-success shrink-0"><!-- checkmark --></svg>
                        Feature
                    </span>
                    <!-- More features... -->
                </div>
            </div>
            <a href="..." class="btn btn-sm md:btn-md btn-primary shrink-0">Upgrade to Premium</a>
        </div>
    </div>
</div>
```

Key details: `border-primary/20` (not `border-base-300`) for the accent. Content centers on mobile, left-aligns on tablet+. Button shrinks to `btn-sm` on mobile.

### Navbar/Footer Theme Seam

The navbar and footer use a `border-primary/30` border to create a visual connection between the themed page content and the neutral navigation frame. Without this, there is a jarring disconnect when using gradient or colored themes.

- **Navbar**: `border-b-2 border-primary/30` on the bottom edge
- **Footer**: `border-t-2 border-primary/30` on the top edge

This is applied globally and requires no per-page configuration.

### Empty States

Wrap in a card for consistency with surrounding content.

```html
<div class="card bg-base-200/90 border-2 border-base-300 shadow-lg shadow-neutral">
    <div class="card-body items-center text-center py-12 md:py-16">
        <svg class="h-12 w-12 md:h-16 md:w-16 text-base-content/30">...</svg>
        <p class="text-base-content/60 mt-3 mb-1 text-base md:text-lg font-semibold">Primary message</p>
        <p class="text-base-content/40 text-xs md:text-sm">Helpful suggestion</p>
    </div>
</div>
```

---

## Color and Contrast Tokens

### Theme-Safe Inner Elements

All inner elements (stat cells, panels, list rows, image containers) use the **theme-safe contrast** pattern. This ensures visibility across both light and dark themes, including gradient-background themes where `bg-base-300/*` backgrounds became invisible.

| Purpose | Class | Notes |
|---------|-------|-------|
| Stat cell / inner panel | `bg-white/[0.03] border border-base-content/5` | Theme-safe: visible on all themes |
| Interactive row hover | `hover:bg-white/[0.06]` | Subtle hover lift for clickable inner rows |
| Table header | `bg-white/[0.03] border-b border-base-content/5` | Bottom-border-only variant for table headers |

**Why not `bg-base-300/*`?** On dark gradient themes (e.g., deep navy, charcoal), `bg-base-300/30|40|50` blends directly into the background and becomes invisible. `bg-white/[0.03]` creates a consistent, subtle lift regardless of theme. The `border-base-content/5` border adds definition without being heavy.

> **Exception: artwork overlay pages** (game detail, badge detail) still use `bg-base-300/35` and `hover:bg-base-300/50` for inner cells. These pages have variable game art backgrounds, and the white overlay pattern looks wrong against bright artwork. See the "Cards over game artwork backgrounds" variant in Card Anatomy above.

### Card and Page-Level Tokens

| Purpose | Class | Notes |
|---------|-------|-------|
| Card background | `bg-base-200/90` | Slightly transparent, defined against page bg |
| Card outer border | `border-2 border-base-300` | Thicker, uses background scale (intentional) |
| Card shadow (default) | `shadow-md shadow-neutral` | Browse cards, list items |
| Card shadow (emphasis) | `shadow-lg shadow-neutral` | Content modules, page headers |
| Card stacking gap | `mb-3` | Between sequential cards on a page |
| Hover glow | `hover:shadow-lg hover:shadow-{color}/30` | Colored glow on browse cards |
| Premium CTA border | `border-2 border-primary/20` | Accent border for premium upsell cards |
| Navbar/footer seam | `border-primary/30` | `border-b-2` on navbar, `border-t-2` on footer |

### Text and Divider Tokens

| Purpose | Class | Notes |
|---------|-------|-------|
| Internal dividers | `border-base-content/10` | Visible in dark themes (content-based, not bg-based) |
| Muted text | `text-base-content/50` | Labels, secondary info |
| Very muted text | `text-base-content/40` | Timestamps, hints |
| Ghost text | `text-base-content/30` | Tertiary info, placeholders |
| Dot separator | `text-base-content/15` or `text-base-content/20` | Between inline stat groups |
| Pipe separator | `text-base-content/15` | Vertical bar between metadata items |

### Divider Pattern

Use `border-base-content/10` for all internal separators. This is more visible than `border-base-300` in dark themes because it derives from the text color rather than the background scale.

```html
<!-- Section separator -->
<div class="border-t border-base-content/10 pt-3 mt-3">

<!-- Between repeated items -->
<div class="divide-y divide-base-content/10">

<!-- Vertical divider between inline groups -->
<div class="w-px h-6 bg-base-content/10 shrink-0"></div>
```

Do NOT use `border-base-content/10` for card outer borders (keep `border-base-300` there) or decorative chart gridlines (keep `border-base-300/30`).

---

## Grid Collapse Rules

### Grids that GO single-column on mobile (`grid-cols-1 md:grid-cols-2`)

Use this when each cell contains an **icon + text + metadata** that needs horizontal space:

- Game lists with icons + names + progress bars
- Badge lists with icons + names + tier info
- Milestone/progress cards
- Trophy rarity cards
- Chart pairs (two Canvas charts side by side)
- Stat-list panels (label: value rows that need ~130px+ for labels)

### Grids that STAY multi-column on mobile

Use this when cells contain **compact, self-contained data** (numbers, icons, short labels):

| Pattern | Mobile | Tablet | Desktop | Example |
|---------|--------|--------|---------|---------|
| Stat numbers | `grid-cols-4 gap-1.5` | `md:gap-3` | `lg:gap-4` | Trophy type counts (P/G/S/B) |
| Summary row | `grid-cols-3` | same | `lg:grid-cols-6` | Earned/Unearned/Games/etc. |
| Badge tier grid | `grid-cols-4 gap-1.5` | `md:gap-2` | same | Bronze/Silver/Gold/Platinum |
| Browse cards | `grid-cols-2 gap-1.5` | `md:grid-cols-3 md:gap-2` | `lg:grid-cols-4 xl:grid-cols-5 2xl:grid-cols-6` | Game/badge grids |
| Calendar months | `grid-cols-3 gap-1.5` | `md:gap-3` | `lg:gap-4` | 3-month paginated calendar |
| Badge icon grid | `grid-cols-4` | same | `lg:grid-cols-5` | Badge showcase selection |
| Theme swatches | `grid-cols-6` | same | `lg:grid-cols-8` | Color picker dots |
| Genre slots | `grid-cols-2` | same | `lg:grid-cols-3` | Genre challenge (short names) |

### Gap Progressions

| Context | Classes |
|---------|---------|
| Stat grids (compact) | `gap-1.5 md:gap-2 lg:gap-3` or `gap-1.5 md:gap-3 lg:gap-4` |
| Content grids (list items) | `gap-2 md:gap-3 lg:gap-4` or `gap-2 lg:gap-3` |
| Browse card grids | `gap-1.5 md:gap-2` |
| Tab panel modules | `gap-3 md:gap-4 lg:gap-6` |

---

## Spacing Progressions

| Element | Mobile | Tablet | Desktop |
|---------|--------|--------|---------|
| Card body (content) | `p-3` | `md:p-5` | `lg:p-7` |
| Card body (utility) | `p-3` | `md:p-4` | same |
| Inner panel | `p-2` | `md:p-3` | `lg:p-4` |
| Stat cell (large) | `p-1.5` | `md:p-3` | same |
| Stat cell (small) | `p-1` | `md:p-2` | same |
| Browse card padding | `p-1` | `md:p-1.5` | same |
| Module gap | `gap-3` | `md:gap-4` | `lg:gap-6` |

---

## Mobile-Specific Patterns

### Icon-Only Buttons

On mobile, hide text labels and show only icons for toolbar/header buttons. Use `hidden md:inline` on the label text.

```html
<button class="btn btn-xs md:btn-sm gap-1">
    <svg class="w-3.5 h-3.5">...</svg>
    <span class="hidden md:inline">Customize</span>
</button>
```

### Short Date Formats

When a relative timestamp ("3 days ago") takes too much space on mobile, render both formats and swap with responsive classes:

```html
<span class="text-xs text-base-content/40 md:hidden">{{ date|date:"n/j/y" }}</span>
<span class="text-xs text-base-content/40 hidden md:inline" data-time-format="relative" data-time="{{ date|date:'c' }}">{{ date|iso_naturaltime }}</span>
```

### Hidden Timestamps

For list items where timestamps are nice-to-have but not critical, hide them on mobile entirely:

```html
<span class="text-xs text-base-content/40 hidden lg:inline" data-time-format="relative" ...>
```

### Chart Centering

When a chart (e.g., radar) stacks to full width on mobile, constrain and center it:

```html
<div style="max-height: 240px;" class="max-w-xs mx-auto md:max-w-none">
    <canvas ...></canvas>
</div>
```

---

## Typography Scale

| Usage | Classes | Notes |
|-------|---------|-------|
| Page heading | `text-lg md:text-xl lg:text-2xl font-bold` | In page header cards |
| Card title | `text-base lg:text-lg font-bold` | No `md:` step |
| Section header | `text-xs font-semibold text-base-content/60 uppercase tracking-wider` | With `w-3.5 h-3.5` icon |
| Large stat numbers | `text-lg md:text-xl lg:text-2xl` | In stat cells |
| Small stat numbers | `text-sm lg:text-base` | In summary rows |
| Body text | `text-sm` | List item names, descriptions |
| Browse card title | `text-xs font-bold` | With `line-clamp-2` |
| Labels | `text-xs` | Stat labels, timestamps, metadata |
| Micro text | `text-[0.6rem]` or `text-[0.55rem]` | Calendar day numbers, chart legends |

**Minimum text size**: Do not go below `text-xs` (12px) for readable content. Reserve arbitrary small sizes (`text-[0.55rem]`) only for decorative/supplementary elements like calendar grids and chart legends.

---

## Iconography

**The rebuild uses [Lucide](https://lucide.dev) (MIT) as its single icon set.** Every UI glyph is a Lucide
stroke icon, hand-inlined as an `<svg>` (never a runtime icon font/library — inline keeps it CSP-safe and
lets `currentColor`/token colours flow). The Lucide signature to match:

```html
<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"
     stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"> … </svg>
```

**Rules:**
- **New glyphs**: copy the SVG inner markup from lucide.dev verbatim (names match, so a glyph can be
  re-copied/updated later). Size with Tailwind `w-*`/`h-*`; colour via `stroke` (a token, not a hex).
- **Shared includes** live in `templates/partials/icons/` (`check`, `percent`, `user`, `octagon-alert`
  via `privacy_warning.html`, `globe`, etc.) — reuse these rather than re-inlining. Each takes `color` +
  `size` params. Job icons are the Lucide sprite behind the `{% job_icon %}` templatetag.
- **Brand-mark exceptions** (NOT Lucide, deliberately): `partials/icons/ps_logo.html` (the PlayStation
  wordmark) and `partials/icons/pp_logo.html` (the PlatPursuit logo image). Use these for their brands;
  Lucide has no equivalent and shouldn't.
- **Filled rating star** is an accepted exception: the site-wide `<polygon>` star rendered `fill`ed (the
  Lucide star geometry, filled) for community ratings — a rating convention, not drift. Everywhere else,
  icons are stroke.
- **Do NOT** introduce FontAwesome / Heroicons / Feather / Bootstrap glyphs. Tells: a `viewBox` other than
  `0 0 24 24` (FontAwesome is `0 0 512 512`), or a filled UI glyph that Lucide would stroke.

---

## Badge type display priority

Wherever the **badges a game belongs to** are listed (browse cards, game detail, etc.), order them by the
site-wide badge-type priority and **count distinct series** (one per `series_slug`), never tiers (a
four-tier series is **one** badge, not four):

> **Franchise › Collection › Developer › Series** (then the special types Megamix › Event › User)

Codified as `BADGE_TYPE_DISPLAY_PRIORITY` in `trophies/constants.py` — sort by it, then by name; unknown/new
types sort last. Don't re-hardcode the order per surface.

---

## Image Styling

These conventions apply site-wide and are not affected by the redesign:

- Game cover art / title images: `aspect-[3/4]` (portrait) with `object-cover object-top`. IGDB's native cover ratio is portrait; the `object-top` anchor preserves game logos at the top of the cover when wider PSN fallback art crops inside the portrait container.
- Trophy icons: `object-cover` with square aspect ratio (these are square by nature).
- Badge images: `object-contain` (transparent backgrounds, custom shapes, stay square).
- Never use `object-fill`.
- See CLAUDE.md "Image Styling Conventions" for full rules.

### Game Image Fallback Chain

Use `{{ game.display_image_url }}` — this is the single source of truth for the IGDB-first fallback chain. Pair it with `{% if game.has_cover_art %}` when the template needs to differentiate styling (real cover art gets `object-cover object-top`; the generic `title_icon_url` fallback gets `object-contain p-3`).

The chain `Game.display_image_url` implements:

- **Normal path** (not `force_title_icon`): **trusted IGDB cover → `concept.concept_icon_url` (PSN MASTER, skipped for `PP_*` stubs) → `game.title_image` → `game.title_icon_url`**.
- **force_title_icon**: admin flag for games with bad PSN store art. Skips PSN intermediate sources entirely: **trusted IGDB cover → `game.title_icon_url`**.

For concept-only contexts (badges, reviews, showcases that don't go through a Game), use `{{ concept.cover_url }}` — same IGDB-first ordering: **trusted IGDB cover → `concept.concept_icon_url` (non-stub only)**. Returns `None` if neither source is available, so gate with `{% if concept.cover_url %}`.

`bg_url` is **deliberately excluded** from both chains — it's landscape (`GAMEHUB_COVER_ART`) and crops badly in portrait containers. Use `concept.bg_url` directly if you actually want the landscape image (e.g., share-card backdrops).

IGDB cover URLs are constructed on the fly from `IGDBMatch.igdb_cover_image_id`. **Always** include `select_related('concept', 'concept__igdb_match')` (or `most_recent_concept__igdb_match` for badges) on querysets that render covers — IGDB is the first lookup on every render now, so a missing prefetch becomes a guaranteed N+1.

**And always pair the `select_related` with `.defer('concept__igdb_match__raw_response')`** (or the equivalent path: `game__concept__igdb_match__raw_response`, `trophy__game__concept__igdb_match__raw_response`, etc.). `IGDBMatch.raw_response` is the ~30 KB JSON blob of the full IGDB API response per game — it's never accessed by cover-art rendering or any user-facing template, but it gets dragged into the join by `select_related` for free. Across high-volume querysets (browse pages with 30 games, badge detail with 200 games across stages, profile trophy case with 500+ games), the unused payload added up to enough memory pressure to OOM the web container in May 2026. The fix is one chain method per call site; the only place that genuinely needs `raw_response` populated is `stats_service._compute_game_library` for Tier-2 IGDB stats, which opts in via an explicit `.only('raw_response')` on a targeted queryset.

For franchise/company browse cards (aggregated cover art across many games), use the four `representative_*` Subquery annotations from `trophies/services/game_grouping_service.py`: `representative_igdb_cover_id_subquery`, `representative_concept_icon_subquery`, `representative_title_image_subquery`, `representative_title_icon_subquery` — checked in that IGDB-first order in the template.

---

## What This Doc Does NOT Cover

These are **page-specific** decisions, not site-wide tokens:

- **Content width**: Dashboard uses `max-w-4xl mx-auto`; other pages use full `container` width, sidebars, or custom layouts
- **Page layout structure**: Single-column vs. multi-column, sidebar presence
- **Tab/navigation systems**: Dashboard's tab bar is specific to that page
- **Module lazy loading**: Dashboard-specific architecture (module registry, skeleton states)

---

## Gotchas and Pitfalls

- **Theme-safe contrast is mandatory**: Never use `bg-base-300/30|40|50` for inner elements on normal pages. These become invisible on dark gradient themes. Always use `bg-white/[0.03] border border-base-content/5`. The only exception is artwork overlay pages (game detail, badge detail) which use `bg-base-300/35` because the white pattern looks wrong against bright game art.
- **Legacy `bg-base-300/*` remnants**: Some older pages (recap slides, platinum grid, checklist editor) still use `bg-base-300/50`. These should be migrated to the theme-safe pattern when those pages are redesigned. Do not copy the old pattern into new work.
- **Italic text clipping**: Always add `pr-1` when combining `line-clamp-*` with `italic`. The italic glyph slant gets clipped by line-clamp's overflow hidden.
- **`sr-only` vs `hidden` for form inputs**: Toggle-button checkboxes must use `sr-only` (invisible but still submits). `hidden` applies `display: none` which prevents form submission entirely.
- **ZoomScaler removed**: The legacy sub-768px transform-scale system was removed (no callers remained); every page builds mobile-first. The `#zoom-container` / `#zoom-wrapper` divs in `base.html` remain only as the host for the modal/ceremony recede (`.pp-receded` -> `#page-recede`).
- **Tailwind rebuild required**: After adding new responsive class variants (e.g., `md:p-5`), run `npm run build` to regenerate `output.css`. The output is minified to a single line.
- **`md:hidden` for mobile-only elements**: This class is visible below 768px and hidden at 768px+. Useful for short date formats shown only on mobile.
- **Touch targets**: Interactive elements should be at least 44px on all sizes. DaisyUI `btn-xs` and `toggle-sm` are smaller but acceptable for secondary controls.
- **Hover glow vs scale**: Prefer colored shadow glow (`hover:shadow-lg hover:shadow-{color}/30`) over `hover:scale-105`. Scale causes layout shifts and feels heavy at tight grid gaps.
- **Container class at 640px**: Tailwind's `.container` snaps to `max-width: 640px` at the `sm` breakpoint, which can cause a visual jump. The effect is minimal since content is typically narrower than 640px on phones.
- **Card stacking**: Use `mb-3` between sequential cards on a page. Do not use `mt-6` or larger margins from the old `<section>` wrapper pattern.

## Related Docs

- [Template Architecture](template-architecture.md): base.html structure, zoom wrapper, blocks
- [JS Utilities](js-utilities.md): ZoomScaler, ZoomAwareObserver (still used on non-redesigned pages)
- [Dashboard](../features/dashboard.md): Module registry, customization, the reference implementation
