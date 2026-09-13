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
