# Pursuer Avatar

> **Status: DESIGN CAPTURE (2026-10-17).** Nothing is built in the product. The direction was
> approved by the owner after three rounds of mockups; the mockups, their drawing code and the
> screenshot tooling live in [mockups/](mockups/). This doc records what was decided, what was only
> proposed, what was tried and set aside, and what is still open.

A customizable, code-drawn character that becomes each hunter's identity on PlatPursuit and their
Pursuer in [the Frontier](../frontier/README.md). A small chibi robot explorer with a screen visor and
big expressive eyes, standing on a display stand like a collectible figure. Parts unlock through the
existing gamification (rank, disciplines, badges, platinums, Contracts), and the whole figure is drawn
from code, so one recipe renders every size and every finish.

---

## Why

A PSN avatar is picked from Sony's catalog. It says nothing about the person as a hunter, and PSN has
no avatar builder (unlike Xbox). A PlatPursuit avatar is the one place a hunter's history can show up
*on them*: a wearable trophy case built from what they earned. Nobody else in the trophy space has one.

The method is the one StepTracker (Glimmlings) uses: all art drawn in code. It was chosen for the same
reasons: one part becomes many looks through parameters, recipes are versionable, and no artist pipeline
is needed. The *style* is deliberately not Glimmlings' bubbly one.

---

## Decisions

### Owner-decided (2026-10-17)

| Decision | Note |
|---|---|
| Build a code-drawn avatar tied to badges and gamification | Accepted that it is a lot of content: "you have to start somewhere" |
| The avatar **is** the Frontier character | One character, two homes. Logged as Frontier [D63](../frontier/decisions.md#d63-the-pursuer-avatar-is-the-frontier-character) |
| The **chibi robot head with big expressive eyes** is the anchor | Called "the best thing we've seen yet" |
| **Full body**, not a head-and-shoulders bust | Raised by the owner, alongside Astro Bot as the reference for feeling |
| **All art is authored in code.** No drawing in a vector tool and importing | Owner: an imported drawing "will look much worse than what we have now" |
| The **ranger costume** in mockup 3 is the quality bar | "Head and shoulders above what we had before, we could definitely work with something like this" |

### Proposed, not yet confirmed

| Proposal | Why |
|---|---|
| **Opt-in.** The PSN avatar stays the default until a hunter builds one | Nobody's identity changes without their choice |
| **Identity parts never lock** (helmet shape, eyes, base colors) | Unlocks are for gear, outfits and finish: things you earn, never who you are |
| **Unlocks follow rules**, not per-item authoring | A new badge or Contract produces its part automatically. See [Unlocks](#unlocks) |
| **Earned, never rolled.** No random trait rolls, no scarcity ranking of parts | Random traits with rarity is the NFT profile-picture look, an [anti-reference](../visual-identity.md#5-what-we-are-not) |
| **Premium is a dial here too.** No member-only parts | [Premium = Membership](../rebuild/premium-proposal.md): members never get objects free users cannot reach |
| **Gear found in the Frontier shows on the avatar** | Makes the game layer visible on the main site. Open: see [Open questions](#open-questions) |
| **Decide where it mounts before building** | The [Pursuer Card](../pursuer-card-orphaned.md) was finished and then mounted on nothing |

---

## Art direction

### The head is the anchor

A large helmet (dome, scout, faceted or crested) with a dark glass screen. The eyes are glowing
shapes on the screen in the hunter's **discipline color**, and they carry all the expression: bright
pills at rest, happy arcs when celebrating, plus calm, determined and star shapes. Eyes blink and the
head glances around at idle. The head is roughly half the figure's height, which is what makes it
read at small sizes.

### The body is a small vinyl toy

A short rounded torso, stubby legs, short arms and mitten hands. The body is kept small and simple on
purpose so the head carries the character (the same choice Astro Bot makes). The vinyl-toy read also
fits the display-stand framing and the finish ladder below.

### Costumes change the silhouette

A costume that is a decal on a box torso looks cheap; that was the failure of mockup 2. A good costume
changes the outline. The polished reference is the **ranger**: a cape with a ragged hem, a shoulder
mantle with stitched trim, a hood bunched under the helmet, a quiver with arrows over the shoulder, a
diagonal strap, belt, pouch and bracers. Costumes are **archetypes** ("the ranger", "the ace pilot"),
never a specific game's character (same rule as Frontier [D62](../frontier/decisions.md#d62-companions-are-archetypes-never-characters)).

### One shading system for every part

Every surface is lit from the top left by one shared helper: a shadow crescent on the lower right, a
faint bounce rim inside it, a soft vinyl gloss on the upper left, and a dark edge so the shape reads on
a dark page. This is what makes cloth, leather and metal look like the same object as the helmet. Flat
fills were the other half of why mockup 2's body looked unfinished.

### Rank sets the finish

The helmet, the metal trim and the display stand use the same five-step ladder as the Pursuer Card.
The finish is read from the hunter's **live rank**, never saved in the recipe.

| Finish | Ranks | Treatment |
|---|---|---|
| Matte | Newbie, Recruit | Flat slate metal, no light catch |
| Steel | Seeker, Hunter | Bright neutral metal, top-edge light |
| Violet | Ranger, Warden, Marshal | Violet metal, gem notches, soft glow |
| Platinum | Vanquisher, Paragon | Cyan platinum metal, brighter notches |
| Radiant | Luminary, Ascendant | Near-white glass, glowing notches, a moving sheen |

### One recipe, two framings

The same recipe renders the **full figure** on its stand (profile, share cards, the Frontier) and a
**head crop** inside a square frame (nav, leaderboards, hunter cards, 28 to 120px). The crop is a fixed
transform over the same drawing, so it costs nothing extra to author.

### Motion, with restraint

Idle: a gentle bob, blinks, an occasional look-around, the cape swaying. **Earn a platinum:**
anticipation crouch, a jump with the arms opening into a V, happy eyes, a platinum trophy popping
overhead with sparkles, the cape trailing on the way up and billowing on landing, a squash on
landing and a settle. The celebration is for meaningful moments only. Celebrating every action is
the generic mobile-game gamification [anti-reference](../visual-identity.md#5-what-we-are-not).

---

## Astro Bot: borrow the feeling, not the look

PlayStation players know Astro Bot, and the owner raised it as a reference. The
[love-letter rule](../visual-identity.md#lower-risk-anti-references-worth-flagging) governs it: the
result must read as a homage from a PlayStation player, never as a Sony copy to a Sony lawyer.

| What Astro Bot does well | How the Pursuer does it |
|---|---|
| Chibi proportions | An explorer's silhouette: suit, pack and boots, in discipline colors |
| Expressive screen eyes | Eyes glow in the hunter's discipline color and change shape with the moment |
| Costumes that celebrate games | Archetype outfits. Never a specific franchise's character |
| Joyful little moments | A celebration when a platinum lands. Everyday actions stay calm |

**Never:** a white body with a blue visor (Astro's own look), PlayStation button shapes or logos, or
costumes of Sony's or anyone's characters.

---

## Unlocks

Proposed sources. The specific thresholds in the mockups are examples, not decisions.

| Source | Unlocks | Example |
|---|---|---|
| Rank | Finish (helmet metal, trim, stand) and some parts | Halo topper at Luminary |
| Discipline levels | Themed parts | Ranger outfit at Exploration level 10 |
| Badge tiers | Signature parts | Crested helmet from any badge at Gold |
| Platinum milestones and rarity | Accessories | Star eyes from an Ultra Rare platinum |
| Contracts | Gear and held items | Map from 10 Contracts completed |
| The Frontier (later) | Equipped gear shows on the figure | See open questions |

Locked parts can be **previewed** on your own figure, marked as locked. "Randomize" only rolls parts
the hunter owns.

---

## Relationship to the Frontier

The owner decided the avatar is the Frontier's character ([D63](../frontier/decisions.md#d63-the-pursuer-avatar-is-the-frontier-character)).
That gives the Frontier a face without breaking its menu-game rule: the figure is a **character
portrait and status figure**, not a character walking around a depicted world. The Frontier still has
no rooms, maps or tiles. It also starts to answer the Frontier's open question
[Does the game layer produce anything visible on the main site?](../frontier/open-questions.md#does-the-game-layer-produce-anything-visible-on-the-main-site):
the avatar can be that visible thread.

The Frontier notes already sketched a **crest / sigil from SVG parts** for the base
([base-and-shifts.md](../frontier/base-and-shifts.md)). Mockup 1's Crest direction is that idea, and it
belongs to the base, not to the avatar.

---

## Technical approach (as prototyped)

- **Pure drawing functions.** `figure.js` builds SVG strings from a recipe, a finish tier and an accent
  color. No canvas, no images, no libraries.
- **Recipe = parts plus a version**, e.g. `pp-pursuer/v1 h0.e0.s1.p0.b0.i0.t0 exploration` in the mockup.
  The finish is not part of the recipe; it comes from live rank at render time.
- **Versioned recipes** (a Glimmlings practice): never redraw a shipped part in place. A changed part
  is a new version, so nobody's saved figure changes under them.
- **Rendering paths are not decided.** The prototype renders client-side. Share cards already render
  through Playwright, so that path exists. Pages that show many avatars (leaderboards, hunter cards)
  will want a cached static SVG per recipe, finish and accent, not live JS per row. PlatBot / Discord
  would need a PNG.

### Process: never draw blind

Polish came from a **render, look, adjust loop**, not from writing coordinates and hoping. Mockup 2's
body was drawn blind in one pass and failed; mockup 3 went through several screenshot rounds and passed.
`mockups/harness.html` renders a contact sheet of variants (poses, finishes, disciplines, crops), and
`mockups/shot.py` screenshots it with Playwright. Any new costume should go through the same loop.

---

## How we got here

| Round | What it showed | Outcome |
|---|---|---|
| Mockup 1 ([avatar-lab.html](mockups/avatar-lab.html)) | Three directions: Explorer (helmeted bust), Hunter (face and hair builder), Crest (heraldic sigil) | Explorer direction chosen. Hunter set aside (most parts to draw, faces are the hardest thing to make look good). Crest belongs to the Frontier base |
| Mockup 2 | Full-body chibi, Astro-inspired, celebration pose | Head approved. Body and costumes were the weakest part: a box torso, sticker costumes, flat fills |
| Mockup 3 ([character-lab.html](mockups/character-lab.html)) | Vinyl-toy body, shared shading, the polished ranger, cape motion | Approved as the bar |

**Set aside along the way:**

- **Drawing parts in Figma or Inkscape and importing the paths.** Proposed by Claude as a polish lever;
  rejected by the owner. Everything is authored in code.
- **Costumes as overlays on the torso.** They must change the silhouette.
- **The Ace pilot and Heavy armor outfits** from mockup 2. Dropped from mockup 3 because they were
  built for the old body; they return only after the ranger-level treatment.

---

## File map

| File | Purpose |
|---|---|
| [mockups/figure.js](mockups/figure.js) | The drawing code: head, vinyl body, shading helper, ranger and field suit, packs, boots, held items, stand, crop framing |
| [mockups/character-lab.html](mockups/character-lab.html) | Mockup 3, self-contained: builder, framings, finish ladder, the celebration (a published copy of `figure.js` is inlined) |
| [mockups/avatar-lab.html](mockups/avatar-lab.html) | Mockup 1: the three original directions |
| [mockups/harness.html](mockups/harness.html) | Contact sheet of variants for the screenshot loop (loads `figure.js`) |
| [mockups/shot.py](mockups/shot.py) | Playwright screenshot: `python shot.py harness.html out.png` |
| [mockups/contact-sheet.png](mockups/contact-sheet.png) | Reference render of mockup 3 variants |
| [mockups/jump-strip.png](mockups/jump-strip.png) | Four frames of the platinum celebration |

`static/css/input.css` excludes `mockups/` from Tailwind's repo-wide class scan (its inline CSS
otherwise adds a junk `text-wrap` utility).

---

## Open questions

### Blocks building

- **Where does it mount first?** Nav avatar, profile header, hunter cards, leaderboards, share cards,
  Career. Needs an answer before any build (the Pursuer Card lesson).
- **How is it rendered on the server?** Client JS, a server-side port, or Playwright pre-rendering to
  cached SVG/PNG. Many-avatar pages need cached static output.
- **Where is the recipe stored**, and what invalidates cached renders (a rank change changes the finish).
- **What shows for hunters who never build one?** Proposed: their PSN avatar, unchanged.

### Blocks content

- **How does Frontier gear relate to the outfit slot?** Gear is the Frontier's only power axis, forged
  and discipline-affine ([D58](../frontier/decisions.md#d58-gear-is-forged-not-found),
  [D59](../frontier/decisions.md#d59-gear-affinity-is-by-discipline)). Whether equipped gear *is* the
  outfit, or a separate visual layer, is undecided.
- **Launch roster.** Roughly six polished outfits was the estimate. Each is a few hours of in-code
  authoring plus the screenshot loop, and each recolors by discipline and refinishes by rank.
- **The Pursuer's fiction.** The explorer suit leans "explorer", which ties into the Frontier's open
  question [What is the Pursuer's verb?](../frontier/open-questions.md#what-is-the-pursuers-verb).
- **Which moments celebrate?** A platinum, yes. Badge tiers, level-ups, Contract completions need a
  deliberate list so it never becomes noise.

### Smaller

- Performance and battery budget for animated figures on mobile; static crops in lists.
- Alt text generated from the recipe; reduced-motion behavior beyond "no idle animation".
- Naming (the figure, the builder, the stand).
- Sequencing against in-flight lanes; likely alongside the Frontier, since it is the same character.

---

## Gotchas and Pitfalls

- **Never draw blind.** Render and look at every pass. The one body drawn without looking failed review.
- **WAAPI: never put `easing` on the whole effect** of a keyframed animation. It warps the keyframe
  offsets; the jump landed in the first third and the mid-jump frame showed the figure on its stand.
  Put easing on each keyframe segment.
- **Pivots come from bounding boxes.** Parts rotate with `transform-box: fill-box`, so the pivot moves
  whenever the bbox does. An invisible anchor circle centers the held item's bbox on the hand; anything
  drawn past that anchor (a glow) silently shifts the pivot. The arm's own anchor must stay below the
  shoulder or the arm pivots from the wrong point.
- **Mirror coordinates, not transforms.** Right-side parts are mirrored by rewriting the path
  (`mirrorD`), which only works on absolute `x,y` pairs (no `H`/`V`/`A` shorthand). A mirror transform
  would also mirror the lighting.
- **Unique IDs per render.** Gradients and clip paths are per-render; several avatars on one page
  collide without the per-render uid.
- **The raised-arm pose must clear the helmet.** The head is half the figure, so arms raised straight
  up vanish behind it. The cheer is a V at about 120 degrees.
- **The head crop is a hard-coded transform** over the head's coordinates. Moving or resizing the head
  breaks every crop.
- **Glow filters cost paint.** A `drop-shadow` filter on an animated figure repaints every frame. The
  full figure carries its glow in the stand instead; keep filters to small static crops (see the
  mobile GPU guidance in [motion-patterns](../../reference/motion-patterns.md)).
- **No trait rarity.** The moment parts are rolled or ranked by scarcity, it reads as an NFT avatar.

---

## Related Docs

- [The Frontier](../frontier/README.md): the game this character lives in. See D63 in its
  [decision log](../frontier/decisions.md) and the base crest in [base-and-shifts.md](../frontier/base-and-shifts.md).
- [Visual Identity](../visual-identity.md): the adjectives, the anti-references and the love-letter rule.
- [Orphaned primitives](../pursuer-card-orphaned.md): why the mount point gets decided first. The
  finish ladder is borrowed from the Pursuer Card's rank escalation.
- [Premium = Membership](../rebuild/premium-proposal.md): the dial-not-door rule that applies to parts.
- [Gamification Plan](../gamification-plan.md): the earlier "framed avatar, customization post-v1" and
  cosmetic store ideas this supersedes as a direction.
