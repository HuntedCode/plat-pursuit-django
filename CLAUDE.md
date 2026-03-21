# PlatPursuit — Project CLAUDE.md

> This file contains project-specific standards. See `~/.claude/CLAUDE.md` for universal collaboration, workflow, and quality standards that apply across all projects.

## Design Standard: Platinum Pursuit

All pages should be designed with the "Platinum Pursuit Standard" in mind: Professional, Sleek, Modern but never losing the charm that comes with our indie development team and our passion for trophy hunting. Flavor text, easter eggs, and nods to the community are welcomed within reason! Fun should always be a priority.

---

## Responsive Design Standards

### Philosophy: Two Layouts, Not Three

We build **two** layouts per page: **tablet (768px)** and **desktop (1024px+)**. We do NOT build separate mobile layouts. Instead, screens below 768px receive the tablet layout uniformly scaled down via `transform: scale()`. This means every page looks identical on an iPhone as it does on a tablet, just proportionally smaller.

**NEVER use CSS `zoom`**: it is non-standard and has inconsistent inheritance in Safari/WebKit.

### Design Targets

- **Desktop/4K (1024px+, `lg:`)**: Primary target. Best, most polished experience.
- **Tablet (768px-1023px, `md:`)**: Minimum designed layout. This is what sub-768px screens see (scaled down).
- **Below 768px**: Automatically handled by transform scaling. DO NOT write mobile-specific styles. DO NOT TOUCH this behavior.

### Breakpoint Strategy

- `md:` (768px-1023px): Tablet optimizations, this is the smallest layout you actually design
- `lg:` (1024px+): Desktop and above, primary design target
- `xl:` (1280px+), `2xl:` (1536px+): Large desktop refinements
- Base styles (no prefix): Must look correct at 768px since that is the scaled-down view
- **NEVER use `sm:` breakpoints**: they target below 768px, which we don't design for
- **NEVER use `grid-cols-1 md:grid-cols-2`** or similar patterns that collapse to single-column below `md:`. Since 768px is the minimum layout, base styles should already show the tablet layout (e.g., use `grid-cols-2` directly, not `grid-cols-1 md:grid-cols-2`)

### Layout Patterns

- Stack vertically at md:, arrange horizontally at lg: `flex flex-col md:flex-col lg:flex-row`
- Scale elements progressively: `w-32 md:w-40 lg:w-48`
- Always include `md:` variants to bridge the gap between the tablet and desktop layouts
- Touch targets: At `md:` and above, interactive elements should be at least 44px
- Grids should start at their tablet column count: `grid-cols-2 lg:grid-cols-3` not `grid-cols-1 md:grid-cols-2 lg:grid-cols-3`

### Architecture: Zoom Wrapper System

Every page has this structure in `base.html`:
```
<body>
  <div id="zoom-container">        <!-- Clips overflow when scaling is active -->
    <div id="zoom-wrapper">         <!-- Receives transform: scale() -->
      navbar, main content, footer
    </div>
  </div>
  back-to-top, mobile tabbar, toasts, modals  <!-- Fixed elements, OUTSIDE wrapper -->
</body>
```

- `#zoom-wrapper` has `min-h-screen flex flex-col` (the page's flex column layout)
- Fixed-position elements live OUTSIDE the wrapper so they aren't affected by the transform
- The CSS rules live in `input.css`, gated behind `#zoom-container.zoom-active`, dormant until activated
- The JS utility `ZoomScaler` lives in `utils.js`, adds `.zoom-active` class and handles height correction
- Pages without `ZoomScaler.init()` are completely unaffected, the wrapper divs are layout-invisible

### How to Opt a Page into Uniform Scaling

Add one line to the page's `{% block js_scripts %}`:
```js
PlatPursuit.ZoomScaler.init();
```

That's it. The CSS is already in `input.css` and the JS utility handles everything (adding `.zoom-active` class, height correction, resize/mutation listeners).

Verify the page's layout looks correct at exactly 768px wide, that is the baseline that gets scaled down.

### Reference Implementation

See `templates/trophies/profile_detail.html` for a working example.

### Technical Details

- All scaling CSS lives in `input.css`, gated behind `#zoom-container.zoom-active` selector
- `ZoomScaler.init()` in `utils.js` adds `.zoom-active` to `#zoom-container` and runs the height correction IIFE
- `transform: scale()` shrinks the visual rendering but does NOT change the element's layout box
- `width: calc(100% / scale)` compensates so the scaled result fills 100% of the viewport
- `overflow: hidden` on `#zoom-container` prevents horizontal scrollbar from the expanded width
- Height correction JS sets `container.style.height` to `wrapper.scrollHeight * scale` to eliminate bottom whitespace
- `transform-origin: top left` anchors the scale to the top-left corner
- MutationObserver handles dynamic content (infinite scroll, AJAX) recalculating the height

---

## Concept Model: Critical `absorb()` Method

The `Concept.absorb(other)` method in `trophies/models.py` migrates ALL related data from one Concept to another before the old one is deleted. This is called automatically by `Game.add_concept()` when a concept reassignment orphans the old Concept.

**IMPORTANT: When adding any new model with a ForeignKey, M2M, or other relationship to `Concept`, you MUST update `Concept.absorb()` to handle that relationship.** Failing to do so will cause data loss when concepts are reassigned during sync.

Currently handled by `absorb()`:
- Comments (all types, including historical concept/trophy-level) + votes + reports
- UserConceptRating (skip duplicates by profile + concept_trophy_group)
- ConceptTrophyGroups (merge by trophy_group_id, orphans cascade-delete)
- Reviews (skip duplicates by profile + concept_trophy_group)
- Checklists + sections, items, votes, reports, user progress
- FeaturedGuide entries
- Profile.selected_background
- Badge.most_recent_concept
- Stage.concepts (M2M)
- GameFamilyProposal M2M
- Genre challenge slots + bonus slots
- GameFamily (inherit if target has none)
- Concept.title_ids (merged/deduplicated)

---

## Image Styling Conventions

### Game and Trophy Icons
- **Always** use `object-cover` with square aspect ratio (`w-N h-N` pairs or `w-full aspect-square`)
- **Never** use `object-fill`, it stretches/distorts images
- In inline-style contexts (share cards), use `object-fit: cover`

### Badge Images
- Use `object-contain`, badges have transparent backgrounds and custom shapes

### Exceptions
- Generic PS placeholder icons (no `title_image`): `object-contain p-3`
- Content rating icons: `object-contain`
- Banner/hero images: `object-cover` but not necessarily square
- User-uploaded content images (guide screenshots): `object-contain h-auto`

---

## Text Clipping Prevention

### Italic Text in Line-Clamped Containers
- **Always** add `pr-1` when combining `line-clamp-*` with `italic` (or when a line-clamped parent contains italic children)
- Italic glyphs slant beyond the text box boundary, and `line-clamp` clips overflow, causing the rightmost characters to be visually cut off
- `pr-1` (4px) provides enough breathing room to prevent clipping

---

## Quality Workflow: Project-Specific Extensions

The global CLAUDE.md defines the three-phase workflow (Plan, Build, Polish). Below are PlatPursuit-specific additions to each phase.

### Phase 1 Additions: Reuse Targets

Before exiting plan mode, specifically search:
- `static/js/utils.js` for utilities (API, ToastManager, HTMLUtils, debounce, InfiniteScroller, UnsavedChangesManager, ZoomScaler)
- Existing JS files for similar UI patterns (modals, tabs, infinite scroll, form handling)
- Existing templates for component patterns that can be reused or extended
- Existing Django views/services for logic that can be shared rather than duplicated

### Phase 2 Additions: Security Focus

In addition to the standard inline audit, check for Django-specific security pitfalls (CSRF, SQL injection, XSS in templates, unsafe querystring handling).

### Phase 3 Additions: Style Audit Criteria

The final audit should review every new/modified template and JS file against:

1. **Platinum Pursuit Standard**: Does it feel professional, sleek, and modern while retaining the indie charm? Or does it feel generic/sterile?
2. **Responsive design compliance**: Two-layout system, no `sm:` breakpoints, base styles correct at 768px, proper `md:`/`lg:` progression
3. **Visual cohesion**: Consistent spacing, colors, and component patterns with existing pages (reference: `profile_detail.html`, `game_detail.html`, `badge_detail.html`)
4. **Interactive polish**: Hover states, transitions, focus indicators, loading states
5. **Image styling**: `object-cover` for game/trophy icons, `object-contain` for badges, no `object-fill`
6. **Text handling**: `pr-1` on italic + line-clamped text, proper truncation
7. **Tailwind consistency**: Using project-standard classes rather than one-off values

---

## Git Commit Scopes

Scopes for this project: `models`, `views`, `templates`, `static`, `sync`, `badges`, `payments`, `notifications`, `admin`, `api`, `commands`

---

## Documentation Structure

All system documentation lives in `docs/`. See [docs/README.md](docs/README.md) for the full index.

**What counts as a doc-worthy change:**
- New models, services, views, or management commands
- New API endpoints or changed endpoint behavior
- New Redis keys or cache patterns
- New JS utilities or significant changes to existing ones
- New template patterns, context processors, or templatetags
- Changes to the sync pipeline, badge evaluation, or payment flows
- New cron jobs or changes to scheduling

**Doc categories:**
- `docs/architecture/` : Cross-cutting engine systems (sync, badges, payments, notifications, data model)
- `docs/features/` : Self-contained feature docs (checklists, challenges, dashboard, etc.)
- `docs/guides/` : How-to and operational docs (setup, commands, cron, email)
- `docs/reference/` : Quick-lookup tables (API endpoints, JS utils, Redis keys, settings)
- `docs/design/` : Long-form vision docs for unimplemented systems

New docs should use [docs/TEMPLATE.md](docs/TEMPLATE.md) as a starting point.
