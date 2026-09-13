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

**In fiction:** a settlement you founded at a stable point in the Backlog. The universe of
gaming history is vast and shifting; your base is the one place in it that holds still, because
you made it hold still. You go out, you come back, it has grown.

Registers do not mix. There is **one fictional layer**, and the real world never pokes through
it. A framing where the base was the player's actual game room was rejected for exactly this
reason ([R17](decisions.md#r17)).

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

### The Hall gates the tiers

Borrowed from CoC and Tribal Wars. A main building (working name **Town Hall**) sets the cap
for everything else. You start with the Hall at level one and nothing else. You can build a few
resource buildings and depots and raise them to the tier cap, and then further progress needs
the Hall upgraded, which lifts the cap again.

This reconciles cleanly with sequence-scarcity: **the Hall tier creates waves, and inside each
wave you still choose build order.** Bounded, recurring, no permanent lockout.

**On maxing out.** A dedicated player will eventually cap everything and wait for an expansion.
CoC has this and its players treat it as an achievement rather than a failure. Our position is
better than theirs for one reason: **the village is not the endgame, the Frontier is.** A maxed
village in CoC means nothing left to do; a maxed village here means a fully tuned engine for
the actual game. That is graduating, not stalling. Mitigate with long timers and large
prerequisites at the top tiers, and see the soft-gating rule in
[content-model.md](content-model.md#soft-gating-gate-on-a-thing-not-a-number).

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

### Customization is a separate layer from buildings

> **Buildings are function. Cosmetics are identity. They never touch.**

Buildings stay **generic and functional** (thematic, but not era-specific), so the base screen
is always legible and a new building is cheap to add. Everything expressive lives in its own
layer with its own economy.

An earlier draft skinned each building by the era its materials came from. It was rejected as a
guaranteed UI mash-up: five visual languages on one screen reads as broken rather than as
characterful. See [R18](decisions.md#r18).

#### The rule that keeps a large cosmetic library coherent

> **Items are *what* you own. The theme is *how* everything renders.**

A decor item is not "a brass lamp," it is "a lamp" that draws in whatever theme is currently
active. Consequences:

- Hundreds of items can ship with zero risk of the screen fighting itself
- Swapping your base theme re-renders your whole collection coherently, which is a satisfying
  moment in itself
- **Two independent authoring pipelines that multiply.** A new item is a name, a description,
  an icon, and a slot. A new theme is a token set. Ten themes and a hundred items is a thousand
  looks, and neither pipeline blocks the other

#### The surfaces

Constraint: in a menu builder, customization has to survive being a row, and it has to be
**visible to a visitor** or it is a setting rather than customization.

| Surface | Notes |
|---------|-------|
| **Base theme** | The era skins, as pure cosmetics. **One active at a time**, governing everything else. Unlocked by exploring that era, then chosen by the player, so it is a goal driven by play rather than an automatic side effect |
| **Name, motto, building names** | Free, infinite, the most personal results. First thing a visitor reads |
| **Crest / sigil** | Shape + field color + symbol from SVG parts. A few dozen parts yield thousands of crests. Best value on the list |
| **Decor** | The flavor rows: a name, a description, no stats. Where found objects live |
| **Display arrangement** | Which finds are featured, how exhibits are grouped |
| **Inhabitants** | Who lives here and what they are called |
| **Arrival flavor** | Variants on the return screen and the character's status line |
| **Dedications** | Pin a platinumed game's cover to a building. Uses art already displayed site-wide |
| **Existing character surfaces** | Frames, titles, marks, backgrounds, nameplate styling. All already modeled |

**Acquisition:** found in the Frontier, bought with Coins, earned from completed exhibits,
awarded by community goals, and time-limited from events.

**Build in a provenance bias.** Found items should carry where they came from in their own
description ("recovered from the deep disc-era vaults"). It is worth more than any stat, and it
is why a found lamp beats a bought lamp emotionally. Bought items are the reliable path; found
items are the ones people talk about.

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

**Gear is a wardrobe, not a ladder.** It is the only track with a *functional* payoff, which
risks making every other track feel like fluff. The guard is that pieces suit particular eras,
themes, or zone types rather than forming a single ascending line of "better." Then a player
wants *many* pieces rather than only the best piece, and gear stays a collection instead of a
stat check. Two sources: crafted at the Workshop, found in the Frontier.

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
| **Gear** | "What can I take on?" | Functional, situational, a wardrobe rather than a ladder |
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

Inhabitants who move into the base as it grows **are** the expeditionary force. That closes a
loop that was otherwise loose:

> Base grows → more inhabitants → more parallel expeditions → more finds → base grows

It also gives the Rig/Staging building an obvious job (housing and dispatch capacity), and it
means village growth directly increases Frontier throughput.

### The hard line: only the player first-clears

> **The player *explores*. Companions *run*.**
>
> Exploration is first-clear, progression, gated by level. Expeditions are farming and economy,
> and only reach places you have already been.

This does a great deal of work:

- The player is always the protagonist. Companions never steal the moment
- **Progression is personal; expeditions are only economy.** Same shape as the level/gear split
- You cannot idle your way into new content
- Exploration and farming stop competing, because they are different activities

### The generalization

> **Companions farm consumables and collectibles. The player finds capacity and access.**

Anything that expands your ability to farm has to come from you: companions, expedition slots,
new zones, key unlocks. Otherwise the system bootstraps itself, compounds without you, and the
player becomes optional. The deepest prestige collectibles belong on that list too, so "I found
this" keeps its weight.

### Slots, cooldowns, and rotation

**Fewer expedition slots than companions.** This is the scarcity model that works, and it is
everything finite plots was not: the decision **recurs every session** rather than locking you
out once, nothing is permanently lost, and collecting more companions always helps without ever
trivializing the cap.

Two cooldowns, which are not redundant because they are **two different growth levers**:

| Cooldown | Purpose | Improved by |
|----------|---------|-------------|
| **Slot re-supply** | Paces total throughput | Building upgrades |
| **Companion rest** | Forces rotation through your bench | Collecting more companions |

### Companions are a choice, not a stat

Each brings unique benefits and drawbacks: one finds rares more often, one clears faster, one
returns extra materials. Combined with Compendium hints this makes farming **directed**: "this
drops in the deep disc-era vaults, and she finds more rares, so that is the run."

**Drawbacks must be tradeoffs, not punishments.** "Finds 20% more rare gear, moves 30% slower"
is a choice. "Sometimes loses part of your haul" violates the nothing-earned-is-lost rule and
will feel bad no matter how small the chance.

**Companions are worse than the player at finding rares, and slower.** That is deliberate: it
creates the central allocation tension, because there is only one of you and your own runs have
two competing uses.

> **Push forward into new areas, or go get that specific thing myself.**

### Progression, gear, failure

- **Companions have their own progression.** Levels or affinity that grow with use.
- **Companion gear: one slot each**, Diablo-follower style. Contained (one item type, one slot,
  not a second inventory) and it gives found gear a second home when a piece is not right for
  you. **Sequence it as v1.x**, not first slice: the loop proves out without it and it doubles
  the gear authoring surface on day one.
- **Expeditions can fail.** Specifics deferred to implementation. The boundary now: a failure
  costs **the run** (time, supplies, opportunity), never the haul already banked and never the
  companion. A failed run can still grant the companion some experience.

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
