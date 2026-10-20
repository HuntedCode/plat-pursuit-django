# Review: Daily Loop

What a synced hunter does on a normal visit: the lobby (`/`), Career (`/career/`) and its three views
(Jobs, Radar, Contracts), the contracts board and history, the contract modal, and the claim ceremony.
Reviewed 2026-10-20 at 375 / 768 / 1440 on dev. Method: [README.md](README.md).

**Account used:** the owner's dev account (about 4,000 trophies, 90 platinums), which had **claimed
nothing**: 0 XP, every job at level 1, 60 contracts ready to claim. That is exactly the state every new
hunter lands in after a first sync, so it is the right state to judge first. Not covered: an account
mid-progress (claimed contracts, levelled jobs) and a whale. Claims were never pressed; the ceremony was
judged through the Career dev panel's canned replays, which write nothing.

**Headline:** clean (every page loaded in one to two and a half seconds on dev, the lobby slowest; no
console errors, no sideways overflow at any size), and the claim moment itself is excellent. The problem is getting to it. Career's chosen view
starts below the fold on every view, the unclaimed state is a wall of zeros, and the lobby never mentions
the contracts waiting to be claimed.

## Findings

| ID | Sev | Finding | Where | Status |
|----|-----|---------|-------|--------|
| DL-01 | High | Career's first screen is identical on every view: the chosen view's panel starts at y = 894 in a 900px desktop window (measured, all three views) and below 950px on a phone. Switching Jobs / Radar / Contracts changes nothing the hunter can see | `templates/trophies/career.html` | open |
| DL-02 | High | The unclaimed state, which every new hunter sees first, is a wall of zeros: four stat tiles (0, 0, 1.0, 1), 25 identical "1 / Initiate / 0 / 3,000 XP" cards, and a radar collapsed to a dot. The one meaningful action, claiming, is a banner between them | `templates/trophies/career.html`, `templates/trophies/partials/career/` | open |
| DL-03 | High | The lobby never mentions claimable contracts, although `home_service._build_glances` computes `claimable_summary` on every lobby render. Only the one-shot 1.0 launch modal reads it. A missed prompt and a wasted query | `core/services/home_service.py`, `templates/trophies/home.html` | open, pairs with [FV-03](first-visit.md#fv-03-the-first-arrival) |
| DL-04 | Medium | Three "level" numbers that disagree: the ring says **Level 25** for a hunter with 0 XP (Pursuer level is the sum of 25 job levels, `career_service`), the tiles say average 1.0 and highest 1, and the discipline legend shows 5 each | `trophies/services/career_service.py`, `templates/trophies/career.html` | open |
| DL-05 | Medium | Contract cards are dense and tall: 643px each on a phone, so 60 claimable contracts is about 60 screens. Each card shows all six platform chips with one lit, all 25 job icons with one to four lit, a ring, XP, a claim button, a split XP bar and a details row | `templates/trophies/partials/contracts/_contract_card.html` | open |
| DL-06 | Medium | On a phone the back-to-top button sits on top of a card's Claim button. Same element as [FV-10](first-visit.md), now blocking the page's main action | `templates/partials/back_to_top.html` | tracked as [SW-02](README.md#site-wide-findings) |
| DL-07 | Medium | The contract modal has no claim action. A hunter opens a ready-to-claim contract, reads it, then has to close it and find the card's button | `templates/trophies/partials/contracts/_contract_modal.html` | open |
| DL-08 | Medium | "Claim plat" and "Claim both" are jargon; the "+4,200 plat · +1,800 at 100%" bar explains the split only to someone who already knows it | `templates/trophies/partials/contracts/_contract_card.html` | open |
| DL-09 | Low | Layout seams on Career: a card titled "Career" inside the Career page, the view switcher right-aligned below the stats while the sub-switchers (Dossier / Sheet, Board / History) sit left-aligned below it | `templates/trophies/career.html` | open |
| DL-10 | Low | Toasts are DaisyUI's solid `alert-*` blocks (`ToastManager` in `static/js/utils.js`). On a phone an info toast is a saturated full-width blue slab over the content above the tab bar | `static/js/utils.js` | open, part of [SW-01](README.md#site-wide-findings) |

## What works

- **The claim ceremony.** XP counts up, each job's bar fills, tier-ups get their own beat, and Continue
  closes it. It feels earned, and it holds up next to anything on the site.
- **The contract modal's content.** Which jobs it levels and by how much, and the game with its progress
  and "Platinum earned". Clear in one read.
- **The gold claim banner.** The clearest call to action in the slice: count, XP waiting, one link.
- **The contracts toolbar.** Search, sort, status chips and the Latest toggle cover what a hunter asks
  for, and "Claim all (60)" is right there.

## Checked, not findings

- **"Latest 279" equal to "All 279", and NEW on every card**: Latest means "added in the last N days"
  and every dev contract is recent. Prod will differ.
- **The dev panel over the phone shots**: DEBUG-only.
- **The late-night toast copy** ("Late night plat session? We see you, legend."): an Easter egg in
  `static/js/easter-eggs.js`, between 2 and 5 AM, once per session. The copy is charm; its styling is
  DL-10.
- **The ring's "25"**: not a rendering bug. It is the sum of job levels by design; whether that is the
  right number to show a newcomer is DL-04.

## Candidate lessons

Second sightings, now ready to promote (see [README.md](README.md#lifecycle-of-a-finding)):

- **First-run and zero states are designed, not defaulted.** FV-03 (the lobby greets a newcomer with
  "Welcome back") and DL-02 (Career's unclaimed state is a wall of zeros).
- **Nothing competes with a page's one job.** FV-10 / FV-11 (back-to-top, Premium and NEW before
  linking) and DL-06 (back-to-top over the Claim button).

New, first sighting:

- A view switcher's content must start within the first screen.
- Every surface that shows an actionable thing offers the action (DL-07's modal, DL-03's lobby).
