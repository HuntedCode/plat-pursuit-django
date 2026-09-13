# Game Lists

A hunter curates games into a named list, keeps it private or publishes it, and other hunters like
and follow it. Rebuilt 2026-09 in a new `gamelists` app; the 2019-era system it replaces still exists
under the same three class names in `trophies`.

> **Status: live (2026-09).** `_DevelopmentGate` is gone and the guard file that pinned the
> teardown is now `tests/engine/test_lists_live.py`. The switch was never just the mixin — see
> [What turning it on actually took](#what-turning-it-on-actually-took).

---

## Two systems, three shared class names

This is the first thing to get right, because an import from the wrong module is silent.

| | Legacy | Rebuilt |
|---|---|---|
| Module | `trophies.models` | `gamelists.models` |
| Owner field | `profile` | `owner` |
| An item points at | **`Game`** (a trophy list) | **`Concept`** (the site's word "game") |
| Routed | no | yes (staff-gated) |
| Tables | **retained** | new |

The legacy tables stay deliberately: they hold every existing list, and the rebuild offers a per-list
importer that reads them. The legacy *code* is unreachable — `trophies/views/list_views.py` is routed
nowhere, and `plat_pursuit/urls.py` imports its list views from `gamelists.views`.

**`trophies/views/__init__.py` still exports `BrowseListsView`, `GameListDetailView` and
`MyListsView`** — the same three names the URLconf imports from `gamelists.views`. Adding any of them
to the big `from trophies.views import …` line would silently shadow the live routes with dead views,
and Django would not complain.

---

## Architecture Overview

Three surfaces, not five. The system this replaced had separate create and edit *addresses*, so
renaming a list you were looking at cost three round trips.

| Surface | Path | What it is |
|---|---|---|
| Browse | `/community/lists/` | Every public list. Filter, sort, infinite scroll. |
| Detail | `/community/lists/<id>/` | One list. **The owner edits here, in place.** |
| My Lists | `/my-lists/` | Your lists and the ones you follow. Create is a modal. |

**A service owns every write.** `gamelists/services/game_list_service.py` is the only thing that
writes to these tables. The legacy system had no service — its writes lived inline across twelve API
views, which is exactly why lists were the one user-content system on the site with no restriction
gate.

---

## File Map

| Path | Role |
|---|---|
| `gamelists/models.py` | The five models, the queryset, the caps and field lengths |
| `gamelists/services/game_list_service.py` | **Every write.** Rules live here, nowhere else |
| `gamelists/services/covers.py` | Batched cover resolution for the tile mosaic |
| `gamelists/views.py` | Three pages, twelve JSON endpoints, and the create form post |
| `templates/gamelists/` | `browse.html`, `my_lists.html`, `detail.html` + partials |
| `static/js/lists-browse.js` | Browse page motion + infinite scroll |
| `static/js/gamelists.js` | My Lists: the create dialog and the scope switcher |
| `static/js/list-detail.js` | Detail: the adder, remove, like/follow, rename, publish, arranging, sections |
| `static/css/components/gamelists.css` | Only what the shared primitives do not cover |

---

## Data Model

Full field-level detail in [data-model.md](../architecture/data-model.md). What matters here:

- **`GameListItem.concept`, not `game`.** Keyed on `Game`, a backlog held Elden Ring twice — once per
  platform stack. This is the most consequential difference from the legacy model, and it is why
  `Concept.absorb()` carries a `GameListItem` branch (see CLAUDE.md for that contract).
- **`position` is dense**, and that is load-bearing past ordering: `covers.attach_cover_games` builds
  the tile mosaic with `position__lt=4`, so a gap renders a three-cover mosaic on a four-game list.
- **`GameListFollow` is the site's first follow relation.** Nothing else on the site has
  follow/follower semantics.
- **`GameListItem.section` is nullable and `SET_NULL`.** Deleting a section keeps its games —
  they fall back into the ungrouped bucket. A `CASCADE` here would delete somebody's games because
  they tidied a header, which is the opposite of what the control says it does. `GameListSection`
  itself cascades from its list, and **needs no `Concept.absorb()` branch**: it has no relation to
  `Concept` at all, and its items travel through the `GameListItem` branch that is already there.
- Denormalized `game_count` / `like_count` / `follower_count`, written only by the service.

### Visibility

`GameListQuerySet` gives one supported read per question. **Which of them an index actually serves
is narrower than it looks**, and the difference is the sort:

| Method | Question | Indexed |
|---|---|---|
| `visible()` | not soft-deleted — the floor | ❌ (no owner, no ordering: nothing to serve) |
| `public()` | somebody else's list | ⚠️ only when the caller NAMES one of the two browse sorts |
| `owned_by(profile)` | your lists | ✅ |
| `readable_by(profile)` | public **or** yours — what a detail page asks | ❌ (its OR spans two columns) |

The three partial indexes are `(owner, -updated_at)` and the two public browse sorts,
`(-like_count, -created_at)` and `(-created_at)`. `Meta.ordering` is `-updated_at`, which matches
**neither** public index — so a `.public()` read that leans on the default ordering is a scan, and
`BrowseListsView` orders explicitly for exactly that reason.

`readable_by()` should stay bounded to one list or a small page. The manager deliberately does **not**
filter in `get_queryset()`: a default manager that hides soft-deleted rows makes `objects` lie about
what is in the table, and admin, undelete and `count()` all then need a second manager.

### Caps

**3 lists free, 25 for members. No cap on list size.** That absence is deliberate — the legacy system
gave members unlimited games per list, so a ceiling would take a perk back and could make the importer
refuse a member's own data. Enforced in exactly one place, `max_lists_for`.

---

## List types

`GameList.list_type` picks the PRESENTATION and nothing else. The rows are identical either way,
so switching is lossless and needs no migration of items — which is why the picker says so out
loud, since the thing that stops people choosing is the fear of choosing wrong.

| Type | What it is | Order |
|---|---|---|
| **Collection** | the default: a shelf | unordered; A-Z and date sorts, no drag |
| **Ranked** | an order the author chose | `position`, 1-based numerals, drag to arrange |

**Only the types that RENDER are declared.** `LIST_TYPE_CHOICES` holds these two, because a choice a
template cannot draw means somebody picks "Tier" and gets a Collection with a different label.
`choices` is not a database constraint, so `_check_list_type` in the service **and the
`gamelist_list_type_valid` CheckConstraint** are what keep the column honest — for the shell and the
importer too.

**The planned type list shrank on 2026-09-13.** Progress and the Backlog tracker were cut (the whale
rule, and the use case is really a user-created challenge — now in scope for the Challenges rebuild),
and Tier became [its own system](../design/tier-lists.md) rather than a type, because a tier list has
one template and N per-viewer responses and `list_type` is a presentation field. Top-N was cut too:
once Ranked exists, a hunter self-regulates the length. Sectioned did not become a type either — it
became a **capability that composes with both** (below), which is why the table above still has two
rows. See [game-list-types.md](../design/game-list-types.md#the-test-a-type-has-to-pass) for the test
a type now has to pass.

---

## Sections

**A capability, not a type.** Sections group the games on a list under named headers, and they work
identically on a Collection and on a Ranked list — which is exactly why they are not a third row in
the table above: a type decides how a list PRESENTS, and sections decide how it is DIVIDED. Making
"Sectioned" a type would have meant four types the day a second capability arrived.

**Members only, for creating and renaming.** Everyone makes lists, adds games, ranks them, publishes
and shares. Members get to *organise* them — the same "everyone X, members X more" shape the `sync`
perk already ships, rather than a capability a free hunter cannot reach at all. What is **not** gated,
and is the part easiest to get wrong:

| Act | Gated | Why |
|---|---|---|
| Create a section | **yes** | authoring a new thing |
| Rename a section | **yes** | same |
| Delete a section | no | removing your own thing is not the act the perk covers |
| Reorder sections | no | arranging |
| Move a game between sections | no | arranging |
| Choose the numbering mode | no | a display choice, ungated like `list_type` |
| **Read** a sectioned list | no | sections are the author's tool; a free hunter's view is identical |

A **lapsed** member keeps every section they have and keeps arranging them. Membership ending must
never delete data or reshuffle a list. The page says so in a line rather than silently dropping the
controls, which on a list that visibly has sections reads as a bug.

### Rendering

`_grouped()` returns `[(section_or_None, [items])]` — the **ungrouped bucket first**, then sections in
their own `position`. Ungrouped leads because a list that has just gained its first section has
everything in it, so burying it would hide the games somebody is about to file; it is omitted only
when empty. The chosen sort orders *within* each group, so sorting a sectioned list A-Z sorts inside
each section rather than flattening the grouping away. **One extra query** for the whole page
(`game_list.sections.all()`), and the grouping is done in Python over rows already fetched.

### Numbering (Ranked only)

Two modes on `GameList.sections_restart_numbering`, both computed at render — `position` stays global
and dense, so neither mode stores or reorders anything.

- **Continue through** (default): 1..N down the list.
- **Restart in each section**: back to 1 under every header.

**Once a list has sections, the sections are part of the sequence.** A rank is computed from the
canonical order — sections in their own order, `position` within each — and then *displayed* under
whatever sort is showing. Two wrong answers preceded that rule and both are worth knowing:

1. `position + 1` straight through. Correct on a flat list, unreadable on a sectioned one: items 0-3
   alternating between two sections print "1, 3" under one header and "2, 4" under the next. Every
   numeral individually true, and the column cannot be read down.
2. Number the *rendered* order. Honours the rank sort and destroys everything else — sorted A-Z the
   list renumbers 1..N alphabetically, claiming the alphabet was the author's ranking. `detail_card`
   shows the plate on every sort precisely because a rank is a fact about the **entry**, not the view.

### Filing a game: two payloads, on purpose

Dragging a card into another section posts to **one of two endpoints**, and the split is the design
rather than an omission:

| Page state | Endpoint | Why |
|---|---|---|
| Ranked **at the `rank` sort** | `list_reorder` (+ `moved_item`, `section`) | the drop POSITION is content, and the order and the filing must land together or not at all |
| Anything else | `list_item_assign` | the position under the cursor belongs to the SORT; posting it would rewrite the author's sequence to match a view of it |

In the second case the drag is configured `sort: false`, so the gesture cannot even promise an order
it will not keep. Two context flags carry the distinction to the template: **`can_arrange`** ("a card
can be dragged at all") and the stricter **`can_reorder`** ("a drop position means something").
Collapsing them into one is what an earlier slice did, and it had to withhold the drag from every
sectioned list to stay honest.

`reorder()` applies the **assignment first**, so a refused section leaves the order untouched. The
function is `@transaction.atomic`, so a raise would roll the whole thing back anyway; the ordering is
belt-and-braces against a future caller that drops the decorator, and it keeps the refusal cheap.

`section_id=None` **with** a `moved_item_id` means the loose bucket, which is a real destination —
dragging a card out of every section is how you un-file one. So "no section" and "no move" are told
apart by whether `moved_item_id` was sent, never by `section_id` being falsy. The same distinction
runs all the way out to `data-section-id=""` on the ungrouped grid.

### What Ranked adds

- A `rank` ("List order") sort, offered ONLY by ranked lists and their default.
- A numeral per card, as an **opaque plate in the cover's top-left corner**. It started in the text
  strip beside the title, on the reasoning that these cards had just been moved off the overlay tile
  so nothing would sit on the art. That reasoning holds for a TITLE (long, wrapping, needing a scrim
  that darkens the image it is printed over) and not for two characters: a small plate covers a
  corner rather than the picture, and on a ranked list the number is what you are scanning for.
  Opaque rather than tinted is the load-bearing part: a translucent chip reads well over the dark
  covers most games ship and vanishes over a bright or high-frequency one.
- The numeral shows on **every** sort, because a rank is a fact about the entry rather than about
  the current view. The drag handles do not, because rearranging a sorted page would post an
  order that means nothing.
- Drag via `DragReorderManager` (SortableJS). **The whole card is the drag surface**, not just the
  grip: the grip was a 26px target on a 166px card, and people reach for the thing itself. That is
  only safe because the card's navigation is suppressed while the mode is on — a click the browser
  did not classify as a drag would otherwise leave the page mid-rearrange. The grip stays as the
  keyboard affordance and the per-card signal that a card is movable.
- **Touch requires a hold** (320ms, `delayOnTouchOnly`), because a finger resting on a card is how a
  scroll begins. `touchStartThreshold` lets a scroll cancel a pending pick-up — without it a finger
  that drifts during the hold arms the drag anyway and the scroll is lost.
- **Clicking a card picks it up; the arrow keys then move it.** Escape or a second click drops it.
  This replaced an arrow-key path bound to the grid that only fired while a *grip* had focus — which
  meant tabbing to a 26px control nobody had reason to suspect, so the on-screen hint described a key
  that appeared to do nothing. It also gives the click a job: suppressing the card's navigation was
  necessary once the whole card became the drag surface, but it left a click meaning nothing, and a
  card that visibly ignores you reads as broken. A focused grip still wins over the picked card, so
  the tab-and-arrow path survives for keyboard readers.
- The key listener is on the **document**, so it needs an `isTyping` guard: the identity editor is
  open whenever this mode is, and an arrow key moving the caret through the list's name must not also
  move a card.

### Position editing is a mode

Reordering is **entered deliberately**, not always live. `data-gl-reorder` is the server saying it is
POSSIBLE here; the mode is the hunter saying they want to do it now. Conflating the two shipped
first, and it made rearranging a list something you could do by accident.

Open the editor → **Edit list positions** → grips appear → drag or arrow-key → a pill reads *Saving*
then *Saved* → press **Done**, or close the editor, and it ends. The hint states that moves save
immediately, because the editor's Cancel button cannot undo a write that already happened.

**The bar sits directly above the grid**, not in the edit panel, where it started and was hard to
find: the one control the Ranked type exists for was the quietest thing on the page. It is still
gated on the editor being open — that is the deliberate-entry half — but it lives next to the thing
it changes, is full-width, and the whole surface changes colour when the mode is on rather than only
a button label.

Its visibility is the **server's answer plus one client condition**: `can_reorder` decides whether
the bar exists at all, and the JS shows it only while the editor is open.

There was briefly a third condition — whether the *selected* type radio was still Ranked — because
saving a type change meant a full page reload, so between switching the radio and saving, the bar
would have offered to reorder a list the hunter had just called a shelf. **Saving refreshes the bar's
slot instead**, so that condition is gone along with the reload, and what is left is more honest: the
list really is still ranked until the save lands.

### Saving a type change refreshes in place

A type switch changes three server-rendered things, and two of them live **outside** the swapped
panel: the cards (numerals and grips), which sorts exist, and whether the position bar exists at all.
That is why this used to reload the page — which lost the hunter's place and made them re-open the
editor to reach the positions they had just switched the list over to use.

One request now carries all three. The grid is the main swap; `?chrome=1` makes `detail_items.html`
append **out-of-band** copies of the sort `<select>` and the position-bar slot.

Two details worth keeping:

- It swaps **the `<select>`, not its form**. The form carries the htmx attributes and the `change`
  listener `browse-filters.js` binds to it; replacing it would silently unbind the sort auto-submit
  until the next full page load. A `change` event bubbles, so swapping only the options is safe.
- The bar is wrapped in a **slot that always exists** (`#gl-positions-slot`), empty when the list is a
  Collection. htmx matches out-of-band content by id against an element already in the document, and
  going Collection → Ranked would otherwise have no bar to match.

The refresh deliberately drops `?sort`: the new type has its own default, and a freshly-ranked list
that opened on A-Z would hide the ordering the switch was made for. The address bar is cleaned to
match. The editor stays open **only** for a type change — after a plain rename, closing it is the
natural "done", since the heading behind it has already updated.

While the mode is on, the grid changes state in three ways, and each is doing a job:

- **The hover goes quiet.** `.pp-gcard:hover` lifts the art, glows the border and recolours the
  title — an invitation to click, which is wrong while dragging, and it fires on every card the
  pointer crosses during a single drag, so the whole grid flickers.
- **The grid recesses into a tray and the cards lift off it.** Loose objects resting on a surface are
  things you can pick up, which is the whole message, and it holds that message without repeating
  it. Both halves are load-bearing: the recess is what the cards read as raised *relative to*, and
  the elevation is what makes them read as pick-up-able rather than merely selected — an accent
  border alone says "selected", which is a different idea. The dragged card lifts further still.

  This was a continuous ±0.55° wobble first (the phone-home-screen idiom), and it worked, but a grid
  of up to 200 cards moving forever is a lot to impose to convey one bit of state, and it keeps
  asking for attention long after it has been understood. Cut 2026-09.
- **It arrives as a transition, not an animation** — one moment of change, then stillness. Reduced
  motion skips the 200ms of easing and lands on the identical end state, so nobody loses the signal.

Three implementation facts that are not free choices:

- The mode flag lives on `#gl-items-panel`, the htmx swap **target**. On `#gl-items` (swapped
  content) htmx restores the server's attributes on settle and the flag disappears.
- Grips are `display: none` outside the mode, never `opacity: 0` — an invisible button is still a tab
  stop that announces itself.
- `syncPositioning` runs on **`htmx:afterSettle`**, never `afterSwap`. During swap the new grid still
  wears the previous one's attributes, so the capability reads stale in both directions. See the
  Gotchas.

**Switching type is not restriction-gated.** Choosing between two presentations of your own rows
submits no content, so it sits with un-publishing and deleting on the allowed side of the line
`update_list` draws.

---

## Key Flows

### Publishing

A list is **private when it is born**, and publishing is a deliberate second act with its own
affordance — not a checkbox ticked while thinking about a name. The create dialog offers no
visibility control at all, for that reason.

Both visibility states are server-rendered and JS toggles `hidden`, so the act completes on the page
it started on. Un-publishing is offered too.

**A restricted hunter cannot publish, but can un-publish and delete.** The gate covers both ways
words reach people — writing them, and making written ones visible — while leaving a hunter free to
take their own content down, which is what restriction exists to encourage.

### Adding a game

The adder searches **concepts**, so a game appears once rather than once per platform stack. After a
write the item panel is re-rendered from the server rather than spliced client-side, which keeps sort
position, the empty state and the truncation line server-owned.

### Cover art

A `Concept` cannot answer "what does this game look like" — two of the cover chain's four sources live
on `Game`. `covers.cover_games_for()` picks a representative `Game` per concept in **one query** for
the whole page, mirroring the concept page's platform-priority rule. Never call it per card.

---

## API Endpoints

Under the page's own path rather than `/api/v1/`, because they are this page's behaviour: they share
the pages' gate, they answer one template's fetches, and routing them through DRF would mean a second
permission stack that has to agree with the first.

All are POST and JSON except the search, and all are rate-limited per user.

| Route | Name | Does | Limit |
|---|---|---|---|
| `/community/lists/create/` | `list_create` | Create (form post, redirects) | 30/m |
| `…/<id>/update/` | `list_update` | Rename, re-describe, **publish/unpublish** | 60/m |
| `…/<id>/reorder/` | `list_reorder` | Set item order (Ranked) | 60/m |
| `…/<id>/like/` | `list_like` | Like / unlike | 60/m |
| `…/<id>/follow/` | `list_follow` | Follow / unfollow | 60/m |
| `…/<id>/add/` | `list_add_game` | Add a concept | 120/m |
| `…/<id>/items/<item>/remove/` | `list_remove_game` | Remove an entry | 120/m |
| `…/<id>/items/<item>/section/` | `list_item_assign` | File one entry under a section | 120/m |
| `…/<id>/sections/` | `list_section_create` | Add a section (**members**) | 30/m |
| `…/<id>/sections/reorder/` | `list_sections_reorder` | Set section order | 60/m |
| `…/<id>/sections/<s>/rename/` | `list_section_rename` | Rename (**members**) | 60/m |
| `…/<id>/sections/<s>/delete/` | `list_section_delete` | Delete a section | 30/m |
| `…/<id>/search/` | `list_game_search` | Adder typeahead (GET) | 120/m |

**`list_reorder` is reached by Ranked lists only** (2026-09). A Collection is unordered by design,
so the drag handles render only when the server says `can_reorder`: owner, ranked, showing the
real sequence, and short enough to render whole. The endpoint had been built and left dormant
ahead of that UI and needed no changes when it arrived.

Every endpoint **that takes a list id** resolves it through `readable_by()` and answers a uniform
**404** — never 403, never a service error — so an id alone can never confirm that a list exists or
whose it is. `list_create` is the exception with nothing to resolve: it is a form post that
redirects with a Django message rather than answering JSON.

**Sub-resources are resolved WITHIN the list, never by their own id.** A section (and an item) is
looked up as `filter(pk=…, game_list=game_list)`, so "exists on somebody else's list" and "does not
exist" answer identically. Looking a section up by id alone would make the difference an oracle on
the id space most easily walked — there are only a handful of sections per list. `AssignItemView`
runs the raw `section` through `safe_int` before that filter, because `filter(pk='abc')` raises
`ValueError` and a junk value would otherwise be a 500 on a route any logged-in hunter can post to.

---

## Integration Points

- **`Concept.absorb()`** — the `GameListItem` branch. Its dedup is load-bearing and cannot be a bare
  `.update()`; see CLAUDE.md.
- **`restriction_service`** — scope `all_ugc`, checked by the service on every write that puts words
  in front of people.
- **`BannedWord`** + `CommentService.sanitize_text` — note the sanitizer is **not idempotent**, so the
  service runs it to a fixpoint.
- **Account deletion** — `users/views.py` hard-deletes a hunter's lists.
- **Shared front-end primitives** — `.pp-gcard` (the game card), `.pp-gtile__mosaic`, `.pp-switch`,
  `.pp-cta`, `[data-search-wrap]` + `wireSearchField`, `HtmxListMixin`, `browse-filters.js`,
  `InfiniteScroller`, `staggerReveal`, `wireCharCounters`.

---

## Gotchas and Pitfalls

- **Import from the right module.** Three class names exist twice, and `trophies/views/__init__.py`
  re-exports three view names the URLconf now takes from `gamelists.views`.
- **`position` must stay dense.** A gap silently renders a short mosaic. Deleting a `Concept`
  cascades items away and leaves **both** a gap and a stale `game_count` — `_recount` does not
  self-heal that, despite once claiming to. No `post_delete` receiver exists yet.
- **Writes post FormData, not JSON.** Every endpoint reads `request.POST`, which Django leaves empty
  for `application/json`. `API.post` would send a body the views cannot see, and a boolean would read
  as absent — i.e. false — on every press, silently.
- **`fetch` follows redirects.** An expired session arrives as `200 text/html`, so a write helper must
  refuse a non-object body rather than treat a login page as success.
- **The adder's typeahead is a sequential scan.** Django compiles `__icontains` to
  `UPPER(col::text) LIKE …`, which the `gin_trgm_ops` index on the raw column cannot serve. Bounded,
  cached and rate-limited; the index question is shared catalogue work and belongs in its own lane.
- **Wire the drag on `htmx:afterSettle`, not `afterSwap`.** htmx copies the OLD node's attributes
  onto the new one before insertion and restores the real ones on settle, so during `afterSwap` an
  id'd swapped element reads as whatever the previous content was. Sorting a ranked list A-Z and back
  left every grip inert; the reverse wired a grid that must not be draggable *and* marked it handled
  so settle could not undo it.
- **A truncated list cannot be reordered**, and says so. `reorder` refuses a partial ordering by
  design, so past `MAX_ITEMS_RENDERED` the page cannot post a complete one and `can_reorder` goes
  false. The handles vanishing without explanation would read as a bug on the one type built for
  ordering, so the truncation line adds a sentence for the owner.
- **The detail page renders at most 200 items** (`MAX_ITEMS_RENDERED`) and says so. Real pagination is
  a follow-up.
- **`#gl-items` does not exist on a sectioned list.** A sectioned list renders a grid per group, so
  anything reaching for that id gets `null` there. Both refresh helpers used it as their
  swap-happened sentinel (`node before !== node after`), which on a sectioned list compared
  `null === null` and reported a failure over a swap that had just worked; `onAfterSwap` used it to
  ask whether the panel held any cards, got `false`, disagreed with the toolbar's presence and
  **reloaded the page — on every swap, including the one the reload caused**. `#gl-items-root` wraps
  all three shapes of the partial and is what those three now read.
- **A drop is not always a reorder.** See [Filing a game](#filing-a-game-two-payloads-on-purpose).
  Posting the drop index under a non-`rank` sort rewrites the author's sequence to match a view.
- **The arrange bar is not gated on the drag.** It also carries "Add a section", so tying the whole
  bar to whether a drag is possible hid the only control that could make one possible — a member with
  a section-less Collection had no way in.
- **The ungrouped bucket stays when empty, for whoever can arrange.** A reader never sees a header
  over nothing; the owner always does, because it is the only way back *out* of a section. Filing the
  last loose card used to remove the bucket and take the drop target with it, so nothing could be
  un-filed by pointer (no grid to drop onto) or by keyboard (no group before the first section) until
  the owner deleted a whole section to get their game back.
- **`:empty` does not tolerate whitespace.** The empty-section drop box is a `::before` gated on
  `:empty`, so the grid's tags must close up tight against the `{% for %}` — laid out over separate
  lines an empty grid still holds `"
    
"`, never matches, and the box never draws in any state.
  Selectors 4 relaxes this; no shipping engine implements it. A test asserts the rendered grid is
  byte-for-byte empty, because asserting the *attribute* is present passes over an invisible box.
- **SortableJS routes `end` to the drag's SOURCE, not its destination.** `onMove` therefore fires on
  the manager the card left; read `evt.to` for where it landed. `utils.js` asserted the opposite for a
  long time and the comment is now corrected there — reading the manager's own container files every
  card straight back where it came from, which looks like a working drag that undoes itself.
- **A cross-section move always refreshes, so it always drops the pick-up.** `refreshItems` →
  settle → `syncPositioning` → `attachDrag` → `detachDrag` → `dropPicked`. The keyboard path carries
  `pendingPickId` across and `restorePick` re-applies it *after* `syncPositioning`; without that the
  keys worked exactly once per pick.
- **Give every `refreshItems` its own catch.** It rejects on a 4xx/5xx, so a shared `.catch` reports
  a stale view as a failed write — "That section could not be added" over a section that exists, and
  `create_section` does not dedupe names.

---

## What turning it on actually took

Kept because the shape recurs: removing the gate was the smallest part, and two of these would have
shipped a visible defect.

1. **The gate.** One class and six mixin references in `gamelists/views.py`.
2. **The guard file inverted**, `test_lists_hidden.py` → `test_lists_live.py`. About half of it
   flipped; the other half is *more* valuable now, because it pins that the LEGACY system stays dead
   — its API unrouted, its rows untouched, `?tab=lists` leading nowhere. A live feature is exactly
   when somebody wires a new page to an old view by reaching for a familiar name.
3. **`GameListSitemap` was a landmine.** It read the legacy `trophies.GameList` while
   `reverse('list_detail')` resolves to the rebuilt app — the two tables share nothing but a class
   name. Enabling it unchanged would have published thousands of legacy ids against new-app routes:
   a sitemap of 404s, handed to Google on day one. Being commented out of the index is the only
   reason that never shipped. Re-pointed, then enabled.
4. **`lists_browse` added to `StaticViewSitemap`**, and robots rules for the parts a crawler has no
   use for: `/my-lists/` (login-only), the typeahead (bare JSON), and the POST-only write paths.
   Listed individually rather than as `/community/lists/*`, because that would also match the detail
   pages — the same mistake the `/games/*/*` rules made in 2026-08.
5. **The Community rail turned on.** Game Lists joins Hunters there; **My Lists goes to My Pursuit →
   Tools**, because the private side of a public system is still personal. `test_nav_reachability`
   then caught that `/my-lists/` was not under any My Pursuit prefix — a rail item whose URL sits
   outside its own hub drops you out of the hub the moment you click it.
6. **`seo_description` and `seo_title` on the detail page.** It is the indexable, shareable page in
   the feature, and without them every list anybody posted previewed as the site-wide generic.
7. **The `game_list_create` SiteEvent**, declared since 2019 with its only call site in an unrouted
   module — so a grep found a hit and it had never once fired.

**`game_list_share` is still unwired**, because there is no share affordance yet. It is declared and
dead, exactly as `game_list_create` was.

---

## Related Docs

- [game-list-types.md](../design/game-list-types.md) — the planned type system, and the
  lists-vs-challenges line
- [data-model.md](../architecture/data-model.md) — field-level model detail
- [api-endpoints.md](../reference/api-endpoints.md)
- [ia-and-subnav.md](../architecture/ia-and-subnav.md) — where these pages live (Community hub,
  decided 2026-09, ships with the un-hide)
