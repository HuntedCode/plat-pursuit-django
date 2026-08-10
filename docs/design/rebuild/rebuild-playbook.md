# Site Rebuild — Playbook & Progress

> **The single "start here" for any page rebuild.** Two jobs: (1) track which pages are done, and
> (2) capture the shared decisions every rebuilt page inherits, so we stop re-deciding them per page.
>
> This doc **indexes** the authoritative docs (it does not duplicate them). When a shared decision
> changes, update the "Shared Elements" section here **and** the authoritative doc it points to.

Related: **[career-reference-standard.md](career-reference-standard.md)** (the quality bar / "what done
means"), **[../../reference/design-system.md](../../reference/design-system.md)** (tokens, patterns,
component blueprints), **[../visual-identity.md](../visual-identity.md)** (the constitution),
**[../../reference/motion-patterns.md](../../reference/motion-patterns.md)** (motion),
**[../../reference/js-utilities.md](../../reference/js-utilities.md)** (`PlatPursuit.*` JS helpers),
**[chrome-audit.md](chrome-audit.md)** (nav/tabbar/footer), **[ia-map.md](ia-map.md)** (IA),
**[system-inventory.md](system-inventory.md)** (engine/system map).

---

## How to use this

**Before rebuilding a page:** read the [Shared Elements](#shared-elements-every-rebuilt-page-inherits-these)
checklist below and the [Career reference standard](career-reference-standard.md). Reuse the tokens and
patterns; do not re-derive them.

**After finishing (or advancing) a page:** update its row in [Page Status](#page-status).

---

## Shared Primitives Index

**One lookup for every reusable primitive — reach for these, don't re-roll them.** Each row points to its
authoritative doc for the full spec. The four docs by role: **this playbook** (page status + the
[Shared Elements](#shared-elements-every-rebuilt-page-inherits-these) rules + tokens) ·
**[design-system.md](../../reference/design-system.md)** (component blueprints: markup + tokens) ·
**[motion-patterns.md](../../reference/motion-patterns.md)** (motion recipes) ·
**[js-utilities.md](../../reference/js-utilities.md)** (`window.PlatPursuit.*` JS helpers).

### Components (CSS)

| Primitive | What | Code | Full spec |
|---|---|---|---|
| `.pp-switch` / `__chip` / `__lbl` | Segmented view/tab switcher — the ONE treatment | `components/switcher.css` | design-system (Tab Group) |
| `.pgl` (+ `.pgl--static`) | Progression ladder (tier/rank stepper); `--pgl-accent` / per-rung `--rung-c` | `components/elements.css` | design-system (Progression ladder) |
| Medallion `.pp-med` | The badge object (size via `--sz`) | `components/badge_medallion.html` | playbook (Shared components) |
| Horizon `.pp-horizon` (+ `--segmented`) | Progress bar / discrete meter | `components/horizon.css` | playbook (Shared components) |
| Tally `.pp-tally` | Display numbers (pair with `countUp`) | `components/tally.css` | playbook (Shared components) |
| `.pp-jobchip` (+ `__icon` / `__name` / `__xp`) | Job chip — disc-tinted pill (icon + name + optional XP); the "jobs this X levels" cross-link | `components/elements.css` + `partials/jobs/_job_chip.html` | playbook (Shared components) |
| `.pp-rarity` / `-surface` / `-gem` | Rarity: the grade label, the material, the gem — all off one `data-rarity` | `components/rarity.css` + `components/rarity_grade.html` | [rarity](../../reference/rarity.md) |
| Accented header card | The page-header card shell | (Tailwind classes) | playbook §2 |
| `.pp-toolbar-card` | Filter/search toolbar | — | design-system (Toolbar) |
| Stat tiles `.scard` / `.pp-bdetail__stat` | Headline / dense stat cells | — | playbook (Shared components) |

### Motion (CSS — all reduced-motion gated, live in `components/motion.css`)

| Primitive | What | Recipe |
|---|---|---|
| `.pp-head-cascade` | Header content opening-beat cascade | motion-patterns (Header content cascade) |
| `.pp-view-in-right` / `-left` | Directional view-switch slide (shared axis) | motion-patterns (Directional view switch) |
| `.pp-tab-ignite` | Active-chip glow bloom | motion-patterns (Tab ignite) |
| `.pp-draw-in` / `ppDrawIn` | Draw an SVG stroke in (needs `pathLength="1"`) | motion-patterns (Draw an SVG stroke in) |

Staggered grid reveal is the JS helper `staggerReveal` below (each grid supplies its own `.pp-reveal` hide CSS + per-card animation). → motion-patterns (Staggered grid reveal).

### JS helpers (`window.PlatPursuit.*`, in `static/js/utils.js`)

| Helper | What |
|---|---|
| `slideViewIn(panel, from, to, order)` | Directional panel slide on a view switch |
| `wireTablist(tabs, {onSelect, manual, …})` | WAI-ARIA tablist: roving tabindex + Arrow/Home/End |
| `igniteTab(chip)` | Fire the `.pp-tab-ignite` bloom |
| `syncViewParam(view, {default, params})` | Reflect the active view in `?view=` |
| `staggerReveal({grid, cardSelector, reveal})` | WAAPI grid-reveal engine (batch + observer) |
| `dismissableSheet(dialog, {onClose, scrim})` | Swipe-down-to-close for a modal on touch (+ grabber handle) |
| also: `countUp`, `InfiniteScroller`, `StickyReveal`, `debounce`, `ToastManager`, `API`, … | see js-utilities.md for the full set |

### Design decisions on record (don't re-litigate)

- **Segmented switcher = ONE component** (`.pp-switch`, unified 2026-07 from three class systems). Career's `.jlayout__btn` (pill) + the Case's `.pp-case__set-tab` (ring filters) are deliberately distinct.
- **Reveal-stagger is THREE tools, not one** — `staggerReveal` (WAAPI, for HTMX/infinite grids) · a CSS `nth-child` stagger (bounded client grids that replay) · bespoke per-card choreography (Career). Don't force-merge them. → motion-patterns.
- **The badge stage spine deliberately doesn't encode linear progress** (stages complete in any order); the "living" tint is per-stage, order-safe.
- **`.pgl` stays in `elements.css`** (coupled to the claim ceremony) though it's a shared primitive — a relocation is needless regression risk.

---

## This is a REBUILD, not a reskin — the from-scratch rule

When a page is rebuilt "from scratch," the old implementation is a **data/behavior contract ONLY** -- it
tells you *which* data exists and *what the page must do*, nothing more. Every visual and UX decision
(palette, emphasis, density, layout, motion, curation) is **re-derived from the rebuild system**, starting
from a blank canvas. Don't open the old file for design cues.

- **Legitimate carryover:** the data contract (which fields/stats exist + what they mean), the behaviours
  the page needs, and the **shared rebuild tokens + components** -- that IS the rebuild (see
  [Approved Building Blocks](#approved-building-blocks)).
- **NOT carryover:** the old page's bespoke decisions -- its colour-coding, thresholds, gradients, one-off
  classes, and its "show every stat" density. Re-expressing those in new class names is a **reskin, not a
  rebuild**.
- **"Everything's a token" is NOT the bar.** You can use only approved tokens and still fail the rebuild, by
  *applying* them like the old page did (e.g. colouring every number). The bar is: each choice is justified
  against how Career/Collection *actually look* -- not against the old file.

> **Litmus test:** if your only reason for a colour / spacing / emphasis / density choice is "the old page
> did it," and you can't point to Career, Collection, or the design system for it, it isn't a from-scratch
> decision. (This section exists because a badge-detail header shipped with ~90% of its palette ported from
> the old design -- tokenised, but still a reskin.)

---

## Page Status

**Legend:** ✅ Done to the Career standard · 🟡 Partial (structurally aligned, full pass pending) ·
⛔ Not started · 🗑️ Sunsetting/legacy.

**Sixteen pages are finished to the standard: Career, Collection, Badges, Badge Detail, Milestones, Titles, Plat Cards, Browse Games, Game Detail, Recently Added, Genres & Themes (list + detail), Franchises (list + detail), Companies (list + detail).**
Everything else — even pages that already borrow the header card or shipped in an earlier phase — is NOT
done: it still needs the full pass (depth, segmented switcher, premium motion, mobile three-layout
verification).

| Page | URL | Status | Notes |
|---|---|---|---|
| **Career** | `/career/` | ✅ | **The reference standard.** Jobs / Radar / Contracts. Depth pass applied. |
| **Collection** | `/collection/` | ✅ | Single Gallery (grouping-badge system; per-edition state derived live; Case + List retired). **Object-depth model** (medallion cast/rim shadows carry depth — deliberately does NOT take the card-lift). **Caption pass 2026-08:** the sort-adaptive stat slot now shows the named **rarity grade** in the shared scale (it computed `rarity_class` and then never rendered it, printing "Top 40%" off `rarity_pct` — which is the share of hunters who HAVE the badge, so it read exclusive and meant the reverse). It also shows the chase count on an edition you have not STARTED: `edition_display_state` calls zero cleared stages `unearned`, and `group_progress` only materializes editions with cleared > 0, so the most motivating number on the card ("0 / 5 stages") was the one case that rendered blank. The total is viewer-independent, so it now comes from the series' Stage count in one grouped query outside the badge loop — measured at 120 engaged series / 240 cards: 5 queries for the whole page. |
| **Badges** | `/badges/` | ✅ | Series + Gallery views; dynamic HTMX view-swap; depth pass; filter/sort settle. Anon quick-peek modal deferred. |
| **Badge Detail** | `/badges/<slug>/` | ✅ | From-scratch: header + tier ladder + how-to-earn grid + context band (rarity/ranks/My Stats modal) + stage journey (game cards w/ **contract band → in-place modal**, bundle, delisted strip, numbered spine w/ "Up next"). **Finish-audited 2026-07** (3-agent gap analysis vs the Career bar): shared motion primitives adopted (gated `.pp-head-cascade` header, sticky mini-header), whale-safe, a11y-covered. Cleanup done: dead context vars + a wasted per-load fundraiser query removed, `raw_response` deferred. Deferred nice-to-haves: `get_stage_completion` re-queried 4× (once/tier), stage-collapse load reflow, `sm:` vs `md:` breakpoint on the hero. |
| **Milestones** | `/milestones/` | ✅ | **Complete.** From-scratch page for the new `milestones` app (the legacy `criteria_type` engine it replaced is deleted). Accented `.pp-head-cascade` header -> count-up `.scard` overview (milestones started / tiers earned / hunters ranked) -> two **spotlight tiles** (*Closest milestone* = the non-maxed ladder furthest toward its next rung, forward pull; *Rarest feat* = the rarest tier you've earned, the brag) -> `{% regroup %}`'d ladder cards under section headers (Trophy Hunting / Supporter). Each `.msc` card is **number-forward** like Career: the hunter's current total is the focal accent `.pp-tally` (counts up on reveal) with a metric-unit sub-label, over a `.pp-horizon` next-rung bar and a 10-rung ladder with per-tier rarity. Per-milestone `--msc-accent`; **whole card is a stretched-link** into where you move it (trophies/plats -> profile, badges -> Collection, level -> Career, tenure -> Support); a slow **gold foil sweep** marks a fully-cleared ladder. `staggerReveal` + `countUp`, all reduced-motion gated. Whale-safe by construction: reads materialized read-models only, never live-evaluates a metric on the request path. Tested (`tests/engine/test_milestones.py`). **Deliberately no earn-celebration** (the page speaks for itself; a load-time popup read as loud). Deferred: Badge Collector + Pursuer Ascent ladder calibration against real data. |
| **Titles** | `/titles/` | ✅ | **Complete (re-done 2026-08).** The first pass shipped on the NEW grouping-badge system and was marked done, but it was the last stack-of-full-width-bars on the site (`.ttl-list`, a flex column of `.ttl-row`) -- the pre-rebuild list idiom, which is anti-reference #1. Worse, it under-served its own subject: a 42px medallion column its own stylesheet called "small on purpose" sat beside a 1.02rem title, so a page about *words you have earned* rendered its words smaller than a thumbnail it said didn't matter. **Rebuilt as the NAMEPLATE WALL.** The page's own `.ttl-plate` -- previously a one-off decoration used once at the top -- is generalised into the unit: `--hero` for the title you're wearing, `--tile` for every other title, in a `1 → 2 → 3` column wall. The page is now self-similar instead of "a nameplate, then a list of something else". Titles own **typography as material** the way Collection owns **medallions as objects**, so the title takes the display face at 1.22rem and the badge art drops to a 46px corner provenance stamp. That stamp is the BARE subject art, not `components/badge_medallion.html`: the medallion is a precious object designed to be looked at, and at stamp size it rendered all chrome and no art (and dragged in its own in-progress meter, so unearned plates drew two progress bars). **Rarity is the plate's material, in the SITE'S rarity vocabulary** -- `badge_rarity.group_rarity`, the same function badge detail and the browse gallery use, so a title cannot read one grade here and another there. Named grade (common/uncommon/rare/mythic, or "Be the first") + the shared `--pp-rarity-*` colours + the `#rarity-*` sprite, with `data-rarity` driving both the label and the plate's tint/edge/glow so material and label can never disagree. **Rarity model corrected 2026-08** (see [rarity](../../reference/rarity.md)): the denominator is the whole COMMUNITY -- every linked account, one cached scalar shared site-wide -- not the series' pursuers. A pursuer base SHRINKS when someone abandons a series (the standing row is deleted at zero progress), so a title could read rarer because people gave up on it. Thresholds recalibrated to 1/5/20% to suit that denominator (and to match PSN's own trophy rarity). Numerator is TITLE holders, not the badge's `earned_count`, because that is the number printed beside the grade -- and it counts everyone GRANTED the title, never the one title they happen to be wearing. Live, never denormed; the denominator costs no per-request query at all now. A title nobody holds gets no grade at all -- 0 earners is unearned, not an achievement. Blank plates for unearned titles are the point, not a fallback (visual-identity's Album rule: empty slots are visible and named). Switcher, `?view=` contract, equip-in-place and `/api/v1/equip-title/` all unchanged; tested (`tests/engine/test_my_titles.py`). **Two data bugs fixed behind it:** `grant_series_title` only fired on the `award` branch (so a badge earned before its series had a title never got one, and `evaluate_badges` could never repair it -- the diff is empty), and `get_or_create` silently returned a legacy row when a series reused a legacy Badge's Title, recording nothing. Both understated a title's holders, which is now the rarity numerator -- a title whose easiest edition most of the community holds was grading Mythic. The adapter now adopts; `sync_series_titles` backfills the history (deploy checklist row K). |
| **Home / Overview** | `/` | 🟡 | 4 gamification-first blocks shipped in an earlier phase; not finished to standard. Shares `.scard` (got the depth lift). |
| **Community Hub** | `/community/` | 🟡 | Hub-of-hubs shipped in an earlier phase; not finished to standard. |
| **Profile** | `/u/<user>/` | 🟡 | Ownership-aware chrome only; full surface pass pending. |
| **Browse Games** | `/games/` | ✅ | **Complete** (rebuild + premium-polish + gamification hooks + community stats). From-scratch rebuild: accented header (count-up Tally + catalogue `.scard` grid) -> quiet `.pp-bgal`/`.pp-gbrowse` toolbar + **animated** collapsible filter panel (house-style; `:has()`-driven flag/badge controls, `--pp-*` sliders, de-DaisyUI'd `browse-filters.js`, opt-in debounced live search) -> from-scratch `.pp-gcard` 3:4 cover card (depth pass, spring land-in, press feedback) -> infinite scroll (`InfiniteScroller` + `staggerReveal`, shared `HtmxListMixin` XHR guard) with `.is-swapping` settle + smart empty state. **Sticky mini-bar** (identity + live count + quick search + desktop sort + Filters reach, via shared `StickyReveal`). Grid-only (list retired); "In a Badge" toggle (specific-series "Pick a Badge" picker MODAL removed). The card carries the **pursuer hooks** (badge series + home contract) + a **community-stats footer** (players / plats earned / 100% completions / avg completion) fed by denormed `Game` columns recomputed nightly (`recalc_earn_rates`; DLC completion recompute lives in `detect_dlc_and_refresh`) -- whale-safe, zero extra queries per card. Tested (`tests/engine/test_game_browse.py`). |
| **Recently Added** | `/games/recently-added/` | ✅ | **Complete.** From-scratch rebuild pulling from Browse Games: accented header (primary/cyan accent, count-up Tally + 30-day `.scard` discovery grid + freshest-add recency) -> a `.pp-switch` **segmented category switcher** (New Games / New DLC, 30-day counts as chip captions; full-page nav per category so server renders the category-scoped sorts, with a sessionStorage-gated directional `slideViewIn` on an actual switch) -> quiet `.pp-gbrowse` toolbar + animated collapsible filter panel (Platforms / Has Platinum [base] / Hide Shovelware, reusing `browse-filters.js`) -> `.pp-gcard` grid + **infinite scroll** (`InfiniteScroller` + `staggerReveal` + `.is-swapping` settle) replacing page-jump pagination. The feed is **time-bound to the last 30 days** (`WINDOW_DAYS`, `POOL_SIZE` as a mass-import ceiling) so the grid matches the header's "last 30 days" discovery stats exactly. **Sticky mini-bar** (identity + live count + sort proxy + Filters reach). Base-games cards now render the SAME shared `.pp-gcard` **with the pursuer hooks** (badge series + contract) via the new **`build_game_card_context()` helper** (`game_views.py`) shared with Browse Games -- legacy Recently Added rendered the card without them. DLC packs (TrophyGroups) render a `.pp-gcard--dlc` sibling (secondary accent, DLC cover tag, parent-game subtitle, summed trophy count). Whale-safe (`raw_response` deferred on both querysets; batched maps); tested (`tests/engine/test_recently_added.py`). |
| **Genres & Themes** | `/genres/` | ✅ | **Complete.** From-scratch rebuild of the combined list page. `GenreThemeListView` converted `TemplateView` → `HtmxListMixin, ListView` (per-tab `model` property, dedicated `browse_results.html` partial replacing the old `hx-select` full-page re-render; no pagination — bounded ~20-genre / ~40-theme taxonomy renders whole). Accented header (primary/cyan, `.pp-head-cascade`, count-up Tally + Genres/Themes `.scard` grid) -> a `.pp-switch` **Genres / Themes segmented switcher** (full-page `?tab=` nav, sessionStorage-gated directional slide + ignite) -> quiet `.pp-gbrowse` toolbar (search + sort only — no platform-style filters) -> a **new from-scratch `.pp-gtile` category-tile card** (`components/grouping-tile.css`): the representative member-game cover as a scrimmed backdrop with the grouping **name over the art** + game count + the stat it's sorted by. Reads as a *category*, not a game. The cover is a **materialized `representative_game` FK** on Genre/Theme (read O(1) at render, no live subquery, so it scales regardless of catalogue / contract-catalogue size), recomputed daily by **`recompute_tag_covers`**: a **contract game** (curated, so tiles favour recognizable titles) picked with a **stable per-tag variety** hash (adjacent tiles differ, but a tile never reshuffles between loads — deterministic, not random), falling back to the most-recent member. Sticky mini-bar (identity + count + sort proxy). Bounded queries (count subquery rides the single list query; cover is a select_related FK); tested (`tests/engine/test_genre_theme_list.py`). **`.pp-gtile` + the materialized-cover pattern are reusable for the Franchise/Company list rebuilds.** |
| **Genre / Theme Detail** | `/genres/<slug>/`, `/themes/<slug>/` | ✅ | **Complete.** From-scratch rebuild of the per-tag game list (`TagDetailBaseView`). It's Browse Games scoped to one tag: **clean accented header** (glyph + name + type chip + a 4-tile `.scard` aggregate: games / owned / platinums / avg completion, computed off denormed `Game` columns in one whale-safe query -- a single game's cover read thin/muddy as a genre hero, so no art here; `representative_game` stays for the small list tiles + related rail) -> the full Browse `.pp-gbrowse` toolbar + collapsible filter panel (search / sort / Lucky / platforms / regions / quick / flags / ratings / time / engine / letter, reusing the shared `partials/browse/*` filter partials) -> `.pp-gcard` grid + **infinite scroll** (replaced page-jump) + sticky mini-bar. Backend reuse: swapped the hand-built rating/user maps for the shared **`build_game_card_context`** (lights up the pursuer band the old page lacked), `raw_response` deferred, dead prefetch dropped. **Materialized related-tags rail** (`.pp-related`): co-occurring same-type tags (top 6 by shared-game count) stored on `Genre/Theme.related_tags` by `recompute_tag_covers`, rendered with the shared **`.pp-gtile`** (extracted to `partials/tag_tile.html`, now shared with the list grid). JS via `onPageReady` (Back/Forward-safe). Tested (`tests/engine/test_tag_detail.py`). |
| **Franchises** | `/franchises/` | ✅ | **Complete.** From-scratch rebuild of the combined Franchise/Series list. `FranchiseListView` renders the shared **`.pp-gtile`** grouping tile (extended this pass with a **corner type badge** — Franchise vs Series — and a **· N versions** suffix when a grouping has more editions than distinct games). Accented header (primary/cyan, `.pp-head-cascade`, type-aware count-up Tally + sublabel) -> a **Franchise / Series / All** toggle styled as `.pp-switch` but backed by **radios** (a segmented *filter*, not a view-island: switching swaps just the grid and **preserves** the active search/sort; native `:checked` + browser Back stay correct with no JS, via `switcher.css` `.pp-switch__chip:has(input:checked)`) -> quiet `.pp-gbrowse` toolbar (search + `/` kbd + sort + single-game-entries toggle) -> `.pp-gtile` grid + **infinite scroll** (`InfiniteScroller` + `staggerReveal` + `.is-swapping` settle). The tile cover is a **materialized `representative_game` FK** on Franchise (O(1) at render, scale-proof), recomputed daily by **`recompute_tag_covers`** (now generalized over Genre/Theme/Franchise; the franchise cover pick **honors `is_excluded`/`is_spinoff`** link flags so curated/spin-off exclusions never provide the art). Sticky mini-bar (identity + count + search + sort proxy). JS via `onPageReady` (Back/Forward-safe). Whale-safe (`raw_response` deferred; count/version subqueries ride the list query); tested (`tests/engine/test_franchise_list.py`). **The Company list rebuild follows this ~1:1.** |
| **Franchise / Series detail** | `/franchises/<slug>/` | ✅ | **Complete.** From-scratch rebuild of `FranchiseDetailView`. **Tabs dropped** (the legacy `also_featured` split was permanently empty): now one IGDB-grouped game list + a **related-groupings rail** at the bottom (the opposite-type series/franchises that share games), rendered with the shared **`.pp-gtile`** — mirroring the Genre/Theme detail's related rail. Accented header (`.pp-head-cascade`) with a **cover thumbnail** (the franchise's most-recent release), a Franchise/Series identity chip, a `.scard` totals row (Games / Versions / Trophies / Platinums, count-up), and an authed **"Your progress"** block (a `pp-horizon` completion bar that fills from 0 + played/earned/platinum tallies). Sort is the only interactive control; it HTMX-swaps just the group list (`#franchise-groups`) with an `.is-swapping` settle + `staggerReveal`. **The IGDB-grouped card was rebuilt as `.fgroup` (`components/group-card.css`)** — cover + name + per-version rows (platform/region/flag badges, trophy counts, per-viewer progress ring) — and it is the **shared `game_groups_list.html` that Company detail also includes, so Company inherited the rebuilt card**. Backend wins: the related rail reads the materialized `Franchise.representative_game` FK (dropped the live `_franchise_cover_annotations` cover subqueries), `raw_response` deferred. JS via `onPageReady` (Back/Forward-safe). Tested (`tests/engine/test_franchise_detail.py`, incl. a Company-detail smoke test + the spin-off-suppression regression). |
| **Company list** | `/companies/` | ✅ | **Complete.** From-scratch rebuild of the Developers & Publishers directory, following Franchises list ~1:1. Renders the shared **`.pp-gtile`** grouping tile, extended this pass with a **studio-logo chip** (top-right over the art, `logo_url`) and a **country meta line** (`country_display`) — both optional slots the other groupings skip. Accented header (count-up Tally) -> a **visible Role quick-filter row** (Developer / Publisher / Porting / Supporting) over a quiet `.pp-gbrowse` toolbar (search + `/` kbd + sort) with a collapsible **Filters panel** (Platform / Country / Genre / Badge series — the genre + badge filters were backend-only before and are now surfaced) -> `.pp-gtile` grid + **infinite scroll** + sticky mini-bar. Cover is the materialized **`Company.representative_game` FK** (migration 0280), recomputed by `recompute_tag_covers` (now generalized over Genre/Theme/Franchise/Company). Backend: dropped the live `_company_cover_annotations`; converted the game/version counts to per-company subqueries (no cross-join multiplication); `raw_response` deferred. JS via `onPageReady` (Back/Forward-safe). Tested (`tests/engine/test_company_list.py`). |
| **Company detail** | `/companies/<slug>/` | ✅ | **Complete.** From-scratch shell rebuild of `CompanyDetailView`, following Franchise detail. Accented `.pp-head-cascade` header: a **logo thumbnail** (studio logo on a light plate, `logo_url`; falls back to a catalogue cover, then a building glyph), a Company chip, country + founding year, **merger links** (Subsidiary of / Now operating as), the IGDB description (clamped), a `.scard` catalogue totals row (Games / Versions / Trophies / Platinums, count-up), an authed **"Your progress"** block (`pp-horizon` fill-from-0 + tallies), and a compact **community-stats strip** (rating / difficulty / fun / grind / hours / players). The **role tabs became the segmented `.pp-switch`** (one chip per populated role — Developed / Published / Ported / Supporting — radio-backed, preserves the active sort; a single-role company shows no switcher and carries its role as a hidden input). Role switch + sort both HTMX-swap just the grouped list (`#company-groups`) with an `.is-swapping` settle + `staggerReveal`; the list is the **shared `.fgroup` `game_groups_list.html`** (gated `group_reveal`). JS via `onPageReady` (Back/Forward-safe). No related-companies rail (companies have no natural opposite-type relation; the merger links cover it). Tested (`tests/engine/test_company_detail.py`). |
| **Challenges / Game Lists / other Browse pages** | various | 🟡 | Header card adopted, but **no** depth pass / segmented switcher / premium motion. Header-aligned only. The shared `game_cards.html` card + flag/badge filter controls they include were upgraded to `.pp-gcard`/house style by the Browse Games pass (not separately verified). |
| **Game Detail** | `/game/<id>/` | ✅ | **Finished to the standard** — from-scratch rebuild of the hero + all its modals + the panels (Trophies, About, Ratings). The **Roadmap tab was removed for launch** (roadmaps-system archive decision pending; its context + legacy CTA-tab JS were dropped, the `roadmap_cta_*.html` partials kept for an easy restore). A **Ranks** panel (per-game leaderboard) was added: keyset-paginated, sticky self-row + jump-to-my-rank, and the ONLY panel not server-rendered — it is fetched on first activation, since its cost scales with a game's popularity and most visitors never open it (`pg_game_leaderboard_idx`, 289 ms → 0.6 ms; see [Game Leaderboards](../../features/game-leaderboards.md)). Rich hero (cover materialize + `.pp-head-cascade` content cascade) + site-standard `.pp-switch` (SSR panels). **Authed**: progress (completion + composite per-CTG group bars + a 4-across trophy `.scard` row with per-tier fills; one compact row on mobile) + a **My Stats** modal (hero trio, you-vs-community, tier haul, self-drawing journey timeline — the **platinum floats to its earn position**, 100% pinned last). **Anon**: a **Platinum Outlook** (PSN-**global** platinum rarity → difficulty meter + est. time; never our sparse votes) + trophy composition + a sign-up CTA. Spine cross-links: **Badges** (showcase medallion grid) + **Contract** — the SHARED `.rpm`/`.pp-detail-modal` (real card for linked viewers via `contract_modal`; anonymised **preview** + CTA for anon/unlinked via the new PUBLIC `contract_modal_preview` endpoint, mirrored onto Badge Detail). "X Players" headline (absolute corner, → Ratings). **Ratings** tab (Phase 4, renamed from Community; players drill-down moved to Ranks, written reviews deferred): a community-stats context strip (four denormed `Game` fields, zero queries) over a **"conditions card"** ratings surface (the Apple-Weather model, no chart: a synthesized **one-line summary sentence** is the headline — "Tough, a real grind, but a blast to platinum" — over a star score + three **icon word-tiles** (Difficulty / Grindiness / Fun) whose headline is the verdict WORD, so polarity lives in the word and nothing reads backwards the way a bar length can; the number is a quiet subscript; hours-to-plat is a separate time callout). Words come from `rating_summary` / `rating_verdict`, tone from `rating_tone`, all three mirrored in the live-update JS. Plus an **adaptive DLC selector** (pills ≤4 groups, Base pill + dropdown beyond) and the quick-rate modal rebuilt to the `gd-modal` standard preserving the `/api/v1/ratings/…/rate/` contract; tested (`tests/engine/test_game_detail_ratings.py`). **Report** modal rebuilt off DaisyUI (SSR + `.gd-report` form). Screenshot lightbox (FLIP open/close + carousel push + adjacent preload). Premium pass: press states, focus rings, choreographed modal exits + viewport-centred page-recede. Whale-safe (all DB-aggregated; `raw_response` deferred); tested (`tests/engine/test_game_detail_hero.py`, `test_contracts_service.py`). |
| **Plat Cards** | `/shareables/` | ✅ | **Complete.** From-scratch rebuild of My Shareables, narrowed from a 4-card wayfinder + four sub-pages to ONE job: browse your completions, make a share card. Built on the Browse Games/Franchises pattern -- accented `.pp-head-cascade` header + count-up `.scard` career stats -> a radio-backed `.pp-switch` variant FILTER (All / Platinum / 100%, preserves search+sort, Back-correct with no JS) -> quiet `.pp-gbrowse` toolbar (search / sort / shovelware, hidden by default here since these are the hunter's OWN completions) -> a new `.pcard` grid + `InfiniteScroller` + `staggerReveal` + `.is-swapping` settle + sticky mini-bar. **The old page rendered EVERY platinum in one response** with client-side search; it's paginated and server-filtered now. `.pcard` is a deliberate SIBLING of `.pp-gcard`, not a reuse: that card links to a game, this one is a button that makes an image. **The share modal was rebuilt too** (`plat-cards.js` replaces share-image.js + shareable-manager.js, both deleted): the preview is the REAL card markup from the HTML endpoint scaled to fit, so preview and download share one template and one theme list and cannot drift -- **eight** designed grounds as named cards -- four dark plus a lifted sibling for each (Substrate/Fog, Midnight/Tide, Ember/Clay, Aurora/Retro Wave), so the picker reads as pairs rather than eight unrelated options -- plus one art ground per landscape image the game actually has, each swatch showing its real image. Retired with it: Platinum Grid, Profile Card, and the Game Detail share button (a plat card now comes from exactly one place). Whale-safe (eligibility is a `ProfileTrophyGroup` read, one query per grid however many cards it draws); tested (`tests/engine/test_plat_cards.py`, `test_plat_cards_page.py`, `test_shareables_retirement.py`, `test_share_card_fonts.py`). **Card refinements after the row was first written:** the badge art stands unframed (the ring's `border-radius:50%` + `object-fit:cover` CROPPED the shield points, so distinct badges all resolved into one gold disc at card size) and the backdrop plate went with it; download feedback is a three-state button (idle -> spinner + Processing -> green Saved) plus a house toast through the modal's own `.modal-toast-container`; the renderer now inlines `/media/` as well as `/static/`, which is what let uploaded badge art reach the PNG at all. **Lighter grounds + modal fit (2026-08):** the team asked for lighter themes; "lighter" can only mean LIFTED here, because every text colour in `plat_card.html` is a hardcoded light hex (it is inline-styled for Playwright, no tokens), so a pale ground would put near-white text on near-white -- and a lifted ground's hot spot has to sit LEFT, since top-right holds the wordmark and the accent-coloured link. Retro Wave is the EXISTING site theme reused unchanged; rendered on the real card it beat the replacement drawn for it. The extra grounds pushed the swatch grid to a second row and the modal past 92vh, so `fit()` now bounds the preview by HEIGHT as well as width and the grid is `auto-fit/minmax(70px,1fr)` (12 columns at the 1000px box): the CARD gives way, never the picker. No ground is hidden behind chrome and the modal does not scroll at any viewport. Doc: [share-images.md](../../features/share-images.md). |
| **My Stats** | `/stats/` | 🗑️ | **Hidden for the 1.0 launch** (2026-08). `/stats/` **redirects to Home** (302, so bookmarks and the legacy `/my-stats/` + `/tools/stats/` paths land somewhere useful rather than 404ing or hitting a login wall); `MyStatsView` + its template/service/API are parked unrouted. Pulled from the My Pursuit rail, the footer, and the premium perk lists — the current page is the legacy 120+-stat dump, and shipping it at launch would set the wrong bar next to Career. It comes back as an upgraded tool: see [stats-page.md](../stats-page.md) for the existing surface + the relaunch plan, and [data-intelligence.md](../data-intelligence.md) for the arc it belongs to. Staff keep access so the rebuild can happen in place. Pinned by `tests/engine/test_my_stats_hidden.py`. |
| **Settings** | `/settings/` | ⛔ | Not rebuilt. Premium theme/background picker **disabled** pending rebuild (see [Gotchas](#gotchas-and-pitfalls)). |
| **Dashboard** | `/dashboard/` | 🗑️ | Sunsetting (301 → `/`); 41-module registry retired. Do last; some `dashboard_service` providers still load-bearing. |
| **Minigames** (Stellar Circuit) | `/arcade/...` | 🗑️ | Only remaining **ZoomScaler** page. Legacy transform-scale. |

> **Chrome** (nav / tabbar / subnav / hotbar / footer) is the site-wide **FRAME**, not a page — it was
> aligned 2026-07 (see [chrome-audit.md](chrome-audit.md)). Style it as chrome, never card-ify it.
>
> **ZoomScaler is effectively phased out** — only the minigame prototype still calls
> `PlatPursuit.ZoomScaler.init()`. Rebuilt pages are mobile-first three-layout (375 / 768 / 1024+),
> not transform-scaled.

---

## Shared Elements (every rebuilt page inherits these)

The reusable decisions. Each is **"the decision → where it lives → the authoritative doc."** Apply all of
them to every new page rebuild.

### 1. Page structure — STACKED chrome, FREE content
Chrome cards (page header, toolbars) are **stacked** cards; the content itself (grids, panels, tab bodies)
flows **FREE** — never wrapped in an outer card, even when tabbed. → design-system.md (Card Variants),
career-reference-standard.md §1.

### 2. Page header = accented card with substance
DaisyUI card shell + `--pp-*` substance: `card bg-base-200/90 border-2 border-base-300 border-l-4
border-l-primary shadow-lg shadow-neutral`. Title + italic subtitle + a headline **Tally** stat, and pull
**substance into the header** (stats, a collapsible explainer) rather than separate cards below (see
Career/Collection/Badges headers). Widely adopted already.

### 3. Tab groups = segmented switcher (ONE treatment site-wide)
Bordered container + transparent chips, tinted-flat active state, an icon per chip, **right-aligned** in a
`flex items-center justify-end` row. **One component: `.pp-switch` / `.pp-switch__chip`**
(`components/switcher.css`) — used by Career, Collection, and Badges (unified 2026-07; the old
`.lab-view-tab` / `.pp-collection__view-chip` / `.pp-vtoggle` are gone). Mini-bar copies wrap each chip's
label in `.pp-switch__lbl` so it collapses to icon-only on mobile. Secondary segmented controls with their
own look stay separate: Career's `.jlayout__btn` (rounded pill), the Case's `.pp-case__set-tab`
(completion-ring filter chips). Old pill tabs retired. → design-system.md (Tab Group / View Switcher).

**Shared behavior (use the helpers, don't re-hand-roll):** wire every switcher with
`PlatPursuit.wireTablist(tabs, {onSelect})` for the WAI-ARIA keyboard model (roving `tabindex` +
Arrow/Home/End) — `{manual: true}` for HTMX `<a>` chips (Badges) so arrows move focus and Enter/click does
the swap. Bloom the newly-active chip with `PlatPursuit.igniteTab(chip)`, and sync the URL with
`PlatPursuit.syncViewParam(view, {default, paramView, params})`. → [js-utilities.md](../reference/js-utilities.md),
motion-patterns.md (tab ignite). The directional panel slide (`slideViewIn`) is §7.

### 4. Depth — the surface ladder (the "depth pass")
Deepened 2026-07 so cards separate from the substrate by the **gap**, not by lightening anything.

| Rung | Token | ~L | Role |
|---|---|---|---|
| Substrate (`<body>`) | `--pp-bg-0` = `--color-base-100` (dark) | 0.13 | page base (`oklch(0.13 0.012 254)`) |
| Base cards | `--pp-bg-1` / `base-200` | 0.23 | content cards |
| Raised / nested | `--pp-bg-2` | 0.28 | cards nested inside a base-200 header; select menus |
| Highest | `--pp-bg-3` | 0.33 | rare |

- **Content cards** catch light + cast: `box-shadow: inset 0 1px 0 rgba(255,255,255,0.07), 0 6px 20px
  rgba(0,0,0,0.30)`. (`.pp-bgal__card`, `.pp-scard`, `.job`.)
- **Nested cards** (a card *inside* a base-200 header) **step UP** `--pp-bg-1 → --pp-bg-2` + a soft lift
  (`inset 0 1px 0 rgba(255,255,255,0.05), 0 3px 10px rgba(0,0,0,0.20)`), or they dissolve into the header.
  (`.pp-btiers__rung`, `.scard`.)
- **Do NOT lighten the substrate to add separation** — deepen it. Lightening flattens the gap and washes
  out the dark identity.
- **Exception — object-depth surfaces (Collection):** where a medallion carries its own outset cast/rim
  shadows + pedestal, keep the card minimal/inset (a drop shadow would clip those glows). Let the *object*
  float, not the card.

### 5. Toolbars = quiet chrome, not heroes
Base surface from the shared `.pp-toolbar-card` (`bg-base-200/90` + border), but **soften the shadow** so
the toolbar sits back and the content cards below own the pop: `box-shadow: 0 1px 3px rgba(0,0,0,0.22)`,
scoped per page (`.pp-bgal .pp-bgal__toolbar`, `.rp-toolbar.pp-toolbar-card`) with a 2-class selector so
it wins over `.pp-toolbar-card` without recolouring the shared class. Compact one-row bar (search + sort +
a Filters toggle); multi-select chips in a collapsible panel; filters auto-apply (no Apply button).

### 6. Premium motion (+ always gate reduced-motion)
Signature moments on a budget — real physics (spring settle), choreographed exits, deliberate restraint.
Use **WAAPI (`el.animate`)** for reveals so they replay reliably on HTMX-swapped nodes (CSS-class
animations don't restart). Every animation gates on `prefers-reduced-motion` — CSS in
`@media (prefers-reduced-motion: no-preference)`, JS via `PlatPursuit.Medallion.prefersReducedMotion()` /
`countUp()` (which jumps to target). → career-reference-standard.md §3, motion-patterns.md.

**Opening beat — every page's header/hero enters the same way.** Put **`.pp-head-cascade`**
(`components/motion.css`) on the header's **card-body**: its direct children (title, then stats, then the
rest) fade + rise in a staggered cascade — a livelier, layered entrance than moving the whole card as one
block, and the stagger is what reads as "premium." Up to three beats, then the rest share the last;
reduced-motion gated. Put it on **every** rebuilt page's top header (on Career, **both** the hero and the
summary header). **If the header lives inside an HTMX swap island** (badge detail's header rides
`#badge-tier-view`), gate it so it plays on first load only — `{% if not is_tier_swap %} pp-head-cascade{% endif %}`
— or it replays on every swap. Live on Career (hero + summary), Collection, Badges list, Badge detail.

### 7. Dynamic view swaps (HTMX innerHTML)
View toggles swap an island via `hx-get` + `hx-target="#..." hx-swap="innerHTML" hx-push-url`, not a full
reload. Re-init reveals/scrollers in an `htmx:afterSwap` handler keyed on `e.detail.target.id`. (Badges
`#badge-view`, Collection, Career.) **Slide the incoming view in directionally** — forward in the tab
order from the right, backward from the left (Material shared axis) — via `PlatPursuit.slideViewIn(panel,
from, to, order)` + the `.pp-view-in-*` classes (`components/motion.css`). Works for JS toggles (Career
tabs, Collection Case/Gallery/List — call it on the shown panel) and HTMX swaps (Badges — track `lastView`
and call it on the swapped-in root in `afterSwap`). → motion-patterns.md (Directional view switch).

**The segmented switcher itself MUST HTMX-swap — never a full-page reload.** A `.pp-switch` whose chips are
`<a href="?tab=…">` that reload the page is a bug, not an acceptable fallback (keep the `href` only as the
no-JS degrade). The canonical shape (Badges, Recently Added, Genres & Themes):
- **Two nested swap targets.** The chips swap a **view island** (`hx-target="#<page>-view"`) that wraps the
  *toolbar + results* — so a per-tab toolbar (different sorts/filters per tab) re-renders in sync with the
  grid. A search/sort/filter change swaps only the inner **`#browse-results`** grid, so the open filter panel
  survives. The view's `get_template_names` returns the view-island partial when `request.htmx.target ==
  '<page>-view'`, the grid partial when it's `'browse-results'` (or an InfiniteScroller XHR), else the full
  page. Even when a page's toolbar is identical across tabs (G&T), swap the island anyway for one uniform
  pattern — never a grid-only tab swap that leaves the toolbar controls out of sync with the rendered grid.
- **Re-init on the island `afterSwap`.** Branch on `target.id`: for the grid swap re-init reveal/scroller +
  tick the count; for the island swap ALSO `slideViewIn(island, lastTab, newTab, ORDER)`, sync the toggle
  active state (`is-active` + `aria-selected`) + any header/mini-bar labels that name the tab, re-wire the
  freshly-rendered toolbar chrome (filter panel, badge, sort proxy) + `StickyReveal.init()`. Read the new tab
  off a `data-active-*` attr baked onto the swapped `#items-grid`. Cancel a redundant swap when the *active*
  chip is clicked (`htmx:beforeRequest` → `preventDefault`). Keep tab-specific header copy (subtitle) neutral
  or update it in the handler, since the header sits outside the island. `wireTablist(chips, {manual:true})`
  for the keyboard model on the `<a>` chips. Reference JS: `static/js/{genre-theme-list,recently-added}.js`.
- **Survive Back/Forward — wire via `PlatPursuit.onPageReady(boot)`.** HTMX restores a pushed-URL page by
  replacing the history element's innerHTML from a snapshot; it does NOT re-fire `DOMContentLoaded` or
  `afterSwap`, so the restored DOM is fresh, unwired nodes — but `document.body` persists. `onPageReady` runs
  `boot(first)` on first load AND on `htmx:historyRestore`: put **element wiring** (resolve nodes, bind their
  listeners, init reveals/scrollers) in `boot` so it re-runs on restore against the fresh nodes (the old
  bindings died with the old nodes — no leak), and guard **body/document/window listeners** with `if (first)`
  so they bind exactly once and keep firing across restores. Skipping this leaves a Back-restored page with a
  dead filter panel / sort proxy / reveal. (Badges predates the helper and does the equivalent inline via a
  `wireView()` re-run on `htmx:historyRestore`.)

### 8. Filter/sort settle (no blank-flash)
On a filter/sort swap, dim the results container while in flight so it never freezes/blank-flashes. Add
the dim **on `change`** (a JS `.is-swapping` class) so it spans the `hx-trigger` debounce — not just the
network request — then clear it in `htmx:afterSwap`. Motion-gated. Empty-state panels fade+rise in.

### 9. Ad slot placement
A horizontal `partials/ad_unit.html` goes **after the page header, before the view tabs**, outside the tab
panels — so it shows on whichever view is loaded and a tab swap never re-inits it. (Badges, Collection,
Career.)

### 10. Modals = top of the elevation stack (insulated from the substrate)
Scrim `rgba(2,4,8,~0.6)` + `backdrop-filter: blur(3–4px)`; dialog on `--pp-bg-1` + a big float shadow
`0 30px 90px rgba(0,0,0,0.55)`; internal stats step up to `--pp-bg-2`. Because they float on a scrim (not
the substrate) they need **no** depth-pass lift — the deeper substrate only helps them. Shared factory:
`PlatPursuit.Medallion.detailModal(config)` (pick-up / put-down). (`.pp-detail-modal`, `.emodal`.)
**Swipe to close on mobile (common practice):** wire every modal's dialog with
`PlatPursuit.dismissableSheet(dialog, {onClose, scrim})` — a downward flick slides it off; `onClose` hides
instantly + runs the close-button cleanup. It adds `.pp-dismissable`, which shows the touch-only grabber
handle (`.pp-dismissable::before`). → js-utilities.md. The medallion peek is wired too (via
`Medallion.detailModal`): it can't FLIP its disc back from a dragged position, so a swipe **returns the
object home** (source medallion re-materializes in its slot); tap/close keeps the grow/shrink put-down.

### 11. Image conventions
Covers use `object-cover object-top` + `aspect-[3/4]`; trophy icons `object-cover` square; badges
`object-contain`. Never `object-fill`. Cover fallback chain lives on `Game.display_image_url` — never
reimplement inline. → project CLAUDE.md (Image Styling Conventions).

### 12. Whale-safe querysets
Per-user aggregates (counts/sums/distributions) **must** DB-aggregate (`.values().annotate(Count)` /
`.aggregate()`), never Python iteration over a profile-scoped queryset. Preview/locked UIs must not run
heavy providers against real user data. → project CLAUDE.md (Performance / Premium Preview).

---

## Approved Building Blocks

**Build from these, not from the old page.** The canonical source is `static/css/input.css` (token values) +
[design-system.md](../../reference/design-system.md) (patterns); this is the quick reference to consult
before a from-scratch pass. When you reach for a colour/spacing/font, it should be one of these.

### Tokens (`--pp-*`)

| Group | Tokens |
|---|---|
| **Surfaces** | `--pp-bg-0` (0.13 substrate) · `--pp-bg-1` (0.23 cards) · `--pp-bg-2` (0.28 nested/raised) · `--pp-bg-3` (0.33) |
| **Text** | `--pp-text` · `--pp-text-dim` · `--pp-text-mute` |
| **Lines** | `--pp-border` · `--pp-divider` |
| **Brand** | `--pp-primary` (cyan) · `--pp-secondary` (violet) · `--pp-accent` (amber) |
| **Semantic** | `--pp-success` · `--pp-warning` · `--pp-error` · `--pp-info` |
| **Type** | `--pp-font-display` (Bricolage — hero + numbers ONLY) · `--pp-font-body` (Inter) |
| **Motion** | `--pp-dur-fast` (140ms) · `--pp-dur` (240ms) · `--pp-dur-slow` (520ms) · `--pp-ease` |
| **Shape** | `--pp-border-w` (2px) · `--pp-radius-sm` / `-md` / `-lg` |

DaisyUI theme colours mirror the brand/semantic tokens and are applied via Tailwind `text-*`/`bg-*`
(`primary`, `secondary`, `accent`, `success`, `warning`, `error`, `base-100`..`base-300`).

**Scoped colour families — use ONLY on their own surfaces, never as generic accents:** trophy
`--color-trophy-{bronze,silver,gold,platinum}` · career disciplines
`--disc-{combat,exploration,mind,heart,finesse}` · pursuer ranks `--rank-*` · tier medallion `--med-c` /
`--med-glow` (data-tier keyed, internal to `.pp-med`).

### Shared components (compose these; don't reinvent)

- **Medallion** — `components/badge_medallion.html` (`.pp-med`, size via `--sz`). The badge object.
- **Horizon** (`pp-horizon`) — progress bars: smooth by default, or **`pp-horizon--segmented`** for a
  discrete meter (one `pp-horizon__seg` cell per unit, `data-state="done"/"active"`, gradient
  `--horizon-from`→`--horizon-to`). **Cap the segment count (~8–12) and fall back to the smooth bar above
  it** so cells don't turn into slivers: `SEGMENT_CAP=12` (medallion meter, `frame_service`) /
  `TILE_SEGMENT_CAP=8` (tile horizons, `badge_views`); `frame.segments` (booleans) is prebuilt for a
  medallion's tier. Reduced-motion gated. Used on the Series-tile tiers, the milestone ladder, the medallion
  meter, and the badge-detail header. **Tally** (`.pp-tally`) — display numbers (+ `PlatPursuit.countUp`).
- **Accented header card** — `card bg-base-200/90 border-2 border-base-300 border-l-4 border-l-primary shadow-lg shadow-neutral`. Give its card-body **`.pp-head-cascade`** (`components/motion.css`) for the shared opening-beat entrance — its content rises in staggered (see Premium motion §6).
- **Stat tiles** — `.scard` (a few headline summary stats, Career/Home) · `.pp-bdetail__stat` k/v (compact, dense badge stats).
- **Progression ladder** (`.pgl`, `static/css/components/elements.css`) — the segmented tier/rank stepper: reached rungs fill the accent (`--pgl-accent`; per-rung `--rung-c`), the current widens + glows. Reuse for ANY progression, don't re-roll a ladder. Consumers: Pursuer rank ladder (Career hero), job prestige ladder, claim ceremony, badge tier ascent rail (`.pgl--static` = resting fill, no mount choreography).
- **`.pp-draw-in`** (`components/motion.css`) — draw an SVG stroke in (checkmarks, glyphs); shapes need `pathLength="1"`. Reuse the `ppDrawIn` keyframe directly for scroll/state-gated draws. → motion-patterns.md.
- **Rarity** (`components/rarity.css` + `components/rarity_grade.html`) — anything gradeable. The hook is **`data-rarity`, not a modifier class**: it declares the `--rar-*` properties and custom properties inherit, so a card carries the attribute ONCE and both the label inside it and the card's own material read the same declaration — they cannot disagree. Opt into `.pp-rarity` (label), `.pp-rarity-surface` (tint + edge + finish) and `.pp-rarity-gem` (an object) independently. Composition stays per-surface (Frame shows only a percentage, the gallery only a name, badge detail adds a gem); what is shared is the SCALE and the styling. This exists because six surfaces hand-rolled it off two tokens — 26 rule blocks, no component — so a seventh surface is an include, not a seventh copy. **Never re-declare a grade colour**; write `color: var(--rar-c)`. → [rarity](../../reference/rarity.md).
- **Segmented switcher** (tab groups) · **`.pp-toolbar-card`** (toolbars) · depth-pass card shadows (see Depth in Shared Elements).

### Colour restraint (how the rebuild actually uses colour)

Colour is **earned by meaning, not decoration.** Default numbers/text to **neutral**. Reserve `--pp-primary`
(cyan) for a **single** headline accent per surface (Career colours one stat value; Collection one Tally).
Use semantic colours (`success`/`warning`/`error`) **only** where they carry glanceable information (a
difficulty rating), never as per-field decoration. Scoped families (tier/disc/rank/trophy) stay on their own
surfaces. **If a surface lights up 4+ hues, that's the old "colour-code everything" instinct — pull back.**

---

## Gotchas and Pitfalls

- **`.scard` is shared (Career + Home).** The depth lift on it improves both; a change there is not
  Career-only. Check Home when touching it.
- **Premium themes are OFF site-wide.** `premium_theme_background` returns `{}` behind a
  `PREMIUM_THEMES_ENABLED` flag and the settings picker is disabled — everyone gets the base substrate.
  The settings-page rebuild restores both. (The old `image_urls` body-background-art path was removed
  permanently; `image_urls.header_bg_url` / `screenshot_urls` for the game-detail header remain.)
- **A palette that sorts as swatches can be unusable as a wall.** The rarity ramp was rebuilt twice: four unrelated hues INVERTED at the top (the loudest grade was the second-rarest), and the single mint→emerald ramp that replaced it separated grades by LIGHTNESS ALONE — fine as four big swatches, collapsed at chip size, where uncommon and mythic both just read "greenish". Judge any scale at the density it will actually be seen. Related: **tint/edge percentages are percentages OF A HUE and don't transfer between palettes** — magenta at the emerald ramp's numbers washed the plate over its own label.
- **Check what already owns a colour before spending one.** Rarity sits ON cards that colour themselves by TIER (gold / platinum pale-cyan / bronze tan / silver grey), and green is `--pp-success`. Those five were the real constraint on the palette, not taste.
- **Substrate is a global token.** Editing `--pp-bg-0` / `--color-base-100` touches every page — verify a
  couple of others, not just the page you're on.
- **Rebuild `npm run build` after any CSS/template change**, and check the value in `output.css`
  (lightningcss reformats, e.g. `oklch(0.13 …)` → `oklch(13% …)`, and emits `color-mix` fallbacks).
- **Don't card-ify chrome.** Nav/tabbar/subnav/hotbar/footer are the FRAME, styled as chrome, not modules.
- **VS Code's built-in CSS linter flags Tailwind v4 at-rules** (`@plugin`/`@theme`/`@apply`) as errors —
  false positives. `npm run build` is the real validator.
