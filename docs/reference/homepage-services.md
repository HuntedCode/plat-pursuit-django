# Site Heartbeat & Home Shells

The site root (`/`) is no longer a single homepage. It is a smart router (`HomeView` in `core/views.py`, see [Home Page Router](../features/home-page.md)) that selects one of four templates based on whether the user is anonymous, has not linked PSN, is mid-sync, or is fully synced. The fully-synced state renders the dashboard directly. There is no separate "homepage view."

What this reference covers is the small set of services that still feed those four shells: a single hourly cache job, the cached community statistics it produces, and the partial that displays them across every state ("PlatPursuit at a Glance" / "Built for Hunters" ribbon). The previous fan-out of `featured_badges`, `featured_checklists`, `whats_new`, `playing_now`, and `featured_games` services has been removed; those collections were made redundant by the dashboard's module pipeline.

## What's Cached

| Cache key | Service | TTL | Frequency | Purpose |
|-----------|---------|-----|-----------|---------|
| `site_heartbeat_{YYYY-MM-DD}_{HH}` | `compute_site_heartbeat()` | 7200s (2x the cron interval) | Hourly via `refresh_homepage_hourly` | Aggregated platform stats for the "Built for Hunters" ribbon |

There is no daily homepage cache. `refresh_homepage_daily` was removed when the homepage redesign collapsed onto the dashboard. If you find a Render cron entry pointing at it, disable or delete it.

## site_heartbeat.py

Location: `core/services/site_heartbeat.py`. Single public function: `compute_site_heartbeat() -> dict`.

The dict shape is intentionally stable so the template can do simple nested lookups (e.g. `heartbeat.always.trophies_total.value`). All queries are wrapped in their own `try/except`; a failure on one stat sets `meta.is_partial = True` and returns `None` for that field rather than blanking the whole ribbon.

```python
{
    "meta": {"computed_at": ISO timestamp, "is_partial": bool},
    "always": {
        "trophies_total": {"value", "label", "sublabel"},
        "games_total":    {"value", "label", "sublabel", "delta"},
        "profiles_total": {"value", "label", "sublabel", "delta"},
        "trophies_24h":   {"value", "label", "sublabel"},
    },
    "expanded": {
        "platinums_total": {...},
        "badges_total":    {...},
        "badge_xp_total":  {...},
        "hours_hunted":    {...},
    },
    "flavor": {"tagline": str, "numbers": str},
}
```

The `always` group renders as the four primary stat tiles. The `expanded` group is shown when the ribbon is expanded. `hours_hunted` and `trophies_24h` are computed live in the service from `ProfileGame.play_duration` and `EarnedTrophy` respectively. The other six values are sourced from `core.services.stats.compute_community_stats()` so the heartbeat doesn't double-query the same aggregates.

The heartbeat is also the compute site for every **browse-header substance stat** (the 2026-08
consolidation: hourly cron, never the request path). `expanded` additionally carries
`games_in_badges` / `games_in_contracts` (Browse Games), `lists_total` (+7-day `delta`) /
`lists_regional` / `lists_with_plat` (Trophy Lists browse), `genres_with_games` /
`themes_with_games` (Genres & Themes index), and the badge-catalog values the Badges browse
header reads. Consuming views do a pure `get_cached_heartbeat()` read and their templates hide
the stat grid until the cron has warmed the cache.

## How It's Read

`HomeView` reads the cached heartbeat via `get_cached_heartbeat()` and attaches it to the
template context as `site_heartbeat` for every pre-synced state. The landing's `land-pulse`
band renders it (the old `_built_for_hunters.html` ribbon partial was deleted with the gates
merge; `home/landing.html` is the one pre-synced template now). If the cache is missing, the
band hides itself rather than showing zeros ({% if site_heartbeat %}). Check
`refresh_homepage_hourly` if it disappears.

## Refresh Job

`core/management/commands/refresh_homepage_hourly.py` runs every hour on Render Cron. It iterates a single-entry `HOURLY_JOBS` list, calls `compute_site_heartbeat()`, and writes the result to `site_heartbeat_{date}_{hour}` with a 2-hour TTL (so the previous bucket survives as a fallback while the current one is being written).

The command logs success or failure per stat. There is no retry: a failure means the ribbon falls back to the previous hour until the next run.

## Gotchas and Pitfalls

- **There is no daily homepage cron anymore.** Anything that references `refresh_homepage_daily` is stale (cron-jobs.md, old Render configurations, the previous version of this doc). Featured games/badges/checklists were folded into dashboard modules instead.
- **`featured_*` and `whats_new` services have been deleted.** Do not import them; they no longer exist in `core/services/`. The dashboard's "Recent Platinums," "Recent Badges," "What's New" feels are handled by per-module providers in `dashboard_service.py`.
- **The site heartbeat ribbon is one of two homepage caches.** The other is the Redis client cache used by individual dashboard modules (see [Dashboard](../features/dashboard.md) and [Redis Keys](redis-keys.md)). They are independent: invalidating dashboard module caches does not refresh the heartbeat, and vice versa.
- **The cache key is namespaced by hour, not by user.** Every visitor sees the same heartbeat snapshot for that hour. Don't try to personalize it; user-specific stats belong on the dashboard.
- **`is_partial` is silent in the UI.** If a single stat fails, the ribbon still renders with the rest. The flag exists for log scraping, not for end-users. Watch the `refresh_homepage_hourly` job logs in Render if you suspect query drift.
- **`hours_hunted` is real PSN playtime.** It is summed from `ProfileGame.play_duration` (a `DurationField`), not from any computed estimate. PS3 / Vita games typically lack play_duration so the number under-represents pre-PS4 hours.

## Related Docs

- [Home Page Router](../features/home-page.md): the `HomeView` smart router and the four shells
- [Dashboard](../features/dashboard.md): the synced state and where dashboard module data comes from
- [Cron Jobs](../guides/cron-jobs.md): the `refresh_homepage_hourly` schedule and dependencies
- [Redis Keys](redis-keys.md): full key map for raw Redis and Django cache
