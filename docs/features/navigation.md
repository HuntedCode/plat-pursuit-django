# Navigation & Site Organization

PlatPursuit's navigation is being reworked into a **hub-of-hubs IA**: 3 direct-link hub destinations in the global navbar (Browse, Community, My Pursuit) plus the Dashboard logo, with a persistent sub-navigation strip below the main navbar that surfaces hub sub-pages on every URL in a hub's family. This doc covers the navigation chrome (navbar, mobile drawer, footer, sub-nav, profile tabs) and the cross-linking inventory between feature pages.

> **Status**: planned. The current navbar is the legacy 4-dropdown form (Browse / Community / Achievements / My Pursuit) with 25 total dropdown items. Phase 10a of the Community Hub initiative implements the hub-of-hubs collapse + sub-nav infrastructure. This doc reflects the planned end state; it will get a final pass after Phase 10a ships to capture any decisions made during build.

## Architecture Overview

Navigation is rendered globally via `base.html` includes. The main navbar appears on every page. A persistent **sub-navigation strip** appears on every page that belongs to one of the four hub families (Dashboard, Browse, Community, My Pursuit), URL-prefix matched. The mobile drawer mirrors the desktop nav structure. The footer is a sitemap grid that mirrors the hub structure.

Design philosophy: **menus expose the few, hubs expose the many**. The global nav has 3 buttons. Each is a direct link to a hub. The hub does the heavy lifting of "introduce the user to what's in this section." The sub-nav handles "I know what I want, take me to the page." This eliminates the redundancy of having 25 dropdown items spread across menus that duplicate the hubs' job.

A second design principle: **no feature silos**. Every page should link outward to related features. The Challenge Hub links to Milestones. Badge detail links to Titles. Profile pages surface Challenges and Reviews. This "mesh" of cross-links reduces dead ends and increases feature discovery.

The four hubs:

| Hub | URL | Mental mode | Sub-nav items |
|-----|-----|-------------|---------------|
| **Dashboard** | `/` | "your personal cockpit" | Dashboard, Stats, Shareables, Recap |
| **Browse** | `/games/` | "find new content" | Games, Trophies, Companies, Genres & Themes, Flagged Games |
| **Community** | `/community/` | "what's everyone doing" | Hub, Pursuit Feed, Reviews, Challenges, Lists, Leaderboards, Profiles |
| **My Pursuit** | `/my-pursuit/` | "your trophy hunting identity and progression" | Badges, Milestones, Titles *(today)* |

The Browse hub IS the games list page — no separate landing — because games are what most users browse for and the sub-nav handles wayfinding to the other browse pages. The Community and My Pursuit hubs have dedicated landing pages designed as destinations.

See [IA and Sub-Nav](../architecture/ia-and-subnav.md) for the detailed design, the URL prefix matching rules, and the sub-nav infrastructure.

## File Map

| File | Purpose |
|------|---------|
| `templates/partials/navbar.html` | Global navbar: 3 hub buttons + Dashboard logo + bell + avatar dropdown |
| `templates/partials/hub_subnav.html` | Sticky sub-nav strip rendered below the navbar on hub-family pages |
| `templates/partials/mobile_tabbar.html` | Mobile/tablet drawer mirroring navbar + sub-nav structure |
| `templates/partials/footer.html` | Sitemap grid footer (6-column layout matching the hub structure) |
| `core/context_processors.py` | `hub_subnav_context()` — URL-prefix matcher that drives the sub-nav |
| `core/constants.py` *(or new `core/hub_subnav.py`)* | `HUB_SUBNAV_CONFIG` — the hub definitions |
| `templates/trophies/profile_detail.html` | Profile page with 7 tabs (Games, Trophies, Badges, Lists, Challenges, Reviews, Activity) |
| `templates/trophies/partials/profile_detail/profile_detail_header.html` | Profile header with quick links row |
| `templates/trophies/partials/profile_detail/challenge_list_items.html` | Profile Challenges tab content |
| `templates/trophies/partials/profile_detail/review_list_items.html` | Profile Reviews tab content (supports infinite scroll) |
| `templates/trophies/partials/profile_detail/activity_list_items.html` | Profile Activity tab content (supports infinite scroll) |
| `trophies/views/profile_views.py` | ProfileDetailView with tab handlers |

## Global Navbar

### 3 hub buttons + chrome

| Element | Behavior |
|---------|----------|
| **Logo** | Direct link to `/` (Dashboard) |
| **Browse** | Direct link to `/games/` |
| **Community** | Direct link to `/community/` |
| **My Pursuit** | Direct link to `/my-pursuit/` |
| **Notification bell** | Existing dropdown, unchanged. Visible at `lg:` (1024px+). At `md:` (768-1023px), the mobile tab bar provides notification access. |
| **Avatar dropdown** | Theme · Profile · My Premium · Settings · Staff items · Logout |

That's it. 4 direct-link buttons (logo + 3 hubs), zero dropdowns at the global nav level. The "Customization" item that lived in the legacy My Pursuit dropdown is killed entirely — it pointed to `settings`, which the avatar dropdown's Settings link already covers, AND the dashboard already has in-page "Edit Layout" controls for module customization.

### Avatar dropdown

Streamlined to essentials: Theme Toggle, Profile, My Premium (if premium), Settings, Staff items, Logout. Heavy features like Monthly Recap and Trophy Case are accessible through the Dashboard sub-nav and profile page instead.

## Hub Sub-Navigation Strip

A thin pill-tab strip rendered below the main navbar (and ABOVE the hotbar) on every page that belongs to a hub's family. URL-prefix matched. Sticky on `lg:+`, inline (horizontal scroll) on mobile.

Stacking order (top of viewport):

```
[Main navbar — sticky]
[Hub sub-nav — sticky on lg:+, inline on mobile]
[Hotbar — inline, NOT sticky]
[Page content]
```

The sub-nav is hidden on pages that don't belong to any hub (settings, auth flows, notification inbox, staff admin pages, error pages).

See [IA and Sub-Nav](../architecture/ia-and-subnav.md) for the prefix matching algorithm, the configuration shape, and the visual treatment details.

## Mobile Drawer

The mobile drawer (`templates/partials/mobile_tabbar.html`) mirrors the navbar + sub-nav structure: 3 hub buttons + Dashboard logo. When the drawer opens, each section header is a hub name, and the items below the header are that hub's sub-nav items. Mobile users get one-click access to any sub-page from the drawer.

The sub-nav strip itself ALSO renders on mobile (as a horizontal scroll strip) so users have two ways to navigate: the drawer (overview) and the strip (in-context).

## Footer Sitemap Grid

Six-column grid (`grid-cols-2 md:grid-cols-3 lg:grid-cols-6`) matching the hub structure:

| Browse | Community | My Pursuit | Dashboard | Legal | Connect |
|--------|-----------|------------|-----------|-------|---------|
| Games | Hub | Badges | My Profile | Privacy | Social icons |
| Trophies | Pursuit Feed | Milestones | My Stats | Terms | (X, YouTube, Discord) |
| Companies | Reviews | Titles* | My Shareables | About | |
| Genres & Themes | Challenges | | Recap | Contact | |
| Flagged Games | Lists | | | | |
| | Leaderboards | | | | |
| | Profiles | | | | |

- Titles link is auth-gated (only shown to authenticated users with a profile)
- Dashboard column is auth-gated; guests see "Account" with Sign In / Sign Up links instead (ensures 6 grid children always)

## Cross-Linking Inventory

These are the cross-links embedded in feature pages. The "mesh" of cross-links reduces dead ends and increases feature discovery.

| Source Page | Links To | Implementation |
|-------------|----------|----------------|
| Milestones overview | Badges, Leaderboards, My Titles | Button row in `milestone_overview.html` |
| Badge list | Leaderboards, Milestones | Link bar in `badge_list.html` |
| Badge detail | My Titles (when badge has a title) | Callout in `badge_detail_header.html` |
| Challenge Hub | Milestones (challenges category) | Alert in `challenge_hub.html` |
| Review Hub (multi-game) | Game detail pages (dropdown) | Dropdown in `hub_header.html` |
| Profile header | Challenges tab, Reviews tab, Leaderboards, Milestones | Quick links in `profile_detail_header.html` |
| Game detail (about card) | Genre detail, Theme detail, Company detail | Clickable badges/links in `game_about_card.html` |
| Game detail (header) | Company detail (developer, publisher) | Links in `game_detail_header.html` |
| Company detail | Game detail (per-role game cards) | Cards in `company_detail/game_section.html` |
| Genre/Theme detail | Game detail (game cards) | Reuses `game_list/game_cards.html` |
| Community Hub | Reviews, Challenges, Lists, Leaderboards, Discord | Feature cards on `community/hub.html` (Feature Spotlight design) |
| My Pursuit Hub | Badges, Milestones, Titles | Feature cards on `my_pursuit/hub.html` |

## Profile Page Tabs

The profile page has 7 tabs, switchable via `?tab=` URL parameter:

| Tab | Context Variable | Paginated | Infinite Scroll | Filters |
|-----|-----------------|-----------|-----------------|---------|
| Games | `profile_games` | Yes (50/page) | Yes | Platform, completion, sort |
| Trophies | `profile_trophies` | Yes (50/page) | Yes | Grade, earned status, sort |
| Badges | `profile_badges` | No | No | Tier, earned status, sort |
| Lists | `profile_lists` | No | No | None |
| Challenges | `profile_challenges` | No | No | None |
| Reviews | `profile_reviews` | Yes (50/page) | Yes | None |
| Activity | `profile_events` | Yes (50/page) | Yes | None (v1) |

The Activity tab reads from the `Event` table filtered by `profile=target_profile`. v1 shows only events authored BY the target user (their badges, reviews, platinums, etc.). It does not show "events about them" (e.g. someone replied to your review).

Tab handlers in `ProfileDetailView`:
- `_build_games_tab_context()`, `_build_trophies_tab_context()`, `_build_badges_tab_context()`, `_build_lists_tab_context()`, `_build_challenges_tab_context()`, `_build_reviews_tab_context()`, `_build_activity_tab_context()`
- Counts for tab badges: `profile_challenge_count`, `profile_review_count`, `profile_lists_count`, `profile_event_count`
- AJAX partial templates returned for paginated tabs via `get_template_names()`

Profile pages live under `/community/profiles/<u>/`, so they show the Community sub-nav strip with "Profiles" highlighted as active.

## Key Flows

### Tab Switching

1. User clicks tab link (`?tab=challenges`)
2. Browser saves scroll position to `sessionStorage`
3. Full page reload with new `tab` param
4. `ProfileDetailView.get_context_data()` routes to the correct tab handler
5. Only the active tab's data is queried (lazy loading)
6. Scroll position restored from `sessionStorage`

### Infinite Scroll (Reviews Tab)

1. `PlatPursuit.InfiniteScroller.create()` initializes with `reviews-grid`, `reviews-sentinel`, `reviews-loading` IDs
2. Observer triggers when sentinel enters viewport
3. AJAX GET to same URL with incremented `?page=` param
4. View returns `review_list_items.html` partial (detected via `X-Requested-With: XMLHttpRequest`)
5. New cards appended to `reviews-grid`

The Activity tab uses the same pattern with `activity-*` IDs.

### Sub-Nav Active State

1. Request comes in to e.g. `/community/profiles/<u>/`
2. `hub_subnav_context()` context processor inspects `request.path`
3. Longest-prefix match against `HUB_SUBNAV_CONFIG` finds `community` hub
4. Active item determined by matching `request.resolver_match.url_name` against the hub's items
5. `hub_subnav.html` renders the strip with the matched item highlighted

## Gotchas and Pitfalls

- **Navbar, drawer, sub-nav, and footer must stay in sync**: any sub-nav item added must also be added to `mobile_tabbar.html` (the drawer) and `footer.html` (the sitemap grid). The sub-nav strip is the source of truth via `HUB_SUBNAV_CONFIG`, but the drawer and footer are still hand-maintained templates and need parallel updates.

- **Footer grid requires 6 children**: the footer uses `grid-cols-2 md:grid-cols-3 lg:grid-cols-6`. If the auth-conditional Dashboard column is removed, an "Account" column with Sign In/Sign Up takes its place to maintain 6 grid children. Removing a column without adding a replacement creates an ugly gap.

- **Auth gating on Titles**: the Titles sub-nav item requires `user.is_authenticated and user.profile` because `MyTitlesView` uses `LoginRequiredMixin`. This gating must be applied in all four navigation layers (sub-nav config, mobile drawer, footer, and the My Pursuit hub feature card).

- **Profile tab handlers**: adding a new tab requires updates in four places: (1) tab link + panel in `profile_detail.html`, (2) handler method in `ProfileDetailView`, (3) tab routing in `get_context_data()`, (4) AJAX template name in `get_template_names()` if paginated.

- **Challenges tab is not paginated**: unlike Games, Trophies, Reviews, and Activity, Challenges loads all records at once. No sentinel/loading elements are needed. The InfiniteScroller gracefully handles missing element IDs.

- **`sm:` breakpoints are forbidden in navigation templates** (per CLAUDE.md). The minimum designed layout is 768px (tablet) for legacy templates; redesigned templates support 375px base. Navigation templates use `md:` and `lg:` only.

- **Cross-links should be contextual.** Don't add cross-links that feel like spam. Each link should make sense in context (e.g., Challenge Hub linking to Milestones because challenge progress counts toward them).

- **Sub-nav vs breadcrumb redundancy is acceptable.** The breadcrumb (`Home > Community > Reviews`) and the sub-nav (with "Reviews" highlighted) both signal "you are here." This is intentional: the breadcrumb stays for SEO (JSON-LD) and accessibility, the sub-nav is the visual primary. They serve different audiences.

- **Sub-nav must be hidden on non-hub pages.** Settings, auth flows, notification inbox, staff admin pages, and error pages render NO sub-nav. The context processor returns `hub_section=None` and the template `{% if hub_section %}` short-circuits. Test these explicitly during Phase 10a.

- **The Dashboard hub is the personal cockpit.** Its sub-nav is for navigating to personal-utility sub-pages (Stats, Shareables, Recap). The dashboard's existing module tabs (Default + custom) are an in-page premium feature for module organization, separate from the IA-level sub-nav. Don't conflate them.

- **My Shareables needs full revival, not just a refresh.** The current `/my-shareables/` URL is a 301 redirect to home (the page was deleted at some point). Phase 10b builds a new `/dashboard/shareables/` page from scratch.

## Related Docs

- [IA and Sub-Nav](../architecture/ia-and-subnav.md): the hub-of-hubs IA structure, sub-nav infrastructure, URL prefix matching
- [Community Hub](community-hub.md): the Community hub destination at `/community/`
- [My Pursuit Hub](my-pursuit-hub.md): the My Pursuit hub destination at `/my-pursuit/`
- [Dashboard](dashboard.md): the personal cockpit at `/`
- [Template Architecture](../reference/template-architecture.md): base.html, the hotbar, context processors
- [JS Utilities](../reference/js-utilities.md): InfiniteScroller, ZoomScaler
- [Challenge Systems](challenge-systems.md): Challenge types and detail pages
- [Review Hub](review-hub.md): Reviews, ratings, concept trophy groups
- [Badge System](../architecture/badge-system.md): Badges, titles, leaderboards
