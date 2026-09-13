# Game List Types

> **Status:** Collection and Ranked are **built** (2026-09) — see
> [docs/features/game-lists.md](../features/game-lists.md#list-types). Top-N and Sectioned are
> candidates. **Progress, Backlog tracker and Tier have been removed from this document's scope**
> (2026-09-13); see [What left, and where it went](#what-left-and-where-it-went).
>
> See also: [product-identity.md](product-identity.md), and the rebuild's own
> [playbook](rebuild/rebuild-playbook.md).

## Why types at all

A list of games is one data shape with several jobs. A backlog and a top-ten are the same rows
arranged differently, and building them as one model with several presentations is what makes the
ambition affordable. Building them as separate features is what would make it a swamp.

**The whole type system is one field on the list and one on the item.** `GameList.list_type` picks
the presentation (it exists, holding `collection` and `ranked`); `GameListItem.group` would hold a
section key (not built). `position` already exists, is dense, and `Meta.ordering = ['position']`, so
ranked ordering was free at the model level — Ranked cost one CharField and no item migration, which
is the evidence that this framing was right for the types it still covers.

## The test a type has to pass

Added 2026-09-13, and it is the reason three planned types left.

> **A list type has ONE rendering. Every viewer sees identical bytes.**

Collection, Ranked, Top-N and Sectioned all pass: one author arranges rows, and what you see does not
depend on who you are. `list_type` is a *presentation* field, and a presentation is exactly what they
differ by.

Progress, Backlog and Tier all fail, in two different ways:

| | What varies per viewer | Storage it needs |
|---|---|---|
| **Progress / Backlog** | the value shown against each row, **derived** from the viewer's trophy data | none — computed at render |
| **Tier / Grid** | the arrangement itself, **authored** by the viewer | a stored response per viewer, which is its own social object |

Both break the one-rendering rule, so neither belongs in `list_type`: every read path would have to
start asking *"am I looking at the canonical version or somebody's version?"*, and the paths that
forgot would silently show the wrong one.

## What left, and where it went

**Progress and the Backlog tracker: cut** (owner's call, 2026-09-13). Two reasons, and the second is
the stronger one:

1. **The whale rule points straight at them.** Per-viewer progress over an *uncapped* list is exactly
   the page CLAUDE.md warns about — a 5,000-game progress list read by a 250,000-trophy hunter. Lists
   have no size cap on purpose, so the feature and the safety rule pull against each other.
2. **The use case is really a user-created challenge**, and Challenges is being rebuilt. "Beat all 30
   of these, here is your standing" wants a start date, evaluation and a payout — everything the
   table below says makes something a Challenge. Building it here would have meant building a second,
   weaker challenge engine inside Lists and then owning both.

**Tier: cut from list types, promoted to its own system** — see
[tier-lists.md](tier-lists.md). It is a prompt that invites a response, not a list with a palette.

Consequences worth recording, because they are all *removals*:

- **`GameListItem` does NOT gain a nullable `game` FK.** It existed only so Progress could track one
  platform stack rather than the whole concept, and with Progress gone the two partial constraints go
  with it. It also removes an obligation from `Concept.absorb()`'s `GameListItem` branch, which would
  have had to handle both shapes.
- **Sectioned lost its main justification.** It was carried largely as Tier's engine ("build
  sectioning once"), and Tier no longer needs it. "Finished / Playing / Someday" is still a real,
  one-rendering list type — but it now has to earn its place on its own, and nobody has asked for it.
  Treat it as a candidate, not a commitment.

## Lists are not Challenges

The line, decided 2026-09 and **amended 2026-09-13** now that user-created challenges are in scope
for the Challenges rebuild:

| | Game List | Challenge |
|---|---|---|
| Authored by | a hunter | PlatPursuit **or a hunter** |
| Content | curated, open-ended | fixed criteria, a start date |
| Reward | none; the artifact is the point | PlatPursuit-specific rewards |
| Progress | not shown | evaluated and paid out |

**What moved:** authorship is no longer the distinguishing test, because hunters will be able to
author challenges. The test is now **evaluation**: a Challenge measures you against criteria over
time and pays out; a List is an artifact that simply exists.

Hunters are still free to use a public list as an informal prompt for their followers ("beat all 30
of these"). That needs no feature work — and once user-created challenges exist, the hunter who wants
it *measured* has the right tool.

## The types

| Type | Sections | Order | Status |
|---|---|---|---|
| **Collection** | none | insertion | **Shipped 2026-09.** The default. |
| **Ranked** | none | author, 1..N | **Shipped 2026-09.** Numerals + drag. |
| **Top-N** | none | author, 1..N | Candidate. Ranked with a cap — constraint drives quality, and it makes the best share card. |
| **Sectioned** | author-named | within section | Candidate, and now unjustified on its own (see above). |

## Parked: brackets

**A bracket is genuinely wanted and deliberately not scheduled.** Owner's call, 2026-09.

Why it is parked rather than dropped: a bracket is a *process*, not an arrangement of rows. It has
state (which round, which matchups resolved), it is filled in over time, and the interesting version
is one other people vote in.

**It fails the one-rendering test the same way Tier does**, and for the same reason — so if it is
ever built, it belongs beside the tier/grid system rather than in `list_type`. That is a much clearer
home than it had when this was written, and it is worth revisiting once that system exists.

What makes it attractive anyway: it is the most *shareable* thing on this page, and "seed 16 games,
crown one" is a format the community already runs by hand in Discord threads.
