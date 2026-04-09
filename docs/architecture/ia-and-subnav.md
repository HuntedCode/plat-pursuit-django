# Information Architecture and Sub-Navigation

PlatPursuit uses a **hub-of-hubs IA**: the global navbar contains exactly three direct-link hub destinations (Browse, Community, My Pursuit) plus the Dashboard logo, with zero dropdowns at the global-nav level. Each of the four hubs has a dedicated landing page and a persistent sub-navigation strip that surfaces its sub-pages on every URL in the hub's family. This doc describes the design, the URL prefix matching rules, the sub-nav infrastructure, and the rationale behind the decisions.

> **Status**: planned. The current navbar is the legacy 4-dropdown form (Browse / Community / Achievements / My Pursuit). Phase 10a of the Community Hub initiative implements the hub-of-hubs collapse + sub-nav infrastructure described in this doc.

## Why this design

The legacy navbar had four dropdown menus with 25 total items spread across them. Two menus were over the 5-7 item comfort zone (Community had 8, My Pursuit had 8). The dashboard and Community Hub were supposed to be wayfinders, but the menus duplicated their job and won by default because they were loaded on every page. The hubs were redundant, and the menus were overwhelming.

The hub-of-hubs design solves both:

- **Menus expose the few. Hubs expose the many.** The global nav has 3 buttons. Each is a direct link to a hub. The hub does the heavy lifting of "introduce the user to what's in this section." The sub-nav handles "I know what I want, take me to the page."
- **Sub-navs scale per-section.** Browse's sub-nav has 6 items. Community's has 7. My Pursuit's has 3 today, growing to 8 after gamification. Dashboard's has 4. Each sub-nav is comfortable inside its own context, and users only see the items relevant to where they are.
- **Each section has a real front door.** The hub landing page is a destination, not a redirect. New users get a wayfinder; power users skip straight to the sub-nav. Both audiences are served.

The pattern is well-established: GitHub's repository nav, Steam's hub strips, Stripe's docs, Notion's workspaces, Apple's product pages all use a sticky sub-nav strip below the main nav for section-specific wayfinding. PlatPursuit is adopting the same primitive.

## The 4 Hubs

| Hub | URL | Mental mode | Landing page |
|-----|-----|-------------|--------------|
| **Dashboard** | `/` | "your personal cockpit" | The modular cockpit (existing dashboard with module customization) |
| **Browse** | `/games/` | "find new content" | The games list page (no separate hub landing) |
| **Community** | `/community/` | "what's everyone doing" | The Community Hub Feature Spotlight page |
| **My Pursuit** | `/my-pursuit/` | "your trophy hunting identity and progression" | The My Pursuit Hub progress overview page |

The Dashboard is the universal landing page (every authenticated user lands at `/`). The Browse hub IS the games list page — no separate landing — because games are what most users browse for and the sub-nav handles wayfinding to the other browse pages. The Community and My Pursuit hubs have dedicated landing pages designed as destinations.

## Global Navbar (3 buttons + chrome)

| Element | Behavior |
|---------|----------|
| **Logo** | Direct link to `/` (Dashboard) |
| **Browse** | Direct link to `/games/` |
| **Community** | Direct link to `/community/` |
| **My Pursuit** | Direct link to `/my-pursuit/` |
| **Notification bell** | Existing dropdown, unchanged |
| **Avatar dropdown** | Theme · Profile · My Premium · Settings · Staff items · Logout (Customization removed) |

That's it. 4 direct-link buttons (logo + 3 hubs), zero dropdowns at the global nav level. Mental load on every page collapses to "which hub am I in?"

The avatar dropdown handles account essentials. The "Customization" item was killed entirely — it pointed to `settings`, which the avatar dropdown's Settings link already covers, AND the dashboard already has in-page "Edit Layout" controls for module customization. The menu item was pure duplication.

## Sub-Navigation Strip

The sub-nav is a thin pill-tab strip rendered below the main navbar (and ABOVE the hotbar) on every page that belongs to a hub's family. URL-prefix matched. Sticky at all breakpoints (`top-16` to sit just below the 64px navbar). Horizontal scroll on mobile when items overflow the viewport width.

### Visual treatment

```
🏛️ Community  •  [Hub] [Feed] [Reviews] [Challenges] [Lists] [Leaderboards] [Profiles]
```

- **Section icon and label** on the left, identifying the current hub
- **Pill-tab items** on the right, one per sub-page
- **Active item** gets primary-color underline + filled background, like browser tab bars
- **Compact**: ~40-44px tall, smaller text than the main navbar
- **Mobile**: collapses to a horizontal scroll strip (one line, swipe to see more), with a fade-out gradient on the right edge to signal overflow, and the active item auto-scrolls into view on page load

### Stacking order (top of viewport)

```
[Main navbar — sticky top-0 z-50 — 64px height (daisyUI .navbar min-h-16)]
[Hub sub-nav — sticky top-16 z-40 — ~46px (h-10/h-11 + border-b-2)]
[Hotbar — sticky top-[7.25rem] z-30 — variable height, has its own collapse toggle, ~6-10px gap above]
[Page content]
[Mobile bottom tab bar — sticky bottom-0 z-40, lg:hidden — 56px height]
```

All three top-of-viewport chrome elements are sticky at all breakpoints. As the user scrolls, they stay pinned together: the navbar at `top-0`, the sub-nav directly below it (flush against the navbar's bottom edge), and the hotbar directly below the sub-nav with a small breathing-room gap. On non-hub pages where the sub-nav is hidden, the hotbar pins directly below the navbar (with the same gap) instead. The mobile bottom tab bar is also sticky at the bottom of the viewport. Total pinned chrome on desktop with the hotbar visible: ~190-210px (varies with hotbar's per-state height + the toggle button). The hotbar's user-controllable collapse toggle (the chevron button hanging off its bottom edge) lets users reclaim that space whenever they want.

### Sticky offsets are JS-aligned at runtime

The Tailwind classes `top-16` (sub-nav) and `top-[7.25rem]` (hotbar) are good first guesses but they assume an exact 64px navbar height, which is the daisyUI `.navbar` `min-height` and NOT necessarily the actual rendered height. Avatar size, font metrics, web-font swaps, and DPI rounding can all push the real navbar height to 65-66px, which causes a visible 1-2px shift when the sub-nav transitions from in-flow to sticky-pinned.

`alignStickyChrome()` in `static/js/main.js` runs on `DOMContentLoaded`, on `resize`, after `document.fonts.ready`, and on every `hotbar:toggle` custom event to measure the navbar's actual `getBoundingClientRect().height` and inline-style `top:` on the sub-nav and hotbar to match. The CSS classes remain as the pre-JS fallback so there's no FOUC; the JS just refines the offset once the layout is known. Future contributors editing the navbar's content should NOT need to retune the sub-nav's `top-*` value — JS handles it.

The function also varies the gap above the hotbar based on its collapsed state: when expanded, there's an 8px breathing-room gap between the hotbar and the sub-nav (or navbar on non-hub pages); when collapsed (`localStorage.hotbar_hidden === 'true'`), the gap drops to 0 so the toggle "tab" attaches flush against the bottom edge of the chrome above it, reading as a tab handle of the sub-nav rather than a floating element. The toggle button has `border-t-0 rounded-b-lg` styling to reinforce that visual.

The earlier iteration of this work made only the sub-nav sticky and let the navbar scroll away, with the rationale "the navbar is too tall to pin always (~120px combined)." That decision was reversed when the user pointed out that scrolling deep into a page made it hard to jump between hubs — they had to scroll back to the top to see the navbar's hub buttons. Pinning both gives constant access to hub navigation at any scroll position.

The previous "navbar would be too tall" concern was also based on an outdated navbar shape that included the search bar and a Recap shortcut icon. Both were dropped when the bottom tab bar took over mobile navigation: search was deprioritized (the IA wayfinding handles content discovery), and Recap is reachable via the Dashboard sub-nav. The slimmer navbar (logo + 4 hub buttons + bell + avatar) at 64px is comfortable to pin alongside the 44px sub-nav.

The hotbar got the same treatment in a follow-up pass. Originally we left it inline because pinning a third strip felt heavy, but the result was inconsistent: the navbar and sub-nav scrolled as a unit, then the hotbar scrolled away on its own and its sync controls became unreachable mid-page. Sticky-as-a-unit is the more consistent behavior, and the hotbar already has a built-in collapse toggle for users who want to reclaim the vertical space. On non-hub pages (settings, notifications, etc.) where the sub-nav doesn't render, the hotbar pins directly below the navbar at `top-16` instead of `top-[6.75rem]` — the template branches on `hub_section` to pick the right offset.

### Mobile layout

On `<lg:` viewports, the same navbar pins at `top-0` but the hub buttons are hidden (the bottom tab bar surfaces them instead). The sub-nav strip pins at `top-16` exactly like desktop. The bottom tab bar pins at `bottom-0` with 4 hub destinations matching the navbar's 4 hub buttons (Dashboard / Browse / Community / My Pursuit). Active state is driven by `hub_section` so tapping the current hub's tab is a no-op visual confirmation.

What used to live in the bottom tab bar (Home / Games / Search / Notifications / More) is gone:

- **Home** is now Dashboard (semantically the same, just labeled to match the hub name)
- **Games** is rolled into the Browse hub
- **Search** was dropped site-wide (the IA handles wayfinding)
- **Notifications** lives in the navbar bell, which is now visible at all breakpoints
- **More** drawer is gone — its job (surface the 4 hubs and their sub-pages) is now done by the bottom tab bar plus the sub-nav strip directly

### Hub sub-nav contents

| Hub | Sub-nav items |
|-----|---------------|
| **Dashboard** | Dashboard \| Stats \| Shareables \| Recap |
| **Browse** | Games \| Trophies \| Companies \| Genres & Themes \| Flagged Games |
| **Community** | Hub \| Profiles \| Reviews \| Challenges \| Lists \| Leaderboards |
| **My Pursuit** *(today)* | Badges \| Milestones \| Titles |
| **My Pursuit** *(after gamification)* | Logbook \| Star Chart \| Quests \| Arcade \| Market \| Badges \| Milestones \| Titles |

Discord is NOT in the Community sub-nav — it's a permanent CTA on the Community Hub landing page itself. Fundraiser is also not a sub-nav item; when active it gets a prominent banner on the hub landing page.

## URL-Prefix Matching

The sub-nav strip is rendered by a context processor (`hub_subnav_context` in `core/context_processors.py`) that inspects `request.path` against a configured prefix-to-hub mapping. The matcher uses **longest-prefix-wins** ordering:

```python
HUB_SUBNAV_CONFIG = {
    'community': {
        'matches': ['/community/'],
        'items': [...],
    },
    'my_pursuit': {
        'matches': ['/my-pursuit/'],
        'items': [...],
    },
    'browse': {
        'matches': ['/games/', '/trophies/', '/companies/', '/genres/', '/themes/'],
        'items': [...],
    },
    'dashboard': {
        'matches': ['/dashboard/', '/'],  # bare '/' is the catchall, ordered last
        'items': [...],
    },
}
```

The matcher iterates the hubs in order, checking each `matches` prefix with `request.path.startswith(prefix)`. The first hub with a matching prefix wins. The Dashboard hub's bare `/` match must be checked LAST so a path like `/community/profiles/<u>/` correctly matches Community, not Dashboard.

The `/` catchall is also constrained: it ONLY matches when `request.path == '/'` exactly (not when the path starts with `/`). The implementation uses an explicit equality check for the bare-root case, separate from the `/dashboard/` startswith check.

### Pages with no hub

The sub-nav is intentionally hidden on pages that don't belong to any hub. The context processor returns `hub_section=None` and the template `{% if hub_section %}` short-circuits the include. Pages that should have NO sub-nav include:

- `/settings/` (account settings)
- `/auth/login/`, `/auth/signup/`, `/auth/...` (auth flows)
- `/notifications/` (notification inbox)
- All `/staff/*` pages (admin tools)
- Error pages (404, 500)
- The Stripe / PayPal / webhook URLs (server-only)

These pages render the global nav as usual but no sub-nav strip below it.

### Active item highlighting

Within a hub's sub-nav, the *active* item is the one whose `url_name` resolves to the current `request.resolver_match.url_name`. For sub-pages with kwargs (e.g., `/community/profiles/<u>/`), the matcher uses the URL name (`profile_detail`) which the sub-nav config maps to the parent sub-nav slug (`profiles`).

Profile detail pages (`/community/profiles/<u>/`) and badge detail pages (`/my-pursuit/badges/<slug>/`) inherit their parent sub-nav highlighting via this URL-name mapping.

## Sub-nav Infrastructure

### Files

| File | Purpose |
|------|---------|
| `core/context_processors.py` | `hub_subnav_context(request)` — runs on every request, returns `hub_section`, `hub_subnav_items`, `hub_subnav_active_slug` |
| `core/constants.py` *(or new `core/hub_subnav.py`)* | `HUB_SUBNAV_CONFIG` constant with the hub definitions |
| `templates/partials/hub_subnav.html` | Renders the strip; included from `base.html` |
| `templates/base.html` | `{% include 'partials/hub_subnav.html' %}` between the navbar and the hotbar |
| `plat_pursuit/settings.py` | Registers `hub_subnav_context` in `TEMPLATES['OPTIONS']['context_processors']` |

### Configuration shape

```python
HUB_SUBNAV_CONFIG = {
    'dashboard': {
        'label': 'Dashboard',
        'icon': 'layout-dashboard',
        'matches': ['/dashboard/'],  # plus exact-match '/' check separately
        'items': [
            {'slug': 'home', 'label': 'Dashboard', 'url_name': 'home', 'icon': 'home'},
            {'slug': 'stats', 'label': 'My Stats', 'url_name': 'my_stats', 'icon': 'bar-chart-3'},
            {'slug': 'shareables', 'label': 'My Shareables', 'url_name': 'my_shareables', 'icon': 'image'},
            {'slug': 'recap', 'label': 'Recap', 'url_name': 'recap_index', 'icon': 'calendar'},
        ],
    },
    'browse': {
        'label': 'Browse',
        'icon': 'compass',
        'matches': ['/games/', '/trophies/', '/companies/', '/genres/', '/themes/'],
        'items': [
            {'slug': 'games', 'label': 'Games', 'url_name': 'games_list', 'icon': 'gamepad-2'},
            {'slug': 'trophies', 'label': 'Trophies', 'url_name': 'trophies_list', 'icon': 'trophy'},
            {'slug': 'companies', 'label': 'Companies', 'url_name': 'companies_list', 'icon': 'building'},
            {'slug': 'genres', 'label': 'Genres & Themes', 'url_name': 'genres_list', 'icon': 'tag'},
            {'slug': 'flagged', 'label': 'Flagged Games', 'url_name': 'flagged_games', 'icon': 'flag'},
        ],
    },
    'community': {
        'label': 'Community',
        'icon': 'users',
        'matches': ['/community/'],
        'items': [
            {'slug': 'hub', 'label': 'Hub', 'url_name': 'community_hub', 'icon': 'home'},
            {'slug': 'profiles', 'label': 'Profiles', 'url_name': 'profiles_list', 'icon': 'user'},
            {'slug': 'reviews', 'label': 'Reviews', 'url_name': 'reviews_landing', 'icon': 'message-square'},
            {'slug': 'challenges', 'label': 'Challenges', 'url_name': 'challenges_browse', 'icon': 'target'},
            {'slug': 'lists', 'label': 'Lists', 'url_name': 'lists_browse', 'icon': 'list'},
            {'slug': 'leaderboards', 'label': 'Leaderboards', 'url_name': 'overall_badge_leaderboards', 'icon': 'bar-chart'},
        ],
    },
    'my_pursuit': {
        'label': 'My Pursuit',
        'icon': 'trophy',
        'matches': ['/my-pursuit/'],
        'items': [
            {'slug': 'badges', 'label': 'Badges', 'url_name': 'badges_list', 'icon': 'award'},
            {'slug': 'milestones', 'label': 'Milestones', 'url_name': 'milestones_list', 'icon': 'flag'},
            {'slug': 'titles', 'label': 'Titles', 'url_name': 'my_titles', 'icon': 'crown', 'auth_required': True},
        ],
    },
}
```

The `auth_required` flag on individual items filters them out server-side for anonymous users.

## URL Audit

Phase 10a executes the following URL renames. All legacy paths get 301 redirects via `RedirectView(pattern_name=..., permanent=True, query_string=True)` so external links survive. Template `{% url %}` calls do NOT need to change because the URL `name=` parameter stays bound to the new canonical path.

### Achievements → My Pursuit

| Legacy | New |
|--------|-----|
| `/achievements/badges/` | `/my-pursuit/badges/` |
| `/achievements/badges/<slug>/` | `/my-pursuit/badges/<slug>/` |
| `/achievements/milestones/` | `/my-pursuit/milestones/` |
| `/achievements/titles/` | `/my-pursuit/titles/` |
| `/badges/` *(legacy)* | `/my-pursuit/badges/` |
| `/badges/<slug>/` *(legacy)* | `/my-pursuit/badges/<slug>/` |
| `/milestones/` *(legacy)* | `/my-pursuit/milestones/` |
| `/my-titles/` *(legacy)* | `/my-pursuit/titles/` |

The original Phase 10 had moved these to `/achievements/*`. The Phase 10a rework re-renames them to `/my-pursuit/*`. Both the new `/my-pursuit/*` paths AND the legacy `/badges/`, `/milestones/`, `/my-titles/` paths AND the intermediate `/achievements/*` paths all need 301 redirects.

### Tools → Dashboard

| Legacy | New |
|--------|-----|
| `/tools/stats/` | `/dashboard/stats/` |
| `/my-stats/` *(legacy)* | `/dashboard/stats/` |
| `/tools/platinum-grid/` | `/dashboard/shareables/platinum-grid/` *(plus a wizard CTA inside the Shareables landing)* |
| `/staff/platinum-grid/` *(legacy)* | `/dashboard/shareables/platinum-grid/` |
| `/recap/` | `/dashboard/recap/` |
| `/my-shareables/` *(legacy redirect to home)* | `/dashboard/shareables/` *(needs full revival — see [my-pursuit-hub.md](../features/my-pursuit-hub.md))* |

The original Phase 10 had moved Stats and Platinum Grid to `/tools/*`. The Phase 10a rework re-relocates them to `/dashboard/*` because they're personal-cockpit features that belong in the Dashboard hub.

### Customization

| Action | Notes |
|--------|-------|
| Customization menu item | KILLED. The legacy My Pursuit menu had a "Customization" link pointing to `settings`. The avatar dropdown's Settings link already covers this, AND the dashboard's existing "Edit Layout" controls cover module customization. Pure duplication. |

### Unchanged

- `/community/*` — already moved in the original Phase 10, stays as-is
- `/games/*`, `/companies/*`, `/genres/*`, `/themes/*` — already correct, no changes needed
- `/games/flagged/` — already nested correctly
- `/api/v1/*` — never touched (mobile/external clients depend on this)

## Mobile Drawer

The mobile drawer (`templates/partials/mobile_tabbar.html`) mirrors the navbar structure: 3 hub buttons + Dashboard logo. When the drawer opens, each section header is a hub name, and the items below the header are that hub's sub-nav items. So mobile users get one-click access to any sub-page from the drawer, the same way desktop users get one-click access from the persistent sub-nav strip.

The sub-nav strip itself ALSO renders on mobile (as a horizontal scroll strip) so users have two ways to navigate: the drawer (overview) and the strip (in-context).

## Footer

The footer (`templates/partials/footer.html`) gets a 6-column refresh to match the new IA:

| Browse | Community | My Pursuit | Dashboard | Legal | Connect |
|--------|-----------|------------|-----------|-------|---------|
| Games | Hub | Badges | My Profile | Privacy | Social icons |
| Trophies | Profiles | Milestones | My Stats | Terms | (X, YouTube, Discord) |
| Companies | Reviews | Titles* | My Shareables | About | |
| Genres & Themes | Challenges | | Recap | Contact | |
| Flagged Games | Lists | | | | |
| | Leaderboards | | | | |

- Titles link is auth-gated (only shown to authenticated users with a profile)
- Dashboard column is auth-gated; guests see "Account" with Sign In / Sign Up links instead (ensures 6 grid children always)

## Gotchas and Pitfalls

- **Longest-prefix-wins matching is load-bearing.** A path like `/community/profiles/<u>/` MUST match the Community hub, NOT the Dashboard hub via the bare `/` catchall. Implement the matcher with explicit prefix iteration ordered by prefix length descending, OR with a separate "exact equality" check for the bare-root case.

- **Sub-nav must be hidden on non-hub pages.** Pages like `/settings/`, `/auth/login/`, `/notifications/`, error pages, and all `/staff/*` admin pages should NOT render a sub-nav. The context processor returns `hub_section=None` and the template `{% if hub_section %}` short-circuits the include. Test these explicitly.

- **The hub-of-hubs is not a license to add hubs forever.** The design supports 4 hubs because the mental model has 4 modes (cockpit / browse / community / progression). Resist the urge to add a 5th hub when the next initiative ships. Gamification expands the My Pursuit hub's sub-nav; it does NOT get its own top-level menu item. If a future feature genuinely doesn't fit any existing hub, that's a signal to reconsider the IA, not to add a 5th button.

- **"My Pursuit" name reuse risk**: existing users have a mental model where "My Pursuit" = the personal-utility menu (Customization, Recap, etc.). After this initiative, "My Pursuit" becomes the badge/milestone/title hub. The personal-utility items relocate to the Dashboard sub-nav. Mitigation: a one-time "We've reorganized!" callout banner shown to authenticated users for 30 days post-launch, and 301 redirects on every legacy URL so muscle memory still works.

- **Customization removal — verify the audit.** Before merging Phase 10b, manually verify that every customization touchpoint that existed in the old menu is reachable via the avatar dropdown's Settings link OR the dashboard's existing "Edit Layout" / theme controls. If anything is stranded, revive it as a Settings sub-page rather than re-adding the Customization menu item.

- **Sticky chrome stacking.** Four pinned elements at the edges of the viewport: navbar at `sticky top-0 z-50` (64px tall, daisyUI `.navbar` min-h-16), sub-nav at `sticky top-16 z-40` (~46px, flush against the navbar), hotbar at `sticky top-[7.25rem] z-30` (variable height, with ~8px gap above) when inside a hub family — falls back to `top-[4.5rem]` on non-hub pages where the sub-nav is hidden — and on mobile a bottom tab bar at `sticky bottom-0 z-40` (56px). The Tailwind `top-*` classes are good first guesses, but `alignStickyChrome()` in `main.js` measures the actual rendered navbar height on load/resize/fonts-ready and inline-styles the sub-nav and hotbar's `top:` to match. This insulates the layout from font scaling, avatar size changes, and DPI rounding (which can push the real navbar height 1-2px above 64px and cause a visible shift when the sub-nav transitions from in-flow to sticky). The hotbar template branches on `hub_section` for the initial fallback offset. Toast/modal overlays use higher z values (z-50+ for toasts, much higher for modals) and intentionally sit above the navbar.

- **Do NOT inline-style `top:` on `#hotbar-wrapper` from JS.** A pre-sticky-era hack in `hotbar.js` used to set `wrapper.style.top = '0px'` on collapse, which now overrides the JS-managed sticky offset and jams the toggle behind the navbar/sub-nav. The collapse animation must touch ONLY `#hotbar-container.style.maxHeight` and (legitimately) `wrapper.style.marginTop`. The marginTop nudge is fine because it only affects the wrapper's natural-flow position (closing a visible gap when the page is at the top, before sticky kicks in) — sticky positioning ignores margins for its own offset calculation.

- **Sticky kicks in only after scroll, so natural-flow position matters too.** The hotbar wrapper sits inside `<main class="container mx-auto px-4 py-2">`, and the `py-2` adds 8px of padding above its first child. At rest (page at top), sticky is inactive and the wrapper renders at `main_top + 8px`, which puts the toggle 8px below the sub-nav even when the sticky `top:` would have kept them flush. `hotbar.js` applies `wrapper.style.marginTop = '-8px'` in the collapsed state to pull the wrapper back up by exactly that 8px in normal flow. When the user scrolls and sticky kicks in, the marginTop is irrelevant (sticky positions from the viewport, not from the natural flow box), so the same nudge stays correct in both states. When expanded the marginTop is cleared because the 8px pad reads as the intentional breathing-room gap.

- **Mobile sub-nav horizontal scroll affordance.** Without a visual cue (a fade-out gradient on the right edge), users may not realize there's more content to scroll to. Add a subtle right-edge gradient mask when the strip overflows, and ensure the active item auto-scrolls into view on page load.

- **Reverse-name strategy keeps template churn small.** By binding URL `name=` parameters to the NEW canonical paths and adding legacy paths as `RedirectView` entries, no `{% url %}` calls in templates need updating. Test that every existing `{% url %}` call still resolves after the rename — the URL test suite catches this.

- **Power-user latency**: someone who lived in the legacy navbar dropdown ("Community → Reviews", 2 clicks) gets the same 2 clicks in the new IA when navigating from outside (Community link → Reviews tab). When already inside `/community/*`, it's 1 click via the sub-nav. Net: equal from cold, faster from warm.

- **Breadcrumb redundancy is acceptable.** The breadcrumb (`Home > Community > Reviews`) and the sub-nav (with "Reviews" highlighted) both signal "you are here." This is acceptable: the breadcrumb stays for SEO (JSON-LD) and accessibility, the sub-nav is the visual primary. They serve different audiences.

- **The Dashboard hub uses the MODULE TABS for internal organization, NOT for IA navigation.** The dashboard's existing tabs (Default + custom) stay intact as a within-page premium feature for module organization. They are separate from the sub-nav, which is IA-level navigation. Don't conflate them.

- **My Shareables needs full revival, not just a refresh.** The current `/my-shareables/` URL is a 301 redirect to home (the page was deleted at some point). Phase 10b builds a new `/dashboard/shareables/` page from scratch — auditing what assets exist (share image generation, Platinum Grid wizard) and building a landing that surfaces them. This is more work than a "refresh" implies; budget accordingly.

## Related Docs

- [Community Hub](../features/community-hub.md): the Community hub destination at `/community/`
- [My Pursuit Hub](../features/my-pursuit-hub.md): the My Pursuit hub destination at `/my-pursuit/`
- [Dashboard](../features/dashboard.md): the personal cockpit at `/`
- [Navigation](../features/navigation.md): the navbar, footer, mobile drawer, profile tabs, cross-link inventory
- [Template Architecture](../reference/template-architecture.md): base.html, context processors, the hotbar
- [Gamification Vision](../design/gamification-vision.md): the next initiative, which expands the My Pursuit hub's sub-nav and feature grid
