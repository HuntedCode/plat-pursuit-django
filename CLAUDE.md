# PlatPursuit — Project CLAUDE.md

> This file contains project-specific standards. See `~/.claude/CLAUDE.md` for universal collaboration, workflow, and quality standards that apply across all projects.

## Design Standard: Platinum Pursuit

All pages should be designed with the "Platinum Pursuit Standard" in mind: Professional, Sleek, Modern but never losing the charm that comes with our indie development team and our passion for trophy hunting. Flavor text, easter eggs, and nods to the community are welcomed within reason! Fun should always be a priority.

---

## Responsive Design Standards

### Philosophy: Three Layouts, Mobile-First

We build **three** layouts per page: **mobile (375px+)**, **tablet (768px+)**, and **desktop (1024px+)**. Tailwind's mobile-first breakpoints mean base styles target phones, with `md:` and `lg:` adding complexity for larger screens.

**Migration in progress**: Pages are being incrementally migrated from the legacy ZoomScaler system (transform-scale) to proper responsive styles. Migrated and non-migrated pages coexist safely. See the ZoomScaler Legacy section below for details on non-migrated pages.

### Design Targets

- **Mobile (375px+, base)**: Tightest layout. Must be functional and readable at 375px.
- **Tablet (768px-1023px, `md:`)**: More breathing room, multi-column where appropriate.
- **Desktop/4K (1024px+, `lg:`)**: Primary target. Best, most polished experience.

### Breakpoint Strategy

- Base styles (no prefix): Must look correct at **375px** (iPhone SE)
- `md:` (768px+): Tablet, where layouts can expand to multi-column
- `lg:` (1024px+): Desktop, primary design target
- `xl:` (1280px+), `2xl:` (1536px+): Large desktop refinements (optional)
- `sm:` (640px): Available but rarely needed; most layouts jump from mobile to `md:`

### Design System Reference

All styling tokens, patterns, and rules are documented in **[docs/reference/design-system.md](docs/reference/design-system.md)**. This includes:

- Card anatomy and class strings
- Responsive spacing progressions (`p-3 md:p-5 lg:p-7`)
- Grid collapse rules (which grids go 1-col on mobile vs. stay multi-col)
- Color/contrast tokens (backgrounds, borders, dividers)
- Mobile-specific patterns (icon-only buttons, short dates, hidden timestamps)
- Typography scale

**Consult the design system doc before styling any page.** It is the single source of truth for how components should look.

### Reference Implementation

See `templates/trophies/dashboard.html` and its module partials in `templates/trophies/partials/dashboard/` for the canonical responsive implementation.

### ZoomScaler Legacy System (Non-Migrated Pages)

Pages not yet migrated still use the ZoomScaler transform-scale system. This is being phased out incrementally.

**How it works**: The `#zoom-container` and `#zoom-wrapper` divs in `base.html` are always present but inert. When a page calls `PlatPursuit.ZoomScaler.init()`, it adds `.zoom-active` which activates CSS transform rules that scale the 768px layout down to fit smaller screens.

**For non-migrated pages:**
- Base styles target 768px (tablet), `lg:` targets desktop
- Do NOT use `grid-cols-1 md:grid-cols-2` patterns (base must show the tablet layout)
- Verify layout at exactly 768px wide (the baseline that gets scaled down)

**To migrate a page:**
1. Remove `PlatPursuit.ZoomScaler.init()` from the page's JS
2. Apply the design system tokens: shift base styles to mobile-first, push current base to `md:`
3. Run `npm run build` to regenerate Tailwind CSS
4. Test at 375px, 768px, and 1024px+

Fixed-position elements (toasts, modals, mobile tabbar) live OUTSIDE the wrapper divs in `base.html` and are unaffected by either system.

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
- StageCompletionEvent.concept (FK, SET_NULL)
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
