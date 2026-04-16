# Company System

Browse and detail pages for game developers, publishers, and other production-credit holders, sourced from IGDB. The browse list at `/companies/` surfaces every company that has at least one linked game; the detail page at `/companies/<slug>/` groups multiple versions of the same game together and surfaces user progress across the studio's catalog. Data models live in the [IGDB Integration](../architecture/igdb-integration.md) doc; this doc covers the user-facing feature.

## Architecture Overview

The company pages closely mirror the franchise pages both visually and architecturally — same poster-hero header, same IGDB-ID game grouping, same per-version progress rings, same stat-cell rhythm. The pages were built second, so they inherit the patterns the franchise rebuild proved out. Shared logic lives in `trophies/services/game_grouping_service.py` and the `templates/trophies/partials/franchise_detail/game_groups_list.html` partial — both features use the identical code and markup for the parts users see as "the same component."

Three notable departures from the franchise pattern, each driven by what companies actually are:

1. **Four roles (Developed / Published / Ported / Supporting Development) stay as vertical sections rather than tabs.** Most companies only populate 1-2 roles, and scrolling through sections preserves the "see everything they've worked on" feel that tabs would hide behind clicks. A compact anchor-link strip at the top lets power users jump.
2. **Community Stats are preserved as a second header strip.** Franchise pages dropped company-wide community ratings (they don't apply). Companies do — "these devs make hard games" is real signal. When a logged-in user views a company page, they see "Your Progress" cells AND "Community Stats" cells stacked in the hero, both always visible.
3. **Country display uses flag emojis + name.** IGDB stores country as ISO 3166-1 numeric codes (840, 392, 826). The browse card and detail header render these as "🇺🇸 United States" via the `Company.country_display` property, which delegates to `trophies/util_modules/countries.py`. Unknown codes render as empty (falls back gracefully).

## File Map

| File | Purpose |
|------|---------|
| `trophies/views/company_views.py` | `CompanyListView` (browse) + `CompanyDetailView` (roles + user progress + community stats) |
| `templates/trophies/company_list.html` | Browse page with search + sort + all filters (role, country, platform, genres, badge series) |
| `templates/trophies/company_detail.html` | Detail page with poster hero, dual stat strips, anchor-link nav, role sections |
| `templates/trophies/partials/company_list/company_cards.html` | Browse card with cover art + logo overlay + country + games·versions badge |
| `templates/trophies/partials/company_detail/role_section.html` | Role header wrapper that delegates to the shared game-groups-list partial |
| `trophies/services/game_grouping_service.py` | Shared IGDB-ID grouping + user progress stats + cover-art subquery factories (also used by franchise pages) |
| `trophies/util_modules/countries.py` | ISO 3166-1 numeric → flag + name mapping |
| `trophies/models.py` (Company, ConceptCompany) | Data models — see [IGDB Integration](../architecture/igdb-integration.md#data-model) |
| `trophies/forms.py` (CompanySearchForm) | Browse-page filter form |
| `trophies/admin.py` (CompanyAdmin) | Admin interface with `country_column` that surfaces the flag+name display |

## Data Model

`Company` and `ConceptCompany` are documented in [IGDB Integration](../architecture/igdb-integration.md#data-model). Key points for this feature:

- `Company.country` is an ISO 3166-1 numeric integer. Read via `Company.country_display` or `Company.country_info`.
- `Company.logo_url(size='logo_med')` constructs IGDB image URLs from the stored `logo_image_id` hash. Returns `None` when absent.
- `ConceptCompany` role flags (`is_developer`, `is_publisher`, `is_porting`, `is_supporting`) are independent booleans. A studio can be true for multiple roles on the same game — the detail page shows the same game group under each role section where it applies.

## Key Flows

### Browse Page Query

[`CompanyListView.get_queryset`](../../trophies/views/company_views.py) runs one annotated query:

- `game_count` — distinct concepts (IGDB-unified games). This is what the card label "X games" means to users.
- `version_count` — distinct Games (individual PSN trophy lists). A game on both PS4 and PS5 counts as 2 versions of 1 game.
- Three cover-art subqueries (`representative_title_image`, `representative_igdb_cover_id`, `representative_title_icon`) ordered newest-first, built via `game_grouping_service.representative_*_subquery` factories so they share code with franchise pages.

Filters from `CompanySearchForm` then layer on: text search on name, role checkboxes, country (ISO numeric match), platform, genres, badge series. Sort options include alphabetical, most/fewest games, highest avg rating, most players, most plats earned.

### Detail Page: Role Grouping

[`CompanyDetailView.get_context_data`](../../trophies/views/company_views.py) fetches every `ConceptCompany` link in one go with `prefetch_related('concept__games')`. One call to `game_grouping_service.build_igdb_groups()` produces the full list of game groups; the view then partitions groups into per-role lists by checking which role flags are set on the `ConceptCompany` records.

**A single group object can appear in multiple role lists.** A game that's both developed AND published by this company lands in both sections with identical stats. Safe because the service produces group dicts once and the view never mutates them after partitioning.

### Detail Page: User Progress

When `request.user.profile` exists, `fetch_user_progress_map` builds `{game_id: ProfileGame}` for every game under the company. `build_igdb_groups` attaches each game's `user_pg` to the game object and rolls up per-group `user_any_progress` / `user_plat_earned` / etc.

`compute_user_progress_stats` then derives the franchise-wide stat dict (`games_played`, `versions_played`, `trophies_earned`, `games_platinumed`, `completion_pct`) scoped across all roles — a game you platinumed counts once regardless of whether the company is credited as developer, publisher, or both.

Anonymous users (or users without a linked profile) get `user_progress_stats = None`, and the hero shows the totals-only view.

## Integration Points

- [IGDB Integration](../architecture/igdb-integration.md): Company + ConceptCompany models, enrichment pipeline, admin
- [Franchise System](franchise-system.md): companion page that uses the same shared grouping/stats service and game-groups-list partial
- [IA and Sub-Nav](../architecture/ia-and-subnav.md): "Companies" sub-nav item in the Browse hub
- [Design System](../reference/design-system.md): cover-art fallback chain, poster-hero proportions, dashboard-style stat cells

## Gotchas and Pitfalls

- **Don't add "Developer" / "Publisher" badges to the page.** Users don't care about IGDB's taxonomy — they care about the games. The role is the section header, not a chip on every card.
- **Country codes are ISO 3166-1 numeric, not alpha-2 or name.** IGDB stores `Company.country` as integers like 840 (US) and 392 (Japan). Never compare against a string name or alpha-2 directly — use `country_info()` / `country_display()` from `trophies.util_modules.countries`. Unknown codes return empty, so the template must tolerate that (it does via the `{% if object.country_display %}` guard).
- **Flag emojis are derived at call time, not stored.** The alpha-2 code in the country table is the source of truth; the emoji is `chr(0x1F1E6 + ord(c) - ord('A'))` for each letter. This works on every modern OS/browser via Unicode regional indicator symbols. Don't store the emoji as a field — it'd double the memory footprint for zero gain.
- **Community stats aren't cached.** `CompanyDetailView` runs fresh rating/player aggregations on every request. For high-traffic companies this is wasteful; a `populate_company_community_stats` management command + Redis cache would be the right move when traffic warrants. Not shipped yet. See [IGDB Integration docs](../architecture/igdb-integration.md) for where to hook in.
- **Game groups are shared across role lists by reference.** A game's group dict object appears in multiple `role_groups[slug]` lists. Never mutate a group after `build_igdb_groups` returns — you'll silently affect every role it appears in. Sorting is fine because `grouping.sort_groups` returns a new list.
- **Merger chain hints ("Subsidiary of X") stay in the hero body.** IGDB tracks renames and acquisitions via `Company.parent` (FK self) and `Company.changed_company` (FK self). The detail header surfaces them as inline text under the name. If a company goes dormant, the "Now operating as Y" hint gives users a click-through to the current entity.
- **Admin method name: `country_column`, NOT `country_display`.** The model has a `country_display` property that returns the rendered string. The admin has a `country_column` method that wraps it with a fallback to the raw numeric code for unknown entries. Don't name them both `country_display` — Python's method lookup gets weird and the property will shadow the admin method.

## Related Docs

- [IGDB Integration](../architecture/igdb-integration.md): data models, enrichment pipeline
- [Franchise System](franchise-system.md): companion feature that shares the grouping service and games-list partial
- [Design System](../reference/design-system.md): poster hero, stat cells, cover-art fallback chain
