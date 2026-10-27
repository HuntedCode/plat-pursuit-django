# Review: Identity and social

How a hunter is seen: a profile (its tabs, its day pages), the Hunters directory, the site-wide Leaderboards,
the share cards (Plat Cards, the profile card, the recap card) and the Monthly Recap.
Reviewed 2026-10-26 at 375, 1440 and the owner's laptop (1280 x 633). Method: [README.md](README.md).

**Where:** the public pages on **https://platpursuit.com**, signed out (read-only GETs): Hunters, Leaderboards
(all three boards), the profile of the site's top hunter (xSkiLL93: 219,041 trophies, 287 badges) on every
public tab and one day page, a private profile (adaxj), and PlatPursuit's Lists and Challenges tabs. The
signed-in pages on **dev** with the owner's saved browser session: the owner's own profile on all seven tabs,
another profile, Hunters, Leaderboards, Plat Cards (with a card opened), the recap archive, and September 2026's
recap played through to its card. The profile card and the recap card were downloaded as images. Nothing was
written: quiz answers in the recap live in the page, and the only request it makes is its page-view event.

**Headline:** the share cards and the recap are the best work in this slice. The cards are clean and
handsome at the size people post them, and the recap's deck has real personality (the quizzes, Night Owl,
the streak). The problems are elsewhere. The top hunter's Badges tab freezes the browser for 37 seconds,
because the shared grid-reveal engine forces a full style recalculation once per card. The profile, Hunters
and Leaderboards pages spend a phone's first screen on headers and tiles before the first game, hunter or
row. And the slice uses "Level" for two different numbers.

## Findings

| ID | Sev | Finding | Where | Status |
|----|-----|---------|-------|--------|
| ID-01 | High | **A profile's Badges tab freezes the browser for 37 seconds** for a hunter with many badges. xSkiLL93 (287 badges): the server answers in 1.0s, but the page is not usable until 36.8s on a 1280 laptop (21s measured on a phone). A CPU profile puts 36.9s of 38.6s in `staggerReveal`'s `play()` (`static/js/utils.js`): for each card it adds a class, starts the reveal, then calls `getAnimations()`, which forces a style recalculation of the whole 736 KB document. Once per card, so the cost grows with cards times page size. Loading the same page with only that call removed (client-side, by intercepting `utils.js`): 1.8s. Every grid that reveals a large first batch pays the same cost; this page is where it is worst. | `static/js/utils.js` `staggerReveal` | fixed: `review/identity` (a batch is every write, then every read: one recalculation, not one per card; 41.6s to 1.5s on the same prod page with the fixed `utils.js` served to the browser) |
| ID-02 | Medium | **A private profile shows a dozen zeros and nothing else.** adaxj (synced a day ago, history private): "0 LV", 0 / 0 / 0 / 0 trophies, Trophies 0, Games 0, Completed 0, Avg. completion 0%, no tabs, then the footer. Nothing says the history is private, so it reads as "this hunter has nothing", which is not true. Rule 3: absence shown as content. | `profile_detail.html`, `profile_detail_header.html` | fixed: `review/identity` (no figures on a private history; the line says it is private, and tells its owner how to make it public) |
| ID-03 | Medium | **A profile spends a phone's first screen on its header.** On a 375 phone the name, a 2 x 2 grid of four tiles, the trophy-type counts and seven tab chips wrapping onto three rows fill the screen; the first game starts below it. The tiles say what the line above them already says (the four trophy-type counts sum to "Trophies"), and on desktop those counts are a column of coloured dots with no labels. The same tiles came off Discovery, Progression and Rate My Games (DS-02, PR-02, AU-05). Rule 2. | `profile_detail_header.html`, the tab bar | fixed: `review/identity` (one `.pp-factline`, zeros left out; `.pp-switch--many` drops the chips' icons below 640px, so seven tabs take two rows) |
| ID-04 | Medium | **Leaderboards shows no hunter on a phone's first screen.** Header, two tab strips whose labels wrap onto two lines ("Badge / Points", "Shovelware / Free", "Rarity / Score"), the country filter, the board title and the search: the first row starts below the fold. The "Go to rank" box sits alone on its own row above the search. Rule 2. | `overall_badge_leaderboards.html`, `leaderboard_board.html` | fixed: `review/identity` (the sub-row's labels stay on one line; search, rank box and jump share one row) |
| ID-05 | Medium | **Hunters leads with site-wide totals and an alphabetical wall.** The header carries four tiles (Hunters tracked, Trophies tracked, Platinums earned, Earned in last 24h): numbers about the site, which belong on the landing, not on a directory. On a phone they fill the first screen. Below, the default order is Alphabetical, so the page opens on "a45100", "AAmonkeAA", "aandr3ia_": no reason to look at any of them. Rule 2 and rule 6. | `profile_list.html`, `ProfilesListView` | fixed: `review/identity` (no tiles; opens on Recently Active, owner's call; the control reads the view's resolved sort) |
| ID-06 | Medium | **The recap deck's controls cover its content.** On the owner's laptop (1280 x 633) the pause button sits on the slide's last line: it covers "26 most in a day" on the calendar, "PEAK HOUR 5PM" on Night Owl, the second game's title on the platinums slide and "33% of the way there" on the badge quiz. The calendar slide is clipped at both ends ("YOUR ACTIVITY IN" cut off at the top, "Every day counts" under the fade). On a phone the round back and forward buttons sit on top of the content at mid-height: the first slide's "September", quiz answers ("Sun", "Thu", two of the rarest-trophy options), the "New games started" tile and the Night Owl line. The short-laptop sweep's family | `templates/recap/monthly_recap.html`, `recap-stage.css` | fixed: `review/identity` (a 4.25rem foot strip under every beat holds the pause glyph and, on a phone, the arrows; overflow is measured as each slide arrives; the calendar's cells shrink on a short desktop window) |
| ID-07 | Medium | **"Level" means two things.** The trophy level is "999 LV" on a profile and "LEVEL" on every Hunters card; the Pursuer level is "3 LEVEL" in the profile card's ring and "3,600 LEVEL" on the Career XP board's rows. The profile card prints both ("386 TROPHY LEVEL" and "3 LEVEL"). A hunter who sees 999 on the profile and 3,600 on the board has no way to tell they are different numbers. Rule 5 | profile header, Hunters cards, `leaderboard_row.html`, the profile card | fixed: `review/identity` (owner: name both. "Trophy Lv" on the profile and Hunters cards, "Pursuer Lv" on the disciplines ring, the profile card, the claim banner and the Career board) |
| ID-08 | Medium | **A series badge and its franchise badge look like one badge twice.** On xSkiLL93's Badges tab "Need for Speed, Ultra HD, 5 stages, Nov 10, 2025, Rare" appears twice in a row: two different badges (`need-for-speed` and `need-for-speed-fr`) with the same name, art, edition and stage count. 23 such pairs on that one tab. The badge pages are near-identical too. Nothing on the card says which is which | `badges_tab.html` cards; the badge catalogue | fixed: `review/identity` (owner: label the kind. An eyebrow over the name on profile and Collection cards; "Franchise badge" leads the badge page's eyebrow) |
| ID-09 | Low | **Numbers that disagree with their twins.** (a) Leaderboards' "Trophies" column on the Shovelware Free board counts trophies outside shovelware (xSkiLL93: 129,470), beside a profile that says 219,041 "Trophies". (b) Plat Cards' "100% clears 1" counts 100%s on games without a platinum, beside a profile that says "Completed 79". (c) Hunters says "2,851 hunters shown" above a tile saying "2,850 hunters tracked" (one live, one hourly). Rule 5 | `leaderboard_row.html`, `shareables/plat_cards.html`, `profile_list.html` | fixed: `review/identity` ("clean trophies" on the Shovelware Free board, defined in its blurb; "completions without a platinum" on Plat Cards; the Hunters tile went with ID-05) |
| ID-10 | Low | **Plat Cards opens on three stat tiles** (Platinums, 100% clears, This year). On a phone they push the first card below the fold. Same shape as ID-03 and AU-05 | `shareables/plat_cards.html` | fixed: `review/identity` (one `.pp-factline`) |
| ID-11 | Low | **Ratings show what is missing.** Your own empty Ratings tab says "This hunter hasn't scored any of their platinums", in the third person and with no way to Rate My Games. On a hunter with ratings, every card carries an empty "No thoughts yet" box the size of the scores, and the summary's fourth tile is "Quick takes 0 written". Rule 3 | `ratings_tab.html` | fixed: `review/identity` (a visitor gets no Ratings chip with nothing behind it; your own empty tab speaks to you and links Rate My Games; no empty take box; Quick takes left out at zero) |
| ID-12 | Low | **A profile's Lists tab repeats the owner and drops your progress.** Every card carries the byline of the hunter whose profile it is, and none carries the "N of M platinumed" the My Lists cards gained in AU-02 | `lists_tab.html`, `gamelists/partials/list_tile.html` | fixed: `review/identity` (`hide_owner` on the profile's wall; the reader's "N of M platinumed" via `viewer_progress`) |
| ID-13 | Low | **Your own profile puts you in Community.** Opening your profile from the avatar lands on Community / Hunters (sub-nav and breadcrumb), the same hand-off AU-08 fixed for your own lists | `ProfileDetailView` | fixed: `review/identity` (your profile and its day pages sit under My Pursuit; crumb Home / you) |
| ID-14 | Low | **Every leaderboard row links through a redirect.** Rows link to `/hunters/xSkiLL93/` (the PSN name's capitals); the profile 301s to `/hunters/xskill93/`. One extra round trip per click | `leaderboard_row.html` | fixed: `review/identity` (rows link `profile_slug`, the stored username) |
| ID-15 | Low | **The recap asks every quiz again on a rewatch, and NEW stops meaning anything.** "Watch it again" gates each of the four quizzes until answered, while the forward arrow stays lit (it shakes the slide instead). The archive marks every unopened month NEW: 27 of the owner's 28 months | `monthly-recap.js` quiz gate, `recap_index.html` | fixed: `review/identity` (owner: free on a rewatch, `quizHolds()`; NEW marks an unwatched month that finished within the last two) |
| ID-16 | Low | **A day page has no way to the next or previous day.** It is reached from a profile's Trophies tab; to see the day before, you go back to the tab and find it | `ProfileDayView` template | fixed: `review/identity` (previous / next day links, two one-row lookups in `adjacent_days`) |

## Owner decisions (2026-10-26)

- **Two levels (ID-07):** name both, "Trophy Lv" and "Pursuer Lv", wherever each shows.
- **Twin badges (ID-08):** label the kind on the card and the badge page.
- **Hunters (ID-05):** open on Recently Active.
- **Recap rewatch (ID-15):** quizzes gate a first watch only.
- **Average completion** (raised after the review): it was earned / total trophies across the library, which a few
  huge lists sink (the owner's dev account read 58% against 68% per game). It is now the mean of each game's PSN %,
  and the header carries the library-wide share as a count instead: "4,017 of 6,942 trophies". One stored field,
  so the lobby, the profile card and the Pursuer card follow; `recalculate_profile_counts` after deploy. The
  lobby's "Earn rate" (earned / total) went: beside the new figure it was a second, different percentage.
  PSNProfiles works the same way: each game's % weighted by trophy grade (PSN's own `progress`), then a plain
  mean across games.

## Checked, not findings

- **The share images are good.** The profile card and the recap card (1200 x 630) and the plat card modal render cleanly on both screens, read well at the size people post them, and offer themes and game art. Nothing to fix.
- **The recap deck's tap zones work.** A scripted click on the hidden Next zone is intercepted by the slide, but the slide's own click handler picks back, pause or forward by where you tapped (`zoneAt`), so a real tap navigates. The arrow keys work.
- **Every page is quick on the server** (0.28 to 0.6s to first byte on prod; 1.0s for the 287-badge tab) **and error-free** (no console errors on any page, signed in or out).
- **Leaderboards loads rows as you scroll** (89,000px once scrolled on the laptop): that is the board doing its job, with a find-a-hunter search and a go-to-rank box.
- **A day page** groups the day by game, with times, rarity and type, and reads well on every screen.
- **Dev's other profile reads "Trophies 0" over 8,198 trophy-type counts.** It is an unlinked dev profile whose stored totals were never filled; no prod profile showed it. Not filed.

## Candidate lessons

- **A layout read inside a per-item loop is quadratic.** ID-01 is one `getAnimations()` per card, each forcing a style recalculation of the whole page. Fine with 24 cards, 37 seconds with 287. Second sighting would promote it into motion-patterns.md's Gotchas.
- **Tiles are already a rule, not a candidate.** [design-system.md](../../reference/design-system.md) ("one line of facts") names Hunters as left for this slice; ID-03 and ID-10 are the profile and Plat Cards headers the same rule covers.
