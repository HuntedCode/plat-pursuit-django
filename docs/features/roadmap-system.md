# Roadmap System

> Staff-authored game guides with ordered steps, general tips, trophy guides, YouTube embeds, and guide metadata. Each game/DLC gets its own dedicated roadmap detail page with sticky TOC, scrollspy navigation, and personal progress tracking.

## Overview

The roadmap system provides structured game guides surfaced as dedicated pages. One roadmap per game (Concept), tabbed by trophy group (base game + DLCs). Each trophy group tab gets its own detail page URL. Staff-only authoring via a dedicated editor page.

Replaces the previous checklist system (DB tables retained, UI removed).

## Data Model

### Roadmap
One-to-one with Concept. Has `status` (draft/published) and `created_by` (Profile).

### RoadmapTab
One per ConceptTrophyGroup within a roadmap. Stores content fields (`general_tips`, `youtube_url`) and guide metadata fields (`difficulty`, `estimated_hours`, `missable_count`, `online_required`, `min_playthroughs`). Auto-created when the editor page loads.

**Guide metadata** is the author's assessment, distinct from community ratings (`UserConceptRating`). Metadata is displayed as a badge strip at the top of the roadmap detail page, labeled "Author Assessment" to avoid confusion.

### RoadmapStep
Ordered stages within a tab. Each has `title`, `description`, `youtube_url`, and `order`.

### RoadmapStepTrophy
Associates trophies with steps by `trophy_id` (IntegerField, not FK to Trophy). Uses PSN trophy_id which is consistent across game stacks within a concept.

### TrophyGuide
Per-trophy guide text within a tab. Identified by `trophy_id`. One guide per trophy per tab.

## Architecture

### Trophy Reference Strategy
Trophies belong to Game (not Concept). A Concept can span multiple Games (PS4/PS5 stacks). Roadmap models store `trophy_id` rather than FK to Trophy. When rendering, trophy display data (name, icon, type) is resolved from the specific Game in the URL.

### Concept.absorb()
Roadmap migration is handled after CTG migration in `absorb()`. If the target concept has no roadmap, the source's roadmap moves over and its tabs are re-pointed to the target's CTGs. If both concepts have roadmaps, the target's is kept.

### Progress Tracking
`RoadmapService.compute_progress(tab, profile_earned)` calculates per-step and overall progress from prefetched data. No additional queries needed. Returns earned/total counts per step and an overall percentage. Displayed as a progress bar in the page header and per-step earned counters.

## Pages

### Roadmap Detail Page (`/games/<np_communication_id>/roadmap/`)
- Public page, no authentication required (anonymous users see guide without progress)
- Separate pages per DLC: base game at `/roadmap/`, DLC at `/roadmap/001/`, `/roadmap/002/`, etc.
- Staff `?preview=true` support for viewing draft roadmaps
- Page structure:
  1. **Page header card**: game cover, title, "Trophy Roadmap" subtitle, author byline, overall progress bar
  2. **DLC navigation strip**: pill buttons to switch between trophy groups (only if multiple DLCs)
  3. **Metrics badge strip**: difficulty, hours, missable count, online required, playthroughs (labeled "Author Assessment")
  4. **Two-column layout** (desktop): main content + sticky sidebar TOC
  5. **Main content**: Overview (tips + video), walkthrough steps (collapsible, with trophy links and per-step progress), trophy guides (with sort/filter)
  6. **Sidebar TOC** (desktop): sticky table of contents with scrollspy highlighting. Mobile: collapsible dropdown above content

### Editor (`/games/<np_communication_id>/roadmap/edit/`)
- Staff-only (uses `@staff_member_required`)
- Tab bar for base game / DLC groups
- Per-tab: drag-reorderable steps, trophy picker, guide metadata (difficulty, hours, missable, online, playthroughs), general tips, YouTube URL, trophy guides
- Debounced autosave on text fields and metadata
- Publish/unpublish toggle
- Preview link opens the roadmap detail page with `?preview=true`

### Game Detail Page (CTA)
- Roadmap section in the Community tabs shows a CTA card linking to the dedicated roadmap detail page
- CTA includes step/guide counts, "View Roadmap" button with arrow, staff Edit button
- DLC links listed below the CTA if multiple groups have roadmaps
- No-roadmap empty state unchanged: neutral "opportunity" card with Discord CTAs

## URL Structure

| URL | View | Description |
|-----|------|-------------|
| `/games/<id>/roadmap/` | `RoadmapDetailView` | Base game roadmap |
| `/games/<id>/roadmap/<group>/` | `RoadmapDetailView` | DLC roadmap (group = `001`, `002`, etc.) |
| `/games/<id>/roadmap/edit/` | `RoadmapEditorView` | Staff editor (all tabs) |

## Discoverability Surfaces

| Surface | File | When it shows | Purpose |
|---------|------|---------------|---------|
| **Game detail CTA** | `roadmap_tab_content.html` | Per-CTG tab when published roadmap exists | Primary link to the dedicated roadmap page |
| **Game detail empty state** | `community_tabs_section.html` | Per-CTG tab when no roadmap exists | Demand capture + author recruitment |
| **Dashboard module** | `roadmap_recommendations.html` | Dashboard, unplatted games with roadmaps | Links directly to roadmap detail page |
| **Community hub recruitment** | `roadmap_recruitment_strip.html` | Community hub, hidden for staff | Author recruitment |

## API Endpoints (Staff-Only)

| Method | Endpoint | Description |
|--------|----------|-------------|
| PATCH | `/api/v1/roadmap/<id>/tab/<id>/` | Update tab (tips, YouTube, metadata) |
| GET/POST | `/api/v1/roadmap/<id>/tab/<id>/steps/` | List/create steps |
| PATCH/DELETE | `/api/v1/roadmap/<id>/tab/<id>/steps/<id>/` | Update/delete step |
| POST | `/api/v1/roadmap/<id>/tab/<id>/steps/reorder/` | Reorder steps |
| PUT | `/api/v1/roadmap/<id>/tab/<id>/steps/<id>/trophies/` | Set step trophy associations |
| PUT/DELETE | `/api/v1/roadmap/<id>/tab/<id>/trophy-guides/<trophy_id>/` | Create/update/delete trophy guide |
| POST | `/api/v1/roadmap/<id>/publish/` | Publish or unpublish |
| POST | `/api/v1/roadmap/upload-image/` | Upload image for markdown embedding |

All endpoints require staff authentication via SessionAuthentication + IsAdminUser.

## Service Layer

`trophies/services/roadmap_service.py` provides:

- `get_roadmap_for_display(concept)` / `get_roadmap_for_preview(concept)`: Full roadmap with all tabs prefetched
- `get_tab_for_display(concept, trophy_group_id)` / `get_tab_for_preview(...)`: Single tab with full prefetch for detail page
- `get_available_tabs(concept, include_drafts)`: All tabs with content presence info for DLC navigation
- `compute_progress(tab, profile_earned)`: Per-step and overall progress calculation
- `update_tab(tab_id, ...)`: Update content and metadata fields
- Step CRUD, reorder, trophy association, guide CRUD, publish/unpublish

## Key Files

| File | Purpose |
|------|---------|
| `trophies/models.py` | Roadmap, RoadmapTab (with metadata), RoadmapStep, RoadmapStepTrophy, TrophyGuide |
| `trophies/services/roadmap_service.py` | Business logic layer |
| `api/roadmap_views.py` | REST API endpoints (staff-only) |
| `trophies/views/roadmap_views.py` | RoadmapDetailView (public) + RoadmapEditorView (staff) |
| `plat_pursuit/urls.py` | URL patterns for detail + editor |
| `templates/trophies/roadmap_detail.html` | Detail page template |
| `templates/trophies/partials/roadmap/` | Detail page partials (header, TOC, steps, guides, metrics, DLC nav) |
| `static/js/roadmap-detail.js` | Detail page JavaScript (scrollspy, TOC, interactions) |
| `templates/trophies/roadmap_edit.html` | Editor template |
| `static/js/roadmap_editor.js` | Editor JavaScript |
| `templates/trophies/partials/game_detail/roadmap_tab_content.html` | Game detail CTA card |
| `templates/trophies/partials/game_detail/community_tabs_section.html` | Community section (ratings + reviews + roadmap CTA) |
| `templates/trophies/partials/dashboard/roadmap_recommendations.html` | Dashboard module |
| `templates/community/partials/roadmap_recruitment_strip.html` | Community hub recruitment strip |

## Gotchas and Pitfalls

- **Concept.absorb() must handle roadmaps**: Any changes to the roadmap model relationships must be reflected in `absorb()`.
- **Trophy IDs, not FKs**: Roadmap models reference trophies by `trophy_id` (IntegerField), not FK. Deleted trophies won't cascade-delete roadmap associations. Templates handle missing trophies gracefully.
- **Guide metadata vs. community ratings**: `RoadmapTab.difficulty` is the author's assessment. `UserConceptRating.difficulty` is community-submitted. These are completely separate systems displayed in different locations with different labels.
- **YouTube URL validation**: Server-side regex validates youtube.com/youtu.be domains. The `youtube_embed_url` template filter extracts video IDs for iframe embedding.
- **Tab auto-creation**: The editor automatically creates RoadmapTab records for any ConceptTrophyGroups that don't have one.
- **JSON data injection**: Editor template uses Django's `json_script` tag for safe JSON serialization.
- **DLC URL routing**: Base game uses `/roadmap/` (no trailing group ID). DLC uses `/roadmap/<group>/`. The `edit/` pattern is registered before these in urls.py, so no shadowing.
- **Progress for anonymous users**: When not logged in, `profile_earned` is empty, progress shows 0/N, and earned indicators don't render. The guide is still fully usable.
