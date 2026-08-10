# Rarity

One vocabulary, one grading function, one presentation, for anything in the product that can be rare.

Rarity was born inside the badge system and stayed there, so by mid-2026 five surfaces were
hand-rolling their own presentation off two shared tokens — 26 CSS rule blocks, no shared component.
Adding rarity to a sixth surface meant writing a seventh copy. This is the extraction.

---

## The model

**Rarity is community-level and absolute.** It describes the *thing*, not your relationship to it. A
Mythic title is Mythic whether or not you hold it, and it reads identically for every hunter. Nothing
in the grading path takes a profile.

**The denominator is the eligible population, not the whole userbase.** For badges that is the series'
*pursuers* (profiles with a `SeriesBadgeStanding` row — real progress). Against the whole userbase
almost everything reads Mythic, which makes the scale useless.

**The numerator is whatever the surface actually prints.** For a group badge that is its
`earned_count`; for a *title* it is the title's holders, because a title is granted by earning **any**
live edition and is therefore strictly easier than any single edition. Grading a different population
from the one displayed is how a card ends up reading "Mythic · 44,210 wearing".

| Grade | Earned by | Glyph |
|---|---|---|
| Mythic | < 5% of the eligible population | sparkle |
| Rare | < 15% | diamond |
| Uncommon | < 35% | dot |
| Common | everything else | none |
| *Be the first* | 0 earners — **not a grade** | none |

`0` earners is unearned, not an achievement, so it never wears a prestige grade.

---

## The ratchet

**A grade may rise, but never fall.**

Rarity is derived from a denominator that only grows, so without a floor a grade drifts downward as
more people earn the thing: a hunter logs in and their Mythic has become Rare. That makes the grade a
weather report rather than a property of the item, and it can quietly take something away.

`GroupBadge.rarity_floor_pct` and `Title.rarity_floor_pct` store the lowest percentage each has ever
reached. `rarity.effective_pct` grades from `min(live, floor)`.

The floor is still community-level — it belongs to the thing and is identical for everyone. Freezing
per-hunter at earn time would be *worse*: two people would see different grades for the same item,
which is not how anyone reads rarity.

**The percentage shown is always the live one.** Only the *class* honours the floor. A ratcheted
percentage would be a lie about the community: the number says how many, the grade says how rare.

Maintained nightly by [`recalc_rarity_floors`](../guides/cron-jobs.md#recalc_rarity_floors).

---

## The code

| Piece | Where | What it owns |
|---|---|---|
| Vocabulary + grading | `trophies/services/rarity.py` | classes, thresholds, labels, icon map, `rarity_for()`, `effective_pct()` |
| Badge-specific population | `trophies/services/badge_rarity.py` | `group_rarity()` (a badge-flavoured name), `annotate_group_rarity()` for DB-side filtering |
| The grade label | `templates/components/rarity_grade.html` | glyph + name + percentage, for the standard composition |
| The scale + material | `static/css/components/rarity.css` | `--rar-*` per grade, `.pp-rarity`, `.pp-rarity-surface`, `.pp-rarity-gem` |
| Glyph sprite | `templates/components/_frame_rarity_sprite.html` | auto-mounted in `base.html`; pages never include it |

### The visual language

Rarity is **tint *and* material**, on one axis. Hue gives the fast cold read (which grade is this, at a
glance); finish gives the premium one (this object is made of something better). Both climb together
and both hang off the same `data-rarity`, so they cannot disagree.

The ramp runs **mint → emerald**, deepening as it climbs (96 → 89 → 81 → 72 lightness). It replaced
four unrelated hues (white / pale green / gold / near-white) that **inverted at the top**: mythic sat
at 94% lightness, back toward where common lives, so the loudest grade on the site was *rare* — the
second-rarest. A single axis cannot invert. Lightness carrying the signal also means the order survives
every form of colour blindness, which green-versus-gold did not.

### Using it

`data-rarity` is the hook, **not** a modifier class. It declares the `--rar-*` properties, and custom
properties inherit — so a card carries `data-rarity` once and both the label inside it and the card's
own material read the same declaration.

```html
<article class="my-card pp-rarity-surface" data-rarity="{{ thing.rarity_class }}">
  <h3>{{ thing.name }}</h3>
  {% include 'components/rarity_grade.html' with rarity_class=thing.rarity_class pct=thing.rarity_pct %}
</article>
```

**Composition is deliberately per-surface.** Frame shows only a percentage, the browse gallery only a
name, badge detail adds a gem and a sub-line. Forcing one rigid partial on all of them would need a
pile of `show_x` flags and be worse than the duplication it replaced. What is shared is the *scale* and
the *styling*: a surface with its own composition still writes `color: var(--rar-c)` rather than
re-declaring four grade colours.

---

## Gotchas and Pitfalls

- **Defaults live on the components, not only on `[data-rarity]`.** Plenty of things are gradeable but
  ungraded (a one-off award, a series with no pursuer base). Without a default their `--rar-*` are
  undefined, the `color-mix()` is invalid, and `.pp-rarity-surface` paints **no background at all** — it
  vanishes into the page. This shipped once.
- **Surface CSS must not paint its own background over `.pp-rarity-surface`.** Component files import
  *after* `rarity.css`, so at equal specificity a local `background` silently wins and every card
  renders the same flat grey. Give the shared surface the ground; keep shape and state local.
- **Never sink the specular with `z-index: -1`.** Mythic's sweep is animated, and the transform promotes
  the pseudo-element to its own composited layer, where a negative z-index paints it **over** the
  content and blanks the card. Lift the content (`.pp-rarity-surface > * { z-index: 1 }`) instead.
- **The gem's halo derives from `--rar-c`, not `--rar-glow`.** `--rar-glow` is deliberately absent below
  Rare so text doesn't glow at every grade — but a gem is an object and reads flat without one,
  uncommon included. Two jobs, two sources.
- **Green now means rarity.** `--pp-success` is emerald, so a green "done"/"yours" marker sitting beside
  a grade reads as the same signal. Titles' "Yours" moved to `--pp-text-dim` for exactly this. Check any
  new surface that puts a success state next to a grade.
- **A floor of `NULL` means "no floor", not zero.** Until `recalc_rarity_floors` first runs, everything
  grades live. Correct, not broken.
- **`GroupBadge.rarity_pct` / `rarity_rank` / `rarity_class` are dead scaffolding**, kept only because
  they are surfaced read-only in admin. They are NOT the ratchet floor and nothing reads them for
  display. Don't wire new code to them.

---

## Related Docs

- [cron-jobs](../guides/cron-jobs.md#recalc_rarity_floors) — the nightly floor sweep
- [design-system](design-system.md) — where rarity sits in the wider token set
- [badge-backend-rebuild](../design/rebuild/badge-backend-rebuild.md) — the live-read philosophy this follows
