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

### Is the base *yours* or *the Pursuer's*?
Not semantics. If it is a place your character operates from, it can carry fiction, geography,
and inhabitants. If it is a visualization of your own trophy history, it has to stay honest to
your data and can invent very little. Leaning toward the former, but undecided.

### Names
All working titles:

| Working name | Notes |
|--------------|-------|
| **The Backlog** | For the Frontier. Strongest candidate. Every hunter has one and making it a place is exactly the right charm |
| **Village** | Placeholder. "Village" implies civilians and settlement, which is slightly off for a solitary hunter identity. Alternatives: hall, outpost, archive, reach, estate |
| **Frontier** | Placeholder for the whole game layer, currently doubling as the branch name |
| **Coins** | Placeholder for the soft currency |
| The five resources | Currently just the discipline names. May want their own material names |

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

- **Is a build reversible?** Permanent build orders create real stakes and real regret, which
  is great for a game and rough on someone who specialized before understanding the system.
  Respec costs are the usual answer and also the usual place a product starts feeling grabby.
- **Does the Village have its own level, separate from Pursuer level?**
- **What happens to a village when a user stops hunting entirely?** Current thinking is it
  keeps generating at the frozen rate, which makes it a soft on-ramp back. Unverified.
- **Do PSN trophies exist for the game layer itself?** (Meta, probably a no, but it has come
  up in the abstract as a "what would reward feel like" question.)
- **Mobile.** The site is mobile-first. A stateful game surface at 375px needs its own pass.

---

## Related Docs

- [decisions.md](decisions.md): what *is* settled, and what was rejected
- [vision.md](vision.md), [systems.md](systems.md), [content-model.md](content-model.md)
