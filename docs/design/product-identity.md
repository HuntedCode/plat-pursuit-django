# Product Identity & North Star

This document captures the strategic identity of PlatPursuit and the committed Information Architecture that follows from it. Every design and product decision should be tested against the pitch and IA below. If a proposed feature, page, or change doesn't serve the pitch, it's the wrong feature.

This is a living document, but most strategic and architectural decisions in this doc are now committed (not directional). Open threads are explicitly marked at the bottom.

---

## The 2-Minute Pitch

> Picture a PlayStation user who just earned their 50th platinum. On every other tracking site, that's a number. On PlatPursuit, that platinum slotted into a Badge they were one game away from completing, leveled up their Detective job, pushed their Intelligence stat closer to the next tier, ticked a daily quest, and earned them currency to spend on a new frame for their Pursuer's Logbook.
>
> That's PlatPursuit. Trophy hunting already exists; we're the layer built on top of it.
>
> The center of everything is Badges. Think of them as platinums for your platinums. Each Badge is a curated game list (every Resident Evil platinum, every From Software soulslike, every PSVR2 100%) with custom artwork our team designs in-house. You can't find these anywhere else. Earning one is a real achievement, with art worth framing and sharing.
>
> As you complete the Badges that interest you, your Pursuer levels up, your stats develop, and your Jobs specialize. The stats are P.L.A.T.I.N.U.M.: Power, Luck, Agility, Toughness, Intelligence, Navigation, Utility, Magic. The 25 Jobs cover every kind of player. A Driver with every Forza. A Detective who's solved every Phoenix Wright case. A Spell Caster carrying the Skyrim platinum. Your character is the cumulative shape of how you actually play.
>
> Around that spine, we've built what a serious trophy hunter needs: staff-written platinum roadmaps, community reviews from players who've actually completed the platinum, deep stats and library tools, shareables built for showing off. None of it competes with PSN. The Badge-driven Pursuer does.
>
> Free experience is generous and complete. Premium unlocks expression, customization, and deeper engagement. The bet is simple: turning casual trackers into a community of Pursuers is a much bigger product than a better tracker would ever be.

About 320 words, ~125 seconds spoken at conversational pace.

---

## Identity Architecture (Conceptual)

### The Spine

**Badges + the Pursuer** form the spine of the product. Badges are the moat: curated game lists with custom in-house artwork, framed as "platinums for your platinums." The Pursuer is the user's RPG identity, built from completing Badges through three layers: Character Level (badge XP), P.L.A.T.I.N.U.M. stats (8-stat radar), and Jobs (25 specializations).

Not every PSN trophy fires the engine. Only trophies that contribute to active Badges do. This is the privileged role Badges play.

### Supporting Pillars

Four pillars exist around the spine. They are intentionally subordinated: they support and entice users to stay on site without competing with the spine for attention.

| Pillar | Role | Discovery / surfacing |
|--------|------|----------------------|
| **Guides (Roadmaps)** | Staff-authored platinum guides | Live on Game Detail; cross-linked from Badge pages; will graduate to a Browse sub-nav slot once volume justifies a list page |
| **Reviews & Ratings** | Community reviews from a trophy-hunter perspective | Live on Game Detail; entry via Community sub-nav; cross-linked from Badge pages; post-platinum review prompts (post-Phase-1) |
| **Browsing & Personal Tracking** | IGDB-powered discovery (Browse hub) and personal-data surfaces (Profile, Stats) | Browse for "find new"; Profile for "your stuff" |
| **Shareables** | Plat cards, profile cards, monthly recaps, challenge cards, platinum grid | Standalone surface; cross-linked from Pursuit ("Share this Pursuer", "Share this Badge") |

### Strategic Implications

- **The spine carries engagement and retention.** Pursuers come back to level up.
- **The pillars carry SEO traffic.** Game-detail pages, guides, and reviews are the inbound funnel. The spine doesn't fight for SEO.
- **Premium gates expression and depth, not the spine.** The Pursuer identity is visible to all (free can see their level, stats, jobs). Customization, currency, store, and deeper engagement loops are premium.
- **Challenges sit inside the spine** as structured progression layered on Badges, not as a separate pillar.
- **Surfacing is multi-channel, not nav-driven.** Reviews and Roadmaps don't earn top-nav slots; they get traffic via contextual cross-linking, home page content tiles, and notification-driven re-engagement.

---

## Site Structure (Information Architecture)

### Hub model

Three hubs plus standalone personal-data surfaces. The Pursuit hub IS the home page; it's the spine destination. The other two hubs (Browse, Community) are content destinations. Personal-data tools (Stats, Shareables, Recap) live as standalone utility surfaces, not nested under a "Dashboard" container.

| Surface | URL | Role |
|---------|-----|------|
| **Pursuit** (home) | `/` | The spine destination. Badges + Pursuer identity. The headline experience |
| **Browse** | `/games/` | IGDB-powered discovery (games, trophies, companies, franchises, genres, themes, engines, recently-added, flagged, roadmaps when justified) |
| **Community** | `/community/` | Reviews, profiles, challenges, lists, leaderboards |
| **Profile** (yours or others) | `/community/profiles/<u>/` | Public face: games, trophies, badges, lists. Lives in Community as the social view |
| **Stats** | `/stats/` | Personal exploratory data tool (rebuilt from old Dashboard Stats; user-driven surfacing, optional exports) |
| **Shareables** | `/shareables/` | Personal share-image landing |
| **Recap** | `/recap/` | Monthly Recap experience |

### URL structure

Flat top-level URLs for Pursuit sub-pages because Pursuit's home is `/`. No `/pursuit/` URL prefix. The "Pursuit" name is a brand and UX concept, not a URL prefix.

```
/                Pursuit home (anonymous landing + synced home)
/badges/         Badges browse and your progress
/logbook/        Pursuer's Logbook (RPG identity deep-dive)
/star-chart/     Star Chart constellation
/quests/         Quests
/milestones/     Milestones
/titles/         Titles

/stats/          Stats (exploratory tool)
/shareables/     Shareables landing
/recap/          Monthly Recap

/games/          Browse hub (existing, unchanged)
/community/      Community hub (existing, unchanged)
```

### Pursuit home (`/`) by user state

| State | What `/` shows |
|-------|----------------|
| **Anonymous** | Marketing landing leading with badges. Hero: custom badge artwork + the pitch. Examples of badges, Jobs, the trophy → Badge → Pursuer chain. CTA: link PSN |
| **Signed in, no PSN** | Badge tour + onboarding wall. "Link your PSN to begin your Pursuit" |
| **Syncing** | "Building your Pursuer..." with previews of likely badge progress based on synced games |
| **Synced (default)** | Pursuit home: Pursuer card top, active badge progressions, featured badges, recent activity, content tiles to other pillars |

### Pursuit home vs. Logbook

These are sibling surfaces with distinct roles:

- **Pursuit home (`/`)** is *the chase*. What's happening in your pursuit right now. Badge-led: active progressions, featured editorial picks, recent activity. Pursuer card is compact; identity is at-a-glance.
- **Logbook (`/logbook/`)** is *who you've become*. RPG identity deep-dive: full P.L.A.T.I.N.U.M. radar with breakdown, all 25 Jobs grid with progress, customization (frames, titles, store entry), star chart preview, quest history, achievement showcase.

Both are badge-rooted. Pursuit pulls you forward; Logbook reflects who you are.

### Pursuit sub-nav

```
[Home] [Badges] [Logbook] [Star Chart] [Quests] [Milestones] [Titles]
```

Seven items. **Market** and **Arcade** are deferred to v1.x until their content is ready, then they join the strip.

### Navbar

**Signed-in:**
```
[Logo (Pursuit/home)] [Browse] [Community] [My Profile]    [bell] [avatar]
```

**Anonymous:**
```
[Logo (Pursuit/home)] [Browse] [Community]    [Sign In] [Sign Up]
```

The navbar adds **My Profile** as a direct button for signed-in users. It's the only personal destination that earns a navbar slot, because it's the most-visited "your stuff" surface and was a recurring user pain point.

### Avatar dropdown (signed-in)

Houses lower-frequency personal tools and account management:

- View My Profile (kept for muscle memory)
- My Logbook
- My Stats
- My Shareables
- My Recap
- Manage Premium
- Settings
- Theme
- Logout

### Mobile bottom tab bar

| State | 4 slots |
|-------|---------|
| Signed-in | Pursuit (home) / Browse / Community / My Profile |
| Anonymous | Home (Pursuit) / Browse / Community / Sign In |

### Dashboard dismantling

Dashboard the *destination* retires. The 41 modules are triaged:

| Module category | Fate |
|-----------------|------|
| **Account state** (sync status, recent activity) | Fold into Pursuit home as a small persistent surface |
| **Progression overview** (badges, Pursuer card) | Already in scope for Pursuit home |
| **Stats / analytics** (Top Studios, Time-to-Beat, Diversity, Library Health, etc.) | Survive on `/stats/` rebuilt as an exploratory tool with optional exports. User picks what they want to see, not 41 fixed modules. |
| **Recommendations** (roadmap recs, recommended games) | Contextual on Browse + featured on Pursuit home |
| **Library health** | Profile or Stats |
| **Featured / community** (Whats new, etc.) | Already retired (Site Heartbeat replaced this stack) |

**Custom Dashboard tabs retire** alongside the Dashboard concept. The premium overhaul gives air cover for this change; users who paid for tab customization will see new value reflected in the new premium structure.

**URL redirects:**
- `/dashboard/stats/` → `/stats/`
- `/dashboard/shareables/` → `/shareables/`
- `/dashboard/recap/` → `/recap/`
- `/my-pursuit/badges/` → `/badges/`
- `/my-pursuit/milestones/` → `/milestones/`
- `/my-pursuit/titles/` → `/titles/`
- `/dashboard/` → `/` (or transitional "where things moved" page during launch window)

### Surfacing strategy for supporting pillars

Roadmaps and Reviews don't earn navbar slots. They get traffic via:

1. **Contextual cross-linking (highest leverage)**: Badge pages show "Roadmaps for the games in this badge" and "Reviews from Pursuers who completed this badge"
2. **Pursuit home content tiles**: Featured Roadmap of the Week, Latest Reviews carousel, Recommendations
3. **Sub-nav promotion** when volume justifies (Roadmaps gets a Browse sub-nav slot post-volume threshold)
4. **Notification-driven re-engagement**: post-platinum review prompts, "new roadmap for a game you own" notifications
5. **Empty-state CTAs**: "Be the first to review this game"
6. **Footer presence** (already in scope)

Most surfacing work is *post-Phase-1* once the spine ships and engagement data informs the design. The exceptions are the Badge → Roadmap and Badge → Review cross-link panels, which are in-scope for the gamification update because they're hard to skip when designing the Badge surface.

---

## Naming Conventions

| Term | Meaning | Status |
|------|---------|--------|
| **Pursuit** / **The Pursuit** | The hub at `/`. The spine destination. The brand experience. Use "Start your Pursuit" for anonymous copy, "Continue your Pursuit" for signed-in | Committed |
| **Pursuer** | The user's RPG identity. The character they're building | Committed |
| **Pursuer's Logbook** / **Logbook** | A sub-page within Pursuit (`/logbook/`) for the RPG identity deep-dive | Committed |
| **Badge** | A curated game list with custom artwork; the input to gamification | Committed (existing system) |
| **P.L.A.T.I.N.U.M.** | The 8-stat acronym (Power, Luck, Agility, Toughness, Intelligence, Navigation, Utility, Magic) | Committed |
| **Job** | One of 25 player specializations driven by completed Badges | Committed |
| **Stage** | A Badge tier (Bronze, Silver, Gold, Platinum) | Committed (existing) |

---

## Open Threads

Most of the original open threads are now decided. What remains open going into formal planning:

- **Logbook MVP scope.** Which sub-pages ship in the first release vs. v1.x. Star Chart, Quests, frame customization, and the Job deep-dive grid all need scoping.
- **Premium specifics.** Direction agreed (Duolingo-style: free is generous and complete, premium for expression and engagement). Specifics deferred until after spine ships and we can see real engagement.
- **Engagement XP ("level 99 enabler").** How users earn job XP through platform engagement (not just trophy-based stage completion) without it feeling chore-like. Open from the vision doc.
- **Roadmaps list page reveal threshold.** What roadmap volume justifies graduating to a `/games/roadmaps/` list page and Browse sub-nav slot? Author's call.
- **Custom Dashboard tabs migration story.** Existing premium subscribers paid for tab customization. Communication and grandfathering plan needs design.

---

## How to Use This Document

This is the test, not the spec. Every product decision should pass through:

1. **Does this serve the pitch?** If a proposed feature or page can't be explained as part of the 2-minute pitch, it's either misaligned or the pitch needs revisiting.
2. **Does this respect the spine?** New work should either strengthen the Badge to Pursuer chain or live unobtrusively within a supporting pillar.
3. **Does this break the additive framing?** "Trophy hunting already exists; we're the layer built on top." Anything that competes with PSN itself, or duplicates what PSN already provides without adding meaningful interpretation, fails this test.

When this document and the gamification vision doc disagree, this document wins. The vision doc is the implementation reference; this is the strategic frame.

---

## Related Docs

- [Gamification Vision](gamification-vision.md): The full RPG system spec. Implementation reference. Note: portions reflect older premium-only and "My Pursuit hub" assumptions; this doc supersedes those framings.
- [Gamification Architecture](../architecture/gamification.md): What's currently shipped (badge XP only).
- [Hub IA & Sub-Nav](../architecture/ia-and-subnav.md): The existing IA implementation. Will be substantially revised by this initiative (3 hubs + standalone surfaces, not 4 hubs); treat as historical reference for the legacy 4-hub model until rewritten.
