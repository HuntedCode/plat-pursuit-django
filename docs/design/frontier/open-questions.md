# The Frontier: Open Questions

> Status: BRAINSTORM CAPTURE (2026-09-12). Not committed. See [README](README.md).

Genuinely undecided. Grouped by what they block.

---

## Blocks planning

### Scope: what is the first shippable slice?
Three systems were designed (Village, Frontier, Collection) plus a social layer. Shipping all
of it at once is how nothing ships. Candidate slices:

- **Village only.** Resources accrue, buildings get built, nothing to fight. Proves the idle
  loop and the return cadence with the least content authoring.
- **Village plus a single era of the Frontier.** Proves the whole circuit but needs enemies,
  loot tables, and zone content on day one.
- **Collection first.** Cards and discs from existing data, shelved somewhere. Cheapest, but
  it is the least game-like piece and might read as another display surface.

### How does this relate to the existing Gamification Plan phases?
The Frontier is arguably a correction to what Phases 2 and 3 should have been rather than a
new phase beside them. If it proceeds, [gamification-plan.md](../gamification-plan.md) needs
reconciling: currency, store, quests, and streaks are all sketched there and several would be
absorbed or replaced.

### Tech approach for the game surface
Django templates plus vanilla JS like the rest of the site? HTMX? A small self-contained JS
app on one page? The site has no precedent for a stateful client that polls and updates
continuously. This decision shapes everything downstream.

---

## Blocks content design

### Rarity source for pre-2008 collectibles
Platinum rarity does not exist before roughly 2008, so cards and discs from PS1/PS2/early PS3
need a different scarcity source. Candidates: authored rarity tiers, real-world scarcity of
the physical release, IGDB rating or significance, or curated "landmark" status.

### How many eras at launch, and how deep?
Related to scope. A launch with one era is testable; a launch with five is a world.

### What are the enemies, concretely?
Decided: original, not franchise-derived, not generic fantasy, drawing on era atmosphere and
genre archetype. Not decided: what that actually looks like on a menu screen, or how many are
needed per zone to avoid repetition.

### Is contested ground in or out?
The shape is designed ([systems.md](systems.md#contested-ground-deferred-but-the-shape-is-decided))
but it is the only competitive mechanic and it needs population. Probably a later addition,
but if it is coming, buildings need contest roles designed in from the start rather than
retrofitted.

---

## Blocks fiction and naming

### Names
The hardest open question so far. All working titles:

| Working name | Notes |
|--------------|-------|
| **The Backlog** | For the Frontier. Strongest candidate. Every hunter has one and making it a place is exactly the right charm. Note the meaning is a *universe of gaming history*, not the player's literal unplayed pile |
| **Village** | Placeholder, and it does not survive the theming. Implies civilians and settlement. Candidates raised: **the Reach** (warm, suggests the edge of somewhere vast), **Basecamp** (plain, expedition-native), **Checkpoint** or **Save State** (gaming-native, means both "preserved" and "the place you return to"), **the Anchor** (a fixed point in a shifting universe, which is literally the fiction) |
| **Town Hall** | Placeholder for the tier-gating main building |
| **Compendium** | For the master collectible list. Alternatives: Catalog (warmer, but Sony uses "Game Catalog" for a PS Plus tier), Index, Registry, Almanac |
| **Frontier** | Placeholder for the whole game layer, currently doubling as the branch name |
| **Coins** | Placeholder for the soft currency |
| The five resources | Currently just the discipline names. May want their own material names |

### Does the base have its own level, separate from Pursuer level?
Probably yes, via the Hall tier, but whether that is surfaced as a number is undecided.

### What is the Pursuer's verb?
The character now *does* something, which was the original gap. But the fiction of what they
are (explorer, collector, archivist, curator) is not settled, and it determines a lot of copy.

---

## Blocks business decisions

### How does this interact with membership?
The established position is that membership is a dial, not a door: nothing is gated, members
get more. A game layer with currency, cosmetics, and packs is the most natural monetization
surface the product has ever had, and also the easiest place to violate that principle.
Decided already: **packs are never purchasable with money.** Everything else is open.

### Does the game layer produce anything visible on the main site?
Profile integration, showcases, the Career page. A village nobody outside the game can see is
a weaker "place worth visiting" than one linked from a profile.

---

## Smaller, but worth recording

- **Do companion affinities map to the five disciplines?** Decided NOT to map them to jobs or
  disciplines, because it would constrain where each companion can go and that is less fun.
  What their affinities *are* instead is still open.
- **What exactly does expedition failure look like?** The boundary is set (costs the run, never
  the haul, never the companion) but the mechanics are deferred to implementation.
- **How many expedition slots at each Hall tier, and what are the cooldown lengths?** Pure
  tuning, but it sets the daily rhythm and should be prototyped early.
- ~~**Is a build reversible?**~~ RESOLVED: nothing is ever demolished and every building is
  eventually available, so there is nothing to respec.
- **What happens to a village when a user stops hunting entirely?** Current thinking is it
  keeps generating at the frozen rate, which makes it a soft on-ramp back. Unverified.
- **Do PSN trophies exist for the game layer itself?** (Meta, probably a no, but it has come
  up in the abstract as a "what would reward feel like" question.)
- **Mobile.** The site is mobile-first. A stateful game surface at 375px needs its own pass.

---

## Related Docs

- [decisions.md](decisions.md): what *is* settled, and what was rejected
- [vision.md](vision.md), [systems.md](systems.md), [content-model.md](content-model.md)
