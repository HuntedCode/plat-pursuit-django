# Franchise System

Browse and detail pages for game franchises and collections, sourced from IGDB. The browse list lives at `/franchises/`; each franchise has a detail page at `/franchises/<slug>/` that groups multiple versions of the same game together (similar to PSNProfiles' "stages" concept) and surfaces user progress across the franchise when the viewer has a linked PSN profile. Franchises and collections are both stored in a single `Franchise` model distinguished by `source_type`; the infrastructure lives in the [IGDB Integration](../architecture/igdb-integration.md) doc while this doc covers the user-facing feature.

## Architecture Overview

The franchise pages exist because a normalized IGDB layer already captured the data (`Franchise` / `ConceptFranchise` tables), but nothing surfaced it to users. Without these pages, players couldn't discover that multiple games they own belong to the same series, and couldn't see their progress across a franchise at a glance.

Two distinct IGDB taxonomies feed this system: **franchises** ("Resident Evil") are top-level IP umbrellas, and **collections** ("Resident Evil Main Series") are curated sub-series. They live in separate IGDB ID namespaces — franchise id 222 and collection id 222 are completely different entities — which the data model handles via a composite `(igdb_id, source_type)` unique constraint. At the UI level, the two types are mostly treated as one: browse shows them side-by-side with identical cards, detail pages render identically regardless of type. The distinction only surfaces on game detail pages where a user is actively investigating a specific title.

Every franchise IGDB lists for a game becomes an equal link — there's no "primary franchise" distinction. Disney Dreamlight Valley appears under "Disney", "Mickey Mouse", AND "Frozen" simultaneously. Admins can hide the occasional bad link via `ConceptFranchise.is_excluded=True`; combined with `Concept.franchises_locked=True` the override survives future enrichment refreshes.

The browse page filters aggressively to stay useful. Only franchises with at least one non-excluded link, and collections that contain at least one "orphan" game (a game with no franchise-type link), appear in the browse list. Redundant collections like "Resident Evil Main Series" (whose games all already have the Resident Evil franchise) stay hidden. A solo-entry toggle (default off) hides franchises/collections with only a single game to keep the page focused on actual series.

## File Map

| File | Purpose |
|------|---------|
| `trophies/views/franchise_views.py` | `FranchiseListView` (browse) + `FranchiseDetailView` (grouped games + user progress) |
| `templates/trophies/franchise_list.html` | Browse page with search, sort, and solo-entry toggle |
| `templates/trophies/franchise_detail.html` | Detail page with poster hero, user progress stats, tabs |
| `templates/trophies/partials/franchise_list/browse_results.html` | HTMX partial for the filtered results grid |
| `templates/trophies/partials/franchise_list/franchise_cards.html` | Individual browse card |
| `templates/trophies/partials/franchise_detail/game_groups_list.html` | Reusable game-group list (single unified list on the franchise detail page) |
| `templates/trophies/partials/game_detail/franchise_lines.html` | "Franchises / Collections" lines on the game detail About card |
| `trophies/models.py` (Franchise, ConceptFranchise) | Data models — see [IGDB Integration](../architecture/igdb-integration.md) for full docs |
| `core/hub_subnav.py` | Adds "Franchises" to the Browse hub sub-nav |

## Data Model

Both `Franchise` and `ConceptFranchise` are fully documented in [IGDB Integration](../architecture/igdb-integration.md#data-model). Key points for the feature:

- `Franchise.source_type`: `'franchise'` or `'collection'`. Browse filters franchises by "has at least one non-excluded link" and collections by "has orphan concept".
- `ConceptFranchise.is_excluded`: admin override that hides a specific link from browse / detail / badge coverage. Default False. Sticky across enrichment refresh ONLY when `concept.franchises_locked=True`.
- `ConceptFranchise.is_spinoff`: true when IGDB types a game's membership in a **collection** as a "Spin-off" (e.g. Agents of Mayhem under Saints Row). Collection links only; always false for franchises. Spin-off members are hidden from the collection's game list/counts but still shown on the game's *own* detail About card (a spin-off legitimately belongs to its parent series from the game's side).

## Key Flows

### Browse Page Query

The browse queryset applies three filters to the `Franchise` table:

1. **Type filter**: `source_type='franchise'` rows must have at least one non-excluded link. `source_type='collection'` rows pass through (we haven't excluded anything yet).
2. **Game-count annotations**:
   - `game_count` counts distinct concepts (IGDB-unified games).
   - `version_count` counts distinct Game rows (PS4/PS5/EU/NA as separate records).
3. **Final cut**:
   - Franchises: `version_count > 0`
   - Collections: `version_count > 0` AND `has_orphan_concept=True` (at least one member concept has zero franchise-type links — meaning this collection is the only discovery path for that game).

The "orphan concept" subquery is the mechanism that keeps redundant collections hidden. "Resident Evil Main Series" doesn't surface because every RE game already has the Resident Evil franchise on it. "Astro Bot" DOES surface because its games have no franchise-type link — the collection is their only IGDB taxonomy.

A final optional filter (default on) hides entries with `game_count < 2`. Users opt into single-game entries via the `?show_solo=1` URL parameter.

**User-facing filters** (lay on top of the queryset above):
- `?query=` — case-insensitive substring search on `name`.
- `?sort=` — `alpha`, `alpha_inv`, `games`, `games_inv` (see `FRANCHISE_SORT_CHOICES`).
- `?type=` — `all` (default), `franchise`, or `collection`. Renders as a radio-chip group in the toolbar (sr-only inputs + peer-checked button styling, same pattern as Company role chips). Junk values clamp to `all`. **The orphan-concept rule on collections is dropped when `type=collection`** — picking the chip is an explicit "show me everything in this namespace" signal, so name-shared pairs like the Spider-Man franchise and the Spider-Man collection both surface. The default and `type=franchise` views keep the orphan rule (default view stays curated; `type=franchise` excludes collections entirely so the rule is moot anyway).
- `?show_solo=` — `1` to show single-game entries.

Browse cards wear a colored type badge under the name so users can tell franchises from collections at a glance: **Franchise** (`badge-primary`) for top-level IPs and **Collection** (`badge-info`) for sub-series. Same template lives at `templates/trophies/partials/franchise_list/franchise_cards.html`.

### Representative Cover Art

Each browse card shows cover art for its most recent release, with a three-tier fallback chain to handle missing data gracefully:

1. `title_image` (PSN store art)
2. IGDB cover constructed from `IGDBMatch.igdb_cover_image_id`
3. `title_icon_url` (generic PS icon)
4. Folder icon placeholder

Picked via three parallel `Subquery` annotations so the fallback happens in the template (not SQL), keeping the query simple. Non-excluded, non-spin-off links contribute equally; `_MOST_RECENT_RELEASE_ORDER` is the tiebreak.

### Detail Page Grouping

The detail view fetches all games in concepts linked to the franchise (excluding `is_spinoff=True` members, so a Series like Saints Row doesn't list games IGDB types as spin-offs of it — franchise-type links are never spin-offs, so this is a no-op for them), then groups them by `IGDBMatch.igdb_id`. Concepts that share an IGDB ID represent the same game across different platforms / regions (e.g., Resident Evil 4 Remake on PS4 and PS5). Groups are treated as a single "game" card in the UI with stacked "version rows" inside.

Games without an IGDB match become their own single-entry groups. This preserves them in the list rather than dropping them.

All non-excluded, non-spin-off linked groups appear as a single unified game list on the detail page — the "main vs tie-in" partition is gone. A separate **Collections tab** (`related_entries`) still lists Franchise rows of the opposite `source_type` that share at least one concept with this franchise; this acts as cross-reference navigation.

The tab bar only renders when the Collections tab has content, so simple franchises (no collections) don't see empty tabs.

### User Progress Integration

When the viewer has a linked PSN profile, the detail view runs one additional query to fetch `ProfileGame` rows for every game in the franchise. Each `Game` object gets a `.user_pg` attribute attached so the template can render per-version progress without N+1 queries.

Aggregate stats (games played, versions played, trophies earned, completion %) operate on the full non-excluded set. Anonymous users and users with no progress see the totals-only view (just `X games`, `Y versions`, `Z trophies`, `W platinums`).

Each version row shows:

- **Anonymous / no profile**: trophy count strip (bronze/silver/gold/platinum)
- **Logged in with progress**: compact conic-gradient progress ring with the percentage inside, plus a platinum icon if earned
- **Logged in, no progress on this version**: small "UNPLAYED" label

### Game Detail About Card

The game detail page's About card shows franchise/collection relationships via two labeled lines rendered by `templates/trophies/partials/game_detail/franchise_lines.html`. The view (`GameDetailView._build_concept_context`) walks the prefetched `concept_franchises` and partitions non-excluded links into two buckets:

- **Franchise(s)**: all `source_type='franchise'` links. Capped at 3 visible with a `<details>`/`<summary>` "+ N more" disclosure for the rest.
- **Collections**: all `source_type='collection'` links. Same capped-at-3-with-disclosure pattern.

`is_excluded=True` links are filtered out entirely. The legacy "Franchise: X / Also Featured" partition is gone — every IGDB-listed franchise appears equally now.

## Sort Options

Detail page sort (applied to the unified game list):

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

- **`is_excluded` is sticky only under the lock**: `ConceptFranchise.is_excluded=True` survives an enrichment refresh only when `concept.franchises_locked=True`. The writer doesn't touch the column directly, but on an unlocked concept the ROW itself gets wiped + recreated on every refresh (the wipe is part of `_apply_enrichment`). Document the lock requirement when staff sets an exclusion.

- **Spin-off flag is collection-only and lives on the link**: `ConceptFranchise.is_spinoff` is set only for collection (Series) memberships, from IGDB's `/collection_memberships` type (2 = Spin-off). A game can be a normal Member of one series and a Spin-off of another, so the flag is per-link, never per-concept. It suppresses the game from the *collection's* list/counts and from collection badge stage coverage, but NOT from the game's own About card (a spin-off still belongs to its parent series). The signal is not in `raw_response`, so the only ways to populate it are live enrichment (one extra IGDB call when a game has collections) or the `backfill_collection_spinoffs` command (which re-queries IGDB). Don't expect a cache rebuild to recover it.

- **Progress rings use conic-gradient, not SVG**: The `w-7 h-7` progress ring on version rows is a pure CSS conic-gradient with a masked inner circle — no SVG, no JS. Integer percentages only (`ProfileGame.progress` is an IntegerField). Font size is `text-[8px]` to fit inside the ring cleanly; don't bump it without checking every browser.

- **Detail page sorts operate on groups, not games**: Sorting is applied AFTER the IGDB-ID grouping, so "Most versions first" is groups-with-most-versions-first. Sorting individual games within a group would change the row order within each game card, which is a different (and less useful) behavior.

- **Anonymous stats on franchise detail**: The aggregate stats row calculation is always computed, even for anonymous users — it's cheap and `user_progress_stats` is the template's branching key. Don't guard the calculation itself with `if profile`, only the UI rendering.

- **Grouping / stats logic is shared with the Company pages.** `trophies/services/game_grouping_service.py` owns `build_igdb_groups()`, `sort_groups()`, `pick_hero_cover()`, `compute_user_progress_stats()`, `fetch_user_progress_map()`, and the three cover-art Subquery factories. Both `FranchiseDetailView` and `CompanyDetailView` use these. Don't duplicate the logic inline in one view — update the service so both features stay in lockstep. The shared `templates/trophies/partials/franchise_detail/game_groups_list.html` partial (also used by company role sections) expects a generic `user_progress_stats` context key, not a franchise-specific one.

## Management Commands

See [IGDB Integration → Management Commands](../architecture/igdb-integration.md#management-commands) for:

- `rebuild_franchises_from_cache`: Full rebuild from cached raw_response, no IGDB API calls
- `rebuild_franchises_from_cache --force`: bypasses `franchises_locked` when curated data is also corrupted. Combine with `--wipe` for a full reset that ignores the lock entirely.
- `backfill_collection_spinoffs`: Stamps `is_spinoff` on collection links (re-queries IGDB `/collection_memberships`; the type isn't in cached raw_response). Supports `--dry-run`, `--limit`, `--batch-size`
- `franchise_stats`: Read-only diagnostic
- `inspect_franchise_data`: Single-concept / single-franchise diagnostic

## Related Docs

- [IGDB Integration](../architecture/igdb-integration.md): data models, enrichment logic, management commands, recovery procedures
- [Company System](company-system.md): sibling feature using the same shared grouping service and games-list partial
- [IA and Sub-Nav](../architecture/ia-and-subnav.md): Browse hub sub-nav placement
- [Design System](../reference/design-system.md): card anatomy, responsive breakpoints, and the cover-art fallback chain used throughout
