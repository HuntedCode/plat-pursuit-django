# Visual Identity

This document is the visual constitution of PlatPursuit. It captures who we look and feel like, the test that any visual decision must pass, and the boundaries that prevent drift.

It is the sibling to [Product Identity](product-identity.md), which answers *what is this product*, and answers *how should it look and feel*. When a future visual decision is in question, the answer is here. When this document and a downstream design disagree, this document wins.

This is a living document. Sections 1, 2, 3, and 5 are committed. Section 4 (Tokens) is explicitly deferred until the gamification surfaces start being designed and we know what we're tokenizing toward. Open threads are listed at the bottom.

---

## 1. The Brief

> PlatPursuit is built for trophy hunters: collectors at heart, adventurers in practice, with a quiet love of numbers ticking up. The visual identity holds all three truths in one frame. Every page is a corner of the same explorer's office, where prized artifacts sit framed on the wall, the next expedition is already being plotted on the desk, and a ledger in the corner records every climb and conquest in running ink. The Pursuit (`/`) is the planning desk: forward-leaning, motion-cued, mapping what's next. The Logbook (`/logbook/`) is part curated archive, part numbered ledger: framed art on the walls, pages tallying every Job, every Stage, every level. The proof of who you've become.
>
> The execution is decisively modern. Clean lines, generous space, restrained color, current typography. Treated as a 2026 designer's interpretation of a collector's space, not a literal period piece. A deliberate minority of tactile signals earn their place: a stamped seal on an earned badge, a pinned lead on the planning board, a horizon glow at the edge of the next progression, a level number turning over with weight. Numbers are first-class material in this language. Level-ups, XP ticks, and milestone counts are designed with the same care as the badge frames around them, because watching them climb is half the joy of the hobby and we should never bury it. Custom badge artwork is the moat. The visual identity exists to frame the art without competing with it. If the chrome ever fights the art, the chrome loses.

The Brief is the test of last resort. When a visual decision is unclear, read it again.

---

## 2. Adjectives

Six words. Each carries one strategic dimension. Any visual decision should be testable against this set: does the proposed work honor each, or does it fight one of them?

| Adjective | What it carries | What it disqualifies |
|-----------|-----------------|---------------------|
| **Prestigious** | Collector's pride. The Logbook's archival gravity. The reason a badge feels worth earning. | Sterile, cheap, flat. |
| **Exploratory** | The Pursuit's forward lean. Discovery, the next horizon, the hobby's "I never know what I'm getting into" instinct. | Static, closed, dead-end. |
| **Rewarding** | The number-go-up satisfaction. Levels, XP, milestones. Effort that visibly pays back. | Grindy without payoff. |
| **Modern** | A 2026 substrate, not a period piece. Clean lines, generous space, current typography. | Dated, costume, fantasy-RPG. |
| **Charming** | Indie spirit. Tactile cues, flavor text, easter eggs. Where personality lives without breaking the professional read. | Generic, charmless, anonymous. |
| **Earnest** | Tonal pledge. We take the user and the hobby seriously without irony. | Detached, ironic, performatively cool. |

The set self-corrects. *Modern* prevents *charming* from becoming twee. *Earnest* prevents *prestigious* from becoming stuffy. *Charming* prevents *modern* from becoming sterile. None of the six can drift into a bad version of itself without one of the others catching it.

---

## 3. Signatures

The recognizable visual primitives that carry the identity. These are the equivalent of Apple's product casing language, Linear's pixel-perfect minimalism, Stripe's gradient-and-grid system. Brand recognition primitives: someone seeing a screenshot in the wild should recognize PlatPursuit before reading any text.

The kit serves a single central idiom: **badge-as-trading-card**.

### Central idiom: badge-as-trading-card

Every badge is conceptually a high-quality collectible card. The four primitives serve this mental model.

| Trading card concept | PlatPursuit translation |
|---|---|
| Card front (the art) | Badge artwork (custom, in-house, the moat) |
| Card frame | The Frame |
| Card grading / authentication | Tier (Bronze/Silver/Gold/Platinum) + earn-stamp variant of Frame |
| Set / expansion membership | Badge series |
| Holo / foil treatment | Tier-specific gleam (modern execution, no skeumorphism) |
| Binder / display | The Badge Gallery |
| Pack-pull moment | Tier-earned celebration |

The test for any badge-rendering decision: *does this respect the card metaphor?* If a treatment makes a badge feel like a generic icon instead of a collectible artifact, it's wrong.

### The Frame (headline primitive)

**Concept.** The PlatPursuit-branded housing that surrounds badge artwork. Binder slot, museum case, graded-card sleeve. The Frame is the *brand*; the artwork inside is the *content*.

**Job.**

1. **Brand recognition.** A badge spotted in any context (Twitter, Discord, share image, screenshot) reads as PlatPursuit at a glance. The Frame is the brand mark.
2. **Container for state and metadata.** Badge name, earn date, tier indicator, count, owner all live on or in the Frame, never floating loose.
3. **Treatment of mode.** Earned/unearned, hover, pinned, premium are expressed via Frame variants. The artwork stays untouched; the Frame carries the mode.

**Where it appears.** Badge Gallery, Badge detail hero, Pursuit home active progressions, cross-link panels on Game detail, share images, notifications, recap slides. Anywhere a badge renders.

**Visual character (early reads, pre-tokens).**

- Bordered shape that surrounds the artwork without crowding it
- Subtle metallic gleam reserved for higher tiers (CSS gradients + conic, no skeumorphic textures)
- Tier reinforcement is light because the badge's existing tier-backing does the heavy lifting
- Earned vs. unearned state is *not* greyscale (greyscale reads "disabled," wrong feeling). Likely: full-color art behind a translucent veil with the Frame in a "blueprint" mode
- Hover: gentle breathing scale + foil shimmer pass on Gold/Platinum tiers
- Earned variant displays earn date and tier as native metadata (absorbing what would otherwise be a separate Stamp primitive)

**Anti-patterns.** Heavy ornate scrollwork (period piece). Generic rounded card (no identity). Frame that visually shouts (the art should be the loudest element). Treatment that varies wildly between tiers (Bronze to Platinum should feel like the same family, not different products).

### The Pursuer Card

**Concept.** The Pursuer's own "trading card." A recognizable identity treatment that appears wherever the Pursuer renders across the app. Sibling to the badge Frame: same family, different content. If Frame is *what you've collected*, the Pursuer Card is *who you are*.

**Job.**

1. **Cross-surface identity recognition.** Seeing a Pursuer Card in any context reads instantly as a Pursuer in the PlatPursuit system.
2. **Container for the gamification spine.** Avatar, Pursuer Name, Pursuer Level, active Title, top Job, recent badge peek.
3. **Scales gracefully.** Hero (Logbook), compact (Pursuit home), profile (header), share (vertical/square), mini (comments, reviews, leaderboards).

**Where it appears.** Logbook hero, Pursuit home compact strip, Profile page header, share images, leaderboards, comment/review chips, "earned by these Pursuers" panels on Badge detail.

**Visual character.** Card vessel with framed avatar, prominent Pursuer Level rendered in the Tally treatment, name, active Title, top Job slot, optional 1-2 recent badges peeking. Echoes the badge Frame's language so the two read as siblings. Premium customization (frames, expression) is *additive* to the base; never replaces it.

**Anti-patterns.** Generic "user profile card" (avatar circle + username + bio, like every social app). Card louder than the Pursuer inside it. Inconsistent treatments between hero/compact/mini that break the family read. Premium customization that fragments recognition.

**Note on existing surfaces.** Not net-new construction. PlatPursuit already has a profile card system and a Pursuer share image. The Pursuer Card primitive is the *unifying language* those existing surfaces should converge on so they read as one thing instead of three.

### The Horizon

**Concept.** The signature treatment for *the path forward.* The glow on the edge of an unfinished progression. The far wall of the planning desk where the next stop is marked but not yet reached.

**Job.** Forward motion. Wherever progress is shown (XP bar, stage progression, tier completion), the Horizon communicates *there's more this way* without literal arrows or "Click to continue" CTAs.

**Where it appears.** XP progress bars on Job rows, tier progression on badges, stage completion bars in series, Pursuer Level toward next milestone, Pursuit home "1 stage to next tier" prompts.

**Visual character.** Subtle gradient at the leading edge of any progress indicator. Possibly a glow plus a faint marker (the next stop). Color carries semantic weight: warmer the closer you are, cooler the further. Gentle pulse on near-completion (the "go finish it" nudge).

**Anti-patterns.** Literal arrows (too directive). Heavy dramatic gradients (it's a hint, not a flag). "Click to continue" CTAs (the horizon is mood, not button). Disconnected from real progress data (must always pair with a real percentage, never decorative-only).

### The Tally

**Concept.** The signature treatment for how numbers, levels, XP, and milestones render. Numbers are first-class material in PlatPursuit; the Tally is what makes them *enjoyable to look at*. It turns a number from information into satisfaction.

**Job.**

1. **Make numbers feel earned, not reported.** A level rendered in the Tally style is a reward, not a label.
2. **Codify how growth shows visually.** The "ticking up" moment (XP fills, a level turns over, a milestone hits) has a unified vocabulary across the app.
3. **Anchor the *rewarding* adjective.** Wherever the visual identity does its rewarding work, the Tally is the primitive doing it.

**Where it appears.** Pursuer Level (headline number on Logbook and Pursuit home), per-Job levels, XP awards during sync, stage and tier completion counts, milestone counters (100th platinum, 50th badge), real-time level-up moments.

**Visual character.** Distinctive type treatment for headline numbers: heavy weight, possibly tabular figures so digits don't shift width, generous breathing room. Ticking-up animation that has *weight* (a roll or flip with mass, not a frantic counter spinning). Level-up moment: brief celebratory beat with a Horizon-style edge glow. Recently-earned numbers carry a subtle "fresh" treatment that decays over hours, so the level you just earned looks different from one earned weeks ago, just briefly.

**Anti-patterns.** Generic dashboard metrics (big-bold number, small label, bordered card). Counters that count up from zero (looks arcade-y, kills weight). Numbers as table cells (alignment-driven, makes them data). Levels that feel like stats screen entries instead of rewards. Tally treatment everywhere it could go (must be reserved for *meaningful* numbers).

**Pairs with.** The Horizon. A Job row in the Logbook is *Tally + Horizon*: the level number rendered in Tally style, with an XP progress bar in Horizon style trailing toward the next level. The two primitives compose, they don't compete.

### Stamp and Pin (treatment-level, not pillars)

Earlier drafts of this kit included Stamp (provenance marker) and Pin (wayfinding marker) as core primitives. They were demoted to *small treatments inside Frame and Pursuer Card* because their work overlapped with the larger primitives:

- **Stamp's provenance work** (earn date + tier display) lives in the Frame's earned-state variant.
- **Pin's wayfinding work** (current state, what's next) lives in the Frame's active-state variant and the Horizon primitive.

They survive as design details (a slightly off-register tier stamp in a badge corner, an active-state highlight for what you're working on) but they don't earn pillar status.

---

## 4. Tokens

Deferred. This section will hold concrete CSS variables, type scale, color extensions beyond DaisyUI's defaults, and motion vocabulary once Phase 1 gamification design begins and we know what we're tokenizing toward. Drafting tokens before the surfaces that use them is premature.

When this section opens, expected contents:

- Typography pair (display + body) and scale for the Tally treatment
- Color extensions for tier states, Horizon gradients, and earnest-warm accents
- Motion vocabulary for level-up beats, badge-earn moments, Pursuer Card animations
- Spacing additions specific to identity surfaces (badge gallery, Logbook hero)
- DaisyUI overrides where the default token doesn't carry the identity

---

## 5. What We Are NOT

Each anti-reference disqualifies a specific kind of bad design we could otherwise drift into. The list is the corrective set: every entry is a known way visual identities like ours go wrong.

### Top anti-references

**1. Not a generic Tailwind dashboard.** The shadcn-derivative baseline of the modern indie web: slate-zinc palette, identical card grids, soft shadows, Inter type, generic gradients. Recognizable as "modern app" but anonymous. The biggest gravity well for an indie team building on Tailwind / DaisyUI. Escaping it is the whole point of the visual identity.

**2. Not a fantasy or RPG cosplay.** Wood textures, parchment overlays, scrollwork frames, Eldritch typography. The risk inherent in the explorer's-office metaphor is someone taking it literally and ending up looking like World of Warcraft or a Skyrim mod menu. We are a 2026 designer's *interpretation* of a collector's space, never a costume.

**3. Not Notion-flat or Linear-sterile.** The opposite failure mode. All-grey, all-restraint, all-typography, no personality. Professional to the point of charmless. *Charming* and *earnest* in the adjective set explicitly disqualify this register.

**4. Not generic mobile-game gamification.** "Achievement Unlocked!" pop-ups, neon XP bars, quest dialogue boxes, confetti for every interaction. Casual mobile games over-celebrate everything; we celebrate *meaningful* moments and let the rest breathe. The Tally is reserved for numbers worth pausing on, not every count on every page.

**5. Not ironic, detached, or brutalist-cool.** "We're an app, lol" tone. Performative ugliness. Anti-design as statement. The brutalist-revival cool that flatters designers but alienates users. *Earnest* specifically disqualifies this. We take the user and the hobby seriously, without irony.

**6. Not twee or hipster-sincere.** The risk for *earnest* if it overshoots. Wes Anderson kitsch, hand-drawn quirk for its own sake, the Mailchimp post-2020 illustration drift. Earnest grounded by *modern* and *prestigious*; charming grounded by *earnest*. The set keeps personality from cloying.

**7. Not skeumorphic.** Actual wax-textured seals, photoreal leather binding on the Logbook, wood-grain panels on a planning desk. The metaphor is *conceptual*, not literal. Tactile cues are gentle suggestions (a slightly off-register stamp inside the Frame, a soft shadow on a pinned element), never photoreal textures.

### Lower-risk anti-references worth flagging

**Not "a better PSNProfiles."** Tracking sites treat trophy data as a database. We are the layer on top of trophy hunting, not a competing tracker. Our visuals must read as a different product class, not "the same product with nicer fonts."

**Not crypto / NFT collectible aesthetics.** Glowing chrome, neon gradients, "rare digital collectible" cues. Trading cards for the love of trading cards, never for resale value. Adjacent to our metaphor and worth naming so we don't drift there.

**Not PSN-derivative, but PSN-era *informed*** (the love-letter rule). A fine line worth walking carefully. Many of our users (and many on the team) grew up with PlayStation. The PS1/PS2 era's visual world (Y2K crystalline glass, neon glows on deep blacks, geometric abstraction, console-boot-sequence aesthetic) shaped a generation. The PS3/4/5 era added clean minimalist surfaces, soft luminous gradients, and that dark-mode-with-luminous-accent palette that's now PlayStation's modern signature. **A love letter to those memories is on-brand**, and serves *earnest* directly. We are unironically writing a love letter to the era, not pretending we exist outside it.

What's safe (PSN-era informed):

- Y2K crystalline / glassy aesthetics (broad era, not Sony-specific)
- Neon glows on deep blacks (general gaming aesthetic of the era)
- Geometric abstraction in motion / loading states
- Backlighting and chromatic accents
- The mood of a console boot sequence (genre, not brand)
- PS3/4/5 era's dark-mode-with-luminous-accent palette
- Trophy tier hierarchy thinking (Bronze / Silver / Gold / Platinum is a broad collection mental model)

What's dangerous (PSN-derivative):

- Sony's actual UI patterns (the PS5 home grid, the trophy notification animation, DualSense-specific blue)
- Trademarked iconography (the four button shapes, the PlayStation logo type)
- Direct lifts of Sony's color palette or motion language
- Anything that reads as a Sony reskin to a Sony lawyer

The test: would this read as *homage to a PlayStation player* without reading as *Sony copy to a Sony lawyer*? Both halves matter.

---

## How to Use This Document

This is the test, not the spec. Every visual decision should pass through:

1. **Does it serve the Brief?** If a treatment can't be located inside the explorer's-office metaphor or doesn't honor the three identities (collector, adventurer, number-watcher), it's misaligned.
2. **Does it pass all six adjectives?** A decision that's prestigious, exploratory, rewarding, modern, charming, and earnest is on-brand. If even one fails, the decision is wrong.
3. **Does it use the four signatures correctly?** Frame for badges, Pursuer Card for identity, Horizon for progress, Tally for numbers. New visual primitives shouldn't be invented unless an existing one genuinely can't do the work.
4. **Does it survive the anti-references?** Each top-7 anti-ref is a check; if a proposed design lives inside one of those categories, redesign.

When this document and a downstream design disagree, this document wins. When this document and `product-identity.md` disagree, *product-identity wins*: strategy precedes visual.

---

## Open Threads

- **Section 4 (Tokens)** opens when Phase 1 gamification surfaces begin design. Premature now.
- **Frame variant inventory** (earned, unearned, hover, pinned, premium, etc.) needs explicit enumeration before the badge gallery is rebuilt.
- **Pursuer Card scale variants** (hero / compact / mini / share) need explicit sizes and content rules before the Logbook is designed.
- **Tally typography choice.** The display face for headline numbers is the single highest-leverage type decision; needs a focused exploration when Section 4 opens.
- **Motion vocabulary ownership.** Whether the level-up beat, the Frame hover shimmer, and the Horizon pulse all share a unified easing/timing system or each gets its own. Decision deferred to Section 4.

---

## Related Docs

- [Product Identity](product-identity.md): the strategic frame this visual identity serves. **When this document and product-identity.md disagree, product-identity wins.**
- [Visual Identity References](visual-identity-references.md): curated real-world references for each primitive (Phase A mood-board content). Working doc for sketching, prototyping, and Figma mood boards.
- [Gamification Plan](gamification-plan.md): Phase 1 surfaces (Pursuit home, Logbook, Badge Gallery) are the first major work to be designed natively in this visual identity.
- [Design System Reference](../reference/design-system.md): the existing site-wide tokens, patterns, and component blueprints. Will be refreshed in service of this identity once Section 4 opens.
