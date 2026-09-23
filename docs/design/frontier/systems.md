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
   │   THE PURSUER      │              raised by trophies ONLY
   │  Level, 24 Jobs,   │              buys WIDTH, nothing else
   │   5 Disciplines    │
   └─────────┬──────────┘
             |
      themed regions            (width: franchise, genre, studio, platform)
             |
             v
      ┌────────────┐   ┌────────────┐
      │    BASE    │──>│  FRONTIER  │   resources, gear, companions
      │ (economy + │<──│  (power)   │   loot, materials, cards, salvage
      │   DEPTH)   │   └────────────┘
      └────────────┘         |
             |               |
             └───────┬───────┘
                     v
            SOCIAL (cooperative)
     visiting, trading, community goals
```

> **Trophies buy WIDTH. The base buys DEPTH. Gear buys VIABILITY.**

Three inputs, three distinct roles, no overlap, and **none of them can block a new player.**
A Pursuer-0 account grinds the main road to endgame on base progress alone; trophy hunting buys
*different* content rather than *more* of it. Full reasoning in
[content-model.md](content-model.md#the-main-path-rule-revised-2026-09-22).

The Base and Frontier feed each other. Everything above the Pursuer is one-way: the game can
never reach up and change your trophy record.

**Nothing else may become a power axis.** If a change lets Pursuer level or the base confer
power directly, the separation the whole design rests on collapses. See
[gear.md](gear.md).

---

## Resources

**Five discipline resources**, matching the existing taxonomy: Combat, Exploration, Mind,
Heart, Finesse.

| Property | Behavior |
|----------|----------|
| **Generation** | Idle accrual over wall-clock time. **The village is the generator**, with some gathering from the Frontier |
| **Rate** | Set by village buildings and their levels. **Pursuer level does not affect it** |
| **Storage cap** | Stores fill and stop accruing. This is the honest "come back" pressure |

There is also a soft currency (working name: Coins) for cosmetics and non-discipline costs.

### Pursuer level buys width, and nothing else  *(REVISED 2026-09-22)*

> **Trophies buy WIDTH. The base buys DEPTH. Gear buys VIABILITY.**

Two drafts preceded this. The first had Pursuer level setting the **resource rate**, which gave
veterans a permanent multiplier. The second had it gating **depth**, which meant a newcomer
could not reach endgame without hunting. Both are gone.

**A ten-year veteran and a brand-new player build their base at the same speed and can both
reach endgame.** What the veteran's history buys is *different* content: themed regions built
around the franchises, genres, studios and platforms they actually play.

That reframes what hunting is for. It stops being **permission** and becomes **identity**, which
is the through-line of this product going back to Badges.

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

## The Base

The village, its buildings, the **Shift** system, companions, and the shared worker pool now
live in their own doc: **[base-and-shifts.md](base-and-shifts.md)**. It grew past the point where
it belonged inside this file.

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

**Gear is the only power axis in the whole design**, which makes it load-bearing. It gets a
genuine ascending power line; the anti-stomp protection comes from gear being earned in-game,
not from gear being weak. All permanence lives in the **set layer** rather than on items.

Full system, including the set layer, signature bonuses, acquisition pacing, and theming:
**[gear.md](gear.md)**.

### Loot

RNG loot tables are appropriate **here** and nowhere else. The rule:

> **Real work pays deterministically. Chance only ever sits on top.**

A platinum always pays its guaranteed XP and resources. Packs, drops, and rare finds come from
the game layer as a bonus. **Earned only, never purchasable with money**, or it becomes a
gacha and the tone is gone.

---

## Collection

This is a game built primarily for collectors, so there are several tracks. **Multiple tracks
only work if each one is a different pleasure.** If they all reduce to "find thing, tick list,
complete set," they blur into one long checklist and none of them feels special. Protect the
differences deliberately:

| Track | The question it answers | Rhythm |
|-------|-------------------------|--------|
| **Cards** | "How much of gaming have I seen?" | Frequent, high volume, packs, duplicates, trading. The social one |
| **Discs** | "What have I actually *found*?" | Rare, deliberate, zone-specific. The white-whale hunt |
| **Items / decor** | "Who am I?" | Broad, mixed sources, expressive. Changes your base, visible to visitors |
| **Gear** | "What can I take on?" | Functional. A real power ladder, with all permanence in the set layer. See [gear.md](gear.md) |
| **Companions** | "Who is with me?" | Slow, each one a character with their own progression |
| **Badges** | "What have I really done?" | Slow, real, PSN-only. The moat, untouched by the game |

### The Compendium

The master list of everything collectible, with where and how each is found. It is both the
motivational surface and, incidentally, the content roadmap: every entry added is visible,
countable content, and players notice the number move.

**Tier the reveal.** Showing four hundred entries on day one is overwhelming and kills
discovery:

| Tier | Shown |
|------|-------|
| **Known** | Found. Full entry, full description, provenance |
| **Rumored** | Silhouette, a name or partial name, and a hint about where it turns up. **This tier does the real work** |
| **Hidden** | Not shown at all. Preserves genuine surprise |

**How specific to be.** Location general, gates explicit, odds never. The reasoning is specific
to this game and is *not* hand-holding:

> **In a normal game the player's wasted time is in-game. In ours it is real evenings on a
> PS5.**

Someone who grinds the wrong zone for two weeks because an entry was coy has not discovered
anything, they have lost part of their life. So: point them at the right country, not the right
rock ("turns up in the deeper disc-era vaults"), state gate requirements plainly ("requires Mind
90", which turns confusion into a goal), and never publish drop rates. Monster Hunter runs
exactly this policy.

### The two game-derived tracks

Both built from real games:

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

## Companions and expeditions

Moved to **[base-and-shifts.md](base-and-shifts.md)**, since companions are now the shared
worker pool for both village shifts and Frontier expeditions.

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

- **Guard the three-way separation.** Trophies buy WIDTH, the base buys DEPTH, gear buys
  VIABILITY. Any change that lets level or the base confer power collapses it, and any change
  that lets a trophy condition gate the main path re-breaks the casual case. (Two earlier drafts
  are stale: level setting the resource rate, reversed 2026-09-14; level gating depth, reversed
  2026-09-22. Treat doc text implying either as out of date.)
- **Width must stay desirable.** If themed regions hold only nice-to-haves, trophy hunting
  becomes optional and the thesis collapses quietly. Apply the disappointment test.
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
