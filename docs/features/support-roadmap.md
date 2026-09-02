# Support Roadmap

`/support/roadmap/` (`users.views.SupportRoadmapView`) — the public product roadmap, presented in
the site's own platinum-roadmap frame: upcoming features as icon cards in three **certainty
tiers**, nothing backward-looking. The storefront (`/support/`) carries a band that compresses the
same content as a teaser (tier chips + three feature miniatures each).

## The content model

Everything renders from two constants in `users/constants.py`:

| Constant | Holds | Edit it to |
|---|---|---|
| `ROADMAP_TIERS` | `(key, display name, subline)` per certainty tier | rename a tier or its subline |
| `ROADMAP_FEATURES` | `{key, tier, name, blurb}` per feature, in display order | add / promote / retire a feature |

**Adding a feature** is one dict in `ROADMAP_FEATURES` plus one `{% elif %}` branch in
`templates/support/_roadmap_icon.html` (the keyed icon dispatch, Lucide-language 24x24 stroke
SVGs). Skipping the icon fails `test_every_feature_key_has_its_own_icon` loudly — the partial's
compass fallback is for genuinely unknown keys, not a quiet default.

**Promoting a feature** (wishlist → up next → in the works, as dreams become real) is editing its
`tier` value. Both surfaces follow automatically; the band shows each tier's first three.

**Adding a tier** is one row in `ROADMAP_TIERS` plus its features. The page sections, the band's
teaser and the test-suite's tier list all derive from the constant, so nothing else needs touching.

## The content rules (test-enforced)

- **No dates, months, quarters, years — ever, at any tier.** The moment a date slips in, the
  roadmap becomes a promise ledger. `test_the_forward_content_promises_no_dates` sweeps every
  feature's name and blurb.
- **No counts or percentages anywhere on the page** (`test_no_counts_anywhere_in_the_forward_body`).
- **The wishlist labels itself as dreams** ("Dreams. No promises here, just direction.") —
  load-bearing copy, pinned by test. It lets ambitious items live on the page without becoming
  commitments.
- **Tier is the only promise.** Certainty, not time.

## The presentation vocabulary

Status and distance ride the **tier chips** and **icon tiles**, temperature-coded to match the
Horizon ramp: *in the works* lit in the accent (the chip breathes, page only), *up next* cool,
*the wishlist* dashed outline — dreams drawn in outline. The variants live in
`static/css/components/support-roadmap.css` keyed on `is-works` / `is-next` / `is-wishlist`
**container classes** (deliberately not `.rm-tier`-scoped, so the storefront band's miniatures
inherit them). The ask card's CTA hosts the storefront buy button's star emitter — the button
previews its destination's motion.

Reveals are scroll-gated: an inline IntersectionObserver arms each tier (`.rm-armed`) and flips
`.is-in` on viewport entry; CSS owns all motion inside `prefers-reduced-motion: no-preference`
gates. Without JS nothing is ever hidden (`test_nothing_is_hidden_without_javascript`).

## Gotchas and Pitfalls

- **The stagger index must restart per tier.** Features are grouped into per-tier lists in the
  view precisely so `forloop.counter0` starts at 0 in each section — a global index once left
  later tiers' first cards invisible for half a second after they had scrolled into view.
- **No project-board links.** The in-house answer is the ask card's line that members shape the
  roadmap in the Discord (which is also a membership perk). Do not embed Trello/GitHub boards.
- **`is-works` / `is-next` / `is-wishlist` are generic container keys.** They are shared between
  the page and the band on purpose; check for collisions before reusing those class names on
  unrelated components.
- **History lives in one lede sentence.** The page deliberately renders no shipped-work cards,
  tallies or progress strips — that was tried (stage trail + Horizon pips) and removed when the
  page pivoted forward-only. Resist re-adding a past section here; the storefront's serve band
  already brags for the past.
