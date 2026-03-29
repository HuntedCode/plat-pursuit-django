# IGDB Integration

Enriches PlatPursuit Concepts with data from the Internet Game Database (IGDB), owned by Twitch/Amazon. Sony's PSN API only provides publisher names. IGDB adds developer info, genre/theme classifications, time-to-beat estimates, game engine data, franchise groupings, VR platform detection, and more.

## Architecture Overview

IGDB acts as a supplementary data layer. PSN remains the source of truth for game identity (concepts, trophy lists, earn data). IGDB enrichment is best-effort: if IGDB is unavailable or a match cannot be found, the system continues normally with PSN data only.

The matching system uses a confidence-based approach with a 7-strategy pipeline. Each Concept is matched to an IGDB game entry through progressively broader searches. Matches above 85% confidence are auto-accepted; 50-84% are flagged for staff review; below 50% are discarded. Staff can approve, reject, or re-match via Django admin or management commands.

Company data is fully normalized. A single Company record represents a real-world studio (e.g. Naughty Dog), linked to Concepts via a ConceptCompany through table that tracks per-game roles (developer, publisher, porting, supporting). This supports developer badges, developer-based challenges, shovelware detection, and stats.

Rate limiting is distributed across all workers via Redis, ensuring all 24 token_keeper processes collectively stay under IGDB's 4 req/sec limit.

## File Map

| File | Purpose |
|------|---------|
| `trophies/services/igdb_service.py` | Core service: auth, search, matching, confidence scoring, enrichment, VR detection, family propagation |
| `trophies/management/commands/enrich_from_igdb.py` | Management command for batch enrichment, search, manual matching, review, refresh |
| `trophies/models.py` (Company, ConceptCompany, IGDBMatch) | Data models for IGDB integration |
| `trophies/admin.py` (CompanyAdmin, IGDBMatchAdmin) | Django admin for match review and company browsing |
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
| **Game engine** | IGDBMatch | `game_engine_name` | "Creation Engine", "Unity", "Unreal Engine 5" |
| **Cover art** | IGDBMatch | `igdb_cover_image_id` | IGDB image hash for URL construction |
| **Franchise names** | IGDBMatch | `franchise_names` (JSONField) | ["Fallout"] (used for GameFamily suggestions) |
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

### ConceptCompany
M2M through table. Links Concept to Company with role flags: `is_developer`, `is_publisher`, `is_porting`, `is_supporting`. Multiple roles can be true simultaneously. Unique on (concept, company).

### IGDBMatch
OneToOne to Concept. Stores matching metadata (`match_confidence`, `match_method`, `status`), parsed Tier 1 data, and the full raw IGDB response (`raw_response`) for future Tier 2 parsing.

### Concept Additions
- `igdb_genres` (JSONField): Genre names from IGDB, separate from PSN's `genres` field
- `igdb_themes` (JSONField): Theme names from IGDB (no PSN equivalent)

## Key Flows

### 7-Strategy Matching Pipeline

When `IGDBService.match_concept(concept)` is called, strategies run in order until one succeeds:

| # | Strategy | API Calls | What It Catches |
|---|----------|-----------|-----------------|
| 1 | **Family sibling lookup** | 0 | Regional variants (e.g. Asian version inherits from NA match) |
| 2 | **External PSN ID** | 1-2 | Games with PlayStation Store IDs in IGDB |
| 3 | **Exact name query** (PS-filtered) | 1-2 | Base games buried under DLC in fuzzy search (e.g. Batman: Arkham Knight) |
| 4 | **Fuzzy search** (PS-filtered) | 1 | Standard title matching, handles 90%+ of games |
| 5 | **Fuzzy search** (unfiltered) | 1 | PC-first games that came to PlayStation later (e.g. The Finals) |
| 6 | **Alternative name search** | 1-2 | Regional title differences (e.g. "Sly Raccoon" -> "Sly Cooper and the Thievius Raccoonus") |
| 7 | **Truncated title search** | 1 | Series prefix matching (e.g. "Sly 3: Honour Among Thieves" -> search "Sly 3"), capped at pending_review |

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

Each IGDB result is scored against the concept. The score considers:

- **Title similarity**: SequenceMatcher ratio against primary name AND all alternative names
- **Containment**: PSN title is a substring of IGDB name (or vice versa)
- **Main game boost**: +10% for category 0 entries with name containment
- **DLC penalty**: -15% for non-main-game categories
- **Release year proximity**: +5% if within 1 year
- **Publisher match**: +5% if publisher names match

Thresholds: >= 85% auto-accepted, 50-84% pending review, < 50% discarded.

### Enrichment Pipeline

1. Match found with confidence >= 0.85: auto-accepted, enrichment applied immediately
2. Match found with confidence 0.50-0.84: IGDBMatch created with `pending_review` status, enrichment deferred
3. Staff approves pending match via admin action or `--manual`: enrichment applied
4. Enrichment creates Company records, ConceptCompany entries, updates Concept's `igdb_genres`/`igdb_themes`, and adds VR platforms to Games

### Family Propagation

When a Concept is enriched and belongs to a GameFamily, the enrichment automatically propagates to family siblings that lack their own IGDBMatch. This means:
- NA version matches IGDB -> Asian version inherits automatically
- Works during backfill, refresh, and admin approval

If two Concepts match to the same IGDB game but are NOT in the same family, a GameFamilyProposal is automatically created for staff review.

### VR Platform Detection

Sony does not provide VR platform information. During enrichment, if IGDB reports PSVR (platform 165) or PSVR2 (platform 390), the system appends `'PSVR'` or `'PSVR2'` to `Game.title_platform` for all games under that Concept. Only adds, never removes.

### Sync Pipeline Hook

In `token_keeper.py`, after `PsnApiService.create_concept_from_details()` creates a NEW concept:
- `IGDBService.enrich_concept(concept)` is called (best-effort, wrapped in try/except)
- Only fires for newly created concepts (not existing ones)
- Only fires for non-stub concepts (skips PP_ prefixed)

## Integration Points

- **Shovelware Detection** (`trophies/services/shovelware_detection_service.py`): Company `company_size` and `game_engine_name` can be used as additional shovelware signals
- **Stats Service** (`trophies/services/stats_service.py`): Developer aggregation (top developers, unique developer count) alongside existing publisher stats, via bulk ConceptCompany query
- **SEO Tags** (`core/templatetags/seo_tags.py`): Developer as `author` Organization, `timeRequired` ISO 8601 duration, IGDB genres with PSN fallback
- **Game Detail Template**: Developer display, estimated completion time
- **Badge System**: Developer badge type already exists; Stages can group Concepts by developer via ConceptCompany queries

## Gotchas and Pitfalls

- **Concept.absorb() must handle ConceptCompany and IGDBMatch**: When concepts merge, ConceptCompany entries are moved with role merging (OR of flags), and IGDBMatch is transferred if the target lacks one. Already implemented.
- **Distributed rate limiting**: All workers share a Redis sorted set (`igdb_rate_limit`) as a sliding window counter. Set conservatively to 3 req/sec (IGDB allows 4). Do not bypass.
- **IGDB tokens expire**: Access tokens last ~60 days. Cached in Redis (`igdb_access_token`). Auto-refreshes on expiry.
- **IGDB does not return `category` field**: IGDB omits the game category from responses (even for DLC). The system uses a name-pattern heuristic to detect DLC instead.
- **IGDB search buries base games under DLC**: For games with many DLC entries (Batman: Arkham Knight has 30+), the fuzzy search returns only DLC. The exact name query (strategy 3) bypasses this.
- **ALL CAPS titles**: IGDB search handles all-caps poorly. Title cleaning lowercases before searching.
- **Colon in titles**: Colons break IGDB's Apicalypse query parser. Stripped from fuzzy search queries, preserved for exact name `where` clauses.
- **Time-to-beat is community-sourced**: Not all games have it. Newer/niche games often have empty time-to-beat data. Fields are nullable.
- **Duplicate IGDB ID constraint**: When two Concepts match to the same IGDB game (e.g. PS4 and PS5 versions), only the first gets an IGDBMatch record. The second gets enrichment (companies, genres, themes) applied directly without its own match row.
- **Company mergers**: IGDB tracks renames/mergers via `changed_company_id`. The `Company.current_company` property follows the chain. When displaying company names, prefer `current_company` for accuracy.
- **Raw response storage**: The `raw_response` JSONField stores the full IGDB API response (5-20KB per game). Tier 2 data can be parsed later without re-querying.

## Management Commands

| Command | Purpose | Typical Usage |
|---------|---------|---------------|
| `enrich_from_igdb --missing` | Enrich concepts without an IGDB match | `python manage.py enrich_from_igdb --missing` |
| `enrich_from_igdb --concept-id X` | Enrich a single concept | `python manage.py enrich_from_igdb --concept-id 12345` |
| `enrich_from_igdb --refresh` | Re-fetch IGDB data for all accepted matches | `python manage.py enrich_from_igdb --refresh` |
| `enrich_from_igdb --search "query"` | Search IGDB and display results | `python manage.py enrich_from_igdb --search "Batman Arkham Knight"` |
| `enrich_from_igdb --manual ID --concept-id X` | Manually assign an IGDB game | `python manage.py enrich_from_igdb --concept-id 200472 --manual 5503` |
| `enrich_from_igdb --review` | Show pending matches with alternatives | `python manage.py enrich_from_igdb --review` |
| `enrich_from_igdb --force` | Re-match all concepts (overwrites) | `python manage.py enrich_from_igdb --all --force` |
| `enrich_from_igdb --verbose` | Enable detailed search/scoring logs | `python manage.py enrich_from_igdb --missing --verbose` |
| `enrich_from_igdb --dry-run` | Preview without saving | `python manage.py enrich_from_igdb --missing --dry-run` |

## Cache Keys

| Key Pattern | TTL | Purpose |
|-------------|-----|---------|
| `igdb_access_token` | ~60 days (from Twitch) | IGDB API bearer token |
| `igdb_rate_limit` | 5s (auto-expire) | Distributed rate limiter sliding window (Redis sorted set) |

## Related Docs

- [Data Model](data-model.md): Concept, Game, and related models that IGDB enriches
- [Sync Pipeline](../guides/sync-pipeline.md): Where IGDB enrichment hooks into the PSN sync flow
