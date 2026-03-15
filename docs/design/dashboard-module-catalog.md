# Dashboard Module Catalog

The dashboard serves as the index page for all logged-in users. Modules are organized into a **tabbed navigation system** with 6 immutable system tabs and up to 6 user-created custom tabs (premium). Each module belongs to a default category tab. See [Dashboard System](../features/dashboard.md) for full architecture docs.

**Design principles:**
- **Performance first:** Always cache, always invalidate properly. This is the production index page.
- **Badges are the selling point:** Maximum badge presence across modules.
- **Smart CTAs:** Modules show actionable prompts when the user hasn't started (never blank cards).
- **Premium as upgrade path:** Settings, reorder, and premium-exclusive modules drive conversions.
- **Gamification deferred:** Hold off on gamification modules until the system goes live.

## Module Status

| Status | Count |
|--------|-------|
| Complete (Tier 1) | 4 |
| Planned (Tier 2-4) | 21 |
| Premium Exclusive | 3 |
| Future (Gamification) | 3 |
| **Total** | **31** |

---

## Category: At a Glance

| # | Module | Strategy | Status | Data Source |
|---|--------|----------|--------|------------|
| 1 | Trophy Snapshot | Server | **Done** | Profile (denormalized, 0 queries) |
| 2 | Recent Platinums | Lazy (5m) | **Done** | EarnedTrophy + Trophy + Game + Concept |
| 3 | Recent Activity | Lazy | Planned | EarnedTrophy, UserBadge, Review, Challenge |
| 4 | Sync Status | Server | Planned | Profile (sync fields) |
| 5 | Unread Notifications | Lazy | Planned | Notification model |

## Category: Progress & Challenges

All challenge modules show smart CTAs when no active challenge exists.

| # | Module | Strategy | Status | Data Source |
|---|--------|----------|--------|------------|
| 6 | Challenge Hub | Lazy (5m) | **Done** | Challenge + slot models, mini previews |
| 7 | A-Z Challenge Progress | Lazy | Planned | AZChallengeSlot (focused view) |
| 8 | Calendar Challenge Progress | Lazy | Planned | CalendarChallengeDay (full 12-month) |
| 9 | Genre Challenge Progress | Lazy | Planned | GenreChallengeSlot + bonus slots |
| 10 | Milestone Tracker | Lazy | Planned | UserMilestoneProgress, Milestone |
| 11 | Completion Milestones | Lazy | Planned | ProfileGame (100% + 90%+ games) |

## Category: Badges & Achievements

Badges are a flagship feature with dedicated real estate.

| # | Module | Strategy | Status | Data Source |
|---|--------|----------|--------|------------|
| 12 | Badge Progress | Lazy (10m) | **Done** | UserBadgeProgress, prerequisite filtering |
| 13 | Recent Badges | Lazy | Planned | UserBadge (earned_at desc) |
| 14 | Badge Showcase | Lazy | Planned | UserBadge grouped by tier |
| 15 | Badge XP & Leaderboard | Lazy | Planned | ProfileGamification + leaderboard_service |

## Category: Community

| # | Module | Strategy | Status | Data Source |
|---|--------|----------|--------|------------|
| 16 | My Reviews | Lazy | Planned | Review model |
| 17 | Community Spotlight | Lazy | Planned | ReviewHubService (shared cache) |
| 18 | Rate My Games | Lazy | Planned | ProfileGame LEFT JOIN UserConceptRating |
| 19 | My Checklists | Lazy | Planned | Checklist + UserChecklistProgress |
| 20 | My Game Lists | Lazy | Planned | GameList + GameListItem |

## Category: Highlights & History

| # | Module | Strategy | Status | Data Source |
|---|--------|----------|--------|------------|
| 21 | Trophy Timeline | Lazy | Planned | timeline_service (expensive, high cache) |
| 22 | Monthly Recap Preview | Lazy | Planned | MonthlyRecap + current month aggregation |
| 23 | Rarity Showcase | Lazy | Planned | EarnedTrophy + Trophy.rarity |

## Category: Utility

| # | Module | Strategy | Status | Data Source |
|---|--------|----------|--------|------------|
| 24 | Quick Links | Server | Planned | Static links + light count queries |
| 25 | Featured Content | Lazy | Planned | FeaturedGame, FeaturedProfile, FeaturedGuide |

## Category: Premium Exclusive

The premium dashboard should feel indispensable.

| # | Module | Strategy | Status | Data Source |
|---|--------|----------|--------|------------|
| 26 | Advanced Stats | Lazy | Planned | Aggregated EarnedTrophy (expensive) |
| 27 | Profile Theme Preview | Server | Planned | Profile (selected_background, selected_theme) |
| 28 | Trophy Roadmap | Lazy | Future | Requires standalone Roadmap feature first |

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

### Tier 2: Engagement (Next)

| # | Module | Why |
|---|--------|-----|
| 13 | Recent Badges | Celebration feed reinforces the badge loop. |
| 8 | Calendar Challenge | Visual calendar drives daily engagement. |
| 3 | Recent Activity | Live feed makes dashboard feel alive. |
| 22 | Monthly Recap Preview | Builds anticipation, shows momentum. |

### Tier 3: Depth

| # | Module | Why |
|---|--------|-----|
| 14 | Badge Showcase | "Look what I've earned." |
| 15 | Badge XP & Leaderboard | Competition drives engagement. |
| 7 | A-Z Challenge Progress | Focused view for participants. |
| 9 | Genre Challenge Progress | Focused view for participants. |
| 10 | Milestone Tracker | Progress is motivating. |
| 11 | Completion Milestones | "Almost there" games are actionable. |
| 16 | My Reviews | Community hub connection. |
| 19 | My Checklists | Checklist participation. |

### Tier 4: Polish

| # | Module | Why |
|---|--------|-----|
| 4 | Sync Status | Utility widget. |
| 5 | Unread Notifications | Notification system. |
| 17 | Community Spotlight | Discovery. |
| 18 | Rate My Games | Engagement prompt. |
| 20 | My Game Lists | List creators. |
| 21 | Trophy Timeline | Beautiful but expensive. |
| 23 | Rarity Showcase | Bragging rights. |
| 24 | Quick Links | Utility. |
| 25 | Featured Content | Staff-curated. |

### Premium Tier

| # | Module | Why |
|---|--------|-----|
| 26 | Advanced Stats | Power user analytics. |
| 27 | Profile Theme Preview | Premium identity. |
| 28 | Trophy Roadmap | Crown jewel (needs feature first). |

---

## Customize Panel Categories

| Slug | Display Name | Modules |
|------|-------------|---------|
| `at_a_glance` | At a Glance | Trophy Snapshot, Recent Platinums, Recent Activity, Sync Status, Notifications |
| `progress` | Progress & Challenges | Challenge Hub, A-Z, Calendar, Genre, Milestones, Completion Milestones |
| `badges` | Badges & Achievements | Badge Progress, Recent Badges, Badge Showcase, Badge XP & Leaderboard |
| `community` | Community | My Reviews, Community Spotlight, Rate My Games, My Checklists, My Game Lists |
| `highlights` | Highlights & History | Trophy Timeline, Monthly Recap Preview, Rarity Showcase |
| `premium` | Premium | Advanced Stats, Theme Preview, Trophy Roadmap |

Utility modules (Quick Links, Featured Content) fold into At a Glance or Highlights.

## Gotchas and Pitfalls

- **Module catalog lives here, not in the plan file.** The plan file (`squishy-munching-octopus.md`) gets overwritten during planning sessions. This doc is the authoritative catalog.
- **New modules must follow the established pattern:** provider in `dashboard_service.py`, template in `partials/dashboard/`, register in `DASHBOARD_MODULES`, add `configurable_settings` if applicable.
- **Cache invalidation:** Any new module that reads data affected by user actions must have invalidation hooks at the mutation points. See `docs/features/dashboard.md` for the current invalidation map.
- **Performance first:** All lazy modules must have `cache_ttl > 0`. Use `invalidate_dashboard_cache()` at data mutation points rather than disabling caching.
