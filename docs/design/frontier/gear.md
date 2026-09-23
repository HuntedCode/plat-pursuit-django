# The Frontier: Gear and Sets

> Status: BRAINSTORM CAPTURE (2026-09-14). Not committed. See [README](README.md).

Gear is the **only** thing that differentiates how quickly and easily two players clear
content. Pursuer level buys access and nothing else; the village runs the economy. That makes
this system load-bearing in a way none of the collection tracks are.

---

## The conflict that shaped this design

The first proposal was that gear should be **horizontal**: a wardrobe of situational pieces
with no ascending power line, so that old pieces never became garbage in a game built for
collectors.

That was wrong, and the reason is worth keeping:

- **"Other collection tracks become decoration" is not a problem.** Cards, discs and items are
  *collection* tracks. Gear being the power track is a clean separation, not a failure.
- **The anti-stomp protection does not come from gear being weak.** It comes from gear being
  **earned in-game**. A veteran with a decade of PSN history arrives with unlocked zones and
  nothing else. They still have to build the village, run the loop, and farm every piece. So
  gear can be a genuine, satisfying power ladder without breaking anything.

The second proposal was **vertical within a piece**: upgrade the pieces you like, forever, so
nothing is ever obsolete. That was also wrong, and more obviously so: if any piece can be
upgraded to current power indefinitely, then **every drop after your first decent set is
noise**, and the loot loop dies entirely.

The resolution splits the two things that were fused:

> **Base power is the treadmill. Collection lives in the set layer.**

---

## Gear items: a clean treadmill

A gear item is essentially a **power number**. Higher is better. Finding a better base is always
an upgrade, and that is the Diablo loop working exactly as it should.

| Property | Behavior |
|----------|----------|
| **Base power** | The ascending line. Unambiguous, legible, answers "am I getting stronger" |
| **Upgrading** | Invest materials at the Workshop to raise a piece within its tier |
| **Upgrade ceiling** | Capped by the piece's tier or rarity. A common base caps low; a rare base caps high. **This is what stops one early set carrying a player forever** |
| **Sources** | Found in the Frontier (exciting), crafted at the Workshop (reliable) |
| **Slots** | Keep the count low, four to six. Loadout management in a menu game gets tedious fast |
| **Obsolescence** | Old pieces salvage into upgrade materials. Nothing is ever wasted |

### Nothing is lost, because the Compendium remembers

> **The Compendium records the find. The item is a resource.**

Finding a piece logs it permanently with its provenance, whether or not you keep it. The item
itself can then be salvaged. Collector satisfaction is decoupled from item utility, which is
what lets gear be a treadmill without betraying the collection premise.

---

## The set layer: where collecting pays

All the permanence lives here, not on the items.

### 1. Permanent passives, from every track

Completing a set grants a **small, permanent, always-on bonus** that stacks forever and is
never invalidated by better gear. This applies to **every collection track**, not just gear:

| Track | What its passives should feed |
|-------|-------------------------------|
| **Cards** | Find rates, map reveal |
| **Discs** | Storage, resource yield |
| **Items / decor** | Coins |
| **Gear** | Combat power |

Keeping each track's passive thematically its own is what stops them all being generic +1s, and
it resolves an earlier worry that gear being the only functional track would make cards and
discs feel like decoration.

**Keep them genuinely tiny.** They stack forever. If each one is meaningful, a five-year veteran
becomes untouchable. Tiny and numerous is the right shape: the aggregate feels great, no single
one distorts anything.

### 2. Signature bonuses, from gear sets only

Completing a **gear** set also unlocks its **signature**. One signature is active at a time,
independent of what you are currently wearing.

This is the build-identity layer and the real decision. It is also one row in a menu rather than
an inventory system.

**Signatures must be situational, not ranked.** If one is flatly strongest, everyone runs it and
the choice is fake. Tie them to intent instead: this one boosts rare finds, this one suits
horror zones, this one speeds clears, this one raises yield. Then the best signature depends on
what the run is *for*.

### 3. Milestones

Bonuses for completing N sets within an era or a family. Costs nothing to author and gives the
Compendium a second layer of goals.

---

## The three levers

The picture this produces, which deliberately mirrors the companion system:

> **Gear sets your power. Your signature sets your purpose. Your companion sets the trade-off.**

Three legible levers, no inventory management, and the right answer changes depending on what
the run is for.

---

## Solving the "100 distinct signatures" problem

If the game grows to a hundred gear sets, authoring a hundred genuinely distinct signature
bonuses without power creep sounds impossible. The framing is the problem, not the number.

> **You do not need 100 distinct effects. You need about 15 effects and a lot of conditions.**

Three levers make it tractable:

**Not every set needs a signature.** Roughly one in four. The rest give only the permanent
passive. That takes 100 sets down to 25 signatures immediately and makes signature sets feel
special.

**Vary the condition, not the effect.** "Rare find +X% in *Y*" is one archetype that generates
dozens of distinct-feeling signatures by varying Y. We have the richest possible source of
conditions already: eras, genres, themes, zone types, companion pairings. Twelve effects times
eight conditions is ninety-six combinations, and the conditions are where the interesting
decisions live anyway. *"Great in disc-era horror zones"* is a more interesting sentence than any
raw number.

**Group them into families.** Roughly six: find, speed, yield, depth, survivability, economy.
Within a family they vary by condition and magnitude. Players grasp families instantly and it
stops the design space feeling arbitrary.

### Affinity: why newly-unlocked width is not a victory lap

Once trophies gate **width** rather than depth
([content-model.md](content-model.md#the-main-path-rule-revised-2026-09-22)), a new problem
appears: a player who unlocks Horror Highway at month six arrives geared from the main path and
would faceroll it. The reward for hunting would be a region you *harvest* rather than *play*.

The fix is a mechanic already designed here, now given a real job:

> **Effective power = base power x affinity match.**

Main-path gear is **solid everywhere and dominant nowhere**. In a themed region it sits at
roughly 60 to 70%, and region-attuned gear closes the gap. So newly-unlocked width is something
to engage with rather than a lap of honour, and there is an immediate reason to chase gear that
belongs to the region you just opened.

Two guards:

- **Do not make it feel like a reset.** Arriving at zero effective power in a new region is
  punishing and will read as the game confiscating your progress. Strong-but-not-dominant is the
  target.
- **Do not make affinity so dominant that every region is a separate grind.** The base ladder
  must still carry most of the weight.

This quietly rehabilitates the horizontal instinct that was rejected as [R20](decisions.md#r20),
and the difference is worth naming: it is now **vertical within a region, horizontal across
regions**, which has a reason to exist rather than being variety for its own sake.

### The rule that keeps creep out

> **New sets add new conditions, not bigger numbers.**

The moment a new set's signature is flatly stronger than an old one, creep has started. If it is
instead *differently conditioned*, it is variety at zero power cost, and that can go on forever.

**Not every set has to be memorable.** In collection games most entries are unremarkable
individually and the collection is the point. Aim for twenty signatures people talk about and
eighty that are simply solid.

---

## Acquisition pacing

The worry: if a set is a nightmare to complete, the player outscales it before finishing and the
whole thing is moot.

The model mostly solves this already, because **a set's rewards are permanent.** The signature
and the passive do not care when you finished it or what you are wearing now. Finishing a set
late is never wasted.

Two levers on top:

- **Deterministic progress alongside RNG.** Every run gives progress toward the thing even when
  the drop does not land. Targeted currency, or straightforward bad-luck protection. Keeps a
  hunt from feeling like nothing happened.
- **Crafting as the floor.** You can always build the piece with enough materials. Finding it is
  the shortcut, not the only road.

---

## Theming

**This is the one system where standard RPG equipping is on-theme**, and the earlier
anti-fantasy rule does not apply the way it does elsewhere. The premise is a celebration of
video games, and equipping gear is one of the most universal video-game languages there is.

The rule was never "no equipment." It was "do not become a generic browser RPG," and what makes
a game generic is **naming and flavor, not mechanics.**

> **Lean into video-game equipment, not fantasy equipment.**

Swords and plate armour are medieval fantasy and are off-theme. Video games have a far richer
equipment vocabulary: power-ups, mods, gadgets, chips, upgrades, loadouts, perks, attachments.

**The opportunity:** each era and genre has its own equipment idiom. Gear from a racing-genre
zone should feel nothing like gear from a horror zone. Done well, the gear collection becomes a
tour of gaming's own equipment language, which is both deeply on-theme and effectively infinite.

---

## Tuning

- **Compress the power range.** A year-one player being 3 to 5 times a month-one player keeps
  content relevant and keeps newcomers from feeling hopeless. A 100x spread makes most of the
  map dead space.
- **Recommended power per zone, not hard power gates.** Players self-select into difficulty
  instead of hitting walls.
- **Gear must never become a gate in disguise.** The base gates depth; gear gates viability. If a
  zone is technically enterable but impossible without one specific item, that is a gate wearing
  a costume. Gear makes things faster and better, not possible.
- **Affinity is a discount, not a wall.** A player with no region-attuned gear must still be able
  to play a region they unlocked, just less efficiently.

---

## Gotchas and Pitfalls

- **Do not let a piece be upgradeable without a ceiling.** That single mistake kills every
  subsequent drop.
- **Do not let permanent passives be individually meaningful.** They stack forever and across
  every collection track, so "small" has to mean genuinely small.
- **Do not rank signatures.** The moment there is a best one, the choice layer is decorative.
- **Do not author new power to make a new set exciting.** Author a new condition.
- **Watch the fantasy drift in naming.** The mechanics are fine; the names are where this system
  slides into generic RPG fastest.

---

## Related Docs

- [systems.md](systems.md): the three layers, resources, companions, the risk boundary
- [content-model.md](content-model.md): zones, gating, the authoring model
- [decisions.md](decisions.md): the decision log, including what was rejected here
