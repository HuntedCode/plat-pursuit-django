# The Frontier: Game Layer Design

> **Status: BRAINSTORM CAPTURE (2026-09-12).** Nothing here is built. Nothing here is
> committed. This folder is the written record of an extended design conversation so the
> reasoning survives, including the ideas we rejected and why.
>
> **Branch discipline:** all work on this feature lives on `feature/frontier`, branched
> directly off `main`. The website's regular update cadence continues in parallel on its own
> branches. Nothing here merges to `main` until the feature is real.

---

## What this is

PlatPursuit's gamification spine (Badges, Contracts, Jobs, the Pursuer) shipped and works.
What it lacks is a reason to visit on a Tuesday. The engine only fires when a user finishes a
game somewhere else, which means the site's reward cadence is set by PSN, is roughly monthly,
and delivers news the user already had.

The Frontier is the proposed answer: a browser-based menu/idle/builder game layered on top of
the existing character system, where **trophy hunting is the engine and the game is what the
engine powers.**

---

## The four docs

| Doc | What it holds |
|-----|---------------|
| [vision.md](vision.md) | The premise, the tone, the driving force, the theming approach, and the tests every future decision has to pass |
| [systems.md](systems.md) | Mechanics: the three layers, the loop, resources, gating, the risk boundary, the social layer |
| [content-model.md](content-model.md) | The map: eras, depth, branch types, gate types, the authoring model, and the pre-2008 trophy gap |
| [gear.md](gear.md) | The only power axis: the gear treadmill, the set layer (passives, signatures, milestones), the 100-signatures problem, and theming |
| [decisions.md](decisions.md) | The decision log, **including rejected alternatives and why they were rejected** |
| [open-questions.md](open-questions.md) | What is genuinely undecided |

---

## The shortest possible summary

You play games on PlayStation. That builds your Pursuer, exactly as it does today, and your
Pursuer's level decides **how much of the map is open to you**. You build a **base** that
generates resources and outfits you, then run expeditions into **the Backlog**, a branching
universe of gaming history, where you find gear, discs, cards, and artifacts to bring home.

Three inputs, three distinct roles:

> **Trophies buy access. The base runs the economy. Gear provides power.**

---

## Gotchas and Pitfalls

- **This is a menu game.** Not 3D, not isometric tiles, not a canvas world, and not a place
  with depicted rooms or physical space. This is the constraint that drifts most often (it was
  re-stated three times in the source conversation), so check every proposal against it before
  costing it. **Atmosphere lives in copy and state, not pixels:** what the page says, what
  changed since last time, and what the character is currently doing.
- **Nothing in the past is broken.** No repair, restoration, rescue, or damaged-goods mechanic.
  Two separate proposals died on this (decay as premise, restoration as crafting). Nostalgia
  requires the past to be good; the only thing missing is that the player doesn't have it yet.
- **Trophy hunting only exists from late PS3 onward.** Roughly 2008. Any mechanic keyed to
  platinum rarity, badge progress, or Contract completion silently does not work for PS1, PS2,
  or early PS3 content. See [content-model.md](content-model.md#the-pre-2008-gap).
- **Authoring is the differentiator, not a cost to minimize.** Generation is for coverage.
  A person writes everything a player reads. This was corrected twice in the source
  conversation; do not let it drift back.
- **The IP line is real.** Naming a game is factual and fine. Building content derived from a
  specific franchise's characters, creatures, or settings is not. See
  [content-model.md](content-model.md#ip-boundaries).
- **Nothing earned can ever be taken.** Trophies, badges, Pursuer level, buildings, and stored
  resources are permanently safe. This constraint is load-bearing and shaped several designs.
- **Only the player first-clears.** Companions farm consumables and collectibles; the player
  finds capacity and access (companions, slots, zones, key unlocks). If anything that expands
  farming capacity can itself be farmed, the system bootstraps and the player becomes optional.
- **Gear is the only power axis, and nothing else may become one.** Level buys access; the base
  runs the economy. A change that lets level or the base confer power collapses the separation
  the whole design rests on.
- **Casual means slow-income hunter, not non-hunter.** The accessibility answer is depth *under*
  the level ceiling, never a separate casual mode. Level is a ceiling, not a pace.

---

## Related Docs

- [Product Identity](../product-identity.md): the strategic frame. If this feature cannot be
  explained as serving the pitch, it is the wrong feature.
- [Gamification Plan](../gamification-plan.md): the phased rollout this would sit beyond.
  The Frontier is arguably a correction to what Phases 2 and 3 should have been.
- [Gamification Architecture](../../architecture/gamification.md): what actually shipped.
- [Visual Identity](../visual-identity.md): the six adjectives. The game surface gets more
  latitude than the site (see vision.md), but it still has to feel like ours.
