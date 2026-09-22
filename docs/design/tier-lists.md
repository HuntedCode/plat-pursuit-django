# Tier Lists and Grids

> **Status:** design, nothing built. Decided 2026-09-13 that this is **its own system**, not a Game
> List type. This document records that decision, what follows from it, and the questions that have
> to be answered before it can be planned.
>
> Related: [game-list-types.md](game-list-types.md) (why it left), and
> [docs/features/game-lists.md](../features/game-lists.md) (the system it sits beside).

## The shape

A hunter authors a **template**: a set of games and a structure to place them in (S/A/B/C/D tiers, or
a grid of labelled slots). Other hunters open it and produce their own **response** — their placement
of those games. The response is the point: it is a statement of taste, it is shareable, and comparing
responses is the engagement.

The reference the owner named is [grids.fun](https://grids.fun).

## Why it is not a list type

A Game List has **one rendering**: one author arranges rows and every viewer sees identical bytes.
This has **one template and N responses**, and the responses are authored, not derived.

That is not a presentation difference, so `GameList.list_type` — which is a presentation field —
cannot carry it. Putting it there would mean every read path in Lists having to ask *"canonical, or
mine?"*, and the ones that forgot would silently show the wrong thing to the wrong person. The full
argument is in [game-list-types.md](game-list-types.md#the-test-a-type-has-to-pass).

## Its own bubble

Owner's call: this gets its own infrastructure rather than being threaded through the list surfaces.

| Surface | What it is |
|---|---|
| Template browse | published templates to respond to |
| Response browse | responses to one template, and a hunter's own responses |
| Author tools | building a template: the game set, the tiers or slots, the labels |
| The response editor | the thing most people will actually use |

**Lists are done and stay done.** The list browser, editor and viewer shipped in 2026-09 and are not
being reopened for this. What is worth *reusing* is infrastructure rather than surfaces — the
concept adder, `covers.cover_games_for`, `.pp-gcard`, the moderation and restriction plumbing, and
`HtmxListMixin` for the browse pages.

## Questions that have to be answered first

Nothing here should be guessed at during implementation.

**1. Does a template own its games, or point at a List?**
Pointing at a List means a hunter builds "The 50 Hardest Platinums" once and attaches a prompt to it,
and the whole list-building UI is reused for free. Owning them means the template is self-contained
and can carry per-item data a list has no business holding. The "own bubble" decision leans toward
owning, but the reuse on offer is large enough to be worth a deliberate answer.

**2. Where do likes live — the template, or the response?**
Both are real, and they are different acts. On a site like grids.fun the interesting thing to like is
somebody's *response*. Note that `GameList.like_count` is denormalized and **browse sorts on it**, so
whichever way this goes, the popular-sort has to rank the thing people are actually applauding.

**3. Who can delete a response?**
A response is my content on your template. The author of the template should almost certainly not be
able to delete mine — but they can currently delete their own list, and if a template can be deleted
or un-published after four hundred people have responded, those responses are orphaned. Needs a rule.

**4. What is it called?**
"Tier list" is the community's own word and should probably survive. "Grid" is a different shape with
the same machinery. Whether they are one feature with two layouts or two features is partly a naming
question and partly a scope one.

**5. Do responses need to be public to exist?**
A private response ("I just wanted to sort these for myself") is a legitimate and cheap use, and it
changes the visibility model: the template's `is_public` and the response's are independent.

## Release plan

**Ships alongside the public launch of Game Lists** (owner's call, 2026-09-13), not after it. Lists
come off `_DevelopmentGate` for everyone; this arrives at the same time behind the **member + staff
beta gate** for a week or two.

That pairing is deliberate: Lists is the finished, unremarkable half that everybody gets, and this is
the novel half that wants a smaller audience first. Shipping them together makes one release with
something to say, rather than two quiet ones.

`PremiumRequiredMixin` (`trophies/mixins.py`) already exists and is currently used by nothing — it
redirects to `/beta-access/`, and the cohort is `user_is_premium or is_mod_or_admin(user)`. Per the
pinned preview rule in CLAUDE.md, what a free hunter sees must be a **static** page: no provider, no
per-user query, nothing that runs this system's data path for somebody who cannot use it.
