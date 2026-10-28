# Navigation & Site Organization

> **Current as of 2026-10-27** (rewritten by the [IA review](../design/reviews/ia.md); the doc still described
> four hubs, a notification bell, the hotbar and a hand-kept footer). Which hub a page belongs to, and why, is
> in [IA and Sub-Navigation](../architecture/ia-and-subnav.md). This doc covers the chrome that carries it.

Five hubs (My Pursuit, Browse, Leaderboards, Community, Support Us), each with a sub-nav rail of its pages.
**Menus expose the few, hubs expose the many**: the navbar holds five direct links and no dropdowns, and each
hub's rail does the "I know what I want, take me there" work. Pages also link outward to related features
(a game page to its franchises and companies, a badge to its titles), so a page is never a dead end.

## File Map

| File | Purpose |
|------|---------|
| `templates/partials/navbar.html` | Wordmark, the hub buttons (`lg` and up), site search, the Premium pill, the avatar menu |
| `templates/partials/mobile_tabbar.html` | The hub buttons below `lg`, pinned to the bottom |
| `templates/partials/hub_subnav.html` | The hub's rail, pinned below the navbar |
| `templates/partials/footer.html` | The sitemap footer, built from the hub config (`{% footer_hubs %}`) |
| `templates/partials/breadcrumb.html` | The trail; views pass `breadcrumb` as a list of `{text, url}` |
| `static/js/navbar-search.js`, `trophies/views/sync_views.py` (`SiteSuggestView`) | Site search |
| `static/js/navsync.js` | The avatar's sync ring and the sync panel |
| `static/js/subnav.js` | The rail's More overflow and the phone sheet |

## Navbar

| Element | Shows | Behaviour |
|---------|-------|-----------|
| **Wordmark** | always | Opens the lobby (`/`), the only nav route to it; takes the active state there |
| **My Pursuit** | signed in, `lg` and up | Opens Career. Carries the attention markers: claimable contracts (a count), unclaimed challenge XP ("XP"), new contracts ("New"), one sentence in its `aria-label` |
| **Browse, Leaderboards, Community, Support Us** | `lg` and up | Open Games, the boards, Hunters and the storefront |
| **Search** | a bar from `md`, an icon below | See Site search |
| **Premium** | signed in, PSN linked, not a member | Opens the storefront. Waits for a linked PSN: a fresh account's one job is linking (FV-11) |
| **Avatar** | always | The account menu; the ring is the sync state, and a corner pill shows the moderation queue or "New" for What's New |

**Avatar menu, signed in:** the sync panel (name and status, last synced, Sync Now, trophy counts), then
Profile (or **Link PSN** before a PSN is linked), Membership (members), Support Us, Settings, What's New, Mod
Center (moderators, with its queue count), Sign out. Staff pages are bookmark-reached; the Mod Center is the one
exception because a queue has something to say without being visited. What's New is here because its modal is
the only other route to it, and a dismissed modal leaves no markup behind.

**Signed out:** Sign in, Sign up, Support Us. Support Us is in this menu at every width because it is not a
phone tab.

## Mobile tab bar

Below `lg` the navbar's hub buttons hide and the tab bar takes over: My Pursuit (signed in), Browse,
Leaderboards, Community, and Support Us from `md`. Five do not fit at 375px (each tab drops from ~93px to ~75px
and "Leaderboards" alone runs ~70px), so Support Us leaves below `md` for the avatar menu, demoted by frequency.
The count runs 3 / 4 / 4 / 5 across signed out below `md`, signed out from `md`, signed in below `md` and signed
in from `md`. The My Pursuit tab carries the same markers as the navbar button, one at a time.

## Hub rail

- **`lg` and up:** the hub's name, then its items in groups under quiet labels; items that do not fit fold into a
  More menu, never a scrollbar. The current item is tinted and `aria-current`.
- **Below `lg`:** one bar ("Browse · Games") that opens a grouped sheet over the page. The current page's
  attention marks ride the bar, so a phone can see which page owes something.
- **Pinned** below the navbar on every hub page; it slides away while a page's own mini-bar is pinned (see
  [IA and Sub-Navigation](../architecture/ia-and-subnav.md#gotchas-and-pitfalls)).
- Pages outside every hub render no rail.

## Footer

One column per hub in nav order, listing the rail items this viewer would see, built by
`core.hub_subnav.footer_columns` from the same config as the rail, so it cannot drift from it (review IA-01: the
hand-kept footer had lost Jobs, Challenges, My Lists and My Challenges, filed Badges under My Pursuit and offered
signed-out visitors a "Contracts" link that only signed them in). Leaderboards, which has no rail items, is one
link. Signed out, My Pursuit lists Milestones (its public page) and Sign in / Sign up.

Then **Platinum Pursuit** (About, What's New, How badges work, Contact, Privacy, Terms) and **Connect** (X,
YouTube, Discord). The grid is `grid-cols-2 md:grid-cols-3 lg:grid-cols-6`: seven columns, so Connect wraps to a
second row at `lg`.

## Site search

One box, two jobs, on `#navbar-sync-form`:

1. **Typeahead** (`site_suggest`, debounced): Games, Badges, Jobs, Franchises, Game Lists and Hunters, five of
   each, grouped, every row a link. The response is identical for every viewer and cached 60s, so it can only
   ever hold public things: Game Lists are `GameList.objects.public()` minus any whose words a moderator hid.
   Jobs and Game Lists joined on 2026-10-27 (review IA-02). Challenge runs are not searched (most share a title;
   searching the hunter finds them), nor Companies or Genres.
2. **Add and sync**: a PSN name nobody tracks yet can be synced from the same box ("Sync NAME, a new hunter"), or
   refreshed when it is tracked.

`/search/` is a separate, older redirect endpoint kept only as the homepage `SearchAction` schema's target.

## Breadcrumbs

A trail is the page's parents through its hub's rail item: **Home / rail item / page**. Browse: "Home / Jobs /
Slayer". My Pursuit: "Home / Titles", "Home / My Challenges / Optimizer". Hubs themselves never appear in a trail
(review IA-03 removed the "My Pursuit" crumbs, which led to the lobby on one page and Career on another). Two
deliberate exceptions: My Lists trails "Home / Game Lists / My Lists", its only route to everyone's lists (review
AU); and a page whose hub depends on the viewer gives each viewer the trail of their hub. Pages outside every hub
carry no trail. The trail is also the page's `BreadcrumbList` schema (`seo_tags`).

What shows is less than what is built: the visible trail is hidden on phones, and wider screens show it only
with more than two crumbs, since "Home / page" says what the page header does (owner, 2026-10-28; the rule lives in
`partials/breadcrumb.html`, detail in design-system.md). Views still pass the full trail: the schema reads it.

## Profile Page Tabs

The profile page has **4 tabs for everyone**, plus three that appear only when there is something behind
them, switchable via the `?tab=` URL parameter:

| Tab | Paginated | Infinite scroll | Filters |
|-----|-----------|-----------------|---------|
| Games | Yes (50/page) | Yes | Full browse toolbar: live search, sort, and an advanced drawer (platform, the three platinum axes, genres, themes, flags, completion + time-to-beat ranges) |
| Trophies | Yes (30 days/page) | Yes | Search + tier chips. TWO shapes behind one tab: the day wall while browsing, trophy cards while searching |
| Badges | No | No | Sort only |
| Ratings | Yes | Yes | Sort, plus a Games/DLC switcher |
| Lists *(only with a published list)* | No (capped at 25) | No | None |
| Challenges *(only with a visible run)* | No (bounded) | No | None. Runs in progress, then finished, as the Challenges page's run cards without the hunter line |
| Card *(owner only)* | No | No | None |

A conditional tab's chip is drawn only when its check passes, and a hand-typed `?tab=` for it otherwise lands
on Games, so no switcher ever renders with nothing selected. The URL is corrected to match (`?tab=games`):
the page script reads the tab from the address bar, so a stale `?tab=` would wire the Games grid with another
tab's card selectors. A full page load rewrites it with `history.replaceState`; an HTMX chip click (a chip that
went stale since the page drew it) gets an `HX-Push-Url` header from `ProfileDetailView.render_to_response`.
Hidden challenge runs never appear, on your own profile included (hiding means "off my profile").

**Lists** (2026-09) and **Challenges** (2026-10) came back as new tabs built on the rebuilt systems, not as
the legacy ones restored; see [game-lists.md](game-lists.md#the-profile-lists-tab-2026-09) and
[challenge-systems.md](challenge-systems.md#the-profile-tab). **Reviews** stays retired: text reviews were
archived in 2026-05, and the ratings that survived them are what the Ratings tab shows.

There is deliberately **no About tab**: see the Hunter Profile row in the playbook for why the trophy
timeline and the showcases it was going to pair both came off the page instead.

Tab handlers in `ProfileDetailView`:
- `_build_games_tab_context()`, `_build_trophies_tab_context()`, `_build_badges_tab_context()`,
  `_build_ratings_tab_context()`, `_build_card_tab_context()` (owner-only)
- `_TAB_TEMPLATES` maps a tab to its panel; `_RESULTS_TEMPLATES` to the partial an HTMX filter swap
  returns; `_INFINITE_SCROLL_TEMPLATES` / `_INFINITE_SCROLL_VIEWS` to the bare items a scroll append
  returns. A tab in the template map with no branch in the dispatcher renders someone else's data, which
  is what the archived Reviews tab did. Pinned by `test_every_mapped_tab_template_can_actually_be_built`.
- Every paginated tab sets `scroll_per_page`, because the scroller measures the grid against it and a
  disagreement silently disables the scroll rather than degrading.
A profile lights **Community / Hunters** for a visitor and **My Pursuit** (no item lit) for its owner, set by the
view; the avatar menu is the one route to your own (there is no Profile rail item).

## Key Flows

### Infinite scroll (Games, Trophies, Ratings)

1. `PlatPursuit.InfiniteScroller.create()` initialises against `<tab>-grid`, `<tab>-sentinel`,
   `<tab>-loading`, told the server's real page size via `scroll_per_page`
2. The observer fires when the sentinel enters the viewport
3. AJAX GET to the same URL with an incremented `?page=`, carrying `X-Requested-With: XMLHttpRequest`,
   which is how `_is_scroll_append()` tells an append from an HTMX filter swap (htmx does not send it)
4. The view returns the bare items partial for that tab (`_INFINITE_SCROLL_TEMPLATES`, or
   `_INFINITE_SCROLL_VIEWS` for the Trophies tab, whose two shapes append different things)
5. New cards are appended to the grid and handed to the reveal, so they land as they scroll in

Two things fail SILENTLY here rather than loudly, and both have bitten: a `cardSelector` that matches
nothing in the appended HTML reads as "no more pages" and stops the scroll (which is how the Games tab
lost its scroll when its card was rebuilt onto `.pp-gcard`), and a `paginateBy` that disagrees with the
server's page size breaks the resume arithmetic. Pinned by
`test_every_paginated_tab_tells_the_scroller_its_real_page_size` and
`test_every_grid_gets_the_selector_its_own_cards_use`.

## Gotchas and Pitfalls

- **The navbar and the tab bar name the hubs by hand.** A hub added or renamed means editing both. The rail and
  the footer read `HUB_SUBNAV_CONFIG` and follow on their own.
- **Search results are cached for every viewer alike**, so nothing private or moderated may enter them. A new
  group reads only public rows. The cache also lags a change by up to 60s: a list made private can still be
  offered (its page then 404s), and one a moderator hides can still show its old name for that minute.
- **Pinned chrome has a budget.** Navbar (~58px) plus rail (~47px) at the top, the tab bar (~56px) at the bottom
  on phones. New sticky chrome (a banner, a status strip) means taking room from something else; a page's own
  mini-bar trades places with the rail rather than stacking under it.
- **Profile tab handlers**: adding a tab means its chip and panel in `profile_detail.html`, a builder in
  `ProfileDetailView`, its entry in `_TAB_TEMPLATES` (and the results and scroll maps if it filters or pages), and
  the conditional check if it only shows with something behind it.
- **Cross-links should be contextual.** Each one should make sense where it sits, not be there for discovery's
  sake.

## Related Docs

- [IA and Sub-Navigation](../architecture/ia-and-subnav.md): the hubs, how a page finds its hub, the config
- [What's New](whats-new.md) and [Moderation Center](moderation-center.md): the avatar menu's two exceptions
- [Game Lists](game-lists.md) and [Challenge Systems](challenge-systems.md): the profile's Lists and Challenges tabs
- [Template Architecture](../reference/template-architecture.md): `base.html` and the context processors
