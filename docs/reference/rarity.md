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

**The denominator is the whole community** — every PSN-linked account (`rarity.community_size`). One
cached scalar, shared by everything gradeable.

It replaced a per-series *pursuer* base, and the reason matters: a pursuer row is **deleted** when a
profile's progress in a series drops to zero, so that denominator could *shrink* — letting a badge look
rarer because people abandoned the series, which says nothing about the badge. A linked-account base
only grows, so the number moves for real reasons only. It is also what the legacy `Badge` model always
used, and what PSN's own trophy rarity means, so "2.1%" reads here the way hunters already read it
everywhere else.

**The numerator is whatever the surface actually prints.** For a group badge that is its
`earned_count`; for a *title* it is the title's **holders** — everyone granted it — because a title is
granted by earning **any** live edition and is therefore strictly easier than any single edition.
Grading a different population from the one displayed is how a card ends up reading "Mythic · 44,210
earned".

Holders, never *wearers*. Only one of a hunter's titles can be equipped at a time, so counting
`is_displayed` would measure which title people currently like best, not who earned it — and would grade
almost everything Mythic for a reason that has nothing to do with difficulty.

Title holders count **`source_type='badge_series'` only**. The Titles page surfaces the new badge system
alone, so a legacy `'badge'` or one-off `'milestone'` grant in the numerator would be measured against a
denominator that knows nothing about it.

| Grade | Earned by | Glyph |
|---|---|---|
| Mythic | < 1% of the community | sparkle |
| Rare | < 5% | diamond |
| Uncommon | < 20% | dot |
| Common | everything else | none |
| *Be the first* | 0 earners — **not a grade** | none |

These are calibrated for a whole-community denominator (and match both the legacy `Badge` model and
PSN's trophy rarity). The previous 5/15/35 set was tuned for "of people who tried it"; against the full
community it would grade almost everything Mythic.

`0` earners is unearned, not an achievement — and note 0% is under *every* ceiling, so the arithmetic
would happily call it Mythic. It gets the "Be the first" nudge instead, which is a better use of the
space and a small CTA into engaging with the badge.

---

## Grades drift, and that is intended

The thresholds are **fixed**; the population underneath them is not. Both numbers grow, but the
denominator grows faster as more hunters start a series, so a percentage climbs over time and a grade
can fall with it — a title earned by 3% of pursuers at launch may read Uncommon a year later.

This is the model, not a bug. The grade describes the thing **as it stands today**, and "under 5% of
the people who tried it" means the same thing in year three as it did in week one. A fixed scale over a
live population is the whole point.

It was briefly built the other way (a stored floor, so a grade could rise but never fall) and removed
before it shipped: it added a column, a nightly job and a migration to preserve a number that is less
true than the live one. **Don't re-add it without a deliberate decision** — if a grade "wrongly" drops,
the answer is to revisit `RARITY_THRESHOLDS`, not to freeze history.

---

## The code

| Piece | Where | What it owns |
|---|---|---|
| Vocabulary + grading | `trophies/services/rarity.py` | classes, thresholds, labels, icon map, `rarity_for()` |
| Badge-specific population | `trophies/services/badge_rarity.py` | `group_rarity()` (a badge-flavoured name), `annotate_group_rarity()` for DB-side filtering |
| The grade label | `templates/components/rarity_grade.html` | glyph + name + percentage, for the standard composition |
| The scale + material | `static/css/components/rarity.css` | `--rar-*` per grade, `.pp-rarity`, `.pp-rarity-surface`, `.pp-rarity-gem` |
| Glyph sprite | `templates/components/_frame_rarity_sprite.html` | auto-mounted in `base.html`; pages never include it |

### The visual language

Rarity is **tint *and* material**, on one axis. Hue gives the fast cold read (which grade is this, at a
glance); finish gives the premium one (this object is made of something better). Both climb together
and both hang off the same `data-rarity`, so they cannot disagree.

The ramp runs **neutral → teal → indigo → magenta**. Hue does the separating (~90° per step) while
chroma and glow climb with it, so the scale still escalates monotonically.

Two earlier palettes failed, in opposite directions, and both are worth remembering:

1. **Four unrelated hues** (white / pale green / gold / near-white) **inverted at the top** — mythic sat
   at 94% lightness, back toward where common lives, so the loudest grade on the site was *rare*.
2. **One mint→emerald ramp** fixed the inversion but separated grades by **lightness alone**, which
   collapses at chip size: on a dense wall uncommon and mythic both read as "greenish", and because
   mythic was the *darkest* step it read **quieter** than the second-commonest grade.

Both were caught by rendering 32 mixed cells at wall scale, neither by reading the values. A palette
that sorts fine as four big swatches can be unusable as forty small ones — always judge it at the
density it will actually be seen.

Every hue clears the four **tier metals** (gold, platinum's pale cyan, bronze's tan, silver's grey),
which matters because rarity sits *on cards that already colour themselves by tier*. It also vacates
`--pp-success` (162), so green can go back to meaning "done / yours" alone. Rare deliberately lands on
`--pp-secondary`'s hue: a house colour rather than an invention.

Colour is never the only channel. The grade is always spelled out and each wears its own glyph
(dot / diamond / sparkle) — that, not the hue, is what carries the scale under colour blindness.

**Tint and edge percentages are not transferable between palettes.** They are percentages *of a hue*, so
a higher-chroma colour lands heavier at an identical number: magenta at the emerald ramp's 20% washed the
plate pink enough to outshout its own label. Re-tune by rendering when the palette moves.

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
- **Rarity no longer borrows green — keep it that way.** The emerald ramp collided with `--pp-success`,
  so a "done"/"yours" marker beside a grade read as the same signal (Titles' "Yours" moved to
  `--pp-text-dim` because of it, and can stay there). Any future palette should stay off 162, and off the
  four tier metals, for the same reason: rarity is never the only thing on the card.
- **A filtered numerator is only as good as the rows behind it.** Titles grade on `UserTitle` rows with
  `source_type='badge_series'`, but the *held* check on the same page filters nothing — so a title
  granted under another source showed as yours, equippable, and graded **"Be the first"** at the same
  time. Two causes, both now fixed: `grant_series_title` fires only on the `award` branch (a badge earned
  before its series had a title never got one, and re-running `evaluate_badges` can't help — the diff is
  empty), and `get_or_create` returned a legacy row on a shared Title without recording anything. It now
  adopts; `sync_series_titles` backfills the history. **Whenever a numerator is filtered more narrowly
  than the display that sits beside it, that gap will eventually be a wrong grade.**
- **A grade can't be rarer than the easiest way to get the thing.** A title is the UNION of its editions'
  earners, so a title reading 0.7% while one of its editions reads 78% is arithmetically impossible and
  means the numerator is under-recorded — a useful smoke test on any new gradeable surface.
- **A mythic badge needs >100 accounts to exist.** With a 1% ceiling, one earner in a community of 80
  is 1.25% — Rare, not Mythic. Tests that want a Mythic fixture have to seed a community past 100.
- **The denominator is cached for an hour** (`rarity:community_size`). Viewer-independent and slow-moving,
  so staleness cannot change a grade noticeably — but tests must clear the key between cases or the first
  one to grade anything fixes the denominator for the whole session. `conftest` does this autouse.
- **`GroupBadge.rarity_pct` / `rarity_rank` / `rarity_class` are dead scaffolding**, kept only because
  they are surfaced read-only in admin. Nothing reads them for display — grading is live. Don't wire
  new code to them, and don't mistake them for a stored grade.

---

## Related Docs

- [design-system](design-system.md) — where rarity sits in the wider token set
- [badge-backend-rebuild](../design/rebuild/badge-backend-rebuild.md) — the live-read philosophy this follows
