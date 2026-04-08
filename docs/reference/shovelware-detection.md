# Shovelware Detection

Shovelware games (cheap, mass-produced platinum trophy farms) are automatically detected and flagged to protect the integrity of challenges, leaderboards, and community features. The system uses platinum earn rate thresholds with concept-level grouping and a publisher blacklist for cascade flagging.

## Detection Algorithm

Location: `trophies/services/shovelware_detection_service.py`

### Thresholds

| Threshold | Value | Purpose |
|-----------|-------|---------|
| `FLAG_THRESHOLD` | 90.0% | Platinum earn rate >= 90% triggers flagging |
| `UNFLAG_THRESHOLD` | 30.0% | Earn rate <= 30% allows unflagging |
| `SIBLING_HOLD` | 80.0% | Concept shield: one "hot" game holds the whole concept |

### Flagging Flow

1. During sync, when platinum trophy data arrives, `evaluate_game(game)` is called
2. **Publisher blacklist check**: If game's concept publisher is already blacklisted, flag immediately (unless concept is shielded)
3. **Earn rate check**: If platinum earn rate >= 90%, flag the game
4. **Concept sibling cascade**: When one game is flagged, ALL games in the same Concept are flagged together (one concept = one editorial unit)
5. **Concept shield**: A concept is only flagged if at least one game in it has >= 80% earn rate. This protects legitimate games that happen to share a concept with a shovelware edition.

### Unflagging Flow

1. When platinum data updates with earn rate <= 30%
2. Check if ANY sibling game in the concept still has >= 80% rate
3. If yes: concept stays flagged (one hot game holds all siblings)
4. If no: entire concept is unflagged
5. Publisher tracking updated; publisher may be un-blacklisted

### Publisher Blacklist

The `PublisherBlacklist` model tracks publishers with systemic shovelware:
- `name`: Publisher name
- `flagged_concepts` (JSONField): List of concept IDs flagged under this publisher
- `is_blacklisted` (bool): Flips True when 5+ concepts are flagged
- When blacklisted, all future games from this publisher are auto-flagged (with concept shield applied)

## Game Model Fields

```python
shovelware_status: CharField(choices=[
    'clean',              # Not shovelware
    'auto_flagged',       # Auto-detected >= 90%
    'manually_flagged',   # Admin locked as shovelware
    'manually_cleared',   # Admin locked as clean
])
shovelware_lock: BooleanField    # Prevents auto-detection changes
shovelware_updated_at: DateTimeField
```

The property `game.is_shovelware` returns `True` for both `auto_flagged` and `manually_flagged`.

## Impact

When a game is marked shovelware:
- **Challenges**: Excluded from A-Z, Calendar, and Genre challenge game search (cannot be assigned to slots)
- **Review Hub**: Concepts where ALL games are shovelware are excluded entirely (404)
- **Leaderboards**: Shovelware platinums may be filtered from certain views
- **Badges**: Shovelware games in badge stages still count (badges evaluate at concept level)

## Concurrency

Concept sibling operations use Redis locking to prevent deadlocks when concurrent sync jobs hit the same concept:
- Lock key: `shovelware_concept_lock:{concept_id}`
- Retry: 3 attempts with 0.1s exponential backoff
- Fallback: If lock fails, proceed without it (better than skip)

## Management Commands

| Command | Purpose | Usage |
|---------|---------|-------|
| `update_shovelware` | Full rebuild from scratch | `python manage.py update_shovelware [--dry-run] [--verbose]` |
| `lock_shovelware` | Manual flag/clear/unlock | `python manage.py lock_shovelware <np_id> --flag\|--clear\|--unlock` |

The `update_shovelware` command: resets all auto flags, rebuilds publisher blacklist, re-evaluates all games with >= 90% platinum rate. Respects locked games.

## Gotchas and Pitfalls

- **Concept-level grouping**: Games move together as a concept. Flagging one game flags all siblings. This is by design.
- **Locked games**: `shovelware_lock=True` prevents ALL auto-detection changes. Only manual admin commands can change locked games.
- **Concept shield is universal**: Applies during flag, unflag, and publisher blacklist cascade. Without it, legitimate games from blacklisted publishers would be incorrectly flagged.
- **Publisher blacklist threshold**: 5 concepts, not 5 games. A publisher with 5 flagged multi-platform concepts (potentially 15+ games) triggers the blacklist.
- **Earn rate source**: Platinum earn rate comes from PSN API data synced during trophy sync. It reflects the global PSN population, not just PlatPursuit users.

## Related Docs

- [Token Keeper](../architecture/token-keeper.md): Shovelware evaluation during sync
- [Challenge Systems](../features/challenge-systems.md): Shovelware exclusion in game search
- [Review Hub](../features/review-hub.md): Shovelware concept exclusion
- [Cron Jobs](../guides/cron-jobs.md): Daily `update_shovelware` schedule
