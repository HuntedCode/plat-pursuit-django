# PlatPursuit — Project CLAUDE.md

> This file contains project-specific standards. See `~/.claude/CLAUDE.md` for universal collaboration, workflow, and quality standards that apply across all projects.

## Design Standard: Platinum Pursuit

All pages should be designed with the "Platinum Pursuit Standard" in mind: Professional, Sleek, Modern but never losing the charm that comes with our indie development team and our passion for trophy hunting. Flavor text, easter eggs, and nods to the community are welcomed within reason! Fun should always be a priority.

The full visual constitution lives in **[docs/design/visual-identity.md](docs/design/visual-identity.md)**: brief, six adjectives (prestigious, exploratory, rewarding, modern, charming, earnest), four signature primitives (Frame, Pursuer Card, Horizon, Tally), and explicit anti-references. Consult it before any non-trivial visual work. The companion **[docs/design/product-identity.md](docs/design/product-identity.md)** is the strategic frame that visual-identity.md serves.

---

## Site-Wide Redesign

### Overview

Every page in PlatPursuit is being rebuilt to match the dashboard's design language. This is a full redesign, not a re-style. The dashboard is the reference implementation and the design baseline. The goal: every page should feel like it belongs to the same app as the dashboard.

**Redesign in progress**: Pages are being rebuilt incrementally. Each page goes through a three-part process (backend audit, frontend rebuild, polish). Redesigned and legacy pages coexist safely due to the opt-in ZoomScaler architecture.

### Three-Part Process Per Page

**1. Backend Audit**: Read the view, queryset, and services. Identify performance issues (N+1 queries, expensive subqueries), missing data opportunities (user-specific context, annotations), and cleanup candidates. Only rebuild the backend where there's a clear win.

**2. Frontend Rebuild**: Ground-up template rebuild. Not "add breakpoints" or "swap colors." The test: **"Would this component look at home inside a dashboard module?"** If no, rebuild it.

**3. Polish**: Final audit against the Platinum Pursuit Standard, responsive compliance, visual cohesion, interactive polish.

### Responsive Philosophy: Three Layouts, Mobile-First

We build **three** layouts per page: **mobile (375px+)**, **tablet (768px+)**, and **desktop (1024px+)**. Tailwind's mobile-first breakpoints mean base styles target phones, with `md:` and `lg:` adding complexity for larger screens.

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

All styling tokens, patterns, component blueprints, and rules are documented in **[docs/reference/design-system.md](docs/reference/design-system.md)**. This includes:

- Card anatomy and variants (content, utility, browse, page header)
- Component patterns (page headers, filter toolbars, toggle buttons, pagination, browse cards, empty states)
- Responsive spacing progressions (`p-3 md:p-5 lg:p-7`)
- Grid collapse rules (which grids go 1-col on mobile vs. stay multi-col)
- Color/contrast tokens (backgrounds, borders, dividers, hover glow)
- Mobile-specific patterns (icon-only buttons, short dates, hidden timestamps)
- Typography scale

**Consult the design system doc before rebuilding any page.** It is the single source of truth for how components should look and behave.

### Reference Implementation

See `templates/trophies/dashboard.html` and its module partials in `templates/trophies/partials/dashboard/` for the canonical implementation.

### ZoomScaler Legacy System (Non-Redesigned Pages)

Pages not yet redesigned still use the ZoomScaler transform-scale system. This is being phased out as pages are rebuilt.

**How it works**: The `#zoom-container` and `#zoom-wrapper` divs in `base.html` are always present but inert. When a page calls `PlatPursuit.ZoomScaler.init()`, it adds `.zoom-active` which activates CSS transform rules that scale the 768px layout down to fit smaller screens.

**For non-redesigned pages:**
- Base styles target 768px (tablet), `lg:` targets desktop
- Do NOT use `grid-cols-1 md:grid-cols-2` patterns (base must show the tablet layout)
- Verify layout at exactly 768px wide (the baseline that gets scaled down)

**To redesign a page:**
1. Backend audit: review view, queryset, services for optimization opportunities
2. Remove `PlatPursuit.ZoomScaler.init()` from the page's JS
3. Rebuild templates from scratch using design system tokens and component patterns
4. Run `npm run build` to regenerate Tailwind CSS
5. Test at 375px, 768px, and 1024px+

Fixed-position elements (toasts, modals, mobile tabbar) live OUTSIDE the wrapper divs in `base.html` and are unaffected by either system.

---

## Concept Model: Critical `absorb()` Method

The `Concept.absorb(other)` method in `trophies/models.py` migrates ALL related data from one Concept to another before the old one is deleted. This is called automatically by `Game.add_concept()` when a concept reassignment orphans the old Concept.

**IMPORTANT: When adding any new model with a ForeignKey, M2M, or other relationship to `Concept`, you MUST update `Concept.absorb()` to handle that relationship.** Failing to do so will cause data loss when concepts are reassigned during sync.

Currently handled by `absorb()`:
- Comments (all types, including historical concept/trophy-level) + votes + reports
- ConceptTrophyGroups (merged FIRST, by trophy_group_id; orphans cascade-delete). This ordering is load-bearing: ratings/reviews below re-point onto the surviving CTG, so the merge must precede them.
- UserConceptRating (re-point concept AND concept_trophy_group to the surviving CTG; dedup by profile + trophy_group_id, NOT the CTG primary key. Both FKs are CASCADE, so leaving concept_trophy_group on `other`'s doomed duplicate CTG silently cascade-deletes the rating — the historical loss bug. Null-CTG base-game ratings re-point concept only)
- Reviews (same as UserConceptRating: re-point concept AND concept_trophy_group to the surviving CTG, dedup by profile + trophy_group_id; concept_trophy_group is non-null + CASCADE so it MUST move off the doomed CTG)
- Checklists + sections, items, votes, reports, user progress
- FeaturedGuide entries
- Profile.selected_background
- Badge.most_recent_concept
- Stage.concepts (M2M)
- ConceptBundle.concepts (M2M, per-bundle membership; skips duplicates so target's existing bundle membership isn't disturbed)
- ContractMembership.concept (OneToOne home Contract for job XP: re-point `other`'s to the survivor only if the survivor has none — a concept has ONE home Contract; otherwise `other`'s cascade-deletes)
- ContractBundle.concepts (M2M satisfier membership, same dedup pattern as ConceptBundle)
- StageCompletionEvent.concept (FK, SET_NULL)
- ConceptJoinReview.proposed_concept (FK, SET_NULL — re-pointed to the survivor. The `game` OneToOne needs no branch: games move out before absorb runs and each `join_review` travels with its Game)
- ConceptSplitEvent.parent_concept (FK) + .child_concepts (M2M)
- Genre challenge slots + bonus slots
- GameFamily (inherit if target has none)
- Concept.franchises_locked (inherit when `other` was locked, so the curated franchise/collection links the survivor just received stay protected from the next IGDB refresh)
- IGDB enrichment through-rows (ConceptCompany, ConceptGenre, ConceptTheme, ConceptEngine, ConceptFranchise) + IGDBMatch itself travel TOGETHER, gated on `inherit_match` (target has no IGDBMatch of its own). IGDB enrichment is a deterministic projection of the IGDBMatch, so when the target keeps its OWN match (the re-anchor / reassignment case) the source's enrichment describes a DIFFERENT IGDB game and is DROPPED (cascade-deletes with the source), not merged. Only when the target lacks a match does it inherit the source's match AND its enrichment rows (companies merge roles via OR-of-flags; genres/themes/engines/franchises dedup by their respective id). Merging enrichment unconditionally was the re-anchor data bug: re-pointing an erroneously-matched concept left the survivor showing both matches' developers/genres/themes/franchises stacked together.
- Concept.title_ids (merged/deduplicated)
- Roadmaps (per-CTG: each Concept may have N Roadmaps, one per ConceptTrophyGroup. Each source roadmap is matched to a surviving CTG by `trophy_group_id` and re-pointed; if the target concept already has a roadmap for that CTG the source's roadmap cascade-deletes with the source concept)

Note: `RoadmapEditLock`, `RoadmapRevision`, `RoadmapNote`, `RoadmapNoteRead`, and the `created_by`/`last_edited_by` FKs on `RoadmapStep`/`TrophyGuide` all FK to `Roadmap` or `Profile`, not `Concept` directly, so they don't need their own absorb branches. They follow `Roadmap` automatically.

---

## Image Styling Conventions

### Game Cover Art and Title Images
- **Aspect ratio**: game-cover containers use `aspect-[3/4]` (portrait), matching IGDB's native cover ratio. PSN fallback images (square/4:3) center-crop with `object-top` so game logos at the top of the cover survive.
- **Always** use `object-cover object-top` for game art (IGDB cover, PSN cover art). The `object-top` anchors to the top of the image, preserving game logos/titles when wider PSN fallback art crops in a portrait container.
- **Never** use `object-fill`, it stretches/distorts images.
- In inline-style contexts (share cards rendered by Playwright), use `object-fit: cover; object-position: top`.
- **Image fallback chain (IGDB-first)**: Use `{{ game.display_image_url }}` (with `{% if game.has_cover_art %}` for styling). This is the single source of truth, defined on the `Game` model. Normal path: **trusted IGDB cover → `concept.concept_icon_url` (PSN MASTER, skipped for `PP_*` stub concepts) → `game.title_image` → `game.title_icon_url`**. When `force_title_icon` is set (admin flag), PSN intermediate sources are skipped: trusted IGDB cover → `title_icon_url`. Never reimplement the chain inline, use the helper.
- `concept.bg_url` is deliberately **not** in the cover chain (it's landscape and crops badly in portrait containers). Callers that specifically want the landscape image (e.g. share-card backdrops) should reference `concept.bg_url` directly.
- IGDB cover art is constructed on the fly from `IGDBMatch.igdb_cover_image_id` via `Concept.get_cover_url(size)` / `Concept.cover_url` property. Only used for trusted matches (`is_trusted`). Querysets that render many games **must** `select_related('concept', 'concept__igdb_match')` to keep `display_image_url` from N+1'ing (IGDB is now the first lookup on every render, not the fallback). **Pair every such `select_related` with `.defer('concept__igdb_match__raw_response')`** (or the equivalent path through Trophy / EarnedTrophy / ProfileGame): `raw_response` is the ~30 KB IGDB API blob that is never read by cover-art templates and was the trigger for the May 2026 web-server OOM when concurrent renders piled up the join payload. Pages that genuinely need Tier-2 fields parsed out of `raw_response` (today: `stats_service._compute_game_library` only) opt back in with an explicit `.only('raw_response')` on a targeted queryset.

### Trophy Icons
- Use `object-cover` with square aspect ratio (`w-N h-N` pairs or `w-full aspect-square`)

### Badge Images
- Use `object-contain`, badges have transparent backgrounds and custom shapes

### Exceptions
- Generic PS placeholder icons (no `title_image`, no IGDB cover): `object-contain p-3`
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

## Performance: Per-User Querysets Must DB-Aggregate

PlatPursuit users range from 0 to 250,000+ earned trophies. Code that handles 100 rows fine in code review will OOM the gunicorn worker (or hit the 30-second timeout and 502) for power users. We hit this exact pattern at least eight times across `dashboard_service.py` and `stats_service.py` during the May 2026 whale OOM saga; each instance felt small in isolation but together cost a multi-day firefight.

**Rule:** When a queryset filters by `profile=...` and produces aggregate output (counts, sums, averages, distributions, min/max, group-by tallies), the aggregation must happen in the database. Never iterate the queryset in Python and build Counters/dicts/lists from it.

**Bad:**
```python
counts = defaultdict(int)
for et in EarnedTrophy.objects.filter(profile=profile, earned=True):
    for cg in et.trophy.game.concept.concept_genres.all():
        counts[cg.genre.name] += 1
```

**Good:**
```python
counts = dict(
    EarnedTrophy.objects.filter(profile=profile, earned=True)
    .values('trophy__game__concept__concept_genres__genre__name')
    .annotate(c=Count('id'))
    .values_list('trophy__game__concept__concept_genres__genre__name', 'c')
)
```

Postgres handles the aggregation in bounded memory (`work_mem`) and returns ~10-100 summary rows. Python iteration over 250K+ rows materializes hundreds of MB of ORM objects + JSONFields and takes 30+ seconds.

**Common anti-patterns to flag in review:**
- `for X in queryset:` building counts, sums, or sets — replace with `.values().annotate(Count('id'))` / `.aggregate()`
- `Counter(x.field for x in qs)` — replace with `.values('field').annotate(c=Count('id'))`
- `list(qs.values_list('field', flat=True))` followed by Python aggregation — replace with the above
- `defaultdict(int)` followed by per-row iteration

**Acceptable:** iteration over a profile-scoped queryset bounded by an explicit `[:N]` slice (e.g. `[:8]` for a preview grid). ProfileGame iteration (~hundreds-thousands for whales) is borderline OK for cheap per-row work, but prefer aggregation when possible.

**Diagnostic tool:** `python manage.py profile_render <url> --user <whale_username> --no-warmup` (run via Render Shell against prod) surfaces these issues in 30 seconds, listing the top allocation sites by file:line. If the top entry is a `for X in qs:` loop in our code, it needs fixing.

---

## Premium Preview Pattern: Never Run Heavy Code Against Real User Data

When a feature shows a "locked" or "preview" UI for users who don't have access (premium previews, gated content), the data-fetching code path must NOT execute against the actual user's data. We tripped this twice:

1. The original premium preview rendering ran each premium module's provider against a "showcase profile" inline during the dashboard render, allocating 200-2400 MB per cold-cache hit (the May 2026 homepage OOM class).
2. Phase 0 emptied the `preview_html` template variable but left `is_preview` modules with `load_strategy='server'`, so `get_server_module_data` still executed every premium provider against the real user's data — for free-tier whale users this fanned out to 10+ providers running sequentially against their 250K-trophy dataset, taking 91 seconds and 153 MB per render → 502s on `/`.

**Rule:** Any preview/locked UI's data layer must explicitly check the gate flag and skip work BEFORE invoking the underlying provider. The visual placeholder must come from one of:
1. A static gradient/skeleton (cheapest, currently in use for dashboard premium previews)
2. A hand-crafted sample fixture stored as constants
3. Cached output from a single canonical demo profile, refreshed via cron (NOT on the request path)

**Never** derive the placeholder from the actual viewing user's data on the request path, no matter how visually you hide it (blur, gradient overlay, etc.). The visual lock is harmless; the data fetching isn't.

**Concrete enforcement points:**
- `trophies/services/dashboard_service.py` `get_server_module_data` skips `mod.get('is_preview')` items. If a similar orchestrator gets added elsewhere (a stats-page preview loop, a community-page preview loop, etc.), the same skip must be there.
- When adding a new preview/locked feature, the code review checklist is: "what does this look like for a free-tier user with a maxed-out library?" If the data layer runs at all in that scenario, the design is wrong.

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
2. **Dashboard cohesion**: Would this component look at home inside a dashboard module? Uses the correct tokens from the design system doc?
3. **Responsive design compliance**: Three-layout mobile-first system, base styles correct at 375px, proper `md:`/`lg:` progression
4. **Component pattern compliance**: Page header cards, filter toolbars, browse cards, pagination, empty states all follow the design system patterns
5. **Interactive polish**: Hover glow (not scale), transitions, focus indicators, loading states
6. **Image styling**: `object-cover` for game/trophy icons, `object-contain` for badges, no `object-fill`
7. **Text handling**: `pr-1` on italic + line-clamped text, proper truncation
8. **Tailwind consistency**: Using project-standard classes rather than one-off values

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
