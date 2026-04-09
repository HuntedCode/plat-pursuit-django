# Community Hub

The Community Hub is PlatPursuit's site-wide community destination at `/community/`. Where the dashboard at `/` is "your personal cockpit", the Community Hub is "the front door to everything PlatPursuit's community has to offer". It is a wayfinder + marketing surface that introduces each community feature (the Pursuit Feed, the Review Hub, Challenges, Game Lists, Leaderboards, Discord) with a tagline, a small slice of real signal, and a CTA to its dedicated page.

> **Status**: Phase 7 of the Community Hub initiative is committed (the page exists at `/community/` with the original aggregator design). The current task is to rework Phase 7 into the Feature Spotlight layout described in this doc, plus add the Discord callout, plus widen the page to the standard site container. See the [initiative plan](../../) for the rework checklist.

> **Naming history**: this doc describes the **new** Community Hub feature added during the Community Hub initiative. The pre-existing system that was previously documented as "Community Hub" is the reviews/ratings system, now correctly named the [Review Hub](review-hub.md). The Review Hub is one of the destinations the Community Hub links out to.

## Why this exists

The site redesign made the dashboard the universal landing page, leaning hard into personal stats and per-user customization. This was deliberate, but it left community-shaped content scattered: leaderboards, reviews, challenges, lists, and the Discord/fundraiser/badge hub all lived in disconnected pages with no shared destination. The Community Hub consolidates them.

The split principle: **dashboard = your stuff, community hub = everyone's stuff**. They serve different mental modes, so they take different shapes. The dashboard is a modular cockpit with per-user customization; the hub is a curated destination page that markets community features and points users at the dedicated page for each one.

## Design Philosophy: Feature Spotlight, NOT aggregator

The hub is deliberately NOT a feed-of-feeds. The original Phase 7 commit took the aggregator approach (dump full top-25 leaderboards, full top-10 reviewers, full Pursuit Feed preview, all inline), and that approach was rejected after design review for two reasons:

1. **It dilutes the marketing intent.** A page that exists to *introduce* community features should not also be the canonical surface for *consuming* them. If the full top-25 leaderboard is on the hub, users have no reason to visit the dedicated leaderboard page.
2. **It feels like a feed-of-feeds.** Visitors get walls of data but no clear "what is this place" framing. The hub should sell features, not aggregate them.

The Feature Spotlight design threads the needle: each community feature gets a card that combines marketing (icon, tagline, CTA) with a small data preview (3-5 items of real signal). Repeat visitors still see fresh data so the page never feels dead, but the page never devolves into raw aggregation. Resist the urge to "just show one more thing" inline — every additional inline item dilutes the hub's job and pushes traffic away from the canonical pages.

## Architecture Overview

The Community Hub is a single fixed-layout page (no drag-and-drop, no module library, no per-user customization) composed of feature spotlight cards plus a hero, an optional fundraiser banner, and a permanent Discord callout. Most modules are read-only aggregations; the Pursuit Feed Spotlight at the top is the centerpiece and reads the most recent 3 events from the Event table.

`CommunityHubView` lives in `core/views.py` next to `HomeView` for symmetry. It is a `TemplateView` with `ProfileHotbarMixin`. The page-data assembler is `core/services/community_hub_service.py`, which orchestrates calls to the underlying services (`ReviewHubService`, `redis_leaderboard_service`, `EventService`, `ChallengeService`) and assembles a single context dict.

The standalone full feed at `/community/feed/` is a separate view (`trophies/views/community_views.py:CommunityFeedView`) using the existing `HtmxListMixin` + `browse-filters.js` + `InfiniteScroller` pattern.

## File Map

| File | Purpose |
|------|---------|
| `core/views.py` | `CommunityHubView` (TemplateView, ProfileHotbarMixin) |
| `core/services/community_hub_service.py` | Page-data assembler; calls into `ReviewHubService`, leaderboard services, EventService |
| `trophies/views/community_views.py` | `CommunityFeedView` (HtmxListMixin, ListView) for the standalone full feed |
| `templates/community/hub.html` | Community Hub page template |
| `templates/community/feed.html` | Full feed page template |
| `templates/community/partials/feed_results.html` | HtmxListMixin partial for filter swaps and infinite scroll |
| `templates/community/partials/pursuit_feed_spotlight.html` | The marquee Pursuit Feed promo block at the top of the hub |
| `templates/community/partials/feature_card.html` | Parameterized feature card used by the 2x2 grid (and reusable elsewhere) |
| `templates/community/partials/discord_callout.html` | The permanent Discord callout strip |
| `templates/community/partials/active_fundraiser_banner.html` | Conditional fundraiser banner (when an active fundraiser exists) |
| `templates/trophies/partials/dashboard/built_for_hunters.html` | Site heartbeat ribbon — lifted as-is from the dashboard, used as the hub's hero. Already fully community-flavored. |
| `static/js/community-feed.js` | Lightweight extension to `browse-filters.js` for the Pursuit/Trophy mode toggle (only created if needed beyond the base controller) |
| `plat_pursuit/urls.py` | Routes: `/community/` (community_hub), `/community/feed/` (community_feed) |
| `core/sitemaps.py` | Entries in `StaticViewSitemap.items()` at priority 0.7 |

## Page Anatomy

The hub renders these modules in order, top to bottom. Width: **full site container** (the `container mx-auto` that every other page inherits from `base.html`). The dashboard's `max-w-4xl` wrapper is unique to the dashboard because it's modular/customizable; the hub is a destination page and should match the rest of the site (Review Hub, browse pages, etc.).

### 1. Hero — Site Heartbeat

Copy of `built_for_hunters.html`. Site-wide stats: total trophies, total games, total profiles, 24-hour activity, plus expanded second-tier stats (platinums, badges, XP, hours hunted). Already cached hourly by the existing `refresh_homepage_hourly` cron. Sets the tone, shows the site is alive.

### 2. Active Fundraiser Banner *(conditional)*

When an active fundraiser exists, render a prominent banner here above the Pursuit Feed Spotlight. Urgency + emotional weight justify the high placement. When no fundraiser is active, this section is omitted entirely. Reuses the existing fundraiser context.

### 3. Pursuit Feed Spotlight *(full-width promo block, the marquee new feature)*

The headline new feature gets its own promo block, distinct from the 2x2 feature grid below.

- **Headline**: "Introducing the Pursuit Feed"
- **Subhead**: one-line pitch ("Every platinum, badge, review, and milestone across PlatPursuit, in real time.")
- **Inline preview**: 3 sample event cards (real data, last 3 globally-visible events from `Event.objects.feed_visible().order_by('-occurred_at')[:3]`, ideally varied event types)
- **Hero CTA**: "Explore the Full Feed →" linking to `/community/feed/`
- **Visual treatment**: gradient or accent border to feel marketing-tier, distinct from the cards below

### 4. Feature Grid *(2x2 on `lg:`, 1-col on mobile)*

Each card has icon + feature name + tagline + small data preview + hero CTA. All four cards share the parameterized `feature_card.html` partial.

| Card | Tagline | Data Preview | CTA |
|------|---------|--------------|-----|
| **Review Hub** | "See what hunters are saying about your next platinum." | Top 3 reviewers (avatar + name + helpful count) | "Visit Review Hub" |
| **Challenges** | "Push yourself further. A-Z, Calendar, Genre, and more." | 3 active challenges (icon + title + participants) | "Browse Challenges" |
| **Game Lists** | "Curated by the community. Your next obsession is in here." | 3 most recent published lists (icon + title + author) | "Browse Lists" |
| **Leaderboards** | "Climb the ranks. Compete with hunters worldwide." | Top 5 badge XP (rank + avatar + name + XP) | "View Leaderboards" |

The leaderboard card shows top 5, NOT top 25. The full top-25 leaderboard lives on `/community/leaderboards/badges/` — the hub just teases it.

### 5. Discord Callout *(full-width strip, always shown)*

Invite-style banner with Discord branding, member count if available, and a "Join the Discord" CTA. Treated as a permanent fixture, not feature-gated. Even if Discord member count fetching fails, the callout still renders without the count.

## Standalone Full Feed (`/community/feed/`)

A dedicated page for users who want to deeply browse community activity. **Layout: narrow main column + sticky right rail.** Feeds are inherently linear/list-shaped and feel awful stretched edge-to-edge on a 1500px display. The page adopts the classic Reddit/news-site pattern:

```
[Page Header card]
[2-col flex: main column (max-w-3xl) + right rail (lg:w-80)]
  [Main column]                          [Right Rail (sticky lg:top-20)]
   - Pursuit/Trophy mode toggle           - Filter Controls card
   - Event feed (cards stack vertically)    (event type chips, time range)
   - Infinite scroll sentinel             - "Right Now" mini module
                                            (live event counts last 24h:
                                             X platinums, Y badges, Z reviews)
                                          - Cross-link cards to other
                                            community features
```

The right rail collapses to inline blocks above the main column on `<lg:` (mobile/tablet). On `lg:+` it becomes a sticky sidebar.

Two switchable modes:

- **Pursuit Feed**: every event type in `PURSUIT_FEED_TYPES` (everything but `day_zero`). The default mode.
- **Trophy Feed**: only `TROPHY_FEED_TYPES` (`platinum_earned`, `rare_trophy_earned`, `concept_100_percent`). For users who want pure trophy-watching without review/list/challenge noise.

Mode switching swaps the valid `event_type` filter chip set. The mode itself is a query parameter (`?feed_mode=trophy`) so URLs are bookmarkable.

Filters available in both modes:

| Filter | Type | Values |
|--------|------|--------|
| `feed_mode` | toggle | `pursuit` (default), `trophy` |
| `event_type` | multi-select chips | All types valid for the current mode; multiple selectable |
| `time_range` | radio | `24h`, `7d`, `30d`, `all` (default `7d`) |

The view subclasses `HtmxListMixin, ListView` so filter changes do partial swaps via HTMX (no full page reloads). Pagination uses `InfiniteScroller` for endless scroll. The form has `data-browse-form` and the result container has the standard `#browse-results` ID, so `browse-filters.js` drives the controller without modification (except the mode-toggle JS shim, see below).

### Mode-toggle JS shim

`browse-filters.js` handles the standard filter UX (auto-submit on checkbox change, text submit on Enter). The mode toggle is slightly different: when the user switches Pursuit ↔ Trophy, the **valid event_type chip set changes**. We need to:

1. Hide chips that are not valid for the new mode.
2. Reset any selected chips that are now invalid.
3. Submit the form so the new mode + cleaned filter set takes effect.

This is handled by a small `community-feed.js` extension that listens for change events on the mode radio and updates the chip visibility before delegating the submit to `browse-filters.js`. If `browse-filters.js` already supports a "groupable filter" pattern, the shim is unnecessary; otherwise it lives as a separate small file.

## Per-User Activity Tab

The Community Hub initiative also gives every user profile a new **Activity** tab (the 7th profile tab, alongside Games / Trophies / Badges / Lists / Challenges / Reviews). It reads from the Event table filtered by `profile=target_profile`.

**v1 semantics**: shows ONLY events authored BY the target user (their badges, reviews, platinums, etc.). Does NOT show "events about them" (e.g. someone else replied to their review). This matches the activity-stream metaphor of "what this user has been up to" rather than "what is happening to this user".

The implementation mirrors the existing Reviews tab structure exactly: a `_build_activity_tab_context()` handler in `ProfileDetailView`, an `activity_list_items.html` partial template, `InfiniteScroller` wired to `activity-grid` / `activity-sentinel` / `activity-loading` IDs, and the same `?tab=activity` URL pattern.

See [Navigation](navigation.md#profile-page-tabs) for the full tab table.

## Dashboard Module Replacement

Phase 6 of the initiative replaces the dashboard's `recent_activity` and `recent_platinums` modules with a single new `pursuit_activity` module backed by the Event table.

**The new module is hybrid, not pure-event-backed.** It reads:
1. Event-table events for the current user (`platinum_earned`, `rare_trophy_earned`, `badge_earned`, `milestone_hit`, `review_posted`, etc.)
2. **Plus** a direct `EarnedTrophy` query for trophy-group cards ("8 trophies earned in Persona today")

The trophy-group cards are intentionally kept because the Event table deliberately does NOT track individual non-rare trophies — the system only records platinums, ultra-rare trophies, and 100% completions to keep volume bounded. Without the EarnedTrophy supplement, the dashboard module would lose the existing UX where bronze/silver/gold trophies group by game+day. The hybrid preserves the visual signal while still consolidating around the Event table for everything that warrants its own row.

A data migration rewrites `DashboardConfig` rows that reference the old module slugs to point at `pursuit_activity`, preserving user customization across the rename.

See [Dashboard](dashboard.md) for the full module catalog.

## Personal vs Community Leaderboard Split

The dashboard's `badge_xp_leaderboard` and `country_xp_leaderboard` modules and the Community Hub's leaderboard card **share the same Redis data source** (`redis_leaderboard_service`) but render different views:

| Surface | What it shows | Audience framing |
|---------|---------------|------------------|
| **Dashboard** `badge_xp_leaderboard` (personal slim) | Your rank + neighbors above and below + "View Community Leaderboard" link | "Where do you stand?" |
| **Dashboard** `country_xp_leaderboard` (personal slim) | Same pattern, scoped to your country | "Where do you stand in your country?" |
| **Community Hub** Leaderboards feature card | Top 5 badge XP + "View Leaderboards" CTA to the dedicated page | "Climb the ranks." (marketing) |
| **Existing** `/community/leaderboards/badges/<slug>/` page | Full paginated leaderboard for a specific series | The deep-dive surface |

This split means the user gets community context on the hub AND personal context on the dashboard, without duplicating modules or splitting the data source. `top_developers` (which is already personal — "developers in YOUR library") stays where it is and is NOT split.

The hub used to render top 25 inline (the original Phase 7 commit) but the Feature Spotlight rework drops that to top 5 and makes the dedicated leaderboard page the canonical full-list surface.

## Integration Points

- [Event System](../architecture/event-system.md): the data source for the Pursuit Feed Spotlight, the standalone full feed, the per-user Activity tab, and the dashboard `pursuit_activity` module
- [Review Hub](review-hub.md): provides community review stats via `ReviewHubService`; the new `get_top_reviewers()` method powers the Top Reviewers card
- [Dashboard](dashboard.md): the `pursuit_activity` module replaces `recent_activity` and `recent_platinums`; personal leaderboard modules stay where they are
- [IA and Sub-Nav](../architecture/ia-and-subnav.md): the hub-of-hubs IA design that puts Community as one of three top-level destinations + the sub-nav infrastructure that surfaces sub-pages on every Community page
- [Navigation](navigation.md): the navbar/footer/mobile structure, profile tabs, cross-link inventory
- [Badge System](../architecture/badge-system.md): badge events feed the Pursuit Feed via the post_save sibling receiver; the badge XP leaderboard powers the hub leaderboard card
- [Token Keeper](../architecture/token-keeper.md): the sync pipeline emits trophy/concept events via the `EventCollector` context; see Phase 2 of the initiative

## Gotchas and Pitfalls

- **Community Hub vs Review Hub**: these are two distinct features. The Review Hub (formerly the doc named `community-hub.md`) handles per-game ratings and reviews at `/community/reviews/`. The Community Hub is the new site-wide destination at `/community/` that aggregates many community surfaces, the Review Hub being one of the destinations it links out to. Do not conflate them.

- **The hub is a wayfinder, not an aggregator.** Resist the urge to "just show one more thing" inline. Every additional inline item dilutes the marketing intent and pushes traffic away from the canonical pages each feature already has. Each card should show 3-5 items maximum as a teaser. If a future contributor wants to expand a hub card, they should ask "does this belong on the dedicated page instead?"

- **The hub uses full site container width.** Do not wrap hub sections in `max-w-4xl`. The dashboard does that because it's modular/customizable; the hub is a destination page and should match the rest of the site (Review Hub, browse pages, etc.).

- **The hub is not customizable.** Unlike the dashboard, the Community Hub has a fixed module layout. Resist the urge to add "let users hide modules" or drag-and-drop. The hub is curated; the dashboard is personal. This separation is a deliberate product decision.

- **The Pursuit Feed Spotlight shows ALL globally-visible events**, not just the ones the viewer cares about. There is no follow system in v1. If you build personalized feeds (e.g. "events from people in your country") later, do it as a separate page or a query-string filter on the existing feed page, not as a replacement for the global view.

- **Events are best-effort.** A failed sync can mean a few feed entries are missing. The hub should never break if the Event table is empty or partially populated; the Day Zero event guarantees there is always at least one row to render.

- **`built_for_hunters.html` is shared between dashboard, home shells, and the Community Hub.** It is cached hourly via the existing `refresh_homepage_hourly` cron and silently hides if its cache is empty. If the cron breaks, the entire heartbeat ribbon disappears from all four surfaces; check the cron and the `site_heartbeat_*` Redis keys before assuming the hub is broken.

- **Fixed-layout means no DashboardConfig.** The hub does not register modules in the dashboard module registry, does not read from `DashboardConfig`, does not respect per-user module visibility flags. Rendering is straightforward template composition.

- **Feed page must use narrow main column + right rail.** Feeds at full container width on a 1500px display look stretched and amateur. Cap the event grid at `max-w-3xl`, put filters and the "Right Now" module in a sticky sidebar. Mobile collapses the rail to inline blocks above the feed.

- **HTMX filter swaps preserve query params via `hx-push-url="true"`** so the URL reflects the current filter state and is bookmarkable. Confirm in any feed-page changes that bookmark links to filtered feed views work correctly across the partial-swap boundary.

- **The mode toggle changes the valid event_type chip set.** Switching Pursuit → Trophy must (a) hide non-trophy chips and (b) clear any chip selections that no longer apply, then submit. Test this carefully — the failure mode is that an invalid chip stays selected and the next filter submit returns an empty result with no obvious cause.

- **Top Reviewers is a NEW method on `ReviewHubService`.** Do not try to derive top reviewers from the existing `get_hub_stats()` or `get_trending_reviews()` methods; they aggregate differently. The new method should use the denormalized `Profile.helpful_votes_received` field if it exists, or aggregate from `ReviewVote` rows if not.

- **Discord callout is permanent**, not feature-gated. Even if the Discord member count fetch fails, the callout still renders (without the count). Do not gate the callout behind "is the Discord widget API healthy"; the callout's job is to point users at Discord, and the link works regardless of whether we know the member count.

- **`/community/feed/` is dynamic — no sitemap entry for filtered states**, but the hub landing page (`/community/`) and the standalone feed page URL (without filters, as a stable entry point) DO get sitemap entries at priority 0.7.

- **SEO meta tags must be set on both new pages** via the standard `{% block title %}`, `{% block meta_description %}`, `{% block og_title %}`, `{% block twitter_title %}` patterns from `templates/base.html`. Use the existing `jsonld_breadcrumbs` templatetag with a `breadcrumb` context list. See [SEO Meta Tags](../reference/seo-meta-tags.md).

## Premium Cosmetic Features (Deferred)

The following are explicitly out of scope for the initial Community Hub release. They may ship in a follow-up branch as additive enhancements once the hub is stable in production:

- **Premium feed entry styling**: subtle gold border, premium icon, or animated shimmer on premium users' rows in the Pursuit Feed (think Twitch sub badges, but tasteful). Cheap to add since per-row metadata already includes `actor_profile`; the cosmetic is a CSS class toggle conditional on `actor_profile.user_is_premium`.
- **"Pin a recent achievement"**: premium users can pin one or two of their own recent activity rows to the top of their personal Activity tab for a few days. Lightweight, opt-in, doesn't affect anyone else's experience. Would require a new `PinnedActivityEntry` model or a `metadata['pinned_until']` field on Event with a per-profile uniqueness constraint.

Both ideas are deliberately deferred to keep the initial scope bounded. Do not implement them as part of the Community Hub initiative; revisit in a follow-up planning round once the hub has shipped and we have real usage data.

## Related Docs

- [Event System](../architecture/event-system.md): the technical foundation for every feed surface in the hub
- [Review Hub](review-hub.md): the reviews/ratings system that the hub's Review Hub feature card teases
- [My Pursuit Hub](my-pursuit-hub.md): the personal-progression hub at `/my-pursuit/` (Badges, Milestones, Titles), forward-compatible with the gamification initiative
- [Dashboard](dashboard.md): the personal cockpit at `/`; the `pursuit_activity` module replaces `recent_activity` and `recent_platinums`
- [IA and Sub-Nav](../architecture/ia-and-subnav.md): the hub-of-hubs IA structure that puts Community as one of three top-level destinations
- [Navigation](navigation.md): the navbar, footer, mobile drawer, profile tabs, cross-link inventory
- [Badge System](../architecture/badge-system.md): the source of badge events and badge XP leaderboards
- [Leaderboard System](../architecture/leaderboard-system.md): the Redis-backed leaderboard data source shared between dashboard and hub
- [SEO Meta Tags](../reference/seo-meta-tags.md): the meta tag block conventions for the new pages
