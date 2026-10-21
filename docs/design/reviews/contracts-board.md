# Contracts board: design pass (fix plan branch 6)

Status: **built** (2026-10-21, `feature/ui/contracts-board-pass`), with the four owner decisions below. Covers
DL-05, DL-07, DL-08 and FV-14.
Judged from fresh captures of Career's board (Ready to Claim, 60 contracts) on the owner's dev account at
375, 768 and 1440, plus the contract modal.

## The one idea

The board is where a hunter turns contracts into levels, so the thing to do (claim) and the number it
pays should be the first thing each card says, in plain words, at a size that lets a phone scroll the
list. Today a phone card spends 436 of its 643px on art and on 25 job icons where 1 to 4 are lit, and
says what it pays in a code ("Claim plat").

## Measured (phone, 375)

| Part | Height |
|---|---|
| Cover art | 226px |
| Title + six platform chips (one lit) | 55px |
| 5x5 job map (25 icons, 1 to 4 lit) | 210px |
| Tier bars ("+4,200 plat · +1,800 at 100%") | 39px |
| "1 game · view details" row | 38px |
| Padding and gaps | about 75px |
| **Card** | **643px** (tablet 680px, two across; desktop 581px, four across) |

60 claimable contracts is about 58 phone screens.

## Owner decisions (2026-10-21)

- **Phone card:** a list row (about 120px), not a shorter tile. Tablet and desktop keep the tile.
- **Claim label:** the XP the press banks, "Claim 4,200 XP".
- **Modal claim off Career:** the footer links to Career's Ready to Claim board ("Claim in Career").
- **Desktop map:** the 25-job map stays on tablet and desktop.

## What changes

### 1. The phone card becomes a row (DL-05)
Below 640px (where the board is one column) the card is a list row: the game's portrait cover on the left, and on the right the title, this
contract's own jobs (lit icons only, named for screen readers), and a reward line with the action. About
120px a row, so 60 contracts is about 11 screens. The row opens the modal; the claim button sits on top.

- **What goes on a phone:** the five dark rows of the job map (it answers "which of the 25 jobs?", a
  desktop browsing question), the unlit platform chips, the separate details row (the whole row opens the
  modal), and the cover banner (the portrait cover is the recognisable art at thumbnail size).
- **What stays:** status (as the action: Claim, a percentage, Not started, or Banked), New, the XP.
- **Tablet and desktop keep the tile.** The 25-job map stays there, where the grid has room for it. The
  platform strip shows only the platforms the contract spans, at every size.
- Same template, CSS re-flows it. The lit-jobs strip is the only new markup (1 to 6 icons). The job-detail
  variant (`job`) rides along and keeps its "this job" emphasis.

### 2. The claim says what it pays (DL-08)
- **"Claim 4,200 XP"**, not "Claim plat" / "Claim both" / "Claim 100%". The number is `claim_xp`, the
  tier(s) this press banks, from the accept path's own `contract_service.tier_amounts`, so it matches what
  accepting pays, an `xp_total_override` that rounds on a .5 included.
- Tier bars read **"+4,200 at platinum"** and **"+1,800 at 100%"**.
- The modal spells the split out once (below), where there is room.

### 3. The modal can claim (DL-07)
- A **Rewards** section: "Earn the platinum · +4,200 XP" and "Then reach 100% · +1,800 XP", each with its
  state (Banked, Ready to claim, N% complete, After the platinum, Not started). A contract without a
  platinum shows one line, "Reach 100%". The anonymous preview shows the split without state.
- On **Career**, a **sticky footer** when something is ready: "Claim 4,200 XP". It claims in place: the
  modal closes and the claim ceremony plays, the same path as the card's button.
- **Off Career** (game detail and badge detail, the other two hosts that open this modal) a **"Claim in
  Career"** link sits under the Rewards, to the Ready to Claim board. Those pages have no claim controller,
  and their shells already pin a "Browse all Contracts" footer, which a second sticky footer covered.
- Data: `build_contract_modal` is `project_card` on the annotated contract filtered to the slug, plus the
  games, so the modal and the card cannot disagree. The preview takes the same path with no profile.

### 4. The cover fan draws a game (FV-14)
Each `.pp-forge__cover` gets a title bar and a small trophy mark, drawn in CSS (pseudo-elements; the mark
is a CSS mask, no image file). The middle card's mark is platinum. One rule in `series-list.css` fixes all
three hosts: the Career explainer, the badge how-to, and How badges work. The explainer's open/fan
animation moves the cover, so the marks ride along.

### 5. House primitives
The card's retired DaisyUI classes (`badge badge-*`, `btn btn-xs btn-primary`) move to the house
primitives (`pp-cta--xs`, the house chip `bd-chip`), so the retired-class allowlist shrinks. (`card` on the
root is not a retired family and stays.)

## As built

- **Phone row: 116 to 133px** (643px before), so 60 contracts is about 12 screens. No sideways scroll at 375.
  The row breakpoint is 640px, not `md`: the board goes two-up at `sm`, and a two-up column wants the tile.
- **The tier bars hide in the row too**, with the map and ring. The row says the total and what the press
  banks; the split lives in the modal's Rewards.
- **One split for card, modal and accept.** `contract_service.tier_amounts` is what `_pending_tiers` pays and
  what the card shows. The card's own float `round()` disagreed with the accept path's Decimal half-up on
  an odd override (6,015: "Claim 4,210 XP" banked 4,211); the audit caught it.
- **Off Career the claim is a link under the Rewards, not a footer**: game and badge detail already pin
  "Browse all Contracts" to the dialog's bottom edge.
- **The modal is built from the card's own dict** (`build_contract_modal` = `project_card` + the games), so
  status, tier states and `claim_xp` come from one place. The anonymous preview takes the same path with
  `profile=None`, which costs no per-user work.
- **The claim waits for the modal to close** (`closedFn` in `hideNow`), then the board's `accept()` runs as if
  the card's own button had been pressed, so the ceremony and the in-place refresh are unchanged.
- **Claim all** moved to `pp-cta` with the card's button, and the retired-class allowlist lost the card's 21
  entries plus Career's 5.
- Tile height on desktop: 565px (581 before), from the lit-only platform strip.

## Not in this branch
- The board toolbar, filters and sort.
- History's own layout beyond the row re-flow.
- The bulk "Claim all" flow (it already exists and works).
