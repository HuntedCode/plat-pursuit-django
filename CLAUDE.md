Hi Claude! We need to make sure that all code is efficient and high quality, prioritizing reuse and organization. If you see something that is worthy of a refactor, include it in your plans for discussion/implementation (unless told otherwise).
Be sure to ask questions when creating plans. Clarification will always be better than assumptions.
Indicate to me when first starting to build a plan that you have read this document.
Your thoughts/insights/recommendations/suggestions are always welcome if they help make our systems better. Even if they are not currently related to the task you are working on, it's better to mention it for later than to ignore it entirely.
All pages should be designed with the following "Platinum Pursuit Standard" mind: Professional, Sleek, Modern but never losing the charm that comes with our indie development team and our passion for trophy hunting. Flavor text, easter eggs and nods to the community are welcomed within reason! Fun should always be a priority.

## Writing Style

- **Never use em dashes** (the long dash character). Use colons, periods, or rephrase instead.

## Responsive Design Standards

### Philosophy: Two Layouts, Not Three

We build **two** layouts per page — **tablet (768px)** and **desktop (1024px+)**. We do NOT build separate mobile layouts. Instead, screens below 768px receive the tablet layout uniformly scaled down via `transform: scale()`. This means every page looks identical on an iPhone as it does on a tablet, just proportionally smaller.

**NEVER use CSS `zoom`** — it is non-standard and has inconsistent inheritance in Safari/WebKit.

### Design Targets

- **Desktop/4K (1024px+, `lg:`)**: Primary target. Best, most polished experience.
- **Tablet (768px-1023px, `md:`)**: Minimum designed layout. This is what sub-768px screens see (scaled down).
- **Below 768px**: Automatically handled by transform scaling. DO NOT write mobile-specific styles. DO NOT TOUCH this behavior.

### Breakpoint Strategy

- `md:` (768px-1023px): Tablet optimizations — this is the smallest layout you actually design
- `lg:` (1024px+): Desktop and above — primary design target
- `xl:` (1280px+), `2xl:` (1536px+): Large desktop refinements
- Base styles (no prefix): Must look correct at 768px since that's the scaled-down view
- **NEVER use `sm:` breakpoints** — they target below 768px, which we don't design for
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
- The CSS rules live in `input.css`, gated behind `#zoom-container.zoom-active` — dormant until activated
- The JS utility `ZoomScaler` lives in `utils.js` — adds `.zoom-active` class and handles height correction
- Pages without `ZoomScaler.init()` are completely unaffected — the wrapper divs are layout-invisible

### How to Opt a Page into Uniform Scaling

Add one line to the page's `{% block js_scripts %}`:
```js
PlatPursuit.ZoomScaler.init();
```

That's it. The CSS is already in `input.css` and the JS utility handles everything (adding `.zoom-active` class, height correction, resize/mutation listeners).

Verify the page's layout looks correct at exactly 768px wide — that is the baseline that gets scaled down.

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

## Concept Model — Critical: `absorb()` Method

The `Concept.absorb(other)` method in `trophies/models.py` migrates ALL related data from one Concept to another before the old one is deleted. This is called automatically by `Game.add_concept()` when a concept reassignment orphans the old Concept.

**IMPORTANT: When adding any new model with a ForeignKey, M2M, or other relationship to `Concept`, you MUST update `Concept.absorb()` to handle that relationship.** Failing to do so will cause data loss when concepts are reassigned during sync.

Currently handled by `absorb()`:
- Comments (concept-level, trophy-level, checklist-level) + votes + reports
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
- Concept.comment_count (recalculated)

## Image Styling Conventions

### Game & Trophy Icons
- **Always** use `object-cover` with square aspect ratio (`w-N h-N` pairs or `w-full aspect-square`)
- **Never** use `object-fill` — it stretches/distorts images
- In inline-style contexts (share cards), use `object-fit: cover`

### Badge Images
- Use `object-contain` — badges have transparent backgrounds and custom shapes

### Exceptions
- Generic PS placeholder icons (no `title_image`): `object-contain p-3`
- Content rating icons: `object-contain`
- Banner/hero images: `object-cover` but not necessarily square
- User-uploaded content images (guide screenshots): `object-contain h-auto`

## Text Clipping Prevention

### Italic Text in Line-Clamped Containers
- **Always** add `pr-1` when combining `line-clamp-*` with `italic` (or when a line-clamped parent contains italic children)
- Italic glyphs slant beyond the text box boundary, and `line-clamp` clips overflow, causing the rightmost characters to be visually cut off
- `pr-1` (4px) provides enough breathing room to prevent clipping

## Inline Audit Checkpoints

During implementation, launch a background **Explore subagent** after each logical chunk of work (a completed file, a feature slice, or a significant set of changes) to audit what was just written. The audit agent should check for:

- Code quality and readability
- Missed edge cases or potential bugs
- Consistency with existing project patterns and conventions
- Security issues (OWASP top 10, Django-specific pitfalls)
- Adherence to CLAUDE.md standards (responsive design, image styling, writing style, etc.)

Continue implementing the next chunk while the audit runs in the background. When audit results come back, surface any findings and fix issues immediately rather than accumulating them. This keeps the workflow as: **brainstorm -> plan -> (implement + audit in parallel) -> final review**.