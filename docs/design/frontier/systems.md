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

### It is a hub town, not a museum and not the main progression driver

The Frontier carries progression. The village is the **home base and support layer**: where you
collect, outfit, decide, dispatch, display, and come back to. Display is one wing of it, not
the point of it.

### Scarcity of sequence, not scarcity of selection

**You will eventually have every building.** Permanent exclusion on a support system feels bad,
and the genre agrees: CoC and Tribal Wars both let a player build everything in time.

An earlier draft used finite plots. That was correct when the village *was* the game and had to
carry every decision itself. It is wrong now. See [decisions.md](decisions.md#r14).

The decision that remains is **build order**, and it still matters because it compounds:

- Depot first means you stop wasting capped resources sooner
- Rig first means more expeditions running while everything else lags
- Workshop first means better gear earlier, so deeper runs sooner
- Costs in five competing resources mean your character's shape decides what is cheap for *you*
- Depth versus breadth: one building at Lv. 10, or five at Lv. 3

**Nothing is ever demolished.** The base is purely additive, and it is a record of everything
you have done.

### What buildings do

Categories, not a final list:

| Building | Job |
|----------|-----|
| **Depot** | Storage capacity across the five stores |
| **Workshop** | Gear and display cases, built from materials |
| **Rig / Staging** | How many expeditions run at once, and how far they reach |
| **Catalogue Office** | What is missing, where to go next, expedition planning |
| **Signal Tower** | Reveals map, surfaces community goals and events |
| **Exchange** | Trade duplicates with other hunters |
| **Lodge** | Visitors, guestbook, social |
| **Stacks** | The collection display |

The Catalogue Office matters more than it looks: it is a **building that points back at
hunting**. The village should keep sending the player to their console.

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

### Making it feel like home, in a menu

The base should carry the pride of a home you built, not the efficiency of a facility you
operate. **In a menu game that feeling lives in copy and state, not pixels.** A Dark Room is
entirely text and is genuinely atmospheric. Three tools: what the page says, what changed since
last time, and what your character is currently doing.

| Feeling | How it is built |
|---------|-----------------|
| **Permanence** | A building list that only ever grows, plus a visible history log of every addition |
| **Non-functional space** | **Flavor rows.** List entries with a name and a description and no stats, that exist purely to be yours. The menu equivalent of a decorative room, and the best Coins sink available |
| **Your Pursuer lives here** | A single line of **status text** that changes with state. "Rellik is at the bench." "Rellik is sorting the shelves." The difference between `Expedition: idle` and a character who is home |
| **The return moment** | A **screen state**. Opening the page tells you what happened while you were gone: what finished, what is waiting, who visited. The genre calls this the offline summary and it is one of the most satisfying things in it |

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

### Finds are never broken

> **Nothing in the past is broken. The only thing missing is that you don't have it yet.**

A find goes **straight to the shelf**. No repair bench, no restoration timer, no damaged-goods
state. Repair implies the past is degraded, which is the decay framing wearing a different
costume, and it undercuts the nostalgia the whole premise runs on. See
[decisions.md](decisions.md#r15).

The resource sink moves to **display, not repair**:

- **Cases, shelves, frames, plaques, exhibit space** are what you spend on. You are honoring
  the thing, not fixing it. "My shelf is full, I need a bigger case" is a good problem
- **Materials** are a separate and mundane loot category (scrap, components) that feeds the
  Workshop for gear. Treasures get displayed; materials get spent
- **Exhibits** are the curation layer. Group finds into themed displays: a full series run, a
  single year, one studio's output. Completing an exhibit pays, and exhibits are what visitors
  come to see

The verbs are **find, shelve, curate, show off**.

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

The point of the whole exercise is that **several things are always mid-flight, and one of them
just finished.** That is the Farmville harvest principle, and the way to guarantee it is
parallel timers of different lengths, so something is always ripe when the page opens.

| Clock | Frequency | What is waiting |
|-------|-----------|-----------------|
| Resource accrual | Continuous, caps out | Stores to collect |
| Expedition | Hours | A run came back and needs re-dispatching |
| Workshop production | Hours to a day | Gear or a case finished |
| Community goal | Weekly | The bar moved without you |
| Rotating offer / visitor | Daily | Something new appeared |
| Platinum | Monthly | The engine got faster |

Today there is one clock. This adds five faster ones without replacing it.

**The strongest single hook is re-dispatch.** An expedition that ended needs the player to send
the next one, and an idle slot is wasted time. That is a real, self-imposed reason to open the
site tomorrow, and it costs the player nothing they earned.

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
