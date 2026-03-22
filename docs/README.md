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
| [Badge System](architecture/badge-system.md) | Badge evaluation, XP, stages, series, milestones |
| [Leaderboard System](architecture/leaderboard-system.md) | Precomputed rankings, caching, rank lookups, dashboard integration |
| [Notification System](architecture/notification-system.md) | 8 services, signals, deferred queue, Discord webhooks, share images |
| [Payment & Webhooks](architecture/payment-webhooks.md) | Stripe + PayPal, subscriptions, donations, webhook routing |
| [Concept Model](architecture/concept-model.md) | Concept sync, absorb(), default concepts, concept lock |
| [Data Model](architecture/data-model.md) | Core model relationships and entity overview |
| [Gamification](architecture/gamification.md) | P.L.A.T.I.N.U.M. stats, XP system, ProfileGamification |

## Design

Long-form vision documents for systems not yet fully implemented.

| Doc | Description |
|-----|-------------|
| [Dashboard Module Catalog](design/dashboard-module-catalog.md) | 28-module roadmap with priority tiers, data sources, and implementation status |
| [Gamification Vision](design/gamification-vision.md) | Full RPG system design: stats, jobs, quests, currency, star chart, avatar frames |
| [Stats Page](design/stats-page.md) | Premium `/my-stats/` page: video game stats screen with 100+ trophy hunting stats |

## Features

Self-contained feature documentation. Read the relevant doc when working on that feature.

| Doc | Description |
|-----|-------------|
| [Checklist System](features/checklist-system.md) | Checklist CRUD, sections, items, voting, sharing |
| [Challenge Systems](features/challenge-systems.md) | A-Z, Calendar, and Genre platinum challenges |
| [Comment System](features/comment-system.md) | Comments, voting, moderation |
| [Community Hub](features/community-hub.md) | Reviews, ratings, concept trophy groups |
| [Dashboard](features/dashboard.md) | Module registry, customization, drag reorder |
| [Easter Eggs](features/easter-eggs.md) | Hidden milestones, titles, and the claim API |
| [Fundraiser](features/fundraiser.md) | Campaign system, donations, badge claims |
| [Game Family](features/game-family.md) | Cross-generation game matching and unification |
| [Monthly Recap](features/monthly-recap.md) | Recap generation, slides, email, share cards |
| [Navigation & Site Organization](features/navigation.md) | Navbar, footer, cross-links, profile tabs |
| [Profile Cards](features/profile-cards.md) | Shareable profile cards, forum signatures, badge showcase |
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
| [Mobile App](guides/mobile-app.md) | Mobile backend API, implementation status |
| [Social Media Strategy](guides/social-media-strategy.md) | Platform strategy, content pillars, calendar, growth tactics |
| [Security](guides/security.md) | Security headers, CSP, rate limiting, CORS, admin log privacy |

## Reference

Quick-lookup tables for mid-task reference.

| Doc | Description |
|-----|-------------|
| [API Endpoints](reference/api-endpoints.md) | All routes, authentication, request/response shapes |
| [Design System](reference/design-system.md) | Site-wide styling tokens, responsive patterns, grid rules, color/contrast reference |
| [JS Utilities](reference/js-utilities.md) | utils.js shared library (API, ToastManager, InfiniteScroller, etc.) |
| [Template Architecture](reference/template-architecture.md) | base.html, zoom wrapper, templatetags, context processors, mixins, themes |
| [Settings Overview](reference/settings-overview.md) | Key Django settings, environment variables, constants files |
| [Redis Keys](reference/redis-keys.md) | Complete key map for raw Redis and Django cache |
| [Shovelware Detection](reference/shovelware-detection.md) | Detection algorithm, thresholds, management commands |
| [Homepage Services](reference/homepage-services.md) | Featured content, What's New, community stats |
| [SEO & Meta Tags](reference/seo-meta-tags.md) | Meta tags, JSON-LD structured data, sitemaps, robots directives |

## Mini-Games

Game design documents for The Arcade system.

| Doc | Description |
|-----|-------------|
| [Development Guide](minigames/DEVELOPMENT_GUIDE.md) | Collaboration principles and working agreement |
| [Implementation Roadmap](minigames/implementation-roadmap.md) | Roadmap for all 25 planned mini-games |
| [Stellar Circuit](minigames/stellar-circuit.md) | Design doc for the Driver mini-game |
