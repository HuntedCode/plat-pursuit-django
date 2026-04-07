# IGDB Integration

Enriches PlatPursuit Concepts with data from the Internet Game Database (IGDB), owned by Twitch/Amazon. Sony's PSN API only provides publisher names. IGDB adds developer info, genre/theme classifications, time-to-beat estimates, game engine data, franchise groupings, and more.

## Architecture Overview

IGDB acts as a supplementary data layer. PSN remains the source of truth for game identity (concepts, trophy lists, earn data). IGDB enrichment is best-effort: if IGDB is unavailable or a match cannot be found, the system continues normally with PSN data only.

The matching system uses a confidence-based approach. Each Concept is matched to an IGDB game entry via title + platform search, with optional external ID matching. Matches above 85% confidence are auto-accepted; 50-84% are flagged for staff review; below 50% are discarded. Staff can approve, reject, or re-match via Django admin.

Company data is fully normalized. A single Company record represents a real-world studio (e.g. Naughty Dog), linked to Concepts via a ConceptCompany through table that tracks per-game roles (developer, publisher, porting, supporting). This supports developer badges, developer-based challenges, shovelware detection, and stats.

## File Map

| File | Purpose |
|------|---------|
| `trophies/services/igdb_service.py` | Core service: auth, search, matching, confidence scoring, enrichment |
| `trophies/management/commands/enrich_from_igdb.py` | Management command for batch enrichment |
| `trophies/models.py` (Company, ConceptCompany, IGDBMatch) | Data models for IGDB integration |
| `trophies/admin.py` (CompanyAdmin, IGDBMatchAdmin) | Django admin for match review and company browsing |
| `trophies/token_keeper.py` | Sync pipeline hook (enriches new concepts on creation) |
| `plat_pursuit/settings.py` | IGDB_CLIENT_ID, IGDB_CLIENT_SECRET, threshold settings |

## Data Model

### Company
Normalized game company from IGDB. Fields: `igdb_id` (unique), `name`, `slug`, `description`, `country` (ISO 3166-1 numeric), `logo_image_id`, `parent` (FK self), `company_size`, `start_date`, `changed_company` (FK self, for mergers/renames), `change_date`.

### ConceptCompany
M2M through table. Links Concept to Company with role flags: `is_developer`, `is_publisher`, `is_porting`, `is_supporting`. Unique on (concept, company).

### IGDBMatch
OneToOne to Concept. Stores: matching metadata (`match_confidence`, `match_method`, `status`), parsed Tier 1 data (`game_category`, `igdb_summary`, `igdb_storyline`, `time_to_beat_*`, `igdb_first_release_date`, `game_engine_name`, `franchise_names`, `similar_game_igdb_ids`, `external_urls`), and the full raw IGDB response (`raw_response`) for future Tier 2 parsing.

`status` values:
- `auto_accepted`: Matched at >= 85% confidence and enrichment applied automatically.
- `pending_review`: Matched at 50-84% confidence, awaiting staff approval.
- `accepted`: Match approved manually after pending review.
- `rejected`: Match rejected manually (rare; usually rematched instead).
- `no_match`: Matching ran but no IGDB result was found. The row exists as a marker so subsequent default enrichment passes skip the concept and so the unmatched review queue can surface it for manual intervention. `igdb_id`, `igdb_name`, `match_confidence`, and `match_method` are all blank/null on these rows.

### Concept Additions
- `igdb_genres` (JSONField): Genre names from IGDB (e.g. ["RPG", "Adventure"])
- `igdb_themes` (JSONField): Theme names from IGDB (e.g. ["Open world", "Fantasy"])

## Key Flows

### Matching a Concept to IGDB

1. `IGDBService.match_concept(concept)` is called
2. Try external ID match: query IGDB `external_games` for PSN Store IDs from concept's `concept_id` and `title_ids`
3. If no external match: search IGDB by `concept.unified_title` filtered to PlayStation platforms (PS3=9, Vita=46, PS4=48, PS5=167)
4. Score each result with `_calculate_confidence()` using title similarity, platform, release year, publisher name
5. Return best match above the review threshold, or None

### Enrichment Pipeline

1. Match found with confidence >= 0.85: auto-accepted, enrichment applied immediately
2. Match found with confidence 0.50-0.84: IGDBMatch created with `pending_review` status, enrichment deferred
3. No match found: IGDBMatch created with `no_match` status (via `IGDBService.record_no_match`). Default enrichment runs skip these on subsequent passes; use `--retry-no-match` to re-attempt them or `--unmatched` to assign manually. `record_no_match` refuses to overwrite an existing accepted/pending/rejected row.
4. Staff approves pending match via admin action: enrichment applied
5. Enrichment creates Company records, ConceptCompany entries, and updates Concept's `igdb_genres`/`igdb_themes`

### Sync Pipeline Hook

In `token_keeper.py`, after `PsnApiService.create_concept_from_details()` creates a NEW concept:
- `IGDBService.enrich_concept(concept)` is called (best-effort, wrapped in try/except)
- Only fires for newly created concepts (not existing ones)
- Only fires for non-stub concepts (skips PP_ prefixed)

## Integration Points

- **Shovelware Detection** (`trophies/services/shovelware_detection_service.py`): Company `company_size` and `game_engine_name` can be used as additional shovelware signals
- **Stats Service** (`trophies/services/stats_service.py`): Developer aggregation (top developers, unique developer count) alongside existing publisher stats
- **SEO Tags** (`core/templatetags/seo_tags.py`): Developer as `author` Organization, `timeRequired` duration, IGDB genres with PSN fallback
- **Game Detail Template**: Developer display, estimated completion time, genres/themes
- **Badge System**: Developer badge type already exists; Stages can group Concepts by developer via ConceptCompany queries

## Gotchas and Pitfalls

- **Concept.absorb() must handle ConceptCompany and IGDBMatch**: When concepts merge, ConceptCompany entries are moved (deduped by concept+company), and IGDBMatch is transferred if the target lacks one. This is already implemented.
- **IGDB rate limit is 4 req/sec**: The service enforces this with `time.sleep()`. During backfill, this means ~15 concepts/minute. Do not bypass the rate limiter.
- **IGDB tokens expire**: Access tokens last ~60 days. Cached in Redis. If the token expires mid-operation, the next request will auto-refresh.
- **External ID matching is not guaranteed**: IGDB may not have PlayStation Store IDs for all games. Name-based matching is the primary fallback.
- **Fuzzy matching can be wrong for short/common titles**: Games like "Golf" or "Tennis" may match incorrectly. The confidence scoring and pending_review status mitigate this, but staff review is important for the initial backfill.
- **Raw response storage**: The `raw_response` JSONField stores the full IGDB API response. This can be large (5-20KB per game). Tier 2 data (keywords, game_modes, multiplayer_modes, ratings, etc.) can be parsed from it later without re-querying.
- **no_match never overwrites real matches**: `IGDBService.record_no_match()` checks for an existing IGDBMatch first and bails if its status is anything other than `no_match`. This means if `--all` is run and a previously-accepted concept temporarily fails to match (transient IGDB hiccup), the accepted row is preserved. The summary still counts it as `no_match` since the matcher returned nothing, but the DB is left untouched.
- **Company mergers**: IGDB tracks company renames/mergers via `changed_company_id`. The `Company.current_company` property follows the chain. When displaying company names, prefer `current_company` for accuracy.

## Management Commands

| Command | Purpose | Typical Usage |
|---------|---------|---------------|
| `enrich_from_igdb` (default) | Enrich concepts without any IGDBMatch row (skips `no_match` markers) | `python manage.py enrich_from_igdb` |
| `enrich_from_igdb --concept-id X` | Enrich a single concept | `python manage.py enrich_from_igdb --concept-id 12345 --dry-run` |
| `enrich_from_igdb --force` | Re-match all concepts (overwrites existing) | `python manage.py enrich_from_igdb --all --force` |
| `enrich_from_igdb --pending` | Re-process pending_review matches | `python manage.py enrich_from_igdb --pending` |
| `enrich_from_igdb --retry-no-match` | Re-run matching against concepts previously recorded as `no_match` | `python manage.py enrich_from_igdb --retry-no-match` |
| `enrich_from_igdb --unmatched` | Interactive queue of `no_match` concepts for manual assignment | `python manage.py enrich_from_igdb --unmatched` |

## Cache Keys

| Key Pattern | TTL | Purpose |
|-------------|-----|---------|
| `igdb_access_token` | ~60 days (from Twitch) | IGDB API bearer token |

## Related Docs

- [Data Model](data-model.md): Concept, Game, and related models that IGDB enriches
- [Sync Pipeline](../guides/sync-pipeline.md): Where IGDB enrichment hooks into the PSN sync flow
