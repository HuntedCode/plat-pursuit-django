# Dashboard Module Catalog

The dashboard serves as the index page for all logged-in users. Modules are organized into a **tabbed navigation system** with 6 immutable system tabs and up to 6 user-created custom tabs (premium). Each module belongs to a default category tab. See [Dashboard System](../features/dashboard.md) for full architecture docs.

**Design principles:**
- **Performance first:** Always cache, always invalidate properly. This is the production index page.
- **Badges are the selling point:** Maximum badge presence across modules.
- **Smart CTAs:** Modules show actionable prompts when the user hasn't started (never blank cards).
- **Premium as upgrade path:** Settings, reorder, and premium-exclusive modules drive conversions.
- **Visualizations sell premium:** Rich data visualizations are the primary premium differentiator.
- **Gamification deferred:** Hold off on gamification modules until the system goes live.

## Module Status

| Status | Count |
|--------|-------|
| Complete (Tier 1-4) | 19 |
| Cut (low value) | 7 |
| Premium Exclusive | 9 |
| Future (Gamification) | 3 |
| **Total** | **38** |

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

## Category: Badges & Achievements

Badges are a flagship feature with dedicated real estate.

| # | Module | Strategy | Status | Data Source |
|---|--------|----------|--------|------------|
| 12 | Badge Progress | Lazy (10m) | **Done** | UserBadgeProgress, prerequisite filtering |
| 13 | Recent Badges | Lazy (10m) | **Done** | UserBadge (earned_at desc) |
| 14 | Badge Stats | Lazy (10m) | **Done** | UserBadge tier breakdown, rarest badge, collection rate |
| 15 | Badge XP & Leaderboard | Lazy (10m) | **Done** | ProfileGamification + leaderboard neighborhood (2 above/below) |
| 15b | Country XP Leaderboard | Lazy (10m) | **Done** | Per-country Redis sorted sets + leaderboard neighborhood |

## Category: Community

| # | Module | Strategy | Status | Data Source |
|---|--------|----------|--------|------------|
| 16 | My Reviews | Lazy (10m) | **Done** | Review aggregate stats + recent list |
| 17 | Community Spotlight | Lazy | **Cut** | Dashboard should focus on individual user, not community |
| 18 | Rate My Games | Lazy (30m) | **Done** | CTA card: unrated platinum count + preview strip |
| 19 | My Checklists | Lazy | **Cut** | May revisit later |
| 20 | My Game Lists | Lazy | **Cut** | May revisit later |

## Category: Highlights & History

| # | Module | Strategy | Status | Data Source |
|---|--------|----------|--------|------------|
| 21 | Trophy Timeline | Lazy (60m) | **Done** | timeline_service.build_timeline_events, high cache |
| 22 | Monthly Recap Preview | Lazy (30m) | **Done** | MonthlyRecap (most recent finalized) |
| 23 | Rarity Showcase | Lazy (10m) | **Done** | EarnedTrophy sorted by earn_rate, rarity_color_hex |

## Category: Utility

| # | Module | Strategy | Status | Data Source |
|---|--------|----------|--------|------------|
| 24 | Quick Links | Server | **Cut** | Low value: navbar covers navigation |
| 25 | Featured Content | Lazy | **Cut** | Better suited for homepage than dashboard |

## Category: Premium Exclusive

The premium dashboard should feel indispensable. Visualization modules are the primary conversion drivers.

| # | Module | Strategy | Status | Data Source |
|---|--------|----------|--------|------------|
| 26 | Advanced Stats | Lazy | Planned | Aggregated EarnedTrophy (expensive) |
| 27 | Profile Theme Preview | Server | Planned | Profile (selected_background, selected_theme) |
| 28 | Trophy Roadmap | Lazy | Future | Requires standalone Roadmap feature first |
| 30 | Platinum Heatmap | Lazy | Planned | EarnedTrophy (platinum earns by date) |
| 31 | Rarity Radar | Lazy | Planned | EarnedTrophy + Trophy.trophy_earn_rate |
| 32 | Personal Records | Lazy | Planned | Aggregated EarnedTrophy (records/milestones) |
| 33 | Year in Review | Lazy | Planned | EarnedTrophy + ProfileGame (year comparison) |
| 34 | Trophy Type Breakdown | Lazy | Planned | EarnedTrophy (type counts by month) |

### Visualization Module Details

#### Platinum Heatmap (#30)
- GitHub-style contribution grid for platinum earns
- Each cell = one day, color intensity = platinums earned that day
- Shows streaks, dry spells, and seasonal patterns
- Covers current year (365 cells) with month labels
- Data: `EarnedTrophy.objects.filter(trophy__trophy_type='platinum', earned=True)` grouped by `earned_date_time` date
- Rendering: Pure CSS/SVG grid (no chart library needed). Each cell is a small colored div.
- Cache: High TTL (30m+), invalidate on sync
- Premium: **Yes**

#### Rarity Radar (#31)
- Spider/radar chart showing the user's collection by rarity tier
- Axes: Ultra Rare (<5%), Very Rare (5-10%), Rare (10-20%), Uncommon (20-50%), Common (>50%)
- Shows what kind of hunter you are: "rarity chaser" vs "casual completionist"
- Data: `EarnedTrophy` joined with `Trophy.trophy_earn_rate`, bucketed by rarity threshold
- Rendering: Chart.js radar chart or pure SVG
- Cache: High TTL (30m+), invalidate on sync
- Premium: **Yes**

#### Personal Records (#32)
- Visual cards showing the user's personal trophy hunting records
- Records: Fastest platinum (fewest days from first trophy to plat), Most platinums in a month, Longest daily streak, Rarest platinum earned, Most trophies in a single day
- Each record shows the value, the game/date, and a small icon
- Data: Aggregated from `EarnedTrophy` with date math
- Rendering: Card grid, no chart library needed
- Cache: High TTL (1hr), invalidate on sync
- Premium: **Yes**

#### Year in Review (#33)
- Line chart comparing this year vs last year
- Metrics: Cumulative platinums, total trophies, games completed
- Shows growth trajectory and pacing
- Data: `EarnedTrophy` grouped by month for current and previous year
- Rendering: Chart.js line chart or pure SVG sparkline
- Cache: High TTL (1hr), invalidate on sync
- Premium: **Yes**

#### Trophy Type Breakdown Over Time (#34)
- Stacked area chart showing bronze/silver/gold/platinum earns by month
- Reveals trends: "Am I earning more platinums over time?" "Am I becoming more of a completionist?"
- Data: `EarnedTrophy` grouped by `trophy__trophy_type` and month
- Rendering: Chart.js stacked area chart
- Cache: High TTL (1hr), invalidate on sync
- Premium: **Yes**

### Free Visualization Modules

These could be offered to all users as simpler, non-chart visualizations:

#### Trophy Earn Rate Timeline (part of Trophy Snapshot or standalone)
- Simple sparkline showing trophies earned per week over the last 3 months
- Rendering: Pure CSS/SVG sparkline (no chart library)
- Could be integrated into the existing Trophy Snapshot module rather than a standalone module

#### Genre Breakdown (part of Advanced Stats or standalone)
- Donut/pie chart of platinums by genre
- "You're 40% Action, 25% RPG, 20% Adventure..."
- Data: `EarnedTrophy` joined with `Concept.genres`
- Could be free as a simpler version, with detailed drilldowns premium-only

#### Completion Distribution (part of Advanced Stats or standalone)
- Histogram of games by completion percentage (0-25%, 25-50%, 50-75%, 75-100%)
- Shows backlog shape at a glance
- Data: `ProfileGame.progress` bucketed
- Simple enough to be free

### Chart Library Considerations

For visualization modules, two approaches:

1. **Chart.js** (~60KB gzipped, Canvas-based): Best for radar charts, line charts, stacked area charts. Zero dependencies. Load only on pages/tabs that need it (lazy script load).

2. **Pure CSS/SVG**: Best for heatmaps, sparklines, simple grids. No library overhead. Better for server-rendered modules.

**Recommendation:** Use pure CSS/SVG for simpler visualizations (heatmap, sparklines, card-based records). Use Chart.js only for complex charts (radar, line, stacked area). Load Chart.js lazily only when a premium visualization tab is activated.

---

## Future: Pending Gamification Launch

| # | Module | Blocked By |
|---|--------|------------|
| F1 | XP & Level Progress | Gamification system launch |
| F2 | P.L.A.T.I.N.U.M. Stats | Gamification system launch |
| F3 | Leaderboard Position | Gamification system launch |

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
| 21 | Trophy Timeline | Done: horizontal scrollable with colored dots |
| 23 | Rarity Showcase | Done: 2-col grid with trophy + game icons |
| 4 | Sync Status | Cut: redundant with sync hotbar |
| 5 | Unread Notifications | Cut: redundant with navbar bell |
| 17 | Community Spotlight | Cut: dashboard should focus on individual user |
| 20 | My Game Lists | Cut: may revisit later |
| 24 | Quick Links | Cut: navbar covers navigation |
| 25 | Featured Content | Cut: better suited for homepage |

### Premium Visualization Tier

These are the crown jewels that make premium feel indispensable.

| # | Module | Why |
|---|--------|-----|
| 30 | Platinum Heatmap | GitHub-style grid. Visual, addictive, shareable. |
| 31 | Rarity Radar | Spider chart reveals your hunter identity. |
| 32 | Personal Records | Gamifies your history. "Can I beat my record?" |
| 33 | Year in Review | Growth trajectory. Motivational. |
| 34 | Trophy Type Breakdown | Trends over time. "Am I improving?" |
| 26 | Advanced Stats | Deep analytics for power users. |
| 27 | Profile Theme Preview | Premium identity. |
| 28 | Trophy Roadmap | Crown jewel (needs feature first). |

---

## Customize Panel Categories

| Slug | Display Name | Modules |
|------|-------------|---------|
| `premium` | Premium | Advanced Stats, Theme Preview, Trophy Roadmap, Platinum Heatmap, Rarity Radar, Personal Records, Year in Review, Trophy Type Breakdown |
| `at_a_glance` | At a Glance | Trophy Snapshot, Recent Platinums, Recent Activity, Sync Status, Notifications, Quick Settings |
| `progress` | Progress & Challenges | Challenge Hub, A-Z, Calendar, Genre, Milestones, Completion Milestones |
| `badges` | Badges & Achievements | Badge Progress, Recent Badges, Badge Showcase, Badge XP & Leaderboard, Country XP Leaderboard |
| `community` | Community | My Reviews, Community Spotlight, Rate My Games, My Checklists, My Game Lists |
| `highlights` | Highlights & History | Trophy Timeline, Monthly Recap Preview, Rarity Showcase |

Utility modules (Quick Links, Featured Content) fold into At a Glance or Highlights.

## Gotchas and Pitfalls

- **Module catalog lives here, not in the plan file.** The plan file gets overwritten during planning sessions. This doc is the authoritative catalog.
- **New modules must follow the established pattern:** provider in `dashboard_service.py`, template in `partials/dashboard/`, register in `DASHBOARD_MODULES`, add `configurable_settings` if applicable.
- **Cache invalidation:** Any new module that reads data affected by user actions must have invalidation hooks at the mutation points. See `docs/features/dashboard.md` for the current invalidation map.
- **Performance first:** All lazy modules must have `cache_ttl > 0`. Use `invalidate_dashboard_cache()` at data mutation points rather than disabling caching.
- **Chart.js lazy loading:** Only load Chart.js when a visualization module's tab is first activated. Do not include it in the global JS bundle.
- **Visualization cache TTLs:** Premium visualization modules query large datasets. Use high cache TTLs (30m-1hr) and invalidate on sync completion.
- **Free vs premium visualizations:** Simple CSS/SVG visualizations (sparklines, grids) can be free. Complex Chart.js visualizations (radar, line, stacked area) are premium.
