# Shovelware Detection

Shovelware games (cheap, mass-produced platinum trophy farms) are automatically detected and flagged to protect the integrity of challenges, leaderboards, and community features. Detection uses platinum earn-rate thresholds with concept-level grouping, plus an IGDB-developer blacklist for catalog-wide cascade flagging.

## Detection Algorithm

Location: [trophies/services/shovelware_detection_service.py](../../trophies/services/shovelware_detection_service.py)

### Thresholds

| Constant | Value | Purpose |
|----------|-------|---------|
| `FLAG_THRESHOLD` | 80.0% | Any platinum at >= 80% earn rate flags the whole concept |
| `UNFLAG_THRESHOLD` | 30.0% | A platinum at strict `< 30%` enables the shield |

### Primary Developer

The "primary developer" of a concept is the first `ConceptCompany` row with `is_developer=True`, ordered by `id` (ascending). This matches IGDB's `involved_companies` array order, where the lead studio is listed first.

The primary developer is **only considered** when the concept has an `IGDBMatch` with `is_trusted=True` (status is `accepted` or `auto_accepted`). Concepts without a trusted IGDB match cannot propagate flags through the developer blacklist, but they can still be flagged by direct earn-rate evidence.

### Rules

**Rule 1 (earn-rate, always wins):** If any game in the concept has platinum earn rate `>= 80.0%`, flag every non-locked game in the concept. If the concept has a trusted IGDB match, add its primary developer to `DeveloperBlacklist` and cascade-flag every other concept whose primary developer is that same company (respecting the shield).

**Rule 2 (developer blacklist):** If the concept's primary developer is on an active `DeveloperBlacklist` entry (`is_blacklisted=True`), flag the concept **unless shielded**.

**Shield (rule 2 only, never rule 1):** A concept is shielded when at least one game in it has platinum earn rate `< 30.0%` AND no game is `>= 80.0%`. The shield represents positive evidence of legitimacy (a challenging platinum) that overrides a reputation-based flag. Direct evidence (an 80%+ game) always wins.

### Entry Points

- **Sync-time**: `evaluate_game(game)` is called when platinum trophy data is created or updated. It delegates to `evaluate_concept(concept)` when the game has a concept, or falls back to a simple per-game earn-rate check for standalone games.
- **IGDB trust transitions**: `on_igdb_match_trusted(concept)` is called from `IGDBService.process_match()` (when a new match is saved with `auto_accepted` status) and from `IGDBService.approve_match()` (when staff promotes `pending_review` to `accepted`). Closes the ordering gap where rule 1 could flag a concept by earn rate before a primary developer was known; the re-eval adds the developer to `DeveloperBlacklist` and fires the cascade.
- **Reconciliation**: `evaluate_concept(concept)` is the canonical concept-level entry point, used by the `update_shovelware` and `backfill_shovelware` management commands.

### Idempotence

`evaluate_concept` is idempotent: re-running it on a concept that's already in the correct state performs no DB writes. Internally, `_flag_concept` excludes already-`auto_flagged` games from its bulk UPDATE, and `_unflag_concept` excludes already-`clean` games. This preserves `shovelware_updated_at` across reconciliation passes.

### Flow Summary

```
evaluate_concept(concept):
    rates = plat earn rates across all games in concept
    primary_dev = first ConceptCompany(is_developer=True) if concept has trusted IGDBMatch else None

    if any(r >= 80%):
        flag concept
        if primary_dev: register in DeveloperBlacklist (cascades to other concepts)
        return

    if primary_dev and DeveloperBlacklist.is_blacklisted(primary_dev):
        if any(r < 30%):   unflag concept  # shielded
        else:              flag concept
        return

    unflag concept  # default state
```

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
| `flagged_concepts` | `JSONField` | List of `concept_id` strings that triggered the entry |
| `is_blacklisted` | `BooleanField` | True while any concept is tracked; cascade-flags use this |
| `date_added` | `DateTimeField` | auto_now_add |
| `notes` | `TextField` | Admin scratch pad |

A concept is added to `flagged_concepts` only when it triggers rule 1 (evidence of >=80% earn rate). Concepts flagged purely via rule 2 are **not** added to the list. When a concept transitions to shielded (unflagged despite an active blacklist entry), it is removed from the list; if the list empties, `is_blacklisted` flips back to False.

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

The recommended cron target (weekly cadence suggested). Walks a targeted candidate set rather than wiping state:

- Concepts with any currently `auto_flagged` game (catches spurious flags + new shield opportunities)
- Concepts containing a platinum at `>= 80%` earn rate (catches missed rule-1 flags)
- Concepts whose developers are on an active `DeveloperBlacklist` (catches missed rule-2 cascades)

Each candidate is re-evaluated via `evaluate_concept`. Because evaluation is idempotent, concepts already in the correct state produce zero DB writes, preserving `shovelware_updated_at`. Safe to run on demand at any time.

## Gotchas and Pitfalls

- **Shield is asymmetric.** A sub-30% game can override a developer-reputation flag, but **never** overrides a direct 80%+ game. If the concept has both, rule 1 wins and the concept is flagged.
- **No IGDB match = no cascade.** Concepts without a trusted IGDB match can still be flagged individually by the 80% rule, but we have no way to attribute the flag to a developer. Their back-catalog will only be flagged if their own platinum earn rates cross 80%.
- **Primary developer = first by id.** When IGDB lists multiple co-developers, only the first-ordered `ConceptCompany` row (with `is_developer=True`) participates in the blacklist. This matches IGDB's intended ordering, which conventionally puts the lead studio first. Co-dev attribution is intentionally simplified.
- **Shielded concepts don't appear in `flagged_concepts`.** Only rule-1 evidence is tracked. When a concept is shielded after previously triggering rule 1 (rates shifted), it is removed from the list and may un-blacklist the developer if it was the last piece of evidence.
- **Locked games are untouchable.** `shovelware_lock=True` + any status is immutable by auto-detection. Use `lock_shovelware --unlock` and re-run `update_shovelware` to restore auto behavior.
- **Earn-rate source.** Platinum earn rate comes from PSN API data synced during trophy sync. It reflects the global PSN population, not PlatPursuit users.

## Related Docs

- [Token Keeper](../architecture/token-keeper.md): Sync-time shovelware evaluation
- [Challenge Systems](../features/challenge-systems.md): Shovelware exclusion in game search
- [Review Hub](../features/review-hub.md): Shovelware concept exclusion
- [Cron Jobs](../guides/cron-jobs.md): Daily `update_shovelware` schedule
- [IGDB Integration](../architecture/igdb-integration.md): How developers are linked via `ConceptCompany`
