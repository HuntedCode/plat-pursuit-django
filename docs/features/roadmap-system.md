# Roadmap System

> Staff-authored game guides with ordered steps, general tips, trophy guides, and YouTube embeds.

## Overview

The roadmap system provides structured game guides on each game's detail page. One roadmap per game (Concept), tabbed by trophy group (base game + DLCs). Staff-only authoring via a dedicated editor page.

Replaces the previous checklist system (DB tables retained, UI removed).

## Data Model

### Roadmap
One-to-one with Concept. Has `status` (draft/published) and `created_by` (Profile).

### RoadmapTab
One per ConceptTrophyGroup within a roadmap. Stores `general_tips` (prose) and `youtube_url` (optional). Auto-created when the editor page loads.

### RoadmapStep
Ordered stages within a tab. Each has `title`, `description`, and `order`.

### RoadmapStepTrophy
Associates trophies with steps by `trophy_id` (IntegerField, not FK to Trophy). Uses PSN trophy_id which is consistent across game stacks within a concept.

### TrophyGuide
Per-trophy guide text within a tab. Identified by `trophy_id`. One guide per trophy per tab.

## Architecture

### Trophy Reference Strategy
Trophies belong to Game (not Concept). A Concept can span multiple Games (PS4/PS5 stacks). Roadmap models store `trophy_id` rather than FK to Trophy. When rendering, trophy display data (name, icon, type) is resolved from the specific Game in the URL.

### Concept.absorb()
Roadmap migration is handled after CTG migration in `absorb()`. If the target concept has no roadmap, the source's roadmap moves over and its tabs are re-pointed to the target's CTGs. If both concepts have roadmaps, the target's is kept.

## Pages

### Editor (`/games/<np_communication_id>/roadmap/edit/`)
- Staff-only (uses `@staff_member_required`)
- Tab bar for base game / DLC groups
- Per-tab: drag-reorderable steps, trophy picker, general tips, YouTube URL, trophy guides
- Debounced autosave on text fields
- Publish/unpublish toggle

### Game Detail Page
- Roadmap section renders after the ad unit, before trophies
- Only shows when roadmap is published
- Staff sees "Edit Roadmap" button (or "Create Roadmap" CTA if none exists)
- Tab switching for DLC groups (client-side)
- YouTube embed via iframe (URL validated server-side)

## API Endpoints (Staff-Only)

| Method | Endpoint | Description |
|--------|----------|-------------|
| PATCH | `/api/v1/roadmap/<id>/tab/<id>/` | Update tab (tips, YouTube) |
| GET/POST | `/api/v1/roadmap/<id>/tab/<id>/steps/` | List/create steps |
| PATCH/DELETE | `/api/v1/roadmap/<id>/tab/<id>/steps/<id>/` | Update/delete step |
| POST | `/api/v1/roadmap/<id>/tab/<id>/steps/reorder/` | Reorder steps |
| PUT | `/api/v1/roadmap/<id>/tab/<id>/steps/<id>/trophies/` | Set step trophy associations |
| PUT/DELETE | `/api/v1/roadmap/<id>/tab/<id>/trophy-guides/<trophy_id>/` | Create/update/delete trophy guide |
| POST | `/api/v1/roadmap/<id>/publish/` | Publish or unpublish |

All endpoints require staff authentication via SessionAuthentication + IsAdminUser.

## Key Files

| File | Purpose |
|------|---------|
| `trophies/models.py` | Roadmap, RoadmapTab, RoadmapStep, RoadmapStepTrophy, TrophyGuide |
| `trophies/services/roadmap_service.py` | Business logic layer |
| `api/roadmap_views.py` | REST API endpoints |
| `trophies/views/roadmap_views.py` | Editor page view |
| `templates/trophies/roadmap_edit.html` | Editor template |
| `static/js/roadmap_editor.js` | Editor JavaScript |
| `templates/trophies/partials/game_detail/roadmap_section.html` | Display partial |

## Gotchas and Pitfalls

- **Concept.absorb() must handle roadmaps**: Any changes to the roadmap model relationships must be reflected in `absorb()`.
- **Trophy IDs, not FKs**: Roadmap models reference trophies by `trophy_id` (IntegerField), not FK. This means deleted trophies won't cascade-delete roadmap associations. The display template handles missing trophies gracefully.
- **YouTube URL validation**: Server-side regex validates youtube.com/youtu.be domains. The `youtube_embed_url` template filter extracts video IDs for iframe embedding.
- **Tab auto-creation**: The editor automatically creates RoadmapTab records for any ConceptTrophyGroups that don't have one. New DLC groups added after initial roadmap creation are handled seamlessly.
- **JSON data injection**: Editor template uses Django's `json_script` tag for safe JSON serialization (prevents XSS via special characters in trophy names/descriptions).
