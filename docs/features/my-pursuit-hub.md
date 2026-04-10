# My Pursuit Hub

The My Pursuit Hub is PlatPursuit's personal-progression and recognition section at `/my-pursuit/`. It is one of the four top-level hubs in the [hub-of-hubs IA](../architecture/ia-and-subnav.md) (Dashboard / Browse / Community / My Pursuit). Its sub-nav contains Badges, Milestones, and Titles. After the gamification initiative ships, the same hub absorbs the full RPG layer (Logbook, Quests, Star Chart, Arcade, Stellar Market) without requiring another IA shuffle.

> **Status**: shipped. The URL namespace is live, the Badges page acts as the hub landing, and the sub-nav strip surfaces Milestones and Titles on every `/my-pursuit/*` page.

## Why "My Pursuit"

The original navbar had a 4-menu structure: Browse / Community / Achievements / My Pursuit. "Achievements" held Badges, Milestones, Titles. "My Pursuit" held the personal-utility menu (Customization, Recap, My Lists, My Challenges, My Profile, My Shareables, My Stats, Platinum Grid). Both menus had problems:

- **"Achievements" was undersized** (3 items) and undersold the section's potential. The next major initiative — gamification — adds an entire RPG layer (P.L.A.T.I.N.U.M. stats, 25 Jobs, dual leveling, Quests, Stellar Marks currency, Stellar Market store, Mini-game Arcade, Star Chart constellation map, customizable avatar frames) that conceptually belongs in the same section. Renaming "Achievements" to something forward-compatible avoids a second IA pivot when gamification ships.
- **"My Pursuit" as a menu was duplication.** Every personal-utility item in that menu had a natural home on the dashboard or under a hub sub-nav. Keeping the menu meant the dashboard's job (the personal cockpit) was being done by both the dashboard and the menu, which is exactly the kind of redundancy the hub-of-hubs IA is trying to eliminate.

The cleanest resolution: **kill "My Pursuit" as a personal-utility menu, and reuse the name for the renamed Achievements hub.** The personal-utility items (Stats, Shareables, Recap) relocate to the Dashboard hub's sub-nav, where they always belonged. The "My Pursuit" name elevates to the personal-progression hub it always *should* have meant. This:

- Capitalizes on existing brand equity ("My Pursuit" is already in the user vocabulary)
- Survives the gamification expansion ("My Pursuit > Quests" reads naturally; "Achievements > Quests" does not)
- Avoids introducing new vocabulary
- Frees the personal-utility pages to live in the right place (Dashboard sub-nav)

The cost is a one-time relearning ("the menu items moved"), mitigated by 301 redirects on every legacy URL and the [Tutorial System](../design/tutorial-system.md): a Welcome Tour runs once on first PSN-link to introduce each hub, and a Badge Detail Tour (coach marks) walks new users through badge series mechanics on their first badge detail page visit.

## Why there's no dedicated landing page

`/my-pursuit/` is a 301 redirect to `/my-pursuit/badges/`. **The Badges page IS the hub landing**; there is no separate landing page for the section.

This is deliberate. With only 3 sub-pages (Badges, Milestones, Titles), a dedicated landing would be a wayfinder for almost nothing — and Badges is by far the headline feature, the thing most users are coming to this section to use. Building a landing page that says "look at all 3 things in this section" when one of them is the obvious destination is overkill. The sub-nav strip handles wayfinding to Milestones and Titles on every Badges page view, so users discover them naturally.

This mirrors the [Browse hub](../architecture/ia-and-subnav.md) decision: `/games/` IS the Browse landing, the sub-nav handles wayfinding to Trophies/Companies/Genres/Themes/Flagged Games. Both hubs follow the same pattern: when one sub-page is the clear headline, that sub-page is the landing.

**When this might change**: once the [gamification initiative](../design/gamification-vision.md) ships and the My Pursuit sub-nav grows to 8+ items (Logbook / Star Chart / Quests / Arcade / Market / Badges / Milestones / Titles), a dedicated landing page becomes worth building. At that point there will be enough breadth to *introduce* and the section will benefit from a proper wayfinder. For v1 (3 items), the redirect-to-Badges approach is the right shape.

## File Map

| File | Purpose |
|------|---------|
| `plat_pursuit/urls.py` | The `my-pursuit/` redirect (`name='my_pursuit_hub'`) plus the Badges/Milestones/Titles sub-page routes |
| `core/hub_subnav.py` | `MY_PURSUIT_HUB` sub-nav config (Badges / Milestones / Titles) |
| `templates/partials/hub_subnav.html` | The sticky sub-nav strip rendered on every `/my-pursuit/*` page |
| (no view file) | The hub itself is a `RedirectView` — no view code, no template |

## Sub-Pages (Sub-Nav Items)

The hub's sub-nav strip surfaces these pages on every `/my-pursuit/*` URL:

| Sub-nav Item | URL | View | Auth |
|--------------|-----|------|------|
| Badges | `/my-pursuit/badges/` | `BadgeListView` | Public |
| Milestones | `/my-pursuit/milestones/` | `MilestoneListView` | Public |
| Titles | `/my-pursuit/titles/` | `MyTitlesView` | Auth-required |

The Titles sub-nav item is conditionally rendered (only shown to authenticated users with a profile) because `MyTitlesView` uses `LoginRequiredMixin`. This gating is enforced server-side in the sub-nav config via the `auth_required=True` flag — see [IA and Sub-Nav](../architecture/ia-and-subnav.md) for the filtering mechanism.

There is no `/my-pursuit/` sub-nav item itself because `/my-pursuit/` is just a redirect to Badges; the Badges sub-nav item is the active item when you're on either `/my-pursuit/` (mid-redirect) or `/my-pursuit/badges/` (post-redirect).

## Forward Compatibility: Gamification

The hub is named and structured to absorb the gamification initiative without another IA shuffle. When gamification ships, the sub-nav grows to:

| Sub-nav Item | URL | Source |
|--------------|-----|--------|
| Logbook | `/my-pursuit/logbook/` | New (Hunter Profile / Explorer's Logbook) |
| Star Chart | `/my-pursuit/star-chart/` | New (constellation progression map) |
| Quests | `/my-pursuit/quests/` | New (daily/weekly/epic quests) |
| Arcade | `/my-pursuit/arcade/` | Migration of `/arcade/stellar-circuit/` and future mini-games |
| Market | `/my-pursuit/market/` | New (Stellar Market store) |
| Badges | `/my-pursuit/badges/` | Existing |
| Milestones | `/my-pursuit/milestones/` | Existing |
| Titles | `/my-pursuit/titles/` | Existing |

That's 8 items at full bloom — the upper edge of the comfort zone but workable. When the section grows that wide, the case for a dedicated `/my-pursuit/` landing page (instead of the current redirect to Badges) gets stronger because there's more breadth to introduce. At that point a hub landing with feature cards becomes worth building. Until then, redirect-to-Badges is correct.

See [Gamification Vision](../design/gamification-vision.md) for the full RPG system design that this hub will host.

## URL Audit

Phase 10a renamed the following URLs. All legacy paths get 301 redirects via `RedirectView(pattern_name=..., permanent=True, query_string=True)` so external links survive.

| Legacy | New |
|--------|-----|
| `/badges/` | `/my-pursuit/badges/` |
| `/badges/<series_slug>/` | `/my-pursuit/badges/<series_slug>/` |
| `/badges/<series_slug>/<psn_username>/` | `/my-pursuit/badges/<series_slug>/<psn_username>/` |
| `/milestones/` | `/my-pursuit/milestones/` |
| `/my-titles/` | `/my-pursuit/titles/` |
| `/achievements/badges/` | `/my-pursuit/badges/` |
| `/achievements/badges/<series_slug>/` | `/my-pursuit/badges/<series_slug>/` |
| `/achievements/milestones/` | `/my-pursuit/milestones/` |
| `/achievements/titles/` | `/my-pursuit/titles/` |

The intermediate `/achievements/*` paths (committed in the original Phase 10) are also redirected because Phase 10a re-renamed them to `/my-pursuit/*`. Both pre-Phase-10 legacy paths AND the Phase 10 intermediate paths now point at the new canonical homes.

The reverse-name strategy keeps existing `{% url 'badges_list' %}` and `reverse('badges_list')` calls working without churn — only the canonical path changes, not the URL name.

## Integration Points

- [IA and Sub-Nav](../architecture/ia-and-subnav.md): the hub-of-hubs IA structure that puts My Pursuit as one of three top-level destinations + the sub-nav infrastructure
- [Badge System](../architecture/badge-system.md): the underlying source of badge data, progress tracking, and the badge views that act as the hub landing + first sub-nav item
- [Gamification](../architecture/gamification.md): the existing gamification scaffolding (`ProfileGamification`, `StatType`, `StageStatValue`) that the next initiative will build on top of
- [Gamification Vision](../design/gamification-vision.md): the full RPG system that this hub will host after the next initiative ships
- [Tutorial System](../design/tutorial-system.md): Welcome Tour (hub intro) + Badge Detail Tour (coach marks on badge pages)
- [Community Hub](community-hub.md): the parallel hub design (community discovery vs personal progression)
- [Dashboard](dashboard.md): the personal cockpit at `/` that surfaces personal-utility features as modules; the My Pursuit hub is for structured progression pages, the dashboard is for the modular cockpit

## Gotchas and Pitfalls

- **The hub is forward-compatible by design.** When the gamification initiative ships, it expands the My Pursuit sub-nav — it does NOT introduce a new top-level menu. Resist the urge to add a "Gamification" or "Hunter" hub later; the My Pursuit name was chosen specifically to avoid that.

- **No dedicated landing page (yet).** `/my-pursuit/` is a 301 redirect to `/my-pursuit/badges/`. If you're tempted to build a landing page for the section, first check whether the section has grown beyond ~5 sub-items. If not, the Badges page is still the right landing. The redirect target lives in `plat_pursuit/urls.py` under `name='my_pursuit_hub'`, so when the day comes to build a landing page, change the URL pattern from a `RedirectView` to a real `TemplateView` and the navbar button (which targets `name='my_pursuit_hub'`) automatically points at the new landing without any template changes.

- **"My Pursuit" name reuse risk**: existing users have a mental model where "My Pursuit" = the personal-utility menu (Customization, Recap, etc.). After this initiative, "My Pursuit" became the badge/milestone/title hub. The personal-utility items relocated to the Dashboard sub-nav. Mitigation: the planned [Tutorial System](../design/tutorial-system.md) Welcome Tour will explicitly introduce each hub on first PSN-link, and every legacy URL (`/my-stats/`, `/my-shareables/`, `/recap/`) 301-redirects to its new home so muscle memory still works.

- **Sub-nav active state for `/my-pursuit/badges/<slug>/`**: the sub-nav strip should highlight "Badges" as active even on the badge detail page. The URL prefix matcher in the [sub-nav infrastructure](../architecture/ia-and-subnav.md) handles this via longest-prefix matching plus an explicit URL-name override map (`badge_detail` → `('my_pursuit', 'badges')`).

- **Titles sub-nav item is auth-gated.** `MyTitlesView` uses `LoginRequiredMixin`. The sub-nav config filters items by `auth_required` server-side so anonymous users don't see a Titles tab that would 302 them to login.

- **Sitemap entries**: the headline sub-pages (`/my-pursuit/badges/`, `/my-pursuit/milestones/`) are in the sitemap. `/my-pursuit/` itself is NOT in the sitemap because it's a permanent redirect — search engines should index `/my-pursuit/badges/` directly. Titles is auth-only and is excluded from the sitemap.

- **The Customization menu item is gone.** The old My Pursuit menu had a "Customization" link that pointed to `settings`. That URL still exists (the avatar dropdown's Settings link points to it) and the dashboard's existing "Edit Layout" controls cover the in-page customization affordances. Phase 10a killed the redundant Customization menu entry and verified that every customization touchpoint is reachable via Settings or the dashboard.

## Related Docs

- [IA and Sub-Nav](../architecture/ia-and-subnav.md): the hub-of-hubs IA structure
- [Community Hub](community-hub.md): the sibling hub for community discovery
- [Dashboard](dashboard.md): the personal cockpit at `/` (where the old My Pursuit menu items relocated to)
- [Badge System](../architecture/badge-system.md): the source of badge data
- [Gamification](../architecture/gamification.md): the existing gamification scaffolding
- [Gamification Vision](../design/gamification-vision.md): the full RPG system this hub will host
- [Tutorial System](../design/tutorial-system.md): Welcome Tour (hub intro) + Badge Detail Tour (coach marks on badge pages)
- [Navigation](navigation.md): the navbar, footer, mobile drawer, sub-nav structure
