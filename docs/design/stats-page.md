# My Stats Page - Design Document

> Dedicated page at `/my-stats/` with a video game stats screen aesthetic showing every possible stat about the user's trophy hunting career. Career Overview is free; all other sections are premium-only. Currently staff-gated for testing.

## Status: Implemented (Staff-Only)

**URL:** `/my-stats/`
**View:** `MyStatsView` (StaffRequiredMixin + ProfileHotbarMixin + TemplateView)
**Service:** `trophies/services/stats_service.py`
**API:** `GET /api/v1/stats/premium/` (returns rendered premium sections HTML)
**Template:** `templates/trophies/my_stats.html` + 13 partials in `templates/trophies/partials/stats/`
**Cache:** `stats_page:{profile_id}`, 4-hour TTL, invalidated on sync completion

**To launch publicly:**
1. Change `StaffRequiredMixin` to `LoginRequiredMixin` in `trophies/views/stats_views.py:15`
2. Remove the preview toggle button from `templates/trophies/my_stats.html`
3. Re-add the "My Stats" link to `templates/partials/navbar.html` (My Pursuit dropdown) and `templates/partials/mobile_tabbar.html` (My Pursuit section). The links were stripped in commit `d85a4f0` for the staff-only window.

## Architecture Decisions

- **Two-phase load:** Page shell (career overview) renders instantly with 0 queries. Premium stats load via AJAX during an intro animation, covering computation time.
- **No charts:** Pure stat rows/grids. Charts add complexity and render time better suited for the dashboard. The two pages complement each other: dashboard owns visual analytics, stats page owns the raw data dump.
- **No dashboard overlap:** Dedicated `stats_service.py` rather than sharing with `dashboard_service.py`. Different data shapes (all-time vs date-range), different caching profiles (4h vs 30m), different rendering strategy.
- **Sync-only updates:** Stats only change when a profile sync completes, so we cache aggressively and invalidate via a single `invalidate_stats_cache()` call in `token_keeper.py`.
- **Free user experience:** Career Overview (free, instant) + 3-4 Personal Records teaser with gradient fade + a dedicated CTA card listing all 11 locked sections by name. No animation for free users. No premium stat queries triggered.
- **Premium intro animation:** 4-second "stat scanner" sequence with PlatPursuit logo, cycling status messages, trophy counter, and staggered section reveal. Runs during the AJAX fetch so animation time = computation time. Replayable via button.
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
- **Premium toggle:** Uses the same `dashboard_preview_premium` session variable as the dashboard. Toggling on either page affects both.
- **Timezone:** All time-based calculations use `profile.user.user_timezone` via pytz. Display dates use Django's `TimezoneMiddleware`.
- **Null timestamps:** `EarnedTrophy.earned_date_time` can be null. Always filter `earned_date_time__isnull=False`.
- **Null play_duration:** `ProfileGame.play_duration` is nullable. Duration stats show `None` (template handles fallback).
- **Game name/icon fallback:** `Concept.unified_title` -> `Game.title_name`, `Concept.concept_icon_url` -> `Game.title_image`.
- **JSONField lists:** `Game.title_platform` and `Concept.genres` are JSON lists unnested in Python.
- **MonthlyRecap:** Always filter `is_finalized=True`.
- **Calendar milestones:** Progress is filled-day count, required value is `CALENDAR_DAYS_PER_MONTH[month]`, not `milestone.required_value`.
- **Region logic:** `is_regional=False` games count as "Global", not by their region tags.
- **Badge images:** Use `object-contain` (transparent backgrounds).
- **Animation:** Only runs for premium users. Free users get instant page load.
- **AJAX dependency:** Premium stats render requires the API endpoint. If it fails, an error card is shown.
