# Franchise System

Browse and detail pages for game franchises and collections, sourced from IGDB. The browse list lives at `/franchises/`; each franchise has a detail page at `/franchises/<slug>/` that groups multiple versions of the same game together (similar to PSNProfiles' "stages" concept) and surfaces user progress across the franchise when the viewer has a linked PSN profile. Franchises and collections are both stored in a single `Franchise` model distinguished by `source_type`; the infrastructure lives in the [IGDB Integration](../architecture/igdb-integration.md) doc while this doc covers the user-facing feature.

## Architecture Overview

The franchise pages exist because a normalized IGDB layer already captured the data (`Franchise` / `ConceptFranchise` tables), but nothing surfaced it to users. Without these pages, players couldn't discover that multiple games they own belong to the same series, and couldn't see their progress across a franchise at a glance.

Two distinct IGDB taxonomies feed this system: **franchises** ("Resident Evil") are top-level IP umbrellas, and **collections** ("Resident Evil Main Series") are curated sub-series. They live in separate IGDB ID namespaces — franchise id 222 and collection id 222 are completely different entities — which the data model handles via a composite `(igdb_id, source_type)` unique constraint. At the UI level, the two types are mostly treated as one: browse shows them side-by-side with identical cards, detail pages render identically regardless of type. The distinction only surfaces on game detail pages where a user is actively investigating a specific title.

IGDB also distinguishes the singular `franchise` field (a game's primary identity) from the plural `franchises` array (tie-ins / featured IPs). Disney Dreamlight Valley's main franchise is "Disney"; "Mickey Mouse" and "Frozen" are tie-ins. The `ConceptFranchise.is_main` flag captures this, and the detail page's Games tab shows only games where this franchise is main. A second "Also Featured" tab lists games where it appears as a tie-in, so the full relationship network stays visible without cluttering the primary view.

The browse page filters aggressively to stay useful. Only franchises that are at least one game's main, and collections that contain at least one "orphan" game (a game with no franchise-type link), appear in the browse list. Redundant collections like "Resident Evil Main Series" (whose games all already have the Resident Evil franchise) stay hidden. A solo-entry toggle (default off) hides franchises/collections with only a single game to keep the page focused on actual series.

## File Map

| File | Purpose |
|------|---------|
| `trophies/views/franchise_views.py` | `FranchiseListView` (browse) + `FranchiseDetailView` (grouped games + user progress) |
| `templates/trophies/franchise_list.html` | Browse page with search, sort, and solo-entry toggle |
| `templates/trophies/franchise_detail.html` | Detail page with poster hero, user progress stats, tabs |
| `templates/trophies/partials/franchise_list/browse_results.html` | HTMX partial for the filtered results grid |
| `templates/trophies/partials/franchise_list/franchise_cards.html` | Individual browse card |
| `templates/trophies/partials/franchise_detail/game_groups_list.html` | Reusable game-group list (used by both Games and Also Featured tabs) |
| `templates/trophies/partials/game_detail/franchise_lines.html` | "Franchise / Also Featured / Collections" lines on the game detail About card |
| `trophies/models.py` (Franchise, ConceptFranchise) | Data models — see [IGDB Integration](../architecture/igdb-integration.md) for full docs |
| `core/hub_subnav.py` | Adds "Franchises" to the Browse hub sub-nav |

## Data Model

Both `Franchise` and `ConceptFranchise` are fully documented in [IGDB Integration](../architecture/igdb-integration.md#data-model). Key points for the feature:

- `Franchise.source_type`: `'franchise'` or `'collection'`. Browse filters franchises by `is_main=True` and collections by "has orphan concept".
- `ConceptFranchise.is_main`: true for at most one franchise-type link per concept. Never true for collections.

## Key Flows

### Browse Page Query

The browse queryset applies three filters to the `Franchise` table:

1. **Type filter**: `source_type='franchise'` rows must have at least one `is_main=True` link. `source_type='collection'` rows pass through (we haven't excluded anything yet).
2. **Game-count annotations**:
   - `game_count` counts distinct concepts (IGDB-unified games).
   - `version_count` counts distinct Game rows (PS4/PS5/EU/NA as separate records).
3. **Final cut**:
   - Franchises: `version_count > 0`
   - Collections: `version_count > 0` AND `has_orphan_concept=True` (at least one member concept has zero franchise-type links — meaning this collection is the only discovery path for that game).

The "orphan concept" subquery is the mechanism that keeps redundant collections hidden. "Resident Evil Main Series" doesn't surface because every RE game already has the Resident Evil franchise on it. "Astro Bot" DOES surface because its games have no franchise-type link — the collection is their only IGDB taxonomy.

A final optional filter (default on) hides entries with `game_count < 2`. Users opt into single-game entries via the `?show_solo=1` URL parameter.

### Representative Cover Art

Each browse card shows cover art for its most recent release, with a three-tier fallback chain to handle missing data gracefully:

1. `title_image` (PSN store art)
2. IGDB cover constructed from `IGDBMatch.igdb_cover_image_id`
3. `title_icon_url` (generic PS icon)
4. Folder icon placeholder

Picked via three parallel `Subquery` annotations so the fallback happens in the template (not SQL), keeping the query simple. For franchise-type rows, only games where the franchise is main are considered; for collection-type rows, any link counts (collections never have `is_main=True`).

### Detail Page Grouping

The detail view fetches all games in concepts linked to the franchise, then groups them by `IGDBMatch.igdb_id`. Concepts that share an IGDB ID represent the same game across different platforms / regions (e.g., Resident Evil 4 Remake on PS4 and PS5). Groups are treated as a single "game" card in the UI with stacked "version rows" inside.

Games without an IGDB match become their own single-entry groups. This preserves them in the list rather than dropping them.

Each group is then partitioned:

- **Games tab** (`main_groups`): groups where the concept's link to this franchise has `is_main=True`. These are the canonical "this franchise" games.
- **Also Featured tab** (`also_featured_groups`): groups where the link is `is_main=False`. Tie-ins, crossovers, featured IPs.
- **Collections tab** (`related_entries`): separate list of Franchise rows with the opposite `source_type` that share at least one concept with this franchise. Acts as cross-reference navigation.

The tab bar only renders when at least one non-Games tab has content, so simple franchises (no tie-ins, no collections) don't see empty tabs.

### User Progress Integration

When the viewer has a linked PSN profile, the detail view runs one additional query to fetch `ProfileGame` rows for every game in the franchise. Each `Game` object gets a `.user_pg` attribute attached so the template can render per-version progress without N+1 queries.

Aggregate stats (games played, versions played, trophies earned, completion %) operate on the `main_groups` set only — tie-ins and collections don't pad the franchise-wide totals. Anonymous users and users with no progress see the totals-only view (just `X games`, `Y versions`, `Z trophies`, `W platinums`).

Each version row shows:

- **Anonymous / no profile**: trophy count strip (bronze/silver/gold/platinum)
- **Logged in with progress**: compact conic-gradient progress ring with the percentage inside, plus a platinum icon if earned
- **Logged in, no progress on this version**: small "UNPLAYED" label

### Game Detail About Card

The game detail page's About card shows franchise/collection relationships via three labeled lines rendered by `templates/trophies/partials/game_detail/franchise_lines.html`. The view (`GameDetailView._build_concept_context`) walks the prefetched `concept_franchises` and partitions into three buckets:

- **Franchise**: the single `is_main=True` franchise-type link (at most one).
- **Also Featured**: tie-in franchises (`source_type='franchise'`, `is_main=False`). Capped at 3 visible with a `<details>`/`<summary>` "+ N more" disclosure for the rest.
- **Collections**: collection-type links. Same capped-at-3-with-disclosure pattern.

Concepts not yet re-enriched (no link flagged `is_main=True`) fall back to showing everything under a single "Franchise(s):" label so pre-backfill data is still useful.

## Sort Options

Detail page sort (applied to both Games and Also Featured tabs):

| Sort key | Default | Behavior |
|----------|---------|----------|
| `release` | ✓ | Release date, oldest first |
| `release_desc` | | Release date, newest first |
| `alpha` | | Alphabetical A→Z |
| `alpha_desc` | | Alphabetical Z→A |
| `versions` | | Most versions first |
| `trophies` | | Most trophies first |

Games without an IGDB-known release date sort to the end on ascending, start on descending.

Browse page sort is simpler: alphabetical (default), reverse alphabetical, most games, fewest games.

## Integration Points

- [IGDB Integration](../architecture/igdb-integration.md): data models, enrichment pipeline, all management commands
- [IA and Sub-Nav](../architecture/ia-and-subnav.md): "Franchises" item in the Browse hub sub-nav
- [Data Model](../architecture/data-model.md): general model overview; franchise-specific model docs live in IGDB Integration

## Gotchas and Pitfalls

- **Franchise vs collection is an internal distinction, not a UI one**: The browse page treats them uniformly on purpose. The eyebrow label on the detail header always says "FRANCHISE" regardless of `source_type`. The only place the distinction is surfaced is the game detail About card, where users investigating a specific game benefit from seeing the full relationship shape. Resist the urge to add "Collection" badges to browse cards or the franchise detail header — it was tried and removed as clutter that drew attention to an internal concern.

- **Browse query visibility is load-bearing**: The orphan-concept subquery and the solo-entry filter together determine which rows surface. If the browse page suddenly gets noisy, `python manage.py franchise_stats --samples 20` is the first diagnostic — it breaks down exactly what's being shown and hidden with sample names.

- **Data corruption symptoms**: If mis-linked games appear (e.g. "College Football 25" linked to "Army of Two"), run `python manage.py inspect_franchise_data --search "College Football"` FIRST before attempting fixes. The output's `[3] Drift detected` section tells you whether the problem is upstream IGDB data, our enrichment logic, or stale DB state. See [IGDB Integration](../architecture/igdb-integration.md) for the specific bug class this catches.

- **`is_main` precedence must stay in sync**: `IGDBService._create_concept_franchises` (live enrichment) and `backfill_franchise_main_flag` (recovery command) both derive the main flag. They MUST use identical precedence rules (plural[0] first, fall back to singular). If you change one, change both and retest.

- **Progress rings use conic-gradient, not SVG**: The `w-7 h-7` progress ring on version rows is a pure CSS conic-gradient with a masked inner circle — no SVG, no JS. Integer percentages only (`ProfileGame.progress` is an IntegerField). Font size is `text-[8px]` to fit inside the ring cleanly; don't bump it without checking every browser.

- **Detail page sorts operate on groups, not games**: Sorting is applied AFTER the IGDB-ID grouping, so "Most versions first" is groups-with-most-versions-first. Sorting individual games within a group would change the row order within each game card, which is a different (and less useful) behavior.

- **Anonymous stats on franchise detail**: The aggregate stats row calculation is always computed, even for anonymous users — it's cheap and `user_franchise_stats` is the template's branching key. Don't guard the calculation itself with `if profile`, only the UI rendering.

## Management Commands

See [IGDB Integration → Management Commands](../architecture/igdb-integration.md#management-commands) for:

- `rebuild_franchises_from_cache`: Full rebuild from cached raw_response, no IGDB API calls
- `backfill_franchise_main_flag`: Narrower — only recomputes is_main
- `franchise_stats`: Read-only diagnostic
- `inspect_franchise_data`: Single-concept / single-franchise diagnostic

## Related Docs

- [IGDB Integration](../architecture/igdb-integration.md): data models, enrichment logic, management commands, recovery procedures
- [IA and Sub-Nav](../architecture/ia-and-subnav.md): Browse hub sub-nav placement
- [Design System](../reference/design-system.md): card anatomy, responsive breakpoints, and the cover-art fallback chain used throughout
