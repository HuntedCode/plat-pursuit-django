# Game List Types

> **Status:** design. Collection ships in the Game Lists rebuild; the other types are planned on top
> of it. Nothing below is implemented except Collection.
>
> See also: [product-identity.md](product-identity.md), and the rebuild's own
> [playbook](rebuild/rebuild-playbook.md).

## Why types at all

A list of games is one data shape with several jobs. A backlog, a top-ten and a tier list are the
same rows arranged differently, and building them as one model with several presentations is what
makes the ambition affordable. Building them as separate features is what would make it a swamp.

**The whole type system is one field on the list and one on the item.** `GameList.list_type` picks
the presentation; `GameListItem.group` holds a section key. `position` already exists, is dense, and
`Meta.ordering = ['position']`, so ranked ordering is free at the model level.

## Lists are not Challenges

The line, decided 2026-09:

| | Game List | Challenge |
|---|---|---|
| Authored by | a hunter | PlatPursuit |
| Content | curated, open-ended | fixed criteria, a start date |
| Reward | none; the artifact is the point | PlatPursuit-specific rewards |
| Progress | shown | evaluated and paid out |

Hunters are **encouraged** to use a public list as an informal challenge for their followers ("beat
all 30 of these"). That is a good use of lists and needs no feature work. What makes something a
Challenge is that PlatPursuit authored it and pays for it.

## The types

| Type | Sections | Order | Notes |
|---|---|---|---|
| **Collection** | none | insertion | The default. What ships first. |
| **Ranked** | none | author, 1..N | Numerals + drag. `position` already does the work. |
| **Top-N** | none | author, 1..N | Ranked with a cap. Constraint drives quality; makes the best share card. |
| **Progress** | none | any | Each row shows trophy progress. See below. |
| **Sectioned** | author-named | within section | "Finished / Playing / Someday". |
| **Tier** | fixed S/A/B/C/D | within tier | **Sectioned with a fixed palette and labels.** Not a separate engine. |
| **Backlog tracker** | *derived* | within section | Sections move themselves. See below. |

Tier being a preset of Sectioned is the load-bearing relationship here. Build sectioning once.

## Progress lists

Rows carry trophy progress. This is the type no general-purpose list tool can copy, because it needs
to know the viewer's library.

Two faces, one build:

- **Owner-facing:** "PS5 Backlog, 4 of 12 platinumed", with real progress per row.
- **Viewer-facing:** a public list where *the viewer* sees their own standing. "The 50 Hardest
  Platinums — you have 7." This is the shareable, argument-starting version and it is nearly free
  once the first exists.

### Concept first, stacks by choice

A list holds **Concepts** (see the Games/Trophy Lists IA). For progress, a hunter may opt an entry
into being tracked as **individual trophy lists** instead — somebody completing both the PS4 and PS5
versions wants two progress bars, not one merged number.

**This must not be the default.** The default is the Concept, because "the game" is the concept and
a merged view is what almost everybody wants. Splitting is a per-entry opt-in.

Schema consequence: `GameListItem` gains a nullable `game` FK. `null` = the whole concept; set = that
one stack. The unique constraint cannot simply become `(game_list, concept, game)` — Postgres treats
NULLs as **distinct**, so that permits unlimited duplicate concept-level rows. Use two partial
constraints instead:

- `unique(game_list, concept) where game is null`
- `unique(game_list, game) where game is not null`

`Concept.absorb()`'s `GameListItem` branch (see CLAUDE.md) must handle both shapes when this lands.

## The backlog tracker

A progress list whose **sections are derived from trophy data rather than authored**. Games enter at
Backlog and move themselves as the hunter plays.

```
Backlog  ->  Platinumed  ->  100% Complete
```

Two rules that are easy to get wrong, both settled here:

**1. Gate on base-group completion, not on `has_plat`.** Plenty of games ship without a platinum
trophy. A section that fills from `ProfileGame.has_plat` can never receive them, so a fully finished
plat-less game would sit in Backlog forever, which reads as a bug. The trigger is the **default
trophy group at 100%**.

**2. The furthest section wins.** Most games have no DLC, so base-group-100% and all-groups-100% are
the same moment and the entry qualifies for both at once. It lands in **100% Complete**.

That resolution is what earns "Platinumed" its place: it comes to mean *plat earned, DLC still
outstanding* — precisely the backlog state hunters care about, and a section that would otherwise
just be a weaker duplicate of the last one.

### Data

Everything needed is already denormalized and indexed:

- `ProfileGame.progress` / `.has_plat` — per (profile, game)
- `ProfileTrophyGroup.progress` — per (profile, trophy group), which is what rules 1 and 2 read

**Derive at render from that denorm; do not add a third layer.** A stored section key is a stale-data
bug waiting to happen (the trophy data moves on every sync, the list does not). One aggregate query
per render, never per-row iteration — the whale rule in CLAUDE.md applies with full force here,
because a backlog tracker is exactly the kind of page a 250,000-trophy hunter opens.

### Open questions

- Whose progress drives the sections on someone else's list — the owner's (their journey) or the
  viewer's? Leaning owner, with the viewer's own progress shown per row.
- Can the author override a derived section? Leaning no: that is what Sectioned is for.
- The move from Backlog to Platinumed is a real emotional beat. A small ceremony (the site has the
  patterns) is worth considering, but not in v1.

## Parked: brackets

**A bracket is genuinely wanted and deliberately not scheduled.** Owner's call, 2026-09: worth
picking up once there is a good idea about implementation.

Why it is parked rather than dropped: a bracket is a *process*, not an arrangement of rows. It has
state (which round, which matchups resolved), it is filled in over time, and the interesting version
is one other people vote in — which is a different feature from every type above, all of which are
one author arranging rows. It does not fit the `list_type` + `group` model, so it needs its own
thinking rather than a slot in the table.

What makes it attractive anyway: it is the most *shareable* thing on this page, and "seed 16 games,
crown one" is a format the community already runs by hand in Discord threads.

Pick this up when: the type system above has shipped, and there is an answer for how a bracket is
filled in (author alone? followers vote? a poll with a deadline?).
