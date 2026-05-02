# Collectibles Sub-Page System

Per-game collectibles guide as a sibling resource to Roadmap. Authors catalog every collectible item (feathers, riddler trophies, korok seeds, etc.); readers get a tool — sort, filter, search, mark-as-found, with optional account sync — instead of the scroll-through-pages-of-ads experience the existing collectibles sites force on trophy hunters.

## Architecture Overview

One Collectibles page per Concept (opt-in). The whole page lives behind the same draft/published lifecycle as Roadmap, with mirrored lock + branch + revision infrastructure so authoring is consistent across both surfaces. Concept-scoped (not Game-scoped) since collectibles are usually identical across PS4/PS5 stacks of the same game — the same as Roadmap.

The page composes three building blocks:
- **CollectibleType**: per-page custom types defined by the author ("Feathers", "Riddler Trophies", "Korok Seeds"). Drives the page's tab strip, with each type assigned a color from a constrained palette.
- **CollectibleArea**: per-page custom areas / chapters / regions. Used for grouping items into per-area sections on the rendered page AND for the "where am I now?" filter.
- **Collectible**: the actual items. Always belongs to a type; optionally to an area. Location is markdown rendered via `render_roadmap_markdown` (full feature set: spoilers, callouts, tables, controller icons, trophy mentions).

Per-user found state lives in `UserCollectibleProgress` — sparse, only collected items have rows. Anonymous viewers get the same shape via localStorage on the reader-side JS layer (Phase 3+).

## Build Phases

This system is being built in phases. Each phase ships independently:

- **Phase 1 (current — schema + minimal viewer)**: All models + migrations. Concept.absorb() updated. Django admin registration so staff can build a page end-to-end via admin for early testing. Bare-bones reader page that lists everything in default order — no filters / sort / search / mark-as-found yet.
- **Phase 2 (editor)**: Lock + branch + merge UI mirroring the roadmap editor. Single-row CRUD + manage types/areas modals.
- **Phase 3 (reader UX)**: Type tabs, filter chips, sort dropdown, search, mark-as-found with localStorage, per-area progress headers, per-type SEO URLs.
- **Phase 4 (account sync)**: API endpoints for syncing localStorage progress to authenticated accounts; cross-device consistency.
- **Phase 5 (bulk import / spreadsheet round-trip)**: XLSX export with dropdown validation; XLSX/CSV import with row-by-row preview and ownership warnings.
- **Phase 6 (roadmap integration)**: CTA card on roadmap detail page; deep-linking from roadmap content.
- **Phase 7 (polish)**: Empty states, edge cases, performance pass, SEO verification.

The full plan (with rationale for each decision) lives in `~/.claude/plans/indexed-tallying-codex.md`.

## File Map

| File | Purpose |
|------|---------|
| `trophies/models.py` | All 7 models (Collectibles, CollectibleType, CollectibleArea, Collectible, UserCollectibleProgress, CollectiblesEditLock, CollectiblesRevision) |
| `trophies/services/collectibles_service.py` | Read-side helpers: get_collectibles_for_display, get_user_progress, compute_progress_summary |
| `trophies/views/collectibles_views.py` | CollectiblesDetailView (public reader page) |
| `trophies/admin.py` | Admin registrations for all 7 models — used for early data entry until Phase 2 lands the editor |
| `templates/trophies/collectibles_detail.html` | Reader page template |
| `templates/trophies/partials/collectibles/collectible_card.html` | Single-item card partial |
| `plat_pursuit/urls.py` | URL: `/games/<np_id>/collectibles/` |
| `trophies/migrations/0221_collectibles_system.py` | Initial schema migration |

## Data Model

### Collectibles

OneToOne with Concept. Page-level container.

- `concept` (OneToOne FK)
- `status`: `'draft' | 'published'`
- `intro_text`: markdown rendered at top of page
- `created_by`, `last_edited_by` (Profile FKs)

### CollectibleType

Per-page custom type. Author-defined.

- `collectibles` (FK)
- `name`, `slug` (unique within page)
- `color`: choice from constrained palette (info / success / warning / error / accent / secondary / primary / neutral)
- `icon`: optional Heroicon name
- `order`: tab display order

### CollectibleArea

Per-page custom area / chapter / region. Optional grouping.

- `collectibles` (FK)
- `name`, `slug` (unique within page)
- `order`: in-game progression order

### Collectible

The items.

- `collectibles`, `type`, `area` (FKs; area is nullable)
- `name`, `description`, `location` (markdown), `prerequisites`, `video_url`
- `images`: JSONField list of `{url, alt, caption}`, capped at 8 server-side
- `is_missable`, `is_dlc`, `is_postgame` (filter flags)
- `order`: in-area sort
- `created_by`, `last_edited_by` (Profile FKs)

`Meta.ordering = ['area__order', 'order', 'name']` — this is the "in-game order" sort.

### UserCollectibleProgress

Sparse — only collected items have rows.

- `profile`, `collectible` (FKs)
- `found_at` (auto)
- `unique_together = ('profile', 'collectible')`

### CollectiblesEditLock + CollectiblesRevision

Mirror RoadmapEditLock + RoadmapRevision exactly. Same idle (15min) + hard cap (1hr) lock semantics. Revisions kept forever as JSON snapshots.

## Concept.absorb() Integration

Collectibles is OneToOne with Concept, so the absorb logic is simpler than for Roadmap (which is per-CTG):

- If `self` (target concept) has no Collectibles and `other` (source) does, the source's Collectibles is moved over (concept FK re-pointed).
- If both have Collectibles, source's cascade-deletes with `other`. We never merge two pages — the data model doesn't support it and trying would lose author intent.

Child models (types / areas / items / locks / revisions / user-progress) follow Collectibles automatically via FK CASCADE — no per-table absorb logic needed.

## URL Structure

Phase 1:
- `/games/<np_communication_id>/collectibles/` — public reader page

Phases 3+:
- `/games/<np_communication_id>/collectibles/<type_slug>/` — per-type SEO-friendly URL with its own `<title>` and meta description
- `/games/<np_communication_id>/collectibles/edit/` — staff editor (Phase 2)
- `/api/v1/collectibles/...` — editor + progress sync APIs (Phase 2 / 4)

## Gotchas and Pitfalls

- **Concept-scoped, not Game-scoped**: Collectibles attaches to Concept (matches Roadmap). PS4 and PS5 stacks of the same game share the page. Don't accidentally key off Game.
- **Type/area slugs are authoritative**: Display names can be renamed; the slug is what the spreadsheet round-trip (Phase 5) and URL routing (Phase 3) reference. Treat slug as the stable identifier.
- **Image limit (8 per item) is a server-side rule** — enforce on save once Phase 2 editor lands. JSONField won't enforce on its own.
- **Image URLs in spreadsheet round-trip are read-only**: Authors can't add or replace images via CSV — that path stays in the editor UI. The CSV column is informational only ("yes, this row has 2 images attached").
- **CollectiblesRevision keeps full JSON snapshots forever** — same wiki-style policy as RoadmapRevision. Worth noting for storage growth on highly-edited pages.
- **`Collectible.area` is nullable** with `on_delete=SET_NULL`: deleting a CollectibleArea unsets its items' area FK rather than deleting the items. The reader page renders area-less items in a trailing "Misc / Unassigned" section.
- **Phase 1 has no editor UI** — author all data via Django admin (`/admin/trophies/collectibles/`). The schema is final; the editor is just deferred.

## Related Docs

- [Roadmap System](roadmap-system.md): sibling resource. Same authoring pattern, shared markdown render path (`render_roadmap_markdown`), shared `ImageUploadModal` (once Phase 2 editor lands).
- [Roadmap Roles, Locks & Revisions](roadmap-roles-and-revisions.md): same role hierarchy and lock semantics will apply to Collectibles in Phase 2.
- Plan: `~/.claude/plans/indexed-tallying-codex.md` — full design rationale + decision history for every phase.
