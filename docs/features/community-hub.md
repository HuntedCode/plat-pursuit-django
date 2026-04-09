# Community Hub

The Community Hub is PlatPursuit's site-wide community destination at `/community/`. Where the dashboard at `/` is "your personal cockpit", the Community Hub is "the front door to everything PlatPursuit's community has to offer". It is a wayfinder + marketing surface that introduces each community feature (Reviews, Challenges, Game Lists, Leaderboards, Discord) with a tagline, a small slice of real signal, and a CTA to its dedicated page.

> **Naming history**: this doc describes the **new** Community Hub feature added during the Community Hub initiative. The pre-existing system that was previously documented as "Community Hub" is the reviews/ratings system, now correctly named the [Review Hub](review-hub.md). The Review Hub is one of the destinations the Community Hub links out to.

> **Pursuit Feed deferral**: an earlier iteration of this initiative shipped a Pursuit Feed feature on the hub (a marquee promo block), a standalone `/community/feed/` page, an Activity tab on profile pages, and a hybrid `pursuit_activity` dashboard module powered by a polymorphic `Event` model. All of that was rolled back before reaching production. The architectural design is preserved at [Event System (Deferred)](../architecture/event-system-deferred.md) for future revival; the deferral rationale is documented in that doc's status header.

## Why this exists

The site redesign made the dashboard the universal landing page, leaning hard into personal stats and per-user customization. This was deliberate, but it left community-shaped content scattered: leaderboards, reviews, challenges, lists, and the Discord/fundraiser/badge hub all lived in disconnected pages with no shared destination. The Community Hub consolidates them.

The split principle: **dashboard = your stuff, community hub = everyone's stuff**. They serve different mental modes, so they take different shapes. The dashboard is a modular cockpit with per-user customization; the hub is a curated destination page that markets community features and points users at the dedicated page for each one.

## Design Philosophy: Feature Spotlight, NOT aggregator

The hub is deliberately NOT a feed-of-feeds. The original Phase 7 commit took an aggregator approach (dump full top-25 leaderboards, full top-10 reviewers, all inline), and that approach was rejected after design review for two reasons:

1. **It dilutes the marketing intent.** A page that exists to *introduce* community features should not also be the canonical surface for *consuming* them. If the full top-25 leaderboard is on the hub, users have no reason to visit the dedicated leaderboard page.
2. **It feels like a feed-of-feeds.** Visitors get walls of data but no clear "what is this place" framing. The hub should sell features, not aggregate them.

The Feature Spotlight design threads the needle: each community feature gets a card that combines marketing (icon, tagline, CTA) with a small data preview (3-5 items of real signal). Repeat visitors still see fresh data so the page never feels dead, but the page never devolves into raw aggregation. Resist the urge to "just show one more thing" inline — every additional inline item dilutes the hub's job and pushes traffic away from the canonical pages.

## Architecture Overview

The Community Hub is a single fixed-layout page (no drag-and-drop, no module library, no per-user customization) composed of feature spotlight cards plus a hero, an optional fundraiser banner, and a permanent Discord callout. All four feature cards are read-only aggregations that pull from existing services (no new data layer was needed).

`CommunityHubView` lives in `core/views.py` next to `HomeView` for symmetry. It is a `TemplateView` with `ProfileHotbarMixin`. The page-data assembler is `core/services/community_hub_service.py`, which orchestrates calls to the underlying services (`ReviewHubService`, `redis_leaderboard_service`, the `Challenge` and `GameList` models directly) and assembles a single context dict.

## File Map

| File | Purpose |
|------|---------|
| `core/views.py` | `CommunityHubView` (TemplateView, ProfileHotbarMixin) |
| `core/services/community_hub_service.py` | Page-data assembler; calls into `ReviewHubService`, leaderboard services, and the Challenge / GameList models |
| `templates/community/hub.html` | Community Hub page template |
| `templates/trophies/partials/dashboard/built_for_hunters.html` | Site heartbeat ribbon — lifted as-is from the dashboard, used as the hub's hero. Already fully community-flavored. |
| `plat_pursuit/urls.py` | Route: `/community/` (community_hub) |
| `core/sitemaps.py` | Entry in `StaticViewSitemap.items()` at priority 0.7 |

## Page Anatomy

The hub renders these modules in order, top to bottom. Width: **full site container** (the `container mx-auto` that every other page inherits from `base.html`). The dashboard's `max-w-4xl` wrapper is unique to the dashboard because it's modular/customizable; the hub is a destination page and should match the rest of the site (Review Hub, browse pages, etc.).

### 1. Hero — Site Heartbeat

Copy of `built_for_hunters.html`. Site-wide stats: total trophies, total games, total profiles, 24-hour activity, plus expanded second-tier stats (platinums, badges, XP, hours hunted). Already cached hourly by the existing `refresh_homepage_hourly` cron. Sets the tone, shows the site is alive.

### 2. Active Fundraiser Banner *(conditional)*

When an active fundraiser exists, render a prominent banner here above the feature grid. Urgency + emotional weight justify the high placement. When no fundraiser is active, this section is omitted entirely. Reuses the existing fundraiser context.

### 3. Feature Grid *(2x2 on `lg:`, 1-col on mobile)*

Each card has icon + feature name + tagline + small data preview + hero CTA.

| Card | Tagline | Data Preview | CTA |
|------|---------|--------------|-----|
| **Review Hub** | "See what hunters are saying about your next platinum." | Top 3 reviewers (avatar + name + helpful count) | "Visit Review Hub" |
| **Challenges** | "Push yourself further. A-Z, Calendar, Genre, and more." | 3 active challenges (icon + title + participants) | "Browse Challenges" |
| **Game Lists** | "Curated by the community. Your next obsession is in here." | 3 most recent published lists (icon + title + author) | "Browse Lists" |
| **Leaderboards** | "Climb the ranks. Compete with hunters worldwide." | Top 5 badge XP (rank + avatar + name + XP) | "View Leaderboards" |

The leaderboard card shows top 5, NOT top 25. The full top-25 leaderboard lives on `/community/leaderboards/badges/` — the hub just teases it.

### 4. Discord Callout *(full-width strip, always shown)*

Invite-style banner with Discord branding, member count if available, and a "Join the Discord" CTA. Treated as a permanent fixture, not feature-gated. Even if Discord member count fetching fails, the callout still renders without the count.

## Personal vs Community Leaderboard Split

The dashboard's `badge_xp_leaderboard` and `country_xp_leaderboard` modules and the Community Hub's leaderboard card **share the same Redis data source** (`redis_leaderboard_service`) but render different views:

| Surface | What it shows | Audience framing |
|---------|---------------|------------------|
| **Dashboard** `badge_xp_leaderboard` (personal slim) | Your rank + neighbors above and below + "View Community Leaderboard" link | "Where do you stand?" |
| **Dashboard** `country_xp_leaderboard` (personal slim) | Same pattern, scoped to your country | "Where do you stand in your country?" |
| **Community Hub** Leaderboards feature card | Top 5 badge XP + "View Leaderboards" CTA to the dedicated page | "Climb the ranks." (marketing) |
| **Existing** `/community/leaderboards/badges/<slug>/` page | Full paginated leaderboard for a specific series | The deep-dive surface |

This split means the user gets community context on the hub AND personal context on the dashboard, without duplicating modules or splitting the data source. `top_developers` (which is already personal — "developers in YOUR library") stays where it is and is NOT split.

## Integration Points

- [Review Hub](review-hub.md): provides community review stats via `ReviewHubService`; the `get_top_reviewers()` method powers the Top Reviewers card
- [Dashboard](dashboard.md): the hub is the community-side counterpart to the dashboard's personal-cockpit role
- [IA and Sub-Nav](../architecture/ia-and-subnav.md): the hub-of-hubs IA design that puts Community as one of three top-level destinations + the sub-nav infrastructure that surfaces sub-pages on every Community page
- [Navigation](navigation.md): the navbar/footer/mobile structure, profile tabs, cross-link inventory
- [Badge System](../architecture/badge-system.md): the badge XP leaderboard powers the hub leaderboard card
- [Event System (Deferred)](../architecture/event-system-deferred.md): the rolled-back Pursuit Feed feature that an earlier iteration of this initiative built; preserved as a reference for future revival

## Gotchas and Pitfalls

- **Community Hub vs Review Hub**: these are two distinct features. The Review Hub (formerly the doc named `community-hub.md`) handles per-game ratings and reviews at `/community/reviews/`. The Community Hub is the new site-wide destination at `/community/` that aggregates many community surfaces, the Review Hub being one of the destinations it links out to. Do not conflate them.

- **The hub is a wayfinder, not an aggregator.** Resist the urge to "just show one more thing" inline. Every additional inline item dilutes the marketing intent and pushes traffic away from the canonical pages each feature already has. Each card should show 3-5 items maximum as a teaser. If a future contributor wants to expand a hub card, they should ask "does this belong on the dedicated page instead?"

- **The hub uses full site container width.** Do not wrap hub sections in `max-w-4xl`. The dashboard does that because it's modular/customizable; the hub is a destination page and should match the rest of the site (Review Hub, browse pages, etc.).

- **The hub is not customizable.** Unlike the dashboard, the Community Hub has a fixed module layout. Resist the urge to add "let users hide modules" or drag-and-drop. The hub is curated; the dashboard is personal. This separation is a deliberate product decision.

- **`built_for_hunters.html` is shared between dashboard, home shells, and the Community Hub.** It is cached hourly via the existing `refresh_homepage_hourly` cron and silently hides if its cache is empty. If the cron breaks, the entire heartbeat ribbon disappears from all surfaces; check the cron and the `site_heartbeat_*` Redis keys before assuming the hub is broken.

- **Top Reviewers is a method on `ReviewHubService`.** Do not try to derive top reviewers from the existing `get_hub_stats()` or `get_trending_reviews()` methods; they aggregate differently. The `get_top_reviewers()` method aggregates `helpful_count` per profile.

- **Discord callout is permanent**, not feature-gated. Even if the Discord member count fetch fails, the callout still renders (without the count). Do not gate the callout behind "is the Discord widget API healthy"; the callout's job is to point users at Discord, and the link works regardless of whether we know the member count.

- **The hub used to render top 25 leaderboards inline** (the original Phase 7 aggregator commit). The Feature Spotlight rework drops that to top 5 and makes the dedicated leaderboard page the canonical full-list surface. Do not re-introduce the top 25 inline view.

- **SEO meta tags must be set** via the standard `{% block title %}`, `{% block meta_description %}`, `{% block og_title %}`, `{% block twitter_title %}` patterns from `templates/base.html`. Use the existing `jsonld_breadcrumbs` templatetag with a `breadcrumb` context list. See [SEO Meta Tags](../reference/seo-meta-tags.md).

- **No dedicated Pursuit Feed surface.** An earlier iteration of this initiative built one and rolled it back. If you find yourself wanting to add a "live activity feed" to the hub, read [Event System (Deferred)](../architecture/event-system-deferred.md) first to understand the design space and the reasons for the deferral. A revived feed feature should be its own initiative, not a hub addition.

## Related Docs

- [Review Hub](review-hub.md): the reviews/ratings system that the hub's Review Hub feature card teases
- [My Pursuit Hub](my-pursuit-hub.md): the personal-progression hub at `/my-pursuit/` (Badges, Milestones, Titles), forward-compatible with the gamification initiative
- [Dashboard](dashboard.md): the personal cockpit at `/`
- [IA and Sub-Nav](../architecture/ia-and-subnav.md): the hub-of-hubs IA structure that puts Community as one of three top-level destinations
- [Navigation](navigation.md): the navbar, footer, mobile drawer, profile tabs, cross-link inventory
- [Badge System](../architecture/badge-system.md): the source of badge XP leaderboards
- [Leaderboard System](../architecture/leaderboard-system.md): the Redis-backed leaderboard data source shared between dashboard and hub
- [Event System (Deferred)](../architecture/event-system-deferred.md): the rolled-back Pursuit Feed design, preserved for future revival
- [SEO Meta Tags](../reference/seo-meta-tags.md): the meta tag block conventions for the new pages
