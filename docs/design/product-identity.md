# Product Identity & North Star

This document captures the strategic identity of PlatPursuit: who we are, what we're building, and how we describe it. Every design and product decision should be tested against the pitch below. If a proposed feature, page, or change doesn't serve the pitch, it's the wrong feature.

This is a living document. Naming decisions, architectural commitments, and open threads will tighten as the gamification update designs out.

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

## Identity Architecture

### The Spine

**Badges + the Pursuer** form the spine of the product. Everything orbits this center.

Badges are curated game lists with custom artwork, conceptually framed as "platinums for your platinums." Each Badge defines a stage progression with a real reward (the badge itself, with original art) at the end. Badges are the privileged input to the gamification engine: not every PSN trophy fires the engine, only the trophies that contribute to active Badges do.

The Pursuer is the user's RPG identity, built from completing Badges. Pursuer progression has three layers: Character Level (from badge XP), P.L.A.T.I.N.U.M. stats (8-stat radar describing how you play), and Jobs (25 specializations). The Pursuer's Logbook is the destination where this identity lives.

### Supporting Pillars

Four pillars exist around the spine. They are intentionally subordinated: each adds value without competing for the user's attention with the spine.

| Pillar | Purpose | Investment Notes |
|--------|---------|------------------|
| **Guides (Roadmaps)** | Staff-authored platinum guides | SEO and retention asset. Needs active investment (someone authoring). |
| **Reviews & Ratings** | Community reviews of games from a trophy-hunter perspective | Community moat. SEO asset. Needs population (more reviews = more value). |
| **Browsing & Personal Tracking** | Library, IGDB-powered discovery, personal stats, profile. The PSNP-equivalent core utility. | Two sub-roles: personal tracking (your stuff) and IGDB-powered discovery (find new stuff). |
| **Shareables** | Plat cards, profile cards, monthly recaps, challenge cards, platinum grid | Marketing engine. Every share is free promo. Worth surfacing more prominently. |

### Strategic Implications

- **The spine is for engagement and retention.** Pursuers come back to level up.
- **The pillars carry SEO traffic.** Game-detail pages, guides, and reviews are the inbound funnel. The spine doesn't fight for SEO.
- **Premium is for expression and depth, not gating the spine.** The Pursuer identity should be visible to all (free can see their level, stats, jobs); customization, currency, and engagement loops are premium.
- **Challenges (A-Z, Calendar, Genre) sit inside the spine** as structured progression layered on top of badges, not as a separate pillar.

---

## Naming Conventions

| Term | Meaning | Status |
|------|---------|--------|
| **Pursuer** | The user's RPG identity. The character they're building. | Committed. |
| **Pursuer's Logbook** | The destination/page where the Pursuer identity lives. | Working title. May tighten during design-doc review. |
| **Badge** | A curated game list with custom artwork; the input to gamification. | Committed (existing system). |
| **P.L.A.T.I.N.U.M.** | The 8-stat acronym (Power, Luck, Agility, Toughness, Intelligence, Navigation, Utility, Magic). | Committed (vision). |
| **Job** | One of 25 player specializations driven by completed Badges. | Committed (vision). |
| **Stage** | A badge tier (Bronze, Silver, Gold, Platinum). | Committed (existing). |

---

## Open Threads (Deliberately Not Decided)

These remain open going into the gamification planning work:

- **Layered destinations vs. integrated IA.** Whether the Pursuer's Logbook becomes a literal separate destination (Steam Library/Community model) or an integrated section. Directional lean: separation. Decision deferred to gamification planning.
- **MVP scope.** Stats first? Jobs first? Both at once? Logbook destination + star chart simultaneously? Deferred to planning.
- **Premium model overhaul.** Direction agreed (Duolingo-style: generous free, premium for expression and engagement). Specifics deferred until after the spine ships and we can see how users actually engage.
- **Pursuer's Logbook destination name.** Working title. "Logbook" leans archival; might undersell live progression. Worth holding lightly.
- **Engagement XP and the "level 99 enabler".** From the vision doc: how do users earn job XP through platform engagement (not just trophy-based stage completion) without it feeling like an engagement chore? Open.

---

## How to Use This Document

This is the test, not the spec. Every product decision should pass through:

1. **Does this serve the pitch?** If a proposed feature or page can't be explained as part of the 2-minute pitch, it's either misaligned or the pitch needs revisiting.
2. **Does this respect the spine?** New work should either strengthen the Badge to Pursuer chain or live unobtrusively within a supporting pillar.
3. **Does this break the additive framing?** "Trophy hunting already exists; we're the layer built on top." Anything that competes with PSN itself, or duplicates what PSN already provides without adding meaningful interpretation, fails this test.

When this document and the gamification vision doc disagree, this document wins. The vision doc is the implementation reference; this is the strategic frame.

---

## Related Docs

- [Gamification Vision](gamification-vision.md): The full RPG system spec. Implementation reference for everything the spine will become.
- [Gamification Architecture](../architecture/gamification.md): What's currently shipped (badge XP only).
- [Hub IA & Sub-Nav](../architecture/ia-and-subnav.md): Current information architecture, will likely shift as the spine becomes prominent.
