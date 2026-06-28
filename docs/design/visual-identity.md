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

**Implementation: the Binder.** The Album concept was prototyped end-to-end as the **Binder Surface** — a literal three-ring binder vessel for the collection, with cover, spine, rings, page tabs, sleeves, and a 3D page-flip Spread view. The Binder is documented as a Surface (a branded container that arranges Frames) in its own section below; see [Surfaces → The Binder](#surfaces-and-the-binder) and [binder-surface.md](binder-surface.md) for the full design + technical reference. The bullets below are the conceptual rationale that the Binder workshop fulfilled.

**Visual character (delivered by the Binder workshop).**

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

**Visual character.** Card vessel with framed avatar, prominent Master Level rendered in the Tally treatment, Pursuer name, active Title, top Job slot, Horizon bar tracking progress to the next tier, and a recent-badge peek row at the bottom built from mini-Frame chrome. Tier-tinted card with diamond corner notches that echo the Frame's. Premium customization is *additive* to the base; never replaces it. Tier itself is achievement-locked and cannot be purchased.

**Workshop status (locked).** The composition above is workshop-locked. Master Level is the sum of all Job levels (RuneScape-style aggregate). Tier brackets: Bronze 1-199, Silver 200-599, Gold 600-1199, Platinum 1200+. Five sizes ship: Hero (Logbook), Default (Pursuit home), Compact (profile header), Share (vertical share card), Mini (comments/leaderboards). Avatar uses a hybrid PSN-default + PlatPursuit customization model. Workshop: [`/design/pursuer-card/`](../../templates/design/pursuer_card_preview.html). Code extraction (to production partials + CSS + JS in the Frame's style) is deferred until the card gets a real product mounting point.

**Customization slots (workshop-locked, four of five).** The card has five customization slots: background texture, frame overlay, particle/animation, title plate, and badge-peek showcase. The first four are workshop-locked with a menu of variants per slot; badge-peek customization is deferred. Whether each variant ships free / via customization currency / via subscription / via seasonal unlock is intentionally not a property of the workshop — that's an economy decision for later. Sibling workshop: [`/design/pursuer-card-customization/`](../../templates/design/pursuer_card_customization_preview.html).

**Anti-patterns.** Generic "user profile card" (avatar circle + username + bio, like every social app). Card louder than the Pursuer inside it. Inconsistent treatments between hero/compact/mini that break the family read. Customization that fragments recognition. Tier override via cosmetic (the tier is achievement-locked, never purchasable).

**Note on existing surfaces.** Not net-new construction. PlatPursuit already has a profile card system and a Pursuer share image. The Pursuer Card primitive is the *unifying language* those existing surfaces should converge on so they read as one thing instead of three.

### The Horizon

**Concept.** The signature treatment for *the path forward.* The glow on the edge of an unfinished progression. The far wall of the planning desk where the next stop is marked but not yet reached.

**Job.** Forward motion. Wherever progress is shown (XP bar, stage progression, tier completion), the Horizon communicates *there's more this way* without literal arrows or "Click to continue" CTAs.

**Where it appears.** XP progress bars on Job rows, tier progression on badges, stage completion bars in series, Pursuer Level toward next milestone, Pursuit home "1 stage to next tier" prompts.

**Visual character.** Subtle gradient at the leading edge of any progress indicator. Possibly a glow plus a faint marker (the next stop). Color carries semantic weight: warmer the closer you are, cooler the further. Gentle pulse on near-completion (the "go finish it" nudge).

**Workshop status (locked).** Four forms ship: linear bar (XP / Job rows / Pursuer-tier progression), stepped pips (discrete-stage progressions), radial arc (compact dashboard tiles, badge-tier completion), and vertical fill (column meters). Color progression locked across six completion levels (5/25/50/75/95/99%) cooler-to-warmer. Tally + Horizon composition is the canonical Job-row pairing. Workshop: [`/design/horizon/`](../../templates/design/horizon_preview.html).

**Extracted (2026-06).** Production implementation: `static/css/components/horizon.css` + `static/js/horizon.js` + the `components/horizon.html` partial. **Two tones**, both load-bearing: `band` (the locked cool→warm completion palette, set by `data-horizon-band` / the `horizon_band` templatetag) and `themed` (an accent/family color via `--horizon-accent`, so family-colored surfaces keep their identity). Forms live as `.pp-horizon__track`/`__fill` (linear), `.pp-horizon--segmented` (pips), and `.pp-horizon--arc` (radial). First mounted on the **Milestones page** (tier-ladder progress + overall/category bars, `band` tone); the Lab / Research Panel will keep `themed`. The partial carries `role="progressbar"` + ARIA value attrs.

**Anti-patterns.** Literal arrows (too directive). Heavy dramatic gradients (it's a hint, not a flag). "Click to continue" CTAs (the horizon is mood, not button). Disconnected from real progress data (must always pair with a real percentage, never decorative-only).

### The Tally

**Concept.** The signature treatment for how numbers, levels, XP, and milestones render. Numbers are first-class material in PlatPursuit; the Tally is what makes them *enjoyable to look at*. It turns a number from information into satisfaction.

**Job.**

1. **Make numbers feel earned, not reported.** A level rendered in the Tally style is a reward, not a label.
2. **Codify how growth shows visually.** The "ticking up" moment (XP fills, a level turns over, a milestone hits) has a unified vocabulary across the app.
3. **Anchor the *rewarding* adjective.** Wherever the visual identity does its rewarding work, the Tally is the primitive doing it.

**Where it appears.** Pursuer Level (headline number on Logbook and Pursuit home), per-Job levels, XP awards during sync, stage and tier completion counts, milestone counters (100th platinum, 50th badge), real-time level-up moments.

**Visual character.** Distinctive type treatment for headline numbers: heavy weight, tabular figures so digits don't shift width, generous breathing room. Ticking-up animation that has *weight* (a roll or flip with mass, not a frantic counter spinning). Level-up moment: brief celebratory beat with a Horizon-style edge glow. Recently-earned numbers carry a subtle "fresh" treatment that decays over hours, so the level you just earned looks different from one earned weeks ago, just briefly.

**Workshop status (locked).** Display face is **Bricolage Grotesque** (variable axes: `opsz` 12-96, `wdth` 75-100, `wght` 200-800). Selected over Inter / Space Grotesk / JetBrains Mono after side-by-side comparison at every scale. The variable-width axis is load-bearing — wider widths read as "headline / monumental," tighter widths as "ledger / inline." Scale ladder spans headline (96px+) down to micro (12px). Tick-up animation, level-up beat, and fresh-decay treatment all locked. Workshop: [`/design/tally/`](../../templates/design/tally_preview.html).

**Extracted (2026-06).** Production implementation: `static/css/components/tally.css` + the `components/tally.html` partial. Base `.pp-tally` carries only the face (Bricolage Grotesque, 800 weight, tabular figures) and is deliberately **metrics-free** — size, `opsz`, line-height, and letter-spacing live on the size rungs `.pp-tally--{hero,xl,lg,md,sm,xs}`, so retrofitting `.pp-tally` onto existing numbers doesn't shift them. `.pp-tally--glow` adds the earned-state edge glow (color via `--pp-tally-glow`). First mounted on the **Milestones page** (overview stat numbers + tier targets). Tick-up animation + fresh-decay stay workshop-only until a surface needs them.

**Anti-patterns.** Generic dashboard metrics (big-bold number, small label, bordered card). Counters that count up from zero (looks arcade-y, kills weight). Numbers as table cells (alignment-driven, makes them data). Levels that feel like stats screen entries instead of rewards. Tally treatment everywhere it could go (must be reserved for *meaningful* numbers).

**Pairs with.** The Horizon. A Job row in the Logbook is *Tally + Horizon*: the level number rendered in Tally style, with an XP progress bar in Horizon style trailing toward the next level. The two primitives compose, they don't compete.

### Stamp and Pin (treatment-level, not pillars)

Earlier drafts of this kit included Stamp (provenance marker) and Pin (wayfinding marker) as core primitives. They were demoted to *small treatments inside Frame and Pursuer Card* because their work overlapped with the larger primitives:

- **Stamp's provenance work** (earn date + tier display) lives in the Frame's earned-state variant. The Frame prototype made this concrete: the earn engraving ("Earn #N") in the plinth is the Stamp's home. The Pursuer's permanent earn-rank is the provenance mark. A small PP-monogram corner stamp also exists as an optional secondary brand mark; whether to ship it is TBD from team feedback.
- **Pin's wayfinding work** (current state, what's next) lives in the Frame's Pinned-state variant and the Horizon primitive. The Frame prototype tested four Pinned treatments (edge glow alone, pin chip + tier glow, pin chip + brand-accent glow, pin chip + accent border + glow); final pick is TBD from team feedback.

They survive as design details (a slightly off-register tier stamp in a badge corner, an active-state highlight for what you're working on) but they don't earn pillar status.

### Surfaces (and the Binder)

The four primitives above are **atomic** — small, repeated units of brand identity used across the product. Some branded elements in PlatPursuit are categorically different: large, one-per-screen **containers** that *arrange* primitives into a coherent metaphor. Those are **Surfaces**.

Surfaces share two properties with primitives: they're brand-recognition vehicles, and they carry the full visual-identity weight (principles, anti-references, locked designs). But they fail the atomicity test — a Surface isn't a small reusable unit, it's a vessel. Categorizing them separately keeps the four-primitive framing crisp.

**The Binder (first Surface, prototype-locked).** The literal trading-card binder that displays the badge collection: three-ring sleeve binder with cover, spine, rings, page tabs, pocket sleeves, page numbers, bookmark. Six views — five binder configurations (Single / Compact / Spread × Binder / Gallery presentation, minus Spread × Gallery which isn't meaningful) plus a sibling sortable list view at `/design/badge-collection/` for power users. Spread mode is the headline interaction: a real 3D page-flip with drag-to-flip + arrow-button affordances, rotating around the spine. The Binder is the implementation of the Album concept named above. Workshop lives at [`/design/binder/`](../../templates/design/binder_preview.html); full design + technical reference in [binder-surface.md](binder-surface.md). Full code extraction (to production partials + CSS + JS, in the style of the Frame component) is deferred until the Binder gets a real product mounting point.

**Future Surfaces.** Plausible siblings the Binder leaves room for: a **Trophy Case** for completed platinums (one-of-one display, plinth-and-pedestal vocabulary), a **Showcase** for the Pursuer Card hero on the Logbook, a **Wall** for milestone displays (100th platinum, badge series completions). Each would be its own Surface entry, not a primitive.

**Anti-patterns for Surfaces** (in addition to the global anti-references in §5):

- Surfaces that ape primitive vocabulary at scale. A Surface should compose primitives, not redraw them in larger sizes. If a Surface needs to look like "a Frame but big," it's misnamed — that's just a Frame variant.
- Skeumorphism. A Binder is a *conceptual* binder, not a photoreal leather one. A Trophy Case is a conceptual display, not a wood-grain shelf. The metaphor lives in silhouette and layout, never in texture replication.
- Surface-on-Surface nesting. A Binder inside a Trophy Case inside a Showcase loses everyone. Surfaces are one-per-screen vessels; if you need composition, use Sections (un-branded layout), not nested Surfaces.

---

## 4. Tokens

**In active development** (2026-06). The bespoke token foundation is being developed
as a workshop at [`/design/style-guide/`](../../templates/design/style_guide_preview.html)
(standalone, DaisyUI-free). Direction is agreed; concrete `@theme` values get promoted
from the workshop into `static/css/input.css` when the first real surface (Badge detail)
is rebuilt, and DaisyUI's theme is re-pointed at them for instant site-wide ownership.
This is a **living layer** — extend it as new surfaces surface new needs.

Agreed direction:

- **Color.** Keep and formalize the existing character (slate surfaces; **cyan =
  primary/platinum** and the one "active / in progress" accent; violet secondary;
  warm-orange forge-spark accent; trophy tiers; semantics), re-homed as owned `--pp-*`
  tokens so templates stop referencing DaisyUI's `base-*`.
- **Type.** **Bricolage Grotesque** (display, locked in the Tally workshop) reserved for
  **hero headlines + numbers only**; **Inter** for body and sub-headers. Scarcity makes
  the display face hit; Inter keeps the broad UI calm and readable.
- **Shape.** Near-square radii + crisp **2px borders** = the "matted, framed artifact"
  read. **Material over drop-shadow** for richness: a top-edge light-catch + faint
  surface gradient ("workbench material") on resting cards; shadow reserved for raised
  surfaces.

Guiding principles for this layer (the test gates):

- **Neon / glow is earned by *state*, never painted on *surfaces*.** It marks active /
  hover / near-completion / earn moments using the one cyan accent (tier tints only for
  tier-specific glows); it never washes backgrounds, body text, or resting chrome.
  Resting = calm matte; glow = reward energy on top. (Keeps us PS-era-premium, not the
  cyberpunk / mobile-game / NFT anti-refs in §5.)
- **Premium substrate, charm seasoning.** Premium is the baseline quality bar on every
  surface, earned through restraint + craft + consistency (NOT more effects). Indie charm
  is deliberate moments on top (flavor, easter eggs, personality), never a coat of paint
  that cheapens. *Prestigious + modern* carry the substrate; *charming + earnest* carry
  the seasoning.
- **Signature moments on a budget.** Invest deep craft in a few canonical beats (badge
  Earn Moment, level-up, first-sync "Pursuer emerges", milestones) and let everything
  else breathe. The calm makes the wow land. Motion is GPU-friendly (transform/opacity),
  honors `prefers-reduced-motion`, and never blocks reading — jank reads as cheap.

Still to formalize as the workshop matures: the concrete type scale, spacing scale,
elevation scale, and the per-surface motion vocabulary (these solidify against real
surfaces rather than in the abstract).

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
3. **Does it use the four signatures correctly?** Frame for badges, Pursuer Card for identity, Horizon for progress, Tally for numbers. New visual primitives shouldn't be invented unless an existing one genuinely can't do the work. If the decision is a *container* arranging multiple primitives, see §3 → Surfaces — Binder is the first; new Surfaces are allowed but must earn their entry.
4. **Does it survive the anti-references?** Each top-7 anti-ref is a check; if a proposed design lives inside one of those categories, redesign.

When this document and a downstream design disagree, this document wins. When this document and `product-identity.md` disagree, *product-identity wins*: strategy precedes visual.

---

## Open Threads

- **Section 4 (Tokens)** opens when Phase 1 gamification surfaces begin design. Premature now.
- **Frame variant inventory** (earned, unearned, hover, pinned, premium, etc.) — resolved for the first four via the Frame prototype (see The Frame § States above). Premium variant deferred until gamification Phase 1 ships.
- **Frame post-feedback decisions** awaiting team review of `templates/design/frame_preview.html`: final Pinned treatment (one of A/B/C/D), whether the PP corner stamp ships alongside the engraving, exact polish on the Earn Moment timing.
- **Badge Gallery as Album → Binder Surface.** The Album concept was prototyped end-to-end as the Binder Surface (six views, page-flip choreography, full binder dressing). The existing production Badge Gallery still predates this kit; the Binder workshop is the visual / interaction reference for its rebuild. Full code extraction of the Binder (to production partials + CSS + JS) is deferred until the surface gets a real product mounting point. See [binder-surface.md](binder-surface.md) for the locked design.
- **Pursuer Card scale variants** — resolved in the Pursuer Card workshop. Five sizes locked: Hero / Default / Compact / Share / Mini (see The Pursuer Card § Workshop status). Content rules per size live in the workshop.
- **Tally typography choice** — resolved in the Tally workshop. Bricolage Grotesque with variable axes (`opsz` / `wdth` / `wght`) selected over Inter / Space Grotesk / JetBrains Mono. The Frame's engraving was a first pass; production Frame engraving should be refreshed to use Bricolage when Frame extraction next sees work.
- **Motion vocabulary ownership** — resolved at the kit level (see § Kit-level vocabulary: Motion + Particles above). Specific easing curves and durations still token-level work for Section 4.
- **Cyan brand accent application** — committed across Frame (blueprint + Pinned), Horizon (cool-end progression color + step backgrounds), and Pursuer Card (Job XP bar leading edge). One brand accent across the kit, calibrated per surface.
- **Code extraction priorities for Tally / Horizon / Pursuer Card.** Frame, **Tally**, and **Horizon** are now extracted to production partial + CSS (+ JS for Horizon) — Tally and Horizon were pulled out for the Milestones rebuild (2026-06), the first real surface to mount them, per the "extract when a surface needs it" rule. The **Pursuer Card** remains workshop-only, awaiting its surface. Remaining extraction follow-up: retrofit the earlier hand-rolled treatments on the Lab / Research Panel / Collection onto the shared Tally/Horizon classes (themed tone).

---

## Related Docs

- [Product Identity](product-identity.md): the strategic frame this visual identity serves. **When this document and product-identity.md disagree, product-identity wins.**
- [Visual Identity References](visual-identity-references.md): curated real-world references for each primitive (Phase A mood-board content). Working doc for sketching, prototyping, and Figma mood boards.
- [Frame design preview](../../templates/design/frame_preview.html) (served at `/design/frame/`): the reference implementation for the Frame primitive. Captures the tier variants, states (Earned / Unearned dim / Unearned blueprint / Pinned), engraving treatment, motion + particle vocabulary, and the full Earn Moment choreography. Where this doc and the prototype disagree on Frame details, the prototype is the source of truth — this doc records the strategic shape, the prototype records the committed implementation.
- [Tally workshop](../../templates/design/tally_preview.html) (served at `/design/tally/`): the typeface showdown, scale ladder, tabular-figure verification, tick-up animation A/B/C, level-up beat, and fresh-decay treatment. Locked: Bricolage Grotesque as the display face.
- [Horizon workshop](../../templates/design/horizon_preview.html) (served at `/design/horizon/`): four forms (linear bar / stepped pips / radial arc / vertical fill), six-step color progression, scale ladder, marker variants, and the canonical Tally + Horizon Job-row composition.
- [Pursuer Card workshop](../../templates/design/pursuer_card_preview.html) (served at `/design/pursuer-card/`): canonical card, four tier states, five sizes (Hero / Default / Compact / Share / Mini), the Frame sibling read, the Tally + Horizon composition inside the card, and the five-slot customization map.
- [Pursuer Card customization workshop](../../templates/design/pursuer_card_customization_preview.html) (served at `/design/pursuer-card-customization/`): sibling deep-dive that workshops the variants per customization slot (background / frame overlay / particle / title plate). Badge-peek showcase deferred. Variants intentionally carry no unlock-type labels (economy decision separate).
- [Binder Surface](binder-surface.md): the first Surface entry. Full design + technical reference for the Binder workshop ([`/design/binder/`](../../templates/design/binder_preview.html)) and its sibling list view ([`/design/badge-collection/`](../../templates/design/badge_collection_list.html)). The Album concept (named in §3) implemented end-to-end.
- [Gamification Plan](gamification-plan.md): Phase 1 surfaces (Pursuit home, Logbook, Badge Gallery) are the first major work to be designed natively in this visual identity.
- [Design System Reference](../reference/design-system.md): the existing site-wide tokens, patterns, and component blueprints. Will be refreshed in service of this identity once Section 4 opens.
