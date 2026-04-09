# Community Hub

The Community Hub is PlatPursuit's site-wide community destination at `/community/`. Where the dashboard at `/` is "your personal cockpit", the Community Hub is "the front door to everything PlatPursuit's community has to offer". It is a wayfinder + marketing surface that introduces each community feature (Reviews, Challenges, Game Lists, Leaderboards, Discord) with a tagline, a small slice of real signal, and a CTA to its dedicated page.

> **Naming history**: this doc describes the **new** Community Hub feature added during the Community Hub initiative. The pre-existing system that was previously documented as "Community Hub" is the reviews/ratings system, now correctly named the [Review Hub](review-hub.md). The Review Hub is one of the destinations the Community Hub links out to.

> **Pursuit Feed deferral**: an earlier iteration of this initiative shipped a Pursuit Feed feature on the hub (a marquee promo block), a standalone `/community/feed/` page, an Activity tab on profile pages, and a hybrid `pursuit_activity` dashboard module powered by a polymorphic `Event` model. All of that was rolled back before reaching production. The architectural design is preserved at [Event System (Deferred)](../architecture/event-system-deferred.md) for future revival; the deferral rationale is documented in that doc's status header.

## Why this exists

The site redesign made the dashboard the universal landing page, leaning hard into personal stats and per-user customization. This was deliberate, but it left community-shaped content scattered: leaderboards, reviews, challenges, lists, and the Discord/fundraiser/badge hub all lived in disconnected pages with no shared destination. The Community Hub consolidates them.

The split principle: **dashboard = your stuff, community hub = everyone's stuff**. They serve different mental modes, so they take different shapes. The dashboard is a modular cockpit with per-user customization; the hub is a curated destination page that markets community features and points users at the dedicated page for each one.

## Design Philosophy: Feature Spotlight + personal hook, NOT aggregator

The hub is deliberately NOT a feed-of-feeds. The original Phase 7 commit took an aggregator approach (dump full top-25 leaderboards, full top-10 reviewers, all inline), and that approach was rejected after design review for two reasons:

1. **It dilutes the marketing intent.** A page that exists to *introduce* community features should not also be the canonical surface for *consuming* them. If the full top-25 leaderboard is on the hub, users have no reason to visit the dedicated leaderboard page.
2. **It feels like a feed-of-feeds.** Visitors get walls of data but no clear "what is this place" framing. The hub should sell features, not aggregate them.

The Feature Spotlight design threads the needle: each community feature gets a card that combines marketing (icon, tagline, CTA) with a small data preview. Repeat visitors still see fresh data so the page never feels dead, but the page never devolves into raw aggregation.

### Split cards (community pulse + personal hook)

Each Feature Grid card is split into **two halves divided by a horizontal rule**:

- **Top half (community pulse)**: a small slice of fresh community signal — 5 most recently reviewed titles, 5 most recent public lists, 5 most recently active challenges, top 5 badge XP. These rows are always padded to 5 slots via `_pad_to_limit` in the service layer; missing rows render as greyed-out placeholders so cards stay the same height regardless of how much real data exists.
- **Bottom half (personal hook)**: viewer-specific stats that connect the community pulse to "what does this mean for me." For authenticated linked viewers it's compact stat tiles (Reviews, Game Lists), a per-type slot grid (Challenges), or a rank + 4 neighbors strip (Leaderboards). For anonymous viewers and authenticated-but-unlinked viewers it's the shared `personal_half_empty.html` partial with a sign-in or link-PSN CTA, sized to match the populated bottom half so the card height stays consistent.

The personal halves do NOT violate the "wayfinder, not aggregator" rule. They answer "how does the community pulse relate to me," which is a *different question* from anything the dashboard surfaces in isolation. The dashboard tells you "your stats." The community hub tells you "your stats *next to* the community pulse." If a future contributor wants to add a card whose personal half just reproduces a dashboard module verbatim, that's a sign the personal half should stay on the dashboard instead.

## Architecture Overview

The Community Hub is a single fixed-layout page (no drag-and-drop, no module library, no per-user customization) composed of feature spotlight cards plus a hero, an optional fundraiser banner, and a permanent Discord callout. All four feature cards are read-only aggregations that pull from existing services (no new data layer was needed).

`CommunityHubView` lives in `core/views.py` next to `HomeView` for symmetry. It is a `TemplateView` with `ProfileHotbarMixin`. The page-data assembler is `core/services/community_hub_service.py`, which orchestrates calls to the underlying services (`ReviewHubService`, `redis_leaderboard_service`, the `Challenge` and `GameList` models directly) and assembles a single context dict.

## File Map

| File | Purpose |
|------|---------|
| `core/views.py` | `CommunityHubView` (TemplateView, ProfileHotbarMixin). Resolves the viewer profile and collapses anonymous / unlinked into a single None signal so the personal-half helpers all branch on one thing. |
| `core/services/community_hub_service.py` | Page-data assembler. Houses the four community-pulse helpers, the four personal-hook helpers, and `_pad_to_limit` (which right-pads each list to `SPOTLIGHT_LIMIT` rows so cards stay visually balanced). |
| `templates/community/hub.html` | Community Hub page template |
| `templates/community/partials/personal_half_empty.html` | Shared empty-state for the personal half of every feature card. Renders a "Sign in" CTA for anonymous viewers and a "Link your PSN" CTA for authenticated-but-unlinked viewers, in a dashed box that matches the populated bottom half's vertical space. |
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

Each card has the same anatomy:

```
[icon + title + tagline]
[5 community rows (padded with placeholders)]
─── divider ───
[personal section (varies per card) OR sign-in/link-PSN empty CTA]
[hero CTA button — pinned to bottom via flex-col + mt-auto]
```

The 2x2 ordering pairs cards by visual shape so each row reads as balanced: row 1 is *title-shaped* cards (concept icons + game titles on the left, a small score on the right) and row 2 is *people-shaped* cards (avatars + usernames on the left, a percentage on the right).

| Row | Card | Tagline | Top half (community) | Bottom half (personal) | CTA |
|-----|------|---------|----------------------|------------------------|-----|
| 1 (left)  | **Review Hub**   | "See what hunters are saying about your next platinum." | 5 most recently reviewed titles (icon + title + review count + recommendation %) | 3 stat tiles: Written / Helpful / Recommend % | "Visit Review Hub" |
| 1 (right) | **Game Lists**   | "Curated by the community. Your next obsession is in here." | 5 most recent published lists (icon + title + author + game count) | 3 stat tiles: Lists / Public / Games | "Browse Lists" |
| 2 (left)  | **Challenges**   | "Push yourself further. A-Z, Calendar, Genre, and more." | 5 active challenges (avatar + author — challenge name + progress %) | Per-type slot grid (A-Z / Calendar / Genre — each row is your latest challenge of that type with progress %, OR a "Start →" placeholder if you haven't started one) | "Browse Challenges" |
| 2 (right) | **Leaderboards** | "Climb the ranks. Compete with hunters worldwide." | Top 5 badge XP (rank + avatar + name + XP) | Your rank + 2 above + 2 below (5 ranked rows, your row highlighted) | "View Leaderboards" |

The leaderboard card top half shows top 5, NOT top 25. The full top-25 leaderboard lives on `/community/leaderboards/badges/` — the hub just teases it. The bottom half shows your rank with 2 neighbors above and below, padded with greyed-out placeholders if you're near the top or bottom of the leaderboard.

All four cards stay the same height regardless of how much real data exists, because both halves are padded to a fixed slot count. The Challenges card's per-type slot grid is the most opinionated bottom half — it ensures all 3 challenge types are always visible to the viewer (with a "Start →" affordance for empty types) instead of the natural-but-uneven shape of "show only the types you've started."

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

- [Review Hub](review-hub.md): the dedicated Reviews destination linked from the hub's Reviews feature card. The card itself shows the 3 most-recently-reviewed titles (deduped by concept, with recommendation %) via `_get_recently_reviewed_titles_spotlight()` in `core/services/community_hub_service.py`. The recommendation percentage math reuses the same formula as `ReviewHubService.get_most_reviewed_games` so the score on the spotlight matches the score on the Review Hub
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

- **The Reviews card shows recently-reviewed titles, not top reviewers and not raw recent reviews.** An earlier iteration sourced this card from `ReviewHubService.get_top_reviewers()`, which filters `total_helpful__gt=0` and excluded any reviewer whose reviews hadn't accumulated helpful votes yet — so the card frequently rendered its empty state on a live site even when fresh reviews existed. A second iteration switched to a raw "most recent reviews" list, but that approach failed two structural tests: it would let three different people reviewing the same hot game take all 3 slots (no deduplication), and it forced reading paragraph excerpts in a card that's meant to be skimmed. The card now groups reviews by concept, orders concepts by `Max(reviews.created_at)`, and shows a recommendation percentage as the at-a-glance score — matching the pattern of every other Feature Spotlight card (`recent_lists`, `active_challenges`) which all show *things* not *people* or *paragraphs*.

- **Each card has BOTH a community half and a personal half.** The personal halves are not aggregator scope creep — they exist because the hub is supposed to answer "how does the community pulse relate to me," which is a different question from anything the dashboard surfaces. If you find yourself wanting to add a personal stat to a card that just reproduces a dashboard module verbatim, that's a sign the stat belongs on the dashboard instead.

- **The viewer profile gate collapses three states into one.** `CommunityHubView` only passes `viewer_profile` to `build_community_hub_context` if the user is authenticated AND has a linked profile. Anonymous viewers, authenticated users with no profile, and authenticated users with an unlinked profile all get `viewer_profile=None`, which propagates to every personal-half helper and triggers the empty CTA. The template separately reads `viewer_is_authenticated` so the CTA partial can pick the right copy ("Sign in" vs "Link your PSN"). If you add a new personal-half helper, follow the same `viewer_profile is None → return None` pattern so the template branching stays uniform.

- **`_pad_to_limit` keeps cards visually balanced.** Every community-pulse helper and the personal XP neighborhood helper pad their return value to `SPOTLIGHT_LIMIT` (5) rows by appending `None` entries. The template iterates the padded list and uses `{% if entry %}` to render either a live row or a greyed-out dashed-border placeholder. This is mostly a dev-machine affordance (placeholders mean fresh installs and design work aren't obscured by partial data); on prod the lists fill naturally. If you bump `SPOTLIGHT_LIMIT`, you don't need to touch the templates — they iterate whatever the helper returns.

- **The Challenges personal half is a fixed 3-row grid, not a list.** Unlike the other personal halves, which are stat tiles or padded leaderboard rows, the Challenges card's personal half always renders ALL THREE challenge types (A-Z / Calendar / Genre) as separate rows with placeholder "Start →" affordances for the types the viewer hasn't started. This is opinionated: it nudges the viewer toward the types they're missing. If you change the challenge type list, update `_get_personal_challenge_slots` in `community_hub_service.py` to match — it carries the type-key, label, and detail-URL-name on each row so the template doesn't hardcode the type list anywhere.

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
