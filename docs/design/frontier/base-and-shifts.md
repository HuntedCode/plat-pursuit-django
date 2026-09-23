# The Frontier: The Base and Shifts

> Status: BRAINSTORM CAPTURE (2026-09-23). Not committed. See [README](README.md).

The village (working name pending), its buildings, the **Shift** system, the companion roster,
and the shared worker pool that both village shifts and Frontier expeditions draw from.

Split out of [systems.md](systems.md) on 2026-09-23 once it outgrew a section. **The base is the
sole depth gate for the whole game**, which makes this the most consequential doc in the folder
for pacing.

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

### The hook principle: row count is the growth signal

In CoC the early hook is watching your base visibly grow. In a menu game, numbers going up is
weak; **the list getting longer is strong.**

> **Row count is the growth signal.**

Which argues for launching with **more, simpler buildings** rather than fewer, deeper ones. Lots
of cheap early upgrades, lots of new rows appearing in the first week.

### The launch building set

| Building | Job | Why at launch |
|----------|-----|---------------|
| **Hall** | Caps everything | The spine |
| **5 producers**, one per discipline | Generate | Five cheap buildings makes the five-resource system tangible and supplies a pile of fast early wins |
| **Depot** | Storage cap | Half of the first real decision |
| **Staging** | Expedition slots | The loop needs it |
| **Workshop** | Forging and upgrades | The loop needs it |

Nine rows on day one, most of them simple. **Lodge, Signal Tower and Stacks** unlock across the
first few weeks, each arriving as a new row so the list keeps visibly growing after the initial
rush. **Exchange** (needs population) and **Catalogue Office** (needs content to guide toward)
are deferred.

### The first real decision: producers versus Depot

More production is worthless if you cannot store it between sessions. That tension appears in
session two or three, it is immediately legible, and it teaches the whole game's logic in one
choice: **your cadence determines what is worth building.**

A player who logs in twice a day wants producers. A weekend player needs the Depot. Same game,
genuinely different correct answers, discovered rather than explained.

### The interlock rule

```
Producers ──> resources ──> everything
Depot ─────> stops waste ──> makes producers worth having
Staging ───> slots ──> materials ──> Workshop
Workshop ──> gear ──> deeper zones ──> better materials ──> Workshop
Hall ──────> caps all of the above
```

> **Every building must make at least one other building better.** A building that only makes
> itself better is a checkbox, and nine checkboxes is not a base.

### Two horizons, and Hall tiers do double duty

**Two or three Hall tiers per road stop**, not one. That way there is always a near goal (next
tier, days away) and a far goal (next stop, weeks away). One-to-one leaves long stretches with
nothing closer than the next big beat, which is where idle games lose people.

And Hall tiers serve two purposes so the base never hard-stops when content runs out:

- **Early tiers gate the road.** Tier N opens stop M.
- **Later tiers improve the economy.** More slots, more storage, throughput, companion capacity.

A player who has cleared everything shipped still has a base to grow, and what they are growing
makes them better at farming what exists. Graduating, not stalling.

### Launch depth is a function of cadence

> **Launch content = content cadence x a safety margin.**

Ship a stop every two months and you need roughly two and a half months of launch content, not
twelve. The margin exists because the top 5% outrun the median no matter what; the target is
that the **median** player does not catch up before the second drop. Chasing the top 5% with
volume is how content budgets explode.

Rough shape to react to (a proposal, not a recommendation):

| When | Where the player should be |
|------|----------------------------|
| **Session 1** | Several upgrades land. Multiple visible wins in ten minutes |
| **Week 1** | Hall 3, expeditions unlocked, first companion |
| **Month 1** | Stop 2. The game visibly opened up |
| **Month 3** | Build identity formed, working on sets, first narrow branch cleared |
| **Month 6** | Caught up with launch content, farming width and rarity |

That implies roughly **8 or 9 Hall tiers and 3 stops at launch**.

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

---

## Shifts

The mechanic that gives a dedicated player something to interact with more often, in the way CoC
has raiding, Tribal Wars has farming, and Farmville has crop timers. What those three share:

> **Attention is a resource you can spend for output, and the conversion is bounded.**

Bounded matters. CoC runs out of targets, Farmville runs out of land. Without a bound the correct
play is never stopping, which punishes everyone with a life.

### The reframe: buildings are capacity, shifts are the gameplay

An earlier draft had buildings as the content, which made the base a static checklist we had
never actually validated. Inverted:

> **Buildings are capacity. Shifts are the gameplay.**

The base is a **workspace**: a set of slots where you assign work. Buildings expand how many slots
you have and what kinds of work are possible. The shift board is what you interact with.

### Naming: NOT "jobs"

They are **Shifts**. "Job", "Job Board", "Contract" and "Project" are all load-bearing vocabulary
in the existing Pursuer career system, and reusing any of them here would be actively confusing.

**This system has nothing to do with the Pursuer's Job Board.** Tying base work to Contracts would
make trophy hunting drive base throughput, which breaks
[D48](decisions.md#d48-trophies-gate-width-the-base-gates-depth-revised-2026-09-22) and re-breaks
the casual case. Shifts are entirely inside the game layer.

### One system, two states

Not "shifts replace production" (the absent player breaks: set a 12h shift, vanish for six days,
produce twelve hours of output in six days) and not "shifts beside passive production" (two
economies to balance, and the bonus is either pointless or was the real system all along).

> **A building always produces. A shift makes it produce better.**

| State | Rate | Who it suits |
|-------|------|--------------|
| **No worker** | Passive floor. Never stops | Anyone who is away |
| **Long shift** (12 to 24h) | Better | The casual player |
| **Short shift** (30m to 2h) | Best | The dedicated player |

When a shift ends with nobody there, the building **degrades to passive** rather than stopping.
Absence costs efficiency, never progress.

### The single most consequential constant

> **The passive-to-short-shift ratio IS "how much does attention matter."**

Too close and shifts are pointless. Too far and absence is punishing. First thing to put in the
balance model.

**Cap the spread at roughly 1.5 to 2x**, dedicated versus casual. Past that, attention becomes the
real progression and the base curve stops being the pacing mechanism, which quietly makes trophy
hunting decorative.

### Which buildings take shifts

> **Converters take shifts. Capacity buildings do not.**

| Building | Shift? | Why |
|----------|--------|-----|
| Producers | Yes | Nothing to resource |
| Workshop | Yes | Materials to gear |
| Signal Tower | Yes | Time to map reveal |
| Lodge | Yes | Faster companion recovery |
| Depot | No | It is a cap, not a conversion |
| Staging | No | It is slot count, not work |

Forcing shifts onto capacity buildings creates filler assignments, which is exactly the chore
feeling this system exists to avoid.

### Keeping it from being a chore

> **A chore is a task with no decision.** Variance and stakes separate assigning from clicking.

Six things, and they stack:

1. **Make each assignment a matching problem.** Not "start the shift" but **who works what**.
   Companions have affinities; buildings produce different things. The answer changes on its own
   as the roster grows, so it stays interesting without new content.
2. **Give workers memory.** **Affinity grows with use.** Today's choice has a tail, the workforce
   specializes according to how the player actually played, and assigning someone off their
   speciality has a real cost. This is the biggest single difference between a chore and a
   strategy.
3. **Variance on top, never underneath.** Base yield stays deterministic (RNG on what you earned
   feels bad), but a shift can occasionally turn something up. Every collection gets a small
   "what did I get" beat without the reward ever feeling unreliable.
4. **Give the session a target.** An **order** (the Lodge wants 40 Scrap by tonight) turns a
   check-in into a small goal with a completion moment. Generated from current state, so templated
   rather than authored.
5. **Repeat last assignment must be ONE click.** A dedicated player with eight slots checking in
   five times a day is forty assignments. At three clicks each the game is unplayable by exactly
   the people who love it most. The routine must be free; the decision layer is opt-in, and opting
   in is when it is interesting.
6. **Flavor text per completed shift.** Cheap to author, infinitely variable, and it turns
   collection from a number incrementing into a small pleasure. **Batch the collection moment** so
   three finished shifts read as an event rather than as admin.

**The honest limit:** it will still be a chore for some people. Farmville was never for everyone.
The goal is that for the people it *is* for, it is a pleasant one with a decision in it.

---

## The shared worker pool

**Shifts and expeditions draw from ONE roster.** This is not a balance choice, it is the entire
reason companions carry two modifiers: if the pools were separate you would assign everyone to
their better role once and never think about it again.

### What keeps the allocation choice live

Structural, and it falls out of the forge decision:

> **Affinity gear needs expedition materials AND village resources.**

Starving either side blocks the thing you want. You cannot sit in the village forever (no
materials) or live in the Frontier forever (no resources). Alternation is forced without any rule
saying so.

### The asymmetry

| | Duration | After |
|---|---|---|
| **Shift** | You choose, 30m to 24h | Available immediately |
| **Expedition** | Fixed, hours | **Cooldown.** Out of action for a while |

Expeditions are **demanding**; shifts are **sustainable**. You rotate people through the Frontier
while others hold the village, and cooldowns get a real job in the allocation puzzle instead of
being an arbitrary wait.

### Cooldowns create the scarcity, not roster size

Count the positions: roughly eight village slots plus three or four expedition slots is eleven,
against a twenty-companion roster. **Nothing is scarce and every position is filled**, which kills
the decision entirely.

> **Cooldown length is what makes the roster scarce.**

If a four-hour expedition costs eight to twelve hours of rest, a real chunk of the roster is always
unavailable, and twenty companions might mean twelve usable against eleven positions. It stays
tight because pushing the Frontier harder puts more people out of action.

**Cooldown length is therefore one of the two most consequential constants in the game**, alongside
the passive-to-shift ratio.

This also gives the **Lodge shift** a strategic job: **spend a slot to get slots back.** A
companion working the Lodge shortens everyone else's recovery, which is a real opportunity cost
rather than a passive perk.

### The Pursuer's version is sharper

The same tension applies to the player, and it bites harder, because **only the player's own runs
first-clear**. Going into the Frontier yourself means the forge loses its best worker for the
duration.

> **Progress costs you economy, and you choose the rate.**

---

## Roster numbers

**Four to eight concurrent shifts** is the sane band for a menu game. Twenty concurrent shifts is
twenty pieces of state to review and twenty flavor lines per collection, which is noise. The genre
sits low: Genshin expeditions run five slots, and Fallout Shelter's dozens-of-dwellers late game is
widely considered a mess.

**Twelve to twenty companions at launch**, growing with content. Enough for real combinatorics, few
enough that they do not blur.

### Two constraints that bind at different times

| Stage | What is scarce | The decision |
|-------|----------------|--------------|
| **Early** | **Workers.** Two companions, five buildings | "I wish I had more people" |
| **Later** | **Slots.** Twenty companions, eight slots | "Who do I field today?" |

The *nature* of the decision evolves, not just the numbers. Early you collect bodies; later you
pick a lineup, which is the more interesting problem.

### A bench, not a lineup

Slot count comes from converter buildings: five producers plus Workshop, Signal Tower and Lodge is
eight at full build, against a roster of twenty. **A maxed player fields 8 of 20.**

- Collecting stays mechanically valuable, because more options means better matches
- Every session involves a real choice, because not everyone can work
- Cooldowns matter, because a resting companion frees a slot for someone benched
- Two players with identical roster size still field different eights

Since companions are **player-found only** and gated across width, nobody has all twenty at launch
pace. Realistically eight to twelve, with the rest behind regions the player personally chose to
chase, so the roster is another portrait of how they played.

---

## Companion design

### Dual modifiers

Every companion carries **a shift modifier and a Frontier modifier**. That makes the scarce thing a
**unit, not a resource**: if a companion is both your best forge worker and your best rare-finder,
you cannot have both, and that is a recurring decision every session.

**Tuning:** most companions **lopsided**, a few generalists, a few hyper-specialists. If everyone is
good at both there is no juggling; if everyone is good at exactly one you have two separate rosters
and no tension. The interesting middle is where the same person is the obvious pick for two
incompatible jobs.

### Archetype, never character

Companions are homages to gaming's recurring character types. The rule is the same one the zones
follow:

> **Evoke the archetype, not the character.**

"The grizzled escort protagonist" lands half a dozen characters at once. "The one who will not stop
explaining the tutorial." "The merchant who is inexplicably always here." "The rival who shows up
at every gym."

This is not a compromise made for legal reasons. A reference to one character is an in-joke; an
archetype that captures a **pattern across the medium** is observation, which is what affectionate
comedy about a medium actually looks like. It also ages better and cannot run out.

Designing a companion to be "obviously who they are meant to be" is the risk zone, in a product
with paid memberships. See [content-model.md](content-model.md#ip-boundaries).

### Personality is not the numbers

Two companions with different modifiers and identical voice are the same companion. Personality
lives in the **shift flavor text**, which costs one line per job and pays off every session. An
archetype plus a voice plus a place they hang around in the base is a character; two numbers is a
stat block.

---

## Gotchas and Pitfalls

- **Never call Shifts "jobs."** The Pursuer career system already owns Job, Job Board, Contract and
  Project. Reusing any of them will confuse two systems we deliberately separated.
- **Shifts must never draw on Contract or trophy state.** That would make hunting drive base
  throughput and re-break the casual case.
- **The passive floor and the shift rate are ONE curve with three points.** Tuning them separately
  is how the ratio silently drifts.
- **"Repeat last" ships with the first version of Shifts**, not as a later QoL pass. Without it the
  system is unusable at the cadence it is designed for.
- **Do not force shifts onto capacity buildings.** Filler assignments are the chore.
- **Watch position count against usable roster.** If positions exceed usable companions, the
  allocation decision silently disappears. Cooldown length is the lever.

---

## Related Docs

- [systems.md](systems.md): the three layers, resources, risk boundary, social
- [gear.md](gear.md): the forge, discipline affinity, the set layer
- [content-model.md](content-model.md): the road, stops, gating, the authoring model
- [decisions.md](decisions.md): the decision log
