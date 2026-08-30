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
if the hidden `GameList` feature revamps under the better name "Collections".

## The four canonical pages

Stable structure, always the same shape -- NO conditional IA (a one-list game renders the same
page with a one-entry switcher). Emphasis adapts inside the page; existence of sections does not.

| Page | Keyed on | Role |
|---|---|---|
| Games browse | Concept | SHIPPED (phase 3): the catalogue deduped -- one card per page identity via the sitemap's election, an "N lists" chip + the partition's platform union on the card, links to the Game page. Tag detail (genre/theme) runs the same condensed pipeline. |
| Game detail | Concept | The wrapper page (anatomy below). Where users live. |
| Trophy Lists browse | Game | SHIPPED (phase 4): `/games/lists/` -- the list-level catalogue, one UN-condensed card per trophy list (observed PSN list names, region/Global chips, this list's own platforms), the full browse filter family with Regions first-class, alphabetical default. |
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
- **Identity chip** on the viewport: list name + platform badges + region, with the link to the
  List detail page living IN the chip -- identity and escape hatch are one object.
- **Lazy**: default list renders server-side; other lists fetch on switch.
- **The hero is List detail's hero, concept-half only** (owner's call, first browser pass):
  same `gd-hero` anatomy -- cover / facts / IGDB teaser / screenshots -- minus every per-list
  piece (progress readout, My Stats, plat-card CTA, Outlook, flags) and minus the modal-bound
  buttons, whose JS lives only on List detail. One exception: the screenshot LIGHTBOX came along
  (owner's call) -- extracted into `shot-lightbox.js` + the `shot_lightbox.html` partial, one
  implementation driven by both pages, so the hero thumbs open the full FLIP/carousel viewer
  here too instead of raw image tabs. Platform chips show the UNION across the list
  set; the players headline reads the aggregated community stats; Released is
  `concept.release_date` (the work's date, not the host list's platform date). The badge/contract
  spine sits IN the hero as one split band (badges left, contract right; each badge medallion
  links its own series page), using the vertical room the dropped progress readout freed.
- **Family band** (owner's call, same pass): below the spine band, the concept's different-igdb
  siblings -- remasters, remakes, collections -- as one-hop links to THEIR Game pages, capped at
  six with a "+N more" tally. Same-igdb concepts are the switcher's territory, never family.
  Wording rule: family is a hero BAND on Game detail and a SECTION on List detail; it is never a
  nav layer.
- **No versions card in About here** (owner's call: "doesn't make sense for the page as a
  whole") -- "Other platforms" restates the switcher's own set and "In the same family" is the
  hero band, so `about_hide_versions` gates the card off on igdb pages ONLY. `/games/c/` pages
  KEEP it: there the card is the sole surface linking untrusted same-igdb sibling concepts.

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
   them. A list-specific query match may surface the list directly, labeled by its identity chip.
5. `audit_psn_capture --names` (main branch) reports how often list names diverge from concept
   titles, classified by kind. It is a DIAGNOSTIC, not a gate -- nothing in this design branches
   on the number. Consult it mid-build if a sizing question actually comes up.

## Rollout log

- **2026-08-30 -- PHASE 4: Trophy Lists browse shipped** (5 commits) -- the IA's LAST canonical
  page. `/games/lists/` (the neutral `lists` SEGMENT per the naming insurance; url name
  `trophy_lists`, subnav slug `trophy-lists` -- the parked GameList system's guards forbid bare
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
  played_count are the ELECTED row's own columns (election ~= partition max; never sum);
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

- **`GameList` name collision**: the hidden user-collections feature is called Game Lists. Until
  it revamps (suggested: "Collections"), never label trophy lists bare "Lists" in nav.
- **`PP_*` stub concepts** must still render a coherent Game page; the title helper chain and
  `display_image_url` already handle stubs -- keep new surfaces on those helpers.
- **Do not hand-copy the trophy grid markup** into a second template; both pages include the one
  partial. Two hand-written copies drifting was the original argument against rendering it twice
  at all.
- **Family != franchise** in UI copy, ever.
- **Ratings/roadmaps are concept/CTG-level** -- they belong on Game detail's tabs, not List
  detail, no matter how tempting "list ratings" sounds. List detail owns what is genuinely
  per-list: leaderboards, denormed stats, achievers, identity.
- **Switcher labels must not flap**: the locale rule above exists because "most recent
  observation" alternates for dual-region games. If the rule changes, change it in the helper,
  once.
