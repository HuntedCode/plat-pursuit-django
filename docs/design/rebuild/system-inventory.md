# System Inventory — Gamification Rebuild

> **Purpose.** A complete, honest catalogue of every system PlatPursuit has built, so we can look at each piece on its own and decide where it fits in the gamification-first direction. This is the first foundation artifact of the rebuild: it makes the "too many moving parts" problem **finite and visible**, and it marks the dead weight for deletion so the codebase comes out lighter.
>
> **Status:** v1 draft (2026-06-07), assembled from a parallel code audit (not just docs). Dispositions below are **proposals for review**, not decisions. Companion docs to come: the design/product **charter** (the destination) and the **conversion playbook** (the test-gated process).

## How to read this

Each system has: what it is, where it lives, real status (**code reality**, not doc claims), coupling, and a **proposed disposition**:

| Disposition | Meaning |
|---|---|
| **Keep** | Already serves the new direction; leave it (maybe minor tweaks) |
| **Adapt** | Reorient it toward the Pursuer / badge spine |
| **Absorb** | Fold it into a new gamification surface; the standalone form goes away |
| **Retire** | Sunset / delete |
| **Build** | Net-new; does not exist yet (the north-star spine) |

Status vocabulary: **Shipped** · **Partial** · **Scaffold-only** (schema exists, no data/logic) · **Legacy/read-only** · **Archived** · **Workshop-only** (locked design, not extracted) · **Design-only** · **Dead** (UI gone, tables retained).

---

## The coupling spine (why "separate parts" isn't fully separable)

Almost every system FKs into one of these. Touch them carelessly and the blast radius is the whole app. The inventory treats them as **load-bearing infrastructure that the rebuild preserves**, not as conversion targets:

- **`Concept`** + **`Concept.absorb()`** — the cross-platform game identity. `absorb()` migrates 15 relationship types on reassignment; *any new model FK'd to Concept must update it* (CLAUDE.md contract).
- **`Profile`** — the user. Hangs onto gamification, showcases, cards, sync state.
- **`Badge` / `Stage` / `UserBadgeProgress`** — the moat artifact and its progress.
- **`Game` / `Trophy` / `EarnedTrophy` / `ProfileGame`** — the PSN data axis.
- **Sync pipeline (`token_keeper`)** — the engine that detects completion and fires badge/XP/milestone/challenge evaluation in `_job_sync_complete`.
- **IGDB enrichment** — the metadata layer that powers developer badges, genre challenges, covers, and (future) Job tag-derivation.

---

## A. Engine / Foundation (the crown jewels)

These are the hard-won, battle-tested backend. **All Keep.** The rebuild does not reorient these; it builds on them.

### Token Keeper & Sync Pipeline
- **What:** Single-process multi-threaded PSN sync engine; OAuth token pool, 5-priority Redis job queues, full lifecycle from trophy fetch to badge/milestone/challenge evaluation.
- **Lives in:** `trophies/token_keeper.py` (~2.7k lines), `psn_manager.py`, `services/psn_api_service.py`, `sync_utils.py`.
- **Status:** Shipped, mature. Doc-aligned.
- **Coupling:** Central hub. Drives Concept reassignment, IGDB enrich deferral, badge/milestone/challenge eval, deferred notifications. Everything profile-related flows through it.
- **Disposition:** **Keep** — crown jewel; rebuild must preserve. Possible later: rationalize the 9 job types (only after profiling).

### Concept Model + `absorb()`
- **What:** Logical game identity across platforms/regions; `absorb()` migrates all related data when a concept is orphaned.
- **Lives in:** `trophies/models.py` (Concept ~893-1358; absorb ~1060-1315), `concept_anchor_service.py`, `concept_split_service.py`, `concept_trophy_group_service.py`.
- **Status:** Shipped, mature, doc-aligned.
- **Coupling:** Depended on by nearly everything (Game, Trophy, Badge stages, challenges, ratings, roadmaps, IGDB enrichment, GameFamily).
- **Disposition:** **Keep** — core abstraction. *Recommendation:* add a `manage.py check` that verifies every model FK'd to Concept is handled in `absorb()` (turn the CLAUDE.md contract into an enforced check).

### IGDB Integration
- **What:** 10-strategy matching pipeline + enrichment (developers, genres, themes, engines, franchises, time-to-beat, covers, release dates). Each Concept matches independently.
- **Lives in:** `trophies/services/igdb_service.py` (~3.2k lines), `IGDBMatch` + enrichment through-models, ~15 management commands.
- **Status:** Shipped; ~89% concept coverage. CJK unlock + GameFamily IGDB-id keying + rematch sweep all live.
- **Coupling:** Fed by sync; consumed by badges (dev badges), challenges (genre), browse, stats, covers. `absorb()` migrates enrichment only on `inherit_match`.
- **Disposition:** **Keep** — core enrichment. *Note:* time-to-beat + engine data stored but under-displayed (future UI). **Job tag-derivation (Phase 1) depends on this taxonomy.**

### Game Family
- **What:** Groups Concepts across platforms/regions by IGDB id (deterministic, one family per IGDB game). Preserves separate Concepts (unlike absorb).
- **Lives in:** `GameFamily` model, `igdb_service._link_concept_to_family` / `_resolve_canonical_igdb_id`.
- **Status:** Shipped; Apr 2026 moved from heuristic matcher to IGDB-id determinism (old service + proposal model removed).
- **Coupling:** Created by enrichment, inherited by absorb, consumed by game_grouping_service + cross-gen badge stages (future).
- **Disposition:** **Keep** — deterministic keying = no UI debt. *Recommendation:* periodic orphan/drift audit command.

### Badge System (internals)
- **What:** Tiered (Bronze/Silver/Gold/Platinum) collections of Concepts via Stages; XP per completed concept + bonus per badge; 6 badge types; ConceptBundles for episodic games.
- **Lives in:** `models.py` (Badge, Stage, UserBadge, UserBadgeProgress, ConceptBundle), `services/badge_service.py`.
- **Status:** Shipped, mature. O(n) eval via prefetch; prerequisite chains enforced.
- **Coupling:** Fires in sync `_job_sync_complete`; feeds XP, leaderboards, Discord roles, titles, milestones, notifications. **The centerpiece.**
- **Disposition:** **Keep + Adapt** — stable framework, but this is *the* surface the Pursuer/Job layer wraps. Adapt = extend (Job XP derivation), not rewrite.

### Leaderboard System
- **What:** Redis sorted-set leaderboards (badge earners, progress, XP — per-series/global/per-country). Incremental signal updates + cron reconciliation.
- **Lives in:** `services/redis_leaderboard_service.py` (~1k lines), `leaderboard_service.py`, `update_leaderboards` cron.
- **Status:** Shipped. O(log n) lookups, 7h TTL.
- **Coupling:** Reads ProfileGamification XP; updated on UserBadge change + sync. Surfaced on dashboard, badge detail, profiles.
- **Disposition:** **Keep** — display layer for badges+XP. *Recommendation:* alert if last rebuild >1h stale. Gets per-Job leaderboards when Jobs ship.

### Core Data Model
- **What:** The relational schema across 6 apps; Profile→Game→Trophy axis with Concept as unifier.
- **Status:** Shipped, stable, no drift. Denormalized counters via signals; soft-delete patterns; JSONFields for flexible data.
- **Disposition:** **Keep** — mature and extensible; no schema refactor needed for the rebuild. Risks: counter drift (audit commands), opaque JSONFields.

---

## B. Gamification & Progression (the north-star spine)

This is the heart of the rebuild. **The foundation (Badge XP) is shipped and solid. The Pursuer layer does not exist yet.**

### Badge XP system (ProfileGamification + xp_service + signals)
- **What:** Real-time XP calc/denormalization from badge progress; `total_badge_xp`, `series_badge_xp`. `bulk_gamification_update()` defers recalc during sync.
- **Lives in:** `models.py` ProfileGamification (~2051-2085), `services/xp_service.py` (~342 lines), `signals.py` (~242-341).
- **Status:** **Shipped, live.** Only the XP layer is real.
- **Disposition:** **Keep** — load-bearing; the Pursuer's numeric foundation. Will gain fields (`job_xp`, `job_levels`, title slots) when Jobs ship — extend, don't replace.

### StatType / StageStatValue (P.L.A.T.I.N.U.M. scaffold)
- **What:** Schema for future per-stage stat values. One live `StatType` record (`badge_xp`); `StageStatValue` empty.
- **Status:** **Scaffold-only** — inert, no data, not read by any service/view.
- **Disposition:** **Adapt** — keep the tables; this is where stage→Job XP mappings will live once the Job system is designed. Do not extend until then.

### StageCompletionEvent
- **What:** Records (profile, badge, stage, concept, completed_at) for temporal badge analytics (recaps, "when did I earn X").
- **Status:** Shipped, managed by badge eval.
- **Disposition:** **Keep** — foundational for temporal queries.

### Milestones & Titles
- **What:** 30+ criteria types; milestones award equippable Titles. Easter eggs are manual-type milestones.
- **Lives in:** `models.py` (~2304-2408), `services/milestone_service.py`, `milestone_handlers.py`.
- **Status:** Shipped; evaluated on sync.
- **Disposition:** **Keep** (adapt) — the title/milestone infra is exactly where Job-level titles ("Apprentice Driver" → "Master Driver") will plug in.

### Easter Eggs
- **What:** Currently one live egg (knife-landing in the reel spinner → "Unboxed!" milestone + "Case Hardened" title). Server-side roll + claim, idempotent.
- **Status:** Shipped.
- **Disposition:** **Keep** — good proof-of-concept pattern for discovery moments; extensible.

### Stats Page (`/my-stats/`)
- **What:** 12-section stats screen (120+ stats); staff-gated, launching public by swapping the mixin.
- **Lives in:** `services/stats_service.py` + `profile_stats_service.py`, `views/stats_views.py`, async HTML fetch.
- **Status:** Shipped (staff-gated). Pure read layer, 4h cache.
- **Disposition:** **Keep** — add a Job-XP section when Jobs ship. Async-load pattern reusable.

### ❗ Jobs system — **does not exist**
- **What (planned):** Player specializations (Driver, Detective, …) auto-derived from IGDB tags on badge stages; per-Job levels + XP; **Pursuer Level = sum of Job levels.**
- **Status:** **Design-only** (gamification-plan.md Phase 1). No model, no logic.
- **Disposition:** **Build** — the core mechanic of the Pursuer; non-negotiable for Phase 1. Blocked by the Job-catalog re-derivation against IGDB taxonomy (open thread).

### ❗ Pursuer / Logbook / Badge Gallery / Pursuit home — **do not exist**
- **What (planned):** The Pursuer identity; `/logbook/` deep-dive; the Badge Gallery (Binder); the new `/` that replaces the Dashboard.
- **Status:** **Design-only** (plan + locked workshops).
- **Disposition:** **Build** — Phase 1 surfaces. Depend on extracting the Tally/Horizon/Pursuer Card/Binder workshops (§D).

### Supporting design/deferred artifacts
- **Gamification Plan** (`docs/design/gamification-plan.md`) — the **spec**. Disposition: the source of truth for Phase 1 scope.
- **Event System / Pursuit Feed** — built then rolled back (migration `0188_drop_event`); design preserved in `event-system-deferred.md`. Disposition: **Archive** (do not revive until a feed is committed).
- **Platinum Journey** (`docs/design/platinum-journey.md`) — premium companion, design-only, deferred. Disposition: **Archive** (post-rebuild premium).

---

## C. Surfaces & Presentation (where the rebuild concentrates)

### Dashboard (41-module registry)
- **What:** The current synced `/` landing; module registry, lazy load, drag reorder, per-module settings, custom tabs (premium).
- **Lives in:** `services/dashboard_service.py`, `views/dashboard_views.py`, `static/js/dashboard.js`, `templates/trophies/dashboard.html`.
- **Status:** Shipped, mature.
- **Disposition:** **Absorb / Retire-as-home** — the plan is explicit that Pursuit home replaces the Dashboard. The *module infrastructure + providers* are reusable display assets; the **dashboard-as-the-home-page** goes away. Modules redistribute to Logbook / Pursuit home / Stats. (See the design-system note below — this surface is also what our design system is anchored to.)

### Home Page Router (`/`)
- **What:** 4-state smart router (anonymous / no-PSN / syncing / synced→dashboard); auto-reload on sync transition.
- **Lives in:** `core/views.py` HomeView, `templates/home/*`, `hotbar.js`.
- **Status:** Shipped, mature.
- **Disposition:** **Adapt** — keep the state machine; re-point the `synced` state from the Dashboard to the new Pursuit home; rework the syncing state into the "Your Pursuer is emerging" high-conversion moment.

### My Pursuit Hub (`/my-pursuit/`)
- **What:** Hub namespace (currently redirects to `/my-pursuit/badges/`); sub-nav over Badges/Milestones/Titles.
- **Status:** Shipped; forward-compatible.
- **Disposition:** **Adapt** — grows to host Logbook (+ later Star Chart/Quests/Arcade/Market). Landing likely shifts from BadgeListView to a purpose-built hub/Logbook view.

### IA & Sub-Nav
- **What:** Hub-of-hubs (Dashboard/Browse/Community/My Pursuit) + URL-prefix-matched sub-nav strips.
- **Lives in:** `core/hub_subnav.py`, `context_processors.hub_subnav`, `partials/hub_subnav.html`.
- **Status:** Shipped.
- **Disposition:** **Keep / Adapt** — extend sub-nav as gamification features ship; the top-level hub set will shift as Dashboard retires and Pursuit becomes home.

### Navigation (navbar / mobile tab bar / footer / profile tabs)
- **Status:** Shipped; updated for hub-of-hubs.
- **Disposition:** **Keep** — the spine; touch only to add/reorder items and to surface Pursuer Level/Title on profile tabs.

### Template Architecture (base.html, context processors, templatetags, mixins)
- **Status:** Shipped/solid (base.html, 5 context processors, 10+ templatetags, 6 mixins). ZoomScaler wrapper present but inert unless activated.
- **Disposition:** **Keep** (base.html + processors + mixins); **remove** the ZoomScaler CSS rules once the last page migrates.

### ZoomScaler (legacy transform-scale)
- **What:** Legacy sub-768px fallback; scales the whole page via CSS transform.
- **Status:** **Legacy/read-only** — only **one** page still uses it (`stellar-circuit.html`).
- **Disposition:** **Retire** — migrate stellar-circuit to mobile-first Tailwind, then delete `.zoom-active` CSS + the ZoomScaler class. (Low effort, removes a whole legacy subsystem.)

### Design System (`docs/reference/design-system.md`) — ⚠️ the key tension
- **What:** Site-wide tokens, card anatomy, responsive patterns, component blueprints.
- **Status:** Shipped/locked, **derived from the Dashboard redesign** — i.e. anchored to the surface being retired.
- **Disposition:** **Adapt (re-anchor)** — this is the most important presentation-layer decision. The tokens are good and mostly survive, but the system needs to be re-grounded on the new center of gravity (Pursuit home + Logbook + Badge Gallery, built natively in the visual identity) rather than the Dashboard. Section 4 (Tokens) was deliberately deferred until these surfaces are designed — that's the moment to open it. **This is what the design/product charter must resolve.**

### JS Utilities (`utils.js`) + page-specific JS
- **What:** ~900-line shared lib (API, ToastManager, InfiniteScroller, DragReorderManager, etc.) + ~40 feature JS files.
- **Status:** utils.js shipped/stable. Feature files mixed: some modern (frame.js, browse-filters.js), some predate the redesign.
- **Disposition:** **Keep** utils.js (extend the namespace pattern for new primitive controllers); **Keep + Audit** feature JS — build new primitive controllers (Tally/Horizon/Pursuer Card) fresh in frame.js's style, don't extend old files; prune confirmed-dead files during conversion.

### Tutorial System (3 tours) — REMOVED
- **What:** Welcome Tour (hub nav) + Game Detail + Badge Detail coach-mark tours; per-user dismissal.
- **Status:** **Removed** in the chrome rebuild (the welcome tour's chrome clone had broken against the rebuilt navbar; all three were legacy). The `Profile.*_tour_completed_at` fields remain as orphaned columns pending a drop.
- **Disposition:** Rebuild from scratch if/when onboarding is revisited.

### Profile Cards & Forum Signatures
- **What:** Shareable profile PNGs (social + forum sig) via Playwright (social) / pre-rendered (sig).
- **Status:** Shipped; reads ProfileGamification, badges, titles, leaderboard ranks.
- **Disposition:** **Keep** — already badge/XP-aware; will reflect Pursuer Level/Jobs naturally.

### Profile Showcases (Steam-style)
- **What:** Pick showcase types to feature on profile; 2 free + 5 premium slots; registry/provider pattern; downgrade handling.
- **Status:** Shipped. Challenge showcase scaffolded but deferred (no provider).
- **Disposition:** **Keep + Expand** — natural home for a future Job/Pursuer showcase.

---

## D. Visual Identity Kit (the "primitives" — design assets)

The reason we built these was to inform design across the rebuild. **Frame is production; the rest are locked workshops that must be extracted to ship Phase 1.**

### Visual Identity (`docs/design/visual-identity.md`)
- **What:** The visual constitution — 6 adjectives, 4 primitives (Frame, Pursuer Card, Horizon, Tally), Surfaces (Binder), anti-references.
- **Status:** Locked (§1-3, 5); §4 Tokens deferred.
- **Disposition:** **Keep** — the constitutional reference every surface is designed against. Open §4 when Logbook/Pursuit home design begins.

### Frame Component
- **What:** Badge chrome primitive (tier-tinted housing, flip, 12-phase Earn Moment).
- **Lives in:** `templates/components/frame.html`, `static/css/components/frame.css`, `static/js/frame.js`, reference doc + test harness.
- **Status:** **Shipped / extracted** — the only production primitive.
- **Disposition:** **Keep** — the reference pattern for extracting the other primitives.

### Tally / Horizon / Pursuer Card
- **What:** Headline-number primitive (Tally), progress primitive (Horizon), identity card (Pursuer Card, 5 sizes + customization slots).
- **Status:** **Workshop-only**, locked at `/design/{tally,horizon,pursuer-card,pursuer-card-customization}/`.
- **Disposition:** **Adapt (extract to production)** — **these BLOCK Phase 1**: Logbook and Pursuit home are built around them. Extract to partials+CSS+JS in Frame's style when surface build begins. (One open thread: badge-peek slot customization is the only deferred sub-piece.)

### Binder Surface
- **What:** Badge Gallery as a trading-card binder; 6 views + 3D page-flip; first "Surface" (composite container).
- **Status:** **Workshop-only**, locked at `/design/binder/` + list at `/design/badge-collection/`. Full reference in `binder-surface.md`.
- **Disposition:** **Adapt (extract)** — the implementation target for the Phase 1 Badge Gallery rebuild (current gallery predates the kit). Depends on Frame (ready).

---

## E. Community & Content (supporting pillars)

Reviews/roadmaps/ratings feed badge detail pages — they're the pillars the badge spine cross-links into.

### Roadmap System (+ roles, locks, revisions)
- **What:** Staff-authored platinum guides per ConceptTrophyGroup; role-based authoring, advisory edit locks, permanent revision history, collectibles, YouTube attribution. Replaced Checklists.
- **Lives in:** `models.py` (Roadmap + ~8 related), `services/roadmap_service.py` + merge/note services, editor + detail views.
- **Status:** Shipped, comprehensive.
- **Disposition:** **Keep & Expand** — a Phase 1 cross-link panel ("roadmaps for games in this badge"). *Consider:* explicit `Profile.is_roadmap_author` for team gating.

### Reviews (text) — Archived; Ratings — Live
- **What:** Text reviews archived May 2026 (URLs 404/redirect; models/services dormant) after the absorb() CTG-cascade data-loss. Ratings kept + extracted, live at `/api/v1/ratings/` + `/community/rate-my-games/`.
- **Disposition:** **Ratings: Keep** (feed badge detail recommendation stats; Phase 1 cross-link). **Reviews: keep archived** — decide during charter whether the rebuild revives them (dormant code is useful reference).

### Comment System
- **What:** Legacy read-only; list/create endpoints removed, vote/report/edit + moderation survive over historical data.
- **Status:** Legacy/read-only. `ChecklistService.process_markdown()` (shared) is the main live artifact.
- **Disposition:** **Retire (eventually)** — keep tables + moderation for cleanup; no role in the new direction. Extract `process_markdown` to its own utility first.

### Checklist System
- **Status:** **Dead** — UI + CRUD removed, tables retained with historical data; only `process_markdown()` survives (used by reviews/roadmaps). Notably **not** handled by `absorb()`.
- **Disposition:** **Absorb** — rename/relocate `process_markdown` to a standalone `MarkdownService`; leave tables as archival. No urgent removal.

### Community Flags
- **What:** User-submitted game data-quality flags (delisted/shovelware/VR/buggy/...); staff review; game-level targeting (safe from absorb).
- **Status:** Shipped.
- **Disposition:** **Keep** — protects challenge/leaderboard/badge data integrity.

### Community Hub (`/community/`)
- **What:** Fixed-layout Feature Spotlight (hero + fundraiser banner + 2x2 grid + roadmap recruitment + Discord). **Pursuit Feed deferred** (doc/README still references it — stale).
- **Status:** Shipped; read-only aggregation. Reviews card → Rate My Games card.
- **Disposition:** **Keep / Adapt** — wayfinder to community pillars; revisit cards as the spine reorients. (Deliberately not an aggregator — don't make it customizable.)

### Community Trophy Tracker
- **What:** Daily Discord post of prev-day community trophy aggregates from Discord-linked profiles; all-time records + PP Score; 3 read-only API endpoints.
- **Status:** Shipped (user-side cron rollout pending per memory).
- **Disposition:** **Keep** — lightweight, well-scoped engagement.

---

## F. Browse & Discovery

Mostly **Keep** — solid HTMX-based discovery layer. Mostly orthogonal to the Pursuer, but feeds it (game pickers, challenge seeds, version-aware progress).

| System | Status | Disposition | Notes |
|---|---|---|---|
| Browse pages (Games/Trophies/Profiles, HTMX) | Shipped | **Keep** | `browse-filters.js` + `HtmxListMixin`; reuse for Pursuit pickers |
| Company System (`/companies/`) | Shipped | **Keep** | Role tabs; shares `game_grouping_service` |
| Franchise System (`/franchises/`) | Shipped | **Keep** | main/tie-in; shares grouping service |
| Genre / Theme / Engine | Shipped | **Keep** (Engine maybe demote) | Normalized M2M; Genre/Theme drive challenges; Engine pages low-value |
| Flagged Games (`/games/flagged/`) | Shipped | **Keep** | Integrity gate |
| Recently Added + Scout Accounts | Shipped | **Keep** | Content freshness; `refresh_scouts` cron |
| Shovelware Detection | Shipped | **Keep** | Proportional blacklist + whitelist (recent); integrity-critical |
| `game_grouping_service.py` | Shipped | **Keep** | Version-stacking — fundamental to series/DLC progress |
| Browse niceties (saved defaults, "I'm Feeling Lucky", split-control flags, perf indexes) | Shipped | **Keep** | Low-cost QoL; reusable patterns |

---

## G. Platform & Ops (supporting infra)

Mostly **Keep**. Flagged where the rebuild needs a change.

| System | Status | Disposition | Rebuild note |
|---|---|---|---|
| Notification System | Shipped | **Keep + Adapt** | Add Job level-up / Pursuer notifications; share-card render already de-coupled from per-sync counts |
| Payments & Subscriptions (Stripe + PayPal) | Shipped | **Keep** | Premium reframe is Phase 3, not now; verify PayPal retry doc-vs-code |
| Fundraiser | Shipped | **Keep** | Badge-artwork funding loop; donor wall |
| Email (SendGrid + Cloudflare routing) | Shipped | **Keep** | Verify badge_earned honors EmailPreferenceService |
| Share Images / Cards (Playwright) | Shipped | **Keep + enhance** | Pillow/S3 path removed; watch base64 size + temp cleanup |
| Monthly Recap | Shipped | **Keep** | Engagement driver; verify TZ fallback + finalized-lock |
| Analytics & Bot Detection | Shipped | **Keep** | Bot filtering gates leaderboard/challenge eligibility |
| Site Heartbeat / Homepage | Shipped | **Keep** | Single hourly job; old featured_* services removed |
| Advertising (AdSense + Funding Choices CMP) | Shipped | **Keep** | CMP order critical; per-page slot IDs |
| Mini-games / Arcade (Stellar Circuit) | Prototype | **Absorb** | Frontend-only Phaser prototype; needs a minimal backend (sessions/scores) before any XP wiring; **also the last ZoomScaler page** |
| Mobile App API | Backend shipped, FE pending | **Keep + complete** | Token auth; Phase 3 push blocked on Firebase |
| API Layer (~130 endpoints) | Shipped | **Keep** | Version carefully (web + mobile clients) |
| Management Commands & Cron (~69 cmds) | Shipped | **Keep** | Drift-correction backbone; Render-scheduled |
| Security / Settings / Redis / SEO | Shipped | **Keep** | Solid baseline; CSP allowlist is manual |

---

## Headline findings

1. **The engine is sound; the rebuild is a spine-and-surface reorientation, not a backend rewrite.** Everything in §A and most of §G is Keep. This validates evolving in place rather than greenfielding.
2. **The Pursuer layer is the real gap.** Badge XP (the foundation) ships; **Jobs, the Pursuer, the Logbook, the Badge Gallery, and the Pursuit home do not exist** (§B). These are the **Build** items and the core of Phase 1.
3. **Four locked design primitives block Phase 1.** Tally / Horizon / Pursuer Card / Binder are workshop-locked but unextracted; the Phase 1 surfaces are built around them, so extraction is on the critical path (§D).
4. **The design system is anchored to the surface we're retiring.** `design-system.md` was derived from the Dashboard; re-anchoring it on the new center of gravity is the key presentation decision for the charter (§C).
5. **Clear retire/cleanup list** (the project gets *lighter*): ZoomScaler (1 page left), Comment system (eventually), Checklist CRUD (dead — reduce to a markdown util), Dashboard-as-home (absorb modules), Pursuit Feed / Platinum Journey (stay archived). Stale docs to fix: README + Community Hub still reference Pursuit Feed as live.

## Proposed disposition tally

- **Build (net-new):** Jobs system · Pursuer · Logbook · Badge Gallery · Pursuit home
- **Adapt:** Badge system (extend) · StatType scaffold · Milestones/Titles · Home router · My Pursuit hub · IA/sub-nav · Design system (re-anchor) · Tally/Horizon/Pursuer Card (extract) · Binder (extract) · Notifications
- **Absorb:** Dashboard (modules → new surfaces) · Checklist (→ markdown util) · Mini-games (needs backend)
- **Retire:** ZoomScaler · Comment system (eventually) · Dashboard-as-home
- **Archive (no action):** Event System/Pursuit Feed · Platinum Journey · Reviews (text) — revive decision deferred
- **Keep (everything else):** the entire engine, browse, ops, and most community/presentation infra

## Open questions for the charter / playbook

1. Do we **revive text Reviews** in the new direction, or stay ratings-only?
2. **Engine pages** — keep as full detail pages or demote to a tag category?
3. **Job catalog re-derivation** against IGDB taxonomy (blocks the Jobs build) — its own design exercise.
4. How much of the **Dashboard module catalog** survives as Logbook/Pursuit-home content vs. gets cut?
5. Testing: which systems get **characterization tests first** (the highest-coupling: sync, absorb, badge eval, XP) before any conversion?
