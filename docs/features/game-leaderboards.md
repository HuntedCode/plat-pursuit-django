# Game Leaderboards

The **Ranks** tab on game detail (`/games/<np_communication_id>/`): every hunter who owns a game, ranked
by completion, with the viewer's own standing surfaced.

**Status**: Phase 1 shipped (overall board). Phase 2 (per-trophy-group boards) and Phase 3 (time boards)
are designed but not built - see [Roadmap](#roadmap).

---

## The ranking

```
progress DESC, most_recent_trophy_date ASC (NULLS LAST), profile_id ASC
```

Completers sit at the top **ordered by when they finished**, then everyone else by how close they are. A
game's board reads as a race rather than a snapshot, and the shape falls out of the ordering rather than
being special-cased: for a fully-completed game the player's most recent trophy *is* the platinum.

| Key | Why |
|-----|-----|
| `progress DESC` | Furthest along leads. |
| `most_recent_trophy_date ASC` | Earliest finisher wins a tie. This is the whole point: at 100% the date is the entire ranking. |
| `profile_id ASC` | **Load-bearing.** Makes the order *total*. |

### Why `profile_id` is not decoration

Ties on the first two keys are the **normal** case, not an edge case - everyone at 100% shares
`progress=100`, and identical timestamps happen. Without a unique final key, Postgres may return tied
rows in a different order between calls, which means:

- keyset pagination **skips or duplicates** players across page boundaries, and
- a displayed rank **flickers** between refreshes.

Both are silent. `tests/engine/test_game_leaderboard_service.py` pages through boards made entirely of
tied rows specifically to catch this.

### Null dates

Owners with zero trophies have `most_recent_trophy_date = NULL` and sort last within their progress band.
Postgres' default for `ASC` is `NULLS LAST`, so this needs no special handling - but it does mean the
index and the query must both rely on that default (they do).

---

## Performance

Backed by `pg_game_leaderboard_idx` on `ProfileGame (game, progress DESC, most_recent_trophy_date, profile)`
(migration `0260`, built `CONCURRENTLY`).

Measured on beta (844K `ProfileGame` rows, biggest board 1,421 players):

| Operation | Before the index | After |
|-----------|------------------|-------|
| Top-20 page | 289 ms, 31,988 buffers | **0.6 ms, 23 buffers** |
| Rank lookup ~1,400 deep | n/a | **1.4 ms** |

Without the index the planner walked `profilegame_progress_idx` **backward** to satisfy the `ORDER BY`
and discarded 458,561 rows to return 20. If a plan ever shows `Incremental Sort` or a large
`Rows Removed by Filter` here, the index is not being used.

**No Redis.** The badge leaderboards live in Redis sorted sets because their score is an expensive
aggregation over `EarnedTrophy`. This board's score is two stored columns on one indexed table, so a
cache layer would add a second source of truth to accelerate something already faster than the network
hop. Re-check with `python manage.py measure_leaderboard --explain` before revisiting.

### Keyset, not offset

Pagination uses a cursor (`progress~timestamp~profile_id`), never `OFFSET`. `OFFSET n` degrades linearly
with depth and is a breaking change to swap once clients depend on the parameter shape.

> **Gotcha:** the cursor separator is `~`, not `.`. The timestamp is a float, so a dot separator splits
> it in half. This shipped broken in the first draft and was caught by a round-trip test.

---

## Endpoint

`GET /games/<np_communication_id>/leaderboard/` - **HTML**, not JSON, and public.

Response shapes from one URL (all honour the view options below):

| Query | Returns | Used by |
|-------|---------|---------|
| *(none)* | Full panel: controls, header, first page, the viewer's standing | First activation of the tab / a control change |
| `?after=<cursor>&from=<rank>` | Rows only | Infinite scroll append |
| `?around=me` | Rows only, a window centred on the viewer | "Jump to my rank" |
| `?rank=N` | Rows only, a window centred on canonical rank N | Typed rank jump |
| `?suggest=<q>` | **JSON** `{players: [{display, username, avatar, rank, progress, url}]}` | Search typeahead |

The toolbar's search field is one input for both: a bare number jumps to that rank, text runs the
`?suggest=` typeahead over the hunters on this board (scoped to the active filters, so a hidden/filtered
player never appears) and selecting a result jumps to their rank. It reuses the shared `[data-search-wrap]`
chrome (`PlatPursuit.wireSearchField`) and `debounce`, mirroring the navbar/browse search.

**The minibar** (the sticky bar that surfaces on scroll) carries the SAME search field while the Ranks tab
is active (`data-mb-only="leaderboard"`), plus a Filters button that reaches the toolbar toggles. The
search works in place -- the whole point while scrolled deep is finding a position without scrolling back
up. One `lbWireSearch(input, drop, form, panel)` drives both fields; the minibar field is wired once to the
persistent leaderboard panel element, so it reads the panel's live toggle state and jumps the board below.
No jump-to-me is duplicated here -- the directional sticky self-row already covers that.

### View options (BoardOptions)

Parsed from the query string, carried by the JS on every fetch so the view stays consistent:

| Param | Default | Effect | Cost |
|-------|---------|--------|------|
| `earners` | `1` (on) | `earners=0` includes 0%/zero-trophy owners | Free/faster - those rows sit at the index's bottom, so keeping them out just ends the scan sooner |
| `registered` | off | `registered=1` shows only profiles with a site account (`Profile.user` set) | A post-join filter, not index-served, but negligible at board scale |
| `invert` | off | `invert=1` shows the board bottom-first | Free - the same index scanned **backward** |
| `rank` / `around` | - | jump to a typed rank / to the viewer | Bounded `OFFSET`, trivial at ≤ a few thousand rows |

**Filters change the population**, so `rank_for` / `board_size` / paging all apply them - a rank is always
"position within the currently-viewed board." **Invert is display-only**: rank NUMBERS stay canonical (from
the top), so an inverted board simply counts down. The rows are numbered from a `start_rank` stepping by
+1 (forward) or -1 (inverted); the scroller's marker carries the next page's starting rank in `from`.

`from` supplies the rank the page starts at. It is **display only** - deriving it server-side would mean
an O(rank) count per page fetch, and a tampered value only shows that one viewer wrong numbers.

`?around=me` exists because a viewer ranked 900th has no row loaded; paging forward to reach them would
be absurd. The server steps back a few places from them and opens a normal keyset page there.

---

## The panel is deliberately not server-rendered

Every other panel on game detail ships in the initial HTML for SEO. This one does **not**: it is the only
panel whose cost scales with a game's popularity, and most visitors arrive from search wanting trophy
info and never open it. It is fetched on first tab activation and cached in the DOM thereafter.

`test_detail_page_offers_the_tab_but_does_not_render_the_board` asserts this, because it is exactly the
kind of thing a later "just include it" refactor would quietly undo.

---

## Gotchas and Pitfalls

- **`Game.played_count` is NOT the board size.** It counts hidden rows (`hidden_flag` / `user_hidden`),
  so the header would disagree with the list. Use `board_size()`. On beta the gap is small (2 of 1,421)
  but it is not zero.
- **The URL sits under the Cloudflare origin guard.** `/games/<x>/<y>/` is the shape
  `CloudflareOriginGuardMiddleware` bounces when a request lacks a `CF-Ray` header. Real browser fetches
  carry it (the page itself came through the proxy), so this is intended protection - but tests must send
  the header, and any future direct server-to-server consumer would be redirected.
- **The URL must be declared before `game_detail_with_profile`** in `urls.py`, or `leaderboard` is
  captured as a `psn_username`.
- **JS init order.** The lazy-load flag is declared *above* the view-switcher IIFE. That IIFE runs
  immediately and honors an initial `?view=` by calling `showView()` during setup, so a `let` declared
  after it would still be in its temporal dead zone and throw - which previously aborted the whole file.
- **Rank numbering restarts at 1** if a continuation is fetched without `from`. The scroller always sends
  it; anything else calling this endpoint must too.
- **A control change re-fetches the WHOLE panel**, not just the list - the controls, header, count, and
  the viewer's rank all depend on the active options. The JS reads the toggle `aria-pressed` states to
  rebuild the query, so the returned HTML re-renders the toggles in the state it was asked for.
- **The directional self-row is moved in the DOM.** A `position: sticky` element only pins toward the edge
  its DOM position allows, so the JS inserts the self-row *before* the list to pin it to the top and
  *after* to pin it to the bottom, depending on which way the viewer's real row lies. The move happens
  while it's hidden (the real row is crossing the viewport at that moment), so there's no flash.
- **The self-row observer watches a specific row node**, which is swapped out by a jump or a filter change,
  so it's re-mounted after every list swap (`lbMountSelf`).

---

## Roadmap

**Phase 2 - group-scoped boards.** The platinum race is not a separate feature: it is the **default
trophy group's** board. DLC boards are the other groups'. This fixes a real defect in progress-only
ranking - `progress` has a moving denominator, so when DLC lands everyone's percentage falls and the
player who platted on day one slides down a board because of content that did not exist when they
finished.

Needs a `ProfileTrophyGroup` denorm (per-group standings are not stored anywhere today). Sized on beta at
**~1.46M rows**, only 1.73x `ProfileGame`, so eager row creation is fine. Denominators come free from
`TrophyGroup.defined_trophies`. Only 1,681 of 37,398 games have DLC, so the board selector must be absent
entirely on single-group games.

**Phase 3 - time boards.** Falls out of Phase 2's `first_trophy_at` / `last_trophy_at`. Elapsed
first-to-last trophy has 92.1% coverage; PSN `play_duration` only 76.1%, so it is secondary and must
render "not tracked" rather than silently dropping a quarter of players. Time is also the most spoofable
thing we rank on (system clocks can be manipulated offline), so it needs anomaly filtering.

---

## Files

| File | Role |
|------|------|
| `trophies/services/game_leaderboard_service.py` | Ordering, keyset paging, rank, jump window |
| `trophies/views/game_leaderboard_views.py` | The three response shapes |
| `templates/trophies/partials/game_detail/_leaderboard_panel.html` | Header + list + pinned self-row |
| `templates/trophies/partials/game_detail/_leaderboard_rows.html` | One page of rows + the next-cursor marker |
| `static/js/game-detail.js` | `loadLeaderboard` / `wireLeaderboard` |
| `static/css/components/game-detail.css` | `.gd-lb*` |
| `core/management/commands/measure_leaderboard.py` | Read-only feasibility/perf probe |

## Related

- [Management Commands](../guides/management-commands.md) - `measure_leaderboard`
- [Data Model](../architecture/data-model.md)
