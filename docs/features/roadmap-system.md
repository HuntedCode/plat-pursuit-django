# Roadmap System

> Staff-authored game guides with ordered steps, general tips, trophy guides, YouTube embeds, and guide metadata. Each game/DLC gets its own dedicated roadmap detail page with sticky TOC, scrollspy navigation, and personal progress tracking.

## Overview

The roadmap system provides structured game guides surfaced as dedicated pages. One roadmap per game (Concept), tabbed by trophy group (base game + DLCs). Each trophy group tab gets its own detail page URL. Authoring is gated by a three-tier role system (writer/editor/publisher) on top of a guide-level edit lock and permanent revision history — see the [Roadmap Roles, Locks & Revisions](roadmap-roles-and-revisions.md) doc for the authoring flow.

Replaces the previous checklist system (DB tables retained, UI removed).

## Data Model

### Roadmap
One-to-one with Concept. Has `status` (draft/published) and `created_by` (Profile).

### RoadmapTab
One per ConceptTrophyGroup within a roadmap. Stores content fields (`general_tips`, `youtube_url`) and guide metadata fields (`difficulty`, `estimated_hours`, `min_playthroughs`). Auto-created when the editor page loads. The two derived fields `youtube_channel_name` and `youtube_channel_url` cache the oEmbed lookup result for the page-level YouTube URL — see "YouTube Attribution" below.

`online_required` and `missable_count` are **derived properties** computed from the per-trophy `is_online` and `is_missable` flags rather than stored fields. They roll up automatically — tag a single trophy as missable and the roadmap's missable count goes up by one without the author needing to keep a separate total in sync.

**Guide metadata** is the author's assessment, distinct from community ratings (`UserConceptRating`). Metadata is displayed as a badge strip at the top of the roadmap detail page, labeled "Author Assessment" to avoid confusion.

### RoadmapStep
Ordered stages within a tab. Each has `title`, `description`, `youtube_url`, and `order`. Step-level YouTube URLs also have cached `youtube_channel_name` / `youtube_channel_url` fields populated by the same oEmbed flow as the page-level video.

### RoadmapStepTrophy
Associates trophies with steps by `trophy_id` (IntegerField, not FK to Trophy). Uses PSN trophy_id which is consistent across game stacks within a concept.

### TrophyGuide
Per-trophy guide text within a tab. Identified by `trophy_id`. One guide per trophy per tab. Optional `youtube_url` (with cached `youtube_channel_name` / `youtube_channel_url`) for a per-trophy video walkthrough; renders inline beneath the body when the trophy row is expanded. A guide may exist with only a YouTube URL (video-only, no body).

## YouTube Attribution

All three YouTube URL fields (Roadmap, RoadmapStep, TrophyGuide) cache channel info on save via [trophies/services/youtube_oembed_service.py](../../trophies/services/youtube_oembed_service.py). The merge service calls `fetch_attribution(url)` whenever a `youtube_url` is set or changed; the result populates the `youtube_channel_name` and `youtube_channel_url` fields on the same record. A failed lookup (timeout, deleted video, network error) silently leaves the cached fields empty — the embed still renders, just without the "Thanks to CHANNEL" line.

The editor's three YouTube inputs share one debounced live-preview handler (`YoutubeAttribution` in `static/js/roadmap_editor.js`) that hits [`GET /api/youtube/attribution-lookup/`](../reference/api-endpoints.md) as the author types. The preview is purely advisory — the canonical attribution is whatever the server resolves on save, not what the editor displayed.

The shared reader-side render lives in [templates/trophies/partials/roadmap/video_embed.html](../../templates/trophies/partials/roadmap/video_embed.html), used by all three embed surfaces.

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

### Markdown Features (Roadmap-Scoped)

Roadmap markdown bodies (general tips, step descriptions, trophy guides) are rendered through the `render_roadmap_markdown` template filter, which calls `ChecklistService.process_markdown(text, icon_set, enable_spoilers=True)`. Two extensions on top of the standard markdown pass:

- **Controller-icon shortcodes**: `:square:`, `:triangle:`, `:l2:`, `:dpad-up:`, etc., resolved to inline SVG glyphs via `trophies/util_modules/controller_icons.py`. The `icon_set` argument (`'ps4'` or `'ps5'`) is sourced from `Game.controller_icon_set` and selects the platform variant.
- **Spoiler tags**: Discord-style `||hidden text||` wraps content in a click-to-reveal span. Multi-token (bold, links, controller icons) and multi-line content inside `||...||` is supported via non-greedy DOTALL matching. Literal `||` inside fenced code blocks survives because `_apply_spoilers` splits on `<code>`/`<pre>` regions before substituting.
- **Callouts**: GitHub-style blockquote markers (`> [!NOTE]`, `> [!TIP]`, `> [!WARNING]`, `> [!IMPORTANT]`) on the first line of a blockquote convert the whole quote into a colored callout box (`<div class="callout callout-{type}">`). Body content can include any other supported markdown — lists, images, links, controller icons, spoilers. The transform runs in `_apply_callouts` after bleach but before the generic blockquote-styling regex, so plain blockquotes (no `[!TYPE]` opener) keep their default treatment.
- **Tables**: GFM pipe-table syntax (`| Header | Header |\n|--------|--------|\n| Cell | Cell |`). Header alignment markers (`:--`, `--:`, `:--:`) are supported. The post-bleach pass wraps every `<table>` in a `prose-roadmap-table-wrap` div for horizontal scroll on narrow viewports, so wide tables don't break out of their card. markdown2's tables extra emits `style="text-align:..."` for alignment cells, which we rewrite to Tailwind utility classes (`text-left`/`text-right`/`text-center`) **before** bleach so the `style` attribute can stay off the allowlist.

The plain `render_markdown` filter (used by reviews, etc.) does NOT enable spoilers, keeping the syntax scoped to roadmap surfaces. Reveal state is in-memory only via `PlatPursuit.SpoilerToggle` in `static/js/utils.js` (no localStorage; refresh re-hides).

### Game Detail Page (CTA)
- Top-level Roadmap card sits directly under the game header, above the Community card. Promoted out of the Community section so it can't be missed during a scroll-by.
- Filled state is a primary-tinted card with a stat chip strip (steps, guides, estimated hours, difficulty, missables, online required, playthroughs, video walkthrough — chips only render when the underlying field has data), a 3-step "Walkthrough Preview" list with overflow indicator, and a prominent "View Full Roadmap" button. Author byline (compact avatars) sits next to the title; staff Edit button is right-aligned.
- The whole card is click-to-navigate (excluding nested links/buttons) via the `.roadmap-cta-link` JS hook that lives in `roadmap_cta_card.html`.
- DLC tab bar inside this card reuses the `.community-tab-btn` / `.community-tab-panel` class pattern, so clicking a DLC tab in this card or the Community card swaps both panels in sync. The shared switching JS lives in `community_tabs_section.html`.
- Empty state (no roadmap for the active CTG): neutral "opportunity" card with Discord CTAs (Request a Roadmap / Join the Team) plus the staff Create button.

## URL Structure

| URL | View | Description |
|-----|------|-------------|
| `/games/<id>/roadmap/` | `RoadmapDetailView` | Base game roadmap |
| `/games/<id>/roadmap/<group>/` | `RoadmapDetailView` | DLC roadmap (group = `001`, `002`, etc.) |
| `/games/<id>/roadmap/edit/` | `RoadmapEditorView` | Staff editor (all tabs) |

## Discoverability Surfaces

| Surface | File | When it shows | Purpose |
|---------|------|---------------|---------|
| **Game detail CTA (top card)** | `roadmap_cta_card.html` + `roadmap_cta_filled.html` | Top of game detail page when a published roadmap exists | Primary link to the dedicated roadmap page; renders stat chips + step preview |
| **Game detail empty state** | `roadmap_cta_card.html` + `roadmap_cta_empty.html` | Top of game detail page when no roadmap exists for the active CTG | Demand capture + author recruitment |
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
| `templates/trophies/partials/game_detail/roadmap_cta_card.html` | Top-level Roadmap CTA card wrapper (header + DLC tab bar + per-CTG panels) |
| `templates/trophies/partials/game_detail/roadmap_cta_filled.html` | Filled-state body: stat chips, step preview, View Full Roadmap button |
| `templates/trophies/partials/game_detail/roadmap_cta_empty.html` | Empty-state body: Discord CTAs + optional staff Create button |
| `templates/trophies/partials/game_detail/community_tabs_section.html` | Community section (ratings + reviews; owns the shared DLC tab-switching JS) |
| `templates/trophies/partials/dashboard/roadmap_recommendations.html` | Dashboard module |
| `templates/community/partials/roadmap_recruitment_strip.html` | Community hub recruitment strip |

## Gotchas and Pitfalls

- **Concept.absorb() must handle roadmaps**: Any changes to the roadmap model relationships must be reflected in `absorb()`.
- **Trophy IDs, not FKs**: Roadmap models reference trophies by `trophy_id` (IntegerField), not FK. Deleted trophies won't cascade-delete roadmap associations. Templates handle missing trophies gracefully.
- **Guide metadata vs. community ratings**: `RoadmapTab.difficulty` is the author's assessment. `UserConceptRating.difficulty` is community-submitted. These are completely separate systems displayed in different locations with different labels.
- **YouTube URL validation**: Server-side regex validates youtube.com/youtu.be domains. The `youtube_embed_url` template filter extracts video IDs for iframe embedding.
- **YouTube attribution caching**: The `youtube_channel_name` / `youtube_channel_url` fields are server-derived. Editor JS never writes them to the branch payload — the merge service fetches oEmbed and overwrites on every `youtube_url` change. If you need to refresh stale channel info, clear the URL field, save, then re-paste the URL.
- **oEmbed timeout is hard-set to 3s**: A slow YouTube response will cause attribution to be saved as empty rather than blocking the merge. The embed still renders correctly without it.
- **Tab auto-creation**: The editor automatically creates RoadmapTab records for any ConceptTrophyGroups that don't have one.
- **JSON data injection**: Editor template uses Django's `json_script` tag for safe JSON serialization.
- **DLC URL routing**: Base game uses `/roadmap/` (no trailing group ID). DLC uses `/roadmap/<group>/`. The `edit/` pattern is registered before these in urls.py, so no shadowing.
- **Progress for anonymous users**: When not logged in, `profile_earned` is empty, progress shows 0/N, and earned indicators don't render. The guide is still fully usable.
