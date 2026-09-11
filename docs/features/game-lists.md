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
| `gamelists/views.py` | Three pages + seven JSON endpoints |
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

`GameListQuerySet` gives one supported read per question, and three of the four ride a partial index
whose predicate they mirror exactly:

| Method | Question | Indexed |
|---|---|---|
| `visible()` | not soft-deleted — the floor | ✅ |
| `public()` | somebody else's list | ✅ |
| `owned_by(profile)` | your lists | ✅ |
| `readable_by(profile)` | public **or** yours — what a detail page asks | ❌ (its OR spans two columns) |

`readable_by()` should stay bounded to one list or a small page. The manager deliberately does **not**
filter in `get_queryset()`: a default manager that hides soft-deleted rows makes `objects` lie about
what is in the table, and admin, undelete and `count()` all then need a second manager.

### Caps

**3 lists free, 25 for members. No cap on list size.** That absence is deliberate — the legacy system
gave members unlimited games per list, so a ceiling would take a perk back and could make the importer
refuse a member's own data. Enforced in exactly one place, `max_lists_for`.

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
| `…/<id>/reorder/` | `list_reorder` | Set item order | 60/m |
| `…/<id>/like/` | `list_like` | Like / unlike | 60/m |
| `…/<id>/follow/` | `list_follow` | Follow / unfollow | 60/m |
| `…/<id>/add/` | `list_add_game` | Add a concept | 120/m |
| `…/<id>/items/<item>/remove/` | `list_remove_game` | Remove an entry | 120/m |
| `…/<id>/search/` | `list_game_search` | Adder typeahead (GET) | 120/m |

**`list_reorder` has no caller.** A Collection is unordered by design; the endpoint and its service
function are finished, tested work waiting for the Ranked list type. See
[game-list-types.md](../design/game-list-types.md).

Every endpoint resolves its list through `readable_by()` and answers a uniform **404** — never 403,
never a service error — so an id alone can never confirm that a list exists or whose it is.

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
- **The detail page renders at most 200 items** (`MAX_ITEMS_RENDERED`) and says so. Real pagination is
  a follow-up.

---

## Turning it on

Removing `_DevelopmentGate` is necessary and **not sufficient**. The full switch:

1. Delete the class and its seven mixin references in `gamelists/views.py`.
2. **Invert `tests/engine/test_lists_hidden.py`** — it pins that anonymous and ordinary hunters are
   refused, that no hub sub-nav or footer links here, and that the sitemap excludes it.
3. **`core/sitemaps.py::GameListSitemap` is a landmine.** It imports `GameList` from
   **`trophies.models`** and reverses `list_detail`, which now resolves to the rebuilt view — old-app
   ids against a new-app route, i.e. a sitemap of 404s. It is commented out of the index in
   `plat_pursuit/urls.py`, and uncommenting it is part of turning lists on. Re-point it first.
4. Add `lists_browse` to `StaticViewSitemap`.
5. **`static/robots.txt` has no lists rules**: `/my-lists/` is personal and login-only, the search
   endpoint returns bare JSON, and the write endpoints sit under a crawlable prefix.
6. **Decide the IA.** These paths sit under `/community/`, which was *retired* in 2026-08 and 301s to
   `/leaderboards/`. [ia-and-subnav.md](../architecture/ia-and-subnav.md) names this as the decision
   to revisit. `core/hub_subnav.py` has no entry for any of the three pages, so they currently render
   with no hub highlighted — and **nothing links to `/community/lists/` at all.**
7. Give the detail page `seo_title` / `seo_description`; it is the indexable, shareable page and
   currently inherits the site-wide generic ones while browse sets its own.
8. Wire the `game_list_create` / `game_list_share` `SiteEvent` types, which are declared and unfired.

---

## Related Docs

- [game-list-types.md](../design/game-list-types.md) — the planned type system, and the
  lists-vs-challenges line
- [data-model.md](../architecture/data-model.md) — field-level model detail
- [api-endpoints.md](../reference/api-endpoints.md)
- [ia-and-subnav.md](../architecture/ia-and-subnav.md) — where these pages live, unresolved
