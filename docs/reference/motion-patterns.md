# Premium Motion Patterns

The actionable, page-builder-facing companion to the motion **philosophy** in
[Visual Identity → Kit-level vocabulary: Motion + Particles](../design/visual-identity.md). That
doc says *what* our motion should feel like (multi-property, functional-not-decorative, signature
moments on a budget, neon earned by state, never confetti). This doc captures *how* to get there:
the "premium feel" principles we learned building the Pursuer Card forge + the home motion pass,
and the concrete CSS recipes/gotchas that make motion read professional instead of amateur.

**Consult this before adding motion to any page.** The difference between "premium" and "cheap" is
almost always one of these specifics.

## Why premium motion reads premium (principles)

These came out of a "what would Google/Apple do here?" pass on a border animation that felt cheap.

1. **Traveling light beats a drawn line.** A soft *moving glow* (a comet, or a gradient flowing
   around a border) reads high-end; a hard, flat, self-drawing stroke reads like a CSS demo.
   Diffuse light also hides the speed unevenness in #5.
2. **Hierarchy: one calm persistent state + one lively accent.** Don't animate the whole object. A
   faint steady marker *plus* a single traveling highlight beats a busy full-object animation.
3. **Restraint is the craft.** Animating everything = busy = cheap, the opposite of premium. The
   taste is in what you leave OUT. On the home we deliberately skipped shimmer/skeletons (nothing
   is actually async there) and per-cover marquee pops (noise). This is "signature moments on a
   budget" applied.
4. **Smooth + snap-free.** A perpetual redraw that resets with a visible jump reads cheap. Make
   loops rest at a neutral/"empty" state at the wrap so there's no snap.
5. **Constant arc-length, not angular.** A `conic-gradient` sweeps by *angle*; on a non-square
   shape the "pen" visibly speeds up on the long sides and crawls at the corners — your eye reads
   it as "off." Fixes: use *diffuse glow* (hides it), or an SVG `stroke-dashoffset` trace (true
   constant arc-length along the path).
6. **Neon is a transient state, never resting chrome.** Motion + glow belong to earn moments and
   *acknowledgment* states (a "new" marker that clears on hover), not to surfaces at rest. Reinforces
   the Visual Identity "neon earned by state" rule.
7. **Numbers that count up feel live.** Roll headline integers on reveal; the reveal's own opacity
   masks the reset to zero so there's no flicker.
8. **GPU-friendly + reduced-motion, always.** Animate only `transform` / `opacity` / `box-shadow` /
   `filter`; honor `prefers-reduced-motion`; never block reading. Jank reads as cheap.

## CSS recipes (the how-to)

### Fade a glow — never pop it
`filter: none` → `filter: drop-shadow(...)` does **not** interpolate; it snaps. Give the resting
state a **zero-magnitude** version of the same filter so only the size animates:
```css
.thing        { filter: drop-shadow(0 0 0 <color>); transition: filter .25s ease; }
.thing:hover  { filter: drop-shadow(0 0 7px <color>); }
```
Same rule for any `none` → function transition (e.g. `box-shadow`, `transform`).

### Draw a glow ABOVE full-bleed content
An `inset` box-shadow on an element whose `<img>` fills it is painted *under* the image and never
shows. Put the ring/glow on a `::after` overlay with a `z-index` above the image. For a border that
follows rounded corners, use the conic + border-mask trick:
```css
.el::after {
  content: ""; position: absolute; inset: 0; z-index: 3; border-radius: inherit; padding: 3px;
  background: conic-gradient(from var(--angle), /* ... */);
  -webkit-mask: linear-gradient(#000 0 0) content-box, linear-gradient(#000 0 0);
  -webkit-mask-composite: xor;
          mask: linear-gradient(#000 0 0) content-box, linear-gradient(#000 0 0);
          mask-composite: exclude;   /* border-box minus content-box = the ring */
}
```
Animate the sweep with a registered `@property <angle>` (see Gotchas for degradation).

### Entrance reveals without a flash (FOUC)
Two safe options — never leave content at `opacity: 0` with no fallback:
- **Pure CSS** (used on the home): `animation: settle .55s ease both;`. The `both` fill applies the
  hidden `from` state from the *first paint* (no flash) with no JS dependency. Stagger with
  `animation-delay: calc(var(--rev) * .07s)`. Reduced-motion → `animation: none` (content visible).
- **JS-gated** (for IntersectionObserver scroll-reveals): add a `.js-motion` class to the root
  *before paint*, and gate the hidden state on it, so content is visible when JS is off.

### Header content cascade (the opening beat)
Every rebuilt page's header/hero opens the same way, and the shared way is a **cascade of the content**,
not a move of the whole card — the staggered rise reads livelier and more deliberate. Put
**`.pp-head-cascade`** (`components/motion.css`) on the header's **card-body**; its direct children fade +
rise (9px) in sequence (title, then stats, then the rest — three beats, then the rest share the last).
`backwards` fill = no flash; reduced-motion gated. Career runs it on **both** its stacked cards (hero +
summary). If the header lives inside an HTMX swap island (badge detail rides `#badge-tier-view`), gate the
class to first load only — `{% if not is_tier_swap %} pp-head-cascade{% endif %}` — or it replays on every
swap. Live on Career, Collection, Badges list, Badge detail.

### Directional view switch (shared axis)
When a view/tab toggle swaps content, slide the incoming panel in **from the side it lives on** — forward
in the tab order enters from the right, backward from the left (Material's "shared axis"), so the switch
has a sense of place. Classes **`.pp-view-in-right` / `.pp-view-in-left`** (`components/motion.css`,
translateX ±16px + fade, 0.28s, reduced-motion gated) applied by the helper **`PlatPursuit.slideViewIn(
panel, fromName, toName, order)`** (picks the direction from `order`, restarts the animation, adds the
class). Two host patterns:
- **JS toggle** (Career tabs, Collection Case/Gallery/List — panels pre-rendered, toggled via `hidden`):
  capture the outgoing view, flip `hidden`, call the helper on the now-shown panel.
- **HTMX island swap** (Badges Series/Gallery): keep a `lastView`; in `htmx:afterSwap` on the island, call
  the helper on the swapped-in root with `lastView → new`, then update `lastView`.
Reference: the claim ceremony's shared-axis paging (`claim-ceremony.js`).

### Tab "ignite" — the active chip comes alive
When a segmented-switcher chip becomes active, bloom its glow once and settle flat back into its resting
`.is-active` state (a small "this is live now" acknowledgment, not a persistent glow — neon is a transient
state). Class **`.pp-tab-ignite`** (`components/motion.css`, a box-shadow bloom, **no** fill-mode so it
hands back to the chip's own styles) applied one-shot by **`PlatPursuit.igniteTab(chip)`** (restart-safe
remove→reflow→add; reduced-motion no-ops). Fire it on every activation (click, keyboard, and once on load
so the active pill greets you). The keyframe uses `rgba`, never `color-mix` — lightningcss strips
`color-mix` from `@keyframes`. Pairs with `wireTablist` (see [js-utilities](js-utilities.md)); live on the
Career, Collection, and Badges switchers.

### Don't let clip containers cut hover states
`overflow: hidden`, `overflow-x: auto` (which forces `overflow-y: auto`), and `clip-path` all clip a
child's hover scale/glow. Fixes:
- Give the container vertical breathing room: `padding-block: 5px`, or `clip-path: inset(-14px 0
  -14px 0)` (clip horizontally, breathe vertically).
- Or gate the clip so it's only on when needed — e.g. the Pursuer Card shelf is *unclipped at rest*
  (so hover shows fully) and only clips *during* the forge, when nothing is hovered.

### Count-up
Roll integers only — parse `\D` out, and skip percentages/formatted text (they'd parse wrong). Fire
as the section reveals so opacity masks the 0-reset. Reduced-motion: show the final value. Reuse the
`tickUp` pattern in `static/js/home-motion.js`.

### Damped spring landing (Apple-style settle)
A thing that rises into place should *settle*, not glide to a stop. Own the bounce in the keyframe
stops — overshoot past the mark, a small undershoot, a smaller second overshoot, then rest — and use
an **expo-out** timing so the animation doesn't add its own overshoot on top (double-bounce reads
wrong). Pair with a short **specular sheen** (a skewed, `mix-blend-mode: screen` gradient band swept
across the landed element) timed to fire *just after* it settles:
```css
@keyframes landIn {
  0%   { opacity: 0; transform: translateY(44px) scale(.78); }
  52%  { opacity: 1; transform: translateY(-3px) scale(1.09); }  /* overshoot */
  70%  { transform: translateY(1px) scale(.978); }               /* undershoot */
  85%  { transform: translateY(0)   scale(1.014); }              /* smaller 2nd overshoot */
  100% { opacity: 1; transform: translateY(0) scale(1); }
}
.el { animation: landIn 1s cubic-bezier(.16,1,.3,1) both; }   /* expo-out lets the stops own the bounce */
```
Reference: the rank hand-off in `claim-ceremony.css` (`ccxHoToIn` + `ccxHoSheen`).

### Persistent acknowledgment markers
A "you have something new" marker should persist until acted on: add the class, then
`el.addEventListener('pointerenter', clear, { once: true })`. `{ once: true }` = no listener leak,
and `pointerenter` covers mouse hover *and* touch tap.

## Reference implementations

| Beat | Files |
|------|-------|
| Forge reveal + flowing-edge "new" marker + slot-in shift | `static/css/components/pursuer-card-forge.css`, `static/js/pursuer-card-forge.js` |
| Home entrance settle + count-ups + hover glows | `static/css/components/home.css`, `static/js/home-motion.js` |
| Claim ceremony: spring settle, specular sheen, shared-axis paging, staggered blooms, control-gating | `static/js/claim-ceremony.js`, `static/css/components/claim-ceremony.css` (the rebuild's signature moment -- see [Career Reference Standard](../design/rebuild/career-reference-standard.md)) |
| The Frame Earn Moment (the canonical "wow") | `templates/design/frame_preview.html` (see [Frame Component](frame-component.md)) |

## Gotchas and Pitfalls

- **`filter: none` → drop-shadow snaps.** Give the resting state a zero-blur drop-shadow of the same
  colour so only the blur animates.
- **Inset shadow under a full-bleed image is invisible.** Draw glows/rings on a `::after` overlay
  above the image, not as an inset shadow on the element itself.
- **`overflow` / `clip-path` cut hover glow + scale.** Add breathing room, or gate the clip to only
  when it's actually needed.
- **`conic-gradient` has uneven angular speed on non-square shapes.** Hide it with a diffuse glow, or
  use an SVG `stroke-dashoffset` trace for true constant-speed motion.
- **`@property` / `mask` aren't universal** (older Safari; Firefox `@property` since 128). A conic
  driven by an animated `@property` angle renders *static* where unsupported — make sure that
  degradation still reads as an intentional marker (it does for the "new" ring: a steady lit border).
- **Perpetual full redraws read cheap.** Loop by resting at an empty/neutral state at the wrap.
- **Never *transition* `filter` on a page-sized element.** A filter re-rasterizes its whole subtree
  every frame its input changes, so transitioning `filter: brightness` on the page (or scaling a page
  behind a static one) stutters — brutal on tall mobile layouts. Dim via a translucent overlay (the
  scrim/backdrop you already have), not a page-level filter. (Career's `.pp-receded` recede: scale the
  content, let the overlay do the dim.)
- **`backdrop-filter: blur` re-samples on *any* repaint in its stacking context.** Per-frame
  animations near it (count-ups rewriting text, SVG glyph draws) force a full re-blur of the page
  behind it each frame — so *only the animating elements* stutter, and only on tall mobile pages.
  Promote the animating element's container to its own compositor layer (`will-change: transform`) so
  its repaints don't dirty the backdrop. (See [career-reference-standard](../design/rebuild/career-reference-standard.md)
  and the memory on mobile GPU jank.)
- **Confetti is off-brand.** `canvas-confetti` / `static/js/celebrations.js` violate anti-reference
  #4 — do **not** reuse them. Draw from the Frame's fabrication vocabulary (weld / scan-beam /
  arcing sparks) instead.
- **Animating everything is the amateur tell.** Restraint is what reads as premium.

## Related Docs

- [Visual Identity](../design/visual-identity.md): the motion **philosophy** this doc implements
  (§ Kit-level vocabulary: Motion + Particles; § Neon earned by state; anti-references).
- [Design System](design-system.md): styling tokens, responsive patterns, component blueprints
  these beats are built on.
- [Frame Component](frame-component.md): the Earn Moment reference + reduced-motion handling.
