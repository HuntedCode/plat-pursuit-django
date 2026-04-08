# Navigation & Site Organization

The site's navigation consists of four layers: a desktop navbar with mega-menu dropdowns, a mobile drawer (mirroring the navbar), a footer sitemap grid, and cross-links embedded within feature pages. Together they ensure every major feature is discoverable from multiple entry points.

## Architecture Overview

Navigation is rendered globally via `base.html` includes. The navbar and footer appear on every page. The mobile drawer replaces the navbar below `lg:` (1024px).

The design philosophy: **no feature silos**. Every page should link outward to related features. The Challenge Hub links to Milestones. Badge detail links to Titles. Profile pages surface Challenges and Reviews. This "mesh" of cross-links reduces dead ends and increases feature discovery.

Menus are organized by intent, not by data type. The Community Hub initiative collapsed the previous 5 menus to 4, sharpening the boundaries between content discovery, community signal, personal progression, and personal pages:

- **Browse**: Finding content (Games, Profiles, Trophies, Companies, Genres & Themes, Flagged Games). Absorbs the previous Discover menu.
- **Community**: Hub, Pursuit Feed, Challenges, Review Hub, Game Lists, Leaderboards, Discord, Fundraiser (when active). The Pursuit Feed and Community Hub are the headline features added by the initiative.
- **Achievements**: Personal progression features (Badges, Milestones, Titles). Renamed from "Earn". Leaderboards moved out to Community.
- **My Pursuit**: Personal hub (Customization, Recap, My Challenges, My Lists, My Profile, My Shareables, My Stats, Platinum Grid). The dashboard is the site root `/` and is no longer listed as a menu item.

## File Map

| File | Purpose |
|------|---------|
| `templates/partials/navbar.html` | Desktop navbar with mega-menu dropdowns + avatar dropdown |
| `templates/partials/mobile_tabbar.html` | Mobile/tablet drawer mirroring navbar structure |
| `templates/partials/footer.html` | Sitemap grid footer (6-column layout) |
| `templates/trophies/profile_detail.html` | Profile page with 6 tabs (Games, Trophies, Badges, Lists, Challenges, Reviews) |
| `templates/trophies/partials/profile_detail/profile_detail_header.html` | Profile header with quick links row |
| `templates/trophies/partials/profile_detail/challenge_list_items.html` | Profile Challenges tab content |
| `templates/trophies/partials/profile_detail/review_list_items.html` | Profile Reviews tab content (supports infinite scroll) |
| `trophies/views/profile_views.py` | ProfileDetailView with tab handlers |

## Desktop Navbar Structure

### Mega-Menus

| Menu | Items | Auth-Gated Items |
|------|-------|------------------|
| Browse | Games, Profiles, Trophies, Companies, Genres & Themes, Flagged Games | None |
| Community | Community Hub, Pursuit Feed, Challenges, Review Hub, Game Lists, Leaderboards, Discord | Fundraiser (when active) |
| Achievements | Badges, Milestones, Titles | Titles |
| My Pursuit | Customization, Monthly Recap, My Challenges, My Lists, My Profile, My Shareables, My Stats, Platinum Grid | All (entire menu auth-gated) |

The "Community Hub" entry is intentionally the first item in the Community dropdown so users have a clear front door to the destination page. The Pursuit Feed sits second because it's the most-visited destination beyond the hub itself.

The dashboard at `/` is the universal landing page; it does not appear in any menu because every authenticated user lands there by default. Personal stats (`/tools/stats/`) and the platinum grid wizard (`/tools/platinum-grid/`) are public after Phase 9 of the Community Hub initiative.

### Avatar Dropdown

Streamlined to essentials: Theme Toggle, Profile, My Premium (if premium), Settings, Staff items, Logout. Heavy features like Monthly Recap and Trophy Case are accessible through the navbar and profile page instead.

### Notification Bell

Visible at `lg:` (1024px+). At `md:` (768-1023px), the mobile tab bar provides notification access.

## Mobile Drawer

Mirrors the navbar exactly. Section headers match mega-menu names. Auth gating is identical.

## Footer Sitemap Grid

Six-column grid (`grid-cols-2 md:grid-cols-3 lg:grid-cols-6`) following the menu structure:

| Browse | Community | Achievements | My Pursuit / Account | Legal | Connect |
|--------|-----------|--------------|---------------------|-------|---------|
| Games | Community Hub | Badges | My Profile | Privacy | Social icons |
| Profiles | Pursuit Feed | Milestones | My Challenges | Terms | (X, YouTube, Discord) |
| Trophies | Challenges | Titles* | My Lists | About | |
| Companies | Review Hub | | My Shareables | Contact | |
| Genres & Themes | Game Lists | | My Stats | | |
| Flagged Games | Leaderboards | | Platinum Grid | | |
| | Discord | | | | |

- Titles link: auth-gated (only shown to authenticated users with a profile)
- My Pursuit column: shown only for authenticated users with a profile. Guests see "Account" with Sign In / Sign Up links instead (ensures 6 grid children always).

## Cross-Linking Inventory

These are the cross-links embedded in feature pages:

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
- `_build_games_tab_context()`, `_build_trophies_tab_context()`, `_build_badges_tab_context()`, `_build_lists_tab_context()`, `_build_challenges_tab_context()`, `_build_reviews_tab_context()`
- Counts for tab badges: `profile_challenge_count`, `profile_review_count`, `profile_lists_count`
- AJAX partial templates returned for paginated tabs via `get_template_names()`

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

## Gotchas and Pitfalls

- **Navbar and drawer must stay in sync**: Any menu item added to the navbar must also be added to `mobile_tabbar.html`. They use different markup (DaisyUI mega-menu vs drawer sections) but must have identical items and auth gating.
- **Footer grid requires 6 children**: The footer uses `grid-cols-3 lg:grid-cols-6`. If the auth-conditional "My Pursuit" column is removed, an "Account" column with Sign In/Sign Up takes its place to maintain 6 grid children. Removing a column without adding a replacement creates an ugly gap.
- **Auth gating on Titles**: The "Titles" link requires `user.is_authenticated and user.profile` because `MyTitlesView` uses `LoginRequiredMixin`. This gating must be applied in all three navigation layers (navbar, drawer, footer).
- **Profile tab handlers**: Adding a new tab requires updates in four places: (1) tab link + panel in `profile_detail.html`, (2) handler method in `ProfileDetailView`, (3) tab routing in `get_context_data()`, (4) AJAX template name in `get_template_names()` if paginated.
- **Challenges tab is not paginated**: Unlike Games, Trophies, and Reviews, Challenges loads all records at once. No sentinel/loading elements are needed. The InfiniteScroller gracefully handles missing element IDs.
- **`sm:` breakpoints are forbidden**: Per CLAUDE.md, never use `sm:` in navigation templates. The minimum designed layout is 768px (tablet). Base styles must work at that width.
- **Cross-links should be contextual**: Don't add cross-links that feel like spam. Each link should make sense in context (e.g., Challenge Hub linking to Milestones because challenge progress counts toward them).

## Related Docs

- [Template Architecture](../reference/template-architecture.md): base.html structure, zoom wrapper
- [JS Utilities](../reference/js-utilities.md): InfiniteScroller, ZoomScaler
- [Challenge Systems](challenge-systems.md): Challenge types and detail pages
- [Review Hub](review-hub.md): Reviews, ratings, concept trophy groups
- [Community Hub](community-hub.md): Site-wide community destination, Pursuit Feed, leaderboards
- [Badge System](../architecture/badge-system.md): Badges, titles, leaderboards
