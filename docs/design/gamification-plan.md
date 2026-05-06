# Gamification Plan

This document is the layered roll-out plan for PlatPursuit's gamification system. Each phase is self-contained: it ships independently, demonstrates value on its own, and earns its successor through real user engagement.

When this document and `docs/design/product-identity.md` disagree, the identity doc wins. This doc is the implementation reference; the identity doc is the strategic frame.

---

## Premise

PlatPursuit is a trophy tracker that became a game. The tracker is the foundation. The game is the engagement layer that wraps it. The brand markets the game; the tracker is fully functional underneath.

The spine of the game is **Badges**: curated collections of trophies with custom in-house artwork, framed as "platinums for your platinums." The engagement layer wraps badges with **the Pursuer**, an RPG identity built from the player's actual trophy history. Players grind real trophies; the system levels their character.

The model: RuneScape's character-grind-number-go-up loop, applied to trophy hunting, with a strong collection-display dimension (badges as artifacts to earn and display).

Premium gates engagement depth, not the spine itself. The Pursuer is visible to all. Customization, quests, store, and deeper engagement loops are premium territory (specifics deferred to a phase where they earn their place).

The system rolls out in phases. Phase 1 ships the loop. Each successive phase elaborates on the loop in response to real user behavior, not as a single big-bang release.

---

## The Core Loop

The simplest possible version of the game, in plain English:

> You play games on PSN. You earn trophies. The trophies you earn contribute to PlatPursuit Badges (curated collections we maintain). As Badge stages and tiers complete, your Pursuer levels up across multiple Jobs (auto-derived from the kinds of games you're playing). Your Logbook grows: more Badges in your gallery, higher job levels, new titles unlocked. Over months and years, your Pursuer becomes a unique reflection of your gaming history.

That's the entire game. Everything else is elaboration.

The loop has three reward beats per platinum:

- **Per-trophy**: small, constant XP tick to relevant jobs (granular feedback)
- **Per-stage completion**: meaningful XP bonus + visible level-up moments
- **Per-badge-tier earned**: large XP bonus + the Badge artifact added to your gallery

Each beat is satisfying on its own. Together they make the trophy-hunting tempo feel like RPG progression.

---

## Phase 1: The Loop is Real

The goal of Phase 1 is to make the core loop ship as a complete, satisfying experience. Free to all users (premium stays mostly unchanged for Phase 1; the premium reframe comes in Phase 3 once we can see how users actually engage). No quests, no store, no currency. Just: the grind, the collection, the visible character.

### What ships

#### 1. The Job system

The Pursuer's progression mechanism. Each job represents a player specialization (Driver, Detective, Survivalist, etc.). Jobs accrue XP automatically based on the games and badges the user engages with.

Key design decisions:

- **Job assignments are auto-derived from IGDB tags on the games in each Badge stage.** No manual stage-to-job mapping. A stage's job tags are the union of IGDB genres / themes / modes on its constituent games, mapped through a job catalog. Auto-scaling, no admin bottleneck.
- **The job catalog is being re-derived against IGDB's actual taxonomy.** The legacy 25-job list from the prior vision doc is a starting point; the new catalog will be designed to map cleanly to IGDB's genre / theme / mode vocabulary. Final list TBD as a separate design exercise (logged as an open thread).
- **XP awarding has three layers**:
  - Per-trophy: small XP tick to the jobs associated with the trophy's game (granular, real-time during sync)
  - Per-stage completion: medium XP bonus to relevant jobs when a stage completes for any badge tier
  - Per-badge-tier earned: large XP bonus to relevant jobs + the Badge artifact unlocks in the gallery
- **Job levels** scale 1 to N (cap TBD; RuneScape's 99 is the inspiration but the actual cap depends on XP curve calibration with real data).

#### 2. The Pursuer

The player's identity. Built from the sum of their job levels.

- **Pursuer Level**: the headline number, equal to the sum of all individual job levels. Mirrors RuneScape's Total Level. Single number, no parallel "Character Level" concept (the legacy vision doc had two; we collapse to one).
- **Job category radar**: a derived view grouping the jobs into 4 to 8 thematic categories (Combat, Exploration, Puzzle, Story, Social, etc.; final categories TBD with the job catalog redesign). Sums of category-internal job levels render as a radar chart for the "what kind of player am I" view. No separate stat system; categories ARE the high-level characterization.
- **Title-based progression rewards**: at job level milestones (e.g., Lv. 5, 25, 50, 99), the user unlocks a job-specific title ("Apprentice Driver," "Skilled Driver," "Master Driver," "Legendary Driver"). Extends the existing Titles system; no new infrastructure. Titles are equippable on the Logbook.

#### 3. The Pursuer's Logbook destination (`/logbook/`)

The identity deep-dive page. The most data-rich gamification surface in Phase 1.

Structure:

- **Hero**: framed avatar (basic frame for v1; customization is post-v1) + Pursuer Name + Pursuer Level + active title + total badges earned
- **Job grid**: all jobs sorted by level (highest first), with level + XP progress bar + most recent stage that contributed
- **Job category radar**: derived radar chart visualization
- **Active Badges**: badges the user is closest to upgrading a tier on (with progress and "1 stage from Silver" cues)
- **Badge Gallery preview**: most recently earned badges (3 to 5), with "View full gallery" link
- **Recent Activity**: last few job level-ups and badge tier earnings

#### 4. The Badge Gallery

The collection display. Centers badges as the moat artifact. Phase 1 ships a literal gallery of earned badges, not a constellation map (that's Phase 2 if it earns its place).

Design:

- Earned badges displayed as cards using their custom artwork
- Each badge displays at its highest-earned tier (Bronze, Silver, Gold, or Platinum visual treatment)
- Filter / sort options: by series, by tier earned, by recency
- Click into any earned badge → its detail page
- "In progress" section showing badges being actively worked toward
- Visual hierarchy: rarer / higher-tier badges get more visual weight

The gallery is the answer to "look at what I've earned." It's the trophy room.

#### 5. Pursuit home (`/`)

The new home page. Replaces the Dashboard. Designed to lead with badges and surface the Pursuer.

Layout:

- **Pursuer card** (compact): avatar, Pursuer Level, top 1 to 2 jobs, recent activity preview
- **Active Badge progressions**: 3 to 5 badges closest to next tier, with progress and clear next-action ("complete 1 more stage to upgrade Bronze to Silver")
- **Featured Badges**: editorially curated tile (newly added, themed collections, custom artwork hero-sized)
- **Recent earnings strip**: latest badge tier earnings + level-ups across the user's network of activity
- **Tiles to other surfaces**: Browse, Community, Stats, Shareables, Logbook (also reachable via sub-nav)

User states (from product-identity.md):

- Anonymous: marketing landing leading with badges + the pitch
- Signed in, no PSN: badge tour + onboarding wall
- Syncing: "Building your Pursuer..." with badge previews
- Synced: the Pursuit home as described above

#### 6. IA migration

The structural changes from the IA decisions in product-identity.md:

- Pursuit hub becomes home page at `/`
- Dashboard the destination retires; modules redistribute
- `/stats/` rebuilt as exploratory tool (own scope)
- `/shareables/` and `/recap/` survive at standalone URLs
- `/badges/`, `/logbook/`, etc. as flat top-level URLs
- URL redirects from legacy paths
- Navbar adds "My Profile" for signed-in users
- Avatar dropdown reorganized
- Custom Dashboard tabs retire

Note: IA migration is its own substantial workstream and probably ships before or alongside the gamification surfaces. Could be Phase 0 effectively.

#### 7. Cross-link panels (Badge to Pillars)

On Badge detail pages, two new panels:

- **Roadmaps for the games in this badge**: top 1 to 3 staff-authored roadmaps for games in active stages, sorted by relevance
- **Reviews from Pursuers who completed this badge**: snippet of recent community reviews from users who've earned the badge

These pull existing pillar content (roadmaps, reviews) into the spine. They're hard to skip when designing the badge surface and they bake the supporting pillars into the gamification flow.

### What's free vs premium in Phase 1

**Phase 1 keeps premium mostly as-is.** The existing premium tiers (Premium Monthly, Premium Yearly, Supporter, Ad Free) and their existing features (custom Dashboard tabs being retired aside, higher game list cap, expanded recap, shimmer flair) carry through unchanged.

**Everything new in Phase 1 is free.** The Pursuer, the Logbook, the Badge Gallery, the Pursuit home, the cross-link panels. All visible, all functional.

The premium overhaul (Duolingo-style framing where premium gates engagement depth) is deferred to Phase 3 when there are engagement systems (currency, quests, store, frames) to gate. Phase 1 doesn't have those; there's nothing meaningful to paywall yet.

This also handles the "custom Dashboard tabs retiring" migration cleanly: existing premium subscribers don't see a value reduction at Phase 1 launch; the premium reframe comes later when premium features exist to compensate.

### What's NOT in Phase 1

Explicitly out of scope:

- Currency / Marks (Phase 2 or 3)
- Store (Phase 3)
- Quests (Phase 3)
- Frame customization (Phase 2 or 3)
- Mini-games / Arcade (Phase 4+ or never)
- Star Chart visualization (Phase 2 or later, if it earns its place)
- Mastery Paths constellation map per badge (Phase 4+ if at all)
- User-plotted constellations (Phase 4+ if at all)
- Discovery Stars (Phase 4+ if at all)
- Premium overhaul (Phase 3)
- Job mastery rewards beyond titles (Phase 2)
- Job category leaderboards (Phase 2)

Each of these is an elaboration on the loop, not the loop itself. They wait until the loop is real and we can see how users engage with it.

### Phase 1 success criteria

When does Phase 1 succeed enough to start Phase 2?

Directional, not numerical (numbers depend on baselines we don't have yet):

- Active users return to the Logbook regularly (not a one-time visit)
- Users self-report enjoying the system (qualitative; user surveys, Discord feedback)
- "Near-completion" badge progressions drive measurable click-through
- Engagement metrics (DAU / MAU, session length, return rate) improve relative to pre-launch baseline
- Social / sharing activity increases (badge gallery shares, level-up shares)
- No major breakage of existing trophy-tracking workflows

If these signals are positive after a soak period (probably 2 to 3 months post-launch), Phase 2 is justified. If signals are mixed or negative, Phase 1 gets refined before any Phase 2 work begins.

### Phase 1 open threads

Decisions deferred until Phase 1 implementation begins:

- **Job catalog redesign** (re-derive against IGDB taxonomy; replace or refine the legacy 25-job list). Separate design exercise, blocking Phase 1.
- **Job category groupings** (4 to 8 categories for the radar chart). Falls out of the catalog redesign.
- **XP curve calibration** (level cap, XP per trophy / stage / tier, balance between job tiers). Needs real data; ship with reasonable initial values, tune from there.
- **Badge tier requirements integration** (modern stages vs. all stages, platinum vs. 100%). Already designed in the existing badge system; verify it integrates cleanly with the new XP layer.

---

## Phase 2: First Elaboration (Sketch)

After Phase 1 ships and engagement data informs design, Phase 2 adds the first elaboration on the loop. Specifics are intentionally not committed; they depend on what Phase 1 reveals.

**Likely candidates** (in rough priority order):

- **Job mastery rewards.** When a user maxes a job (hits the level cap), unlock something visually distinctive: a special title ("Legendary Driver"), a profile flourish, or similar. Mirrors RuneScape's skill capes. No new infrastructure beyond extended titles.
- **Constellation map visualization.** A second view of progression in the Logbook. Each badge series rendered as a constellation (each stage = star, completion brightness). Complementary to the Badge Gallery (the gallery shows artifacts; the constellation shows structure). Earns its place if Phase 1 data shows users engage deeply with progression visualization.
- **Leaderboards (per-job and overall).** Hi-scores for individual jobs, Pursuer Level, badge collection size. Public, opt-in to surface in Profile. Drives social comparison and competition.
- **Title customization options.** Beyond auto-unlocked titles, allow simple modifications (colors, prefixes). Leverages existing titles infrastructure. Possible premium hook (text styling = premium, base titles = free).
- **Job category deep-dive views.** Click a category in the radar to see all jobs in that category with comparison / depth. Educational + discovery.
- **Badge tier progress views.** Within a badge's detail page, a clearer view of which stages are done at which tiers, and what's needed for the next tier. Minor improvement but high-leverage.

**Probably NOT yet in Phase 2:** currency, store, quests, frame customization. Those wait for Phase 3 when the engagement-systems layer makes sense as a coherent unit.

**Phase 2 success criteria:** similar directional gates as Phase 1, plus engagement metrics on the new features themselves.

---

## Phase 3: Engagement Systems (Sketch)

The engagement-loops layer. Premium reframes here: free users keep everything from Phases 1 to 2; premium gates the new engagement systems.

**What likely ships:**

- **Currency.** Earned from job level-ups, stage completions, badge tier earnings, daily logins. Bronze / Silver / Gold / Platinum denomination structure (mirrors trophy types, on-brand). Final naming TBD.
- **Initial store.** 10 to 20 common-tier cosmetic items: basic frame shapes, frame border styles, nameplate colors. Enough to make the currency loop feel real; not the full economy.
- **Daily quests.** 1 to 3 daily quest slots. Free users get 1 (no rerolls, no streak rewards). Premium gets 3 + weekly quests + rerolls + streak bonuses. The free quest gives free users a taste of the engagement loop; premium upsell is "more of this."
- **Streak system.** Login streak with curve (small per-day, larger per-week, milestones at 7 / 14 / 30 days). Streak Shield item in the store (premium; protects 1 missed day).
- **Premium overhaul.** The Duolingo-style framing committed in product-identity.md takes effect. Free is generous and complete (everything from Phase 1 to 2 + 1 daily quest); premium adds engagement depth (more quests, currency, store access, frame customization).

**Premium overhaul transition:** existing premium subscribers see new value (engagement systems unlock for them by default). Custom Dashboard tabs retired in Phase 1 are now compensated by the new premium engagement features.

**Phase 3 open threads:**

- Quest catalog (categories, generation rules, anti-gaming rules)
- Currency balancing (earning rates, store pricing, anti-inflation mechanisms)
- Store catalog (initial cosmetic items, design and procurement)
- Premium tier restructuring (do existing tiers map to new value? Are price points still right?)

---

## Phase 4+: Depth and Variety (Sketch)

Phase 4 and beyond. Highly speculative. Specifics depend entirely on what Phases 1 to 3 reveal about user behavior.

**Possible features (in roughly the order they'd be most likely to ship):**

- **Mastery Paths per badge.** Detail-page constellation map showing per-stage progress against tier requirements, with mastery milestones (25 / 50 / 75 / 100% of stages = unlocks). Adds depth at the badge level.
- **Star Chart layered features.** Stat nebulae (job-category color glow), job pathways (cross-constellation threads), discovery stars (engagement-earned bonus stars). Each layer ships independently.
- **Expanded quest categories.** Beyond Phase 3's basic quests, the full 6-category vision (trophy / badge / checklist / community / exploration / meta).
- **Frame customization full system.** Frame shapes, border styles, aura effects, corner badges, animated effects. The full identity expression layer.
- **Mastery Cape equivalent.** When a user maxes ALL jobs (the equivalent of RuneScape's max cape), unlock something genuinely special.
- **Mini-games / The Arcade.** One mini-game per job, Phaser-based. Stellar Circuit prototype already exists. Real engagement filler. Phase 4+ if at all; might never ship if Phase 3 engagement loops are sufficient.
- **User-plotted constellations.** Creative-expression layer on the Star Chart. Users draw their own connections between discovery stars. Endgame creative content.
- **Social features beyond leaderboards.** Friend comparisons, side-by-side stats, gift currency, community fund. Depends on community demand.

The discipline at every Phase 4+ feature: it earns its place by demonstrably solving a user problem or driving engagement. No "cool ideas" ship without justification.

---

## Cross-Cutting Decisions

These apply across phases:

**Theme.** The space colony framing from the original vision doc is dropped. Constellation as a *visualization metaphor* (pattern of connected points) survives if it earns Phase 2 inclusion. The brand's primary visual identity is the custom badge artwork, not a sci-fi setting. Specific terminology (Stellar Marks, Star Chart, etc.) depends on whether the constellation visualization survives; if it does, modest space-flavored naming is acceptable for those specific features. If it doesn't, even those names rename to plain English.

**Premium model.** Phase 1: premium stays as-is. Phase 2: minor premium hooks (e.g., title customization). Phase 3: full premium overhaul (Duolingo-style framing). Phase 4+: stable; new features tag onto the established premium frame.

**Naming consistency.** Pursuer (the player), Pursuer's Logbook (the destination), Job (specialization), Badge (collection), Stage (unit), Pursuer Level (headline number = sum of job levels). No "Hunter Profile," no "Explorer's Logbook," no "Character Level" parallel concept.

**Admin tooling.** Each phase needs admin support proportional to its scope. Phase 1: job catalog management, IGDB tag mapping configuration. Phase 2: title catalog. Phase 3: quest templates, store catalog, currency dashboards. The admin tooling ships with the feature, not before or after.

---

## Open Threads (Cross-Phase)

Decisions explicitly deferred:

- **Job catalog final list** (re-derived from IGDB taxonomy; supersedes legacy 25-job list)
- **Premium model overhaul specifics** (Phase 3 timing)
- **XP curve calibration** (initial values, ongoing tuning)
- **Theme details** (final naming for currency, star chart, etc., depends on whether those features ship at all)
- **Constellation visualization vs. badge gallery** (gallery wins for Phase 1; constellation may complement in Phase 2)
- **Phase 2 / 3 / 4 scope** (genuinely undecided; depends on Phase 1 data)

---

## Related Docs

- [Product Identity](product-identity.md): the strategic frame this implementation serves. **When this document and product-identity.md disagree, the identity doc wins.**
- [Gamification Architecture](../architecture/gamification.md): what's currently shipped (badge XP only).
- [Hub IA & Sub-Nav](../architecture/ia-and-subnav.md): the existing IA implementation; the Phase 1 IA migration substantially revises this.
