# Game Family System

The Game Family system groups related Concepts across platforms, regions, and
other PSN-side splits without merging them. Unlike `Concept.absorb()` which
fully merges two Concepts into one, Game Family is a lightweight grouping
layer: each Concept keeps its own comments, ratings, reviews, and checklists
while being linked to siblings through a shared `GameFamily` record.

Used for cross-platform unification (PS4 and PS5 versions of a game), regional
sibling grouping (US / EU / JP of the same release), and foundational data for
future features like "see all versions" on game pages.

## Architecture Overview

**IGDB id is the single source of truth for family membership.** One family
per IGDB game, period. Families are created and populated automatically by
the IGDB enrichment pipeline as matches land — there's no separate matching
algorithm, no name normalization, no fuzzy heuristics, no proposal queue.

The rule is trivial:

```
For every concept with an accepted IGDBMatch:
  canonical_id = _resolve_canonical_igdb_id(match.raw_response, match.igdb_id)
  family, _ = GameFamily.objects.get_or_create(igdb_id=canonical_id)
  concept.family = family
```

The `_resolve_canonical_igdb_id` step collapses versions/releases of the
same underlying game. IGDB models "same game, different release" via
`parent_game` (set on Ports, Remakes, Remasters — game_type 11/8/9) and
`version_parent` (set on editions like Deluxe/GOTY/Anniversary). When
either is present, the family keys on the parent id instead of the
derivative's own id. Result: Jak and Daxter: The Precursor Legacy
(IGDB #1528), its PS3 HD remaster (#302690), and its PS4 port (#325261)
all collapse into one family keyed on 1528, while each release still has
its own `IGDBMatch` with its own platform, release date, and companies.

Two concepts with the same canonical IGDB id unambiguously belong
together. Two concepts with different canonical ids are different games.
Done.

This behavior is implemented in `IGDBService._link_concept_to_family` which
fires from `_apply_enrichment` on every match acceptance (auto_accepted
during `process_match`, manually approved via `approve_match`, or refreshed
via `refresh_match`). The live sync and enrichment paths populate new
families automatically; no background job required.

## What changed in Phase 2.6 (2026-04)

Previously the system ran a daily cron (`match_game_families`) over a
name-based + trophy-based heuristic matcher, with a confidence scoring
model that auto-created high-confidence families and queued medium-confidence
candidates as `GameFamilyProposal` rows for staff review. That matcher
shipped ~555 lines of normalization, regex, and scoring logic in
`core/services/game_family_service.py`.

All of it was removed in Phase 2.6. IGDB id equality is strictly stronger
signal than any name/trophy fuzziness we could reconstruct ourselves, and
with Phase 2.5's CJK unlock (`/game_localizations` search + scoring)
virtually every concept has access to the IGDB id signal. The heuristic
path was adding a layer of machinery for a job IGDB already does
better.

**Removed components:**
- `core/services/game_family_service.py` (matching algorithm)
- `core/management/commands/match_game_families.py` (daily cron)
- `GameFamilyProposal` model + admin registration + API endpoints
- `ProposalApproveView` / `ProposalRejectView` DRF views
- Superuser navbar "pending proposals" count badge
- `GameFamilyProposal` branch in `Concept.absorb()`

**Added components:**
- `GameFamily.igdb_id` field (unique, nullable, db_indexed)
- `IGDBService._link_concept_to_family` — the new deterministic linking
- Orphan family cleanup in `_link_concept_to_family` (when a concept
  migrates to a new family and leaves its old one empty)
- `backfill_game_families_from_igdb` management command for the one-shot
  historical pass against existing accepted matches
- `Concept.family` exposure on the Django admin changelist (searchable,
  sortable, raw-id-edited)
- `GameFamily.igdb_id` exposure on `GameFamilyAdmin` list_display

## File Map

| File | Purpose |
|------|---------|
| `trophies/models.py` (GameFamily) | Grouping model. Keyed on `igdb_id` (unique) |
| `trophies/models.py` (Concept.family) | FK to GameFamily (nullable, SET_NULL) |
| `trophies/services/igdb_service.py` (`_link_concept_to_family`) | The deterministic linking logic; runs on every match acceptance |
| `trophies/management/commands/backfill_game_families_from_igdb.py` | One-shot historical backfill against existing accepted matches |
| `trophies/views/admin_views.py` (GameFamilyManagementView) | Staff admin page at `/staff/game-families/` — inspection + manual overrides |
| `api/game_family_views.py` | Staff-only CRUD API for manual creates/edits on edge cases IGDB doesn't cover |
| `templates/trophies/game_family_management.html` | Admin dashboard template |

## Data Model

### GameFamily

| Field | Type | Notes |
|-------|------|-------|
| `canonical_name` | CharField (indexed) | IGDB canonical name. Cleaned via `clean_title_field()` on save |
| `igdb_id` | IntegerField (unique, nullable, indexed) | IGDB game id this family maps to. Unique when set. Nullable for the rare admin-created family that doesn't correspond to an IGDB entry |
| `admin_notes` | TextField (blank) | Staff notes. Presence locks `canonical_name` from auto-update during re-enrichment |
| `is_verified` | BooleanField | Always True for IGDB-created families. Reserved for admin-created families where verification status matters |
| `created_at` | DateTimeField | Auto |
| `updated_at` | DateTimeField | Auto |

Ordering: alphabetical by `canonical_name`.

## Key Flows

### Live path (IGDBService._apply_enrichment)

Runs on every match acceptance — `process_match` auto-accepts, `refresh_match`
refreshes metadata, `approve_match` surfaces from the pending-review queue.
All three call `_apply_enrichment`, which calls `_link_concept_to_family`:

1. Pull `igdb_id` from the IGDBMatch. If null (status=no_match), skip.
2. `GameFamily.objects.get_or_create(igdb_id=igdb_id, defaults={...})`.
3. If the canonical_name has drifted from the IGDB name AND no admin_notes
   are set on the family, update canonical_name.
4. If `concept.family_id != family.pk`, snapshot the old family and set
   `concept.family = family`.
5. If the old family is now empty (the migrating concept was its last
   member), delete the old family. This covers the rare case where IGDB
   merges duplicate entries across two IDs and a refresh moves a concept
   to the consolidated id.

### Backfill command

`backfill_game_families_from_igdb` is the one-shot historical pass. Intended
run order: deploy Phase 2.6 → migrate → run the rematch pass so every
matchable concept is in `accepted`/`auto_accepted` state → then run this
command. That ordering maximizes family coverage.

Mechanics: walks every `IGDBMatch` where status is accepted/auto_accepted
and igdb_id is not null, groups by igdb_id, and runs the same linking
logic as the live path for each group. Idempotent — safe to re-run. Offers
`--dry-run` for projection without writes.

### Manual overrides (admin API)

`api/game_family_views.py` provides CRUD for admin-created families and
concept add/remove operations. Intended for the edge cases IGDB doesn't
cover (Concepts with `no_match` that the admin wants to group anyway,
or overrides to IGDB's classification). Creation requires no igdb_id
since the admin is explicitly going outside IGDB's model.

## Integration Points

- **Concept.absorb()**: migrates family FK from `other` to `self` if `self`
  has no family but `other` does. The GameFamilyProposal M2M branch that
  used to live here was removed in Phase 2.6.
- **Badge Stage system**: Stages can reference GameFamily for cross-gen
  badge grouping (unchanged by Phase 2.6 — the API is the same; only the
  data population changed).
- **Sync pipeline**: `_apply_enrichment` is the single point of population.
  `refresh_match` handles IGDB-side id merges. `approve_match` fires the
  same hook when admin resolves a pending match.

## Gotchas and Pitfalls

- **IGDB id is the contract**: don't write code that creates `GameFamily`
  rows from PlatPursuit-side signals (name matching, trophy overlap,
  concept_id patterns). The invariant "one family per IGDB id" can't be
  preserved if multiple paths write families independently. Manual
  admin-created families are the only exception, and they carry a null
  `igdb_id`.

- **Canonical name updates vs. admin notes**: `_link_concept_to_family`
  keeps `canonical_name` in sync with IGDB's latest `igdb_name`, but only
  when `admin_notes` is empty. Setting admin_notes is the signal that
  the family's name has been deliberately customized — we won't
  overwrite it on re-enrichment.

- **Orphan cleanup is scoped**: only the OLD family a concept is
  migrating away from is candidate for cleanup, and only if it's empty
  after the move. We never touch the target family or unrelated families
  during link operations.

- **Concepts without IGDB matches are unfamilied**: no more heuristic
  groupings exist. A concept with `status=no_match` or no IGDBMatch row
  at all won't be in any family until it successfully matches. This is
  intentional — grouping-by-guess was the behavior Phase 2.6 eliminated.

- **ID migrations happen in refresh_match**: if IGDB consolidates
  two entries into one, concepts previously matched to the deprecated
  id get moved to the surviving id's family on their next refresh. The
  old family, if empty, gets deleted.

## Management Commands

| Command | Purpose | Typical Usage |
|---------|---------|---------------|
| `backfill_game_families_from_igdb` | One-shot populate families from existing accepted IGDB matches | `python manage.py backfill_game_families_from_igdb --dry-run` then without `--dry-run` |

## Related Docs

- [IGDB Integration](../architecture/igdb-integration.md): the matching pipeline that populates IGDBMatch rows this system keys off
- [Concept Model](../architecture/concept-model.md): concept lifecycle including `absorb()` which interacts with family FK
- [Data Model](../architecture/data-model.md): full model relationships
