# IGDB Integration

Enriches PlatPursuit Concepts with data from the Internet Game Database (IGDB), owned by Twitch/Amazon. Sony's PSN API only provides publisher names. IGDB adds developer info, genre/theme classifications, time-to-beat estimates, game engine data, franchise groupings, VR platform detection, and more.

## Architecture Overview

IGDB acts as a supplementary data layer. PSN remains the source of truth for game identity (concepts, trophy lists, earn data). IGDB enrichment is best-effort: if IGDB is unavailable or a match cannot be found, the system continues normally with PSN data only.

The matching system uses a confidence-based approach with a 6-strategy pipeline. Each Concept is matched to an IGDB game entry through progressively broader searches. Each concept matches independently (no family-based inheritance). Matches above 85% confidence are auto-accepted; matches at or above the review threshold (50%) are flagged for staff review. Platform overlap with the concept's games is required: results without at least one shared PlayStation platform are skipped entirely. Staff can approve, reject, or re-match via Django admin or management commands.

Company data is fully normalized. A single Company record represents a real-world studio (e.g. Naughty Dog), linked to Concepts via a ConceptCompany through table that tracks per-game roles (developer, publisher, porting, supporting). This supports developer badges, developer-based challenges, shovelware detection, and stats.

Franchise and collection data follows the same normalization pattern. A single `Franchise` record represents either an IGDB franchise ("Resident Evil") or an IGDB collection ("Resident Evil Main Series"), distinguished by `source_type`. Links go through a `ConceptFranchise` table with an `is_main` flag marking the game's primary franchise. Franchises and collections are **separate IGDB namespaces** — franchise id 222 is "NCAA", collection id 222 is "Army of Two" — so the `Franchise` model uses a composite `(igdb_id, source_type)` unique constraint, never `igdb_id` alone. The franchise browse page at `/franchises/` surfaces franchises that are at least one game's main, plus collections that contain at least one game with no franchise-type link (so collection-only series like Astro Bot remain discoverable). See [Franchise System](../features/franchise-system.md) for the user-facing feature.

Rate limiting is distributed across all workers via Redis, ensuring all 24 token_keeper processes collectively stay under IGDB's 4 req/sec limit.

## File Map

| File | Purpose |
|------|---------|
| `trophies/services/igdb_service.py` | Core service: auth, search, matching, confidence scoring, enrichment, VR detection |
| `trophies/management/commands/enrich_from_igdb.py` | Management command for batch enrichment, search, manual matching, review, refresh |
| `trophies/models.py` (Company, ConceptCompany, Franchise, ConceptFranchise, IGDBMatch) | Data models for IGDB integration |
| `trophies/admin.py` (CompanyAdmin, FranchiseAdmin, ConceptFranchiseAdmin, IGDBMatchAdmin) | Django admin for match review, company browsing, and franchise curation |
| `trophies/views/franchise_views.py` | `FranchiseListView` (browse) + `FranchiseDetailView` (per-franchise page) |
| `trophies/management/commands/rebuild_franchises_from_cache.py` | Rebuild Franchise/ConceptFranchise rows from cached IGDBMatch.raw_response (no API calls) |
| `trophies/management/commands/backfill_franchise_main_flag.py` | Recompute `is_main` flags from cached raw_response without re-enriching |
| `trophies/management/commands/franchise_stats.py` | Read-only diagnostic: taxonomy coverage, browse surfacing counts, orphan concepts |
| `trophies/management/commands/inspect_franchise_data.py` | Read-only diagnostic: show raw IGDB response vs. stored links for a concept |
| `trophies/token_keeper.py` | Sync pipeline hook (enriches new concepts on creation) |
| `plat_pursuit/settings.py` | IGDB_CLIENT_ID, IGDB_CLIENT_SECRET, threshold settings |

## What Data We Get from IGDB

IGDB stores games as single all-encompassing entries (like our Concept model), not per-platform. One IGDB entry covers all platforms a game appears on.

### Tier 1: Parsed into Structured Fields

Data we extract from the IGDB response and store in dedicated model fields:

| Data | Stored On | Field(s) | Example |
|------|-----------|----------|---------|
| **Developer/Publisher** | Company + ConceptCompany | `name`, `is_developer`, `is_publisher`, etc. | Bethesda Game Studios (dev), Bethesda Softworks (pub) |
| **Company details** | Company | `country`, `logo_image_id`, `parent`, `company_size`, `start_date`, `description` | Country: 840 (US), Parent: ZeniMax Media |
| **Company hierarchy** | Company | `parent` (FK self), `changed_company` (FK self) | Sony > SIE > Naughty Dog; merger tracking |
| **Genres** | Concept | `igdb_genres` (JSONField) | ["Shooter", "Role-playing (RPG)"] |
| **Themes** | Concept | `igdb_themes` (JSONField) | ["Action", "Science fiction", "Open world"] |
| **Game category** | IGDBMatch | `game_category` | 0=Main Game, 1=DLC, 8=Remake, 9=Remaster, 11=Port |
| **Summary** | IGDBMatch | `igdb_summary` | Short game description |
| **Storyline** | IGDBMatch | `igdb_storyline` | Full plot synopsis |
| **Time to beat** | IGDBMatch | `time_to_beat_hastily`, `_normally`, `_completely` | Speedrun, average, 100% completion (in seconds) |
| **Release date** | IGDBMatch | `igdb_first_release_date` | First release date across all platforms |
| **Game engine (legacy)** | IGDBMatch | `game_engine_name` | Single string, kept for backwards compatibility |
| **Game engine (normalized)** | GameEngine + ConceptEngine | `name`, `slug`, `description`, `logo_image_id`, `companies` | One normalized row per engine; `companies` M2M via `EngineCompany` |
| **Cover art** | IGDBMatch | `igdb_cover_image_id` | IGDB image hash for URL construction |
| **Franchises / collections** | Franchise + ConceptFranchise | `name`, `slug`, `source_type`, `is_main` | Main franchise + tie-ins + collections, each as a linkable row |
| **Franchise names (legacy)** | IGDBMatch | `franchise_names` (JSONField) | ["Fallout"] (kept for backwards compatibility; prefer the normalized `Franchise` model) |
| **Similar games** | IGDBMatch | `similar_game_igdb_ids` (JSONField) | IGDB IDs for future recommendations |
| **External links** | IGDBMatch | `external_urls` (JSONField) | {steam: url, wikipedia: url, official: url, ...} |
| **VR platforms** | Game | `title_platform` (appended) | PSVR/PSVR2 added when IGDB identifies VR platforms |

### Tier 2: Stored in Raw JSON for Future Parsing

The full IGDB API response is stored in `IGDBMatch.raw_response`. This includes data we don't parse yet but can extract later without re-querying:

| Data | Raw JSON Key | Example |
|------|-------------|---------|
| **Keywords** | `keywords` | ["post-apocalyptic", "crafting", "open world", "roguelike"] |
| **Game modes** | `game_modes` | ["Single player", "Multiplayer", "Co-operative"] |
| **Player perspectives** | `player_perspectives` | ["First person", "Third person"] |
| **Age ratings** | (not in current query) | ESRB/PEGI ratings |
| **Per-platform release dates** | `release_dates` | Date + platform + region per entry |
| **Alternative names** | `alternative_names` | Regional titles (JP, KR, EU variants) |
| **Websites** | `websites` | Full list of official, social, store links |
| **Community/critic ratings** | `rating`, `aggregated_rating` | IGDB user + critic average scores |
| **Platform IDs** | `platforms` | Which platforms the game is on |
| **External game IDs** | `external_games` | PSN Store IDs, Steam IDs, etc. |

### What We Query

Two API calls per game:

1. **Game details** (`/games` endpoint): All fields listed above via Apicalypse query with field expansion
2. **Time to beat** (`/game_time_to_beats` endpoint): Separate endpoint, queried by game ID

Platform filter covers the full PlayStation family: PS1 (7), PS2 (8), PS3 (9), PSP (38), Vita (46), PS4 (48), PSVR (165), PS5 (167), PSVR2 (390).

## Data Model

### Company
Normalized game company from IGDB. Fields: `igdb_id` (unique), `name`, `slug`, `description`, `country` (ISO 3166-1 numeric), `logo_image_id`, `parent` (FK self for corporate hierarchy), `company_size` (1-9 scale), `start_date`, `changed_company` (FK self for mergers/renames), `change_date`.

Country rendering: use `Company.country_display` (string like "🇺🇸 United States") or `Company.country_info` (`(flag_emoji, name)` tuple). Both delegate to `trophies/util_modules/countries.py`, which maps the ISO 3166-1 numeric code to a name and derives the flag emoji from the alpha-2 code at call time via Unicode regional indicators. Unknown codes return empty / None so templates must tolerate that gracefully.

### ConceptCompany
M2M through table. Links Concept to Company with role flags: `is_developer`, `is_publisher`, `is_porting`, `is_supporting`. Multiple roles can be true simultaneously. Unique on (concept, company).

### Franchise
Normalized IGDB franchise or collection. Fields: `igdb_id` (indexed, NOT globally unique), `name`, `slug` (unique), `source_type` (choice: `'franchise'` or `'collection'`). The composite unique constraint `(igdb_id, source_type)` allows the same numeric ID to exist in both IGDB namespaces without collision — franchise id 222 ("NCAA") and collection id 222 ("Army of Two") get two distinct rows.

### ConceptFranchise
M2M through table. Links Concept to Franchise. `is_main` is true exactly when the Franchise is IGDB's primary franchise for this game (derived from the plural `franchises[0]` field at enrichment time, with the singular `franchise` field as a fallback). Collections never have `is_main=True`. Unique on (concept, franchise), indexed on `is_main` for browse-page queries.

### GameEngine
Normalized IGDB game engine (Unreal, Unity, Decima, RE Engine, etc). Fields: `igdb_id` (unique), `name`, `slug`, `description`, `logo_image_id`, `companies` (M2M through `EngineCompany`). `logo_url(size='logo_med')` method mirrors `Company.logo_url`. Admin-editable via Django admin with `filter_horizontal` on companies.

### ConceptEngine
M2M through table linking Concept to GameEngine. Unique on (concept, engine). IGDB's `game_engines` array conflates real engines with dev tools (Photoshop, Blender, Audacity alongside Unity), so ingestion only creates ONE `ConceptEngine` per concept — the first entry in IGDB's array, which in practice is the real engine. Admin can manually adjust for edge cases where IGDB's ordering is wrong.

### EngineCompany
M2M through table linking GameEngine to Company (Epic Games → Unreal, Unity Technologies → Unity). Populated during `_create_normalized_tags` from IGDB's `game_engines.companies` field. Only creates links for companies that already exist in the DB — missing maker companies are silently skipped and will fill in on subsequent enrichment of any of their games.

### IGDBMatch
OneToOne to Concept. Stores matching metadata (`match_confidence`, `match_method`, `status`), parsed Tier 1 data, and the full raw IGDB response (`raw_response`) for future Tier 2 parsing.

**`cover_url(size='cover_big')`** method: Constructs an IGDB Cloudinary image URL from `igdb_cover_image_id`. Returns `f'https://images.igdb.com/igdb/image/upload/t_{size}/{igdb_cover_image_id}.png'`, or `None` if no image ID is stored. Same pattern as `Company.logo_url()`. Available sizes include `cover_small` (90x128), `cover_big` (264x374), `720p` (1280x720), `1080p` (1920x1080).

`status` values:
- `auto_accepted`: Matched at >= 85% confidence and enrichment applied automatically.
- `pending_review`: Matched at 50-84% confidence, awaiting staff approval.
- `accepted`: Match approved manually after pending review.
- `rejected`: Match rejected manually (rare; usually rematched instead).
- `no_match`: Matching ran but no IGDB result was found. The row exists as a marker so subsequent default enrichment passes skip the concept and so the unmatched review queue can surface it for manual intervention. `igdb_id`, `igdb_name`, `match_confidence`, and `match_method` are all blank/null on these rows.

### RematchSuggestion
FK to Concept (many per concept). Triage queue entry for `rematch_auto_accepted` proposals that didn't clear the auto-apply bar. Snapshots the old and proposed IGDB id/name/confidence/method plus the full proposed IGDB payload (`proposed_raw_response`) so approval can swap the IGDBMatch without re-querying IGDB. `status` is `pending`, `approved`, or `dismissed`; admin actions on `RematchSuggestionAdmin` drive the state transitions. See [Phase 3: Rematch Sweep](#phase-3-rematch-sweep) for the apply rules.

### Concept Additions
- `igdb_genres` (JSONField): Genre names from IGDB, separate from PSN's `genres` field
- `igdb_themes` (JSONField): Theme names from IGDB (no PSN equivalent)

## Key Flows

### 6-Strategy Matching Pipeline

When `IGDBService.match_concept(concept)` is called, strategies run in order until one succeeds:

| # | Strategy | API Calls | What It Catches |
|---|----------|-----------|-----------------|
| 1 | **External PSN ID** | 1-2 | Games with PlayStation Store IDs in IGDB |
| 2 | **Fuzzy search** (PS-filtered) | 1 | Standard title matching, handles 90%+ of games |
| 3 | **Exact name query** (PS-filtered) | 1-2 | Base games buried under DLC in fuzzy search (e.g. Batman: Arkham Knight) |
| 4 | **Fuzzy search** (unfiltered) | 1 | PC-first games that came to PlayStation later (e.g. The Finals); confidence is reduced by 5% to reflect the looser filter |
| 5 | **Alternative name search** | 1-2 | Regional title differences (e.g. "Sly Raccoon" -> "Sly Cooper and the Thievius Raccoonus") |
| 6 | **Truncated title search** | 1 | Series prefix matching (e.g. "Sly 3: Honour Among Thieves" -> search "Sly 3"), capped below auto-accept threshold |

All strategies (except external ID) filter out likely DLC results before scoring, using a name-pattern heuristic (entries with " - Skin", " - Pack", " - DLC", " - Season Pass", etc.).

### Title Cleaning

Before searching IGDB, titles are cleaned to improve match rates:

- **Platform suffixes stripped**: "It Takes Two PS4 & PS5" -> "It Takes Two"
- **Edition suffixes stripped**: "Croc Legend of the Gobbos Platinum Edition" -> "Croc Legend of the Gobbos"
- **Year suffixes stripped**: "Alone in the Dark 2 (1996)" -> "Alone in the Dark 2"
- **Brand prefixes stripped**: "Disney Pixar Toy Story 3" -> "Toy Story 3"
- **Unicode normalized**: smart quotes, bullets, trademark symbols removed
- **Lowercased**: IGDB search handles ALL CAPS poorly

### Confidence Scoring

Each IGDB result is first filtered by **platform overlap** (a hard requirement, not a confidence modifier): the IGDB result must list at least one PlayStation platform that the concept's games also have. Results with no overlap (including VR-only IGDB entries when the concept is not on PSVR/PSVR2) are skipped before scoring runs.

For results that survive the platform filter, the score considers:

- **Title similarity**: SequenceMatcher ratio against primary name AND all alternative names
- **Containment**: PSN title is a substring of IGDB name (or vice versa)
- **Main game boost**: +10% for category 0 entries with name containment
- **DLC penalty**: -15% for non-main-game categories
- **Release year proximity**: +5% if within 1 year
- **Publisher match**: +5% if publisher names match

Thresholds: >= 85% auto-accepted, >= 50% pending review. There is no hard discard floor for results that pass platform overlap; the lowest-scored survivor is still surfaced for staff review (so no signal is lost).

### Enrichment Pipeline

1. Match found with confidence >= 0.85: auto-accepted, enrichment applied immediately
2. Match found with confidence 0.50-0.84: IGDBMatch created with `pending_review` status, enrichment deferred
3. No match found by any of the 6 strategies: IGDBMatch created with `no_match` status (via `IGDBService.record_no_match`). Default enrichment runs skip these on subsequent passes; use `--retry-no-match` to re-attempt them or `--unmatched` to assign manually. `record_no_match` refuses to overwrite an existing accepted/pending/rejected row.
4. Staff approves pending match via admin action or `--manual`: enrichment applied
5. Enrichment creates Company records, ConceptCompany entries, Franchise + ConceptFranchise rows (see below), updates Concept's `igdb_genres`/`igdb_themes`, and adds VR platforms to Games

Each Concept matches independently. Family-based propagation was removed because it caused regional/platform variants to inherit incorrect data when one sibling matched poorly. PS4 and PS5 versions of the same game now each get their own full IGDBMatch record, as do regional siblings, and each is matched and reviewed on its own merits.

### Franchise Enrichment

`IGDBService._create_concept_franchises` walks the IGDB response's `franchise` (singular), `franchises` (plural), and `collections` fields and creates normalized rows for each. Determining which franchise is `is_main`:

1. First entry of the plural `franchises` array wins. IGDB is actively phasing out the singular `franchise` field (per their changelog), and the plural array is what IGDB's own UI surfaces. Within the array, curator-confidence ordering means the first entry is the umbrella IP.
2. Fall back to the singular `franchise` field only when the plural array is empty. Covers older entries curated before the plural field existed.
3. Otherwise no `is_main` flag is set. The concept's `Franchise` links exist but none is primary — typically a collection-only concept (e.g. Astro Bot).

Collections never receive `is_main=True` regardless of how they appear in the response. IGDB's collection taxonomy explicitly does not designate a primary.

Deduplication and identity lookups use the composite `(igdb_id, source_type)` key throughout. An earlier implementation dedup'd by `igdb_id` alone, which silently collapsed franchise id 222 ("NCAA") into collection id 222 ("Army of Two") and corrupted thousands of `ConceptFranchise` links across the database. The recovery was a schema migration (dropping the old global-unique constraint), an enrichment-code fix, and a full rebuild via `rebuild_franchises_from_cache --wipe`. If franchise data ever looks suspicious again, run `inspect_franchise_data` to check for drift between the raw IGDB response and the stored links.

### VR Platform Detection

Sony does not provide VR platform information. During enrichment, if IGDB reports PSVR (platform 165) or PSVR2 (platform 390), the system appends `'PSVR'` or `'PSVR2'` to `Game.title_platform` for all games under that Concept. Only adds, never removes.

### Phase 3: Rematch Sweep

`rematch_auto_accepted` replays the current matching pipeline against every `IGDBMatch` with `status='auto_accepted'`. The goal is to re-evaluate matches made under earlier (inferior) pipeline behaviour now that Phase 2 inputs (search-title selection, best-so-far accumulation, Strategy 6 localized-name, Strategy 7 `/search`, Strategy 9 romanization) are in place. Human-approved matches (`status='accepted'`) are intentionally excluded: the admin already signed off on those outcomes.

For each match the command compares the new pipeline's top candidate against the stored match and applies one of four rules:

| Case | Action |
|---|---|
| Same IGDB id | Skip silently, no record |
| Different id, new confidence >= auto-accept threshold AND > old confidence | Apply new match via `process_match` (clear upgrade) |
| Different id, below threshold OR <= old confidence | Keep old, write a `RematchSuggestion` row for admin review |
| Pipeline returns no match | Keep old, log anomaly |

`RematchSuggestion` rows snapshot the full proposed IGDB payload (`proposed_raw_response`) so the admin action can apply the swap without re-querying. The `RematchSuggestionAdmin` provides per-row context (old vs. proposed id/name/confidence, delta), plus `Apply` and `Dismiss` bulk actions. Apply routes through `process_match`, which overwrites the existing IGDBMatch and re-runs `_apply_enrichment` (family relinking, CJK title promotion, companies, franchises).

Idempotence: re-running the command against the same auto-accepted pool refreshes any existing pending suggestion for the same `(concept, proposed_igdb_id)` pair rather than duplicating rows.

### Sync Pipeline Hook

In `token_keeper.py`, after `PsnApiService.create_concept_from_details()` creates a NEW concept:
- `IGDBService.enrich_concept(concept)` is called (best-effort, wrapped in try/except)
- Only fires for newly created concepts (not existing ones)
- Fires for all concepts including `PP_` stubs. Stubs benefit the most from IGDB enrichment because they lack PSN-side metadata, so excluding them was a regression worth reverting.

## Integration Points

- **Cover Art (IGDB-first)**: `Game.display_image_url` resolves in this order: **trusted IGDB cover → `concept.concept_icon_url` (PSN MASTER, skipped for `PP_*` stub concepts) → `game.title_image` → `game.title_icon_url`**. IGDB is the primary source, not a fallback: enrichment coverage is ~16k of ~18k concepts and IGDB provides consistent portrait aspect ratios where PSN art varies (PS4 ≈ 4:3, PS5 ≈ square). `Concept.get_cover_url(size)` returns the PSN MASTER icon for non-stub concepts, else constructs an IGDB cover URL from `igdb_cover_image_id` for trusted matches. `Concept.cover_url` property provides no-arg access. All game-cover containers across the site use `aspect-[3/4]` with `object-cover object-top`. **All querysets that render covers must `select_related('concept', 'concept__igdb_match')` — this is load-bearing since IGDB is the first lookup on every render.**
- **Shovelware Detection** (`trophies/services/shovelware_detection_service.py`): Company `company_size` and `game_engine_name` can be used as additional shovelware signals
- **Stats Service** (`trophies/services/stats_service.py`): Developer aggregation (top developers, unique developer count) alongside existing publisher stats, via bulk ConceptCompany query
- **SEO Tags** (`core/templatetags/seo_tags.py`): Developer as `author` Organization, `timeRequired` ISO 8601 duration, IGDB genres with PSN fallback
- **Game Detail Template**: Developer display, estimated completion time
- **Badge System**: Developer badge type already exists; Stages can group Concepts by developer via ConceptCompany queries
- **Game Grouping Service** (`trophies/services/game_grouping_service.py`): The Franchise and Company detail pages both group games by IGDB id so multi-platform releases stack as "versions" of one card. `build_igdb_groups()`, `sort_groups()`, `pick_hero_cover()`, `compute_user_progress_stats()`, and parameterised cover-art Subquery factories live here. Both features' detail pages use the shared `templates/trophies/partials/franchise_detail/game_groups_list.html` partial to render per-group cards with identical per-version UI (progress rings, trophy counts, flag badges, etc.). See [Franchise System](../features/franchise-system.md) and [Company System](../features/company-system.md).

## Gotchas and Pitfalls

- **Concept.absorb() must handle ConceptCompany, ConceptFranchise, and IGDBMatch**: When concepts merge, ConceptCompany entries are moved with role merging (OR of flags), ConceptFranchise links are moved with dedup by `franchise_id`, and IGDBMatch is transferred if the target lacks one. Already implemented. Any NEW model with an FK to Concept must be added to `absorb()` too.

- **Franchises and collections are separate IGDB namespaces**: The `Franchise` table stores both, distinguished by `source_type`. The composite `(igdb_id, source_type)` unique constraint is load-bearing — do NOT reinstate global unique on `igdb_id` alone or cross-namespace ID collisions (franchise id 222 vs. collection id 222) will silently corrupt links across the DB. An earlier version shipped with that bug; recovery required a full rebuild. Any code that does `Franchise.objects.get(igdb_id=x)` without also passing `source_type` is at risk.

- **`Franchise.is_main` precedence**: Derived from the plural `franchises[0]` entry first, with the singular `franchise` field as fallback only when the plural array is empty. Mirrored exactly in both `_create_concept_franchises` (enrichment) and `backfill_franchise_main_flag` (recovery command). Changing the precedence requires touching both.
- **Distributed rate limiting**: All workers share a Redis sorted set (`igdb_rate_limit`) as a sliding window counter. Set conservatively to 3 req/sec (IGDB allows 4). Do not bypass.
- **IGDB tokens expire**: Access tokens last ~60 days. Cached in Redis (`igdb_access_token`). Auto-refreshes on expiry.
- **IGDB does not return `category` field**: IGDB omits the game category from responses (even for DLC). The system uses a name-pattern heuristic to detect DLC instead.
- **IGDB search buries base games under DLC**: For games with many DLC entries (Batman: Arkham Knight has 30+), the fuzzy search returns only DLC. The exact name query (strategy 3) bypasses this.
- **ALL CAPS titles**: IGDB search handles all-caps poorly. Title cleaning lowercases before searching.
- **Colon in titles**: Colons break IGDB's Apicalypse query parser. Stripped from fuzzy search queries, preserved for exact name `where` clauses.
- **Time-to-beat is community-sourced**: Not all games have it. Newer/niche games often have empty time-to-beat data. Fields are nullable.
- **Multiple Concepts can share an IGDB ID**: PS4, PS5, PS3, and regional siblings of the same game can each have their own IGDBMatch row pointing at the same `igdb_id`. The unique constraint was dropped (only `db_index=True` remains) so each concept owns its own enrichment lifecycle. As of Phase 2.6, shared-IGDB-id concepts are deterministically grouped into the same `GameFamily` by `_link_concept_to_family` — no external diagnostic command is needed any more.
- **Company mergers**: IGDB tracks renames/mergers via `changed_company_id`. The `Company.current_company` property follows the chain. When displaying company names, prefer `current_company` for accuracy.
- **Raw response storage**: The `raw_response` JSONField stores the full IGDB API response (5-20KB per game). Tier 2 data can be parsed later without re-querying.
- **no_match never overwrites real matches**: `IGDBService.record_no_match()` checks for an existing IGDBMatch first and bails if its status is anything other than `no_match`. This means if `--all` is run and a previously-accepted concept temporarily fails to match (transient IGDB hiccup), the accepted row is preserved. The summary still counts it as `no_match` since the matcher returned nothing, but the DB is left untouched.

## Management Commands

| Command | Purpose | Typical Usage |
|---------|---------|---------------|
| `enrich_from_igdb` (default) | Enrich concepts without any IGDBMatch row (skips `no_match` markers) | `python manage.py enrich_from_igdb` |
| `enrich_from_igdb --concept-id X` | Enrich a single concept | `python manage.py enrich_from_igdb --concept-id 12345` |
| `enrich_from_igdb --refresh` | Re-fetch IGDB data for all accepted matches | `python manage.py enrich_from_igdb --refresh` |
| `enrich_from_igdb --retry-no-match` | Re-run matching against concepts previously recorded as `no_match` | `python manage.py enrich_from_igdb --retry-no-match` |
| `enrich_from_igdb --search "query"` | Search IGDB and display results | `python manage.py enrich_from_igdb --search "Batman Arkham Knight"` |
| `enrich_from_igdb --manual ID --concept-id X` | Manually assign an IGDB game | `python manage.py enrich_from_igdb --concept-id 200472 --manual 5503` |
| `enrich_from_igdb --review` | Show pending matches with alternatives | `python manage.py enrich_from_igdb --review` |
| `enrich_from_igdb --unmatched` | Interactive queue of `no_match` concepts for manual assignment | `python manage.py enrich_from_igdb --unmatched` |
| `enrich_from_igdb --force` | Re-match all concepts (overwrites) | `python manage.py enrich_from_igdb --all --force` |
| `enrich_from_igdb --verbose` | Enable detailed search/scoring logs | `python manage.py enrich_from_igdb --verbose` |
| `enrich_from_igdb --dry-run` | Preview without saving | `python manage.py enrich_from_igdb --dry-run` |
| `rematch_auto_accepted` | Re-run the matching pipeline against every `auto_accepted` match. See [Phase 3: rematch sweep](#phase-3-rematch-sweep). | `python manage.py rematch_auto_accepted --dry-run` |
| `rebuild_franchises_from_cache` | Rebuild Franchise + ConceptFranchise rows from cached `IGDBMatch.raw_response`. No IGDB API calls. Use for schema/logic changes that don't require fresh data. | `python manage.py rebuild_franchises_from_cache --wipe` |
| `backfill_franchise_main_flag` | Recompute `ConceptFranchise.is_main` from cached raw_response using the current precedence rules. Narrower than a full rebuild — only updates the flag, leaves rows otherwise untouched. | `python manage.py backfill_franchise_main_flag --dry-run` |
| `franchise_stats` | Read-only diagnostic: franchise/collection totals, per-concept coverage, browse-page surfacing counts, sample names. Useful for auditing enrichment coverage. | `python manage.py franchise_stats --samples 20` |
| `inspect_franchise_data` | Read-only diagnostic: compare raw IGDB response to stored links for a concept or franchise. First stop when mis-linked games appear. | `python manage.py inspect_franchise_data --search "College Football"` |

## Cache Keys

| Key Pattern | TTL | Purpose |
|-------------|-----|---------|
| `igdb_access_token` | ~60 days (from Twitch) | IGDB API bearer token |
| `igdb_rate_limit` | 5s (auto-expire) | Distributed rate limiter sliding window (Redis sorted set) |

## Related Docs

- [Data Model](data-model.md): Concept, Game, and related models that IGDB enriches
- [Franchise System](../features/franchise-system.md): user-facing franchise/collection browse + detail pages; how main / also-featured / collections surface to users
- [Company System](../features/company-system.md): user-facing developer/publisher browse + detail pages; shares the game-grouping service with franchise pages
- [Token Keeper](token-keeper.md): Where IGDB enrichment hooks into the PSN sync flow (after `create_concept_from_details()` and during the health-check default-concept fallback path)
