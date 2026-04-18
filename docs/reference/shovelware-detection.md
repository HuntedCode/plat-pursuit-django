# Shovelware Detection

Shovelware games (cheap, mass-produced platinum trophy farms) are automatically detected and flagged to protect the integrity of challenges, leaderboards, and community features. Detection uses platinum earn-rate thresholds with concept-level grouping, plus an IGDB-developer blacklist for catalog-wide cascade flagging.

## Detection Algorithm

Location: [trophies/services/shovelware_detection_service.py](../../trophies/services/shovelware_detection_service.py)

### Thresholds

| Constant | Value | Purpose |
|----------|-------|---------|
| `FLAG_THRESHOLD` | 80.0% | Any platinum at >= 80% earn rate flags the whole concept (rule 1) |
| `UNFLAG_THRESHOLD` | 30.0% | A platinum at strict `< 30%` enables the shield |
| `EVIDENCE_THRESHOLD` | 70.0% | A developer stays blacklisted while any primary-developed concept has a non-locked game at >= 70% |

The 10% gap between `FLAG_THRESHOLD` (80%) and `EVIDENCE_THRESHOLD` (70%) is deliberate hysteresis: a developer enters the blacklist at 80%+ and only leaves it when all their primary-developed concepts drop below 70%. This prevents oscillation when earn rates jitter around the flag threshold.

### Primary Developer

The "primary developer" of a concept is the first `ConceptCompany` row with `is_developer=True`, ordered by `id` (ascending). This matches IGDB's `involved_companies` array order, where the lead studio is listed first.

The primary developer is **only considered** when the concept has an `IGDBMatch` with `is_trusted=True` (status is `accepted` or `auto_accepted`). Concepts without a trusted IGDB match cannot propagate flags through the developer blacklist, but they can still be flagged by direct earn-rate evidence.

### Rules

**Rule 1 (earn-rate, always wins):** If any non-admin-locked game in the concept has platinum earn rate `>= 80.0%`, flag every non-locked game in the concept. If the concept has a trusted IGDB match, set its primary developer's `DeveloperBlacklist` entry to active and cascade-flag every other concept whose primary developer is that same company (respecting the shield).

**Rule 2 (developer blacklist):** If the concept's primary developer is on an active `DeveloperBlacklist` entry (`is_blacklisted=True`), flag the concept **unless shielded**. Before flagging, the service re-checks whether the developer still has qualifying evidence (see Hysteresis below); if not, the entry is flipped to inactive and the concept falls through to the default unflag path.

**Shield (rule 2 only, never rule 1):** A concept is shielded when at least one non-locked game in it has platinum earn rate `< 30.0%` AND no non-locked game is `>= 80.0%`. The shield represents positive evidence of legitimacy (a challenging platinum) that overrides a reputation-based flag. Direct evidence (an 80%+ game) always wins.

**Admin-locked games are invisible to auto-detection.** Any game with `shovelware_lock=True` (set via `lock_shovelware --flag` or `--clear`) is filtered out of every rate-based calculation: its earn rate does not contribute to rule 1 on siblings, does not contribute to the shield, and does not contribute to developer blacklist evidence. Admin has the final say per-game.

### Entry Points

- **Sync-time**: `evaluate_game(game)` is called when platinum trophy data is created or updated. It delegates to `evaluate_concept(concept)` when the game has a concept, or falls back to a simple per-game earn-rate check for standalone games.
- **IGDB trust transitions**: `on_igdb_match_trusted(concept)` is called from `IGDBService.process_match()` (when a new match is saved with `auto_accepted` status) and from `IGDBService.approve_match()` (when staff promotes `pending_review` to `accepted`). Closes the ordering gap where rule 1 could flag a concept by earn rate before a primary developer was known; the re-eval adds the developer to `DeveloperBlacklist` and fires the cascade.
- **Reconciliation**: `evaluate_concept(concept)` is the canonical concept-level entry point, used by the `update_shovelware` and `backfill_shovelware` management commands.

### Idempotence

`evaluate_concept` is idempotent: re-running it on a concept that's already in the correct state performs no DB writes. Internally, `_flag_concept` excludes already-`auto_flagged` games from its bulk UPDATE, and `_unflag_concept` excludes already-`clean` games. This preserves `shovelware_updated_at` across reconciliation passes.

### Flow Summary

```
evaluate_concept(concept):
    rates = plat earn rates for non-admin-locked games in concept
    primary_dev = first ConceptCompany(is_developer=True) if concept has trusted IGDBMatch else None

    if any(r >= 80%):
        flag concept
        if primary_dev: activate DeveloperBlacklist entry (cascades on new activation)
        return

    if primary_dev and DeveloperBlacklist.is_blacklisted(primary_dev):
        if dev_has_qualifying_evidence(primary_dev):    # any primary-developed concept with a game >= 70%
            if any(r < 30%):   unflag concept  # shielded
            else:              flag concept
            return
        else:
            release dev from blacklist  # hysteresis un-blacklist
            # fall through to default unflag

    unflag concept  # default state
```

### Hysteresis

A developer enters the blacklist on rule 1 (>=80% earn rate on one of their primary-developed concepts). They leave the blacklist when `_dev_has_qualifying_evidence` returns False: no concept primary-developed by them has any non-locked game at or above `EVIDENCE_THRESHOLD` (70%). The 10% deadband prevents a concept whose rate jitters around 80% from flip-flopping the developer's status.

The evidence check is **derived** from live query, not cached. This means:

- Concept mergers, deletions, and IGDB data changes never leave stale blacklist state
- Admin-locked games (in either direction) are correctly excluded from evidence
- `DeveloperBlacklist.qualifying_concepts_for(company)` is the single source of truth for both evidence checks and the admin "flagged concepts" count display

### Cascade on New Blacklist Entry

When `_register_developer_flag` promotes a developer to `is_blacklisted=True` for the first time, `_flag_developer_concepts(company)` iterates every other concept whose primary developer is that company and flags the non-shielded ones. This is how a brand-new 80%+ discovery propagates to a developer's back-catalog without waiting for the daily rebuild.

## Game Model Fields

```python
shovelware_status: CharField(choices=[
    'clean',              # Not shovelware
    'auto_flagged',       # Auto-detected
    'manually_flagged',   # Admin locked as shovelware
    'manually_cleared',   # Admin locked as clean
])
shovelware_lock: BooleanField    # Prevents auto-detection changes
shovelware_updated_at: DateTimeField
```

The property `game.is_shovelware` returns `True` for both `auto_flagged` and `manually_flagged`.

Auto-detection never overrides `manually_flagged` or `manually_cleared`. Games with `shovelware_lock=True` are never modified by auto-detection regardless of status.

## DeveloperBlacklist Model

| Field | Type | Notes |
|-------|------|-------|
| `company` | `OneToOneField(Company)` | IGDB company; one entry per developer |
| `is_blacklisted` | `BooleanField` | True while the developer has qualifying evidence; cascade-flags use this |
| `date_added` | `DateTimeField` | auto_now_add |
| `notes` | `TextField` | Admin scratch pad |

The flagged-concepts list is **not stored** on the model. It is derived from live data via `DeveloperBlacklist.qualifying_concepts_for(company)`, which returns a `QuerySet` of concepts primary-developed by the company that have at least one non-admin-locked game with platinum earn rate >= 70%. The admin's "Flagged Concepts" column reads `flagged_concept_count` (one count query per row).

Deriving evidence live means concept mergers, deletions, and IGDB company rewrites self-heal without any migration logic. It also means `Concept.absorb()` requires no special handling for `DeveloperBlacklist`.

## Impact

When a game is marked shovelware:
- **Challenges**: Excluded from A-Z, Calendar, and Genre challenge game search
- **Review Hub**: Concepts where ALL games are shovelware are excluded (404)
- **Leaderboards**: Shovelware platinums may be filtered from certain views
- **Badges**: Shovelware games in badge stages still count (badges evaluate at concept level)

## Concurrency

Concept-sibling writes use a Redis lock to prevent deadlocks between concurrent sync workers that hit the same concept:
- Lock key: `shovelware_concept_lock:{concept_id}`
- Retry: 3 attempts with 0.1s linear backoff
- Fallback: if the lock cannot be acquired, the update proceeds unguarded (preferred over silently skipping)

## Management Commands

| Command | Purpose | Usage |
|---------|---------|-------|
| `backfill_shovelware` | One-shot wipe + rebuild from scratch | `python manage.py backfill_shovelware [--dry-run] [--verbose]` |
| `update_shovelware` | Surgical reconciliation (idempotent) | `python manage.py update_shovelware [--verbose]` |
| `lock_shovelware` | Manually flag / clear / unlock a concept | `python manage.py lock_shovelware <np_id> --flag\|--clear\|--unlock` |

### Day-to-day flagging

New-game discovery and drift into the >=80% zone are handled **in real time** by the sync pipeline. TokenKeeper calls [`psn_api_service.py`](../../trophies/services/psn_api_service.py) on every platinum sync, which calls `ShovelwareDetectionService.evaluate_game(game)`. Missed attribution (rule 1 flagged but no primary developer known yet) is repaired by the IGDB hooks on match creation and approval. No management command needs to run for routine operation.

### `backfill_shovelware`

Use once after schema migrations or when state diverges significantly from what the current algorithm would compute (e.g. after the IGDB-developer migration). It:

1. Resets every `auto_flagged` game to `clean` (respects `shovelware_lock` and manual statuses).
2. Deletes every `DeveloperBlacklist` entry.
3. **Pass 1**: evaluates every concept with a platinum at `>= 80%` earn rate, seeding `DeveloperBlacklist` (cascade fires).
4. **Pass 2**: evaluates remaining concepts whose primary developer ended up on the new blacklist.

### `update_shovelware`

The recommended cron target (daily cadence is fine thanks to idempotence). Walks a targeted candidate set rather than wiping state:

- Concepts with any currently `auto_flagged` game (catches spurious flags + new shield opportunities)
- Concepts containing a platinum at `>= 80%` earn rate (catches missed rule-1 flags)
- Concepts whose developers are on an active `DeveloperBlacklist` (catches missed rule-2 cascades)

Each candidate is re-evaluated via `evaluate_concept`. Because evaluation is idempotent, concepts already in the correct state produce zero DB writes, preserving `shovelware_updated_at`. Safe to run on demand at any time.

**Final sweep:** after the per-concept pass, the command iterates every `DeveloperBlacklist` entry with `is_blacklisted=True` and re-checks evidence. Any developer whose primary-developed concepts no longer provide qualifying evidence (e.g. because they lost "primary developer" status on every concept after admin edits or IGDB data changes, so the per-concept loop never touched their entry) is released. Without this sweep, such stranded entries would persist indefinitely.

## Gotchas and Pitfalls

- **Shield is asymmetric.** A sub-30% game can override a developer-reputation flag, but **never** overrides a direct 80%+ game. If the concept has both, rule 1 wins and the concept is flagged.
- **No IGDB match = no cascade.** Concepts without a trusted IGDB match can still be flagged individually by the 80% rule, but we have no way to attribute the flag to a developer. Their back-catalog will only be flagged if their own platinum earn rates cross 80%.
- **Primary developer = first by id.** When IGDB lists multiple co-developers, only the first-ordered `ConceptCompany` row (with `is_developer=True`) participates in the blacklist. This matches IGDB's intended ordering, which conventionally puts the lead studio first. Co-dev attribution is intentionally simplified.
- **Hysteresis is a deadband, not a hard threshold.** A new game at 82% flags; drifting to 79% does NOT unflag (still above 70%). Only once it falls below 70% is the developer released. This also means a concept flagged purely via rule 2 whose dev is then released automatically unflags on the next evaluation.
- **Locked games are untouchable AND invisible.** `shovelware_lock=True` + any status is immutable by auto-detection, AND the locked game's earn rate is filtered out of every rate-based calculation. An admin-cleared game's 95% rate will not flag its siblings.
- **Earn-rate source.** Platinum earn rate comes from PSN API data synced during trophy sync. It reflects the global PSN population, not PlatPursuit users. Early-lifecycle games with few players can have unstable earn rates, but reconciliation + hysteresis ensure any false-positive damage is transient.

## Related Docs

- [Token Keeper](../architecture/token-keeper.md): Sync-time shovelware evaluation
- [Challenge Systems](../features/challenge-systems.md): Shovelware exclusion in game search
- [Review Hub](../features/review-hub.md): Shovelware concept exclusion
- [Cron Jobs](../guides/cron-jobs.md): Daily `update_shovelware` schedule
- [IGDB Integration](../architecture/igdb-integration.md): How developers are linked via `ConceptCompany`
