# Design System Reference

The canonical styling reference for PlatPursuit. These tokens and patterns were established on the dashboard (the site's homepage and most component-rich page) and must be applied consistently across all pages during mobile-responsive migration.

This doc covers **site-wide building blocks** (cards, grids, spacing, colors). Page-level layout decisions (content width, sidebars, tab systems) are page-specific and not covered here.

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

### Migration Pattern

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

---

## Inner Elements

### Stat Cells (number + label)

Used for trophy counts, stats grids, tier breakdowns.

```html
<div class="bg-base-300/50 rounded-lg p-1.5 md:p-3 text-center">
    <div class="text-lg md:text-xl lg:text-2xl font-black text-primary">42</div>
    <div class="text-xs text-base-content/50 font-medium">Label</div>
</div>
```

For smaller stat cells (summary rows):

```html
<div class="flex flex-col items-center gap-0.5 p-1 md:p-2 rounded-lg bg-base-300/40">
    <span class="font-bold text-sm lg:text-base">99</span>
    <span class="text-xs text-base-content/50">Label</span>
</div>
```

### Inner Panels (grouped stats, analytics sections)

Used in premium analytics modules where multiple stat groups sit side by side.

```html
<div class="bg-base-300/40 rounded-lg p-2 md:p-3 lg:p-4">
    <!-- Panel content -->
</div>
```

### List Item Rows (icon + text + metadata)

Used for game lists, badge lists, activity feeds, leaderboard entries.

```html
<div class="flex items-center gap-2 md:gap-3 p-2 rounded-lg bg-base-300/40 hover:bg-base-300/60 transition-colors">
    <!-- Icon (shrink-0) -->
    <div class="flex-1 min-w-0">
        <!-- Text content with line-clamp-1 pr-1 -->
    </div>
    <!-- Trailing metadata (shrink-0) -->
</div>
```

---

## Color and Contrast Tokens

| Purpose | Class | Notes |
|---------|-------|-------|
| Card background | `bg-base-200/90` | Slightly transparent, defined against page bg |
| Inner cell background | `bg-base-300/50` | Stat cells, day cells |
| Inner panel/row background | `bg-base-300/40` | List items, analytics panels |
| Internal dividers | `border-base-content/10` | Visible in dark themes (content-based, not bg-based) |
| Card outer border | `border-2 border-base-300` | Thicker, uses background scale (intentional) |
| Card shadow | `shadow-lg shadow-neutral` | Consistent depth |
| Muted text | `text-base-content/50` | Labels, secondary info |
| Very muted text | `text-base-content/40` | Timestamps, hints |
| Ghost text | `text-base-content/30` | Tertiary info, placeholders |

### Divider Pattern

Use `border-base-content/10` for all internal separators. This is more visible than `border-base-300` in dark themes because it derives from the text color rather than the background scale.

```html
<!-- Section separator -->
<div class="border-t border-base-content/10 pt-3 mt-3">

<!-- Between repeated items -->
<div class="divide-y divide-base-content/10">
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
| Calendar months | `grid-cols-3 gap-1.5` | `md:gap-3` | `lg:gap-4` | 3-month paginated calendar |
| Badge icon grid | `grid-cols-4` | same | `lg:grid-cols-5` | Badge showcase selection |
| Theme swatches | `grid-cols-6` | same | `lg:grid-cols-8` | Color picker dots |
| Genre slots | `grid-cols-2` | same | `lg:grid-cols-3` | Genre challenge (short names) |

### Gap Progressions

| Context | Classes |
|---------|---------|
| Stat grids (compact) | `gap-1.5 md:gap-2 lg:gap-3` or `gap-1.5 md:gap-3 lg:gap-4` |
| Content grids (list items) | `gap-2 md:gap-3 lg:gap-4` or `gap-2 lg:gap-3` |
| Tab panel modules | `gap-3 md:gap-4 lg:gap-6` |

---

## Spacing Progressions

| Element | Mobile | Tablet | Desktop |
|---------|--------|--------|---------|
| Card body | `p-3` | `md:p-5` | `lg:p-7` |
| Inner panel | `p-2` | `md:p-3` | `lg:p-4` |
| Stat cell (large) | `p-1.5` | `md:p-3` | same |
| Stat cell (small) | `p-1` | `md:p-2` | same |
| Module gap | `gap-3` | `md:gap-4` | `lg:gap-6` |

---

## Mobile-Specific Patterns

### Icon-Only Buttons

On mobile, hide text labels and show only icons for toolbar/header buttons. Use `hidden md:inline` on the label text.

```html
<button class="btn btn-xs gap-1">
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
| Card title | `text-base lg:text-lg` | No `md:` step |
| Page heading | `text-xl md:text-2xl lg:text-3xl` | Full 3-step progression |
| Large stat numbers | `text-lg md:text-xl lg:text-2xl` | In stat cells |
| Small stat numbers | `text-sm lg:text-base` | In summary rows |
| Body text | `text-sm` | List item names, descriptions |
| Labels | `text-xs` | Stat labels, timestamps, metadata |
| Micro text | `text-[0.6rem]` or `text-[0.55rem]` | Calendar day numbers, chart legends |

**Minimum text size**: Do not go below `text-xs` (12px) for readable content. Reserve arbitrary small sizes (`text-[0.55rem]`) only for decorative/supplementary elements like calendar grids and chart legends.

---

## Image Styling (unchanged)

These conventions apply site-wide and are not affected by the responsive migration:

- Game/trophy icons: `object-cover` with square aspect ratio
- Badge images: `object-contain` (transparent backgrounds)
- Never use `object-fill`
- See CLAUDE.md "Image Styling Conventions" for full rules

---

## What This Doc Does NOT Cover

These are **page-specific** decisions, not site-wide tokens:

- **Content width**: Dashboard uses `max-w-4xl mx-auto`; other pages use full `container` width, sidebars, or custom layouts
- **Page layout structure**: Single-column vs. multi-column, sidebar presence
- **Tab/navigation systems**: Dashboard's tab bar is specific to that page
- **Module lazy loading**: Dashboard-specific architecture (module registry, skeleton states)

---

## Gotchas and Pitfalls

- **Italic text clipping**: Always add `pr-1` when combining `line-clamp-*` with `italic`. The italic glyph slant gets clipped by line-clamp's overflow hidden.
- **ZoomScaler coexistence**: During migration, some pages still use `ZoomScaler.init()`. The zoom wrapper divs in `base.html` are inert without `.zoom-active`, so migrated and non-migrated pages coexist safely.
- **Tailwind rebuild required**: After adding new responsive class variants (e.g., `md:p-5`), run `npm run build` to regenerate `output.css`. The output is minified to a single line.
- **`md:hidden` for mobile-only elements**: This class is visible below 768px and hidden at 768px+. Useful for short date formats shown only on mobile.
- **Touch targets**: Interactive elements should be at least 44px on all sizes. DaisyUI `btn-xs` and `toggle-sm` are smaller but acceptable for secondary controls.
- **Container class at 640px**: Tailwind's `.container` snaps to `max-width: 640px` at the `sm` breakpoint, which can cause a visual jump. On pages still using ZoomScaler, the zoom CSS overrides this. On migrated pages, the effect is minimal since content is typically narrower than 640px on phones.

## Related Docs

- [Template Architecture](template-architecture.md): base.html structure, zoom wrapper, blocks
- [JS Utilities](js-utilities.md): ZoomScaler, ZoomAwareObserver (still used on non-migrated pages)
- [Dashboard](../features/dashboard.md): Module registry, customization, the reference implementation
