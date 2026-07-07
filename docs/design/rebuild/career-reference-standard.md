# Career Page: The Rebuild Reference Standard

`/career/` (`templates/trophies/career.html` + `trophies/services/career_service.py` + the claim
ceremony) shipped to prod on 2026-07-07 as a complete, polished product. It is the **finished-quality
bar for the site-wide rebuild**: every page rebuilt from here on is measured against it. If a page
doesn't meet these dimensions, it isn't done.

This doc is the *bar*, not a how-to. The how-to lives in
[Design System](../../reference/design-system.md) (tokens, components, responsive) and
[Premium Motion Patterns](../../reference/motion-patterns.md) (motion recipes + GPU gotchas). This
says **what "done" looks like**, using Career as the worked example.

## The polishing mindset: "what would Google / Apple do here?"

This was the single most important practice in getting Career to its quality. Every polish pass asked,
of each moment: *how would a top-tier Google or Apple product handle this?* That question is what
turned "fine" into "premium" over and over -- the damped spring on the rank landing, the specular
sheen sweep, Material shared-axis paging, the mobile fit passes, the GPU-jank fixes all came out of
it. **Apply this lens deliberately when polishing any page.** The gap between amateur and premium is
almost always a specific detail that question surfaces:

- Does it use real physics (spring settle, momentum, overshoot-then-settle) or a flat linear ease?
- Is there anticipation + follow-through, or does the thing just appear?
- Is the exit choreographed as carefully as the entrance?
- Would this micro-interaction feel at home inside iOS / Material, or does it read like a CSS demo?
- What did we deliberately leave OUT? Restraint is the craft -- animating everything is the amateur
  tell (see [motion-patterns](../../reference/motion-patterns.md) principle 3).

## The bar: dimensions every rebuilt page must meet

### 1. Design coherence
- Composes the Visual Identity primitives (Frame, Pursuer Card, Horizon, Tally) instead of inventing
  one-offs; uses `--pp-*` / `--disc-*` / `--rank-*` tokens, never hex one-offs.
- **Chrome is a frame, not a module.** Nav / sub-nav / hotbar / footer are the fixed frame; only the
  page content is "the page." (This is why the modal recede scales `#page-recede`, not the chrome.)
- Every surface reads like it belongs to the same app as the rest of the rebuild.

### 2. Responsive: three layouts, mobile-first, actually verified
- Correct at 375 / 360 / iPhone-SE (375x667) -- verified on-device-size, not assumed.
- **Fit passes are real work.** When a dense surface (the claim ceremony) overflowed a short phone,
  we compacted the tile *airiness* on mobile (padding, gap, bar height, subtitle margin) and restored
  it at `md:` -- never shrinking what carries weight (icon, name). Omit the truly expendable block on
  mobile (the recap chips) rather than introduce a scroll.
- No horizontal scroll on the body, ever.

### 3. Premium motion (signature moments on a budget)
- One genuine "wow" per surface (the claim ceremony), not motion everywhere.
- Apple/Google-grade physics: damped **spring settle** on landings, **specular sheen** sweeps,
  Material shared-axis paging, staggered reveal cascades, count-ups, reveal-on-visit
  (IntersectionObserver so a tab animates in with fresh values when shown).
- Honors `prefers-reduced-motion` on every beat; animates only
  `transform` / `opacity` / `box-shadow` / `filter`.

### 4. Performance discipline
- Per-user querysets DB-aggregate (whale-safe) -- see the DB-aggregation rule in
  [CLAUDE.md](../../../CLAUDE.md).
- **GPU cost is first-class, especially on tall mobile pages.** Don't transition `filter` on
  page-sized elements (dim via a translucent overlay instead); isolate per-frame animations that sit
  over a `backdrop-filter` onto their own layer (`will-change: transform`). See motion-patterns Gotchas.

### 5. Interaction polish
- Modals: grow-from-source flip, portal out of the scaled wrapper, content-only step-away recede,
  focus trap, drag-to-dismiss, choreographed exit.
- Hover glow (not scale) on cards; visible focus indicators; loading / empty / error states covered.

### 6. State & URL coherence
- The URL reflects the shareable state: the active tab in `?view=`, and view-specific params scoped
  to their view (contract filters live in the URL only on the Contracts tab).
- After a mutation that changes server-derived display (a claim), refresh the affected surfaces so
  the DOM never lies to the user.

## Reference files

| Beat | Files |
|------|-------|
| The whole surface | `templates/trophies/career.html`, `trophies/services/career_service.py` |
| Claim ceremony (the signature moment) | `static/js/claim-ceremony.js`, `static/css/components/claim-ceremony.css` |
| Rank ladder + label/bar pulse + charging strain | `static/css/components/elements.css` (`.pgl--rank`) |
| Modal recede / content-only step-away | `elements.css` (`.pp-receded`, `#page-recede`), `templates/base.html` |

## Gotchas and Pitfalls

- **Don't cargo-cult the ceremony onto every page.** The "one signature moment" is per-surface; most
  pages earn their polish through coherent tokens + reveals + interaction states, not a takeover.
- **The bar is holistic.** A page isn't "done" because it looks right at desktop width -- the mobile
  fit pass, reduced-motion paths, and performance checks are part of the same bar.
- **Career is the bar, not a component library.** Reuse the *patterns*
  ([design-system](../../reference/design-system.md), [motion-patterns](../../reference/motion-patterns.md)),
  not copy-pasted Career markup.
- **The Google/Apple lens is a question, not a coat of paint.** Applied late as decoration it reads as
  noise; applied as "how should this *behave*" it reads as premium.

## Related Docs

- [Visual Identity](../visual-identity.md) -- the constitution Career satisfies (the motion philosophy).
- [Design System](../../reference/design-system.md) -- tokens / components / responsive how-to.
- [Premium Motion Patterns](../../reference/motion-patterns.md) -- the motion recipes + GPU gotchas.
