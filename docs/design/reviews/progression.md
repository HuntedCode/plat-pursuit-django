# Review: Progression

The catalogues a hunter progresses through: Badges (`/badges/`, a badge's page, How badges work), Jobs
(`/jobs/`, a job's page), and the hunter's own shelves: Collection, Milestones and Titles. Reviewed 2026-10-24
at 375 / 768 / 1440, plus the owner's laptop (1280 x 633). Method: [README.md](README.md).

**Where:** the public pages on **https://platpursuit.com**, signed out (read-only GETs): Badges, How badges
work, Crash Bandicoot, Jobs, Architect, Milestones. The signed-in pages on **dev** with the owner's saved
browser session: the same browses, Rockstar, Slayer, Collection, Milestones and Titles. Dev's data is thin
(4 badge series, no milestones, no titles), so the signed-in pages were judged for their shape, and anything
that only looked wrong because of dev data is under "Checked, not findings".

**Headline:** fast and error-free (every page under 2.7 seconds on prod, most under 1.5; no console errors),
and the catalogue cards are good: Jobs reads at a glance, the badge stage list is clear. The problems are the
first screen and the zeros. Four of these pages open on a block of stat tiles that the browse pass already
took off the Discovery list pages, and on a phone the tiles push the first job and the first badge in your
collection below the tab bar. A hunter with nothing yet (the newcomer, and every hunter on Titles until they
clear a series) meets a page of zeros. And the job page's contract cards break job names mid-word on every
screen from tablet to a 1440 desktop.

## Findings

| ID | Sev | Finding | Where | Status |
|----|-----|---------|-------|--------|
| PR-01 | Medium | A job page's contract cards break job names mid-word: "Gunsling / er", "Survivali / st", "Champio / n", "Cartograph / er". The job tiles sit three to a row in a card about 300px wide, and the name wraps by character. Measured on `/jobs/architect/` (prod): 24 of 97 tiles at 1440, **30 at 1280** (the owner's laptop), 16 at 1024, 7 at 768; none at 1920, and phones show icon-only tiles. Career's board does not use this variant | the job-scoped contract card (`_contract_card.html` with `job`, `.rp-jobtile__name`) | fixed: `feature/ui/progression-pass` (the job-scoped card is a container; under 365px wide its tiles take two columns, centred, from 640px up so the phone row is untouched) |
| PR-02 | Medium | The Jobs browse opens on four stat tiles: "Jobs 25" (beside the corner's "25 jobs"), live contracts, games in contracts, and "Job XP banked 285,583,800". None of them helps pick a job, and on a phone they fill the first screen: the first job card starts at 637px against the tab bar's 610 (375 x 667). The browse pass removed exactly this from the six Discovery list pages ([DS-02](discovery.md), [DS-04](discovery.md)); Jobs and Badges were not among them, and Badges keeps a "Badge series 182 to collect" tile under its "182 series" corner | `templates/trophies/jobs_browse.html`, `templates/trophies/badge_list.html` headers | fixed: `feature/ui/progression-pass` (Jobs and Badges headers keep their tally alone; the four Jobs heartbeat stats only those tiles read are deleted) |
| PR-03 | Medium | A job page puts the job before the hunter. Signed in, the hero leads with the job's own totals (contracts, total XP, pursuers) and "Your standing" (level, XP banked, XP to next level, rank) comes after them. On a phone the hero is the whole first screen: the icon and discipline sit on the left, the job's name is pushed to the far right, and three tiles plus the standing card follow, so the first contract starts at 506px. Rule 1: where the hunter stands on this job should lead | `templates/trophies/job_detail.html` hero | fixed: `feature/ui/progression-pass` (signed in, Your standing comes straight under the job's name; the totals are one `.pp-factline`, pursuers only when there are some; the header row is off `.pp-bhead`) |
| PR-04 | Medium | Collection's numbers disagree: the header says **0 / 4** badges collected, and the grid under it says **3 of 3**. The header counts live editions (`profile_card_service.catalog_total`); what the grid counts, and why one edition is missing from it, is still to trace. On a phone the header (count, three tiles, "What are editions?") and the toolbar fill the first screen: the first badge starts at 632px, under the tab bar | `templates/trophies/collection.html`, `trophies/views/collection_views.py` | fixed: `feature/ui/progression-pass` (the tiles are one facts line that names only what is there; the grid says "3 started", or "2 of 3 shown" when filtered. The header counts the catalogue and the grid counts started badges, so the fourth edition was the unstarted one) |
| PR-05 | Medium | Titles, for a hunter who has none, is a page of absence: "No title equipped" in a dashed hero plate, three tiles reading 0, a switcher of Yours 0 / Within reach 0 / All 0, then a "No titles yet" empty state. Every hunter sees this until they clear a whole badge series. The one useful thing on the page, "Clear a badge series and its title is yours" with a Browse badges link, is at the bottom, below the fold on a phone. Same shape as [DL-02](daily-loop.md) (the unclaimed wall of zeros) | `templates/trophies/my_titles.html` | fixed: `feature/ui/progression-pass` (no title yet: a "Your first title" card with the series closest to one, or the most-held titles, and Browse badges; the nameplate and switcher arrive with the first title; no count tiles for anyone) |
| PR-06 | Low | The Badges browse opens its How badges work modal over the page on a first visit, signed in or out. On a phone it is a tall scroll of its own (four steps stacked, the Got it button far below), so a visitor who came from search to look at badges reads a tutorial first. The modal is good; the question is whether a browse page should interrupt. The same teaching lives at `/badges/how-it-works/` | `templates/trophies/partials/badge_list/badge_howto.html` (auto-open) | fixed: `feature/ui/progression-pass` (owner: no auto-open; the header hint LINKS to How badges work, so there is one teaching home. The browse modal, its quick-peek dialog and their CSS are deleted) |
| PR-07 | Low | Signed out, Milestones shows every milestone as "Not yet started" over an empty bar, eight times. It is a preview ("Here's what's waiting") so the cards earn their place, but the repeated empty state is absence as content; the tiers row already says what each rung is | `templates/milestones/milestone_list.html` (anon) | fixed: `feature/ui/progression-pass` (signed out, each card is what the milestone is plus its rungs; status and bar only with progress) |
| PR-08 | Low | A job page's only control above its contracts is a lone "Latest 67" chip. It is a filter (contracts added in the last 14 days), but alone and unpressed it reads as the list's label and count, while the list below shows all 311. Rule 6: the default (all contracts, newest first?) is not shown | `templates/trophies/job_detail.html` contracts toolbar | fixed: `feature/ui/progression-pass` ("311 contracts, A to Z" always beside the chip; no toolbar on a job with no contracts) |

## Owner decisions (2026-10-24)

- **Stat tiles (PR-02, PR-04, PR-05):** the same fix as Discovery. Headers carry one count that matches the grid;
  anything worth keeping folds into the one-line facts row (`.pp-factline`).
- **Job page (PR-03):** signed in, the hunter's standing on the job leads the hero (level, XP bar, rank); the job's
  totals shrink to the facts row. Signed out, the job leads as before.
- **Badge intro (PR-06):** no auto-open. A "How do badges work?" hint in the header LINKS to How badges work
  rather than reopening the modal (the owner's follow-up call: one teaching home, so no second copy to drift). The
  browse page's modal is deleted.
- **Titles before the first title (PR-05):** lead with the way in: titles come from clearing a badge series, with
  the series the hunter is closest to finishing and a link to Badges. The plate, counts and tabs appear once a title
  is owned.

## Principles checklist

| Page | 1 Hunter first | 2 Job leads (375px) | 3 No absence | 5 Numbers agree | 6 Defaults visible |
|------|----------------|---------------------|--------------|-----------------|--------------------|
| Badges browse | ok (cards show your progress) | after the modal, the first card starts at 538px | ok | the "182 to collect" tile repeats the corner (PR-02) | ok |
| Badge page | ok (your progress bar in the hero) | ok | ok | ok | ok |
| How badges work | n/a (teaching page) | ok | ok | ok | n/a |
| Jobs browse | no standing on the cards | **no**: first job at 637px (PR-02) | ok | "Jobs 25" twice (PR-02) | ok |
| Job page | **no**: standing after the job's totals (PR-03) | ok signed out; 506px signed in | "0 pursuers" on a new job | ok | **no**: lone "Latest" chip (PR-08) |
| Collection | ok | **no**: first badge at 632px (PR-04) | three tiles, two at 0 for a newcomer | **no**: 0 / 4 against 3 of 3 (PR-04) | sort visible; filters in a drawer |
| Milestones | ok signed in | ok | signed out: eight "Not yet started" (PR-07) | ok | n/a |
| Titles | ok | the useful line is below the fold | **no**: a page of zeros (PR-05) | ok | ok |

## Checked, not findings

- **Milestones signed in on dev** shows only "0 of 0" tiles and no cards: dev has no `Milestone` rows. Prod has them
  (the signed-out page lists eight). A catalogue with no milestones at all would show no empty state, but that cannot
  happen on prod.
- **Titles "All 0"** on dev is dev data too (no titles defined); "Yours 0" and "Within reach 0" are the real newcomer
  state PR-05 describes.
- **Rockstar's "Your series standing #1 of 1" with 0 earners** is a dev-data artifact of a one-hunter database.
- **Job pages are very long** (14,206px on a prod desktop after scrolling, 22,532px on a tablet) because the contract
  grid infinite-scrolls through every contract the job has. That is the list doing its job, not a layout bug.
- **The overlays on these pages** (badge stats, collection badge, contract modal) were measured on 2026-10-24's
  laptop sweep and fit.

## Candidate lessons

- **A fix applied by page list misses the pages not on it.** The browse pass took stat tiles off the six Discovery list
  pages; Jobs, Badges, Collection, Titles and Milestones kept theirs (PR-02, PR-04, PR-05). Second sighting of
  [DS-02](discovery.md)'s tiles, so it is promoted: "the hunter's own shelves follow it too" in
  [design-system.md](../../reference/design-system.md).
- **A browser test at one viewport misses the other layouts.** PR-01's container rule tied on specificity with the
  phone row's hide rule and won on source order, bringing the tiles back on phones; the test laid cards out in a
  1440px window only. Caught by a phone screenshot before review, and the test now has a phone case. First sighting.
- **Zero states are a whole-page decision.** PR-05 repeats DL-02: each part of the page handles "none" on its own, and
  together they make a wall. Second sighting.
