# Review: Discovery

How a hunter finds a game and reads it: Browse Games (`/games/`), Trophy Lists (`/games/lists/`), Recently
Added, the three curation browses (Franchises, Companies, Genres & Themes) and their detail pages, a game page
(`/games/<igdb_id>/`), a trophy list page (`/games/<np_communication_id>/`), and search (the navbar dropdown and
the browse search box). Reviewed 2026-10-22 at 375 / 768 / 1440. Method: [README.md](README.md).

**Where:** the public pages on **https://platpursuit.com**, signed out (read-only GETs, plus typing into search
boxes), because dev holds 375 games and prod about 40,000: browse, sorting and search only mean something at
prod scale. The signed-in states on **dev** with the owner's saved browser session: a platinumed game (Jak II),
a game in progress (Jak 3, 45%), a game with three trophy lists, and the browse grid. Not covered: a hunter's
saved browse defaults (they redirect, so the bare `/games/` a newcomer sees was judged), the roadmap guide
pages (the sample game had none), and every filter in the drawer one by one.

**Headline:** fast and error-free (every prod page loaded in 0.8 to 3.5 seconds, no console errors anywhere),
and the navbar search is excellent. The problems are trust and the first screen. Browse Games quietly shows
PS4 and PS5 only, so the same search finds different games depending on where it was typed; the headline
numbers count trophy lists while the grid counts games; and on a phone, no browse page shows a single result
on its first screen.

## Findings

| ID | Sev | Finding | Where | Status |
|----|-----|---------|-------|--------|
| DS-01 | High | Browse Games shows **PS4 and PS5 only** by default, and nothing on the page says so: the platform chips sit inside the closed Filters drawer, and `browse-filters.js` leaves platform out of the active-filter count on purpose. So the same search gives different answers by entry point: `/games/?query=ratchet` (the navbar's link, any shared URL) finds 11 games, while typing "ratchet" into the page's own search box finds 3, because the box submits the drawer's hidden `platform=PS5&platform=PS4`. A PS3 or Vita hunter searching from the page concludes their game is not tracked | `trophies/views/game_views.py` (`get_filter_form`, `MODERN_PLATFORMS`), `static/js/browse-filters.js` | fixed: `fix/discovery-quick-fixes` (a bare hit binds no filter; a chosen platform shows as a removable chip) |
| DS-02 | High | On a phone, no browse page shows a result on its first screen. The title card (title, flavour line, "N shown", four stat tiles) and the toolbar fill the whole 667px window: the first card's top edge sits at 570 to 627px against the tab bar's 610px (measured on Games, Trophy Lists, Recently Added and a genre page; Franchises, Companies and Genres end their first screen on the toolbar). A search with no matches puts its "No games found" under the tab bar, so the screen looks unchanged. Same shape as [DL-01](daily-loop.md) | the browse templates' title cards (`templates/trophies/game_list.html` and siblings) | planned: `feature/ui/browse-pass` |
| DS-03 | High | Two detail pages scroll sideways on a phone: Franchise (`/franchises/accel-world/`, layout 399px in a 375px screen) and Company (`/companies/07th-expansion/`, 503px). A long unbroken game title ("Higurashi no Naku Koro ni Hou") stretches the `.fgroup` card past its column, and the page widens with it (the tab bar loses its right half). Measured with `innerWidth` against `clientWidth`; nothing reports past the widened edge, so an `overflow` check against `innerWidth` alone misses it | `.fgroup` (franchise and company detail) | fixed: `fix/discovery-quick-fixes` (`.fgroup-grid` tracks are `minmax(0, 1fr)`; both pages measure 375px) |
| DS-04 | Medium | Headline numbers that disagree with the page under them. Browse Games: "15,824 games shown" beside "Total games 40,125": the tiles count **trophy lists** (`Game` rows, from the heartbeat) and the grid counts **games** (PS4 and PS5 only, per DS-01); "In badge series" and "In contracts" are list counts too. Recently Added calls the same 40,125 "games and counting". Franchises: "871 franchises tracked" beside "458 shown" (the default hides single-game entries; the header counts them). Companies: "7,895 companies" beside "7,726 shown" | `core/services/site_heartbeat.py`, the browse title cards, `trophies/views/franchise_views.py` | planned: `feature/ui/browse-pass` (tiles removed; one count that matches the grid) |
| DS-05 | Medium | The default sort is Alphabetical, so the catalogue's first impression is numeral-titled shovelware: "0000", "007 First Light", "01 Deadliest Zone Catch: Boat Crab & Fishing Simulator", "0 Degrees", "0 Rally Desert Race". Trophy Lists opens on three identical "Zero Zero Zero Zero" cards. The first game a crawler or a newcomer follows from `/games/` is a game tagged Shovelware | `trophies/views/game_views.py` (default `sort`) | fixed: `fix/discovery-quick-fixes` (Browse Games opens on Trending; owner 2026-10-22) |
| DS-06 | Medium | Absence shown as content. Most browse cards spend two of their rows on dashed "No badges" and "No contract" boxes (2,095 of 40,125 lists are in a badge series), and the game and trophy list heroes do the same with "Not in a badge series" and "Not on the Job Board". On a game with neither, a quarter of the card says nothing | `templates/trophies/partials/game_list/game_cards.html`, the game page hero | planned: `feature/ui/browse-pass` (game page hero: `feature/ui/game-page-pass`) |
| DS-07 | Medium | The game page never says where the hunter stands. Signed in on Jak II (platinumed), the hero shows art, facts and "2 players"; the first sign of "100%" is the trophy list chip about 1,150px down on a phone. The trophy list page does it right ("In progress 45%", "Your profile") in its hero. On a phone and a tablet both heroes are mostly cover art: the first screen ends at the player count | `templates/trophies/game_page.html` (concept page) | planned: `feature/ui/game-page-pass` |
| DS-08 | Medium | One game, two player counts: the browse card for "0000" says 60 players and its game page says 229. The card shows the elected trophy list's own `played_count` (by design, per the card template's comment); the page counts every list | `templates/trophies/partials/game_list/game_cards.html` | planned: `feature/models/concept-player-count` (owner: total players, each counted once) |
| DS-09 | Medium | The trophy list picker on a game page can show identical cards. "0000" has six lists, all titled "Zero Zero Zero Zero", told apart only by platform and region chips; three are PSVITA with no region, so they cannot be told apart at all. On a phone the two-column picker also leaves a gap on its right edge | game page trophy list picker | planned: `feature/ui/game-page-pass` |
| DS-10 | Low | On a phone, each trophy row shows two percentages with only a logo to tell them apart ("93.3%", "71.8%"): the "PP rate" / "PSN rate" labels are `display: none` below 768px | `static/css/components/game-detail.css` (`.gd-trate__lbl`) | fixed: `fix/discovery-quick-fixes` (labels beside each rate on phones) |
| DS-11 | Low | Wrong art on franchise cards: Ace Attorney shows the Marvel vs. Capcom 3 cover (a crossover it appears in), and Accel World and A Certain Magical Index share the same Dengeki Bunko art | franchise `representative_game` | planned: `fix/franchise-cover-art` |
| DS-12 | Low | The phone's navbar search button is announced as "Search PSN profiles", but it searches games, badges, franchises and hunters | `templates/partials/navbar.html` | fixed: `fix/discovery-quick-fixes` (labelled "Search games, badges, and hunters", like the input) |
| DS-13 | Low | Repeated names that read as mistakes: the trophy list breadcrumb doubles when the list is named after its game ("Home > Games > Jak 3 > Jak 3"), and Jak II's "In the same family" shows "Jak II" with the same art, so the link looks like it points at itself | trophy list breadcrumb, game page family row | partly fixed: `fix/discovery-quick-fixes` (a list named after its game reads "PS4 trophy list"); the family row: `feature/ui/game-page-pass` |
| DS-14 | Low | "6000 XP" on browse cards, without the thousands separator Career uses ("4,200") | `templates/trophies/partials/game_list/game_cards.html` | fixed: `fix/discovery-quick-fixes` ("6,000 XP") |

## What works

- **The navbar search.** Grouped results (games, badges, franchises) with art and type chips, an offer to
  sync an unknown name, and on a phone a full-width sheet that reads well. The best surface in the slice.
- **The trophy list page.** Four useful tiles (players, platinums, 100% club, average completion), the
  "Common Platinum, earned by 68.6%" rarity line, a clear anonymous call to action ("Start tracking free"
  with a sign-in link), and the signed-in "In progress 45%" with a link to the hunter's profile.
- **Trophy rows on desktop.** Icon, grade chip, description, and the PP and PSN rates side by side with labels.
- **Speed.** Every prod page loaded in 0.8 to 3.5 seconds with no console errors, including 40,000-row browses.
- **Recently Added's freshness line** ("Freshest addition 38 minutes ago") makes the catalogue feel alive.

## Checked, not findings

- **The multi-list game page timed out once on dev** (45 seconds, phone only). Reloaded twice, it took 0.66s
  and 0.31s: a cold dev server.
- **The tab bar mid-page in full-page shots** is a screenshot artefact of fixed elements.
- **"Ratchet & Clank" twice under Franchises in the search dropdown**: one franchise and one series, chip-labelled.
- **The Lucky button matched "Filters" in a probe** (its accessible name mentions the filters it respects) and
  opened a random game. Behaviour as designed.

## Candidate lessons

Second sightings, ready to promote (see [README.md](README.md#lifecycle-of-a-finding)):

- **The first screen shows the page's content, not only its header.** DL-01 (Career's chosen view starts
  below the fold) and DS-02 (no browse result on a phone's first screen).
- **Absence is not content.** DL-02 (the unclaimed wall of zeros) and DS-06 ("No badges", "No contract").
- **Numbers on one screen must count the same thing.** DL-04 (three level numbers) and DS-04 / DS-08
  (lists counted as games; two player counts for one game).

First sightings, held:

- **A default scope must be visible where its results are.** DS-01.
- **Sideways overflow is measured against `clientWidth`, not `innerWidth`**: a widened layout viewport hides
  its own culprit. Also in memory as a known pitfall; DS-03 is its first appearance in a review.
