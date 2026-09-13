# The Frontier: Content Model

> Status: BRAINSTORM CAPTURE (2026-09-12). Not committed. See [README](README.md).

This doc covers the shape of the map, how content is gated, how it gets authored, and the two
hard constraints (the pre-2008 trophy gap and the IP line).

---

## The shape of the map

Two axes, deliberately separated.

| Axis | Meaning | Gated by |
|------|---------|----------|
| **Lateral: era** | Which region of PlayStation history you are in | Nothing. Free choice, any order |
| **Vertical: depth** | How legendary and hard-to-find the contents are | Pursuer level, absolutely |

From the hub, all eras are open. The player picks whichever appeals to them, in any order, at
no penalty. What they cannot do is go **deep** in any of them early.

### Why not escalating costs for later branches

An earlier proposal was that picking one era makes the others more expensive to open. It was
dropped because it **punishes breadth**, and breadth is exactly the behavior we want. It also
creates a feels-bad optimization problem (pick correctly first or waste levels) and makes side
branches impossible to gate, since you cannot predict the player's route.

Absolute depth gates solve that outright. Depth gates are plain numbers that apply identically
on every route, so route order never has to be predicted.

### Depth is legend, not decay

The deep strata hold the rare, the storied, and the hard-to-find. See
[vision.md](vision.md#tone-celebration-not-decay) for why the earlier "decay" framing was
rejected.

**How new eras enter.** A new generation arrives as a **shallow region**: nothing in it is
legendary yet, everything is current, and every player can enter it immediately, which is what
new content needs. It deepens over the years as its games become classics. Canonization, not
rot.

The other lever for new content is **deepening existing regions** rather than only adding new
ones.

### The two playstyles pay differently

Neither is punished:

- **Going deep** pays rarity, prestige, and the scarce collectibles.
- **Going wide** pays variety, more of the map, and more complete card and disc sets.

---

## Branch types

Each era region is a hub with exits. Roughly ordered by how numerous they would be:

| Branch | Gated by | Notes |
|--------|----------|-------|
| **Genre / theme pockets** | Job or discipline level | The most numerous by far. A Mind-gated puzzle vault, a Combat-gated arena |
| **Franchise strata** | Badge progress | Opens with any stage earned in the series, extends with more |
| **Studio wings** | Company-related progress | Uses existing `ConceptCompany` data |
| **Platform pockets** | Platform-specific progress | Vita, PSP, VR. A genuinely underserved corner the people who care about it would adore |
| **Rarity vaults** | Owning ultra-rare platinums | Small, brutal, prestigious |
| **Preservation sites** | Various | Built around games whose platinums are actually unobtainable. The one place decay belongs |
| **Seasonal expeditions** | Time | Authored, timed. The clock we control |

### Branches reconnect

**A pure tree dead-ends everywhere and reads as a checklist.** Some branches should rejoin the
spine a stratum or two down, so a player who explored sideways arrives somewhere a
depth-runner has not. Reconnection is what turns a tree into a place.

---

## Gate types

| Gate | Example |
|------|---------|
| Total level | Pursuer Lv. 160 |
| Single job | Survivalist Lv. 30 |
| Job combination | Slayer 20 + Exorcist 20 |
| Discipline | Mind Lv. 90 |
| Discipline spread | Any three above 60 |
| Badge progress | Any stage in a series, more stages for deeper zones |
| Village | Watchtower Lv. 5 |

### The main path rule

> **Main path gates on total Pursuer level only. Side content gates on specifics.**

Nobody is ever hard-blocked from progressing because they dislike a genre. Specialists still
get things generalists never see. This preserves the long-standing rule that PlatPursuit
should pull players toward a **direction**, never assign them a specific game.

### Gated does not mean empty

A gate with nothing to do behind it is a wall. While level-gated, a player should always have:
farming for rare drops, incomplete card and disc sets, and village build-out.

### Badge gating specifics

Two guards, decided:

1. **Franchise worlds are side content, never the main path.** Otherwise a player who dislikes
   a series is permanently walled off from progression.
2. **Access is permanent once earned.** If a badge lapses into maintenance, the world stays
   open. Losing a world because a badge went stale would punish the player for something
   outside the game.

---

## The pre-2008 gap

**Trophy hunting only exists from late in the PS3 era, roughly 2008.** Most of what makes this
premise emotionally powerful (PS1, PS2, early PS3) predates the entire trophy system and is
therefore absent from our player data.

The split is cleaner than it first appears:

| Data | Coverage |
|------|----------|
| **IGDB** (genres, themes, franchises, companies, release dates, covers) | All eras, back to the beginning |
| **Player data** (trophies, platinum rarity, badges, Contracts) | 2008 onward only |

So **zone membership, disc sets, and franchise grouping work fine for every era.** What breaks
for pre-2008 content is anything keyed to personal progress or completion rarity:

- Card rarity cannot key to platinum rarity
- Badge progress cannot directly gate a PS1 zone
- Contracts and job XP do not exist back there

### The franchise bridge

> **Your modern hunting earns you passage into the history of the series you love.**

You cannot have earned anything in the PS2 entry of a series. But your **current** badge
progress in that series can be the key that opens it.

This is better than if the data had been there. The pre-trophy eras become the **mythic
past**: unreachable by ordinary progress, entered only through the legacy of what came after.
The oldest strata should feel different from the rest, and now they structurally are.

### Consequences to design around

- Pre-2008 zones are the **most authored** parts of the game, since data cannot carry them.
- Their collectibles need a rarity source that is not platinum rarity. Candidates: authored
  rarity tiers, real-world scarcity of the physical release, IGDB rating or significance.
  **Open question.**
- Their currency is the **collection layer** (discs, artifacts, sets) rather than the
  completion layer, which usefully makes the old eras feel mechanically distinct.

---

## The authoring model

> **Generation is for coverage. Authoring is for everything a player reads.**

This mirrors how the badge system already works: staff-curated lists over IGDB data, where the
curation is the value.

| Handled by data | Handled by a person |
|-----------------|---------------------|
| Which games belong to a zone | The zone itself: its name, its atmosphere, its copy |
| Gating math | The landmarks and what makes them worth reaching |
| Rarity where trophy data exists | Encounter flavor and enemy design |
| Cross-references to trophy lists, roadmaps, reviews | Disc set curation and why a set matters |
| Era membership | The memorial corner, seasonal events, all narrative |

The skeleton can be combinatorial (era x genre x theme x franchise x platform), which
generates an enormous map cheaply. **The flavor is written.** Procedural bones, authored
highlights.

### Implication: the authoring tools are a primary product

If authoring is the differentiator, the admin surface ships **alongside the first zone, not
after ten**. The existing Admin Hub (`/staff/`) is the foundation. Whatever the content model
turns out to be, the tool to write it is part of the same deliverable.

---

## IP boundaries

| Fine | Not fine |
|------|----------|
| Naming a real game or franchise (factual, nominative) | A zone populated by a franchise's characters or creatures |
| Displaying cover art the way the site already does | Recreating a specific game's setting as content |
| Era aesthetics (low-poly, fog, dithering, chunky UI) | A specific console or controller's protected design |
| Hardware archetypes (a disc, a memory card, a save file) | Trademarked hardware names used as our own product names |
| Genre and theme archetypes | A zone literally named after a copyrighted title |

### The rule

> **The franchise gates the door. The archetype furnishes the room.**

Earning stages in a survival-horror series opens a survival-horror world. So does progress in
another one. Several badges unlock overlapping worlds, the content stays reusable across all
of them, and the player still feels their series is what got them in. You get the emotional
payoff without building derivative content per series.

A worked example of the right instinct: a foggy town with a rusted otherworld and a masked
figure who stalks a fixed route. Every horror fan knows exactly what it evokes. It is not
anyone's property.

**The risk is not a letter.** It is building a whole content pipeline on the wrong pattern and
having to rip it out after it is load-bearing.

---

## Gotchas and Pitfalls

- **Depth gates must be absolute, not relative to the player's route.** The moment gating
  depends on what a player picked first, side-branch tuning becomes unsolvable.
- **Do not let franchise worlds onto the main path.** They are the one gate type a player can
  be permanently unable to satisfy.
- **Anything keyed to platinum rarity silently breaks pre-2008.** Check every collectible,
  every gate, and every stat against the 2008 line.
- **A branch that never reconnects is a dead end.** Budget reconnections into the map from the
  start; retrofitting them means re-tuning every gate along the path.
- **Era skins must not change layout structure.** Only surface treatment. See
  [vision.md](vision.md#the-theming-approach).

---

## Related Docs

- [vision.md](vision.md), [systems.md](systems.md), [decisions.md](decisions.md)
- [IGDB Integration](../../architecture/igdb-integration.md): the enrichment data this leans on
- [Badge System](../../architecture/badge-system.md): stages, tiers, maintenance
