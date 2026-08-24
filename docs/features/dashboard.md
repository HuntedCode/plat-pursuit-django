# Dashboard System

> **STATUS: deleted 2026-08.** The modular dashboard is gone -- views, template, the 41-module registry,
> `dashboard_service`, `dashboard.js`, the `DashboardConfig` model (migration `0304`) and the customization
> API. This page records what it was and why it went, because pieces of its thinking survive elsewhere.

## What it was

The personal command center at `/` for synced users: a registry of 41 modules across 6 immutable system
tabs plus premium user-created custom tabs, drag-reorderable, with per-module lazy loading and a
server/client load-strategy split. `/dashboard/` redirected to `/` and `HomeView` called
`build_dashboard_context()` directly.

## Why it was deleted

It was the pre-rebuild answer to "where does a hunter land", and the rebuild answered that question
differently: `/` became a **lobby**, and the personal surfaces became real pages (Career, Collection, My
Stats, the Pursuer Card) rather than modules on a grid. Once those pages existed, the dashboard was a
second, worse copy of each of them, with a customization layer maintained for a page nobody needed to
customize any more.

It was also the origin of two production incidents, both worth carrying forward:

- **The premium-preview OOM.** Locked modules ran their real providers against the viewing user's data to
  build a blurred placeholder. For a free-tier hunter with a 250,000-trophy library that fanned out to 10+
  providers sequentially: 91 seconds and 153 MB per render, and 502s on `/`. The rule that came out of it
  (a preview's data layer must skip the provider *before* invoking it) is in the project CLAUDE.md and
  still binds.
- **The whale aggregation class.** Several providers iterated profile-scoped querysets in Python to build
  Counters. The DB-aggregation rule in the project CLAUDE.md is the residue.

## What survived

| Piece | Where it went |
|---|---|
| The design language it seeded | The Career page is the reference standard now; see [career-reference-standard.md](../design/rebuild/career-reference-standard.md) |
| `_built_for_hunters.html` | Deleted with the gates merge (2026-08); the landing's `land-pulse` band covers the heartbeat for every pre-synced state |
| Badge progress module | `collection_service.closest_badge`, read by Home's Collection CTA |
| `/dashboard/` URL | Still a permanent redirect to `/`, so old bookmarks keep working |

## Related Docs

- [home-page.md](home-page.md): what `/` is now
- [ia-and-subnav.md](../architecture/ia-and-subnav.md): the hub IA that replaced the tab grid
