# Review: Game page and trophy list page, below the hero

A follow-on to [discovery.md](discovery.md): the game page's Trophy Lists and Ratings tabs, and the trophy list
page (`/games/<np_communication_id>/`) from top to bottom, after the game page's hero pass (DS-07). Reviewed
2026-10-22 at 375 / 768 / 1440. Method: [README.md](README.md).

**Where:** **dev** signed in with the owner's saved browser session (Golf With Your Friends PS4: platinumed,
five DLC packs, a contract to claim; Jak 3 PS4: 45%; Kingdom Hearts HD 1.5+2.5: four lists), and
**https://platpursuit.com** signed out for real community numbers (read-only GETs).

**Headline:** on a phone the trophy list page's first trophy was almost three screens down (1,883px), behind a
cover-first hero, a facts grid, four grade cards, screenshots, "Not in a badge series", the Jobs panel, four
community tiles and a full toolbar. The fix was to give it the game page's hero, asked of one list, and to
give both pages one small control row. Mock-ups approved in two rounds; the phone status card was reworked in
the second (the owner found the first cluttered).

## Findings

| ID | Sev | Finding | Where | Status |
|----|-----|---------|-------|--------|
| GL-01 | High | On a phone the trophy list page's first trophy is almost three screens down (1,883px on Golf With Your Friends; the game page, 843 to 1,109px). The trophies are the page's job (rule 2) | the trophy list hero | fixed: `feature/ui/trophy-list-pass` (the game page's hero, asked of one list: 963px on Golf, 843px on Jak 3; desktop 1,282px to 822px) |
| GL-02 | Medium | The trophy list page still shows an absence ("Not in a badge series", rule 3) and states a fact twice (two "Publisher" facts for a game with two publishers) | the trophy list hero | fixed: `feature/ui/trophy-list-pass` (one facts line; badges and Jobs are the game page's About) |
| GL-03 | Medium | The same player count three times signed out ("327 Players" in the corner, in the sign-up box and in the tile row), and the four community tiles between the hero and the trophies: the stat-tile pattern the browse pass took off six pages | the trophy list page | fixed: `feature/ui/trophy-list-pass` (the tiles head the Ranks view; the corner is the platinum stat; owner's pick) |
| GL-04 | Medium | On a phone the DLC packs are one chip per row before the first trophy (about 290px on a five-pack game, on both pages) | the shared group nav | fixed: `feature/ui/trophy-list-pass` (one row that scrolls sideways on a phone; measured not to widen the layout viewport under iPhone emulation) |
| GL-05 | Medium | A one-list game names itself three times before the trophies: the title, a picker with one card, and "Viewing list: ...". On several-list games the "Viewing list" bar repeats the selected card | the game page's Trophy Lists tab | fixed: `feature/ui/trophy-list-pass` (no picker for one list; the bar retired, its link to the list's page moved into the control row) |
| GL-06 | Medium | An earned trophy says so three times (green edge, green check, green italic line) with a timestamp in a format nothing else uses ("Earned Jun. 08, 2026, 04:31 PM (EDT)") | the shared trophy rows | fixed: `feature/ui/trophy-list-pass` ("Earned Jun 8, 2026", the exact time on hover; the edge is the one mark; owner's pick) |
| GL-07 | Low | The two rates do not line up row to row: on a phone "PP RATE" wraps in some rows (rows 112, 119 or 145px tall), on desktop the columns shift by the check mark and the number's width | the shared trophy rows | fixed: `feature/ui/trophy-list-pass` (fixed-width columns, tabular figures, labels that never wrap) |
| GL-08 | Low | Each trophy row is tall on a phone (112px) | the shared trophy rows | fixed with GL-06 and GL-07 (no further anatomy change, as proposed) |
| GL-09 | Low | Signed out, the game page hero is a title, one line of facts and about 180px of nothing beside the cover | the game page hero | fixed: `feature/ui/trophy-list-pass` (the status slot invites, shared with the trophy list page; owner's pick) |
| GL-10 | Low | The two pages filter trophies differently: a full toolbar on the trophy list page, none on the game page's Trophy Lists tab | both pages | fixed: `feature/ui/trophy-list-pass` (one shared control row: All / Unearned, Sort, More filters; one filter function; owner's pick) |
| GL-11 | Low | One list, two percentages: Jak 3's hero says 45% (PSN's, which weighs trophies by grade) and its base-game group header says 49% (a trophy count). Rule 5 | the shared group headers | fixed: `feature/ui/trophy-list-pass` (headers and the hero's bar use PSN's weighting, `psn_progress`; it reproduced PSN's own percentage on 204 synced lists; owner agreed) |

## What works

- **The Ratings tab.** On prod it reads well: the community band, the rating with its one-line summary,
  difficulty, grind and fun, the spread. With no ratings it says "Be the first to rate this" with the button,
  an empty state with an action, which rule 3 allows.
- **The group headers.** Each pack's art, name, progress bar and grade counts. Clear, and they line up.
- **Desktop rows** are compact (81px) and scan well, apart from GL-07.

## Checked, not findings

- DLC icons that looked blank in the first captures were lazy-loading; they load when scrolled to (confirmed on
  prod).
- The trophy list page's year is the list's own platform date (2017 for Jak 3 on PS4), where the game page
  says the work's 2003. Both are right for their page.

## Decisions the owner made

- GL-03: the tiles move behind Ranks. GL-06: date only, the time on hover. GL-09: show the invitation.
  GL-10: one shared small row. GL-11: agreed.
- Round 2 of the mock-up: the phone status card in full-width rows; **My stats** moved from the action row into
  the status card. Calls of mine the owner accepted: the four grade cards became the status card's line of
  counts; "Plat Card" became "Share" (the game page's name).
- Kept from an earlier call: the trophy list page keeps **Versions & editions** ("this page IS one version, so
  its relatives belong here"), now a quiet "N versions" on the facts line.
