# The Frontier: Decision Log

> Status: BRAINSTORM CAPTURE (2026-09-12). Not committed. See [README](README.md).

Decisions reached in the source conversation, with reasoning. **The rejected section is the
more valuable half**: several of these were arrived at only after a wrong turn, and the
reasons are what stop the wrong turn being taken again.

---

## Settled

### D1. The problem is that we do not own a clock
Every reward beat fires on completion, which is PSN's schedule, roughly monthly, and delivers
news the user already had. Fixing this by decorating the completion event does not work; it
needs a second system with its own cadence.

### D2. Build a second layer, do not re-visualize the first
A new way of displaying job levels is a chart. The second layer has to contain decisions,
scarcity, and uncertainty that the character sheet does not.

### D3. The character is a rate, not a bank
Trophy history sets your **generation rate**, not a stock of resources. This is the single
most load-bearing decision in the document. It simultaneously solves:

- **The veteran dump.** A user with ten years of history cannot consume the whole game in one
  sitting, because there is nothing to consume. They get a better engine.
- **The fairness question.** They *are* genuinely more powerful from minute one, which honors
  the existing "same work, same reward, regardless of when the work was done" principle in
  [gamification-plan.md](../gamification-plan.md).
- **The empty-day problem.** Generation does not depend on having platinumed something.

### D4. Idle accrual, with storage caps
Resources accrue on wall-clock time and stop when stores fill. No active grinding is required,
which also means no one can out-click a hunter.

### D5. Village upgrades never raise the base generation rate
They expand capacity, variety, and what can be built. If they raised the rate, the village
would compound on itself and eventually make trophy hunting irrelevant to the player's own
game.

### D6. Timers may generate, never gate
An expedition that produces something while you are away is a gift. A progress bar between you
and something you already paid for is a tax, and in this genre that mechanic exists to sell
the skip. Pacing comes from progression gates, escalating costs, and authored chapters.

### D7. Menu and text based, not 3D
See [R5](#r5) and [R6](#r6). The genre's best examples (Tribal Wars, Kittens Game,
A Dark Room, Universal Paperclips, Melvor Idle) are menu games, and PlatPursuit already looks
better as a premium web app than a first year of self-taught 3D would.

### D8. The art budget is icons, CSS, and assets we already own
[game-icons.net](https://game-icons.net) (4,000+ CC-BY monochrome SVGs, tintable with the
discipline colors), CSS treatments, existing cover art, and existing badge artwork.

### D9. Theme treatments are authored against IGDB themes, never per game
One Horror treatment serves every horror game IGDB will ever add. Per-game authoring does not
scale; per-theme authoring covers the library forever on a finite budget.

### D10. PvE, and the social layer is cooperative
No raiding, no theft, no whale-stomping. See [R8](#r8).

### D11. If competition is added: compete FOR, not OVER
Neutral contested ground nobody owns. Losing costs an income stream, never your stores.

### D12. Two masters, cleanly split
> Trophies set level. The Village sets gear. Level gates access, gear gates efficiency.

Hunting stays the hard ceiling. The village gets real teeth. Neither substitutes.

### D13. Depth is legend, not decay
Same games, same data, opposite emotional frame. See [R11](#r11).

### D14. Eras are lateral and free; depth is vertical and level-gated
All eras open from the hub in any order at no penalty. Depth gates are absolute numbers that
apply identically on every route, which makes side-branch tuning solvable.

### D15. Franchise gates the door, archetype furnishes the room
Badge progress in a series opens an archetypal world that several series share. Emotional
payoff without derivative content per franchise.

### D16. Franchise worlds are side content, and access is permanent
Never on the main path, because a player can be permanently unable to satisfy that gate. And a
badge lapsing into maintenance must not close a world.

### D17. Authoring is the differentiator
Generation is for coverage; a person writes everything a player reads. The authoring tools
ship alongside the first zone, not after ten.

### D18. Era atmosphere is delivered through the interface
You hand the player a PS1 interface rather than showing them a PS1 world. Type, palette,
chrome, artifacts, copy voice, and eventually sound. Layout structure never changes.

### D19. Collectibles are real games
Cards are games (rarity from actual platinum rarity, where trophy data exists). Discs are a
physical-media collection track with sets by real franchise.

### D20. Nothing earned can ever be lost
Trophies, badges, Pursuer level, buildings, stored resources, collection. Positions and
income can be lost; accomplishments cannot.

### D21. RNG sits on loot only
Real work pays deterministically. Packs and drops are earned, never purchasable with money.

### D22. Parallel development on `feature/frontier`
Branched directly off `main`. Regular site updates continue on their own branches.

### D23. The village is a hub town and home base, not the main progression driver
The Frontier carries progression. The village is where you collect, outfit, decide, dispatch,
display, and return to. It also has to carry the **pride of a home you built**, not the
efficiency of a facility you operate.

### D24. Scarcity of sequence, not scarcity of selection
Every building is eventually available. The decision is **build order**, which compounds.
Replaces the earlier finite-plots model. See [R14](#r14).

### D25. Nothing is ever demolished
The base is purely additive and is a record of everything the player has done.

### D26. Nothing in the past is broken
Finds go straight to the shelf. No repair, no restoration bench, no damaged-goods state. The
resource sink is **display** (cases, frames, plaques, exhibit space), and **materials** are a
separate mundane loot category that feeds gear crafting. See [R15](#r15).

### D27. Atmosphere lives in copy and state, not pixels
The three tools available to a menu game are: what the page says, what changed since last time,
and what the character is currently doing. Home-feeling is built from a growing list, flavor
rows with no stats, character status text, and the offline-summary return screen. A Dark Room
is entirely text and is genuinely atmospheric.

### D28. Parallel timers of different lengths
Accrual, expedition, production, community goal, and a daily rotating element, so something is
always ripe when the page opens. **Re-dispatch is the strongest single daily hook.**

### D29. One fictional layer, no reality poking through
The base is an in-fiction settlement founded at a stable point in the Backlog. The Backlog is a
universe of gaming history, not the player's literal pile of unplayed games. See [R17](#r17).

### D30. Buildings are generic; cosmetics are a separate layer
Buildings are functional and uniform. Everything expressive lives in its own layer with its own
economy. **Items are what you own; the theme is how everything renders**, which lets a large
cosmetic library ship without the screen fighting itself. See [R18](#r18).

### D31. The Hall gates the tiers
A main building sets the cap for everything else. Its tiers create **waves**, and inside each
wave the player still chooses build order.

### D32. Soft-gate on materials, not on Pursuer level
For village progression specifically. Gate on a thing found past a level-gated zone rather than
on the level itself. Same effect, completely different feel, and it feeds discovery. See
[R19](#r19).

### D33. The player explores; companions run
Only the player can first-clear. Expeditions only reach places already cleared. Generalized:
**companions farm consumables and collectibles, the player finds capacity and access.** Anything
that expands your ability to farm (companions, slots, zones, key unlocks, the deepest prestige
collectibles) must come from the player, or the system bootstraps itself and the player becomes
optional.

### D34. Fewer expedition slots than companions, with two cooldowns
The scarcity model that works, because the decision **recurs** rather than locking you out.
Slot re-supply cooldown paces throughput and is improved by **buildings**; companion rest
cooldown forces rotation and is improved by **collecting companions**. Two growth paths, two
levers.

### D35. Companions are choices with tradeoffs, and are worse than the player
Each has unique benefits and drawbacks, which makes farming directed rather than idle.
**Drawbacks are tradeoffs, never punishments** (slower or lower yield, never losing a haul).
Companions are deliberately slower and worse at finding rares, which creates the central
allocation tension: push forward into new areas, or go get that specific thing yourself.

### D36. Companions have their own progression; gear is one slot and is v1.x
Levels or affinity that grow with use. One gear slot each, Diablo-follower style, sequenced
after the first slice.

### D37. Expeditions can fail, but failure costs only the run
Time, supplies, and opportunity. Never the haul already banked, never the companion. Specifics
deferred to implementation.

### D38. Compendium reveal is tiered; location general, gates explicit, odds never
Known / Rumored / Hidden. The rumored tier does the motivational work. Being specific about
place and gate is **not** hand-holding here, because in this game the player's wasted time is
real evenings on a console rather than in-game time.

### D39. Collection tracks must each be a different pleasure
Cards (volume, social), discs (rare, deliberate), items (expressive), gear (functional
wardrobe), companions (characters), badges (real work). If they all reduce to the same verb they
blur into one checklist.

### D40. Casual means slow-income hunter, and the answer is depth under the ceiling
Level is a ceiling, not a pace. Keep collection tracks level-agnostic, make zones wide at every
band, and never build a separate casual mode. Full reasoning in
[vision.md](vision.md#who-this-has-to-work-for).

---

## Rejected

### R1. Enhancing the existing PSN clock
Five proposals were made and all five rejected: instrumenting the in-progress hunt,
personalized news (delistings, rarity drift, new list drops), a "what to play next" planner,
social clocks, and a daily puzzle.

**Why:** all were functions of PSN data, so all failed the prediction test. Two additional
reasons: the news angle means competing with established names on their own turf before we
have the market position to do so, and the planner is something Badges and Contracts already
do to a good degree. The daily puzzle survives as a separate, unrelated idea.

### R2. Starting with seasons
**Why:** the character and the world have to exist and be fully playable before a seasonal
version of them means anything. Diablo 2 ladder analogy. Seasons remain the obvious later
layer, and the prestige/expansion reset is the bridge to them.

### R3. The architectural cutaway (first mockup)
A cross-section building where five floors were the disciplines and rooms were the 24 jobs.

**Why:** rooms were bound one-to-one to jobs, so the entire building was derivable from the
character sheet. It was a chart. **The test it failed:** could two players with identical job
levels end up with different results? No. It also had 25 fixed slots and therefore no
decisions at all, which is the real lesson: **finite space and choice are what make a builder
a game**, not the rendering.

### R4. Isometric or cartoon tile art (the Farmville look)
**Why:** breaks the visual identity, needs endless commissioned art, and is unusable at 375px
in a document layout. The *mechanics* of Farmville were kept; only the costume was thrown out.

Note: a later correction established that a **game surface does not have to match the site's
design system** the way a document does, and that badge artwork is a better connective strand
than UI tokens. The art-cost and learning-time objections are what actually settled it.

### R5. Unity or Godot web export
**Why:** integration friction. A builder constantly reads and writes server state, and with a
WASM runtime that means marshalling everything through a JS bridge and solving auth from
inside the runtime, forever. Godot's threaded web export additionally requires cross-origin
isolation headers (COOP/COEP) which can break third-party embeds site-wide; the
single-threaded export avoids it at a performance cost. three.js was the better fit given
PPDrive already exists.

### R6. Any 3D pipeline, including three.js plus Blender
**Why:** dedicating significant time to learning a brand-new discipline for a feature that is
not yet certain. Deferred rather than dismissed. The Blender learning path was captured in
case it is revisited: the Donut for interface literacy, then Imphenzia for low-poly technique
and the shared-palette-texture workflow, then Polygon Runway for stylized dioramas, with
GameDev.tv as the structured paid option.

### R7. Escalating cost for opening additional era branches
The idea: pick one era, and the others become more expensive.

**Why:** it punishes breadth, and breadth is the behavior we want. It also creates a feels-bad
optimization problem and makes side-branch gating unsolvable, since the player's route cannot
be predicted. Replaced by [D14](#d14-eras-are-lateral-and-free-depth-is-vertical-and-level-gated).

### R8. PvP raiding, Monopoly GO style
**Why:** Monopoly GO works because the money you lose came from a free dice roll. Here it came
from someone's 60 hours, which is a categorically different feeling. Plus whale asymmetry and
hostility in a hobby that has very little of it.

### R9. Duels played from your trophy library
A weekly theme, both players field a hand of games they have platinumed, outcome decided by
real accomplishment.

**Why:** it could exist with no village at all. It was a library minigame in a village costume,
which is the same failure as [R3](#r3) in a different coat. **The test it produced:** does this
need the Village to exist?

### R10. Discipline factions
Combat hunters versus Mind hunters contributing to competing goals.

**Why:** your discipline is derived from what you play, not chosen. Rooting for a faction you
did not pick does not work. Possible later side event, not core.

### R11. Decay as the premise
The original framing: games die, servers close, platinums become unobtainable, and the player
is a preservationist fighting rot.

**Why:** depressing rather than nostalgic. Astro Bot features many games that have seen their
last light of day and celebrates every one of them. The frame made the player a mortician.
Replaced by [D13](#d13-depth-is-legend-not-decay). Decay survives as an event type, a small
solemn corner, and restoration activities.

### R12. Chronological descent with the newest era as the starter zone
**Why:** if the newest era is the shallow starting area, new content lands *behind* veterans,
which is the opposite of what a live game needs. Replaced by the two-axis model where era is
lateral and a new generation arrives as a shallow region that deepens as its games become
classics.

### R13. Zones named for and populated by a specific franchise
Example that surfaced it: a zone called "The Silent Hill" with enemies from that series.

**Why:** naming a game is factual and fine (a badge for "every Resident Evil platinum" is
nominative use). Building creative content derived from a franchise's characters and settings
in a product with paid memberships is not. The risk is not a letter; it is building a content
pipeline on that pattern and having to rip it out once it is load-bearing. Replaced by
[D15](#d15-franchise-gates-the-door-archetype-furnishes-the-room).

### R14. Finite plots in the village
More buildings available than room to place them, forcing permanent exclusions.

**Why:** it was correct while the village *was* the game and had to carry every decision
itself. Once the Frontier became the progression driver, the village is support infrastructure,
and permanently locking a player out of a support building just feels bad. The genre agrees:
CoC and Tribal Wars both let a player build everything in their main base eventually. Replaced
by [D24](#d24-scarcity-of-sequence-not-scarcity-of-selection).

### R15. Restoration as a crafting mechanic
Finds come back damaged (scratched discs, corrupted saves, torn manuals) and are repaired at a
bench before they can be shelved.

**Why:** it is the decay framing in a different costume. Repair implies the past is degraded,
and nostalgia requires the past to be **good**. This was the second time the same mistake was
made after [R11](#r11), which is why the principle is now written as a rule:
*nothing in the past is broken, the only thing missing is that you don't have it yet.*

### R16. Framing the village purely as a museum
**Why:** a museum is a display case, and display cases are passive. The village has to be the
main daily draw, which means dispatch, production, decisions, and return moments. Display is
one wing of it, not the building.

### R17. The base as the player's real game room ("your setup")
A framing where the home base was a real-world gaming space (desk, shelves, CRT, couch) and the
Backlog was what you dove into through the screen.

**Why:** it does a lot of clever work (display and operations in one place, deeply nostalgic,
"come see my setup" is a real joy) but it **breaks the fiction**. The Backlog is meant to be a
universe of gaming history that the character explores, not the player's literal pile of
unplayed games. Injecting reality kills the vibe and pulls the player out of the world. One
fictional layer only.

### R18. Buildings skinned by the era their materials came from
Each building wearing the visual treatment of the era it was salvaged from, making the base a
visible patchwork of where you have been.

**Why:** five UI languages on one screen reads as broken, not as characterful. Also required
era-tagged salvage, which is a **second resource axis** on top of the five disciplines and a
real complexity cost for a support layer. Replaced by
[D30](#d30-buildings-are-generic-cosmetics-are-a-separate-layer): generic buildings plus one
active base theme governing all rendering.

### R19. Gating the top Hall tiers directly on Pursuer level
**Why:** it tells players who are new to trophy hunting, or who do not have much time to play,
that they are not wanted. Replaced by
[D32](#d32-soft-gate-on-materials-not-on-pursuer-level), which achieves a similar pacing effect
while reading as a reason to explore rather than a refusal.

---

## Flagged, not decided

### Login streaks as sketched in Phase 3
A streak that rewards *logging in*, on a product whose real activity is monthly, is a chore
bolted onto an earnest product. If a streak ships, it should measure hunting (a trophy earned
this week keeps the flame) or not ship. Raised as a concern against
[gamification-plan.md](../gamification-plan.md) Phase 3; not formally resolved.

---

## Related Docs

- [vision.md](vision.md), [systems.md](systems.md), [content-model.md](content-model.md)
- [open-questions.md](open-questions.md)
