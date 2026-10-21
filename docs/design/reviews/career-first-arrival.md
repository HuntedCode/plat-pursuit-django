# Career + first arrival: design pass (fix plan branch 4)

Status: **built** (2026-10-21, `feature/ui/career-first-arrival`). Approved with the two owner decisions below. Covers FV-03, FV-12, DL-01, DL-02, DL-03, DL-09
and the Career legend follow-up from the Pursuer Level redefinition (#226). Judged from fresh screenshots of
the owner's dev account: linked, 0 XP, 60 contracts claimable.

## The one idea

A new hunter's history has already earned them a Pursuer. On dev, claiming the 60 contracts waiting would
make the owner's account **Level 103, Hunter V**, with Pathfinder at Lv 17. Today no screen says so: the
sync finale shows trophy counts, the lobby says "Welcome back" and never mentions contracts, and Career
opens on a wall of zeros. Every surface in this branch should say the same thing: **your Pursuer is waiting,
claim it.**

The projection (the level and rank claiming everything would give, plus each job's projected level) is one
DB aggregate over the claimable contracts, split evenly across each contract's jobs, the way claims split
XP. It costs nothing new to know the claimables exist: contract detection runs inside `sync_complete`,
before the profile is marked synced (`token_keeper._job_sync_complete`).

## What changes, by surface

### 1. The sync finale (FV-03)
`templates/home/_hero_syncing.html`. "Your Pursuer has emerged" is followed by four PSN trophy counts.

- Show the Pursuer instead: **"60 contracts are ready to claim"** and **"Claim them to reach Level 103 ·
  Hunter V"**, with the jobs it raises most. The level line shows only when claiming moves the level.
- Primary action: **Claim your contracts** (Career, claimable filter). Secondary: Enter your Pursuit.
- The trophy counts go: the hunter already has them on PSN, and the lobby shows them.
- Data: the poll payload gains a small block **only when `sync_status == 'synced'`**, so syncing polls
  cost nothing extra.
- A hunter with no claimables keeps today's "Enter your Pursuit", with an honest line.

### 2. The lobby (FV-03, DL-03, FV-12)
`templates/trophies/home.html`.

- **Lead with the Pursuit.** A claim prompt at the top when contracts are waiting: "60 contracts ready ·
  claim to reach Level 103" (the `claimable_summary` the lobby already computes and never shows, DL-03).
  Career and Collection move above the trophy statistics.
- **"Welcome back" only for a returning hunter.** First-timers get "Welcome". Signal: whether the hunter
  has ever claimed (Career XP > 0); no new flag or write.
- **Sync status once (FV-12).** Keep "Updated 5 days ago · Sync now" in the hero; drop the "Synced" dot
  in the Trophy Collection card.
- **Two "levels" a scroll apart.** The Trophy Collection badge reads "Lv 386" (the PSN trophy level)
  beside a Career ring reading "1". Relabel it **"Trophy Lv 386"**.

### 3. Career's first screen (DL-01, DL-09)
`templates/trophies/career.html`.

- **Delete the card titled "Career" inside the Career page.** Its four stats become a slim row in the
  hero, under the rank bar.
- **The switchers line up.** The view switcher stays right-aligned, per the site-wide Tab Group standard
  (design-system.md), and the Dossier / Sheet switch under it is now right-aligned too; they used to sit
  at opposite edges. The "How does my Career work?" link takes the left end of the switcher's row. (The
  first draft left-aligned the view switcher instead, which broke that standard; caught while building.)
  The Contracts view's Board / History scope stays at the left of its toolbar, opposite the sort, which is
  an ordinary toolbar layout.
- Effect: the Jobs view starts at y = 632 on a 900px desktop for a newcomer with contracts waiting, instead
  of 894, so switching views visibly changes the first screen (DL-01). On a phone the view still starts
  below the fold, and the existing sticky switcher already covers the scroll.

### 4. Career before the first claim (DL-02)
- **The stat row is hidden at 0 XP**: four zeros say nothing.
- **The claim banner says what claiming makes you**: "Claim them to reach Level 103 · Hunter V", with the
  top projected jobs (Pathfinder 1 → 17, Gunslinger 1 → 11, Infiltrator 1 → 10). One component
  (`partials/components/_claim_banner.html`) shared with the lobby. On a phone the jobs list is dropped
  from the banner (the tiles below say the same), but kept in the finale.
- **Each job tile with XP waiting shows it**: "1 → 17" beside the level, on its baseline so the tiles
  stay one height, so the 25 identical cards become a preview of the Pursuer the hunter is about to be.
  Tiles with nothing waiting stay as they are.
- The radar collapsed to a dot is left alone: it is one tab away, and its empty state is a later call.

### 5. The legend reads as gains (agreed 2026-10-21)
The ring's legend and peeks show each discipline's levels gained: **"+7"**, and **"+0"** for a newcomer,
so "Combat 0" no longer sits beside five Combat jobs that each say Lv 1. Job tiles keep Lv 1.

## Also in the branch
- The finale's buttons are house `pp-cta`s (its DaisyUI `btn` left the retired-class allowlist).
- The hero's two italic empty lines ("No title equipped", "New Pursuer") become one.
- FV-12's and DL-09's status rows, and the design-system doc, updated in the same branch.

## Not in this branch
- The radar's empty state.
- Contract cards (branch 6).

## Owner decisions
- **Projection everywhere** (2026-10-21): the finale, the lobby and Career all show what claiming would
  make the hunter (level, rank, top jobs), and job tiles show their projected level.
- **Pursuit first on the lobby** (2026-10-21): claim prompt, then Career + Collection, then the trophy
  statistics.
