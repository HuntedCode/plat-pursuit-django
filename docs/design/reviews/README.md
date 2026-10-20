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
| Daily loop | Lobby, Career, contracts and claims | Next | |
| Discovery | Browse games, game detail, trophy lists, search | Not started | |
| Challenges | The Challenges system (public since 2026-10-18) | Not started | |
| Identity and social | Profiles, badges, leaderboards, share cards | Not started | |
| Account | Settings, membership, fundraiser | Not started | |

## Site-wide findings

Findings that belong to no single slice. Same status values as the slice records.

| ID | Sev | Finding | Status |
|----|-----|---------|--------|
| SW-01 | Medium | DaisyUI is half-replaced, and the seam is visible. See below | open, plan after the Daily loop review |

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
[FV-01](first-visit.md#fv-01-two-design-generations).

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
