# My Stats Page - Design Document

> **HIDDEN 2026-08, and more deleted than this page implies.** `MyStatsView`, `stats_service.py`, the
> template and its 13 partials, and `/api/v1/stats/premium/` were DELETED in badge cutover 5b -- the
> "parked, not deleted" wording below and the recipe for bringing it back mechanically are both out of
> date. `/stats/` 302s to Home.
>
> The design thinking stands; the restoration instructions do not.

> Dedicated page at `/stats/` with a video game stats screen aesthetic showing every possible stat about the user's trophy hunting career. Career Overview is free; all other sections are premium-only.

## Status: HIDDEN for the 1.0 launch (2026-08)

The page below is **built and working**, but it is the last surface still wearing the pre-rebuild
design language, and it was never taken through the three-part rebuild process. Shipping a 120+-stat
dump at 1.0, right next to Career and Milestones, would set the wrong bar. So it is hidden rather than
rebuilt-in-a-hurry or deleted:

- **`/stats/` answers with a redirect to Home** (`RedirectView`, `permanent=False`). Everyone gets the
  same answer -- bookmarks, stale links, staff. It is deliberately a **302, not a 301**: browsers cache
  a permanent redirect hard, and the page is coming back at this same URL.
- The old `/my-stats/`, `/tools/stats/`, and `/dashboard/stats/` 301s stay, so they funnel into that
  bounce instead of 404ing.
- `MyStatsView` is **parked, not deleted** -- kept unrouted in `trophies/views/stats_views.py` (with its
  staff gate still on the class, so re-routing it mid-rebuild can't accidentally expose the old page).
  Its template, 13 partials, `stats_service.py`, and the `/api/v1/stats/premium/` endpoint are all kept
  the same way; the endpoint is unreachable in practice with nothing rendering the page that calls it.
- The My Pursuit sub-nav item and the `/stats/` hub prefix are removed from `core/hub_subnav.py`.
- The footer link is removed, and the "My Stats (120+ Stats)" perk card/line is pulled from
  `subscribe.html` + `subscription_management.html` (a paid perk must not point at a page that bounces).
- Pinned by `tests/engine/test_my_stats_hidden.py`.

**Still marketing it:** the landing page's "Stats Worth Bragging About" section (SECTION 6) sells this
page with a screenshot of it. That is a copy decision, batched with the other stale landing copy (it
still markets the retired Challenge system) rather than handled here.

**Relaunch:** as an upgraded tool, not this page re-skinned. It is the natural home of the
[Data Intelligence arc](data-intelligence.md) (one insight engine, three interfaces, materialized off
the request path). When it comes back, it goes through the full rebuild process like any other page and
gets a row in the [rebuild playbook](rebuild/rebuild-playbook.md).

**To bring it back (mechanically):** re-route `/stats/` at `MyStatsView` (re-adding the import in
`plat_pursuit/urls.py`), swap `StaffRequiredMixin` → `LoginRequiredMixin`, restore the sub-nav item +
`/stats/` prefix, the footer link, and the perk entries, and update `tests/engine/test_my_stats_hidden.py`.

---

## What exists today

**URL:** `/stats/` (currently a redirect to Home -- see Status)
**View:** `MyStatsView` (StaffRequiredMixin + TemplateView), parked/unrouted
**Service:** `trophies/services/stats_service.py`
**API:** `GET /api/v1/stats/premium/` (returns rendered premium sections HTML)
**Template:** `templates/trophies/my_stats.html` + 13 partials in `templates/trophies/partials/stats/`
**Cache:** `stats_page:{profile_id}`, 4-hour TTL, invalidated on sync completion

(The old "to launch publicly" checklist is superseded by the hide above — the page is not launching in
this form. See "To bring it back" in the Status section.)

## Architecture Decisions

- **Two-phase load:** Page shell (career overview) renders instantly with 0 queries. Premium stats load via AJAX during an intro animation, covering computation time.
- **No charts:** Pure stat rows/grids. Charts add complexity and render time better suited for the dashboard. The two pages complement each other: dashboard owns visual analytics, stats page owns the raw data dump.
- **No dashboard overlap:** Dedicated `stats_service.py` rather than sharing with `dashboard_service.py`. Different data shapes (all-time vs date-range), different caching profiles (4h vs 30m), different rendering strategy.
- **Sync-only updates:** Stats only change when a profile sync completes, so we cache aggressively and invalidate via a single `invalidate_stats_cache()` call in `token_keeper.py`.
- **Free user experience:** Career Overview (free, instant) + 3-4 Personal Records teaser with gradient fade + a dedicated CTA card listing all 11 locked sections by name. No animation for free users. No premium stat queries triggered.
- **Premium intro animation:** 4-second "stat scanner" sequence with PlatPursuit logo, cycling status messages, trophy counter, and staggered section reveal. Runs during the AJAX fetch so animation time = computation time. Plays once per browser session (tracked via `sessionStorage`); repeat visits silently fetch and reveal content instantly. Replay button is staff-only.
- **Milestone stats (not showcase):** Aggregate milestone data (earned/available counts, per-category progress bars, most recent + next closest). No individual milestone grid. Calendar month milestones use `CALENDAR_DAYS_PER_MONTH` for correct progress display.
- **Community ratings crossover:** Game Library section includes community rating averages for the user's library (difficulty, grindiness, fun, hours) plus extremes (hardest, easiest, most fun, most grindy).
- **Region handling:** Non-regional games count as "Global" instead of being split by region tags. Only `is_regional=True` games use specific region codes.
- **Contextual observations:** Flavor text annotations woven into relevant sections (e.g., "Night owl: 63% of your trophies are earned between midnight and 6 AM").

## Stat Sections (12 + recap)

| # | Section | Cost | Key Stats |
|---|---------|------|-----------|
| 1 | Career Overview (FREE) | 0 queries | Trophy counts, type distribution, rates, velocity, account age, PSN level |
| 2 | Personal Records | ~10 queries | First trophy, fastest/slowest plat, best day/week, plat gaps, playtime |
| 3 | Rarity Profile | ~6 queries | Tier distribution, avg earn rates by type, notable trophies, hardest/easiest game |
| 4 | Streaks & Consistency | 0 extra | Longest/current/plat streaks, drought, active days/ratio, monthly streak, yearly highlights |
| 5 | Time Patterns | 0 extra | Time of day, day of week, peak hour/day, weekend ratio, seasonal, year-over-year |
| 6 | Platform Breakdown | 0 extra | Trophies/games/plats by platform, cross-gen, avg progress |
| 7 | Genre Breakdown | 0 extra | Plats/games by genre, top publishers, genre diversity |
| 8 | Game Library | 1 extra | Backlog analysis, regions, community ratings crossover (difficulty/fun/grindiness extremes) |
| 9 | Badge & XP Stats | ~4 queries | XP, tier breakdown, top series, velocity, stages, series completed |
| 10 | Challenge Progress | ~5 queries | A-Z/Calendar/Genre progress, milestones, titles |
| 11 | Community | ~4 queries | Reviews, helpful/funny votes, ratings, most helpful review |
| 12 | Milestone Stats | ~3 queries | Earned/available counts, per-category progress, most recent + next closest |
| - | Monthly Recaps | ~2 queries | Months tracked, averages, best/worst months |

**Total premium queries: ~35-40, cached for 4 hours.**

## Query Strategy

Two shared fetches power multiple sections (avoiding redundant queries):
1. **Earned timestamps** (`earned_date_time`, `trophy_type`) for Sections 2, 4, 5
2. **Profile games** (with `select_related('game__concept')`) for Sections 6, 7, 8

## Key Files

| File | Purpose |
|------|---------|
| `trophies/services/stats_service.py` | All stat computation, caching, invalidation |
| `trophies/views/stats_views.py` | View class (staff-gated) |
| `api/dashboard_views.py` | `StatsPageDataView` API endpoint for premium stats HTML |
| `api/urls.py` | `stats/premium/` URL registration |
| `templates/trophies/my_stats.html` | Main page template (animation, shell, free CTA) |
| `templates/trophies/partials/stats/premium_sections.html` | All premium sections (rendered by API) |
| `templates/trophies/partials/stats/*.html` | 12 individual section partials |
| `trophies/token_keeper.py` | Cache invalidation hookpoint (after sync) |

**Navigation status:** The "My Stats" link was removed from both `navbar.html` and `mobile_tabbar.html` while the page is staff-gated (commit `d85a4f0`). The page is reachable directly via `/my-stats/` and via the dashboard's `my_stats_teaser` module (whose CTA links to it). Re-add the nav links to both templates as part of the public launch.

## Gotchas and Pitfalls

- **Staff-only:** Page uses `StaffRequiredMixin`. Swap to `LoginRequiredMixin` for public launch.
- **Timezone:** All time-based calculations use `profile.user.user_timezone` via pytz. Display dates use Django's `TimezoneMiddleware`.
- **Null timestamps:** `EarnedTrophy.earned_date_time` can be null. Always filter `earned_date_time__isnull=False`.
- **Null play_duration:** `ProfileGame.play_duration` is nullable. Duration stats show `None` (template handles fallback).
- **Game name/icon fallback:** `Concept.unified_title` -> `Game.title_name`, `Concept.concept_icon_url` -> `Game.title_image`.
- **JSONField lists:** `Game.title_platform` and `Concept.genres` are JSON lists unnested in Python.
- **MonthlyRecap:** Always filter `is_finalized=True`.
- **Calendar milestones:** Progress is filled-day count, required value is `CALENDAR_DAYS_PER_MONTH[month]`, not `milestone.required_value`.
- **Region logic:** `is_regional=False` games count as "Global", not by their region tags.
- **Badge images:** Use `object-contain` (transparent backgrounds).
- **Animation:** Only runs for premium users on their first visit per session. Repeat visits in the same session skip the intro. Free users get instant page load.
- **AJAX dependency:** Premium stats render requires the API endpoint. If it fails, an error card is shown.
