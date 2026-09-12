# Game Lists

A hunter curates games into a named list, keeps it private or publishes it, and other hunters like
and follow it. Rebuilt 2026-09 in a new `gamelists` app; the 2019-era system it replaces still exists
under the same three class names in `trophies`.

> **Status: behind a staff-only gate.** Every surface carries `_DevelopmentGate`, and removing that
> mixin is the whole of "turn it on". See [Turning it on](#turning-it-on) — the switch is larger than
> deleting the class.

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
| `gamelists/models.py` | The four models, the queryset, the caps and field lengths |
| `gamelists/services/game_list_service.py` | **Every write.** Rules live here, nowhere else |
| `gamelists/services/covers.py` | Batched cover resolution for the tile mosaic |
| `gamelists/views.py` | Three pages, seven JSON endpoints, and the create form post |
| `templates/gamelists/` | `browse.html`, `my_lists.html`, `detail.html` + partials |
| `static/js/lists-browse.js` | Browse page motion + infinite scroll |
| `static/js/gamelists.js` | My Lists: the create dialog and the scope switcher |
| `static/js/list-detail.js` | Detail: the adder, remove, like/follow, rename, publish |
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

**Only the types that RENDER are declared.** `LIST_TYPE_CHOICES` holds these two and not the five
more that [game-list-types.md](../design/game-list-types.md) plans, because a choice a template
cannot draw means somebody picks "Tier" and gets a Collection with a different label. `choices` is
not a database constraint, so `_check_list_type` in the service is what actually keeps the column
honest — for the shell and the importer too.

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
| `…/<id>/search/` | `list_game_search` | Adder typeahead (GET) | 120/m |

**`list_reorder` is reached by Ranked lists only** (2026-09). A Collection is unordered by design,
so the drag handles render only when the server says `can_reorder`: owner, ranked, showing the
real sequence, and short enough to render whole. The endpoint had been built and left dormant
ahead of that UI and needed no changes when it arrived.

Every endpoint **that takes a list id** resolves it through `readable_by()` and answers a uniform
**404** — never 403, never a service error — so an id alone can never confirm that a list exists or
whose it is. `list_create` is the exception with nothing to resolve: it is a form post that
redirects with a Django message rather than answering JSON.

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

---

## Turning it on

Removing `_DevelopmentGate` is necessary and **not sufficient**. The full switch:

1. Delete the class and its **six** mixin references in `gamelists/views.py`.
2. **Invert `tests/engine/test_lists_hidden.py`** — it pins that anonymous and ordinary hunters are
   refused, that no hub sub-nav or footer links here, and that the sitemap excludes it.
3. **`core/sitemaps.py::GameListSitemap` is a landmine.** It imports `GameList` from
   **`trophies.models`** and reverses `list_detail`, which now resolves to the rebuilt view — old-app
   ids against a new-app route, i.e. a sitemap of 404s. It is commented out of the index in
   `plat_pursuit/urls.py`, and uncommenting it is part of turning lists on. Re-point it first.
4. Add `lists_browse` to `StaticViewSitemap`.
5. **`static/robots.txt` has no lists rules**: `/my-lists/` is personal and login-only, the search
   endpoint returns bare JSON, and the write endpoints sit under a crawlable prefix.
6. **Build the Community hub.** DECIDED 2026-09 — see
   [ia-and-subnav.md](../architecture/ia-and-subnav.md#community-decided-2026-09-not-yet-built). The hub returns
   holding Hunters, Game Lists and (later) Challenges and the Hall of Fame, on the rule that
   user-generated content is its own class regardless of intent. **The paths stay** — `/community/lists/`
   becomes correct rather than incoherent, and `/my-lists/` is unchanged.

   Concretely: a `COMMUNITY_HUB` in `core/hub_subnav.py` with prefixes `/community/` and `/hunters/`;
   the `profiles` item MOVED there out of Browse (sub-nav only, `/hunters/` is unchanged); items for
   `lists_browse` and `my_lists`; `SUBNAV_MAP` entries for `lists_browse`, `list_detail` and
   `my_lists`; the Support Us tab swapped for Community in `mobile_tabbar.html` with Support Us moving
   into the avatar dropdown; and the Support hub relabelled "Support Us".

   **This ships WITH the un-hide, not before it,** and that is a constraint rather than a preference:
   `HubSubnavItem` has `auth_required` and `membership_required` but no staff gate, so a Game Lists
   entry added while `_DevelopmentGate` is on would show every visitor a link that 302s them. A
   Community hub holding only Hunters in the meantime would be churn with no benefit.
7. Give the detail page `seo_description` (and consider `seo_title`, which feeds the og/twitter
   tags). It is the indexable, shareable page: today it sets its own `{% block title %}` but no
   `seo_description`, so the social card falls back to the site-wide generic. Browse sets
   `seo_description` and no `seo_title`, so neither page is complete here.
8. Wire the `game_list_create` / `game_list_share` `SiteEvent` types. Both are declared in
   `core/models.py`; `game_list_share` has no call site at all, and `game_list_create` has exactly
   one — `api/game_list_views.py`, which is unrouted, so the call is unreachable rather than
   missing. Do not be reassured by the grep hit.

---

## Related Docs

- [game-list-types.md](../design/game-list-types.md) — the planned type system, and the
  lists-vs-challenges line
- [data-model.md](../architecture/data-model.md) — field-level model detail
- [api-endpoints.md](../reference/api-endpoints.md)
- [ia-and-subnav.md](../architecture/ia-and-subnav.md) — where these pages live (Community hub,
  decided 2026-09, ships with the un-hide)
