# Homepage Services

The homepage (`/`) displays community stats, featured content, and activity feeds. All data is pre-computed by cron jobs and stored in Django cache with time-bucketed keys. The view (`IndexView` in `core/views.py`) reads from cache with fallback to the previous time period if the current bucket is missing.

## Sections

### Hero Section
- Shown only to unauthenticated users
- Static marketing content with CTA to sign up

### Community Stats
- 8 aggregate statistics about the platform (total profiles, trophies earned, etc.)
- Cache: hourly time bucket
- Source: Cron-populated cache

### Featured Badges
- 6 curated badges: top 4 by `earned_count` (social proof) + 2 newest
- Filter: Tier 1 (Bronze entry), must have badge image, has `series_slug`
- Cache: daily time bucket

### Featured Checklists
- 4 most-tracked published checklists
- Ordered by `progress_save_count` (primary), `upvote_count` (tie-breaker)
- Filter: `status='published'`, `is_deleted=False`
- Cache: daily time bucket

### Shareable Cards Showcase
- Static example images in `static/images/showcase/`
- No dynamic data; requires manual screenshots from PlatPursuit profiles

### What's New Sidebar
- Up to 8 items from 3 sources (max 3 each): new badges, new checklists, new guides
- Time window: last 14 days
- All items sorted by timestamp descending
- Cache: hourly time bucket

## Services

### featured_badges.py (`core/services/`)

```python
def get_featured_badges(limit=6):
    # Top 4 by earned_count + 2 newest
    # Returns: list of badge dicts with name, series, tier, earned_count, layers
```

Badge layers come from `badge.get_badge_layers()` which returns a dict with backdrop/main/foreground image URLs. The `has_custom_image` flag determines whether to use `{% static %}` paths or raw URLs in templates.

### featured_checklists.py (`core/services/`)

```python
def get_featured_checklists(limit=4):
    # Most tracked (progress_save_count), then most upvoted
    # Returns: list of checklist dicts with author, game icon, description
```

Uses `select_related('profile', 'concept')` for author and game metadata. Description truncated to 150 chars.

### whats_new.py (`core/services/`)

```python
def get_whats_new(limit=8):
    # Aggregates 3 item types in unified feed
    # Returns: list of activity items sorted by timestamp
```

Item types:
- `new_badge`: Recently created badge series (tier=1, last 14 days, max 3)
- `new_checklist`: Recently published checklists (last 14 days, max 3)
- `new_guide`: Concepts with guides created in last 14 days (max 3)

### playing_now.py (`core/services/`)

Returns profiles with recent sync activity, indicating currently active users.

## Caching

### Cache Key Patterns

| Key Pattern | TTL | Frequency | Purpose |
|-------------|-----|-----------|---------|
| `community_stats_{YYYY-MM-DD}_{HH:00}` | 3600s | Hourly | Platform aggregate stats |
| `latest_badges_{YYYY-MM-DD}_{HH:00}` | 3600s | Hourly | Recently awarded badges |
| `whats_new_{YYYY-MM-DD}_{HH:00}` | 3600s | Hourly | Activity feed items |
| `featured_badges_{YYYY-MM-DD}` | 86400s | Daily | Curated badge showcase |
| `featured_checklists_{YYYY-MM-DD}` | 86400s | Daily | Curated checklist showcase |
| `featured_games_{YYYY-MM-DD}` | 86400s | Daily | Featured games |
| `playing_now_{YYYY-MM-DD}` | 172800s | Daily | Active players |

### Fallback Pattern

The view checks the current time bucket first. If empty (cron hasn't run yet), it falls back to the previous period:
- Hourly keys: check `{HH-1:00}` bucket
- Daily keys: check `{YYYY-MM-DD-1}` bucket

This prevents blank sections during the brief window after midnight or between cron runs.

## Management Commands

| Command | Schedule | Purpose |
|---------|----------|---------|
| `refresh_homepage_hourly` | Every hour | Populate community stats, latest badges, what's new |
| `refresh_homepage_daily` | Daily midnight UTC | Populate featured badges, checklists, games |
| `redis_admin --flush-index` | Manual | Clear all homepage cache keys |

## Gotchas and Pitfalls

- **Badge image serialization**: `get_badge_layers()` returns a dict. Templates check `has_custom_image` to decide between `{% static %}` and raw URL. Getting this wrong shows broken images.
- **Fallback TTL**: Daily keys use 86400s (24h), but the fallback checks the previous day. If both days are missing, sections render empty.
- **Cron dependency**: The homepage is entirely cache-driven. If cron jobs stop running, data becomes stale after TTL expiry. There is no on-demand fallback to live queries.
- **Showcase images are static**: The shareable cards section uses pre-captured screenshots. These need manual updates when the share card design changes.

## Related Docs

- [Cron Jobs](../guides/cron-jobs.md): Homepage cache warming schedules
- [Badge System](../architecture/badge-system.md): Badge data and `get_badge_layers()`
- [Checklist System](../features/checklist-system.md): Checklist data model
- [Redis Keys](redis-keys.md): Complete cache key reference
