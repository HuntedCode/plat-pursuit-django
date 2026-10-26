# Review: Authoring

What hunters make: Game Lists (browse, a list's page, making and editing one, My Lists) and Rate My Games.
Reviewed 2026-10-26 at 375, 1440 and the owner's laptop (1280 x 633). Method: [README.md](README.md).

**Where:** the public pages on **https://platpursuit.com**, signed out (read-only GETs): the list browse and two
lists (Plat Pursuit's Top Games By Job, 100 Games Every Trophy Hunter Should Play). The signed-in pages on
**dev** with the owner's saved browser session. Dev had no lists, so three were seeded for the review and
deleted once the fixes were checked: a public ranked list and a private backlog on the owner's account, and a public list owned
by another profile. Nothing was submitted: dialogs were opened and closed, and the add-game search was typed
into without adding.

**Roadmaps are out of this slice** (owner, 2026-10-26: not live, and may not return). A game's roadmap page
404s to the public unless a roadmap is published, and none is, so no hunter can reach one.

**Headline:** the list pages are good-looking and the editing flow is solid (inline edit, drag or
click-to-pick reordering, sections, notes, an add-game search that marks what is already on the list). The
problems are a data-quality bug in Rate My Games, and lists that never say anything about the hunter
reading them. Rate My Games files its five scores at their starting values when a hunter does not move the
sliders, so every "recommend only" rating adds a 5 / 5 / 5 / 3.0 to the community averages. And a list,
your own backlog included, shows covers and titles but never which of them you have finished.

## Findings

| ID | Sev | Finding | Where | Status |
|----|-----|---------|-------|--------|
| AU-01 | High | Rate My Games, and the quick-rate modal it shares its fields with, files the five scores at their starting values when a hunter leaves the sliders alone: difficulty 5, grindiness 5, fun 5, overall 3.0. The form's own hint says "A recommendation and your hours are all we need", so a hunter who picks "Do it" and submits is invited to, and every such rating pulls the game's community averages toward the middle. The averages are what the game page, the cards and the plat card show. Rule 5: the numbers no longer count what they say | `templates/partials/_rating_fields.html` ("the five scores all carry defaults"), `static/js/quick-rate.js`, the rating API | fixed: `review/authoring` (owner: require every slider. A fresh form starts each slider unset, readout "-"; nothing posts until all four are set; any press sets one, including on the thumb where it sits; an existing rating prefills as set) |
| AU-02 | Medium | A list never says anything about the hunter reading it. My Lists' cards, your own list's page and someone else's list show covers, titles and the author's notes, and nothing about which games you have platinumed, are playing or have not started. A backlog that cannot show progress is a shelf of box art. Rule 1: the signed-in hunter is the focal point | `templates/gamelists/partials/detail_card.html`, `list_tile.html`, `my_lists.html` | fixed: `review/authoring` (owner: a mark on each card, the browse cards' own, now one partial `partials/_hunter_mark.html`; "You: 3 of 8 platinumed" in the header; "3 of 8 platinumed" on My Lists cards; `gamelists/services/viewer_progress.py`) |
| AU-03 | Medium | The New list dialog hides its Create list button on the owner's laptop and on a phone: the button sits under the dialog's own scroll (1280 x 633: at 594-629px inside a 555px scroller; 375 x 667: at 678px, below the screen). On a 1440 x 900 desktop it fits. Same family as the short-laptop sweep's fixes | the New list dialog (`.gl-dialog`) | fixed: `review/authoring` (`.gl-dialog > form` passes the flex column through, so the body scrolls between a pinned head and foot; the Report dialog had the same shape and is fixed with it) |
| AU-04 | Medium | The list browse downloads every tile's covers at full size: 93 IGDB covers at `t_cover_big` (264 x 374), about 5.7 MB, for tiles that draw each one at about 120px wide. On a 1440 desktop the page takes about 5.4 seconds to finish loading (the server answers in 0.4). `loading="lazy"` does not help: the page is only 3,054px tall, inside Chrome's lazy-load distance. `display_image_url_small` (`t_cover_small_2x`, 180 x 256) exists for exactly this | `templates/gamelists/partials/list_tile.html`, the pick's strip in `browse.html` | fixed: `review/authoring` (`display_image_url_small` on the tiles and the pick strip) |
| AU-05 | Medium | Rate My Games opens on four stat tiles that say two things twice ("Games waiting 89", "Games total 89", "DLC waiting 30", "DLC total 30"). On a phone they fill the first screen and the game to rate starts under the pinned Skip / Submit bar; on the owner's laptop the game banner closes the first screen and Submit sits at 1,205px. Rule 2: the job (rate this game) should lead. The same tiles came off Discovery and Progression ([DS-02](discovery.md), [PR-02](progression.md)) | `templates/trophies/rate_my_games.html` header | fixed: `review/authoring` (one facts line: "89 games to rate · 30 DLC"; Skip / Submit pinned at every size) |
| AU-06 | Medium | On a phone, a list's header is most of the first screen. The title is squeezed into half the width beside "75 games / 3 likes", so a long name and description run five or six lines in a narrow column; on your own list "Edit list", the publish sentence and "Make private" then stack as three rows, and the first game starts at 563px of 610. The browse header and toolbar do the same at a smaller scale (the first list at 407px) | `templates/gamelists/detail.html`, `browse.html` headers | fixed: `review/authoring` (the tallies sit under the name on a phone; Make private moves into edit mode, so the owner's band is Edit list and Share) |
| AU-07 | Low | Your own lists say "by PlatPursuit" (you) on every My Lists card and in the header of your private backlog, which only you can see | `my_lists.html` cards, `detail.html` byline | fixed: `review/authoring` |
| AU-08 | Low | Opening one of your lists from My Lists changes section under you: My Lists sits under My Pursuit, and the list's page switches the sub-nav to Community and its breadcrumb to Home / Game Lists | `detail.html` sub-nav and breadcrumb | fixed: `review/authoring` (`hub_subnav_override`, as the challenge run page does; crumb Home / My Lists) |
| AU-09 | Low | Edit mode has two ways out: the form's Cancel and the toggle's Done, a few rows apart. Delete list sits beside Save, squeezed onto two lines on a laptop | `detail.html` edit form | fixed: `review/authoring` (no Cancel; Done and Escape drop unsaved changes; Delete stays on one line) |
| AU-10 | Low | A published list has no Share or Copy link. The owner reads "Published. Anyone with the link can read it." with no way to get the link but the address bar; a visitor gets Like, Follow and Report. The Discovery decision was Share beside the other actions, on the first screen | `detail.html` header actions | fixed: `review/authoring` (Share for owner, reader and visitor on a published list: the system share sheet on a phone, else the link is copied) |
| AU-11 | Low | The games toolbar is labelled "Games" with a monitor icon, which names nothing the page does not already say; on your own list the add-game search is the real lead of that row | `detail.html` toolbar | fixed: `review/authoring` (a reader's bar is the sort alone, without the card) |
| AU-12 | Low | The list browse shows Our Pick, then the same list again as the third card of the grid. Kept on purpose (removing one row mid-pagination shifts InfiniteScroller's offsets, `browse.html`), but it reads as a repeat | `templates/gamelists/browse.html` | fixed: `review/authoring` (owner: left out of the grid, in the QUERY on every page, so InfiniteScroller's offsets stay true; "N lists shown" counts the pick) |

## Owner decisions (2026-10-26)

- **Ratings (AU-01):** require every slider before a rating posts (over making the scores optional).
- **Your progress (AU-02):** a mark on each card plus a header line, and on the My Lists cards.
- **Our Pick (AU-12):** left out of the grid while the band shows.
- **Roadmaps:** out of this slice; not live.

## Checked, not findings

- **Every page is fast on the server** (0.26 to 0.55 seconds to first byte on prod) **and error-free** (no console errors on any list page or Rate My Games, signed in or out). The only slow load is AU-04's images.
- **The add-game search** is quick and clear: results show a cover and name, and a game already on the list reads "On this list" instead of Add.
- **Edit mode's reordering** is labelled ("Drag a card, or click one to pick it up. Moves save as you make them.") and every card has a reorder handle and an actions menu with an accessible name.
- **Delete list asks first** (the browser's own `confirm()`, a documented choice in `detail.html`).
- **`/community/lists/create/`** answers a GET with 405: it is a POST endpoint behind the New list dialog, never linked.
- **Rate My Games' Shovelware chip** explains itself in its tooltip ("Asset-flip platinums are left out of the queue by default").
