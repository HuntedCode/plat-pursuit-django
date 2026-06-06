# Shovelware Detection

Shovelware games (cheap, mass-produced platinum trophy farms) are automatically detected and flagged to protect the integrity of challenges, leaderboards, and community features. Detection keys off a concept's **median** platinum earn rate across its versions, plus a **proportional** IGDB-developer blacklist for catalog-wide cascade flagging and an admin-curated developer **whitelist** for full exemption.

## Detection Algorithm

Location: [trophies/services/shovelware_detection_service.py](../../trophies/services/shovelware_detection_service.py)

### Representative earn rate: the median

A concept usually has several versions (regional + PS4/PS5), each with its own platinum earn rate. Detection uses the **median** of those rates (over non-admin-locked versions), not the maximum of any single version. The median is robust to a low-population regional or legacy version with an inflated earn rate, which under a max-based rule would flag a concept whose typical version is a legitimately challenging platinum. For a single-version concept the median is just that version's rate, so behavior is unchanged for the common case.

The same median drives all three judgments (rule 1, the shield, and the blacklist proportion), so the "one weird version" problem cannot resurface at any level.

### Thresholds

| Constant | Value | Purpose |
|----------|-------|---------|
| `FLAG_THRESHOLD` | 80.0% | Median platinum earn rate >= 80% flags the whole concept (rule 1). Also the enter-numerator rate for the blacklist proportion. |
| `SHIELD_THRESHOLD` | 40.0% | Median platinum earn rate `< 40%` shields a concept from rule 2 |
| `EVIDENCE_THRESHOLD` | 70.0% | The stay-numerator rate for the blacklist proportion |
| `BLACKLIST_PROPORTION` | 0.50 | A developer is blacklisted when **more than** 50% of their platinum-bearing concepts are independently shovelware |
| `BLACKLIST_MIN_CONCEPTS` | 3 | Floor: the proportional rule applies only when a developer has at least 3 platinum-bearing primary-developed concepts |

The band between `SHIELD_THRESHOLD` (40%) and `FLAG_THRESHOLD` (80%) is the **gray zone**: a concept whose typical version sits there is flagged only if its primary developer is blacklisted. Below 40% the concept is shielded; at or above 80% rule 1 flags it outright.

The 10% gap between `FLAG_THRESHOLD` (80%) and `EVIDENCE_THRESHOLD` (70%) is deliberate hysteresis on the numerator rate: a developer is measured at the strict 80% bar to **enter** the blacklist and only **leaves** when the proportion falls below 50% even at the looser 70% bar. This prevents oscillation when individual earn rates jitter around the flag threshold.

### Primary Developer

The "primary developer" of a concept is the first `ConceptCompany` row with `is_developer=True`, ordered by `id` (ascending). This matches IGDB's `involved_companies` array order, where the lead studio is listed first.

The primary developer is **only considered** when the concept has an `IGDBMatch` with `is_trusted=True` (status is `accepted` or `auto_accepted`). Concepts without a trusted IGDB match cannot propagate flags through the developer blacklist, but they can still be flagged by direct earn-rate evidence.

### Proportional Blacklisting

The blacklist measures a **proportion**, not a single concept:

```
proportion = independently_shovelware_concepts / platinum_bearing_primary_developed_concepts
```

- **Numerator** (`DeveloperReputation.qualifying_concepts_for(company, threshold)`): concepts primary-developed by the company whose **median** platinum earn rate is at or above a rate threshold (80% to enter, 70% to stay). Computed in Postgres via `percentile_cont(0.5)` so it agrees with the in-Python median used by rule 1.
- **Denominator** (`DeveloperReputation.primary_developed_concepts(company)`): concepts primary-developed by the company that have at least one non-locked, platinum-bearing game (any earn rate). Every concept in the numerator is also in the denominator, so the ratio is always apples-to-apples.

**Critical:** the numerator is derived from live earn-rate evidence, **never** from `shovelware_status`. Counting cascade-flagged concepts would create a feedback loop (the cascade inflates the proportion, which pins the developer on the blacklist forever). Because evidence is a deterministic projection of earn rates, the order of concept evaluation never affects the final blacklist set.

### Rules

**Rule 1 (earn-rate, always wins):** If the concept's median platinum earn rate is `>= 80.0%`, flag every non-locked game in the concept. If the concept has a trusted IGDB match, evaluate its primary developer for the proportional blacklist (`_maybe_blacklist_developer`); on a fresh activation the cascade flags every other non-shielded concept the developer is primary on.

**Rule 2 (developer blacklist):** If the concept's primary developer is on an active `DeveloperReputation` entry (`is_blacklisted=True`), flag the concept **unless shielded**. Before flagging, the service re-checks the proportion at the 70% stay threshold; if it has dropped to <= 50%, the developer is released (which fires an immediate unflag cascade) and the concept falls through to the default unflag path.

**Shield (rule 2 only, never rule 1):** A concept is shielded when its median platinum earn rate is `< 40.0%`. The shield represents positive evidence of legitimacy (a typically challenging platinum) that overrides a reputation-based flag. A median `>= 80.0%` is direct evidence and always wins (rule 1 fires before the shield is ever consulted).

**Developer whitelist (full exemption, wins over everything):** If the concept's primary developer is on a `DeveloperReputation` entry with `is_whitelisted=True`, **no concept they are primary developer on is ever auto-flagged** (rule 1 included). This is the escape hatch for studios that ship legitimately high-earn-rate games (visual novels, narrative titles). The whitelist is admin-set only. Per-concept `manually_flagged` locks still win, so an admin can force-flag a single bad title even for a whitelisted developer.

**Admin-locked games are invisible to auto-detection.** Any game with `shovelware_lock=True` (set via `lock_shovelware --flag` or `--clear`) is filtered out of every rate-based calculation: its earn rate does not contribute to rule 1 on siblings, does not contribute to the shield, and does not contribute to the blacklist proportion (numerator or denominator). Admin has the final say per-game.

### Entry Points

- **Sync-time**: `evaluate_game(game)` is called when platinum trophy data is created or updated. It delegates to `evaluate_concept(concept)` when the game has a concept, or falls back to a simple per-game earn-rate check for standalone games.
- **IGDB trust transitions**: `on_igdb_match_trusted(concept)` is called from `IGDBService.process_match()` (when a new match is saved with `auto_accepted` status) and from `IGDBService.approve_match()` (when staff promotes `pending_review` to `accepted`). Closes the ordering gap where rule 1 could flag a concept by earn rate before a primary developer was known; the re-eval re-measures the developer's proportion and fires the cascade if the threshold is met.
- **Admin whitelist toggle**: `on_developer_whitelisted(company)` / `on_developer_unwhitelisted(company)` are called from `DeveloperReputationAdmin.save_model` when the `is_whitelisted` flag changes, so the exemption (or its removal) takes effect across the developer's catalog immediately.
- **Reconciliation**: `evaluate_concept(concept)` is the canonical concept-level entry point, used by the `update_shovelware` and `backfill_shovelware` management commands.

### Idempotence

`evaluate_concept` is idempotent: re-running it on a concept that's already in the correct state performs no DB writes. Internally, `_flag_concept` excludes already-`auto_flagged` games from its bulk UPDATE, and `_unflag_concept` excludes already-`clean` games. This preserves `shovelware_updated_at` across reconciliation passes.

### Flow Summary

```
evaluate_concept(concept):
    primary_dev = first ConceptCompany(is_developer=True) if concept has trusted IGDBMatch else None

    if primary_dev and is_whitelisted(primary_dev):
        unflag concept   # full exemption; never auto-flag a whitelisted dev
        return

    median = median platinum earn rate over non-admin-locked games (None if no platinum)

    if median >= 80%:
        flag concept
        if primary_dev: maybe_blacklist_developer(primary_dev)  # proportional enter check; cascades on new activation
        return

    if primary_dev and DeveloperReputation.is_blacklisted(primary_dev):
        if dev_meets_blacklist_threshold(primary_dev, 70%):    # proportion at stay bar still > 50%
            if median < 40%:   unflag concept  # shielded
            else:              flag concept
            return
        else:
            release_developer(primary_dev)   # set inactive + immediate unflag cascade
            # fall through to default unflag

    unflag concept  # default state
```

### Hysteresis

A developer enters the blacklist when the proportion measured at the 80% bar exceeds 50% (with at least 3 platinum-bearing concepts). They leave when `_dev_meets_blacklist_threshold(company, 70%)` returns False: the proportion at the looser 70% bar has dropped to 50% or below. Because the 70% filter admits at least as many concepts as 80%, a developer must earn a strong majority to enter but only leaves once even the looser bar fails. On release, every cascade-only flag clears immediately via `_unflag_developer_concepts`.

The proportion is **derived** from live queries, not cached. This means:

- Concept mergers, deletions, and IGDB data changes never leave stale blacklist state
- Admin-locked games (in either direction) are correctly excluded from both numerator and denominator
- `DeveloperReputation.qualifying_concepts_for(company)` and `primary_developed_concepts(company)` are the single source of truth for the proportion, the admin display, and the management-command release sweep

### Cascade on New Blacklist Entry

When `_maybe_blacklist_developer` promotes a developer to `is_blacklisted=True` for the first time, `_flag_developer_concepts(company)` iterates every other concept whose primary developer is that company and flags the non-shielded ones. This is how a developer crossing the proportional threshold propagates to their back-catalog without waiting for the daily rebuild. The symmetric `_unflag_developer_concepts` runs on release and clears every cascade-only flag (concepts with a median >= 80% stay flagged by rule 1 on their own merit).

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

## DeveloperReputation Model

| Field | Type | Notes |
|-------|------|-------|
| `company` | `OneToOneField(Company)` | IGDB company; one entry per developer |
| `is_blacklisted` | `BooleanField` | True while > 50% of the company's platinum-bearing primary-developed concepts are independently shovelware; cascade-flags use this |
| `is_whitelisted` | `BooleanField` | Admin full exemption; wins over `is_blacklisted`. The company's primary-developed concepts are never auto-flagged |
| `date_added` | `DateTimeField` | auto_now_add |
| `notes` | `TextField` | Admin scratch pad |

The flagged-concepts count is **not stored** on the model. It is derived from live data via `DeveloperReputation.qualifying_concepts_for(company)` (numerator) and `primary_developed_concepts(company)` (denominator). The admin's "Shovelware Concepts" column renders the live `N / D (P%)` proportion.

Deriving the proportion live means concept mergers, deletions, and IGDB company rewrites self-heal without any migration logic. `is_whitelisted` is the only stored decision. It also means `Concept.absorb()` requires no special handling for `DeveloperReputation`.

## Impact

When a game is marked shovelware:
- **Challenges**: Excluded from A-Z, Calendar, and Genre challenge game search
- **Review Hub (dormant)**: The `/reviews/` detail view 404s when ALL games in a concept are shovelware
- **Ratings**: NOT gated. Shovelware platinums can be rated (rate POST + share/dashboard/game-detail prompts). The Rate My Games wizard hides shovelware from its queue by default, opt-in via `?include_shovelware=1`
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
| `backfill_shovelware` | One-shot reset + rebuild from scratch (whitelists preserved) | `python manage.py backfill_shovelware [--dry-run] [--verbose]` |
| `update_shovelware` | Surgical reconciliation (idempotent) | `python manage.py update_shovelware [--verbose]` |
| `lock_shovelware` | Manually flag / clear / unlock a concept | `python manage.py lock_shovelware <np_id> --flag\|--clear\|--unlock` |
| `review_shovelware_blacklist` | Read-only review sheet of blacklisted developers (proportion, genres/themes, sample games) to decide whitelist candidates. Add `--compact` for a one-line-per-developer summary that's easy to hand to staff | `python manage.py review_shovelware_blacklist [--compact] [--samples N] [--limit N] [--include-whitelisted]` |

### Day-to-day flagging

New-game discovery and drift into the >=80% zone are handled **in real time** by the sync pipeline. TokenKeeper calls [`psn_api_service.py`](../../trophies/services/psn_api_service.py) on every platinum sync, which calls `ShovelwareDetectionService.evaluate_game(game)`. Missed attribution (rule 1 flagged but no primary developer known yet) is repaired by the IGDB hooks on match creation and approval. No management command needs to run for routine operation.

### `backfill_shovelware`

Use once after schema migrations or when state diverges significantly from what the current algorithm would compute (e.g. after the proportional-blacklist migration). It:

1. Resets every `auto_flagged` game to `clean` (respects `shovelware_lock` and manual statuses).
2. Resets `is_blacklisted=False` on every `DeveloperReputation` entry (does **not** delete entries: that would wipe admin-curated whitelists and notes).
3. **Pass 1**: evaluates every concept with *any* version at `>= 80%` earn rate (an intentional superset of "median >= 80%", since a high median implies a high max), re-seeding the blacklist as developers cross the proportional threshold (cascade fires). `evaluate_concept` is authoritative, so concepts that only had a single high outlier are correctly left clean. Whitelisted developers self-skip.
4. **Pass 2**: evaluates remaining concepts whose primary developer ended up on the new blacklist.

### `update_shovelware`

The recommended cron target (daily cadence is fine thanks to idempotence). Walks a targeted candidate set rather than wiping state:

- Concepts with any currently `auto_flagged` game (catches spurious flags + new shield opportunities)
- Concepts containing *any* version at `>= 80%` earn rate (a superset of median >= 80%; catches missed rule-1 flags, and `evaluate_concept` correctly leaves single-outlier concepts clean)
- Concepts whose developers are on an active `DeveloperReputation` blacklist (catches missed rule-2 cascades and proportion drops)

Each candidate is re-evaluated via `evaluate_concept`. Because evaluation is idempotent, concepts already in the correct state produce zero DB writes, preserving `shovelware_updated_at`. Safe to run on demand at any time.

**Final sweep:** after the per-concept pass, the command iterates every `DeveloperReputation` entry with `is_blacklisted=True` and re-checks the proportion at the 70% stay threshold. Any developer who no longer clears it (e.g. because they lost "primary developer" status on enough concepts after admin edits or IGDB data changes, so the per-concept loop never touched their entry) is released via `_release_developer`, which also fires the unflag cascade. Without this sweep, such stranded entries would persist indefinitely.

## Gotchas and Pitfalls

- **Median, not max.** A concept is judged by the median earn rate of its versions, so a single low-population version with an inflated rate (e.g. an old regional copy at 85% amid versions at 20-30%) no longer flags the whole concept. The flip side: a genuinely-shovelware concept needs its *typical* version to be easy, not just one. For single-version concepts (the majority) the median is that one rate, so nothing changed.
- **Even-version-count medians interpolate.** `statistics.median` (Python, rule 1 / shield) and Postgres `percentile_cont(0.5)` (the proportion queries) both average the two middle values for an even count. They are kept in sync deliberately; if you ever swap one for a discrete percentile, the Python and DB paths will disagree.
- **The proportion is computed from earn-rate evidence, not flag status.** Counting `auto_flagged` concepts would let the cascade inflate the numerator and pin developers on the blacklist permanently. Always measure via `qualifying_concepts_for` / `primary_developed_concepts`.
- **3-concept floor.** A developer with only 1-2 platinum-bearing concepts is never blacklisted, even at 100% shovelware. Rule 1 still flags those concepts individually; there is just no catalog to cascade to, so blacklisting would be noise.
- **Shield is gray-zone only.** A median below 40% shields a concept from a developer-reputation (rule 2) flag, but the shield is never consulted for rule 1: a median >= 80% flags outright. The two thresholds can't both apply to one median.
- **Whitelist beats rule 1.** A whitelisted developer's 95%-median game is NOT auto-flagged. This is intentional (high-effort VNs). Use a per-game `manually_flagged` lock to override for a specific title.
- **No IGDB match = no cascade, no whitelist.** Concepts without a trusted IGDB match can still be flagged individually by the median-80 rule, but we have no way to attribute the flag (or exemption) to a developer.
- **Primary developer = first by id.** When IGDB lists multiple co-developers, only the first-ordered `ConceptCompany` row (with `is_developer=True`) participates in the blacklist/whitelist. A whitelisted studio that merely co-developed a mill's title (the mill is primary) does not shield that title. Co-dev attribution is intentionally simplified.
- **Hysteresis is a rate deadband on a single proportion boundary.** Enter when the 80%-median proportion exceeds 50%; release when the 70%-median proportion drops to <= 50%. A concept whose median crosses 80% and pushes a developer over half their catalog blacklists; a concept drifting to a 79% median does not release them (still counts at the 70% bar).
- **Locked games are untouchable AND invisible.** `shovelware_lock=True` + any status is immutable by auto-detection, AND the locked game's earn rate is filtered out of the median and the blacklist proportion. An admin-cleared game's 95% rate will not pull up its concept's median or count toward its developer's proportion.
- **Earn-rate source.** Platinum earn rate comes from PSN API data synced during trophy sync. It reflects the global PSN population, not PlatPursuit users. Early-lifecycle games with few players can have unstable earn rates, but reconciliation + hysteresis ensure any false-positive damage is transient.

## Related Docs

- [Token Keeper](../architecture/token-keeper.md): Sync-time shovelware evaluation
- [Challenge Systems](../features/challenge-systems.md): Shovelware exclusion in game search
- [Review Hub](../features/review-hub.md): Shovelware concept exclusion
- [Cron Jobs](../guides/cron-jobs.md): Daily `update_shovelware` schedule
- [IGDB Integration](../architecture/igdb-integration.md): How developers are linked via `ConceptCompany`
