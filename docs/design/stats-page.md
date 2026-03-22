# My Stats Page - Design Document

> Premium-only dedicated page at `/my-stats/`. Video game stats screen aesthetic showing every possible stat about the user's trophy hunting career.

## Motivation

The dashboard's Advanced Stats module provides a compact analytics overview, but users want a deep-dive into their full trophy history. A dedicated stats page delivers the "wow" factor that sells premium subscriptions, similar to end-of-campaign stats screens in games like Uncharted or Destiny.

**Why a page, not a dashboard module/tab:**
- No module card constraints: full-width sections, multi-column layouts, visual flourishes
- Server-rendered for instant load (no lazy-load AJAX)
- Discoverable outside the dashboard (navbar link, profile tab)
- Shareable URL potential for future social features
- Heavy aggregation queries don't slow dashboard tab switching

## Visual Direction

- Dark inner background sections with subtle borders
- Stat rows: label left, value right, monospace numbers for game UI feel
- Section headers with themed icons
- Color-coded values (green for good streaks, gold for records, red for idle days)
- Compact but comprehensive: aim for "I could scroll through this for 10 minutes"

## Stat Sections

### 1. Career Overview
All denormalized on Profile (0 queries).

| Stat | Source | Cost |
|------|--------|------|
| Total trophies earned | `Profile.total_trophies` | FREE |
| Bronze / Silver / Gold / Platinum counts | `Profile.total_bronzes/silvers/golds/plats` | FREE |
| Trophy type distribution (%) | Derived from counts | FREE |
| Total games played | `Profile.total_games` | FREE |
| Games 100% completed | `Profile.total_completes` | FREE |
| Average completion % | `Profile.avg_progress` | FREE |
| Account age (days) | `now() - Profile.created_at` | FREE |
| PSN level + tier | `Profile.trophy_level`, `Profile.tier` | FREE |

### 2. Speed Records
Mix of denormalized and cheap queries.

| Stat | Source | Cost |
|------|--------|------|
| First trophy earned (date + game + trophy) | `EarnedTrophy.order_by('earned_date_time').first()` | CHEAP |
| First platinum (date + game) | `EarnedTrophy.filter(type='platinum').order_by('earned_date_time').first()` | CHEAP |
| Fastest platinum (play_duration + game) | `ProfileGame.filter(has_plat=True).order_by('play_duration').first()` | CHEAP |
| Slowest platinum (play_duration + game) | `ProfileGame.filter(has_plat=True).order_by('-play_duration').first()` | CHEAP |
| Most trophies in a single day (count + date) | `TruncDate` aggregate on EarnedTrophy | CHEAP |
| Most platinums in a single month | `max(MonthlyRecap.platinums_earned)` | CHEAP |
| Most prolific month (total trophies) | `max(MonthlyRecap.total_trophies_earned)` | CHEAP |
| Best week (7-day sliding window) | Sliding window over daily counts | MODERATE |

### 3. Rarity Records

| Stat | Source | Cost |
|------|--------|------|
| Rarest trophy (earn rate + name + game) | `EarnedTrophy.order_by('trophy__trophy_earn_rate').first()` | CHEAP |
| Rarest platinum | `Profile.rarest_plat` FK | FREE |
| Most common trophy earned | `EarnedTrophy.order_by('-trophy__trophy_earn_rate').first()` | CHEAP |
| Average earn rate of all trophies | `Avg('trophy__trophy_earn_rate')` | CHEAP |
| Ultra Rare count (<5%) | `Count` with Q filter | CHEAP |
| Rarity tier distribution | Case/When aggregate | CHEAP |

### 4. Streaks & Consistency

| Stat | Source | Cost |
|------|--------|------|
| Longest all-time streak (consecutive days) | Distinct earned dates, consecutive run algorithm | MODERATE |
| Current streak | Same algorithm, check if last date is recent | MODERATE |
| Total active days (distinct days with trophies) | `TruncDate` + `distinct().count()` | CHEAP |
| Days since last trophy | `now() - max(earned_date_time)` | CHEAP |
| Months with activity | `Count(MonthlyRecap)` where `total_trophies_earned > 0` | CHEAP |
| Active days ratio | `active_days / account_age_days` | FREE (derived) |

### 5. Time Patterns (Timezone-Adjusted)

| Stat | Source | Cost |
|------|--------|------|
| Time of day distribution (Morning/Afternoon/Evening/Night) | Python-side bucketing of `earned_date_time` | MODERATE |
| Peak hunting hour | Most common hour from timestamps | MODERATE |
| Day of week distribution (Mon-Sun) | Python-side weekday extraction | MODERATE |
| Weekend vs weekday ratio | Derived from day-of-week counts | FREE (derived) |

### 6. Platform Breakdown

| Stat | Source | Cost |
|------|--------|------|
| Trophies by platform (PS5/PS4/PS3/Vita) | `Game.title_platform` JSONField unnest | MODERATE |
| Games by platform | Same source, count distinct games | MODERATE |
| Platform with most platinums | Derived | MODERATE |

### 7. Badge & XP Stats
Mostly denormalized on ProfileGamification (0-1 queries).

| Stat | Source | Cost |
|------|--------|------|
| Total badge XP | `ProfileGamification.total_badge_xp` | FREE |
| Total badges earned | `ProfileGamification.total_badges_earned` | FREE |
| Unique badge series | `ProfileGamification.unique_badges_earned` | FREE |
| Top 5 series by XP | Sort `series_badge_xp` JSON | FREE |
| Average XP per badge | `total_badge_xp / total_badges_earned` | FREE (derived) |
| Highest tier earned | `UserBadge.order_by('-badge__tier').first()` | CHEAP |
| Most recent badge | `UserBadge.order_by('-earned_at').first()` | CHEAP |

### 8. Challenge Progress

| Stat | Source | Cost |
|------|--------|------|
| Active challenges count | `Challenge.filter(is_complete=False).count()` | CHEAP |
| Completed challenges count | `Challenge.filter(is_complete=True).count()` | CHEAP |
| A-Z progress (X/26) | `Challenge` slots query | CHEAP |
| Calendar progress (X/365) | Calendar days filled | CHEAP |
| Genre progress (X/total) | Genre slots query | CHEAP |

### 9. Community Contributions

| Stat | Source | Cost |
|------|--------|------|
| Reviews written | `Review.filter(profile=profile).count()` | CHEAP |
| Total helpful votes received | `Sum('helpful_count')` | CHEAP |
| Total funny votes received | `Sum('funny_count')` | CHEAP |
| Guides authored | `Checklist.filter(profile=profile, status='published').count()` | CHEAP |
| Most helpful review (game + count) | `Review.order_by('-helpful_count').first()` | CHEAP |

### 10. Special Achievements / Fun Tags
Computed badges based on stats thresholds. These are flavor, not data.

| Tag | Condition |
|-----|-----------|
| Speedrunner | Any platinum with play_duration < 1 day |
| Completionist | avg_progress > 95% |
| Rare Hunter | > 50% of platinums have < 10% earn rate |
| Marathon Runner | Longest streak > 30 days |
| Night Owl | > 50% of trophies earned between 12am-6am |
| Weekend Warrior | > 60% of trophies earned Sat/Sun |
| Badge Collector | unique_badges_earned > 20 |
| Platinum Machine | total_plats > 100 |

## Implementation Notes

### Query Strategy
- **Phase 1 (instant)**: Read all denormalized Profile + ProfileGamification fields (0 queries after select_related)
- **Phase 2 (cheap)**: Run 5-8 simple aggregates on EarnedTrophy and ProfileGame (indexed fields)
- **Phase 3 (moderate)**: Timestamp-based computations (time of day, streaks) run in Python over a single `values_list` fetch
- **Phase 4 (expensive, consider caching)**: All-time streak, leaderboard ranks/percentiles

### Caching
- Cache the entire page's data dict with 30-60 minute TTL
- Invalidate on sync completion (same hook as dashboard: `invalidate_dashboard_cache`)
- Consider a separate cache key: `stats_page:{profile_id}`

### Performance Budget
- Target: < 500ms server-side for all queries combined
- Most data is FREE (denormalized) or CHEAP (single indexed queries)
- The timestamp scan (time of day + day of week + streaks) is the heaviest operation
- For users with 10,000+ trophies, the timestamp scan may need optimization (monthly recap data can substitute for some stats)

### URL & Access
- Route: `/my-stats/` (or `/stats/`)
- View: `MyStatsView(LoginRequiredMixin, PremiumRequiredMixin, TemplateView)`
- Template: `templates/trophies/my_stats.html`
- Service: `trophies/services/stats_service.py` (new, dedicated service)

### Future Enhancements
- Share stats as an image (Playwright PNG, like share cards)
- Compare stats with friends
- Historical stats snapshots (track stat changes over time)
- Leaderboard integration (rank overlays on relevant stats)

## Gotchas and Pitfalls

- **Timezone**: All time-based stats must use the user's selected timezone, not UTC. Use `profile.user.user_timezone` with pytz conversion.
- **Null timestamps**: `EarnedTrophy.earned_date_time` can be null. Always filter with `earned_date_time__isnull=False`.
- **play_duration**: `ProfileGame.play_duration` is nullable (not all games report it). Stats using this field need "N/A" fallbacks.
- **Concept vs Game fields**: Use `Concept.unified_title` / `Concept.concept_icon_url` with fallback to `Game.title_name` / `Game.title_image`.
- **MonthlyRecap**: Only finalized months have reliable data. Current month may be partial.
- **Platform JSONField**: `Game.title_platform` is a list (e.g., `["PS5", "PS4"]`). Cross-buy games count toward all listed platforms.
