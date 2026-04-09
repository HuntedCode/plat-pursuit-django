# My Pursuit Hub

The My Pursuit Hub is PlatPursuit's personal-progression and recognition destination at `/my-pursuit/`. It is one of the four top-level hubs in the [hub-of-hubs IA](../architecture/ia-and-subnav.md) (Dashboard / Browse / Community / My Pursuit). Today its sub-nav contains Badges, Milestones, and Titles. After the gamification initiative ships, the same hub absorbs the full RPG layer (Logbook, Quests, Star Chart, Arcade, Stellar Market) without requiring another IA shuffle.

> **Status**: planned. The hub does not yet exist as a route. Phase 10a of the Community Hub initiative renames `/achievements/*` URLs to `/my-pursuit/*` and registers the hub URL. Phase 10b builds the landing page described in this doc.

## Why "My Pursuit"

The original navbar had a 4-menu structure: Browse / Community / Achievements / My Pursuit. "Achievements" held Badges, Milestones, Titles. "My Pursuit" held the personal-utility menu (Customization, Recap, My Lists, My Challenges, My Profile, My Shareables, My Stats, Platinum Grid). Both menus had problems:

- **"Achievements" was undersized** (3 items) and undersold the section's potential. The next major initiative — gamification — adds an entire RPG layer (P.L.A.T.I.N.U.M. stats, 25 Jobs, dual leveling, Quests, Stellar Marks currency, Stellar Market store, Mini-game Arcade, Star Chart constellation map, customizable avatar frames) that conceptually belongs in the same section. Renaming "Achievements" to something forward-compatible avoids a second IA pivot when gamification ships.
- **"My Pursuit" as a menu was duplication.** Every personal-utility item in that menu had a natural home on the dashboard or under a hub sub-nav. Keeping the menu meant the dashboard's job (the personal cockpit) was being done by both the dashboard and the menu, which is exactly the kind of redundancy the hub-of-hubs IA is trying to eliminate.

The cleanest resolution: **kill "My Pursuit" as a personal-utility menu, and reuse the name for the renamed Achievements hub.** The personal-utility items (Stats, Shareables, Recap) relocate to the Dashboard hub's sub-nav, where they always belonged. The "My Pursuit" name elevates to the personal-progression hub it always *should* have meant. This:

- Capitalizes on existing brand equity ("My Pursuit" is already in the user vocabulary)
- Survives the gamification expansion ("My Pursuit > Quests" reads naturally; "Achievements > Quests" does not)
- Avoids introducing new vocabulary
- Frees the personal-utility pages to live in the right place (Dashboard sub-nav)

The cost is a one-time relearning ("the menu items moved"), mitigated by 301 redirects on every legacy URL and a one-time "we've reorganized" callout banner shown to authenticated users for 30 days post-launch.

## Architecture Overview

`MyPursuitHubView` lives in `core/views.py` next to `HomeView` and `CommunityHubView` for symmetry. It is a `TemplateView` that supports both anonymous and authenticated users — the page renders a marketing variant for guests and a personalized variant for authenticated users (with progress overview, recent unlocks, and near-completion badges).

The page-data assembler is `core/services/my_pursuit_hub_service.py`, which orchestrates calls to the existing badge / milestone / title services and assembles a single context dict.

The hub URL `/my-pursuit/` is the canonical landing page. The sub-pages — `/my-pursuit/badges/`, `/my-pursuit/milestones/`, `/my-pursuit/titles/` — are the existing badge / milestone / title list views, just relocated under the new prefix. Phase 10a renames the URLs and adds 301 redirects from the legacy `/badges/`, `/milestones/`, `/my-titles/` paths.

## File Map

| File | Purpose |
|------|---------|
| `core/views.py` | `MyPursuitHubView` (TemplateView; supports anonymous + authenticated rendering) |
| `core/services/my_pursuit_hub_service.py` | Page-data assembler: progress overview, recent unlocks, near-completion |
| `templates/my_pursuit/hub.html` | Hub landing page template |
| `templates/my_pursuit/partials/progress_overview.html` | Hero card with badge / milestone / title counts |
| `templates/my_pursuit/partials/recent_unlocks.html` | Horizontal scroll of last N unlocks |
| `templates/my_pursuit/partials/near_completion.html` | Badges where the user is >75% complete |
| `templates/community/partials/feature_card.html` | Reused parameterized feature card (shared with Community Hub) |
| `plat_pursuit/urls.py` | Routes: `/my-pursuit/` (my_pursuit_hub) + renamed sub-page routes |
| `core/sitemaps.py` | Entry in `StaticViewSitemap.items()` at priority 0.7 |

## Page Anatomy

The hub renders these modules in order, top to bottom. Width: full site container, matching the Community Hub and Review Hub.

### 1. Hero — Progress Overview

The hero card sets the tone: **"this is your hunt."** Four stat cards in a row showing:

| Stat | Source |
|------|--------|
| Badges Earned | `ProfileGamification.total_badges_earned` |
| Badges In Progress | Count of `UserBadgeProgress` rows where the user has progress but hasn't earned the badge |
| Milestones Hit | Count of `UserMilestone` rows |
| Titles Unlocked | Count of titles the user has access to (via badges + milestones + easter eggs) |

**Logged-out variant**: marketing copy + "Sign in to see your hunt." CTA. The four stat cards become explanation cards ("Badges: long-form trophy hunting goals." "Milestones: lifetime achievements." etc.).

### 2. Recent Unlocks *(authenticated only)*

Horizontal scroll of the last 6 badges/milestones/titles unlocked, mixed and sorted by recency. Each card shows the badge/milestone/title icon, name, and date earned. Click-through goes to the badge / milestone / title detail page.

Data source: `Event.objects.filter(profile=request.user.profile, event_type__in=['badge_earned', 'milestone_hit'])` ordered by `-occurred_at`. Note: `badge_earned` events are bulk-coalesced (one per sync) so the metadata may contain multiple badges; the recent-unlocks renderer expands the metadata into individual cards.

### 3. Near Completion *(authenticated only)*

Six badges where the user is closest to earning. Sorted by completion percentage descending, filtered to >75% complete and not yet earned. Each card shows badge icon, series name, current tier progress, completion bar, and "X of Y stages" text.

Data source: `UserBadgeProgress` rows where `completed_concepts / max_progress > 0.75` and the badge has not been earned at the displayed tier. The query needs to handle multi-tier badges correctly — show progress toward the *next* tier, not the highest unlocked one.

### 4. Feature Cards *(always shown)*

A 3-card grid (today) that grows to a richer grid (after gamification) showing each sub-page of the hub. Reuses the `feature_card.html` partial from the Community Hub for visual consistency.

**Today's cards** (3, 1-col on mobile, 3-col on `lg:`):

| Card | Tagline | CTA |
|------|---------|-----|
| **Badges** | "Long-form trophy hunting goals across genres, themes, and challenges." | "Browse Badges →" |
| **Milestones** | "Lifetime achievements that mark your trophy hunting journey." | "View Milestones →" |
| **Titles** | "Earned recognition. Wear your accomplishments." | "View Titles →" |

**After gamification ships** (8, with visual grouping):

The same grid grows to absorb the gamification features. Visual grouping (e.g., section headers like "Recognition" / "Identity" / "Activities") keeps the larger grid scannable.

| Section | Cards |
|---------|-------|
| Recognition | Badges, Milestones, Titles |
| Identity | Logbook (Hunter Profile / Explorer's Logbook), Star Chart |
| Activities | Quests, Arcade, Stellar Market |

The expansion is purely additive — Phase 10b ships the 3-card version, the gamification initiative adds the rest.

### 5. Tier Overview *(authenticated only, optional)*

Existing badge tier breakdown (Bronze / Silver / Gold / Platinum counts), pulled from `ProfileGamification.series_badge_xp` or computed live from `UserBadge` rows. Could ship as a separate card or be folded into the hero progress overview. Phase 10b decides based on visual fit during implementation.

## Sub-Pages (Sub-Nav Items)

The hub's sub-nav strip surfaces these pages on every `/my-pursuit/*` URL:

| Sub-nav Item | URL | View | Auth |
|--------------|-----|------|------|
| Hub | `/my-pursuit/` | `MyPursuitHubView` | Public |
| Badges | `/my-pursuit/badges/` | `BadgeListView` | Public |
| Milestones | `/my-pursuit/milestones/` | `MilestoneListView` | Public |
| Titles | `/my-pursuit/titles/` | `MyTitlesView` | Auth-required |

The Titles sub-nav item is conditionally rendered (only shown to authenticated users with a profile) because `MyTitlesView` uses `LoginRequiredMixin`.

## Forward Compatibility: Gamification

The hub is named and structured to absorb the gamification initiative without another IA shuffle. When gamification ships, the sub-nav grows to:

| Sub-nav Item | URL | Source |
|--------------|-----|--------|
| Hub | `/my-pursuit/` | Existing |
| Logbook | `/my-pursuit/logbook/` | New (Hunter Profile / Explorer's Logbook) |
| Star Chart | `/my-pursuit/star-chart/` | New (constellation progression map) |
| Quests | `/my-pursuit/quests/` | New (daily/weekly/epic quests) |
| Arcade | `/my-pursuit/arcade/` | Migration of `/arcade/stellar-circuit/` and future mini-games |
| Market | `/my-pursuit/market/` | New (Stellar Market store) |
| Badges | `/my-pursuit/badges/` | Existing (relocated in Phase 10a) |
| Milestones | `/my-pursuit/milestones/` | Existing (relocated in Phase 10a) |
| Titles | `/my-pursuit/titles/` | Existing (relocated in Phase 10a) |

That's 9 items at full bloom — the upper edge of the comfort zone but workable. The hub landing page can group them visually (Recognition / Identity / Activities) so the sub-nav doesn't feel undifferentiated. The existing `feature_card.html` partial scales to N cards without changes.

See [Gamification Vision](../design/gamification-vision.md) for the full RPG system design that this hub will host.

## URL Audit

Phase 10a renames the following URLs. All legacy paths get 301 redirects via `RedirectView(pattern_name=..., permanent=True, query_string=True)` so external links survive.

| Legacy | New |
|--------|-----|
| `/badges/` | `/my-pursuit/badges/` |
| `/badges/<series_slug>/` | `/my-pursuit/badges/<series_slug>/` |
| `/badges/<series_slug>/<psn_username>/` | `/my-pursuit/badges/<series_slug>/<psn_username>/` |
| `/milestones/` | `/my-pursuit/milestones/` |
| `/my-titles/` | `/my-pursuit/titles/` |

The previously-renamed `/achievements/*` URLs (committed in the original Phase 10) are also part of this rename — they shift from `/achievements/badges/` to `/my-pursuit/badges/`. Phase 10a updates the canonical paths, and the legacy `/achievements/*` paths get added as 301 redirects alongside the legacy `/badges/`, `/milestones/`, `/my-titles/` redirects.

The reverse-name strategy keeps existing `{% url 'badges_list' %}` and `reverse('badges_list')` calls working without churn — only the canonical path changes, not the URL name.

## Integration Points

- [IA and Sub-Nav](../architecture/ia-and-subnav.md): the hub-of-hubs IA structure that puts My Pursuit as one of three top-level destinations + the sub-nav infrastructure
- [Badge System](../architecture/badge-system.md): the underlying source of badge data, progress tracking, and the existing badge views that become hub sub-pages
- [Gamification](../architecture/gamification.md): the existing gamification scaffolding (`ProfileGamification`, `StatType`, `StageStatValue`) that feeds the hero progress overview
- [Gamification Vision](../design/gamification-vision.md): the full RPG system that this hub will host after the next initiative ships
- [Event System](../architecture/event-system.md): the source of `badge_earned` and `milestone_hit` events that power the Recent Unlocks module
- [Community Hub](community-hub.md): the parallel hub design (community discovery vs personal progression)
- [Dashboard](dashboard.md): the personal cockpit at `/` that surfaces personal-utility features as modules; the hub is for structured progression pages, the dashboard is for the modular cockpit

## Gotchas and Pitfalls

- **The hub is forward-compatible by design.** When the gamification initiative ships, it expands the My Pursuit sub-nav and feature grid — it does NOT introduce a new top-level menu. Resist the urge to add a "Gamification" or "Hunter" hub later; the My Pursuit name was chosen specifically to avoid that.

- **"My Pursuit" name reuse risk**: existing users have a mental model where "My Pursuit" = the personal-utility menu (Customization, Recap, etc.). After this initiative, "My Pursuit" becomes the badge/milestone/title hub (and eventually the gamification hub). Mitigation: a one-time "We've reorganized!" callout banner shown to authenticated users for 30 days post-launch, with explicit "old My Pursuit menu items moved to → Dashboard" wording. Also: every legacy URL (`/my-stats/`, `/my-shareables/`, `/recap/`) 301-redirects to its new home, so muscle memory still works.

- **Anonymous + authenticated rendering must both look intentional.** The marketing variant (anonymous) and the personalized variant (authenticated) are two different visual states. Test both during Phase 10b implementation; an anonymous user landing on a page that looks like "your dashboard but empty" feels broken. The marketing variant should sell the section, not show an empty version of the personal one.

- **Recent Unlocks expands `badge_earned` event metadata.** Bulk-per-sync emission means one `badge_earned` event can represent multiple badges; the recent-unlocks renderer needs to flatten the metadata into individual cards, sorted within the same `occurred_at` timestamp. Use the `metadata['badges']` list, not the event row itself, as the iteration unit.

- **Near Completion needs the next-tier progress, not the highest-unlocked tier.** Multi-tier badges (Bronze → Silver → Gold → Platinum) have more nuance than single-tier badges. If a user has earned the Bronze tier but is 80% of the way to Silver, the card should show "Silver: 80% (8/10 stages)", not "Bronze: 100%". Query the badge tier-by-tier when computing completion percentage.

- **Sub-nav active state for `/my-pursuit/badges/<slug>/`**: the sub-nav strip should highlight "Badges" as active even on the badge detail page. The URL prefix matcher in the [sub-nav infrastructure](../architecture/ia-and-subnav.md) handles this via longest-prefix matching (`/my-pursuit/badges/` is the longest matching prefix).

- **Titles sub-nav item is auth-gated.** `MyTitlesView` uses `LoginRequiredMixin`. The sub-nav config needs to filter items by `auth_required` server-side so anonymous users don't see a Titles tab that 302s them to login.

- **Sitemap entries**: the hub landing page (`/my-pursuit/`) and the sub-pages (`/my-pursuit/badges/`, `/my-pursuit/milestones/`) get sitemap entries at priority 0.7. Titles is auth-only and is excluded from the sitemap.

- **The Customization menu item is gone.** The old My Pursuit menu had a "Customization" link that pointed to `settings`. That URL still exists (the avatar dropdown's Settings link points to it) and the dashboard's existing "Edit Layout" controls cover the in-page customization affordances. Phase 10a kills the redundant Customization menu entry and verifies that every customization touchpoint is reachable via Settings or the dashboard.

## Related Docs

- [IA and Sub-Nav](../architecture/ia-and-subnav.md): the hub-of-hubs IA structure
- [Community Hub](community-hub.md): the sibling hub for community discovery
- [Dashboard](dashboard.md): the personal cockpit at `/` (where the old My Pursuit menu items relocate to)
- [Badge System](../architecture/badge-system.md): the source of badge data
- [Gamification](../architecture/gamification.md): the existing gamification scaffolding
- [Gamification Vision](../design/gamification-vision.md): the full RPG system this hub will host
- [Navigation](navigation.md): the navbar, footer, mobile drawer, sub-nav structure
