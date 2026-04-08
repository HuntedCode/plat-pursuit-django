# Dashboard Module Catalog

The dashboard is the synced-state home page (`/`) for all logged-in users. Modules are organized into a **tabbed navigation system** with 6 immutable system tabs and up to 6 user-created custom tabs (premium). Each module belongs to a default category tab.

**For the canonical, always-current module list, see [Dashboard System](../features/dashboard.md).** This catalog is the design/roadmap document: it tracks the original vision, what was cut, and what (if anything) remains planned. When the live registry and this doc disagree, the live registry wins and this doc should be updated.

**Design principles (still load-bearing):**
- **Performance first:** Always cache, always invalidate properly. This is the production home page.
- **Badges are the selling point:** Maximum badge presence across modules.
- **Smart CTAs:** Modules show actionable prompts when the user hasn't started (never blank cards).
- **Premium as upgrade path:** Settings, reorder, and premium-exclusive modules drive conversions.
- **Visualizations sell premium:** Rich data visualizations are the primary premium differentiator.

## Module Status (as of 2026-04-08)

| Status | Count |
|--------|-------|
| Complete (live in `DASHBOARD_MODULES`) | 41 |
| Cut (planned but rejected as low value) | 9 |
| **Catalog total** | **50** |

The "Complete" count is the source of truth from `DASHBOARD_MODULES` in `trophies/services/dashboard_service.py`. Run `grep -c "^        'slug':" trophies/services/dashboard_service.py` to verify.

---

## Category: At a Glance

| # | Module | Strategy | Status | Data Source |
|---|--------|----------|--------|------------|
| 1 | Trophy Snapshot | Server | **Done** | Profile (denormalized, 0 queries) |
| 2 | Recent Platinums | Lazy (5m) | **Done** | EarnedTrophy + Trophy + Game + Concept |
| 3 | Recent Activity | Lazy (5m) | **Done** | EarnedTrophy + UserBadge, grouped by game+day |
| 4 | Sync Status | Server | **Cut** | Redundant with sync hotbar on every page |
| 5 | Unread Notifications | Lazy | **Cut** | Redundant with navbar notification bell |
| 29 | Quick Settings | Server | **Done** | Profile + CustomUser (settings fields) |
| 37 | My Stats Teaser | Lazy (10m) | **Done** | `stats_service.get_career_overview()`. 4 hero stats + CTA to `/my-stats/` |
| 40 | Trophy Diversity Score | Lazy (30m) | **Done** | `ConceptGenre` + `ConceptTheme` M2Ms. Composite 0-100 score across distinct genres + themes the user has trophies in (IGDB-powered) |

### Quick Settings (#29) - DONE
- Inline toggle switches for `hide_hiddens`, `hide_zeros`, `use_24hr_clock`
- Timezone display with browser-detect button
- Default region dropdown (Any, NA, EU, JP, AS, KR, CN)
- Server-rendered (reads Profile + User fields, zero extra queries)
- Auto-saves via `POST /api/v1/user/quick-settings/` with toast confirmation
- Timezone changes un-finalize the current month's recap
- All users (not premium-gated)
- Default tab: `at_a_glance`

## Category: Progress & Challenges

All challenge modules show smart CTAs when no active challenge exists.

| # | Module | Strategy | Status | Data Source |
|---|--------|----------|--------|------------|
| 6 | Challenge Hub | Lazy (5m) | **Done** | Challenge + slot models, mini previews |
| 7 | A-Z Challenge Progress | Lazy (5m) | **Done** | AZChallengeSlot + icons, last plat, next target, Pick Next Game CTA |
| 8 | Calendar Challenge Progress | Lazy (5m) | **Done** | CalendarChallengeDay, 3-month paginated view with JS arrows |
| 9 | Genre Challenge Progress | Lazy (5m) | **Done** | GenreChallengeSlot + subgenres + last plat, next target, Pick Next Game CTA |
| 10 | Milestone Tracker | Lazy (10m) | **Done** | UserMilestoneProgress, Python-side pct sort |
| 11 | Almost There | Lazy (10m) | **Done** | ProfileGame (90%+ configurable threshold) |
| 28 | Roadmaps for Your Library | Lazy (30m) | **Done** | `Roadmap` (1:1 with `Concept`). Surfaces published roadmaps for concepts in the user's library where they haven't platinumed yet. The dashboard replacement for the legacy "My Checklists" surface. |
| 41 | VR Trophy Hunter | Lazy (30m) | **Done** | PSVR + PSVR2 progress at a glance: stats, in-progress games, "back into the headset" CTA. Powered by IGDB VR-platform detection. |

## Category: Badges & Achievements

Badges are a flagship feature with dedicated real estate.

| # | Module | Strategy | Status | Data Source |
|---|--------|----------|--------|------------|
| 12 | Badge Progress | Lazy (10m) | **Done** | UserBadgeProgress, prerequisite filtering |
| 13 | Recent Badges | Lazy (10m) | **Done** | UserBadge (earned_at desc) |
| 14 | Badge Stats | Lazy (10m) | **Done** | UserBadge tier breakdown, rarest badge, collection rate |
| 15 | Badge XP & Leaderboard | Lazy (10m) | **Done** | ProfileGamification + leaderboard neighborhood (2 above/below) |
| 15b | Country XP Leaderboard | Lazy (10m) | **Done** | Per-country Redis sorted sets + leaderboard neighborhood |

## Category: Share & Export

| # | Module | Strategy | Status | Data Source |
|---|--------|----------|--------|------------|
| 16 | Badge Showcase | Lazy (10m) | **Done** | UserBadge selection, featured badge for profile card |
| 17 | Profile Card | Lazy (none) | **Done** | Client-side HTML preview via `/api/v1/profile-card/html/`, PNG download |
| S1 | Latest Platinum | Lazy (10m) | **Done** | Live share card preview via `/api/v1/shareables/platinum/<id>/html/` |
| S2 | Challenge Cards | Lazy (10m) | **Done** | Up to 3 challenge share card previews (A-Z, Calendar, Genre) |
| S3 | Recap Card | Lazy (30m) | **Done** | Most recent finalized recap share card preview |
| S4 | Platinum Grid CTA | Lazy (10m) | **Done** | Builds a shareable grid image of every platinum the user has earned |

Moved to Highlights: My Reviews (#16 old), Rate My Games (#18 old). Cut: Community Spotlight, My Checklists, My Game Lists.

## Category: Highlights & History

| # | Module | Strategy | Status | Data Source |
|---|--------|----------|--------|------------|
| 16 | My Reviews | Lazy (10m) | **Done** | Review aggregate stats + recent review list |
| 18 | Rate My Games | Lazy (30m) | **Done** | Unrated platinums with auto-scrolling ticker strip |
| 21 | Trophy Timeline | Lazy | **Cut** | Low utility, didn't add enough value |
| 22 | Monthly Recap Preview | Lazy (30m) | **Done** | MonthlyRecap (most recent finalized) |
| 23 | Rarity Showcase | Lazy (10m) | **Done** | EarnedTrophy sorted by earn_rate, rarity_color_hex |
| 32 | Personal Records | - | **Moved** | Moved to `/my-stats/` dedicated page (shipped, currently staff-gated) |
| 38 | Top Studios | Lazy (30m) | **Done** | `ConceptCompany` (developer/publisher roles) joined to earned trophies. Sub-tab toggle between developers and publishers. Links to `/companies/<slug>/`. IGDB-powered. |
| 39 | Library Health Alerts | Lazy (30m) | **Done** | Surfaces games in the user's library with auto-applied data quality flags from the `GameFlag` system. Severity buckets: blockers (delisted/unobtainable), issues (buggy/online), info (manually flagged). Filters to games where the user has earned at least one trophy. |
| 42 | Earned Titles | Lazy (10m) | **Done** | Quick-equip from the user's collection of earned `UserTitle` records. Switch displayed title without leaving the dashboard. |

## Category: Utility

| # | Module | Strategy | Status | Data Source |
|---|--------|----------|--------|------------|
| 24 | Quick Links | Server | **Cut** | Low value: navbar covers navigation |
| 25 | Featured Content | Lazy | **Cut** | Better suited for homepage than dashboard |

## Category: Premium Exclusive

The premium dashboard should feel indispensable. Visualization modules are the primary conversion drivers.

| # | Module | Strategy | Status | Data Source |
|---|--------|----------|--------|------------|
| 26 | Advanced Stats | Lazy (30m) | **Done** | EarnedTrophy aggregates (velocity, rarity tiers, platform breakdown, completion tiers, time/day patterns) |
| 27 | Theme Picker (formerly Premium Settings) | Lazy (none) | **Done** | Quick-pick row + full 105-theme browser. Renamed from "Premium Settings" to better describe what it does. |
| 30-34 | Trophy Visualizations | Lazy (30m) | **Done** | Combined module with year/all selector. Contains: trophy heatmap (CSS grid), engine radar (Chart.js, replaces the old genre radar), year-in-review (Chart.js line), games started vs completed (Chart.js line), trophy progress earned vs unearned (Chart.js line), yearly totals with quarterly breakdown (Chart.js stacked bar, all-time mode). Uses first-earn-per-game reconstruction for accurate unearned history. |
| 35 | Advanced Badge Stats | Lazy (30m) | **Done** | Badge velocity, series completion depth, XP breakdown by series, badge type distribution, tier distribution, rarest badges, stage progress, XP efficiency, stage velocity. Date range switcher using StageCompletionEvent. |
| 36a | Badge Series Overview | Lazy (30m) | **Done** | All-time badge status: stage progress bar (current grind), series XP radar, series completion tier grid. Not date-filtered. |
| 36b | Badge Visualizations | Lazy (30m) | **Done** | Date-filtered badge timeline with year/all selector: stages by series (stacked bar), XP growth, badges vs stages (dual cumulative line), stage completion rate. All stage data from StageCompletionEvent. |
| 43 | Genre & Themes (theme_mastery) | Lazy (30m) | **Done** | Combined module: side-by-side IGDB genre and theme radars across earned trophies (`ConceptGenre` + `ConceptTheme`). All-time only. The year-aware genre radar lives nowhere now since the field was moved out of trophy_visualizations into engine_radar. |
| 44 | Time-to-Beat | Lazy (30m) | **Done** | `IGDBMatch.time_to_beat_completely` to compute average plat duration, longest hunts, and currently-grinding remaining hours. IGDB-powered. |
| 45 | Profile Showcase Editor | Lazy (none) | **Done** | Drag-reorder for the 5-slot `ProfileBadgeShowcase`. Persisted via `POST /api/v1/badges/showcase/reorder/`. Coexists with the existing `badge_showcase` module (which handles the share-card featured badge picker). |
| 32 | Personal Records / Stats Page | - | **Done (off-dashboard)** | The `/my-stats/` page shipped (currently staff-gated). Not a dashboard module; the dashboard's `my_stats_teaser` (#37) is the on-dashboard surface that links to it. See [Stats Page](stats-page.md). |

### Visualization Module Details (Historical Sketches)

The original catalog defined five separate visualization modules (Platinum Heatmap, Rarity Radar, Personal Records, Year in Review, Trophy Type Breakdown). All shipped, but as **components inside the combined `trophy_visualizations` module** rather than standalone tiles. Pulling them apart would have meant six separate Chart.js loads on the same tab. The combined module is the canonical implementation.

For the rendering decisions and chart-library choices that resulted, see the live [`trophy_visualizations` module template](../../templates/trophies/partials/dashboard/trophy_visualizations.html) and `provide_trophy_visualizations()` in `dashboard_service.py`.

**Chart library decision (final):** Chart.js (~60KB gzipped, Canvas-based) is loaded lazily only when a premium visualization tab is activated. Pure CSS/SVG is used for the heatmap component within `trophy_visualizations`. This matches the original recommendation.

---

## Resolved: Gamification Surfaces

The original catalog reserved three modules (XP & Level Progress, P.L.A.T.I.N.U.M. Stats, Leaderboard Position) for after the gamification system shipped. Gamification has shipped, and the data ended up surfaced through existing modules rather than as standalone tiles:

- **XP / level progress** is in [`badge_xp_leaderboard`](#category-badges--achievements) (header section) and [`advanced_stats`](#category-premium-exclusive)
- **P.L.A.T.I.N.U.M. stats** are in [`my_stats_teaser`](#category-at-a-glance) and the [`/my-stats/`](stats-page.md) page
- **Leaderboard position** is in [`badge_xp_leaderboard`](#category-badges--achievements) and [`country_xp_leaderboard`](#category-badges--achievements)

No standalone gamification modules are planned. If a future feature needs one, design it from scratch against the current `ProfileGamification` shape rather than reviving the F1-F3 sketches.

---

## Priority Tiers

### Tier 1: Core (COMPLETE)

| # | Module | Why |
|---|--------|-----|
| 1 | Trophy Snapshot | Anchor module. Zero-cost, universally relevant. |
| 6 | Challenge Hub | Flagship feature. Smart CTAs for all users. |
| 12 | Badge Progress | THE selling point. "Almost there" drives engagement. |
| 2 | Recent Platinums | Latest conquests front and center. |

### Tier 2: Engagement (COMPLETE)

| # | Module | Why |
|---|--------|-----|
| 13 | Recent Badges | Celebration feed reinforces the badge loop. |
| 3 | Recent Activity | Live feed makes dashboard feel alive. |
| 22 | Monthly Recap Preview | Builds anticipation, shows momentum. |
| 29 | Quick Settings | Quality-of-life, makes dashboard the true home base. |

### Tier 3A: Depth (COMPLETE)

| # | Module | Why |
|---|--------|-----|
| 14 | Badge Stats | Collection analytics: tier breakdown, rarest badge, completion rate. |
| 15 | Badge XP & Leaderboard | Competition drives engagement. |
| 7 | A-Z Challenge Progress | Focused view for participants. |
| 8 | Calendar Challenge Progress | Full 12-month view with navigation. |
| 9 | Genre Challenge Progress | Focused view for participants. |

### Tier 3B: Depth (COMPLETE, 1 cut)

| # | Module | Status |
|---|--------|--------|
| 10 | Milestone Tracker | Done: progress is motivating |
| 11 | Completion Milestones | Done: "almost there" games are actionable |
| 16 | My Reviews | Done: engagement-focused (weekly vote feed) |
| 19 | My Checklists | Cut: may revisit later |

### Tier 4: Polish (COMPLETE, 6 cut)

| # | Module | Status |
|---|--------|--------|
| 18 | Rate My Games | Done: ticker strip CTA with hover effects |
| 21 | Trophy Timeline | Cut: low utility |
| 23 | Rarity Showcase | Done: 2-col grid with trophy + game icons |
| 4 | Sync Status | Cut: redundant with sync hotbar |
| 5 | Unread Notifications | Cut: redundant with navbar bell |
| 17 | Community Spotlight | Cut: dashboard should focus on individual user |
| 20 | My Game Lists | Cut: may revisit later |
| 24 | Quick Links | Cut: navbar covers navigation |
| 25 | Featured Content | Cut: better suited for homepage |

### Premium Visualization Tier (COMPLETE)

These are the crown jewels that make premium feel indispensable. The original sketches (Platinum Heatmap, Rarity Radar, Personal Records, Year in Review, Trophy Type Breakdown) shipped as **components inside the combined `trophy_visualizations` module** (#30-34) rather than as standalone tiles. Personal Records ended up on the dedicated `/my-stats/` page.

| # | Module | Why | Lives In |
|---|--------|-----|----------|
| 30-34 | Trophy Visualizations | The combined visualization suite. Heatmap + radar + cumulative + stacked bar. | Standalone module |
| 26 | Advanced Stats | Deep analytics for power users. | Standalone module |
| 27 | Theme Picker | Premium identity. Renamed from "Profile Theme Preview". | Standalone module |
| 28 | Roadmaps for Your Library | Surfaces staff-authored platinum guides for the user's unplatted library. Was originally listed as a future module blocked by a "standalone Roadmap feature"; the Roadmap system shipped, so this module shipped with it. | Standalone module (free, not premium) |
| 32 | Personal Records | Gamifies your history. | Off-dashboard at `/my-stats/` |
| 35 | Advanced Badge Stats | Velocity, depth, XP breakdowns. Shipped as a separate premium module after the original catalog was written. | Standalone module |
| 36a/b | Badge Series Overview / Badge Visualizations | Stage-completion timeline and series radar. New since the original catalog. | Two standalone modules |
| 43-45 | Genre & Themes / Time-to-Beat / Profile Showcase Editor | IGDB-driven additions to the premium tier. New since the original catalog. | Standalone modules |

---

## Customize Panel Categories (System Tabs)

| Slug | Display Name | Modules |
|------|-------------|---------|
| `premium` | Premium | Advanced Stats, Theme Picker, Trophy Visualizations, Advanced Badge Stats, Badge Series Overview, Badge Visualizations, Genre & Themes, Time-to-Beat, Profile Showcase Editor |
| `at_a_glance` | At a Glance | Trophy Snapshot, Recent Activity, Recent Platinums, Your Stats (teaser), Diversity Score, Quick Settings |
| `progress` | Progress & Challenges | Challenge Hub, Almost There, Milestone Tracker, Roadmaps for Your Library, A-Z Challenge, Platinum Calendar, Genre Challenge, VR Trophy Hunter |
| `badges` | Badges & Achievements | Badge Progress, Recent Badges, Badge Stats, Badge XP & Leaderboard, Country XP Leaderboard |
| `highlights` | Highlights & History | Monthly Recap Preview, Library Health, Top Studios, Earned Titles, Rarity Showcase, My Reviews, Rate My Games |
| `share` | Share & Export | Profile Card, Latest Platinum, Recap Card, Platinum Grid CTA, Challenge Cards, Badge Showcase |

The 6 system tabs are immutable. Premium users can additionally create up to 6 custom tabs and move modules between any tabs. See [Dashboard](../features/dashboard.md#tab-system) for the live tab system reference.

## Gotchas and Pitfalls

- **Module catalog lives here, not in the plan file.** The plan file gets overwritten during planning sessions. This doc is the authoritative catalog.
- **New modules must follow the established pattern:** provider in `dashboard_service.py`, template in `partials/dashboard/`, register in `DASHBOARD_MODULES`, add `configurable_settings` if applicable.
- **Cache invalidation:** Any new module that reads data affected by user actions must have invalidation hooks at the mutation points. See `docs/features/dashboard.md` for the current invalidation map.
- **Performance first:** All lazy modules must have `cache_ttl > 0`. Use `invalidate_dashboard_cache()` at data mutation points rather than disabling caching.
- **Chart.js lazy loading:** Only load Chart.js when a visualization module's tab is first activated. Do not include it in the global JS bundle.
- **Visualization cache TTLs:** Premium visualization modules query large datasets. Use high cache TTLs (30m-1hr) and invalidate on sync completion.
- **Free vs premium visualizations:** Simple CSS/SVG visualizations (sparklines, grids) can be free. Complex Chart.js visualizations (radar, line, stacked area) are premium.
