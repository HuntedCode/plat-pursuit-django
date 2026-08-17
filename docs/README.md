# PlatPursuit Documentation

Central documentation hub for the PlatPursuit platform. All system documentation lives here, organized by audience and purpose.

**When to update docs**: Any time a system is created, modified, or extended, the corresponding doc must be updated in the same PR. See [CLAUDE.md](../CLAUDE.md) for the full documentation maintenance mandate.

**Creating new docs**: Copy [TEMPLATE.md](TEMPLATE.md) and fill in the sections that apply. Not every section is required, but **Gotchas and Pitfalls** is always mandatory.

---

## Architecture

Cross-cutting engine systems. Read these to understand how the core platform works.

| Doc | Description |
|-----|-------------|
| [Token Keeper & Sync Pipeline](architecture/token-keeper.md) | PSN sync engine, job queues, workers, rate limiting, PSN API |
| [Badge System](architecture/badge-system.md) | Series x platform edition, the evaluation engine, XP, stages |
| [Leaderboard System](architecture/leaderboard-system.md) | The Postgres standing tables every board reads; rank-equals-position |
| [Notification System](architecture/notification-system.md) | HIDDEN pending rebuild: 8 services, signals, deferred queue, Discord webhooks, share images |
| [Payment & Webhooks](architecture/payment-webhooks.md) | Stripe + PayPal, subscriptions, donations, webhook routing |
| [Concept Model](architecture/concept-model.md) | Concept sync, absorb(), default concepts, concept lock |
| [Data Model](architecture/data-model.md) | Core model relationships and entity overview |
| [Event System (Deferred)](architecture/event-system-deferred.md) | DEFERRED. Polymorphic Event model and Pursuit Feed design, rolled back before reaching production. Preserved for future revival. |
| [Gamification](architecture/gamification.md) | The two sealed XP economies: badge XP and Contract/job XP |
| [IA and Sub-Nav](architecture/ia-and-subnav.md) | Hub-of-hubs IA, sub-navigation infrastructure, URL prefix matching, hub configuration |

## Design

Strategic-identity docs and long-form vision documents for systems not yet fully implemented. The product-identity and visual-identity docs are the test that every product/visual decision should pass.

| Doc | Description |
|-----|-------------|
| [Product Identity](design/product-identity.md) | Strategic identity, 2-minute pitch, IA, hub model, naming, supporting pillars. The strategic frame every other doc serves. |
| [Rebuild Playbook & Progress](design/rebuild/rebuild-playbook.md) | **Start here for any page rebuild.** Tracks which pages are done (only Career/Collection/Badges) and captures the shared decisions every rebuilt page inherits: stacked structure, header card, segmented tabs, the depth-pass surface ladder, toolbars, premium motion, HTMX swaps, modals. Indexes the authoritative docs. |
| [Career: Rebuild Reference Standard](design/rebuild/career-reference-standard.md) | **The finished-quality bar for the site-wide rebuild.** Every rebuilt page is measured against Career's polish + coherence: design tokens, mobile-first fit, premium motion, performance, interaction, URL/state — plus the "what would Google/Apple do here?" polishing lens. |
| [Leaderboards: Section Rebuild](design/rebuild/leaderboards-rebuild.md) | **PLAN.** Full section rebuild: boards live ON their entity, the hub becomes a discovery layer of thin directories (Global / Game / Badge / Job). Country is a FILTER not a board; badge XP is renamed Badge Points to separate it from Career XP; `/jobs/` + job detail land in Browse. Includes the backend audit's findings and the Lane B cutover sequencing. |
| [Visual Identity](design/visual-identity.md) | Visual constitution: brief, six adjectives, four signature primitives (Frame, Pursuer Card, Horizon, Tally), Surfaces category (Binder first, more to come), anti-references. The test every visual decision must pass. |
| [Visual Identity References](design/visual-identity-references.md) | Curated real-world references for each primitive (trading-card chrome, identity-card design, progress UIs, number-as-reward) plus PSN-era and explorer's-office moods. Working doc for sketching/mood-boarding. |
| [Binder Surface](design/binder-surface.md) | **SUPERSEDED** by the [Badge Medallion Case](reference/badge-medallion.md) on `/collection/` (2026-07). Retained as the `/design/binder/` design lab reference: six views, 3D page-flip, technical learnings. |
| [Gamification Plan](design/gamification-plan.md) | Layered Phase 1 to 4+ rollout for the Pursuer + Job system. Phase 1 ships the core loop; later phases earn their place through engagement data. |
| [Dashboard Module Catalog](design/dashboard-module-catalog.md) | 28-module roadmap with priority tiers, data sources, and implementation status |
| [Data Intelligence](design/data-intelligence.md) | The flagship premium *value* arc: one per-profile insight engine, three interfaces (insight engine → My Stats drill-down → conversational companion). Whale-safe via materialized pre-compute. |
| [Premium = Membership](design/rebuild/premium-proposal.md) | Premium direction: support-led membership (not a paywall), four buckets, v1 = positioning/storefront/flair, value flagships on a published roadmap. |
| [Platinum Journey](design/platinum-journey.md) | Premium "patient companion": **Phase C of the Data Intelligence arc** — the insight engine spoken rather than charted; knows the user, helps plan their next pursuits |
| [Stats Page](design/stats-page.md) | Premium `/my-stats/` page: video game stats screen with 100+ trophy hunting stats |

## Features

Self-contained feature documentation. Read the relevant doc when working on that feature.

| Doc | Description |
|-----|-------------|
| [Badge Art Reveal](features/badge-art-reveal.md) | Community platinum-driven badge-artwork reveal event: site-wide progress banner + event page (carousel + grid), art auto-released as the community earns badge platinums |
| [Challenge Systems](features/challenge-systems.md) | **RETIRED 2026-08** (design reference for the planned rewrite): A-Z, Calendar, and Genre platinum challenges |
| [Comment System (Legacy)](features/comment-system.md) | Read-only legacy: surviving moderation/vote endpoints, why no new comments are accepted |
| [Community Flags](features/community-flags.md) | User-submitted game data quality flags (delisted, shovelware, VR, buggy trophies) |
| [Community Hub](features/community-hub.md) | Retired 2026-08: what the hub was and where each piece went |
| [Community Trophy Tracker](features/community-trophy-tracker.md) | Daily Discord post summarizing prev-day community trophy stats from Discord-linked profiles, with all-time records and a weighted PP Score |
| [Company System](features/company-system.md) | Developer / publisher browse + detail pages, IGDB-grouped games, user progress summary |
| [Dashboard](features/dashboard.md) | Deleted 2026-08: what it was, why it went, what survived |
| [Easter Eggs](features/easter-eggs.md) | Hidden finds + the server-side roll API (the title award retired with the legacy milestone engine) |
| [Engine System](features/engine-system.md) | Game engine browse + detail pages (shared `tag_detail.html` with Genre/Theme), game-detail engine link-out |
| [Franchise System](features/franchise-system.md) | Franchise / collection browse + detail pages, main vs. tie-in partitioning, user progress summary |
| [Fundraiser](features/fundraiser.md) | Campaign system, donations, badge claims |
| [Game Family](features/game-family.md) | Cross-generation game matching and unification |
| [Game Leaderboards](features/game-leaderboards.md) | Per-game Ranks tab: completion ranking with first-to-finish tie-break, keyset pagination, jump-to-my-rank; why it is DB-only and lazy-loaded |
| [Game Ratings Tab](features/game-ratings.md) | Per-game Ratings tab: aggregate conditions card, per-quality tiles, Your take, quick-take blurbs + moderation/guidelines; the deferred "blurbs at scale" cluster |
| [Home Page Router](features/home-page.md) | Smart `/` router: anonymous, no-PSN, syncing, and synced shells |
| [Monthly Recap](features/monthly-recap.md) | Recap generation, slides, email, share cards |
| [My Pursuit Hub](features/my-pursuit-hub.md) | Personal-progression hub at `/my-pursuit/`: badges, milestones, titles (forward-compatible with gamification) |
| [Navigation & Site Organization](features/navigation.md) | Navbar, footer, sub-nav, cross-links, profile tabs |
| [Profile Cards](features/profile-cards.md) | Shareable profile cards, forum signatures, badge showcase |
| [Profile Showcases](features/profile-showcases.md) | Steam-style customization: pick showcase types to feature on your profile |
| [Review Hub](features/review-hub.md) | Reviews, ratings, concept trophy groups (formerly "Community Hub") |
| [Roadmap System](features/roadmap-system.md) | Staff-authored platinum guides on game detail (replaces the legacy checklist system) |
| [Roadmap Roles, Locks & Revisions](features/roadmap-roles-and-revisions.md) | Role-based authoring (writer/editor/publisher), guide-level edit lock, branch-and-merge save flow, permanent revision history |
| [Share Images](features/share-images.md) | Playwright renderer, caching, card types |
| [Subscription Lifecycle](features/subscription-lifecycle.md) | Activation, cancellation, renewal, admin dashboard |

## Guides

How-to and operational documentation.

| Doc | Description |
|-----|-------------|
| [Local Setup](guides/local-setup.md) | Docker, environment variables, development workflow |
| [Management Commands](guides/management-commands.md) | All 55+ commands across 4 apps |
| [Cron Jobs](guides/cron-jobs.md) | Scheduled tasks: what runs when, dependencies |
| [Email Setup](guides/email-setup.md) | SendGrid configuration, Cloudflare email routing |
| [Mobile App](guides/mobile-app.md) | Why the mobile API was removed, and what to know when rebuilding it |
| [Social Media Strategy](guides/social-media-strategy.md) | Platform strategy, content pillars, calendar, growth tactics |
| [Security](guides/security.md) | Security headers, CSP, rate limiting, CORS, admin log privacy |
| [Staging / Beta](guides/staging.md) | Staff-only `beta.platpursuit.com`: Render + DNS + env setup, the `BETA` gate |

## Reference

Quick-lookup tables for mid-task reference.

| Doc | Description |
|-----|-------------|
| [API Endpoints](reference/api-endpoints.md) | All routes, authentication, request/response shapes |
| [Design System](reference/design-system.md) | Site-wide styling tokens, responsive patterns, grid rules, color/contrast reference |
| [Premium Motion Patterns](reference/motion-patterns.md) | How premium motion reads premium: principles (traveling light, restraint) + CSS recipes/gotchas (fade-don't-pop, glow-above-image, no-FOUC reveals, clip breathing room, count-ups) |
| [Frame Component](reference/frame-component.md) | Badge chrome primitive: partial, CSS, JS controller, Earn Moment, sizes / states / tiers, reduced-motion |
| [Badge Medallion + Case](reference/badge-medallion.md) | The shipped `/collection/` presentation: badge as a precious medallion OBJECT (4 states, segmented ring), the series-grouped Case, and the fetch-on-tap detail. Supersedes the Frame + binder on the collection. |
| [JS Utilities](reference/js-utilities.md) | utils.js shared library (API, ToastManager, InfiniteScroller, etc.) |
| [Template Architecture](reference/template-architecture.md) | base.html, zoom wrapper, templatetags, context processors, mixins, themes |
| [Settings Overview](reference/settings-overview.md) | Key Django settings, environment variables, constants files |
| [Rarity](reference/rarity.md) | The one rarity model: grading, the ratchet, and the shared tint+material component |
| [Redis Keys](reference/redis-keys.md) | Complete key map for raw Redis and Django cache |
| [Shovelware Detection](reference/shovelware-detection.md) | Detection algorithm, thresholds, management commands |
| [Homepage Services](reference/homepage-services.md) | Featured content, What's New, community stats |
| [SEO & Meta Tags](reference/seo-meta-tags.md) | Meta tags, JSON-LD structured data, sitemaps, robots directives |
| [Stats Page Inventory](reference/stats-page-inventory.md) | Quick reference of every stat displayed on `/my-stats/` (12 sections, 120+ stats) |

## Mini-Games

Game design documents for The Arcade system.

| Doc | Description |
|-----|-------------|
| [Development Guide](minigames/DEVELOPMENT_GUIDE.md) | Collaboration principles and working agreement |
| [Implementation Roadmap](minigames/implementation-roadmap.md) | Roadmap for all 25 planned mini-games |
| [Stellar Circuit](minigames/stellar-circuit.md) | Design doc for the Driver mini-game |
