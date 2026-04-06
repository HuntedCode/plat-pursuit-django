# Navigation & Site Organization

The site's navigation consists of four layers: a desktop navbar with mega-menu dropdowns, a mobile drawer (mirroring the navbar), a footer sitemap grid, and cross-links embedded within feature pages. Together they ensure every major feature is discoverable from multiple entry points.

## Architecture Overview

Navigation is rendered globally via `base.html` includes. The navbar and footer appear on every page. The mobile drawer replaces the navbar below `lg:` (1024px).

The design philosophy: **no feature silos**. Every page should link outward to related features. The Challenge Hub links to Milestones. Badge detail links to Titles. Profile pages surface Challenges and Reviews. This "mesh" of cross-links reduces dead ends and increases feature discovery.

Menus are organized by intent, not by data type:

- **Browse**: Finding content (Games, Profiles, Trophies)
- **Discover**: Exploring metadata (Companies, Genres & Themes, Flagged Games)
- **Community**: Social features (Discord, Challenges, Game Lists, Review Hub)
- **Earn**: Progression features (Badges, Milestones, Leaderboards, Titles)
- **My Pursuit**: Personal hub (Dashboard, Customization, Recap, My Challenges/Lists/Profile/Shareables)

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
| Discover | Companies, Genres & Themes, Flagged Games | None |
| Community | Discord, Challenges, Game Lists, Review Hub | Fundraiser (when active) |
| Earn | Badges, Milestones, Leaderboards, Titles | Titles |
| My Pursuit | Dashboard, Customization, Monthly Recap, My Challenges, My Lists, My Profile, My Shareables | All (entire menu auth-gated) |

Dashboard is staff-only within My Pursuit.

### Avatar Dropdown

Streamlined to essentials: Theme Toggle, Profile, My Premium (if premium), Settings, Staff items, Logout. Heavy features like Monthly Recap and Trophy Case are accessible through the navbar and profile page instead.

### Notification Bell

Visible at `lg:` (1024px+). At `md:` (768-1023px), the mobile tab bar provides notification access.

## Mobile Drawer

Mirrors the navbar exactly. Section headers match mega-menu names. Auth gating is identical.

## Footer Sitemap Grid

Six-column grid (`grid-cols-3 lg:grid-cols-6`):

| Browse | Discover | Community | Earn | My Pursuit / Account | Legal + Connect |
|--------|----------|-----------|------|---------------------|-----------------|
| Games | Companies | Discord | Badges | My Profile | Privacy |
| Profiles | Genres & Themes | Challenges | Milestones | My Challenges | Terms |
| Trophies | Flagged Games | Game Lists | Leaderboards | My Lists | About |
| | | Review Hub | Titles* | My Shareables | Contact |
| | | | | | + Social icons |

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

The profile page has 6 tabs, switchable via `?tab=` URL parameter:

| Tab | Context Variable | Paginated | Infinite Scroll | Filters |
|-----|-----------------|-----------|-----------------|---------|
| Games | `profile_games` | Yes (50/page) | Yes | Platform, completion, sort |
| Trophies | `profile_trophies` | Yes (50/page) | Yes | Grade, earned status, sort |
| Badges | `profile_badges` | No | No | Tier, earned status, sort |
| Lists | `profile_lists` | No | No | None |
| Challenges | `profile_challenges` | No | No | None |
| Reviews | `profile_reviews` | Yes (50/page) | Yes | None |

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
- [Community Hub](community-hub.md): Reviews, ratings, review hub
- [Badge System](../architecture/badge-system.md): Badges, titles, leaderboards
