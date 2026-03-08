# Game Family System

The Game Family system groups related Concepts across generations, platforms, and regions without merging them. Unlike `Concept.absorb()` which fully merges two Concepts into one, Game Family is a lightweight grouping layer: each Concept keeps its own comments, ratings, and checklists while being linked to siblings through a shared `GameFamily` record. This powers cross-gen unification (e.g., linking the PS4 and PS5 versions of a game) and feeds into badge stage relationships and future features like "see all versions" on game pages.

## Architecture Overview

The matching algorithm uses a two-pass approach. Pass 1 is name-based: it normalizes game titles by stripping common suffixes (remastered, definitive edition, director's cut, etc.) and platform tags ((PS4), (PS5)), then groups Concepts whose normalized titles converge. Pass 2 was originally planned for trophy-based matching of cross-language titles (e.g., matching the Japanese and English versions by trophy icon overlap), but the current implementation handles this within Pass 1's confidence scoring by augmenting name matches with trophy data signals.

Confidence scoring determines what happens to a match. Each candidate pair is evaluated against three signals: name match type (exact vs. fuzzy), trophy name overlap (0.0 to 1.0, calculated as intersection over the smaller set), and structural fingerprint match (trophy count by type plus group count). These signals combine into a confidence score:

- **High confidence (>= 0.85)**: Auto-creates a GameFamily and assigns all Concepts in the group. No human review needed.
- **Medium confidence (0.50 to 0.84)**: Creates a GameFamilyProposal for staff review. Appears in the admin queue at `/staff/game-families/`.
- **Low confidence (< 0.50)**: Skipped entirely. No record created.

The system is designed to run as a cron job (daily), not as part of the sync pipeline. This keeps the matching logic in a single location and avoids adding latency to profile syncs. The `match_game_families` management command is the sole entry point.

Performance matters because the algorithm compares thousands of Concepts. Bulk precomputation (`_precompute_data()`) runs three queries upfront to build in-memory lookup dictionaries for game counts, trophy fingerprints, and existing proposals. Trophy names are loaded lazily and cached per-concept to avoid holding every trophy name string in memory simultaneously.

## File Map

| File | Purpose |
|------|---------|
| `core/services/game_family_service.py` | Matching algorithm: `find_matches()`, `diagnose_concept()`, normalization, confidence scoring |
| `trophies/models.py` (GameFamily) | Lightweight grouping model with canonical name and verified flag |
| `trophies/models.py` (GameFamilyProposal) | Admin review queue for medium-confidence matches |
| `trophies/models.py` (Concept.family) | FK to GameFamily (nullable, SET_NULL) |
| `trophies/views/admin_views.py` | Admin page views for game family management |
| `api/game_family_views.py` | Staff-only API: CRUD, add/remove concepts, approve/reject proposals, concept search |
| `templates/trophies/game_family_management.html` | Admin dashboard template |

## Data Model

### GameFamily

| Field | Type | Notes |
|-------|------|-------|
| `canonical_name` | CharField (indexed) | Best representative name. Cleaned via `clean_title_field()` on save |
| `admin_notes` | TextField (blank) | Staff notes |
| `is_verified` | BooleanField | False for auto-created families, True after staff review |
| `created_at` | DateTimeField | Auto |
| `updated_at` | DateTimeField | Auto |

Ordering: alphabetical by `canonical_name`.

### GameFamilyProposal

| Field | Type | Notes |
|-------|------|-------|
| `concepts` | M2M to Concept | The Concepts proposed for grouping |
| `proposed_name` | CharField | Suggested canonical name |
| `confidence` | FloatField | Algorithm confidence score (0.0 to 1.0) |
| `match_reason` | TextField | Human-readable explanation (e.g., "Exact title match + 85% trophy name overlap") |
| `match_signals` | JSONField | Raw signal data: `{name_match, trophy_name_overlap, fingerprint_match}` |
| `status` | CharField | pending, approved, rejected |
| `reviewed_by` | FK to CustomUser (nullable) | Staff reviewer |
| `reviewed_at` | DateTimeField (nullable) | When reviewed |
| `resulting_family` | FK to GameFamily (nullable) | The family created on approval |

Ordering: descending by confidence, then by creation date.

### Concept (relevant fields)

| Field | Type | Notes |
|-------|------|-------|
| `family` | FK to GameFamily (nullable, SET_NULL) | At most one family per concept |

## Key Flows

### Running the Matching Algorithm

1. `match_game_families` management command calls `find_matches()`
2. All Concepts loaded with games prefetched (heavy fields deferred)
3. `_precompute_data()` runs three bulk queries: game counts per concept, trophy fingerprints, existing proposal concept sets
4. Pass 1 groups concepts by normalized title. For each group of 2+ unmatched concepts:
   - Determine name match type: if all raw titles (stripped of platform suffixes) are identical, it's "exact"; otherwise "fuzzy"
   - Calculate pairwise confidence using the best pair's score
   - Pick canonical name: prefer real concepts over PP_ stubs, then the concept with the most games
5. High-confidence groups get a GameFamily created with all concepts assigned
6. Medium-confidence groups get a GameFamilyProposal created (unless one already exists for that concept set, including rejected proposals to avoid re-proposing)
7. Stats returned: `{auto_created, proposals_created, skipped, total_concepts}`

### Staff Reviewing a Proposal

1. Staff navigates to `/staff/game-families/` and views the proposals tab
2. For each proposal: sees the proposed name, confidence, match reason, and the list of concepts
3. Approve: creates a GameFamily, assigns all concepts, sets proposal status to approved
4. Reject: sets proposal status to rejected. The concept set is tracked so future runs skip it

### Diagnosing a Single Concept

1. `diagnose_concept(concept_id)` compares one concept against all others
2. For each other concept: determines name match type, calculates confidence
3. Returns top N matches sorted by confidence with full signal breakdown
4. Read-only: does not create or modify any data. Useful for debugging why two concepts are or are not matched

### Manual Family Management

1. Staff can create families manually via the admin dashboard
2. Staff can add or remove individual concepts from a family
3. Staff can edit the canonical name or delete a family entirely

## API Endpoints

| Method | Path | Auth | Purpose |
|--------|------|------|---------|
| POST | `/api/v1/game-families/` | Staff | Create a new GameFamily |
| PUT | `/api/v1/game-families/<family_id>/` | Staff | Update canonical name or notes |
| DELETE | `/api/v1/game-families/<family_id>/delete/` | Staff | Delete a family (clears concept.family FK) |
| POST | `/api/v1/game-families/<family_id>/add-concept/` | Staff | Add a concept to a family |
| POST | `/api/v1/game-families/<family_id>/remove-concept/` | Staff | Remove a concept from a family |
| POST | `/api/v1/game-families/proposals/<proposal_id>/approve/` | Staff | Approve a proposal (creates family) |
| POST | `/api/v1/game-families/proposals/<proposal_id>/reject/` | Staff | Reject a proposal |
| GET | `/api/v1/game-families/search-concepts/` | Staff | Search concepts for manual assignment |

## Management Commands

| Command | Purpose | Typical Usage |
|---------|---------|---------------|
| `match_game_families` | Run the two-pass matching algorithm | `python manage.py match_game_families --verbose` |
| `match_game_families --dry-run` | Preview matches without creating anything | `python manage.py match_game_families --dry-run --verbose` |
| `match_game_families --auto-only` | Only process high-confidence matches, skip proposals | `python manage.py match_game_families --auto-only` |

## Integration Points

- **Concept.absorb()**: When concepts merge, the target inherits the absorbed concept's family if the target has none. Updated in the absorb method.
- **Concept.family FK**: Used by the absorb flow and the admin family management. SET_NULL on family deletion.
- **GameFamilyProposal M2M**: Listed in `Concept.absorb()` so proposals referencing the absorbed concept are migrated.
- **Badge stages (Stage.concepts M2M)**: Badges reference Concepts directly, not families. Families provide the grouping metadata, but badge evaluation still uses the Concept-level relationship.
- **Cron schedule**: Intended to run daily. Not part of the sync pipeline (avoids adding latency to profile syncs).

## Gotchas and Pitfalls

- **Rejected proposals are tracked**: The algorithm checks both pending and rejected proposals to avoid re-proposing matches that staff already declined. If you clear proposals from the database, the next run may recreate them.
- **Platform suffix stripping for exact/fuzzy classification**: "Game (PS4)" and "Game (PS5)" are classified as exact matches because platform suffixes are stripped before comparison. Without this step, they would be classified as fuzzy and receive lower confidence.
- **PP_ stub concepts**: Concepts with IDs starting with `PP_` are default stubs created when the API lookup fails. The canonical name logic prefers real concepts over stubs. Stubs may still be grouped into families if their titles match.
- **Trophy name cache is lazy**: Trophy names are not precomputed upfront (to avoid memory pressure). They are loaded per-concept on first access and cached in a dictionary for the duration of the run.
- **Normalized title collisions**: Normalization is aggressive. Stripping "Remastered", "Definitive Edition", etc. can cause false groupings if two genuinely different games share a base title. This is why trophy data signals gate the confidence level.
- **Memory usage at scale**: The algorithm holds all concepts and their precomputed data in memory. For very large databases, the trophy fingerprint dictionary and lazy trophy name cache are the primary memory consumers.

## Related Docs

- [Concept Model](../architecture/concept-model.md): Core Concept model, the `absorb()` migration pattern, and concept lock
