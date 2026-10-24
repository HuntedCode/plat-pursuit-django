# Review: Challenges

The Challenges system, public since 2026-10-18: the community browse (`/community/challenges/`), the Hall of Fame,
a run's own page (A-Z, Job Coverage, Plat Calendar), My Challenges, the square picker and the Challenge Optimizer.
Reviewed 2026-10-24 at 375 / 768 / 1440, plus the owner's laptop (1280 x 633). Method: [README.md](README.md).

**Where:** the public pages on **https://platpursuit.com**, signed out (read-only GETs): the browse, the Hall of
Fame, an A-Z run (#94) and a Plat Calendar run (#8). The private pages on **dev** with the owner's saved browser
session: My Challenges, the optimizer's first step, the owner's A-Z and Job Coverage runs, and the square picker
opened on an empty letter (nothing assigned; no write was sent). Prod has 213 runs in flight and none finished; dev
has two runs, both the owner's, so the private pages were judged for their shape. The first-visit "How Challenges
work" modal was removed from the page for the captures rather than dismissed, since dismissing writes a flag to the
owner's account.

**Headline:** the run pages are the best thing here. The A-Z board of covers with its letters, the Calendar's month
rings and day sheet, and the picker that opens on "13 games fit" are clear and feel like the product. Every page is
fast (the browse answers in 0.3 seconds on prod) and error-free. The problems are absence and the way in. Six days
after launch, 43% of the runs on the public browse have no progress, the Hall of Fame is an empty page in the menu,
and My Challenges leads with "0 finished". A signed-out visitor who arrives on somebody's run (the pages are public, linked
from the browse and from profiles) has nowhere to go, and the one "Start a challenge" link a visitor can find opens "Welcome back" sign-in.

## Findings

| ID | Sev | Finding | Where | Status |
|----|-----|---------|-------|--------|
| CH-01 | Medium | The public browse is close to half absence. Of 213 runs on prod, **91 show no progress** ("0/26 LETTERS", "0/25 JOBS"); for Job Coverage it is **57 of 72**. Sorted by progress they fill the second half of the page, and the header's "213 in flight" counts them. A run nobody has put a game into is not yet anything to look at (rule 3) | `templates/challenges/browse.html`, `partials/browse_results.html`, `ChallengesBrowseView` | fixed: `feature/ui/challenges-pass` (a run joins the browse with its first done square; the tally hides at zero) |
| CH-02 | Medium | A signed-out visitor has no way in. The browse and a run's page offer nothing to start your own (the header's action is "My Challenges", signed in only), so a visitor who lands on a run (from the browse, a profile or a shared link) ends on the footer. The one link a visitor can find, the Hall of Fame's empty-state "Start a challenge", goes to `/my-challenges/`, which sends them to **sign in** ("Welcome back"), not sign up | `partials/_browse_header_actions.html`, `challenge_detail.html`, `partials/browse_results.html` | fixed: `feature/ui/challenges-pass` ("Start your own" for visitors on the browse, the Hall of Fame and a run's page; sign-up, back to My Challenges) |
| CH-03 | Medium | The Hall of Fame is empty on prod and sits in the Community menu beside Challenges: a "0 finished" tally over "Nobody has finished one yet". It will be for a while (an A-Z run is 26 platinums; the nearest prod run is at 23/26). A menu item that opens onto nothing is absence as a destination | `templates/challenges/hall_of_fame.html`, `core/hub_subnav.py` | fixed: `feature/ui/challenges-pass` (until the first finish: "Nobody has finished one yet" and the four runs closest to it; no tally) |
| CH-04 | Medium | My Challenges leads with the hunter's zero, and on a phone their runs are not on the first screen. The header's tally is "0 finished" (every hunter on prod), then the Challenge Optimizer's promo card (title, pitch, three ticks, a saved-plan line, a full-width button) fills the screen: the first run card starts at 505px and its **Continue** is under the tab bar (610px). The page's job is "carry on with my runs"; the optimizer is depth on demand (rule 4) | `templates/challenges/my_challenges.html` | fixed: `feature/ui/challenges-pass` (runs first, the optimizer card after them; the tally counts runs in progress until a finish) |
| CH-05 | Medium | A Job Coverage run's page, on a phone, shows no square on its first screen (the first at 636px against the tab bar's 610). The title wraps to two lines beside its title chip, the three actions stack into two rows, and the reward panel's headline is a giant **"0 Job XP claimed"**, with what the run pays ("6,000 Job XP per finished square, 150,000 for a full run") in small print under it (rule 3: the useful fact sits under a zero) | `challenge_detail.html` header, `partials/_rewards_panel.html` | partly fixed: `feature/ui/challenges-pass` (the title chip wraps under the title and Share / Optimize fold to icons, so the first squares are on the first screen). The headline leads with "+6,000 Job XP per finished square" until the first claim (owner, 2026-10-24) |
| CH-06 | Low | An unfinished run's title chip looks held. "A-Z Champion" sits beside the run's name in the same filled chip whether the run is finished or at 0/26; only a hover tooltip says "Awarded when this run is finished", which a phone never shows | `challenge_detail.html` (`rewards.title_name` chip) | fixed: `feature/ui/challenges-pass` ("Earns A-Z Champion" in a quiet chip until earned) |
| CH-07 | Low | A Plat Calendar header says **207/365 days**, and under the title ladder "**209** days with any platinum". The gap is the shovelware lens, but the page never says so, so two day counts disagree in one card (rule 5) | `partials/_calendar_rail.html` | fixed: `feature/ui/challenges-pass` ("209 days with any platinum, shovelware included") |
| CH-08 | Low | A browse card's footer is `naturaltime`: "Started 5 days, 21 hours ago" wraps to two lines on a phone (`compact_since` exists for exactly this cell). A-Z and Job Coverage cards say when the run **started** while Calendar cards say when it last **updated**; on a page sorted by progress, the last move is the useful one | `partials/_run_card.html` | fixed: `feature/ui/challenges-pass` ("Updated 5d ago" for every type, `compact_since`) |
| CH-09 | Low | The optimizer's sticky "Next" bar is translucent, and on the laptop's first screen it crosses the middle of the two challenge cards, with their descriptions showing through it | `templates/challenges/optimizer.html` | fixed: `feature/ui/challenges-pass` (the dock is opaque) |
| CH-10 | Low | The picker says its count twice ("13 games fit" in the head, "B: 13 games fit" above the grid), and its platform chips start with none pressed, which means "all platforms" without saying so (rule 6) | `challenge_detail.html` `#cpick` | fixed: `feature/ui/challenges-pass` (the repeated count is spoken, not shown; an "All" chip is pressed until a platform is) |

## Owner decisions (2026-10-24)

- **Runs with nothing done (CH-01):** hidden from the browse until their first square; the headline counts the rest.
- **The empty Hall of Fame (CH-03):** the runs closest to finishing, as the race to be first, until somebody finishes.
- **My Challenges (CH-04):** runs first, the optimizer card after them. This reverses the card's place from
  2026-10-17 ("I really want people to see and use this"); it still leads the page's second screen.
- **The way in (CH-02):** "Start your own" on the browse, the Hall of Fame and a run's page, signed out only.
- **The reward headline (CH-05):** the pay ("+6,000 Job XP per finished square") until the first claim, then the
  claimed total. This reopens the owner's 2026-09-30 call that the big number always means "claimed", zero
  included.

## Principles checklist

| Page | 1 Hunter first | 2 Job leads (375px) | 3 No absence | 5 Numbers agree | 6 Defaults visible |
|------|----------------|---------------------|--------------|-----------------|--------------------|
| Browse (signed out) | n/a | ok: first card at 466px | **no**: 91 of 213 runs at zero (CH-01) | "in flight" counts runs with nothing in them (CH-01) | ok: "All" and "Most progress" shown |
| Hall of Fame | n/a | ok | **no**: empty on prod (CH-03) | ok | n/a |
| A run's page (visitor) | n/a | ok: first square at 289px | ok | Calendar: 207 vs 209 (CH-07) | ok |
| A run's page (owner) | ok | A-Z ok (525px); **Job Coverage no**: 636px (CH-05) | "0 Job XP claimed" (CH-05) | ok | ok |
| My Challenges | **no**: "0 finished" leads (CH-04) | **no**: Continue under the tab bar (CH-04) | "0 finished" | ok | ok |
| Square picker | ok ("From your history" first) | ok | ok | count said twice (CH-10) | **no**: platform default unstated (CH-10) |
| Optimizer | ok | ok | ok | ok | ok: step 1 of 5 shown |

## Checked, not findings

- **The first-visit modal on My Challenges** auto-opens once, like Career's. Badges dropped theirs (Progression PR-06)
  because the teaching has a page of its own at `/badges/how-it-works/`; Challenges has none, so the modal is the
  teaching, which is the Career case. Reachable again from "How Challenges work" on every Challenges page.
- **Finished squares wear a foil** (prismatic wash, a platinum ring, one gleam), so on a phone they read softer than
  an in-progress square's plain cover. That is the owner's call of 2026-10-17 (`_square_foil.html`); the tick on
  every finished square carries the state.
- **The browse took 36 seconds once** in the capture (laptop size, waiting for the network to go quiet). Re-timed
  three times with curl: 0.25 to 0.30 seconds to first byte. An image straggler in the capture, not the page.
- **The rising stars on "Optimize my challenges"** pass behind its label: it is the shared `pp-cta--rise` from the
  Support family, and the label is positioned above them.
- **"Job Challenge Champion"** is the Job Coverage title's name while the run is "Job Coverage Challenge". It reads
  as deliberate naming, so it is noted rather than filed; worth a look if the owner wants the two to match.
- **The overlays** (the intro, the picker, the share dialog) were measured on 2026-10-24's laptop sweep and fit. The
  calendar opening is still not reachable on dev (the owner has no Calendar run there). The optimizer's plan now
  has a saved copy on dev ("Open my plan") but was not opened in this pass, so its weekly allowance is untouched.

## Candidate lessons

- **Launch-day emptiness is a design state, not a data state.** Three of these findings (CH-01, CH-03, CH-04) are a
  new feature meeting a community that has only just started: zero-progress runs, no finishes, "0 finished". Every
  page here was designed against a full board. Second sighting of the shape behind [DL-02](daily-loop.md) and
  Progression's PR-05 (a page that handles "none" part by part rather than as a whole).
