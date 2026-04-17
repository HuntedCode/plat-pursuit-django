# Engine System

Browse and detail pages for the game engines powering our library (Unreal, Unity, Decima, RE Engine, etc.), sourced from IGDB. The browse list at `/engines/` surfaces every engine linked to at least two games; the detail page at `/engines/<slug>/` has a poster-hero header with engine logo, description, "By [company]" metadata, and an IGDB-ID-grouped list of games. Data models live in the [IGDB Integration](../architecture/igdb-integration.md) doc; this doc covers the user-facing feature.

## Architecture Overview

Engine detail is a **hybrid** of the franchise and genre/theme patterns: a franchise-style **poster hero** on top (logo, description, "By Company" metadata, stat cells, viewer-progress cells, completion bar) paired with the genre/theme-style **flat, filter-drawer-driven paginated game list** below. It's the only page on the site that mixes these two patterns.

**Why the hybrid:** engines carry rich metadata (description, logo, maker companies) that genres/themes don't, which justifies the richer hero. But unlike franchises, games sharing an engine are NOT the same game — Hades and Cuphead both use Unity but they're completely different experiences. IGDB-ID grouping works for franchises because "RE4 PS4 and RE4 PS5 are still RE4," but the same logic doesn't apply to engines. Users browsing a Unity page with thousands of heterogeneous indies need the filter drawer (platforms, ratings, time-to-beat, community flags) far more than they need version-stacking. So the list portion reuses the genre/theme pipeline verbatim.

Shared code:
- **`TagDetailBaseView`** (in `genre_views.py`) — `EngineDetailView` extends it to inherit the filter/sort pipeline, pagination, and shared context. Same base class as `GenreDetailView` and `ThemeDetailView`.
- **`templates/trophies/partials/tag_detail/filter_drawer.html`** — filter drawer shared with Genre/Theme detail. `EngineDetailView` populates `tag_name`, `detail_url_name='engine_detail'`, and `detail_slug` so the drawer form submits back to the engine detail URL.
- **`templates/trophies/partials/tag_detail/browse_results.html`** — paginated game grid shared with Genre/Theme detail.
- **`trophies/services/game_grouping_service.py`** — still used, but ONLY to compute hero totals (`total_games`, `total_versions`, `total_trophies`, `total_platinums`) and user-progress stats across the engine's full library. The game list below the hero is flat; grouping is not visible to the user.

Engine-specific layout tweaks vs. a bare tag detail page:
- **Square logo container** (`w-32 h-32 md:w-40 md:h-40 lg:w-48 lg:h-48`) with `object-contain`. Engine logos are landscape/square, not portrait game-cover proportions.
- **Eyebrow label** is "Game Engine".
- **"By [company]" line** under the name, when the engine has linked maker companies (Epic Games → Unreal, Unity Technologies → Unity). Each company links to its detail page.
- **Description text** in the hero body. IGDB provides usable blurbs ("Cross-platform game engine developed by Epic Games..."); clamped at four lines so the hero stays compact. Admin can trim the stored value if needed.
- **Stat cells + completion bar**. Computed across the engine's full library regardless of the active filter, so "this engine has 5,000 games" stays accurate even when you've narrowed the list to "PS5, rated 4+". Completion bar is labeled "Engine Library Completion."

### Noise filtering

IGDB's `game_engines` field per game is really "software used in production," not "engine this runs on." Sagebrush's IGDB payload has `[Unity, Audacity, Photoshop, Blender]` — only Unity is the engine. Dev tools (Blender, Photoshop, Audacity, Maya, FMOD, etc.) are conflated with real engines.

**Our filter**: at ingestion time, only the first entry in `game_engines` becomes a `ConceptEngine` link. IGDB's ordering reliably puts the real engine first in practice. Admin retains the escape hatch — the `GameEngine` admin page lets curators manually adjust if they spot a mis-ordered game.

**Browse-page safety net**: the list filters engines by `game_count__gte=2`. One-off noise (an obscure indie that mis-ordered Audacity first, creating a 1-game engine row) gets dropped from the browse surface without touching the underlying data.

## File Map

| File | Purpose |
|------|---------|
| `trophies/views/engine_views.py` | `EngineListView` (browse) + `EngineDetailView` (extends `TagDetailBaseView` for the filter pipeline, overlays engine-specific hero context) |
| `templates/trophies/engine_list.html` | Browse page: header card, search + sort, engine grid cards with logo chip |
| `templates/trophies/engine_detail.html` | Detail page: poster hero (logo + eyebrow + "By X" + description + stat cells + completion bar), then the shared filter drawer + paginated game grid |
| `templates/trophies/partials/tag_detail/filter_drawer.html` | Shared filter drawer (genre / theme / engine detail pages) |
| `templates/trophies/partials/tag_detail/browse_results.html` | Shared paginated game grid (genre / theme / engine detail pages) |
| `templates/trophies/partials/game_detail/game_about_card.html` | "Engine:" line in the Quick Facts section, linked to `/engines/<slug>/` when the `ConceptEngine` M2M exists |
| `trophies/services/game_grouping_service.py` | Shared IGDB-ID grouping, cover-art subquery factories, user-progress rollup |
| `trophies/services/igdb_service.py` | Engine enrichment pipeline: pulls `game_engines.{name,slug,description,logo.image_id,companies}`, takes `[0]` only, populates new fields, links maker companies |
| `trophies/models.py` (GameEngine, ConceptEngine, EngineCompany) | Data models — see [IGDB Integration](../architecture/igdb-integration.md#data-model) |
| `trophies/admin.py` (GameEngineAdmin) | Admin with description/logo_image_id/companies fields + has_logo indicator |
| `core/hub_subnav.py` | BROWSE_HUB `/engines/` prefix + Engines sub-nav item + `engine_detail` URL override |
| `plat_pursuit/urls.py` | `engines_list` and `engine_detail` patterns |
| `trophies/migrations/0199_engine_description_logo_companies.py` | Schema migration: description, logo_image_id, EngineCompany through model |
| `trophies/migrations/0200_enrich_engines_and_prune_links.py` | Data migration: re-enrich existing rows from `IGDBMatch.raw_response`, prune `ConceptEngine` links to first-engine-only |

## Data Model

- **`GameEngine`**: `igdb_id` (unique), `name`, `slug`, `description`, `logo_image_id`, `companies` (M2M through `EngineCompany`). `logo_url(size='logo_med')` method mirrors `Company.logo_url` and returns `None` when no logo.
- **`ConceptEngine`**: through model `concept ↔ engine`, `unique_together`. Ingestion enforces one per concept, but the model allows many for admin flexibility and historical data.
- **`EngineCompany`**: through model `engine ↔ company`, `unique_together`. Populated from IGDB's `game_engines.companies` field per engine.

## Key Flows

### Browse Page Query

[`EngineListView.get_context_data`](../../trophies/views/engine_views.py) runs one annotated query over `GameEngine`:

- `game_count` — distinct Games reached via `engine_concepts__concept__games`.
- `.filter(game_count__gte=2)` — drops one-off noise.
- Text search on `name`; sort options alpha / most games / avg rating / most players / most platinums earned.

Each card shows the engine logo (or the circuit-board icon fallback), name, and game count.

### Detail Page: Hero + Filter-Driven Game List

[`EngineDetailView`](../../trophies/views/engine_views.py) extends `TagDetailBaseView` so the paginated game list reuses the genre/theme pipeline verbatim. On top, it layers engine-specific context:

1. `dispatch()` loads `self.engine` from the slug (Http404 on miss) and prefetches `companies`.
2. `get_tag_filter()` returns `Q(concept__concept_engines__engine=self.engine)` — this is what `TagDetailBaseView.get_queryset` uses to scope the Games queryset before the filter drawer's filters + sort are applied.
3. `get_context_data()`:
   - Calls `super().get_context_data()` — returns the standard ListView context with paginated, filtered Games plus `object_list`.
   - Sets `tag_name`, `tag_type='Engine'`, `detail_url_name='engine_detail'`, `detail_slug` so the shared filter drawer knows which URL to submit to.
   - Adds `engine` and `engine_companies` for the hero.
   - When NOT an HTMX request (hero isn't re-rendered on partial swaps), fetches all games tied to the engine, runs them through `game_grouping_service.build_igdb_groups`, and rolls up `total_games` / `total_versions` / `total_trophies` / `total_platinums` / `user_progress_stats`. These totals reflect the engine's FULL library, not the filtered subset — consistent with franchise/company hero stats.
   - Calls `get_shared_context()` to fill in platform/region choices, rating map, user progress map, etc.
4. Template renders: breadcrumb → hero card → filter drawer → paginated game grid.

Hero totals computation is skipped on HTMX partial requests (`HX-Request` header) because only `browse_results.html` gets swapped on filter updates — the hero isn't re-rendered, so fetching all-engine-games would be wasted work.

### Ingestion (one engine per concept)

[`_create_normalized_tags`](../../trophies/services/igdb_service.py) reads `igdb_data['game_engines']` and processes only the first entry. It:

1. `slugify`s the IGDB slug (strips URL-unsafe chars like parens).
2. `get_or_create`s the `GameEngine` row with description + logo_image_id populated on create.
3. On existing rows, **backfills only empty fields** — admin-curated description/logo_image_id is never clobbered.
4. Links the concept via `ConceptEngine.objects.get_or_create`.
5. For each IGDB company ID in `engines[0].companies`, links via `EngineCompany` — but only when the `Company` row already exists in our DB (from involved_companies enrichment on some game). Missing companies are silently skipped.

### Game Detail Link-out

When `game.concept.concept_engines.all` is non-empty, [`game_about_card.html`](../../templates/trophies/partials/game_detail/game_about_card.html) renders:

```html
<a href="{% url 'engine_detail' slug=ce.engine.slug %}">{{ ce.engine.name }}</a>
```

Fallback to the plain-text `igdb.game_engine_name` is kept for concepts that haven't been re-enriched since the normalization rollout. With the one-engine-per-concept invariant, the `{% for %}` loop always renders exactly one row in practice.

## Integration Points

- [IGDB Integration](../architecture/igdb-integration.md): GameEngine + ConceptEngine + EngineCompany models, enrichment pipeline, IGDB query shape
- [Franchise System](franchise-system.md): sibling detail page using the same grouping service + game-groups-list partial
- [Company System](company-system.md): another sibling; engine maker companies (`engine.companies`) link out to their company detail pages
- [IA and Sub-Nav](../architecture/ia-and-subnav.md): "Engines" sub-nav item in the Browse hub
- [Design System](../reference/design-system.md): poster hero, stat cells, cover-art fallback chain

## Gotchas and Pitfalls

- **Engines are NOT tags.** Engines have their own view file (`engine_views.py`), their own detail template (`engine_detail.html`), and their own layout (poster hero). Genres and themes share `tag_detail.html` and `TagDetailBaseView`; engines don't. Don't merge them — the poster-hero layout is driven by metadata that genres/themes don't have.
- **One `ConceptEngine` per concept at ingestion.** If you change the ingestion loop to process all entries in `game_engines`, you'll reintroduce the Sagebrush problem (Photoshop appears on game detail). The blocklist alternative was considered and rejected — first-entry-only is cheaper and empirically sufficient.
- **`EngineCompany` link creation is lossy.** If a concept is enriched before any of its engine's maker companies have been enriched (unusual but possible on cold-start syncs), the `EngineCompany` links don't get created. A follow-up sync of any game by that company will trigger `_create_normalized_tags` again and the links will fill in. If this becomes a problem, add a backfill command or do the linking during the company enrichment path instead.
- **Backfill migration preserves admin edits.** `0200_enrich_engines_and_prune_links` only writes `description`/`logo_image_id` when the existing value is empty. Re-running the migration is safe — admins can curate engine descriptions without worrying about an enrichment sync overwriting them.
- **IGDB slug parens must be slugified.** "CTG (Core Technology Group)" → IGDB slug `ctg-(core-technology-group)` → breaks Django's default `<slug:>` URL converter. `0198_normalize_tag_slugs` cleaned existing data; ingestion now runs every IGDB slug through `slugify()`.
- **`game_count__gte=2` threshold is browse-only, not admin-hidden.** Low-count engines still exist in the DB and still have detail pages accessible via direct URL. The filter only prevents them from cluttering the browse list. If you need to delete noise permanently, do it in admin.
- **Admin-manual override path.** If IGDB orders badly for a specific game (e.g. Blender listed first), the admin can delete the bad `ConceptEngine` link and create the right one. If a GameEngine row is garbage (e.g. Photoshop somehow ended up with a game_count > 2), admin can delete the entire `GameEngine` row — cascade will remove all `ConceptEngine` links to it.
- **Engine hero uses the engine's OWN logo, not a representative game cover.** Franchise pages use `pick_hero_cover(groups)` to show the newest game's cover in the hero. Engines skip that entirely — games under an engine are varied (Hades and Cuphead both use Unity) so a single representative cover doesn't communicate anything meaningful. The engine logo is the only hero art.
- **Engine detail is the only page that mixes the franchise hero with the genre/theme list.** Franchise and Company detail use grouped lists (`game_groups_list.html`); Genre and Theme detail use flat lists. Engine is the one hybrid — rich hero + flat list. Don't try to "unify" this toward one of the other patterns without understanding why: the hero is justified by engine-specific metadata, and the flat list is justified by engines producing heterogeneous game collections where IGDB-ID grouping has near-zero value.
- **"By [company]" line appears only when `EngineCompany` links exist.** For niche/custom engines where IGDB doesn't list maker companies, the byline is absent (not a broken "By " with nothing after). Don't add a fallback — the silence is accurate.

## Related Docs

- [IGDB Integration](../architecture/igdb-integration.md): data models, enrichment pipeline, query shape
- [Franchise System](franchise-system.md): sibling feature; shares grouping service + game-groups-list partial
- [Company System](company-system.md): sibling feature; engine maker companies cross-link into company detail pages
- [Design System](../reference/design-system.md): poster hero, stat cells, cover-art fallback chain
