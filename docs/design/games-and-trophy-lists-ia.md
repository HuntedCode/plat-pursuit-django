# Games and Trophy Lists: Vocabulary and IA

The site's word "game" has quietly meant two different things: the *work* (the IGDB-anchored
Concept) and the *trophy list* (the `Game` model row, one per `np_communication_id`). Most features
already live at the concept level (contracts, ratings, roadmaps, badges, franchise/company pages);
the browse and detail surfaces and the vocabulary are list-level. This doc makes the separation
deliberate so every page rebuilt from here inherits one decision instead of re-litigating it.

Decided 2026-08-29 across a design conversation with Jeffrey; the display question that triggered
it ("IGDB title or PSN title?") is resolved structurally rather than by choosing a winner: the
Game page header carries the concept (IGDB) title, and the list switcher entries carry the actual
list names. Which name is which is communicated by where it sits, never by a source label.

## Vocabulary

| Site term | Model | What it is |
|---|---|---|
| **Game** | `Concept` | The work. IGDB-anchored where matched; the cross-platform join point (a future Steam/Xbox list hangs off the same Concept via its IGDB id). |
| **Trophy List** | `Game` | One platform trophy list (`np_communication_id`), owning its trophy groups (base + DLC). The community calls multiples of these *stacks*; flavor text may too. |
| **Family** | `GameFamily` | Other versions of the same origin work (original / remaster / remake). A SECTION on Game detail, never a nav layer -- coverage is partial (inherit-if-none from IGDB) and a mandatory layer with holes reads as broken. |
| **Franchise** | `Franchise` | The series (TLOU + Part II). Distinct from family; conflating them in UI is the easy mistake. |

**The Django models are NOT renamed.** Same precedent as the Career reframe (models stayed
Job/Contract): the surface changes, the schema vocabulary does not. `Game` in code = Trophy List
on the page. This table is the mapping; do not "fix" it.

**Naming insurance for the multi-platform future**: display copy says "Trophy Lists" today; URL
segments and code identifiers stay NEUTRAL (`lists`, `list_*`), because copy is a template-string
change and URLs are a 301 migration. "Trophy Lists" is PlayStation-flavored and will not survive
Xbox/Steam ("achievements"); when that bridge arrives, per-platform flavoring may even be correct
(Trophy Lists on a PS tab, Achievement Lists on an Xbox tab), and bare "Lists" becomes available
if the `GameList` feature revamps under the better name "Collections". **Answered
(2026-09): it did not rename** -- the rebuild kept "Game Lists", because `/collection/`
is the finished Badge Collection page and the collision would have been worse.

## The four canonical pages

Stable structure, always the same shape -- NO conditional IA (a one-list game renders the same
page with a one-entry switcher). Emphasis adapts inside the page; existence of sections does not.

| Page | Keyed on | Role |
|---|---|---|
| Games browse | Concept | SHIPPED (phase 3): the catalogue deduped -- one card per page identity via the sitemap's election, an "N lists" chip + the partition's platform union on the card, links to the Game page. Tag detail (genre/theme) runs the same condensed pipeline. |
| Game detail | Concept | The wrapper page (anatomy below). Where users live. |
| Trophy Lists browse | Game | SHIPPED (phase 4): `/games/lists/` -- the list-level catalogue, one UN-condensed card per trophy list (observed PSN list names, region/Global chips, this list's own platforms), the browse filter family (minus the contract drill-down) with Regions first-class, alphabetical default. |
| List detail | Game | The list as a COMMUNITY OBJECT: leaderboards, earn rates/rarity (the per-Game community-stats denorm), first achievers, playtime stats, stack identity -- plus its trophy grid. |

## Game detail anatomy: the wrapper pattern

Concept-oriented tabs (ratings, roadmap, family, media -- all already concept/CTG-keyed in the
model) around ONE list viewport tab:

- **The viewport embeds the trophy grid of the selected list** and a switcher across the game's
  lists. Users see trophies without a hop; stackers compare progress without leaving the page
  (each switcher entry shows the viewer's % and plat state -- the switcher IS the
  stack-comparison view).
- **Selected list lives in the URL** (`?list=NPWR...`), htmx partial swap + `history.pushState`
  (the chips are real links whose navigation htmx swallows; stopping a navigation without pushing
  strands the Back button -- utils.js's own rule). `?view=` on the concept tabs stays
  replaceState (buttons, not links). Back button, refresh, sharing and
  deep links (a profile log's "platted the PS5 stack") all work. Param states carry
  `rel=canonical` to the bare game URL so six stack-states do not index as six pages.
- **Default list rule (deterministic, decided here)**: the viewer's own in-progress list when they
  have progress on exactly one stack; otherwise `PLATFORM_PRIORITY_ORDER`. Personalized when it
  can be, stable when anonymous.
- **A switcher only when there is something to switch** (GL-05, 2026-10-22). One list: no picker (a picker
  with one card has nothing to pick). Several: the selected chip already says which list is showing. The old
  identity chip ("Viewing list: ...") retired with that; its one useful part, the link to the list's own page
  ("List details, leaderboard & stats"), rides the control row above the trophies and follows each swap.
- **Lazy**: default list renders server-side; other lists fetch on switch.
- **The hero leads with the hunter** (rebuilt 2026-10-22 under the Page Principles, DS-07; the owner
  approved mock-ups first, in four rounds). Five parts: the cover; the head (platform UNION chips, title,
  ONE facts line: developer, year, the page's distinct players); the signed-in hunter's **status card**
  from their BEST list (a platinum on any list wins, else the highest percentage; it does not follow the
  switcher); the **platinum stat**; and the **actions**, Share (once finished, the share card), Add to list,
  Report an issue, at equal weight on the first screen. The status card and the platinum stat are shared
  partials (`game_page/_status_card.html`, `_plat_stat.html`, fed by `views/hero_context.py`): List detail's
  hero asks the same questions of one list (below).
  - **Signed out, the status slot invites** (GL-09): "N hunters here are chasing this platinum" (named only
    from 25 players; "hunting these trophies" when no list has a platinum), else "Track your platinum
    journey", with Start tracking free and Sign in; signed in without a linked PSN, Link your PSN
    (`_status_invite.html`). Without it a visitor's hero was a title, one line of facts and empty space.
  - **The platinum stat** has no box: a cut gem in the house rarity grade beside the rate and its PSN tier
    (the hunter's list, else the most-played list). The light climbs with the grade (Common flat, a halo
    from Rare, a glint from Very Rare, a sweeping gleam only on Ultra Rare). With no platinum on any list
    the same gem is drawn hollow beside the trophy count and "No platinum", so the corner always answers
    "is there a platinum?": a percentage means yes.
  - **The cover's edges are the hero's edges.** What starts the story sits level with the cover's top, what
    ends it level with the cover's bottom. On a phone and a tablet the title runs full width above the
    cover so a long name cannot push the row beside it past the art; desktop is three columns (cover |
    story | the stat top right).
  - **Badges and Jobs live in About** ("Counts toward": the series medallions, each linking its edition,
    and each job with its discipline, the contract's state on the heading). They describe the game for
    every viewer, so they are depth. The one personal part, a contract ready to claim, is the status
    card's last line, linking the Contracts board.
  Everything else the hero carried (publisher, the full release date, time to beat, the description and
  tags, screenshots, the family) moved to the About tab. The screenshot LIGHTBOX (`shot-lightbox.js` +
  `shot_lightbox.html`) opens from About's thumbs; List detail's hero no longer has screenshots, so this page
  is its only host. Released is `concept.release_date` (the work's date, not the host list's platform date).
- **Flags come back for the WHOLE game only** (owner, 2026-10-10). The hero shows a game flag (Shovelware,
  Delisted, Unobtainable, Online Trophies, Buggy Trophies) only when EVERY list in the set carries it
  (`flag_badges.every_version_keys` since 2026-10-24, the rule every whole-game badge uses; Unobtainable means no
  list is obtainable). It reuses List detail's `_game_flags.html` (fed the shared keys), Shovelware in
  the platform row as there. A flag only some lists have stays on those lists' own pages: naming it for the
  game would tell a hunter on the good list that their game is broken. For the same reason there is no "No
  Known Issues" chip here: the flags row renders only when a real flag is shared (`has_chips`), so the partial's
  clean state never shows, and an empty shared set does not mean no list has an issue.
- **Family** (the concept's different-igdb siblings: remasters, remakes, collections): a section of the
  About tab's "Versions & editions" card, each row a one-hop link to THEIR Game page with its platform
  chips (so "Jak II" on PS3 reads apart from this PS4 page, DS-13). It was a hero band capped at six until
  2026-10-22. Same-igdb concepts are the switcher's territory, never family. It is never a nav layer.
- **No "Other platforms" in About on igdb pages** (owner's call: it restates the switcher's own set):
  `about_hide_other_platforms` leaves it out there; the family stays. `/games/c/` pages KEEP both: there
  "Other platforms" is the sole surface linking untrusted same-igdb sibling concepts.

## List detail anatomy: the Game page's hero, asked of one list

The trophy list pass (2026-10-22; the review record is
[reviews/game-and-list-lower.md](reviews/game-and-list-lower.md), GL-01..GL-11, mock-ups approved in two
rounds). Before it, a hunter on a phone scrolled almost three screens to the first trophy.

- **The hero is the Game page's** (`gp-hero gp-hero--list`, the same grid and edges): the cover; the head
  ("Part of <game> - View game", this page's way back up, over the list's platform and region chips; the
  title; ONE facts line: developer, the list's own year, players opening Ranks, and "N versions" opening
  Versions & editions when it has any); the status card; the platinum stat of THIS list; the actions.
- **The status card carries the detail** (`_status_card.html` with `detail`): the bar by trophy group, its
  legend, the grade counts (earned/defined, the group headers' own `_grade_counts.html`) and **My stats**,
  which moved here from the action row (owner, round 2: it is this hunter's stats for this list). On a phone
  and a tablet the card is rows, every row the card's full width; desktop keeps one footer row. "Contract
  ready to claim" is its last line, never on another hunter's page. Signed out, the invitation, naming what
  there is to earn.
- **Actions**: Share (the share card, once the VIEWER finished this list), Add to list, Report (this list).
- **Gone from this hero**: the facts grid, the description and tags, screenshots, the badge and Jobs spine,
  the trophy grade cards, the Platinum Outlook. They are the Game page's (About), one tap up. "Not in a badge
  series" and "No screenshots" were absences shown as content (rule 3).
- **The four community numbers sit behind Ranks** (GL-03): they are this list's community, so they head the
  Ranks view, server-rendered above the board's lazy host (`#gd-lb-host`), counting up on its first showing.

## The trophy grid is ONE COMPONENT, rendered on both pages

Duplicating CODE is the sin; duplicating CONTENT across pages serving different moments is good
product -- trophies are quite literally why anyone visits. One shared partial (context contract:
list + viewer earned-state), included by Game detail's viewport AND by List detail. A "Trophy
List" page that does not show its trophies is a broken promise; anyone landing from a leaderboard
row expects the list.

The grid's group nav is ADAPTIVE (owner's call, 2026-08-30): the chip cloud stays for the
common 2-8 group case, but above that threshold (Sea of Thieves / Vampire Survivors class,
tens of packs) it becomes one compact "Jump to pack" control -- a native `<details>`
disclosure with a filter input and a scrollable row per pack (icon, name, count, viewer %). The
control itself is static on BOTH hosts: mid-scroll jumping belongs to the sticky minibar --
List detail's, and the Game page's port of it (identity icon per tab + jump-to-pack select,
same StickyReveal/sentinel/data-mb-active contract, wired in game-page.js). The Game page's bar
also carries a LIST SWITCH select beside the title: options server-rendered from
switcher_entries (the chips' own source), a pick proxied to a real chip CLICK so the swap runs
the one htmx path, and the select follows swaps made from either control.
`<details>` is deliberate: the grid is htmx-swapped on both hosts, and a native disclosure plus
document-delegated enhancements (`trophy-grid.js`, loaded by both hosts) needs zero rebinding.
Rows keep `data-gd-groupjump`, so List detail's smooth-jump delegate and minibar sync work
unchanged; without JS the rows are plain anchor jumps.

Group headers do NOT pin (2026-10-23). The pinned bar names the group under it instead, with the
hunter's percentage in it when the header shows one (`partials/trophy_grid/_minibar_group.html`, filled
by `trophy-grid.js` from the headers on each scroll, so swaps need no wiring; a group jump parks its section at the
scroll padding, just under the bar, so that line counts too), in place of the page title. With the sub-nav giving way to the bar, the pinned stack on a trophy list is the navbar plus that
one bar: 111px, down from 215px. On phones the Game page's bar has room for the list switch OR the group
name: the name wins while the trophies scroll past (owner, 2026-10-23), and the list chips sit just
above the grid. Laptop and tablet keep both.

**One control row above it, on both pages** (GL-10): the count (and on the Game page the list's own page),
"All / Unearned" (only with a hunter displayed), Sort, and "More filters" with Type, Rarity and DLC
(`trophy_grid/_controls.html`). Both pages filter with ONE function, `game_views.filter_and_sort_rows`; the
Game page's viewport accepts the same GET params. Each host owns its `<form>`: List detail swaps
`#browse-results` and pushes the URL; the Game page's form stands OUTSIDE `#gp-viewport` (so a swap never
closes the panel or steals a select's focus), carries the list in a hidden input, does not push the URL
(game-page.js owns `?list=`), and shares ONE `hx-sync` scope with the list picker. A list switch starts the
new list unfiltered and resets the row. "More filters" is the house `PlatPursuit.filterPanel`; the count and
load bar are `PlatPursuit.TrophyControls` (trophy-grid.js), shared by both pages' scripts.

**The rows** (GL-06, GL-07): "Earned Jun 8, 2026" with the exact time on hover; the green edge is the one
earned mark (no check). The two rates sit in fixed-width columns with tabular figures, so they line up row to
row, and never wrap on a phone. **The DLC packs** scroll sideways on a phone instead of stacking one per row
(GL-04). **The group headers use PSN's percentage** (GL-11, `util_modules/trophy_progress.psn_progress`: bronze
15, silver 30, gold 90 points, the platinum left out, floored), as does the hero's bar, so a list shows one
number: counting trophies put Jak 3's base game at 49% under a hero that said 45%.

SEO stays clean because the PAGES differ even though the component is shared: Game detail answers
"the game" queries; List detail is the canonical, indexable home of stack-specific intent
("<game> PS5 trophy list", "EU stack" -- real query classes). Distinct titles/meta, distinct
surrounding content.

## List display names: the helper chain

The switcher/chip name comes from a `display_list_name`-style helper -- ONE precedence chain on
the model, never reimplemented inline (the `display_image_url` pattern):

1. Freshest `trophy_titles`-source `PSNTitleObservation.title_name_raw` for the list --
   display-cleaned at render (strip marks the way `clean_game_title` does). Raw stays raw in the
   table; cleaning is a render concern.
2. Fallback: `Game.title_name` (always present; coverage of #1 grows with every sync and can be
   front-loaded via `backfill_psn_game_observations`).

Locale rule for #1 (games synced by JP and US users have two live names; "most recent" would flap
the label): when multiple names were seen within the last 30 days, prefer the Latin-script one;
otherwise most recent. Deterministic, and the CJK original remains one click away wherever an
"also known as" affordance lands.

Why not `Game.title_name` alone: it is cleaned (fine) but also OVERWRITTEN -- the IGDB CJK
promotion replaces it and locks the replacement, and merge renames rewrite it -- so it is not
reliably the list's own name, which is the whole point of the switcher label.

## Stats: per-list is atomic, concept aggregates are additive

Stacking is load-bearing for the core audience: the plat on PS4 and PS5 lists is TWO platinums,
earned on purpose. Per-list numbers (plat rarity, completion %, played_count, earn rates) remain
the source of truth; any concept-level number is an additive aggregate, never a replacement. The
failure mode to design against is a merged completion number that makes a stacker's second plat
invisible.

## Rollout

Page-by-page during the rebuild, never a big-bang rename:

1. Today's list-keyed game page is the BASIS for List detail (adopted, not rebuilt from zero).
2. Game detail + Games browse are the new builds.
3. When URLs move, 301 the old ones -- the SEO lane just stabilized; migrations are deliberate.
4. Universal search: Games (concepts) are the primary result type; lists are reachable through
   them. A list-specific query match may surface the list directly, labeled by its platforms.
5. `audit_psn_capture --names` (main branch) reports how often list names diverge from concept
   titles, classified by kind. It is a DIAGNOSTIC, not a gate -- nothing in this design branches
   on the number. Consult it mid-build if a sizing question actually comes up.

## Rollout log

- **2026-10-22 -- the trophy list pass** (GL-01..GL-11, [reviews/game-and-list-lower.md](reviews/game-and-list-lower.md)).
  List detail's hero became the Game page's (shared status card, platinum stat and invitation partials,
  `views/hero_context.py`); one control row and one filter for both pages; no picker for a one-list game and
  the identity chip retired; the community numbers moved behind Ranks; short earned dates, fixed rate columns,
  a sideways DLC strip on phones; PSN-weighted group percentages. First trophy on a phone: 1,883px to 963px
  (Golf With Your Friends PS4), 1,605px to 843px (Jak 3 PS4).
- **2026-10-23 -- the pinned chrome.** On a laptop the navbar, sub-nav, mini-bar and a sticky group header
  pinned 215px (a third of 1366x657). The sub-nav now gives way to a pinned page bar (site-wide, see
  [ia-and-subnav.md](../architecture/ia-and-subnav.md)), group headers stopped pinning, and the bar names
  the group under it with its percentage, replacing the bar's shown-trophies count. Mid-scroll: 111px.

- **2026-08-30 -- PHASE 4: Trophy Lists browse shipped** (7 commits incl. rail pins + audit response) -- the IA's LAST canonical
  page. `/games/lists/` (the neutral `lists` SEGMENT per the naming insurance; url name
  `trophy_lists`, subnav slug `trophy-lists` -- the GameList system's guards forbid bare
  `lists` in nav slugs and sitemap keys, and hold `lists_browse`/`list_detail`/`my_lists`).
  The TagDetailBaseView sibling-browse shape: plain form, NO browse_defaults dispatch (the bare
  URL must 200 -- it is static-sitemap-advertised), the shared filter -> sort pipeline WITHOUT
  the election (per-list is the point) + the destination np floor. The shared card's THIRD mode,
  `list_identity_cards`: titles/alts from the observed PSN list names (`display_list_names`,
  ONE batched query per render, mirrored into the ItemList schema) and region chips
  REINTRODUCED page-gated as `.pp-gcard__region` (+ a muted Global chip for non-regional lists)
  -- `.pp-gcard__plat--region` stays retired and the work-describing cards stay region-free
  (leak-banned in their suites). Deliberate v1 omissions: no Lucky (it hardwires to Game
  pages), no saved browse defaults, no contract drill-down. Recorded divergence (the phase-3
  interim class): search and the alpha sort operate on `title_name` while the card displays the
  observed name -- they rarely diverge; a stale-titled list can sort slightly off its label.
  Beta feedback round (2026-08-30): the header converted to the browse-family standard
  (right-aligned headline Tally + a LIST-scoped `.scard` substance grid; the games heartbeat
  values stay off the page), and the Game page's list switcher gained region chips + a region
  suffix in the minibar select -- stacks that differ by region alone rendered as
  indistinguishable twins. A follow-up consolidation moved the scard stats onto the hourly
  SITE HEARTBEAT (grid hidden until the cron warms it, like game_list) and gated every browse
  page's header furniture off the HTMX/XHR swap path.

- **2026-08-30 -- PHASE 3: Games browse condensing shipped** (6 commits + tag pages). One card
  per page identity via `game_page_canonicals()` slotted into the browse pipeline AFTER every
  filter and BEFORE the final order_by -- the composition rule is load-bearing: a `.filter()`
  chained after the window silently narrows the election POPULATION (which is also the right
  semantics: `?platform=PS3` promotes the PS3 sibling to its partition's card). The card's
  "N lists" + platform union use the DESTINATION page's trust-UNGATED membership (GamePageView's
  rule, np floor included), deliberately diverging from the trust-gated election partition so
  the chip agrees with the switcher the click lands on. Viewer progress folds partition-best
  across siblings (one query). Cards link `game_page_url`, title by `unified_title`; region
  chips retired from the shared card (owner call -- the card describes the WORK; region stays a
  filter); `seo_item_list` + Lucky follow. Recently Added stays deliberately PER-LIST with
  List-detail links (pinned). Interims recorded in the card's comments: flags overlay +
  played_count were the ELECTED row's own columns (election ~= partition max; never sum) until
  2026-10-22, when the card's player count became the page's distinct players (`players_map`, DS-08)
  so card and page agree; the flags overlay followed on 2026-10-24 (the page's lists must all agree, from the same
  membership read, `page_version_rows`);
  rating/badge/DLC maps stay elected-row/host-concept keyed; alpha sort AND text search operate
  on the elected row's title_name while the card displays unified_title (they rarely diverge; a
  stack-specific search can return a card whose visible title omits the query). Window cost at
  prod scale is a prod-deploy-checklist item; the fallback (materialized elected ids) is named,
  not built. Final-audit additions: the election population carries the destination's np floor
  (a blank-np work must never mint a card that 404s); regions GRADUATED from scope to an
  active-filter chip (the card lost its region chips, so an applied ?regions= needed a visible
  representation); the trust-split overlap is ACCEPTED and pinned -- an untrusted-match concept
  sharing a trusted igdb id renders its own c/-destination card while the trusted card's ungated
  count includes its lists (two cards because there are two live pages; self-heals on
  graduation); the tag hero's header stats (and the Genres & Themes list tiles) still count
  LISTS while the grids count page identities -- accepted, the stats describe catalogue scale.

- **2026-08-30 -- PHASE 2: the List-detail slim-down shipped** (7 commits). List detail is
  Trophies + Ranks; the concept Game page is the ONE active ratings host (quick-rate, blurb
  report, guidelines and the flag modal all ship there; `concept_tabs_readonly` retired; the
  ratings JS lives in `ratings-tab.js`). The community snapshot became a shared partial
  (`_community_snapshot.html`): List detail renders it as page CHROME above the switcher --
  a `.scard`/`.pp-tally` strip, the house stat-strip vocabulary, after the owner's design audit
  found the plain band reading as a placeholder -- while the Game page's Ratings tab keeps the
  framed band among its sibling sections (the partial's `snapshot_chrome` flag picks the look). Old `/games/<np>/?view=ratings|about`
  deep links 302 up to the Game page's same view. SEO: every list page is SELF-canonical (an
  explicit view-computed `page_canonical_url` -- never request.path, which would mint per-viewer
  canonicals on the username variant); the AggregateRating claim lives only on the Game page;
  `ListSitemap` advertises every non-shovelware list while `GameSitemap` narrows to
  concept-bearing Game pages (disjoint by construction, both under the sitemap==canonical
  invariant test). `ConceptContextMixin` stays on BOTH views: GameDetailView calls the split
  subset (`_build_badges_context`, `_build_versions_context`, images, pursuit);
  `_build_concept_context` is the Game page's entry point. The Game page also gained the
  viewer's play_duration through `_viewer_maps` (quick-rate playtime hint + the About TTB "You"
  row -- parity List detail's About had).

- **2026-08-30 -- adaptive group nav**: the trophy grid's chip cloud collapses to the
  "Jump to pack" menu above 8 groups (see the component section above). One shared partial +
  `trophy-grid.js`, so List detail and the Game page changed together. Same day, owner's
  follow-up: the menu's Game-page sticky was replaced by a PORT OF LIST DETAIL'S MINIBAR
  (one pinned-chrome idiom across both detail pages, not two).
- **2026-08-30 -- slice 1 refinement round** (owner's first browser pass + audit): page width
  matched to List detail (the narrower wrapper had no reason); the hero ADOPTED from List
  detail's concept half (anatomy bullet above); badge/contract spine became a split band in the
  hero; the family band added; jump links (`More about this game`, the players headline) made
  real anchors that preserve a non-default `?list=` and scroll past the sticky navbar; the About
  versions card gated off on igdb pages only (kept on `/games/c/` -- the audit caught that a
  blanket gate orphaned untrusted same-igdb siblings, and that the first gate suppressed the
  About empty state along with the card, leaving a blank panel). Read-only gating hardened: the
  blurb report button and the three ratings modals now honor `concept_tabs_readonly`. Follow-up
  the same day: the screenshot lightbox EXTRACTED from game-detail.js into shared
  `shot-lightbox.js` + `shot_lightbox.html` (owner asked for the viewer here, not raw image
  links); both pages load the one module.
- **2026-08-30 -- slice 1 shipped** (rebuild branch, 9 commits): `display_list_name` chain,
  `_build_earned_state` extraction (+ the per-trophy N+1 fix), the shared trophy grid
  (`templates/trophies/partials/trophy_grid/`, contract-bound, id-prefixed), `ConceptContextMixin`,
  the Game page at `/games/<igdb_id>/` + `/games/c/<concept_id>/` with the list switcher,
  canonicals both directions, `game_page_canonicals()` sitemap, nav/search wiring, the List-detail
  link-up. Interim decisions taken then, to revisit deliberately:
  - List pages rel=canonical UP to the Game page until the slim-down phase gives them distinct
    stack content (then they earn back a self-canonical). RESOLVED by phase 2 below.
  - Split-concept pages show the HOST concept's ratings only; merging sibling concepts' rating
    sets is the refinement phase's design problem.
  - The identity chip shows name+platforms; "region" has no data source on Game and is deferred.
    (The chip itself retired with the trophy list pass, 2026-10-22.)
  - The `/games/c/` fallback tail is CRAWLABLE and sitemap-advertised. (An earlier draft of this
    bullet claimed a robots.txt `/games/*/*` disallow covered it -- that rule was REMOVED in SEO
    Lane 0 on 2026-08-23 precisely because wildcards also blocked canonical pages; the final audit
    caught the stale claim.) Kept crawlable DELIBERATELY: unmatched games' List pages canonicalize
    UP to their c/ page, and a canonical target must be indexable or the consolidation is void.
    Thin-stub exposure accepted. REVISITED in phase 2: with list pages self-canonical the c/
    page is no longer any list's canonical target, but it remains the About/versions host for
    unmatched concepts and stays crawlable + sitemap-advertised.
  - Page identity decision recorded: **IGDB id, no slug** (owner's call) -- stable across renames,
    no slug backfill, and deliberately-split concepts share one page by construction.

## Gotchas and Pitfalls

- **`GameList` name collision**: the user-collections feature is called Game Lists, and since
  its 2026-09 rebuild the name exists TWICE in code (`trophies.GameList` legacy,
  `gamelists.GameList` live) -- see [game-lists.md](../features/game-lists.md). Until
  it revamps (suggested: "Collections"), never label trophy lists bare "Lists" in nav.
- **`PP_*` stub concepts** must still render a coherent Game page; the title helper chain and
  `display_image_url` already handle stubs -- keep new surfaces on those helpers.
- **Do not hand-copy the trophy grid markup** into a second template; both pages include the one
  partial. Two hand-written copies drifting was the original argument against rendering it twice
  at all. The same goes for the control row and the filter behind it (`_controls.html`,
  `filter_and_sort_rows`).
- **A GET form's htmx swap has the list in `finalRequestPath`, not `requestPath`.** For the Game page's filter
  form, `pathInfo.requestPath` is the bare `hx-get` path and the query htmx appends lives only in
  `finalRequestPath`; reading the first sent every filter back to the default list, switching the chips
  under the viewer. Caught in the browser; pinned in test_trophy_list_hero.py.
- **A filter and a list switch must share one sync scope** (`hx-sync="#gp-viewport:replace"` on the form AND
  the picker). In separate scopes both can be in flight and land in either order, leaving one list's
  trophies under the other's controls.
- **Family != franchise** in UI copy, ever.
- **Ratings/roadmaps are concept/CTG-level** -- they belong on Game detail's tabs, not List
  detail, no matter how tempting "list ratings" sounds. List detail owns what is genuinely
  per-list: leaderboards, denormed stats, achievers, identity.
- **Switcher labels must not flap**: the locale rule above exists because "most recent
  observation" alternates for dual-region games. If the rule changes, change it in the helper,
  once.
