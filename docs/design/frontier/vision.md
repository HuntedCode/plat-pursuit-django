# The Frontier: Vision

> Status: BRAINSTORM CAPTURE (2026-09-12). Not committed. See [README](README.md).

---

## The problem this exists to solve

PlatPursuit's engine is built on users going elsewhere, playing games, and coming back once
finished. Every reward beat in the current system (badge tier, Contract accept, job XP,
Pursuer level) fires on **completion**, which means:

1. **The cadence is PSN's, not ours.** Roughly one platinum every few weeks at best.
2. **The user already knows.** They were there when the platinum popped. By the time they
   visit, the site is confirming news they already have.
3. **Nothing is unpredictable.** If a user knows their PSN activity for the coming month, they
   can predict everything PlatPursuit will say to them.

The result is a luxury product people check occasionally rather than something they are
excited to check in on.

### The test that follows

> **If I know my PSN activity for the next month, can I predict what happens on PlatPursuit?**
> If yes, it is derivative and it will not excite anyone. If no, we have made something of
> our own.

Badges, for all their quality, largely fail this test. A badge is a curated re-partition of
completions the user already has. That is a beautiful thing and it is still a restatement of
the input.

---

## The reframe

> **Trophies are the resource, not the reward. Playing games earns you agency. The agency is
> spent here, and what happens when you spend it is not knowable from PSN.**

Trophies stay the thing you actually work for. They stop being the punchline and become the
fuel. The dice are the player's. The board is ours.

### Why a builder, specifically

Every other genre punishes the player for leaving. **Builders assume you left.** The loop is:
resources accumulate while you are away, you return, you spend them on a decision, you set
something in motion, you leave. A two-minute visit is a complete turn.

That is not a taste preference. It is the only major genre whose core assumption matches
PlatPursuit's actual constraint, which is that the user is elsewhere by design and cannot be
moved.

### What the current system is missing, mechanically

The shipped gamification has an **earn** side and no **spend** side. XP goes in, a level goes
up, a title unlocks automatically. The user never decides anything, spends anything, or
forgoes anything.

Games are exciting because of choices with consequences. Scoreboards are not. Three
ingredients the current system has none of:

| Missing | What it means |
|---------|---------------|
| **Scarcity** | Everything is available to everyone eventually. Nothing costs anything. Nothing is foregone |
| **Stakes** | Nothing can be lost, missed, or failed |
| **Uncertainty** | Nothing is hidden, nothing is revealed, nothing surprises |

A builder is the deep version of the spend side. A cosmetic store (the Phase 3 sketch) is the
shallow version.

---

## The premise

**A celebration of PlayStation history, explored and collected.**

Your Pursuer travels the Backlog: a vast branching map of PlayStation's past, organized by era
and genre, where the deeper strata hold the legendary and the hard-to-find. You bring things
home (discs, cards, artifacts, materials) and build a Village that houses your collection and
outfits your next expedition.

### Tone: celebration, not decay

An earlier version of this premise framed the descent as **decay**: dying games, closing
servers, lost media, and the player as a preservationist fighting rot. It was rejected as too
depressing to build a whole game on.

> **Depth is not decay. Depth is legend.**

Same games, same underlying data, opposite feeling. A 0.3% platinum is not rotting, it is a
white whale. The deep strata are not a graveyard, they are where the treasures are. The
reference point is Astro Bot: it features many games that have seen their last light of day
and it **celebrates** every one of them.

**Where decay still earns a place** (as an event type, never the ambient mood):

- **Timed events** when a game genuinely is about to become unobtainable. Real urgency, rare,
  high stakes.
- **One small solemn corner.** A memorial for the truly lost. A quiet room in a joyful
  building, not the building.
- **Restoration activities** aimed at those specific things.

---

## The driving force

Every successful builder's driving force is other people. Tribal Wars: take from them. Clash
of Clans: beat them. Farmville: help them. Solo incrementals ("see the next layer") burn out
in weeks; they are snacks, not homes.

Conquest and combat are false notes for this hobby. The actual culture of trophy hunting is
**display and help**: nobody platinums a game in secret, people post their cases, compare, and
write guides for each other.

> **Build a place worth visiting, and go visit other hunters' places.**

Your Village is a destination assembled from things only you could have earned. It reads as a
portrait of your hunting. Other hunters come through, see what you built and what is on your
shelf, and can do something useful while they are there.

**Racing** (first-to-earn, seasonal ladders) is the second driving force, added once there is
a crowd. It plugs directly into a future seasons concept.

---

## The theming approach

The feeling to chase is the Astro Bot nostalgia hit: visiting distinctly PlayStation places
and recognizing things. The hard constraint is that Sony gets that feeling by putting their
characters on screen and we cannot.

### In a menu game, you hand them the era, you do not show it

> **You do not show someone a PS1 world. You hand them a PS1 interface.**

The nostalgia lands harder because they are touching it rather than looking at it, and nobody
else in the space does this. Each era is authored once as:

| Channel | What varies per era |
|---------|---------------------|
| **Type** | Chunky and condensed for the disc era, sleek and futuristic later, glassy and thin after that |
| **Palette** | Muddy and dithered with heavy blue-black, versus deep saturated, versus gloss and chrome, versus soft minimal |
| **Rendering artifacts** | Dither patterns, scanlines, a faint vertex-style jitter in the oldest strata. All CSS |
| **Chrome language** | Chunky bevels versus hairline versus glass. The shape of a panel says which era you are in before you read a word |
| **Copy voice** | The oldest zones can carry a little of that era's translation-jank charm in their labels |
| **Sound** (later) | A disc spin-up costs nothing and does more than any graphic |

This reuses the architecture the site already has: an era skin is a token override.

**Guard: the layout structure stays identical across every era.** Only the surface treatment
changes. Otherwise players relearn the screen every time they travel, and charm becomes
friction.

### What makes it PlayStation without infringing

Legitimate material:

- **Eras and generations.** Factual periods, not IP.
- **The trophy system itself.** Bronze, Silver, Gold, Platinum. The shared culture of the hobby.
- **The rarity ladder.** Ultra Rare through Common. Real and meaningful to hunters.
- **The hobby's own vocabulary.** Platinum, plat, backlog, missable, stack, autopop, grind,
  100%, roadmap. Use this before inventing any new words.
- **Hardware archetypes.** The idea of a disc, a memory card, a save file. Not a specific
  controller silhouette.
- **IGDB data.** Genres, themes, franchises, companies, release dates, covers.

### Anti-references

- Generic fantasy RPG furniture: orcs, mana, swords-and-sorcery nouns. If a name could appear
  in any other browser RPG, it is the wrong name.
- Cartoon isometric tiles (the Farmville *look*, as distinct from its mechanics).
- Anything that reads as a mobile free-to-play skin.

---

## The tests

Applied to any future proposal, in this order:

1. **The prediction test.** Can a user predict the outcome from their PSN activity alone? If
   yes, it is derivative.
2. **The two-identical-players test.** Could two players with identical job levels end up with
   different results? If no, it is a chart, not a game.
3. **The village test.** Does this need the Village to exist? If it would work just as well
   without it, it is a separate minigame wearing a costume.
4. **The respect test.** Does real trophy work remain the only source of the ceiling, and is
   everything a hunter earned permanently safe?
5. **The menu test.** Can this be built as an interface, without 3D or commissioned art per
   entity?

---

## Related Docs

- [systems.md](systems.md), [content-model.md](content-model.md), [decisions.md](decisions.md)
- [Product Identity](../product-identity.md)
- [Visual Identity](../visual-identity.md)
