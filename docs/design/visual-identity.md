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

### The fabrication layer (added during the Frame prototype)

The explorer's-office framing still holds, but the Frame prototype made a second truth visible: badges aren't just *displayed*, they're *made*. Earning a badge is a forge moment, not just a wall-mount moment. The welding-and-scanning language that emerged from the prototype (build sparks, border weld, scan beams, engraving etch) is canon: the Frame is the artifact, but *the fabrication of the artifact* is part of the identity.

The card is a 2026 collector's piece being made on a 2026 workbench, then displayed in a 2026 archive. The three Brief reads (collector / adventurer / number-watcher) all sit on top of a fourth quiet read: *maker*. The Frame's Earn Moment is the canonical demonstration of this layer; future earn moments for other primitives should draw from the same vocabulary.

**The kind of industrial we are.** "Industrial" is wide; most of it is off-brand for us. The flavor we mean is the **modern artisan's workshop** — the Patek watchmaker's bench, the knifemaker's forge, the bespoke restoration shop, the Hasselblad assembly room. Precision craft. Single-piece work. Hand attention. Mid-century industrial design (Bauhaus, Eames, modernist factory) is adjacent and safe: honest materials, function-first. Everything else is off-limits:

- **Cyberpunk industrial** (Blade Runner neon factories, ghost-in-the-shell) → drifts into anti-ref #5 (ironic / cool-detached).
- **Steampunk** (Victorian machines, brass cogs, leather + bronze) → anti-ref #2 (fantasy cosplay).
- **Factory-floor commodity production** (assembly lines, mass-scale, undifferentiated) → anti-ref #1 (sterile / generic).

When a future design decision invokes the industrial vibe, the test is: *would this fit in a Patek watchmaker's atelier?* If yes, on-brand. If it'd fit in a sci-fi factory or a steampunk machine room or an IKEA flat-pack warehouse, redesign.

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

### The Album (badge gallery as trading-card binder)

If the Frame turns a badge into a trading card, the Badge Gallery turns the collection into an album. The trading-card metaphor doesn't stop at the individual card; it extends to the surface that displays the collection. The Badge Gallery is the kit's canonical "binder / display" — slots for each card, labeled spaces, the satisfaction of seeing your collection housed.

**Visual character (to be designed; current Badge Gallery predates this kit).**

- Distinct **slots** per badge, each clearly labeled (series, name) — not a wall of identically-spaced grid cards.
- Empty slots are **visible and named**. The "I haven't earned that one yet" gap is part of the collector's pull; an empty slot should look like a real place a card belongs, not a missing data row.
- Series grouping: pages or sections by series, not one flat alphabetical wall.
- Subtle slot chrome that feels like high-quality binder pages (the sleeves), not generic CSS grid cells.
- A finished collection (all slots filled) is a visible achievement; an in-progress collection is visibly in-progress.

**Where it appears.** Badge Gallery as a primary surface. Smaller manifestations in the Logbook hero, on profile pages, on series detail pages.

**Anti-patterns.** A generic Tailwind grid of cards (loses the album-ness). A wall of identical `[?]` placeholders (no differentiation, kills the collector pull). Card-per-cell with no slot chrome (the slot itself is part of the metaphor — without it, it's just a grid).

**Future expansion.** When non-badge trading cards eventually ship (Pursuer cards as a literal collectible, series cards, game cards), the album concept extends to those surfaces. The Badge Gallery is the first album; not the only one.

### Kit-level vocabulary: Motion + Particles

The four primitives share a motion and particle language that the Frame prototype made visible. Future primitives (Pursuer Card, Horizon, Tally) inherit this language rather than reinventing it. The vocabulary is just as load-bearing as the trading-card idiom; both serve brand recognition.

**Motion vocabulary (baseline, applied across all primitives).**

- Slow, multi-property, desynced. The Frame's hover uses translate, rotate, and scale on separate timing curves; never a single monolithic animation.
- Organic, never frantic. Durations measured in hundreds-of-ms to seconds, easing favors cubic-bezier with subtle overshoot, never `linear` outside of mechanical effects like scan beams.
- Functional, not decorative. Every motion has a job: a lift presents, a wobble adds weight, a scan transforms.

**Particle vocabulary (welding-tier signature, reserved for fabrication and earn moments).**

- **Hot welding sparks** — white-yellow-orange radial gradients with warm box-shadows. Carry the metaphor that the badge is being made from molten metal.
- **Tier-tinted molten sparks** — Bronze / Silver / Gold / Platinum-colored particles mixed alongside hot sparks. The frame is literally being made out of its tier material.
- **Scan beams** — bright horizontal or vertical sweeps used to indicate transformative passes (the uncloak, the back-face reveal, future level-ups). Always hot orange-white at the leading edge.
- Trajectories arc and fall under gravity. Never straight-line. Never confetti-style explosion.

**Where it appears today.** Frame's Earn Moment uses the full vocabulary. **Where it appears next.** Tally's level-up beat, Horizon's progression milestones, Pursuer Card unlock moments — all should draw from this vocabulary, not invent local one-offs.

**Anti-patterns for motion + particles.** Confetti bursts on routine actions. Sparkles on hover states. Frantic counters. "Achievement Unlocked!" pop-ups. Generic mobile-game gamification (see anti-ref #4). The vocabulary is reserved for *meaningful* moments; everywhere else, the kit breathes.

### The Frame (headline primitive)

**Concept.** The PlatPursuit-branded housing that surrounds badge artwork. Binder slot, museum case, graded-card sleeve. The Frame is the *brand*; the artwork inside is the *content*.

**Job.**

1. **Brand recognition.** A badge spotted in any context (Twitter, Discord, share image, screenshot) reads as PlatPursuit at a glance. The Frame is the brand mark.
2. **Container for state and metadata.** Badge name, earn date, tier indicator, count, owner all live on or in the Frame, never floating loose.
3. **Treatment of mode.** Earned/unearned, hover, pinned, premium are expressed via Frame variants. The artwork stays untouched; the Frame carries the mode.

**Where it appears.** Badge Gallery, Badge detail hero, Pursuit home active progressions, cross-link panels on Game detail, share images, notifications, recap slides. Anywhere a badge renders.

**Visual character (committed via the Frame prototype).**

- Bordered shape (1px solid earned, 1px dashed unearned) that surrounds the artwork without crowding it.
- Corner notches at all four corners as the brand mark — diamond-shaped, tier-colored, the same shape across every Frame.
- Tier reinforcement is *layered* across chrome, art backdrop, and notches. (Originally drafted as "light"; the prototype proved a fully-tier-tinted card reads as more cohesive and survives customization layers without losing its tier identity.)
- Hover: gentle multi-property motion (translate + rotate + scale on separate easing curves) plus a tier-tinted gleam sweep. (Originally drafted as "breathing scale + foil shimmer"; the multi-property motion proved more organic and is now the kit-level hover language.)
- Earn engraving lives in the plinth: a small italic "Earn #N" line treated as if etched into chrome with a faux-deboss text-shadow. Tabular figures. The N is the Pursuer's permanent earn-rank — locked in forever, the limited-edition serial of the trading-card metaphor. Subtle pulse on the rare #1 case.
- Each Frame is flippable. Click-to-flip reveals a back face with description, stats (earn date, stages, rarity, next tier), and footer.

**States.**

| State | Visual treatment |
|---|---|
| Earned | Solid tier border. Tier-tinted chrome + art backdrop. Full-color badge artwork. Engraving visible in plinth. |
| Unearned · dim (default lock) | Dashed tier border. Diagonal-stripe veil over art. Lock icon centered. "?" ribbon. Engraving slot reserved (placeholder, height-matched). |
| Unearned · blueprint | Dashed tier border. Art container becomes cyan blueprint grid on dark indigo. Badge layers desaturated + dimmed. "Fabricating" banner across art top. Lock cyan-tinted. Builds incrementally bottom-up as stages clear (mask reveals by completion %). |
| Pinned | Solid tier border. Tier-tinted edge glow (slow pulse on Gold + Platinum). "Pursuing" ribbon. Optional map-pin chip at top-left corner. Exact treatment (tier-coupled vs brand-accent-decoupled) TBD from prototype A/B/C/D options. |

**The Earn Moment.**

The choreographed transition from Unearned-blueprint to Earned. Plays once when the final stage clears. ~20 seconds. Twelve phases, in order:

1. **Build pulse** — final stage clears; weld sparks fly; build mask climbs to 100%.
2. **Cooling** — card lifts off the surface; banner exits, lock fades, construction line cools.
3. **Border weld lap** — welding torch traces the card's perimeter; each side flips from dashed to solid as the torch finishes it. The frame is sealed.
4. **Sealed pulse** — brief warm border glow as the seal settles.
5. **Uncloak scan** — horizontal beam sweeps bottom-to-top over the full card; blueprint grid + cyan veil dissolve in the beam's wake; title and plinth text brighten as the beam passes.
6. Strip blueprint and unearned class hooks.
7. **Completion sheen** — diagonal sheen sweep across the card as the "stamp of completion."
8. **Front engraving etch** — welding head traces the actual text bounds of "Earn #N" with hot sparks; engraving glows hot then cools to tier color.
9. **Flip to back.**
10. **Back scan** — vertical beam sweeps right-to-left over the back face; description, stats, and footer reveal in its wake.
11. **Flip back to front.**
12. **Settle** — card descends from its lift with a tier-tinted glow flourish; rests at hover elevation.

The Earn Moment is the canonical demonstration of the kit's motion + particle vocabulary. The reference implementation lives in `templates/design/frame_preview.html`. Future earn moments (Pursuer level-up, Horizon completion) should draw from this same vocabulary, not invent new one-offs.

**Anti-patterns.** Heavy ornate scrollwork (period piece). Generic rounded card (no identity). Frame that visually shouts (the art should be the loudest element). Treatment that varies wildly between tiers (Bronze to Platinum should feel like the same family, not different products). Greyscale unearned state (reads "disabled," wrong feeling).

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

- **Stamp's provenance work** (earn date + tier display) lives in the Frame's earned-state variant. The Frame prototype made this concrete: the earn engraving ("Earn #N") in the plinth is the Stamp's home. The Pursuer's permanent earn-rank is the provenance mark. A small PP-monogram corner stamp also exists as an optional secondary brand mark; whether to ship it is TBD from team feedback.
- **Pin's wayfinding work** (current state, what's next) lives in the Frame's Pinned-state variant and the Horizon primitive. The Frame prototype tested four Pinned treatments (edge glow alone, pin chip + tier glow, pin chip + brand-accent glow, pin chip + accent border + glow); final pick is TBD from team feedback.

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

**Cyan as the kit accent.** The Frame prototype introduced a luminous teal-cyan (oklch ≈ 55% 0.2 hue 206, the project's primary token) in two places: the blueprint mode's grid + ribbon styling, and the Pinned-state edge glow option. This is deliberately PSN-era informed — the cool luminous accent on dark surfaces, the DualSense / PS5 mood, but never their actual colors. The kit treats cyan as *the* brand accent for "currently active" and "in progress" cues, distinct from tier colors. The Horizon (which the doc calls out as "cooler the further from completion") should inherit this same cyan rather than introducing a competing accent. The Pursuer Card's "currently working on" affordances should too. One brand accent across the kit, not three.

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
- **Frame variant inventory** (earned, unearned, hover, pinned, premium, etc.) — resolved for the first four via the Frame prototype (see The Frame § States above). Premium variant deferred until gamification Phase 1 ships.
- **Frame post-feedback decisions** awaiting team review of `templates/design/frame_preview.html`: final Pinned treatment (one of A/B/C/D), whether the PP corner stamp ships alongside the engraving, exact polish on the Earn Moment timing.
- **Badge Gallery as Album.** The existing Badge Gallery predates this kit and renders badges in a generic grid. A rebuild informed by the Album concept (slots, labels, series grouping, named empty slots) is the natural next surface after the Frame primitive is locked. Same priority as Pursuer Card sizing.
- **Pursuer Card scale variants** (hero / compact / mini / share) need explicit sizes and content rules before the Logbook is designed.
- **Tally typography choice.** The display face for headline numbers is the single highest-leverage type decision; needs a focused exploration when Section 4 opens. The engraving's tabular-figure treatment in the Frame is a first pass; the Tally type may or may not reuse it.
- **Motion vocabulary ownership** — resolved at the kit level (see § Kit-level vocabulary: Motion + Particles above). Specific easing curves and durations still token-level work for Section 4.
- **Cyan brand accent application** — committed in the Frame prototype for blueprint + Pinned. Horizon and Pursuer Card should inherit; final calibration when each is designed.

---

## Related Docs

- [Product Identity](product-identity.md): the strategic frame this visual identity serves. **When this document and product-identity.md disagree, product-identity wins.**
- [Visual Identity References](visual-identity-references.md): curated real-world references for each primitive (Phase A mood-board content). Working doc for sketching, prototyping, and Figma mood boards.
- [Frame design preview](../../templates/design/frame_preview.html) (served at `/design/frame/`): the reference implementation for the Frame primitive. Captures the tier variants, states (Earned / Unearned dim / Unearned blueprint / Pinned), engraving treatment, motion + particle vocabulary, and the full Earn Moment choreography. Where this doc and the prototype disagree on Frame details, the prototype is the source of truth — this doc records the strategic shape, the prototype records the committed implementation.
- [Gamification Plan](gamification-plan.md): Phase 1 surfaces (Pursuit home, Logbook, Badge Gallery) are the first major work to be designed natively in this visual identity.
- [Design System Reference](../reference/design-system.md): the existing site-wide tokens, patterns, and component blueprints. Will be refreshed in service of this identity once Section 4 opens.
