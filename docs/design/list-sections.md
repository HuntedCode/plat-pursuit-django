# List Sections

> **Status:** design, nothing built. Decided 2026-09-13 with the owner. Sections are a **capability
> that composes with the list types**, not a type of their own — which is why this is its own
> document rather than a row in [game-list-types.md](game-list-types.md).
>
> **Members only.** Everyone creates and uses lists; sections are the perk.

## What it is

A hunter groups the games on a list into author-named sections — *Finished / Playing / Someday*, or
*PS4 / PS5*, or whatever the list is for. It works on both existing types:

| On a | Sections do this |
|---|---|
| **Collection** | group an unordered shelf; insertion order within each section |
| **Ranked** | group the ordering, so the author ranks *within* a section |

A ranked, sectioned list chooses how it numbers: **continue straight through** (1..N across the whole
list) or **restart at 1 in each section**. Both are real and neither is obviously the default; the
list picks.

## Why it is not a type

The type system's rule is that **`list_type` picks a PRESENTATION** — see
[game-list-types.md](game-list-types.md#the-test-a-type-has-to-pass), which is what removed Progress
and Tier from it. Sectioning fails that test in the other direction from those two: it is not a
different presentation, it is *structure that any presentation can carry*. Making it a type would
mean a `sectioned` and a `sectioned-ranked` and eventually a combinatorial table, which is exactly
the swamp the one-field model exists to avoid.

So `list_type` stays orthogonal, and a list is `(type, sections?)`.

## The model

**Sections get their own table.** The original sketch was a `GameListItem.group` CharField, and that
only works if sections sort alphabetically and cannot exist while empty. Both fail the first real
use: *Finished / Playing / Someday* wants that order, and a hunter creates "Someday" empty and then
drags into it.

```
GameListSection(game_list FK, name, position)   # dense, ordered, author-named
GameListItem.section FK, null=True              # null = ungrouped
```

**Ungrouped is a real state, not an error.** A list that gains sections has every item unassigned,
and forcing assignment before the page can render would make adding sections feel like a migration.
Ungrouped items render in their own bucket at the top.

### `position` stays GLOBAL and dense — this is the important one

It is tempting to make ordering per-section. Do not. `GameListItem.position` is dense across the
whole list today, and that is load-bearing beyond ordering: `covers.attach_cover_games` bounds the
browse tile's mosaic with `position__lt=4`, so a gap renders a three-cover mosaic on a four-game
list. Per-section positions would break that invariant and force `game_list_service.reorder` — which
refuses partial orderings by design — to be rewritten.

Keep it global, let sections be a grouping overlay, and **both numbering modes become render-time
derivations**:

- **continue through** → `position + 1`, exactly what ships today
- **restart per section** → the item's index within its section

The numbering choice therefore costs one boolean field and a template branch. No migration of
positions, no change to `reorder`, and the mosaic invariant is untouched.

## The membership gate

**Sections are members-only** (owner's call, 2026-09-13). Everyone creates lists, adds games, ranks
them, publishes and shares; members get to organise them.

It fits the shape the tiering already uses — `max_lists_for` reads `profile.user_is_premium`, and the
shipped `sync` perk is "everyone syncs, members sync more often" rather than a capability a free
hunter cannot reach at all. The precedent this replaces is the one that lost its subject: the plan
had wanted to member-gate "the more complicated list types", and Progress was cut while Tier left for
its own system.

Three rules that have to hold, and the second is the one that is easy to get wrong:

1. **The gate lives in the service**, next to `max_lists_for`, so there is one enforcement point.
2. **A lapsed member does not lose their sections.** Membership ending must not delete data or
   scramble a list — the sections stay, the list keeps rendering exactly as it did, and what they
   lose is the ability to CREATE or RENAME. Anything else is a takeback, and the caps comment in
   `models.py` already argues this about list size.
3. **A free hunter's view of a sectioned list is unaffected.** Sections are the author's tool; a
   reader does not need a membership to read a list that has them.

## Open questions

- **Switching type with sections present.** Ranked → Collection keeps them (they are orthogonal), but
  the numbering toggle becomes meaningless and should probably hide rather than reset.
- **Deleting a section.** Its items go ungrouped rather than being deleted — same reasoning as
  `absorb()`'s list branch, where a cascade would destroy hand-curated entries over a structural
  change the hunter never saw.
- **A cap on sections per list.** Lists are deliberately uncapped in SIZE; sections are cheap rows
  but each one is a rendered header. Probably wants a modest ceiling, unlike list size.
- **Drag between sections** reuses `DragReorderManager`'s existing cross-container support (`group`,
  `onMove`, `canAccept`), which is machinery that already exists rather than new work.
