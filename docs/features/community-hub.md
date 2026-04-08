# Community Hub

The Community Hub is PlatPursuit's site-wide community destination at `/community/`. Where the dashboard at `/` is "your personal cockpit", the Community Hub is "what's happening across PlatPursuit right now". It aggregates community-wide signal — the global Pursuit Feed, full leaderboards, top reviewers, active challenges, fundraiser activity — into a single curated page that gives users a clear front door to every social and competitive surface on the site.

> **Status**: planned. Implementation lands in Phase 7 of the Community Hub initiative; the supporting Event infrastructure ships in Phases 1-4. Until the initiative ships, `/community/` does not exist as a route. See [the Event System architecture doc](../architecture/event-system.md) for the technical foundation.

> **Naming history**: this doc describes the **new** Community Hub feature added during the Community Hub initiative. The pre-existing system that was previously documented as "Community Hub" is the reviews/ratings system, now correctly named the [Review Hub](review-hub.md). The Review Hub is one of the destinations the Community Hub links out to.

## Why this exists

The site redesign made the dashboard the universal landing page, leaning hard into personal stats and per-user customization. This was deliberate, but it left community-shaped content scattered: leaderboards, reviews, challenges, lists, and the Discord/fundraiser/badge hub all lived in disconnected pages with no shared destination. The Community Hub consolidates them.

The split principle: **dashboard = your stuff, community hub = everyone's stuff**. Some surfaces appear on both (e.g. badge XP leaderboards) but in different presentations: the dashboard shows your rank and your neighbors, the hub shows the full top 25 and the global picture. Same data source, two views, no drift.

## Architecture Overview

The Community Hub is a single fixed-layout page (no drag-and-drop, no module library, no per-user customization) composed of curated modules that read from a mix of existing services and the new Event system. Most modules are read-only aggregations; the Pursuit Feed preview module is the centerpiece and reads from the Event table directly.

`CommunityHubView` lives in `core/views.py` next to `HomeView` for symmetry. It is a `TemplateView` with `ProfileHotbarMixin`. The page-data assembler is `core/services/community_hub_service.py`, which orchestrates calls to the underlying services (`ReviewHubService`, `redis_leaderboard_service`, `EventService`, etc.) and assembles a single context dict.

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
| `templates/community/partials/*.html` | Per-module partials for the hub (feed preview, leaderboards, top reviewers, active challenges, fundraiser callout) |
| `templates/trophies/partials/dashboard/built_for_hunters.html` | Site heartbeat ribbon — lifted as-is from the dashboard, used as the hub's hero. Already fully community-flavored. |
| `static/js/community-feed.js` | Lightweight extension to `browse-filters.js` for the Pursuit/Trophy mode toggle (only created if needed beyond the base controller) |
| `plat_pursuit/urls.py` | New routes: `/community/` (community_hub), `/community/feed/` (community_feed) |
| `core/sitemaps.py` | New entries in `StaticViewSitemap.items()` at priority 0.7 |

## Page Anatomy

The hub renders these modules in order, top to bottom. All are present for everyone (no conditional visibility unless noted); user-specific touches like "your rank" only appear when logged in.

1. **Hero / Site Heartbeat** — copy of `built_for_hunters.html`. Site-wide stats: total trophies, total games, total profiles, 24-hour activity, plus expanded second-tier stats (platinums, badges, XP, hours hunted). Already cached hourly by the existing `refresh_homepage_hourly` cron.
2. **Pursuit Feed Preview** — last 10 globally-visible events from `Event.objects.feed_visible().order_by('-occurred_at')[:10]`, mixing event types. Each row shows actor avatar, action verb, target object link, relative time. "View Full Feed →" CTA links to `/community/feed/`.
3. **Full Badge XP Leaderboard** — top 25 globally from `redis_leaderboard_service.get_xp_top(25)`. If the viewer is logged in and has a profile, their rank shows as a sticky row at the top with "you are #N" framing. Click-through goes to the existing `/leaderboard/badges/` page (which becomes `/community/leaderboards/badges/` after the URL audit). Same Redis source as the dashboard's personal `badge_xp_leaderboard` module — see [Personal vs Community Leaderboard Split](#personal-vs-community-leaderboard-split) below.
4. **Full Country Leaderboard** — top 25 in viewer's country (only shown when logged in AND `profile.country` is set). Same dual-presentation pattern.
5. **Top Reviewers** — top 10 reviewers by helpful votes received. New `ReviewHubService.get_top_reviewers(limit=10)` method. Each row links to that reviewer's profile.
6. **Active Challenges** — recent `challenge_started` and `challenge_completed` events from the Event table, capped at 12. Mixes recently-started and recently-completed for a sense of what challenges the community is engaging with.
7. **Fundraiser callout** — only shown when an active fundraiser exists. Reuses the existing fundraiser context.

The page does NOT include personal modules (no "your dashboard" preview, no "your stats", no recent badges of your own). Those belong on the dashboard.

## Standalone Full Feed (`/community/feed/`)

A dedicated page for users who want to deeply browse community activity. Two switchable modes:

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

The Community Hub also gives every user profile a new **Activity** tab (the 7th profile tab, alongside Games / Trophies / Badges / Lists / Challenges / Reviews). It reads from the Event table filtered by `profile=target_profile`.

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

The dashboard's `badge_xp_leaderboard` and `country_xp_leaderboard` modules and the Community Hub's leaderboard modules **share the same Redis data source** (`redis_leaderboard_service`) but render different views:

| Surface | What it shows | Audience framing |
|---------|---------------|------------------|
| **Dashboard** `badge_xp_leaderboard` (personal slim) | Your rank + neighbors above and below + "View Community Leaderboard" link | "Where do you stand?" |
| **Dashboard** `country_xp_leaderboard` (personal slim) | Same pattern, scoped to your country | "Where do you stand in your country?" |
| **Community Hub** `full_badge_xp_leaderboard` | Top 25 globally, with viewer's rank as a sticky row if logged in | "Who's leading the community?" |
| **Community Hub** `full_country_leaderboard` | Top 25 in viewer's country, only shown when logged in + has country | Same |
| **Existing** `/leaderboard/badges/<slug>/` page | Full paginated leaderboard for a specific series | The deep-dive surface |

This split means the user gets community context on the hub AND personal context on the dashboard, without duplicating modules or splitting the data source. `top_developers` (which is already personal — "developers in YOUR library") stays where it is and is NOT split.

## Integration Points

- [Event System](../architecture/event-system.md): the data source for the Pursuit Feed preview, the standalone full feed, the per-user Activity tab, and the dashboard `pursuit_activity` module
- [Review Hub](review-hub.md): provides community review stats via `ReviewHubService`; the new `get_top_reviewers()` method powers the Top Reviewers module
- [Dashboard](dashboard.md): the `pursuit_activity` module replaces `recent_activity` and `recent_platinums`; personal leaderboard modules stay where they are
- [Navigation](navigation.md): the Community menu becomes a clickable destination, with Hub + Pursuit Feed as the first two items in the dropdown
- [Badge System](../architecture/badge-system.md): badge events feed the Pursuit Feed via the post_save sibling receiver; the badge XP leaderboard powers the hub leaderboard module
- [Token Keeper](../architecture/token-keeper.md): the sync pipeline emits trophy/concept events via the `EventCollector` context; see Phase 2 of the initiative

## Gotchas and Pitfalls

- **Community Hub vs Review Hub**: these are two distinct features. The Review Hub (formerly the doc named `community-hub.md`) handles per-game ratings and reviews at `/reviews/`. The Community Hub is the new site-wide destination at `/community/` that aggregates many community surfaces, the Review Hub being one of the destinations it links out to. Do not conflate them.

- **The hub is not customizable.** Unlike the dashboard, the Community Hub has a fixed module layout. Resist the urge to add "let users hide modules" or drag-and-drop. The hub is curated; the dashboard is personal. This separation is a deliberate product decision.

- **The Pursuit Feed preview shows ALL globally-visible events**, not just the ones the viewer cares about. There is no follow system in v1. If you build personalized feeds (e.g. "events from people in your country") later, do it as a separate page or a query-string filter on the existing feed page, not as a replacement for the global view.

- **Events are best-effort.** A failed sync can mean a few feed entries are missing. The hub should never break if the Event table is empty or partially populated; the Day Zero event guarantees there is always at least one row to render.

- **`built_for_hunters.html` is shared between dashboard, home shells, and the Community Hub.** It is cached hourly via the existing `refresh_homepage_hourly` cron and silently hides if its cache is empty. If the cron breaks, the entire heartbeat ribbon disappears from all four surfaces; check the cron and the `site_heartbeat_*` Redis keys before assuming the hub is broken.

- **Fixed-layout means no DashboardConfig.** The hub does not register modules in the dashboard module registry, does not read from `DashboardConfig`, does not respect per-user module visibility flags. Rendering is straightforward template composition.

- **HTMX filter swaps preserve query params via `hx-push-url="true"`** so the URL reflects the current filter state and is bookmarkable. Confirm in Phase 8 that bookmark links to filtered feed views work correctly across the partial-swap boundary.

- **The mode toggle changes the valid event_type chip set.** Switching Pursuit → Trophy must (a) hide non-trophy chips and (b) clear any chip selections that no longer apply, then submit. Test this carefully — the failure mode is that an invalid chip stays selected and the next filter submit returns an empty result with no obvious cause.

- **Top Reviewers is a NEW method on `ReviewHubService`.** Do not try to derive top reviewers from the existing `get_hub_stats()` or `get_trending_reviews()` methods; they aggregate differently. The new method should use the denormalized `Profile.helpful_votes_received` field if it exists, or aggregate from `ReviewVote` rows if not.

- **`/community/feed/` is dynamic — no sitemap entry for the feed itself**, but the hub landing page (`/community/`) and the standalone feed page URL (without filters, as a stable entry point) DO get sitemap entries at priority 0.7.

- **SEO meta tags must be set on both new pages** via the standard `{% block title %}`, `{% block meta_description %}`, `{% block og_title %}`, `{% block twitter_title %}` patterns from `templates/base.html`. Use the existing `jsonld_breadcrumbs` templatetag with a `breadcrumb` context list. See [SEO Meta Tags](../reference/seo-meta-tags.md).

## Premium Cosmetic Features (Deferred)

The following are explicitly out of scope for the initial Community Hub release. They may ship in a follow-up branch as additive enhancements once the hub is stable in production:

- **Premium feed entry styling**: subtle gold border, premium icon, or animated shimmer on premium users' rows in the Pursuit Feed (think Twitch sub badges, but tasteful). Cheap to add since per-row metadata already includes `actor_profile`; the cosmetic is a CSS class toggle conditional on `actor_profile.user_is_premium`.
- **"Pin a recent achievement"**: premium users can pin one or two of their own recent activity rows to the top of their personal Activity tab for a few days. Lightweight, opt-in, doesn't affect anyone else's experience. Would require a new `PinnedActivityEntry` model or a `metadata['pinned_until']` field on Event with a per-profile uniqueness constraint.

Both ideas are deliberately deferred to keep the initial scope bounded. Do not implement them as part of the Community Hub initiative; revisit in a follow-up planning round once the hub has shipped and we have real usage data.

## Related Docs

- [Event System](../architecture/event-system.md): the technical foundation for every feed surface in the hub
- [Review Hub](review-hub.md): the reviews/ratings system that the hub's Top Reviewers and Reviews navigation links point at
- [Dashboard](dashboard.md): the `pursuit_activity` module that replaces `recent_activity` and `recent_platinums`
- [Navigation](navigation.md): the new 4-menu IA structure that puts Community as a clickable top-level destination
- [Badge System](../architecture/badge-system.md): the source of badge events and badge XP leaderboards
- [Leaderboard System](../architecture/leaderboard-system.md): the Redis-backed leaderboard data source shared between dashboard and hub
- [SEO Meta Tags](../reference/seo-meta-tags.md): the meta tag block conventions for the new pages
