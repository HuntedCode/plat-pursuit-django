# Fresh-Eyes Design Reviews

Periodic frontend reviews of the live product, done from a visitor's point of view rather than from
the style guide. The rebuild measured every page against our own documents (Career, the design system,
visual identity), which is what made the site coherent. It is also blind to anything that is consistent
and still not good. These reviews look for that.

Each review covers one **user journey** (a slice), not one page, because most of what feels off lives in
the hand-offs between pages.

## Slices

| Slice | Covers | Status | Record |
|-------|--------|--------|--------|
| First visit | Anon landing, sign up / sign in, email verify, link PSN, first sync, first arrival on the lobby + Career | Reviewed 2026-10-20 | [first-visit.md](first-visit.md) |
| Daily loop | Lobby, Career, contracts and claims | Reviewed 2026-10-20 | [daily-loop.md](daily-loop.md) |
| Discovery | Browse games, game detail, trophy lists, search | Reviewed 2026-10-22 | [discovery.md](discovery.md) |
| Challenges | The Challenges system (public since 2026-10-18) | Not started | |
| Identity and social | Profiles, badges, leaderboards, share cards | Not started | |
| Account | Settings, membership, fundraiser | Not started | |

## Site-wide findings

Findings that belong to no single slice. Same status values as the slice records.

| ID | Sev | Finding | Status |
|----|-----|---------|--------|
| SW-01 | Medium | DaisyUI is half-replaced, and the seam is visible. See below | partly fixed: `refactor/daisyui-split` (status colours aligned to `--pp-*`; `btn`, `badge`, `tabs` retired behind a ratchet guard; `pp-cta` gained sizes, a quiet variant and icon buttons). Alerts/toasts: planned, site-wide noise. Form fields: planned, onboarding rebuild |
| SW-02 | Medium | The back-to-top button (`templates/partials/back_to_top.html`, DaisyUI `btn-primary`, 64px at `lg`) is the loudest element on every page, and on a phone it covers a contract card's Claim button. Seen as [FV-10](first-visit.md) and [DL-06](daily-loop.md) | fixed: `feature/ui/site-wide-noise` (quiet house icon button, offered only on scroll-up, inert while hidden) |
| SW-03 | Low | Every trophy-list detail page (`/games/<np_communication_id>/`) throws a console error: `game_detail.html` loads `submit_shimmer.js`, which wires `#filter-form` / `#submit-btn`, and the page's form is `#gd-filter-form`. Pre-existing; found by the 2026-10 cleanup's browser pass | fixed: `fix/review-leftovers` (the script was dead on its only page, so it was deleted) |
| SW-04 | Low | `templates/recap/monthly_recap.html` loads `color-grid-modal.js`, but nothing on the page calls it (its only host was the deleted platinum grid). A dead script load. Pre-existing; found by the 2026-10 cleanup's audit | fixed: `fix/review-leftovers` (script and tag deleted; `tests/engine/test_template_static_refs_exist.py` now fails on any `{% static %}` path that does not exist) |

### SW-01: DaisyUI, foundation versus retired components

DaisyUI was never meant to be removed wholesale. Part of it is the documented foundation: the design
system's card recipe is DaisyUI `card` + `card-body` dressed in house tokens, and its theme supplies the
`bg-base-*` / `text-base-content` colors used across nearly every template. Career, the reference
page, uses `card`, `btn btn-primary` and `loading` too.

What is unfinished is the component swap. Some components have house replacements (`badge` became
`.bd-chip`, whose header calls itself "the strangler replacement for DaisyUI"; browse toolbars became
`.pp-bgal__*`), but DaisyUI component classes were still present in about 190 templates and JS files on
2026-10-20, roughly 20 of them staff or admin screens. The visible cost is **two button systems**: the
house `pp-cta` (dark teal, the landing and the no-PSN welcome) and DaisyUI's bright-cyan `btn-primary`
(sign up, link PSN, the sync finale, Career). That split is a large part of
[FV-01](first-visit.md#fv-01-two-design-generations). Toasts are the same story: `ToastManager` builds
DaisyUI `alert-*` blocks, a saturated slab on a phone ([DL-10](daily-loop.md)).

Proposed shape, not yet decided:

1. **Name the split in [design-system.md](../../reference/design-system.md).** Kept as foundation (card
   shell, theme colors, spinner) versus retired with its house replacement (`badge` to `.bd-chip`,
   `btn` to `pp-cta`, and decisions still to make for `alert`, `input` / `select`, `progress`).
2. **A guard test for retired classes**, with the existing uses on an allowlist that may only shrink, so
   new code cannot add more.
3. **Convert inside the fix branches already planned**, page area by page area, rather than as one sweep.
   Check each heavy file for liveness first: some (the archived review hub, legacy comments) are likely
   deletions, not conversions.

Re-measure before planning: the figures above are a snapshot, not a target.

**Left after branch 2 (2026-10), now done (`fix/review-leftovers`):** the three `input.css` rules that
hard-coded the old DaisyUI success/error literals went in a wider dead-CSS sweep: about 60 classes
nothing rendered (the old checklist editor, trophy cards and landing animations), roughly 500 lines.
`trophy_phases.py` now gives each phase a house `tone` instead of a `badge-*` class, and the roadmap's
phase pills, grouped section headers and editor pill are `.bd-chip`s built from it. The two quiet phases
(Side, Grindy) share the `ghost` tone.

## Fix plan

Agreed with the owner on 2026-10-20 after the first two slices. Every finding in both records maps to
one of these branches (its status row names it). Run in this order:

| # | Branch | Covers | Shape |
|---|--------|--------|-------|
| 1 | Quick fixes (`fix/review-quick-fixes`) | FV-04, FV-09 copy, FV-16, FV-18, FV-19 | Done |
| 2a | Dead legacy cleanup (`refactor/delete-dead-legacy-ui`) | SW-01 prerequisite | The SW-01 inventory found 25 of the 33 heaviest DaisyUI files unreachable. Deleted (owner's call): the old review hub, old Game Lists, platinum grid, trophy case page, comments UI, notification inbox, roadmap game-page partials. Done |
| 2 | DaisyUI split (`refactor/daisyui-split`) | SW-01 | Status colours read the `--pp-*` tokens; the kept / retired / pending table in design-system.md; `pp-cta` sizes, `--bare` and `--icon`; `btn`, `badge`, `tabs` retired behind `tests/engine/test_retired_daisyui.py`. Done |
| 3 | Site-wide noise (`feature/ui/site-wide-noise`) | SW-02 (FV-10, DL-06), FV-11, DL-10, FV-15 | Quiet scroll-up back-to-top; `.pp-notice` / `.pp-toast` with one messages renderer; `alert` and `toast` retired behind the guard; Premium and NEW wait for a linked PSN. Done |
| 3a | Review leftovers (`fix/review-leftovers`) | SW-03, SW-04, the SW-01 leftovers | Two dead scripts and the dead-CSS sweep; phase pills on `.bd-chip`; back-to-top steps aside for toasts; pins for branch 3's two unpinned fixes. Done |
| 3b | Pursuer Level from 1 (`feature/gamification/pursuer-level-from-one`) | DL-04 | The owner chose to redefine the level rather than restyle it: it counts levels gained, so a newcomer is 1. Ranks and Ascent rungs moved down 25; a backfill command moves the stored copies. Its own branch because it changes stored numbers. Done |
| 4 | Career + first arrival (`feature/ui/career-first-arrival`) | FV-03, FV-12, DL-01, DL-02, DL-03, DL-09 | Design record: [career-first-arrival.md](career-first-arrival.md). Every surface says what claiming makes the hunter (`claim_projection`); the lobby leads with the Pursuit; Career's summary folds into the hero. Done |
| 5 | Onboarding rebuild (`feature/ui/onboarding-rebuild`) | FV-01, FV-07, FV-08, FV-09 step-2 help, FV-13, FV-20 | Design record: [onboarding-rebuild.md](onboarding-rebuild.md). One auth shell with focused chrome; house fields (`input`, `select`, `textarea`, `checkbox`, `progress` retired behind the guard); "Check your inbox" names the address, resends and fixes a typo; the sync card leads with the wait. Done |
| 6 | Contracts board (`feature/ui/contracts-board-pass`) | DL-05, DL-07, DL-08, FV-14 | Design record: [contracts-board.md](contracts-board.md). The card is a list row on a phone; the claim names its XP; the modal explains the split and claims; the cover fan draws a game. Done |
| 7 | Landing (`feature/ui/landing-pass`) | FV-05, FV-06 | Design record: [landing-pass.md](landing-pass.md). "Look up" plus the claim, carried through sign-up to Link PSN; captions and a real-size viewer for the share pictures. Done |

Career comes before onboarding because every hunter hits Career on every visit, while onboarding
happens once per hunter.

**Owner decisions** (2026-10-21, the last three open ones):

- **FV-07:** sign up asks for one email and one password. The confirm fields go: the password field's
  show toggle catches typos, and a mistyped email is caught by "Check your inbox" showing the address
  it was sent to, with a resend and a way to start over (FV-08).
- **FV-05:** the landing keeps the PSN search, because seeing your history before giving an email is
  the pitch. It stops competing with sign up: the button becomes "Look up", and a found profile offers
  "That's you? Sign up free to claim it." Linking reuses the profile the search synced
  (`LinkPSNView` `get_or_create`), so the claim does not start a second sync.
- **FV-14:** the cover fan stays pure CSS, but each card gets a drawn trophy mark and title bar so it
  reads as a game, not a failed image. One change fixes its three hosts; it goes in the contracts board
  branch, since the fan stands for a contract.

Also decided: the action names are **Sign in / Sign up / Sign out**, sentence case (FV-04, pinned by
`tests/engine/test_action_names.py`). A newcomer is **Pursuer Level 1**: the level counts levels gained,
with ranks and rungs moved down 25 and no announcement (DL-04, 2026-10-21).

## Discovery fix plan

Agreed with the owner on 2026-10-22, after the [Discovery](discovery.md) review and the
[Page Principles](../page-principles.md). The first pages built under the principles: the owner reviewed live
mock-ups (the real dev pages rearranged in a browser) at phone and desktop before any of it was built. Run in
this order:

| # | Branch | Covers | Shape |
|---|--------|--------|-------|
| 1 | Quick fixes (`fix/discovery-quick-fixes`) | DS-01, DS-03, DS-05, DS-10, DS-12, DS-13 (breadcrumb), DS-14 | Browse Games binds no default filter and opens on Trending; a chosen platform shows as a chip; the franchise and company grid stops widening the page; rate labels on phones; the search button names what it searches; a list named after its game reads "PS4 trophy list"; "6,000 XP". Done |
| 2 | Total players (`feature/models/concept-player-count`) | DS-08 | Each player counted once per game (distinct profiles across its lists), stored on the concept and refreshed nightly, the `Genre.player_count` pattern. Cards and the game page both read it, so neither double-counts a hunter who owns two versions; Most Played on game-card grids sorts by it too. Done |
| 3 | Browse pass (`feature/ui/browse-pass`) | DS-02, DS-04, DS-06 | No stat tiles on the six browse list pages, one count that matches the grid; detail pages fold their tiles into one line. One card anatomy (title with two lines reserved, one hooks row that keeps its height, the footer lined up under it); the hunter's mark on the cover; the trophy breakdown only on single-list cards, platforms on every card. Two-row phone toolbar. Breadcrumbs hidden on phones site-wide. Done |
| 4 | Game page pass (`feature/ui/game-page-pass`) | DS-07, DS-09, DS-13 (family row) | The hunter's status card; the platinum's rarity in a side panel; Share / Add to list / Report an issue on the first screen at equal weight; desktop in three columns (cover, story, two equal side panels); a phone thumbnail; facts into About; trophy list picker cards that can be told apart. The trophy list page's hero follows in the same branch |
| 5 | Franchise art (`fix/franchise-cover-art`) | DS-11 | A franchise's card art comes from its own games, not a crossover it appears in |

**Owner decisions** (2026-10-22):

- **DS-01:** no default filter. **DS-05:** Trending first. **DS-08:** total players, each counted once.
- **Breadcrumbs** are hidden on phones everywhere: the sub-nav already says where you are, and the
  `BreadcrumbList` schema is generated separately (`core/templatetags/seo_tags.py`), so search is unaffected.
- **Trophy breakdown** (19 / 10 / 5 / 1) stays on cards that are one trophy list and leaves cards for a whole
  game; **platforms** are on every card.
- **The game page's corner number** is the platinum's rarity ("Very Rare platinum 9.1%"), no "of players earn
  it" line; the player count joins the one-line facts.
- **Share, Add to list and Report an issue** stay on the first screen at equal weight. Hiding a button kills
  its use; this became the "Actions are not depth" line in the Page Principles.
- **Desktop layout must line up**: repeated cards share one anatomy, and panels side by side share a width,
  a height and their neighbours' edges. Written into [design-system.md](../../reference/design-system.md) by
  the browse pass.

## Page Principles re-pass

The [Page Principles](../page-principles.md) were agreed on 2026-10-22, after First Visit and Daily loop had been
reviewed and fixed. Those two slices get a **re-pass**: the checklist only, not a fresh review, recorded as a
"Principles re-pass" section in each record, with any new findings numbered on from the record's last ID.

| Slice | Pages | Expectation | Status |
|-------|-------|-------------|--------|
| First visit | Landing (signed out: rules 2 to 6), sign up and link PSN (focused flows), first arrival on the lobby and Career | Small: the flows already do one job each; the landing is the place site-wide numbers belong | Not started |
| Daily loop | Lobby, Career (Jobs, Radar, Contracts), contracts board and history, contract modal | Small to medium: branch 4 made the claim lead, but Career and the board carry many systems at once | Not started |

Discovery (2026-10-22) was reviewed before the rules existed but is fixed under them: its fix plan applies
the rules, and is the pilot.

## How a review is run

1. **Judge rendered pages, not templates.** Playwright screenshots at phone (375), tablet (768) and
   desktop (1440), with a fold shot and a full-page shot after scrolling the page once so scroll-reveals
   have fired.
2. **Phones need real device emulation.** Use `p.devices['iPhone 12']` (or similar) with the viewport
   overridden, not just a narrow `viewport`. A desktop-sized headless window ignores the meta viewport
   and hides mobile-only layout bugs. Check `window.innerWidth` against
   `document.documentElement.clientWidth` for sideways overflow.
3. **Cron-cached sections must be judged on prod.** Dev caches are usually cold, so the landing's
   heartbeat band, badge shelf, showcase card and ratings carousel render empty or as fixtures. Capture
   the public pages from `https://platpursuit.com` (read-only) before calling anything empty.
4. **Use the team preview doors** (`?preview=syncing`, `?preview=landing`, `?preview=launch-welcome`;
   see `core/previews.py`) for states a normal account cannot reach. They write no state. Remember what
   they cannot show: a synced account previewing the syncing room starts at "Synced!" with a full bar.
5. **Logged-in captures.** Create a throwaway local account for no-PSN states and sign it in through the
   real login form. For staff-only states, open a headed browser on the local login page, let the owner
   sign in, and reuse that browser's `storage_state`. Never mint a session for a real account.
6. **Exercise the interactions that write nothing**: menus, validation errors (submit invalid input so
   nothing is created and no email is sent), modals, the DEBUG simulate panels.
7. **Measure before claiming.** Text size, tap targets, overflow and load time are cheap to measure in
   the browser. A finding that says "unreadable" should carry the number.
8. **Rank findings by impact** and record each with where it lives, the evidence, and a status.
9. **Then run the [Page Principles](../page-principles.md) checklist** on every page in the slice, signed in and
   signed out. Second, not first: the screenshot pass finds what a visitor feels, the checklist names why and
   catches what the eye got used to.

## Lifecycle of a finding

- Each review's record holds its findings table. **A fix branch updates the finding's status row in the
  same commit as the fix**, or the record goes stale.
- Findings that recur across slices are **promoted into the docs people already read** (usually a
  Gotchas entry in [design-system.md](../../reference/design-system.md)), or better, into a test. Do not
  start a separate "don'ts" document: it would be one more place to check and would drift.
- One sighting is not a rule. Each record keeps a short list of candidate lessons; a lesson is promoted
  only when a second slice shows the same thing.

## Gotchas and Pitfalls

- **Reading the docs first defeats the purpose.** The value of the review is the outside view. Do the
  first pass from screenshots and the page's purpose alone, then check findings against the docs to sort
  them into "page breaks a rule", "rule should change" and "docs never covered this".
- **Preview and simulate states are not real states.** The syncing preview's numbers and the simulate
  panel's tallies are fabricated; do not file findings against their values.
- **Coincidences look like bugs.** Two equal numbers on one page are worth one grep, not a finding (see
  first-visit.md's "Checked, not findings").
