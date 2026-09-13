# The Frontier: Systems

> Status: BRAINSTORM CAPTURE (2026-09-12). Not committed. See [README](README.md).

---

## The three layers

```
        OFF SITE                       (not ours, cannot be moved)
   Playing games on PSN
            |
            v
      Contracts / Badges               (existing, shipped)
            |
            v
   ┌────────────────────┐
   │   THE PURSUER      │              the engine
   │  Level, 24 Jobs,   │              raised by trophies ONLY
   │   5 Disciplines    │
   └─────────┬──────────┘
             |
    resource rate     zone access
             |               |
             v               v
      ┌────────────┐   ┌────────────┐
      │  VILLAGE   │──>│  FRONTIER  │   gear, consumables
      │            │<──│            │   loot, materials, cards
      └────────────┘   └────────────┘
             |               |
             └───────┬───────┘
                     v
            SOCIAL (cooperative)
     visiting, trading, community goals
```

The Pursuer powers both systems. The Village and Frontier feed each other. Everything above
the Pursuer is one-way: the game can never reach up and change your trophy record.

### Why the Pursuer is a rate and not a balance

This was the resolution to the veteran-onboarding problem (see
[decisions.md](decisions.md#d3)). A user arriving with ten years of PSN history gets a
**bigger engine**, not a full warehouse. They are meaningfully more powerful from their first
minute, which honors the existing "same work, same reward, regardless of when" principle, and
they still have to run the engine like everyone else. There is nothing to dump.

---

## Resources

**Five discipline resources**, matching the existing taxonomy: Combat, Exploration, Mind,
Heart, Finesse.

| Property | Behavior |
|----------|----------|
| **Generation** | Idle accrual over wall-clock time. Rate is set by Pursuer level |
| **Which resources** | Determined by the shape of your character. A Combat hunter generates Combat fastest |
| **Storage cap** | Stores fill and stop accruing. This is the honest "come back" pressure |
| **Village effect on rate** | **None.** Buildings expand capacity, variety, and what you can build, never the base rate |

That last row is load-bearing. If village upgrades boosted generation, the village would
compound on itself and eventually dwarf the character multiplier, at which point trophy
hunting stops mattering to the player's own game.

There is also a soft currency (working name: Coins) for cosmetics and non-discipline costs.

### On time

> **PlatPursuit never charges the user time.**

No build timers, no energy, no pay-to-skip. A hunter who just spent 60 hours on a platinum
should not then be told to wait four hours for a building. The wait already happened on their
console.

**Timers may generate, never gate.** An expedition that runs for three hours and produces
something is a gift for coming back. A progress bar standing between the player and something
they already paid for is a tax, and the only reason that mechanic exists in this genre is to
sell the skip.

Pacing comes from progression gates, escalating costs, and authored chapters, never from a
stopwatch.

---

## The Village

A menu builder. Buildings are rows and cards with levels and upgrade buttons, not tiles on a
map. Tribal Wars is the proof this works: its village screen is a list, and almost none of its
grip came from art.

### Scarcity is the whole game

**Finite plots.** More buildings available than plots to put them on. Every screen exists to
make the player choose. This was the single thing missing from the first rejected mockup.

Sources of decision pressure:

- Limited plots, so composition is a real choice
- Costs in five separate resources, so a Heart-poor Combat hunter genuinely cannot build
  everything
- Buildings gated behind other buildings
- Upgrade paths that exclude each other

### What buildings do

Categories, not a final list:

| Kind | Example role |
|------|--------------|
| **Crafting** | Forge produces gear components for expeditions |
| **Capacity** | Archive raises storage caps across all five stores |
| **Conversion** | Workshop turns surplus from a full store into Coins |
| **Display** | Visitors' Hall shows featured badges and the disc shelf to anyone who visits |
| **Social** | Almshouse doubles contribution to community goals |
| **Direction** | Cartographer's Office suggests a Contract a week from a district you have neglected |

That last category matters more than it looks: **buildings that point back at hunting**. The
Village should keep sending the player to their console.

### Customization surfaces

The constraint: in a menu builder, customization has to survive being a row. A name, a color,
an icon, a small treatment, an image. And it has to be **visible to a visitor**, or it is a
setting rather than customization.

| Surface | Notes |
|---------|-------|
| **Village name and motto** | Free to build, infinite expression, first thing a visitor reads |
| **Crest / sigil** | Shape + field color + symbol, composed from SVG parts. A few dozen parts yield thousands of crests. Best value on the list |
| **Building names** | User-written. Costs nothing, produces the most personal results |
| **Theme treatments** | The authored Horror/Fantasy/Noir styling applied per building. The scaling surface |
| **Dedications** | Pin a platinumed game's cover to a building. Uses art already displayed site-wide, costs zero new assets |
| **Display slots** | A fixed number of featured positions. Same pattern as existing profile showcases |
| **Village-wide ambience** | A season or time-of-day treatment that recolors everything |
| **Existing character surfaces** | Frames, titles, marks, backgrounds, nameplate styling. All already modeled |

**"Plot layout" is not a customization surface.** There is no map, so arrangement has nowhere
to live. What that phrase was reaching for is *which buildings you have at all*, which is the
core loop.

**First two to build:** crest and naming. Nearly free, immediately visible to visitors, and
they make a village feel owned before a single building is upgraded.

---

## The Frontier

Idle PvE. The Pursuer runs expeditions into zones; encounters resolve over time; loot drops.

Working name for the place: **the Backlog**. Every hunter has one, it is a running joke and a
source of guilt, and making it a literal place you go is exactly the charm the project's
design standard calls for.

### Enemies

Original creations, not derived from any franchise, and not generic fantasy. Encounter design
should draw on era atmosphere and the archetypes of a zone's genre rather than on orcs.

### Gear and the two-master rule

The character has two inputs, and they must not substitute for each other:

> **Trophies set the character's level. The Village sets the character's gear.**
> **Level gates what you can enter. Gear decides how well you clear it.**

A heavy village grinder with excellent gear still cannot enter a zone their level does not
allow, and only hunting raises level. Hunting stays the hard ceiling; the Village gets real
teeth; neither replaces the other.

Gear slots (illustrative): weapon, armour, trinket, charm, banner, supply.

### Loot

RNG loot tables are appropriate **here** and nowhere else. The rule:

> **Real work pays deterministically. Chance only ever sits on top.**

A platinum always pays its guaranteed XP and resources. Packs, drops, and rare finds come from
the game layer as a bonus. **Earned only, never purchasable with money**, or it becomes a
gacha and the tone is gone.

---

## Collection

Two collectible tracks, both built from real games:

**Cards.** One per game, drawn from IGDB, so the set is effectively infinite at zero authoring
cost for the base data. Rarity keys off **actual platinum rarity**, so scarcity has an honest
source. Duplicates trade. Set bonuses at thresholds.

**Discs.** Rare physical-media finds shelved in the Village, with sets completed by real
franchise (named factually, never depicted). This is the retro-collector fantasy and it is the
purest expression of the celebration premise: the whole feeling is "oh, I remember that one."

The disc shelf is also what gives the Village a payload for the "place worth visiting" driving
force. People come to see your shelf.

> Note: card rarity cannot key to platinum rarity for pre-2008 games. See
> [content-model.md](content-model.md#the-pre-2008-gap).

---

## The risk boundary

| Category | Contents |
|----------|----------|
| **Never at risk** | Trophies, badges, Pursuer level, buildings, stored resources, collection |
| **Can be spent** | Resources, consumables, Coins, expedition supplies |
| **Can be lost** | An expedition run, a contested site's income, standing/rank |

Anything paid for in real hours is permanently safe. Positions can be lost; accomplishments
cannot.

---

## The social layer

**Cooperative only. No PvP.** Monopoly GO-style raiding works because the money you lose came
from a free dice roll. Here it came from someone's 60 hours, which is a categorically
different feeling.

| Mechanic | Notes |
|----------|-------|
| **Visiting** | The reason to go is that other hunters' villages are interesting. Someone's village is built from games they actually played, so walking through it is browsing a person's taste |
| **Discovery** | You see a wing built with a game you have never touched, and you can go look it up. This closes the circuit: hunting builds your village, visitors find games, they go hunt |
| **Trading** | Duplicate cards and discs |
| **Supporting** | Contribute to another player's build |
| **Community goals** | A shared target with a deadline. The only social mechanic that works at low population: a leaderboard with 50 people is depressing, a shared goal with 50 people is a team. Also the one number on the page nobody controls, which makes it the thing people check without being asked |

Precedent for community goals: the existing community-funded badge artwork loop already runs
this exact emotional beat with real money, and it works.

### Contested ground (deferred, but the shape is decided)

If competition is added, the rule is **compete FOR something, not OVER something**. Neutral
sites nobody owns, contested by committing part of your village. Holding one pays income.
Losing a contest costs the income stream, never your stores. Village never damaged, only
out-competed.

This is also where the build tension of **economy versus contest** would come from, and where
a village's dedications could carry competitive weight (a Horror-themed site favoring a
village with horror dedications).

---

## Return cadence

The point of the whole exercise:

| Clock | Frequency | Source |
|-------|-----------|--------|
| Collect | Daily | Idle accrual, storage filling |
| Expedition ends | Hours | Frontier run completing |
| Community goal | Weekly | Moves without you |
| Platinum | Monthly | Raises the engine |

Today there is one clock. This adds three faster ones without replacing it.

---

## Gotchas and Pitfalls

- **Village upgrades must never raise the base generation rate.** Compounding growth there
  eventually makes trophy hunting irrelevant to the player's own game.
- **Cap the daily take if any active conversion is added.** If more clicking always means more
  progress, the cheap loop out-competes the expensive one and someone who visits obsessively
  beats someone who actually hunts. Idle-only accrual sidesteps this entirely, which is one
  reason it is the current direction.
- **Do not let gear substitute for level.** The moment gear can open a zone, the Village
  becomes the real progression and hunting becomes optional.
- **RNG belongs on loot, never on the reward for a platinum.** A hunter's 100 hours must never
  pay out a random trinket.
- **A building that changes nothing is a screensaver.** Every building needs a mechanical role.

---

## Related Docs

- [vision.md](vision.md), [content-model.md](content-model.md), [decisions.md](decisions.md)
- [Gamification Architecture](../../architecture/gamification.md): the shipped XP economies
- [Badge System](../../architecture/badge-system.md)
