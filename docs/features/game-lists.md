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
| `gamelists/admin.py` | The curation desk: featuring a list for the browse Spotlight. Read-mostly |
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
- **`featured_at` is the whole of the Spotlight's storage.** Nullable timestamp: null means not
  featured, and the most recent non-null wins. Deliberately not the `FeaturedGuide` /
  `FeaturedGame` / `FeaturedProfile` shape (FK + `priority` + a date window) that `trophies` already
  carries three copies of, two of which have no consumer outside the admin. See
  [The Spotlight](#the-spotlight).

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

**3 lists free, 25 for members** (`max_lists_for`), and **200 games per list, flat**
(`MAX_ITEMS_PER_LIST`, enforced in `add_concept`). One enforcement point each, so the shell, the
admin and the future importer are bound by them too.

The size cap arrived 2026-09-14 and overturned a "no cap" position held since the rebuild. The old
argument was that the legacy system gave members unlimited games per list, so a ceiling would take a
perk back — an argument about the membership system this one replaced, and no real list ever
approached 200 anyway.

**200 because that is what one page renders**, and the equality is the design rather than a
coincidence. A list longer than one render was genuinely half-broken: it truncated, its section
counts were computed from the slice and therefore lied, and it **could not be reordered at all**
(`reorder` refuses a partial ordering, so the page had to explain why the Ranked type's defining
feature was unavailable). Capping at the render bound does not improve that state, it deletes it —
along with the truncation notice, the `can_reorder` clause, the count-omission branch and their
tests. That is not housekeeping: two real defects came out of those branches, `can_arrange` silently
inheriting the truncation clause and the counts lying.

**Flat, not tiered.** The size cap is abuse prevention, and abuse prevention must not be purchasable
— a spam limit somebody can pay to raise is not a spam limit. The tiering stays on list *count*,
where it says the honest thing: members get more lists. Keeping it flat is also what keeps
`cap == render bound` true for everybody.

**Enforced on the way in, never by deletion.** A list somehow already over the cap keeps every row
and simply cannot take more. Counted from the **rows**, not from `game_count`, which drifts high when
a Concept is deleted (CASCADE, no service involved) — capping on the counter would lock a hunter out
of a list that has room, permanently, with nothing that clears it.

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
everything in it, so burying it would hide the games somebody is about to file. **It is omitted
whenever it is empty**, for readers and owners alike — see the Gotchas for why that stopped being an
owner exception in 2026-09. The chosen sort orders *within* each group, so sorting a sectioned list A-Z sorts inside
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

### Getting a game onto a list

Three entry points, as the design conversation settled. The adder on the list itself was built first;
the other two arrived 2026-09-15.

| Where | What it looks like |
|---|---|
| The shared game card | An icon, top-right of the cover, quiet until hover or focus. Included by **four** browse pages: Browse Games, Recently Added, tag detail, Trophy Lists browse. |
| Both game detail pages | A labelled "Add to list" button — there is room in an action row, and the card's reason for an icon (every cell of four grids) does not apply. |
| The list's own adder | The typeahead in the toolbar. |

All three open **one detached popover**, moved to whichever trigger was pressed and filled from a
single request. It adds, removes and creates. Three things about it are load-bearing:

- **The card needed a wrapper.** `.pp-gcard` is an `<a>`, and a `<button>` inside a link is invalid
  HTML that swallows the link's own activation. `.pp-gcard-wrap` makes the button a sibling, exactly
  as `.gl-item` does on list detail. It renders **only when the button does**, so a grid with no
  button keeps the DOM it had.
- **Infinite scroll has to clone the CELL.** `InfiniteScroller` clones `cardSelector` nodes out of the
  fetched HTML, so cloning the card alone dropped the wrapper and the button with it: page one had
  buttons and every page after the first scroll did not. `cellSelector` is the opt-in fix and all four
  grids pass it. **If you add a fifth grid, pass it there too.**
- **The card carries no membership state**, deliberately. Showing "already on a list" per card would
  mean a per-user query for every concept on four grids, one of them the main catalogue — the whale
  rule's exact case. A test asserts the browse grid makes **zero** queries against the list tables.
  The button is an action; the popover reads membership for one game, when it opens.

Two endpoints serve it: `lists_for_concept` (one bounded read — every list plus one `IN` over their
items) and `list_create_with_concept` (create and file in one transaction, because two requests can
leave an empty list named after a game it does not contain, with one of three slots spent).

### Previewing the non-member render

`?preview=lists-free` on any list you own renders the page as a **free hunter** sees it, for staff
and moderators only, through the shared door in `core/previews.py`. Sections are the one
membership-gated thing on this page, so a single flag is the whole surface: `can_manage_sections`
goes false and nothing else changes. Everything a free owner really can do — arrange, delete a
section, reorder, add games — stays live under the preview, because a preview that also withdrew
the ungated controls would answer a different question than the one it was asked. A ribbon says the
mode is on, which matters because for a free owner with no sections the honest render is that
*nothing appears* — indistinguishable from a broken preview otherwise.

**What a free owner actually sees**, which is worth knowing before the next membership decision:

| Their list | What renders |
|---|---|
| Ranked, with games | the arrange bar (arranging is ungated), no section controls |
| Collection, no sections | no arrange bar at all |
| Any list that already has sections | headers, **delete** but no rename |

...plus the **sections lockup** in every row, which is what closes the gap this table used to
describe: before it, a free owner whose list had never had a section saw no trace of the feature
anywhere, so the perk was invisible to exactly the hunter who might buy it.

### The sections lockup

One block, two states, sitting where the section controls would be:

| Their state | What it says |
|---|---|
| Never had sections | "Group this list into sections" + what members get + **See membership** |
| Membership lapsed | "Your sections are still here" + what they keep + **See membership** |

Four things about it are load-bearing:

- **It is a sibling of the arrange bar, not a row inside it.** That bar is `hidden` until the identity
  editor is opened and does not render at all for a free owner of a section-less Collection, so
  anything inside it is invisible to the exact hunter this is for. The line it replaced
  (`.gl-sections__locked`) lived there and reached nobody.
- **It is gated on `is_linked`.** An unlinked owner also fails `can_manage_sections`, and selling them
  a membership answers a question they did not ask — what stands between them and sections is linking
  a PSN account.
- **It needs `bool(items)`.** Sections group games; on an empty list this is selling a way to organise
  nothing.
- **It is STATIC.** A flag, a heading and a link — no provider, no query, nothing per-user beyond
  booleans the render already computed. CLAUDE.md's premium-preview rule exists because a locked UI
  twice ran its real data path for people who could not use it, and a test asserts the free render
  costs exactly as many queries as the member one.

The voice is additive: it says what membership **adds**, never what the hunter lacks. Lists, games,
ranking, publishing and sharing are all theirs already.

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

### One mode (rebuilt 2026-09)

There is **one** editing state and one control: **Edit list** / **Done** in the page header.

It replaced three answers to "can I change this list right now". Adding and removing games worked
always; renaming and sections needed *Edit list*; moving a game needed *Edit list* **and** a second
toggle inside the bar that revealed. The owner's report was that the tool was "awkward in displaying
what state it's in", which it was.

Entering the mode brings the identity panel, the section controls, the per-section adders, the
remove rows and the dragging on together. Leaving takes them all away.

**Two flags, one mode**, and not collapsing these was the load-bearing call:

| Flag | On `#gl-items-panel` | Means |
|---|---|---|
| `data-gl-editing` | the mode | rename, sections, add, remove |
| `data-gl-arranging` | **derived** | drag actually attached: grips, grab cursors, the tray, the lift |

`arranging` is never toggled by anybody. The mode can be on over a grid with nothing draggable: a
Collection with no sections has nothing to file, a ranked list sorted A-Z has no position a drop
could mean, and SortableJS may simply have failed to load. Keying the tray and the grab cursor off
`editing` would promise a drag the page cannot honour on exactly those three surfaces — which the old
two-mode split had been preventing by accident.

`data-gl-arranging` is the old `data-positioning` renamed, with its selector **shape** deliberately
unchanged (`#gl-items-panel[...]`, never flattened): the sortable-ghost rule depends on winning a
(1,4,0) vs (1,3,0) specificity fight that a shorter selector loses silently, and the stylesheet
records having lost it once already.

**Deliberate entry survived the collapse.** `data-gl-reorder` is still the server saying reordering
is POSSIBLE here; the mode is still the hunter saying they want it now. Conflating the two shipped
first and made rearranging a list something you could do by accident.

**Remove and rearrange now coexist on a card.** The old CSS hid the remove button whenever the
arrange mode was on, which with one mode would mean hiding it always. Deleting that rule exposed a
real bug: both controls were anchored `top: 6px; right: 6px` and had "never collided" only because
one was hidden, so on touch their 44px hit areas were fully coincident — a tap meant for *reorder*
would delete a game. They are separated by geometry instead (grip at `right: 38px`, `44px` on touch,
so the two targets abut rather than overlap).

The hint copy is the mode's, not an instruction for getting into it, and it has three states because
the capabilities are independent: nothing draggable, filing only, and full ordering. Naming a gesture
the page will refuse is worse than naming none.

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

### Getting into the editor

One entry point, `[data-gl-edit-open]`, and it opens the **whole** editing state: name, description,
sections, the per-section adders, the remove rows and the dragging. There is deliberately no second
door — they are one state, not four controls.

That sentence was aspirational until 2026-09. The control opened a panel and revealed a bar, and a
second press *inside* that bar turned on dragging, while adding and removing worked regardless of
either. See *One mode* above.

It lives in the `.gl-actions` band beside Publish, as a labelled `Edit list` button. It used to be a
44px icon-only pencil welded to the `h1`, and that was the wrong idiom twice over: a bare pencil
beside a field is the *inline field-edit* pattern, the one you meet a dozen times down a settings
form each editing the value it sits next to — so it read as "rename this heading" and went unfound,
while what it opens is the entire editor. The band is where the page already puts actions that
"act on the very thing the header describes", which is exactly what this does.

Ghost, and first: Publish keeps the only filled treatment because it is the act a new owner should
take, and editing goes first because it is the everyday one.

It carried `aria-expanded` and `aria-controls="gl-edit-panel"` until 2026-09, and **both were removed
deliberately** when it became the single mode toggle. The rule behind them is the reason they went,
not a casualty of it: a control must not state a fact about itself it will not maintain. It used to
disclose one panel, so *expanded* was honest. It now enters a mode reaching the cards, the section
headers and the adders — `aria-expanded="true"` would describe the identity panel while the hunter
watches grips appear over every card, which is the same lie told about a bigger thing. The **label**
carries the state instead (`Edit list` / `Done`), which is also why there is no `aria-pressed`: a
toggle must not carry both a pressed state and a changing label. Both attributes are actively
*removed* on paint rather than merely unset, because an out-of-band chrome swap re-renders this
button and a stale attribute from an older template would otherwise survive with nothing to clear
it.

### Per-card actions: the `...` menu

Every card carries one menu trigger opening **Move to <section>** rows plus **Remove from list**.

It replaced a bare remove button, and the corner is why. Remove sat at `right: 6px` and the grip at
the same coordinate; adding a third control for "move to" would have put ~90px of buttons across the
top of a ~170px card at 375px. One trigger instead, and the grip keeps its own place because it has
to be **grabbable** rather than chosen.

It also protects the destructive action better than the rule it replaced. The old CSS hid Remove
while dragging so an irreversible action was not under a moving cursor; hiding it meant it could not
be reached at all. A menu gets the protection and stays reachable.

**The three-way fork lives in one function.** Which endpoint a move posts to depends on what a
position means on the page: at the real sequence the order and the section go in ONE write to
`list_reorder`; everywhere else it is `list_item_assign` with no order at all. A menu that always
assigned would silently diverge a ranked list's ordering — silent because the card still lands under
the right header and the damage only shows on the next load. `moveItemToSection` is that function,
extracted out of the drag handler, and both callers go through it.

Destinations are read from the **rendered** grouping — the headers the cards already sit under —
rather than assembled client-side, because a second source for the grouping is how a menu comes to
offer a section deleted in the swap that just landed. The id is parsed from the header's own `id`
and never from its text: two sections may share a name, so a name identifies nothing.

**"No section" is appended by the client**, not read from the page, and that is load-bearing. The
loose bucket is omitted when empty, so once a hunter files their last loose card there is no header
left to drop onto. See *The ungrouped bucket* below.

### Section headers: add here, and reorder

Each header carries a labelled **+ Add game** and a `...` menu (rename / move up / move down /
delete).

"+ Add game" is the one labelled action because it is what the header exists to make easy: filing a
game used to mean adding it at the toolbar and then dragging it down, which on a long list is a drag
past everything in between. Everything else went into a menu for the same arithmetic the card solved
one element up — there were two 28px icon buttons here with a load-bearing `margin-left: 8px` keeping
their 44px hit areas apart, and five would not have fitted beside a name and a count.

**Sections can be reordered at last.** `reorder_sections`, `ReorderSectionsView` and
`list_sections_reorder` had all been live since sections shipped, with a service test and no client
anywhere — which is why a section could be renamed and deleted but never moved. Move up / Move down
post the **whole** order, because the service refuses a partial one for the same reason `reorder`
does for items, so "move up" cannot be a delta. The rendered order *is* the order (`position` is
dense and the server sorts on it), so it is read back from the page rather than kept twice.

Rename stays member-gated by being **rendered or not** — an empty `data-rename-url` — rather than by
rendering a row the service refuses, which is the remedy-that-refuses shape this project has fixed
three times.

> **Naming trap, recorded because it cost three test failures.** The add button was
> `data-gl-section-add-game`, which *contains* `data-gl-section-add`, the section-CREATION form in
> the controls strip — so three member-gate tests asserting that form is absent for a free owner
> started matching this button instead. It is `data-gl-add-to-section` now. Same hazard the
> `[data-gl-delete]` ordering comment in `list-detail.js` warns about, arriving as a substring.

### The adder is moved, never duplicated

There is **one** `GameAdder` on the page. Pressing "+ Add game" on a header relocates that node under
the header and sets `data-section` on it; pressing it again sends it home.

One instance because `GameAdder` binds a document listener and has **no teardown**, so an adder per
header on a panel that re-swaps on every write is a leak that grows for the life of the tab. Moving
the node keeps its listeners, its WeakSet guard and any in-flight search. `GameAdder` reads
`root.dataset.section` at **send** time rather than capturing it at wire time, and this is the reason:
the root's destination changes as it moves.

**It must be parked before anything replaces the panel.** The headers live inside `#gl-items-panel`
and are replaced wholesale, so a docked adder is destroyed mid-type. `refreshItems()` parks it — and
so does an `htmx:beforeSwap` hook, which is the half that is easy to miss, because the sort toolbar
submits through htmx **directly** and never passes through `refreshItems`. Sorting with the adder
docked would otherwise leave the page with no way to add a game until reload.

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
position and the empty state server-owned. (It kept the truncation line server-owned too, until the
size cap made truncation impossible and the line was deleted.)

### Cover art

A `Concept` cannot answer "what does this game look like" — two of the cover chain's four sources live
on `Game`. `covers.cover_games_for()` picks a representative `Game` per concept in **one query** for
the whole page, mirroring the concept page's platform-priority rule. Never call it per card.

---

## The Spotlight

One featured list, in a wide band between the filter toolbar and the browse grid. It is the only
place on the page where the site speaks in its own voice rather than showing what hunters made.

**Why a band and not a shelf of four.** A row of featured tiles that look like their neighbours says
*these are popular*; one band with a written line says *a person chose this*. It also survives the
authoring constraint: a Featured shelf needs four fresh blurbs forever, a Spotlight needs one.

**The blurb is the list's own `description`.** There is no separate editorial-blurb field, because
that would be a second thing to write for every pick when the first already exists.

### How it is chosen

`GameList.objects.featured()` — `public()`, plus `featured_at__isnull=False`, ordered
`-featured_at`, `.first()`. Built on `public()` rather than `visible()` so a list that is
un-published, deleted or moderated *after* being featured drops out on its own. The page cannot
promote something its own grid would refuse to show.

Set it from Django admin (`/admin/gamelists/gamelist/`), which `core/admin_site.py` narrows to
**superusers** — so featuring is the owner's lever, not the admin team's. That is right while the
picks are staff-written; if it ever wants to be a moderator action it belongs in `/staff/`, where it
would be logged. The `feature_selected` action refuses a multi-row selection (most-recent-wins would
silently pick one and leave the others looking featured) and refuses a list the band could not show.

Nothing restricts featuring to staff-owned lists, in the model or in a service guard. Curation *is*
the act of setting the field, and the people who can set it are already the people with admin
access. Encoding "staff-authored" a second time would have to be unpicked, with its tests, on the
day a hunter's list deserves the slot.

### The gate, and why it is only one

`BrowseListsView._spotlight()` returns `None` on a **partial render** — HTMX or XHR — and otherwise
fetches. The band lives *outside* `#browse-results`, so a filter swap and an InfiniteScroller page
render the grid partial and never render it, but `get_context_data` still runs for them. Without
this the query fires on every keystroke of live search to build a value that is discarded. The
condition is `HtmxListMixin.is_partial_render()`, the mixin's own test — asked rather than
re-implemented, so the two cannot drift.

**There used to be a second gate on `has_filters`, and it was wrong in a way no server test could
see.** A filtered page must not *show* the band, and returning `None` achieved that on a full
render — but live search does not do full renders. It swaps `#browse-results`, the band is outside
that target, so the band already sent simply stayed on screen above the reader's results. The gate
worked only on the path nobody takes interactively.

So the band is fetched on every full render and the **template** decides whether it starts
collapsed (`.gl-spotwrap.is-collapsed` + `inert`), with `lists-browse.js` collapsing and restoring
it as filters come and go. The grid partial carries `data-has-filters` so the client reads the
server's definition rather than re-deriving it — a second copy of "is a filter on" in JS is what
made the band vanish on the first keystroke in the first place.

That also fixed the other half: landing on `?q=soulslike` and clearing the box used to leave no
band at all, because none had ever been rendered to reveal. The cost is one indexed lookup on a
filtered *full* page load — a direct link or an Enter press, never the per-keystroke path.

**The collapse is an animation**, not `display: none`: `grid-template-rows: 1fr → 0fr`, because a
band that vanishes mid-keystroke and drops the grid up the page reads as a glitch. `inert` rides
with it — a 0px transparent band still has a focusable link otherwise, and opacity is not
hit-testing.

### Rendering

`.gl-spotlight` in `gamelists.css`. It is **not** built on one of the four signature primitives:
Frame, Pursuer Card, Horizon and Tally do not own "editorial feature", and Horizon in particular is
a *progress* meter whose own anti-patterns forbid decorative use. The editorial accent comes from
`.msc-spot` (`milestone-cards.css`) — a local `--spot-c`, a diagonal wash, a tinted border that
strengthens on hover.

**The band is a cover reel, not a bigger tile**, and that is the second version. The first paired a
four-cover mosaic with three short lines of text, which on a desktop width left most of the band as
empty gradient: it read as *highlighted* rather than *featured*. The fix came from the identity
doc's own rule for the Frame — **the art should be the loudest element** — so the space went to the
list's actual contents:

| Part | What it does |
|---|---|
| `__reel` | A row of the list's real covers that deliberately **overflows** under a mask fade. The cut-off is the effect: a strip stopping short of the edge looks unfinished, one running off it says there is more of this list. Fills the width by construction at every breakpoint, so the dead space cannot return. |
| `__eyebrow` | A bordered chip, not 9.5px of tracked-out caps. Caps are the site's *label* treatment; this is the one line on the page that is us talking. |
| `__name` | Engages Bricolage's `wdth` axis at 100. `visual-identity.md` calls that axis load-bearing ("wider widths read as headline / monumental") and nothing else on the site had used it. |

`SPOTLIGHT_COVERS` (8) is deeper than `LIST_TILE_COVERS` (4) **so that the reel overflows**, which
is why the band gets its own `attach_cover_games` call rather than riding the grid's. Those were
briefly merged, correctly, while both wanted four; merging them now would mean fetching eight covers
for all twenty-four grid lists to serve one band. The layout splits at **640px**, not the usual
`md:`, because the stacked layout wastes more width the wider it gets.

The featured list also appears in the grid below, on purpose: excluding one row mid-pagination
drifts the offsets InfiniteScroller pages on, so page two would skip or repeat a list.

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

## Where a list is discoverable

| Surface | Scope | Notes |
|---|---|---|
| `/community/lists/` | every public list | Search, game-count filters, five sorts, the Spotlight band |
| `/my-lists/` | **yours, public and private** | The management surface. The only place private lists render |
| `/hunters/<psn>/?tab=lists` | that hunter's **public** lists | Author-scoped. See below |

### The profile Lists tab (2026-09)

Added when Lists went public, and the gap it closes is the one the Spotlight creates: before it,
nothing on the site answered *"what else has this person written?"* — browse filters on text, game
count and a follow scope, never on a person — so meeting a list and clicking its author landed on a
profile that never mentioned lists.

Four decisions, each of which has a test pinning it (`tests/engine/test_profile_lists_tab.py`):

- **Public only, including on your own profile.** `/my-lists/` owns private lists and the whole
  management surface. Two places managing one thing is the failure being avoided, so this tab answers
  only "what has this hunter put out" — the same question for the owner as for a visitor.
- **The chip is conditional**, rendered only when the hunter has at least one public list (an indexed
  `.exists()`, served by `glst_owner_idx`). At launch that is almost nobody, and a chip that is empty
  for everybody is chrome on every profile render plus crowding on a switcher that must survive
  375px. The `card` chip is already conditional, on ownership, so the switcher handles it.
- **`?tab=lists` normalizes back to Games when that check fails**, exactly as `?tab=card` does for a
  visitor. Without it a hand-typed URL (or a chip gone stale because the list was unpublished between
  renders) selects a tab that has no chip, leaving the switcher with nothing marked active.
- **Not paginated, and the render still carries its own bound.** `MEMBER_MAX_LISTS` caps an account
  at 25, so the wall has no second page and the panel deliberately renders no `lists-sentinel` — which
  is how `profile_detail.html` knows not to build an `InfiniteScroller`. The view slices to that cap
  anyway, per the rule in `gamelists/models.py`: the cap is enforced by the *service*, so a row that
  arrived another way must not be able to make a public page unbounded.

It reuses `list_tile.html` and `attach_cover_games` wholesale (two queries for the wall regardless of
size), and passes no `show_privacy`, because every list on it is public by definition. It does **not**
`select_related('owner')` the way browse does: every row belongs to the profile being viewed, so the
view assigns the already-loaded owner instead of joining a wide row once per list.

Privacy rides on the page's existing `_history_visible` guard, which covers the HTMX path as well as
the full render — the bug class this page has hit before, where a check living only in
`profile_detail.html` is bypassed by a request that never renders the parent.

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

## Reporting and moderation

A hunter reports a list's **words**, not the list. `hide_list_text` sets `text_hidden`, which the
`display_name` / `display_description` readers honour everywhere; the games, likes, followers and
the owner's curation are untouched. That mirrors `hide_blurb`'s call: removing an objectionable
title is not a reason to destroy a two-hundred-game backlog.

**Hiding is reversible**, through the same Reverse button every other decision uses —
`_UNDO['list_text_hidden']` → `_undo_list_text_hidden`. It shipped without that and was a one-way
door whose only exit was a shell write that bypassed the audit log. `restore_list_text` was the
dead parallel writer and has been **deleted**: it set no `reverses` link and carried none of the
standing-decision guard, so a future caller reaching for the obvious name would have re-created
the bug below.

**Two hides can cover one list.** The second finds the words already gone, writes `changed={}`,
and is deliberately irreversible on that basis. So `_undo_list_text_hidden` also refuses when
*another* unreversed `list_text_hidden` exists for the same target — without it, reversing the
first hide put the words back over a standing decision nobody disputed, with no entry left that
could take them down again. `_restore_hidden` carries the identical guard for the blurb path.

### Hiding has to hide from the index too

Three surfaces leaked the hidden text after the templates were fixed, and each is worth knowing
because they are all the same shape — a flag honoured where somebody remembered:

| Surface | Why it leaked |
|---|---|
| Breadcrumb, `og:title`, `og:description` | built in the **view**, not the template. The crumb renders visibly two inches above a corrected `h1`, and the OG tags are what Discord and Google scrape. |
| Browse **search** | matched the raw columns. The tile said "Untitled list", but the match itself confirmed the string — reconstructable substring by substring on an anonymous page. |
| Browse **`?sort=alpha`** | ordered on the raw name, so a hidden list sat at its real alphabetical position between two visible ones and could be read off by bisection. It sorts under the placeholder now. |

A hidden list stays findable by its **owner's name**, which is not the moderated text and is how
somebody returns to a list they know exists.

### The restriction asymmetry

`report_list` checks `'reports' in active_scopes_for(profile)` — the **narrow** scope, not
`is_restricted_from(profile, 'reports')`, which resolves through `SCOPE_COVERS` and would also
refuse an `all_ugc`-restricted hunter. That is deliberate: a restriction on writing content is not
a reason to stop somebody flagging a slur, while a restriction aimed at report abuse is exactly
that. **It makes list reports the one surface on the site that accepts an `all_ugc`-restricted
reporter** — every other report path blocks them via the covering helper. Deliberate, but a
divergence; the counter-argument is that `details` is 500 characters of free text pushed at a
human, which is the thing `all_ugc` names.

## Gotchas and Pitfalls

- **Import from the right module.** Three class names exist twice, and `trophies/views/__init__.py`
  re-exports three view names the URLconf now takes from `gamelists.views`. This is not theoretical:
  a code-reading agent pointed at "the Game Lists browse page" in 2026-09 spent its entire run in
  `templates/trophies/browse_lists.html` and `trophies.GameList`, and reported confident, precise,
  wholly inapplicable findings. Anything sourced from those paths is about the dead system.
- **The Spotlight must stay outside `#browse-results`.** Inside the swap target it is torn out and
  rebuilt on every filter keystroke and every scroll page, and `_spotlight()`'s partial-render gate
  stops matching the thing it is gating. A template-order test pins it, because no assertion about
  rendered text can see this.
- **A marked byline needs its mark capped.** `.pp-markname` is an `inline-flex`; inside a
  `white-space: nowrap` container nothing bounds it, so the container's `overflow: hidden` shears off
  the overhang — and the glyph sits *after* the name, so what gets sheared is the mark itself, on
  exactly the long names that made the row truncate. `max-width: 100%` fixes it. Making the byline a
  flex container does **not**: that repairs the marked case and breaks the ~99% unmarked one, since a
  bare text node inside a flex parent becomes an anonymous flex item at min-content width and cannot
  ellipsize at all.
- **Raising the ceiling means raising BOTH constants.** `MAX_ITEMS_RENDERED` is *derived* from
  `MAX_ITEMS_PER_LIST` for that reason. Decoupling them re-creates the truncation bug family; genuine
  pagination is the other way to break the tie, and that is a project rather than a constant.
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
  Worth knowing before anyone goes looking for a better example to copy: **all six search views on
  the site share this shape**, so there is no good one to replicate. Fixing it means deciding whether
  `trophies_concept` gains an expression index on `UPPER(unified_title::text)`, with EXPLAIN output
  from production.
- **Its `already_added` check is bounded to the page of results**, the same idiom
  `api/rating_views.py` uses for its prefill rows. It read every `concept_id` on the list into a
  Python set on every keystroke — the third anti-pattern in CLAUDE.md's whale rule — on a system with
  no cap on list size, and the query cache above deliberately does not cover this half. Note that
  **query counting cannot catch a regression here**: both shapes issue exactly one query, and what
  differs is how many rows it returns, so the test asserts on the `IN (…)` bound instead.
- **Wire the drag on `htmx:afterSettle`, not `afterSwap`.** htmx copies the OLD node's attributes
  onto the new one before insertion and restores the real ones on settle, so during `afterSwap` an
  id'd swapped element reads as whatever the previous content was. Sorting a ranked list A-Z and back
  left every grip inert; the reverse wired a grid that must not be draggable *and* marked it handled
  so settle could not undo it.
- **The detail page renders at most 200 items** (`MAX_ITEMS_RENDERED`) and no longer needs to say so,
  because a list cannot hold more than that. The slice survives as a backstop against a row put there
  outside the service, not as a page boundary.
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
- **The ungrouped bucket is omitted when empty, for everybody** (changed 2026-09). It used to stay
  for an owner who could arrange, because it was the only way back *out* of a section: filing the
  last loose card removed the bucket and took the drop target with it, so nothing could be un-filed
  by pointer (no grid to drop onto) or by keyboard (no group before the first section) until the
  owner deleted a whole section to get their game back.

  The card menu ended that argument — "No section" is a row on every card, appended by the client
  precisely so it is offered when this bucket is not rendered. What was left was a permanent "Not in
  a section" header over nothing on every fully-filed list, which is what the owner reported.

  **The cost, stated rather than glossed:** a card can no longer be *dragged* out of every section,
  because there is nothing to drag it onto. The menu is the route. If a drop target is ever wanted
  back it belongs behind `[data-gl-arranging]` — on screen only while a drag is actually live — and
  not on every render of every sectioned list. `test_un_filing_survives_the_bucket_being_gone` is
  what stops that trade being broken quietly.
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
