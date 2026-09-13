# Game List Types

> **Status: CLOSED at two types.** Collection and Ranked are built (2026-09) — see
> [docs/features/game-lists.md](../features/game-lists.md#list-types). Everything else once planned
> here is cut, its own system, or an orthogonal capability (2026-09-13); see
> [What left, and where it went](#what-left-and-where-it-went). Nothing in this document is
> outstanding work.
>
> See also: [product-identity.md](product-identity.md), and the rebuild's own
> [playbook](rebuild/rebuild-playbook.md).

## Why types at all

A list of games is one data shape with several jobs. A backlog and a top-ten are the same rows
arranged differently, and building them as one model with several presentations is what makes the
ambition affordable. Building them as separate features is what would make it a swamp.

**The whole type system is ONE field.** `GameList.list_type` picks the presentation, holding
`collection` and `ranked`. `position` already existed, is dense, and `Meta.ordering = ['position']`,
so ranked ordering was free at the model level — Ranked cost one CharField and no item migration,
which is the evidence that this framing was right for the types it still covers.

(An earlier version added "and one on the item", a `GameListItem.group` holding a section key.
Sections turned out not to be a type at all — see [list-sections.md](list-sections.md) — and they
want their own table rather than a key, because a CharField cannot carry section ORDER or an empty
section.)

## The test a type has to pass

Added 2026-09-13, and it is the reason three planned types left.

> **A list type has ONE rendering. Every viewer sees identical bytes.**

Collection and Ranked pass: one author arranges rows, and what you see does not depend on who you
are. `list_type` is a *presentation* field, and a presentation is exactly what they differ by.

Progress, Backlog and Tier fail it, in two different ways:

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
- **Sectioned lost its main justification here**, and found a better one elsewhere. It was carried
  largely as Tier's engine ("build sectioning once"), and Tier no longer needed it. Rather than
  earning its place as a type, it stopped being one: see [list-sections.md](list-sections.md).

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

| Type | Order | Status |
|---|---|---|
| **Collection** | insertion | **Shipped 2026-09.** The default. |
| **Ranked** | author, 1..N | **Shipped 2026-09.** Numerals + drag. |

(The Sections column this table used to carry went with the decision that sections are not a type.
Either type can have them.)

**Top-N was cut** (owner's call, 2026-09-13): once Ranked exists, a hunter who wants a top ten simply
makes one with ten games. A cap adds enforcement, an edge case when it is lowered below the current
size, and a second thing that renders identically to Ranked — for a constraint people apply
themselves.

**Sectioned is no longer a type either.** It became a capability that composes with the two that
exist, and moved to [list-sections.md](list-sections.md). It failed the one-rendering test from the
other direction: sectioning is not a different presentation, it is structure any presentation can
carry, and as a type it would have meant `sectioned` plus `sectioned-ranked` and a combinatorial
table.

**Which leaves the type system finished at two.** Collection and Ranked are what `list_type` is for,
and everything else that was once planned for it is either cut, its own system, or an orthogonal
capability. That is a better outcome than the seven-row table this document opened with.

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
